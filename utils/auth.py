# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Globus Auth integration — token validation and the authenticated principal.

Every handler that touches per-user data depends on :func:`get_current_user`
and reads the researcher identity from the returned :class:`Researcher`.  The
identity is *never* taken from a query parameter or request body: a caller who
can name a researcher_id can otherwise read and overwrite that researcher's
papers, proposals and stored API keys.

Three modes, selected by ``GLOBUS_AUTH_ENABLED`` (**default: enabled**):

  enabled + Bearer token  — introspect against Globus, map to a Researcher
  enabled + no token      — 401
  disabled                — ``X-User-ID`` header, else "anonymous" (local dev)

Disabling authentication is a local-development convenience, not a
configuration: :func:`assert_safe_configuration` refuses to start a server that
has it disabled while bound to anything other than the loopback interface. The
default is enabled so that forgetting to set it fails closed.

Configuration::

    GLOBUS_AUTH_ENABLED=true
    GLOBUS_CLIENT_ID=<confidential client UUID>
    GLOBUS_CLIENT_SECRET=<client secret>
    GLOBUS_ALLOWED_GROUPS=<comma-separated group UUIDs>   # optional allowlist
    GLOBUS_REDIRECT_URI=http://localhost:5173             # SPA callback
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

# Fail closed: an unset value means authentication is ON.
GLOBUS_AUTH_ENABLED = os.environ.get("GLOBUS_AUTH_ENABLED", "true").lower() == "true"

# Escape hatch for the deliberate case — running the containers locally without
# a Globus client, for instance. Never set this on a shared deployment.
ALLOW_INSECURE_DEV = (
    os.environ.get("CASSIOPEIA_ALLOW_INSECURE_DEV", "false").lower() == "true"
)
GLOBUS_CLIENT_ID = os.environ.get("GLOBUS_CLIENT_ID", "")
GLOBUS_CLIENT_SECRET = os.environ.get("GLOBUS_CLIENT_SECRET", "")
GLOBUS_REDIRECT_URI = os.environ.get("GLOBUS_REDIRECT_URI", "http://localhost:5173")

GLOBUS_AUTH_URI = "https://auth.globus.org/v2/oauth2/authorize"
GLOBUS_TOKEN_URI = "https://auth.globus.org/v2/oauth2/token"
GLOBUS_GROUPS_SCOPE = "urn:globus:auth:scope:groups.api.globus.org:all"

# Optional allowlist — when set, membership in at least one group is required.
ALLOWED_GROUPS = [
    g.strip() for g in os.environ.get("GLOBUS_ALLOWED_GROUPS", "").split(",") if g.strip()
]

SCOPES = ["openid", "profile", "email", GLOBUS_GROUPS_SCOPE]

# Introspection is a network round-trip on every request without this.
TOKEN_CACHE_TTL = 300  # seconds

ANONYMOUS_ID = "anonymous"


# ── Principal ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Researcher:
    """The authenticated caller.

    ``id`` is the canonical researcher_id used as the tenancy key everywhere:
    the Globus identity UUID (``sub``) under real auth, which is stable across
    username and e-mail changes.
    """

    id: str
    username: str = ""
    email: str | None = None
    name: str | None = None
    groups: list[str] = field(default_factory=list)
    auth_method: str = "development"

    @property
    def is_authenticated(self) -> bool:
        return self.auth_method == "globus"

    @property
    def display_name(self) -> str:
        return self.name or self.username or self.id


# ── Token introspection ───────────────────────────────────────────────────────

@dataclass
class TokenInfo:
    """Cached result of a Globus token introspection."""

    sub: str
    username: str
    email: str | None
    name: str | None
    active: bool
    expires_at: datetime | None
    scope: str
    cached_at: datetime
    raw: dict[str, Any]


_token_cache: dict[str, TokenInfo] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_cache_valid(info: TokenInfo) -> bool:
    if _utcnow() - info.cached_at > timedelta(seconds=TOKEN_CACHE_TTL):
        return False
    if info.expires_at and _utcnow() > info.expires_at:
        return False
    return info.active


async def introspect_token(token: str) -> TokenInfo | None:
    """Validate *token* against Globus Auth, returning None when it is not usable."""
    cached = _token_cache.get(token)
    if cached is not None:
        if _is_cache_valid(cached):
            return cached
        del _token_cache[token]

    try:
        from globus_sdk import ConfidentialAppAuthClient
    except ImportError:
        logger.error("globus-sdk is not installed — install with: pip install '.[auth]'")
        return None

    if not GLOBUS_CLIENT_ID or not GLOBUS_CLIENT_SECRET:
        logger.error("GLOBUS_CLIENT_ID / GLOBUS_CLIENT_SECRET are not configured")
        return None

    # globus-sdk is synchronous — keep the network round-trip off the event loop.
    try:
        client = ConfidentialAppAuthClient(
            client_id=GLOBUS_CLIENT_ID, client_secret=GLOBUS_CLIENT_SECRET
        )
        response = await run_in_threadpool(client.oauth2_token_introspect, token)
        data = response.data
    except Exception:
        logger.exception("Globus token introspection failed")
        return None

    if not data.get("active", False):
        logger.warning("Rejected an inactive Globus token")
        return None

    info = TokenInfo(
        sub=data.get("sub", ""),
        username=data.get("username", ""),
        email=data.get("email"),
        name=data.get("name"),
        active=True,
        expires_at=(
            datetime.fromtimestamp(data["exp"], tz=timezone.utc) if "exp" in data else None
        ),
        scope=data.get("scope", ""),
        cached_at=_utcnow(),
        raw=dict(data),
    )
    _token_cache[token] = info
    logger.info("Authenticated %s (%s)", info.username, info.sub)
    return info


async def get_user_groups(token: str) -> list[str]:
    """Return the Globus Group UUIDs *token*'s owner belongs to (empty on failure)."""
    try:
        from globus_sdk import AccessTokenAuthorizer, GroupsClient
    except ImportError:
        return []
    try:
        client = GroupsClient(authorizer=AccessTokenAuthorizer(token))
        groups = await run_in_threadpool(client.get_my_groups)
        return [g["id"] for g in groups]
    except Exception as exc:
        logger.warning("Could not read Globus group memberships: %s", exc)
        return []


# ── FastAPI dependency ────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Researcher:
    """Resolve the caller to a :class:`Researcher`.

    Raises 401 when authentication is required and absent or invalid, and 403
    when ``GLOBUS_ALLOWED_GROUPS`` is set and the caller is in none of them.
    """
    if not GLOBUS_AUTH_ENABLED:
        return Researcher(
            id=request.headers.get("X-User-ID", ANONYMOUS_ID),
            username=request.headers.get("X-User-ID", ANONYMOUS_ID),
            auth_method="development",
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    info = await introspect_token(token)
    if info is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    groups = await get_user_groups(token) if GLOBUS_GROUPS_SCOPE in info.scope else []
    if ALLOWED_GROUPS and not any(g in groups for g in ALLOWED_GROUPS):
        logger.warning("Denied %s — not a member of any allowed group", info.username)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your Globus identity is not authorised for this instance.",
        )

    return Researcher(
        id=info.sub,
        username=info.username,
        email=info.email,
        name=info.name,
        groups=groups,
        auth_method="globus",
    )


# Annotate handler parameters with this to receive the authenticated caller:
#     async def handler(user: CurrentUser) -> ...:
CurrentUser = Annotated[Researcher, Depends(get_current_user)]


# ── Startup safety check ──────────────────────────────────────────────────────

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "[::1]"}


def _bind_host() -> str:
    """Best-effort read of the interface uvicorn was told to bind.

    Covers the documented launch paths: ``--host`` on the command line (Docker
    Compose passes ``--host 0.0.0.0``) and the usual environment variables. A
    bare ``uvicorn api_server:app`` binds 127.0.0.1, which is why that is the
    fallback.
    """
    argv = sys.argv
    for i, arg in enumerate(argv):
        if arg == "--host" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--host="):
            return arg.split("=", 1)[1]
    return os.environ.get("UVICORN_HOST") or os.environ.get("HOST") or "127.0.0.1"


def assert_safe_configuration() -> None:
    """Refuse to serve unauthenticated traffic to anything but the local machine.

    Raises:
        RuntimeError: authentication is disabled while bound to a non-loopback
            interface, and the insecure-dev override was not set.
    """
    if GLOBUS_AUTH_ENABLED:
        logger.info("Globus authentication enabled")
        return

    host = _bind_host()
    if ALLOW_INSECURE_DEV:
        logger.warning(
            "AUTHENTICATION DISABLED on %s via CASSIOPEIA_ALLOW_INSECURE_DEV. "
            "Any caller can claim any researcher identity.",
            host,
        )
        return

    if host not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            f"Refusing to start: GLOBUS_AUTH_ENABLED is false but the server is "
            f"bound to {host!r}, which is reachable from other machines. Without "
            f"authentication any caller can read and modify any researcher's "
            f"papers, proposals and stored API keys.\n"
            f"  • To run for real:   set GLOBUS_AUTH_ENABLED=true and configure "
            f"GLOBUS_CLIENT_ID / GLOBUS_CLIENT_SECRET\n"
            f"  • For local work:    bind to 127.0.0.1\n"
            f"  • Deliberate excep.: set CASSIOPEIA_ALLOW_INSECURE_DEV=true"
        )

    logger.warning(
        "Authentication is DISABLED (development mode, bound to %s). "
        "The X-User-ID header is trusted — this is not an access control.",
        host,
    )


def auth_config() -> dict[str, Any]:
    """Public auth parameters the SPA needs to start the PKCE flow."""
    return {
        "enabled": GLOBUS_AUTH_ENABLED,
        "client_id": GLOBUS_CLIENT_ID,
        "auth_uri": GLOBUS_AUTH_URI,
        "token_uri": GLOBUS_TOKEN_URI,
        "redirect_uri": GLOBUS_REDIRECT_URI,
        "scopes": SCOPES,
    }
