# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Globus Auth integration — token validation and the authenticated principal.

Every handler that touches per-user data depends on :func:`get_current_user`
and reads the researcher identity from the returned :class:`Researcher`.  The
identity is *never* taken from a query parameter or request body: a caller who
can name a researcher_id can otherwise read and overwrite that researcher's
papers, proposals and stored API keys.

Three modes, selected by ``GLOBUS_AUTH_ENABLED``:

  enabled + Bearer token  — introspect against Globus, map to a Researcher
  enabled + no token      — 401
  disabled                — ``X-User-ID`` header, else "anonymous" (local dev)

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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

GLOBUS_AUTH_ENABLED = os.environ.get("GLOBUS_AUTH_ENABLED", "false").lower() == "true"
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
    # Globus id_token (a JWT) — usable as the Bearer for AmSC / MAG.
    access_token: str | None = None

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

    try:
        client = ConfidentialAppAuthClient(
            client_id=GLOBUS_CLIENT_ID, client_secret=GLOBUS_CLIENT_SECRET
        )
        data = client.oauth2_token_introspect(token).data
    except Exception as exc:
        logger.error("Globus token introspection failed: %s", exc)
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
        groups = GroupsClient(authorizer=AccessTokenAuthorizer(token)).get_my_groups()
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
        # The id_token JWT is what AmSC/MAG accept; fall back to the opaque token.
        access_token=request.headers.get("X-Id-Token") or token,
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
