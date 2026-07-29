# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Authentication API router — Globus PKCE support for the SPA.

The frontend runs the OAuth 2.0 authorization-code + PKCE flow itself, but the
code-for-token exchange is proxied through :func:`exchange_token` because
Globus's token endpoint does not send CORS headers for browser origins.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from utils.auth import (
    GLOBUS_CLIENT_SECRET,
    GLOBUS_TOKEN_URI,
    CurrentUser,
    auth_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthConfigResponse(BaseModel):
    """Everything the SPA needs to start the PKCE flow."""

    enabled: bool
    client_id: str
    auth_uri: str
    token_uri: str
    redirect_uri: str
    scopes: list[str]


class UserInfoResponse(BaseModel):
    """The caller's identity as the backend sees it."""

    id: str
    username: str
    email: str | None
    name: str | None
    display_name: str
    authenticated: bool
    auth_method: str


class TokenExchangeRequest(BaseModel):
    code: str
    code_verifier: str
    redirect_uri: str


class TokenExchangeResponse(BaseModel):
    access_token: str
    # JWT — required by APIs that validate via JWKS (AmSC / MAG).
    id_token: str | None = None
    expires_in: int
    token_type: str
    scope: str | None = None
    refresh_token: str | None = None


@router.get("/config", response_model=AuthConfigResponse)
async def get_config() -> AuthConfigResponse:
    """Return the Globus client configuration (no secrets)."""
    return AuthConfigResponse(**auth_config())


@router.get("/me", response_model=UserInfoResponse)
async def get_me(user: CurrentUser) -> UserInfoResponse:
    """Return the authenticated caller, or the development principal."""
    return UserInfoResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        name=user.name,
        display_name=user.display_name,
        authenticated=user.is_authenticated,
        auth_method=user.auth_method,
    )


@router.post("/token", response_model=TokenExchangeResponse)
async def exchange_token(req: TokenExchangeRequest) -> TokenExchangeResponse:
    """Exchange an authorization code for tokens (CORS proxy for Globus)."""
    config = auth_config()
    if not config["enabled"]:
        raise HTTPException(status_code=400, detail="Authentication is not enabled")

    form = {
        "grant_type": "authorization_code",
        "client_id": config["client_id"],
        "code": req.code,
        "redirect_uri": req.redirect_uri,
        "code_verifier": req.code_verifier,
    }
    if GLOBUS_CLIENT_SECRET:
        form["client_secret"] = GLOBUS_CLIENT_SECRET

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(GLOBUS_TOKEN_URI, data=form)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach Globus Auth: {exc}")

    if response.status_code != 200:
        try:
            err = response.json()
            detail = err.get("error_description") or err.get("error") or "Token exchange failed"
        except ValueError:
            detail = "Token exchange failed"
        logger.warning("Globus token exchange rejected: %s", detail)
        raise HTTPException(status_code=400, detail=detail)

    data = response.json()
    return TokenExchangeResponse(
        access_token=data["access_token"],
        id_token=data.get("id_token"),
        expires_in=data.get("expires_in", 3600),
        token_type=data.get("token_type", "Bearer"),
        scope=data.get("scope"),
        refresh_token=data.get("refresh_token"),
    )


@router.post("/logout")
async def logout() -> dict[str, str]:
    """No server-side session exists — the client discards its tokens."""
    return {"status": "ok"}
