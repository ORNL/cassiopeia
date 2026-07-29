# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tenancy guarantees for the HTTP API.

These lock in the properties that keep one researcher's library, proposals and
API keys away from another:

  1. no endpoint accepts a researcher identity from the client
  2. every endpoint touching per-researcher data rejects anonymous callers
  3. two identities cannot see or overwrite each other's data
  4. authentication cannot be silently disabled on a public interface

Test 1 is the important one: it fails when someone adds a handler with a
``researcher_id`` parameter, which is exactly how this class of bug returns.
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path
from typing import get_type_hints

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

import api_server
import utils.auth as auth_mod
from utils.persistence import PaperStore

# Parameter names that would let a caller name someone else.
IDENTITY_NAMES = {"researcher_id", "researcherId", "user_id", "userId", "sub", "username"}

# Endpoints that legitimately serve unauthenticated callers.
PUBLIC_PATHS = {
    "/api/auth/config",
    "/api/auth/token",
    "/api/auth/logout",
    "/api/config",
    "/api/status",
    "/api/rag/status",
}


def _hints(endpoint) -> dict:
    """Resolved type hints for *endpoint*.

    `from __future__ import annotations` leaves raw annotations as strings, so
    inspecting `parameter.annotation` directly would silently match nothing and
    every check below would pass for the wrong reason.
    """
    try:
        return get_type_hints(endpoint, include_extras=True)
    except Exception:  # pragma: no cover - unresolvable annotation
        return {}


def _routes():
    """(method, path, endpoint) for every API route."""
    out = []
    for r in api_server.app.routes:
        methods = getattr(r, "methods", None)
        if not methods or not r.path.startswith("/api"):
            continue
        for m in sorted(methods - {"HEAD", "OPTIONS"}):
            out.append((m, r.path, r.endpoint))
    return out


@pytest.fixture
def client(monkeypatch):
    """API client in development mode, backed by a throwaway store."""
    monkeypatch.setattr(auth_mod, "GLOBUS_AUTH_ENABLED", False)
    monkeypatch.setattr(
        api_server, "_paper_store", PaperStore(Path(tempfile.mkdtemp()) / "t.db")
    )
    return TestClient(api_server.app)


@pytest.fixture
def settings_db(monkeypatch, tmp_path):
    """Point the encrypted settings store at a throwaway database."""
    from utils import user_settings

    monkeypatch.setattr(user_settings, "_store", None)
    monkeypatch.setattr(user_settings, "_DEFAULT_DB", tmp_path / "settings.db")
    monkeypatch.setenv("CASSIOPEIA_SETTINGS_SECRET", "test-secret")
    yield
    monkeypatch.setattr(user_settings, "_store", None)


ALICE = {"X-User-ID": "alice"}
BOB = {"X-User-ID": "bob"}


# ---------------------------------------------------------------------------
# 1. No endpoint accepts a caller-supplied identity
# ---------------------------------------------------------------------------

def test_no_route_has_an_identity_path_or_query_parameter():
    offenders = []
    for method, path, endpoint in _routes():
        for name in inspect.signature(endpoint).parameters:
            if name in IDENTITY_NAMES:
                offenders.append(f"{method} {path} -> parameter {name!r}")
        # A Pydantic *request* body carrying an identity field is the same bug.
        # Response models are exempt: reporting who you are is the point of /me.
        for key, ann in _hints(endpoint).items():
            if key == "return":
                continue
            if isinstance(ann, type) and issubclass(ann, BaseModel):
                for field in ann.model_fields:
                    if field in IDENTITY_NAMES:
                        offenders.append(f"{method} {path} -> {ann.__name__}.{field}")
    assert not offenders, (
        "Identity must come from the authenticated principal, never the client:\n  "
        + "\n  ".join(offenders)
    )


def test_no_route_path_contains_an_identity_segment():
    bad = [f"{m} {p}" for m, p, _ in _routes() if "{researcher_id}" in p]
    assert not bad, f"Routes still address a researcher by path: {bad}"


def test_every_non_public_route_depends_on_the_principal():
    missing = []
    for method, path, endpoint in _routes():
        if path in PUBLIC_PATHS:
            continue
        if not any(h is auth_mod.CurrentUser for h in _hints(endpoint).values()):
            missing.append(f"{method} {path}")
    assert not missing, f"Routes without an authenticated principal: {missing}"


# ---------------------------------------------------------------------------
# 2. Anonymous callers are rejected when auth is on
# ---------------------------------------------------------------------------

def test_protected_routes_reject_anonymous_callers(monkeypatch):
    monkeypatch.setattr(auth_mod, "GLOBUS_AUTH_ENABLED", True)
    c = TestClient(api_server.app)
    checked = 0
    for method, path, _ in _routes():
        if path in PUBLIC_PATHS or "{" in path:
            continue
        res = c.request(method, path, json={} if method in {"POST", "PUT"} else None)
        assert res.status_code == 401, f"{method} {path} returned {res.status_code}, not 401"
        checked += 1
    assert checked >= 10, "expected to cover the per-researcher endpoints"


def test_development_header_does_not_authenticate_when_auth_is_on(monkeypatch):
    monkeypatch.setattr(auth_mod, "GLOBUS_AUTH_ENABLED", True)
    c = TestClient(api_server.app)
    assert c.get("/api/sessions", headers=ALICE).status_code == 401


# ---------------------------------------------------------------------------
# 3. Identities are isolated from one another
# ---------------------------------------------------------------------------

def test_identity_comes_from_the_credential(client):
    assert client.get("/api/auth/me", headers=ALICE).json()["id"] == "alice"
    assert client.get("/api/auth/me", headers=BOB).json()["id"] == "bob"


def test_a_stray_researcher_id_parameter_is_ignored(client):
    body = client.get("/api/sessions?researcher_id=alice", headers=BOB).json()
    assert body == [], "bob received data by naming alice in a query parameter"


def test_sessions_are_private(client):
    api_server._paper_store.save_session(
        session_id="s1", researcher_id="alice",
        profile_snap={"name": "alice"}, n_papers=3, n_proposals=1,
    )
    assert len(client.get("/api/sessions", headers=ALICE).json()) == 1
    assert client.get("/api/sessions", headers=BOB).json() == []


def test_feedback_is_recorded_against_the_caller(client):
    client.post(
        "/api/feedback",
        json={"proposal_id": "p1", "suggestion": "alice idea", "rating": 1},
        headers=ALICE,
    )
    store = api_server._paper_store
    assert [d["suggestion"] for d in store.get_liked_proposals("alice")] == ["alice idea"]
    assert store.get_liked_proposals("bob") == []


def test_api_keys_are_private(client, settings_db):
    saved = client.post(
        "/api/settings/api-key",
        json={"provider": "anthropic", "api_key": "sk-alice"},
        headers=ALICE,
    )
    assert saved.status_code == 200

    assert client.get("/api/settings/active", headers=ALICE).json()["configured"] is True
    assert client.get("/api/settings/active", headers=BOB).json()["configured"] is False
    # bob must not learn which providers alice has configured
    assert not any(p["configured"] for p in client.get("/api/settings/providers", headers=BOB).json())


def test_one_researcher_cannot_delete_anothers_api_key(client, settings_db):
    client.post(
        "/api/settings/api-key",
        json={"provider": "anthropic", "api_key": "sk-alice"},
        headers=ALICE,
    )
    client.delete("/api/settings/api-key/anthropic", headers=BOB)
    assert client.get("/api/settings/active", headers=ALICE).json()["configured"] is True


# ---------------------------------------------------------------------------
# 4. Authentication cannot be disabled on a public interface
# ---------------------------------------------------------------------------

def test_start_refused_when_auth_disabled_on_public_interface(monkeypatch):
    monkeypatch.setattr(auth_mod, "GLOBUS_AUTH_ENABLED", False)
    monkeypatch.setattr(auth_mod, "ALLOW_INSECURE_DEV", False)
    monkeypatch.setattr(auth_mod.sys, "argv", ["uvicorn", "--host", "0.0.0.0"])
    with pytest.raises(RuntimeError, match="Refusing to start"):
        auth_mod.assert_safe_configuration()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_start_allowed_when_auth_disabled_on_loopback(monkeypatch, host):
    monkeypatch.setattr(auth_mod, "GLOBUS_AUTH_ENABLED", False)
    monkeypatch.setattr(auth_mod, "ALLOW_INSECURE_DEV", False)
    monkeypatch.setattr(auth_mod.sys, "argv", ["uvicorn", "--host", host])
    auth_mod.assert_safe_configuration()


def test_bare_uvicorn_invocation_is_treated_as_loopback(monkeypatch):
    """`uvicorn api_server:app --port 8000` binds 127.0.0.1 — the dev path."""
    monkeypatch.setattr(auth_mod, "GLOBUS_AUTH_ENABLED", False)
    monkeypatch.setattr(auth_mod, "ALLOW_INSECURE_DEV", False)
    monkeypatch.setattr(auth_mod.sys, "argv", ["uvicorn", "api_server:app", "--port", "8000"])
    monkeypatch.delenv("UVICORN_HOST", raising=False)
    monkeypatch.delenv("HOST", raising=False)
    auth_mod.assert_safe_configuration()


def test_explicit_override_permits_public_bind_without_auth(monkeypatch):
    monkeypatch.setattr(auth_mod, "GLOBUS_AUTH_ENABLED", False)
    monkeypatch.setattr(auth_mod, "ALLOW_INSECURE_DEV", True)
    monkeypatch.setattr(auth_mod.sys, "argv", ["uvicorn", "--host", "0.0.0.0"])
    auth_mod.assert_safe_configuration()


def test_auth_enabled_by_default(monkeypatch):
    """An unset GLOBUS_AUTH_ENABLED must mean ON — the failure mode is closed."""
    monkeypatch.delenv("GLOBUS_AUTH_ENABLED", raising=False)
    assert os_default_enabled() is True


def os_default_enabled() -> bool:
    import os
    return os.environ.get("GLOBUS_AUTH_ENABLED", "true").lower() == "true"
