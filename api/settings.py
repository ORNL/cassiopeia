# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Settings API router — provider selection and API key management.

The identity is taken from the authenticated principal, never from the
request: an API key is the most sensitive thing Cassiopeia stores, and a
caller-supplied researcher_id would let anyone overwrite or delete another
researcher's key.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from utils.auth import CurrentUser
from utils.user_settings import PROVIDERS, _get_store

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ApiKeyRequest(BaseModel):
    provider: str
    api_key: str


class ActiveProviderRequest(BaseModel):
    provider: str


@router.get("/providers")
async def get_providers(user: CurrentUser) -> list[dict]:
    """List all providers with configured/active status for the caller."""
    store = _get_store()
    active = store.get(user.id, "active_provider")
    return [
        {
            "provider": pid,
            "display_name": spec["display_name"],
            "configured": store.has_key(user.id, pid),
            "is_active": pid == active,
        }
        for pid, spec in PROVIDERS.items()
    ]


@router.get("/active")
async def get_active(user: CurrentUser) -> dict:
    """Return the active provider and whether it has a key stored."""
    store = _get_store()
    provider = store.get(user.id, "active_provider")
    configured = store.has_key(user.id, provider) if provider else False
    return {"provider": provider, "configured": configured}


@router.post("/api-key")
async def set_api_key(req: ApiKeyRequest, user: CurrentUser) -> dict:
    """Store an encrypted API key for *provider*. Auto-activates if none is set yet."""
    if req.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider!r}")
    if not req.api_key.strip():
        raise HTTPException(status_code=400, detail="api_key must not be empty")
    store = _get_store()
    store.set(user.id, f"api_key:{req.provider}", req.api_key.strip(), encrypt=True)
    if store.get(user.id, "active_provider") is None:
        store.set(user.id, "active_provider", req.provider)
    return {"status": "ok", "provider": req.provider}


@router.delete("/api-key/{provider}")
async def delete_api_key(provider: str, user: CurrentUser) -> dict:
    """Remove the caller's stored key for *provider* and clear active_provider if it matched."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider!r}")
    store = _get_store()
    store.delete(user.id, f"api_key:{provider}")
    if store.get(user.id, "active_provider") == provider:
        store.delete(user.id, "active_provider")
    return {"status": "ok"}


@router.post("/active-provider")
async def set_active_provider(req: ActiveProviderRequest, user: CurrentUser) -> dict:
    """Switch the active provider (key must already be stored)."""
    if req.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider!r}")
    store = _get_store()
    if not store.has_key(user.id, req.provider):
        raise HTTPException(
            status_code=400,
            detail=f"No API key stored for {req.provider!r} — save a key first.",
        )
    store.set(user.id, "active_provider", req.provider)
    return {"status": "ok", "provider": req.provider}
