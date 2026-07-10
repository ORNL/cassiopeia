# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Settings API router — provider selection and API key management."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from utils.user_settings import PROVIDERS, _get_store

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ApiKeyRequest(BaseModel):
    provider: str
    api_key: str


class ActiveProviderRequest(BaseModel):
    provider: str


@router.get("/providers")
async def get_providers(researcher_id: str) -> list[dict]:
    """List all providers with configured/active status for *researcher_id*."""
    store = _get_store()
    active = store.get(researcher_id, "active_provider")
    return [
        {
            "provider": pid,
            "display_name": spec["display_name"],
            "configured": store.has_key(researcher_id, pid),
            "is_active": pid == active,
        }
        for pid, spec in PROVIDERS.items()
    ]


@router.get("/active")
async def get_active(researcher_id: str) -> dict:
    """Return the active provider and whether it has a key stored."""
    store = _get_store()
    provider = store.get(researcher_id, "active_provider")
    configured = store.has_key(researcher_id, provider) if provider else False
    return {"provider": provider, "configured": configured}


@router.post("/api-key")
async def set_api_key(researcher_id: str, req: ApiKeyRequest) -> dict:
    """Store an encrypted API key for *provider*. Auto-activates if none is set yet."""
    if req.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider!r}")
    if not req.api_key.strip():
        raise HTTPException(status_code=400, detail="api_key must not be empty")
    store = _get_store()
    store.set(researcher_id, f"api_key:{req.provider}", req.api_key.strip(), encrypt=True)
    if store.get(researcher_id, "active_provider") is None:
        store.set(researcher_id, "active_provider", req.provider)
    return {"status": "ok", "provider": req.provider}


@router.delete("/api-key/{provider}")
async def delete_api_key(researcher_id: str, provider: str) -> dict:
    """Remove the stored key for *provider* and clear active_provider if it matched."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider!r}")
    store = _get_store()
    store.delete(researcher_id, f"api_key:{provider}")
    if store.get(researcher_id, "active_provider") == provider:
        store.delete(researcher_id, "active_provider")
    return {"status": "ok"}


@router.post("/active-provider")
async def set_active_provider(researcher_id: str, req: ActiveProviderRequest) -> dict:
    """Switch the active provider (key must already be stored)."""
    if req.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider!r}")
    store = _get_store()
    if not store.has_key(researcher_id, req.provider):
        raise HTTPException(
            status_code=400,
            detail=f"No API key stored for {req.provider!r} — save a key first.",
        )
    store.set(researcher_id, "active_provider", req.provider)
    return {"status": "ok", "provider": req.provider}
