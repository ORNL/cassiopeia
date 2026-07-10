# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Per-user LLM provider settings with Fernet-encrypted API key storage.

Provider configs are hardcoded — users pick a provider and paste one API key;
models are fixed per provider (no per-user model selection):

  anthropic  — direct Anthropic API  (Haiku scoring / Sonnet reasoning)
  amsc       — Model Access Gateway (MAG), Anthropic-compatible proxy
  azure      — Azure OpenAI at the ORNL APPL deployment (gpt-4 for both roles)

Usage::

    from utils.user_settings import get_llm_config, LLMNotConfiguredError

    try:
        cfg = get_llm_config(researcher_id)
        response = await litellm.acompletion(**cfg.for_scoring(), messages=[...])
    except LLMNotConfiguredError:
        raise HTTPException(status_code=412, detail="LLM provider not configured")

Set CASSIOPEIA_SETTINGS_SECRET to a stable secret for production.
If absent the key falls back to the host MAC address (fine for dev/single-host).
Generate a secret with: python -c "import secrets; print(secrets.token_urlsafe(48))"
"""

from __future__ import annotations

import base64
import logging
import os
import sqlite3
import stat
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# ── Provider registry ──────────────────────────────────────────────────────────

PROVIDERS: dict[str, dict] = {
    "amsc": {
        "display_name": "Model Access Gateway (MAG)",
        "scoring_model": os.environ.get("AMSC_SCORING_MODEL", "anthropic/claude-haiku-4-5"),
        "reasoning_model": os.environ.get("AMSC_REASONING_MODEL", "anthropic/claude-sonnet-4-6"),
        "api_base": "https://api.i2-core.american-science-cloud.org",
    },
    "anthropic": {
        "display_name": "Anthropic",
        "scoring_model": os.environ.get(
            "ANTHROPIC_SCORING_MODEL", "anthropic/claude-haiku-4-5-20251001"
        ),
        "reasoning_model": os.environ.get(
            "ANTHROPIC_REASONING_MODEL", "anthropic/claude-sonnet-4-6"
        ),
    },
    "azure": {
        "display_name": "Azure OpenAI",
        "scoring_model": os.environ.get("AZURE_SCORING_MODEL", "azure/gpt-4"),
        "reasoning_model": os.environ.get("AZURE_REASONING_MODEL", "azure/gpt-4"),
        "api_base": "https://aoai-eus-wrkflowecosystems.openai.azure.com/",
        "api_version": "2024-02-01",
    },
}


# ── LLMConfig ──────────────────────────────────────────────────────────────────

@dataclass
class LLMConfig:
    """Runtime LLM parameters for a researcher — unpack into litellm.acompletion calls."""

    provider: str
    scoring_model: str
    reasoning_model: str
    api_key: str
    api_base: str | None = None
    api_version: str | None = None

    def for_scoring(self) -> dict:
        """LiteLLM kwargs for fast/cheap calls (haiku-tier)."""
        return self._build(self.scoring_model)

    def for_reasoning(self) -> dict:
        """LiteLLM kwargs for complex reasoning calls (sonnet-tier)."""
        return self._build(self.reasoning_model)

    def _build(self, model: str) -> dict:
        d: dict = {"model": model, "api_key": self.api_key}
        if self.api_base:
            d["api_base"] = self.api_base
        if self.api_version:
            d["api_version"] = self.api_version
        return d


class LLMNotConfiguredError(Exception):
    """No LLM provider configured for this researcher."""


# ── Encryption helpers ────────────────────────────────────────────────────────

def _mac_secret() -> str:
    import uuid
    return uuid.UUID(int=uuid.getnode()).hex


def _fernet(user_id: str) -> Fernet:
    secret = os.environ.get("CASSIOPEIA_SETTINGS_SECRET") or _mac_secret()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=user_id.encode(),
        iterations=100_000,
        backend=default_backend(),
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    return Fernet(key)


# ── Persistent store ──────────────────────────────────────────────────────────

_DEFAULT_DB = (
    Path(os.environ["CASSIOPEIA_SETTINGS_DB"])
    if "CASSIOPEIA_SETTINGS_DB" in os.environ
    else Path.home() / ".cassiopeia" / "user_settings.db"
)


class UserSettingsStore:
    """Per-user key-value store backed by SQLite with optional Fernet encryption."""

    def __init__(self, db_path: Path = _DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                user_id   TEXT NOT NULL,
                key       TEXT NOT NULL,
                value     TEXT NOT NULL,
                encrypted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, key)
            )
        """)
        self._conn.commit()
        try:
            db_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
        except OSError:
            pass  # read-only FS or non-POSIX

    def set(self, user_id: str, key: str, value: str, encrypt: bool = False) -> None:
        stored = _fernet(user_id).encrypt(value.encode()).decode() if encrypt else value
        self._conn.execute(
            "INSERT OR REPLACE INTO settings (user_id, key, value, encrypted) VALUES (?, ?, ?, ?)",
            (user_id, key, stored, int(encrypt)),
        )
        self._conn.commit()

    def get(self, user_id: str, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value, encrypted FROM settings WHERE user_id = ? AND key = ?",
            (user_id, key),
        ).fetchone()
        if row is None:
            return None
        value, encrypted = row["value"], bool(row["encrypted"])
        if encrypted:
            try:
                return _fernet(user_id).decrypt(value.encode()).decode()
            except Exception:
                logger.warning("Failed to decrypt %r for %s — removing stale entry", key, user_id)
                self.delete(user_id, key)
                return None
        return value

    def delete(self, user_id: str, key: str) -> None:
        self._conn.execute(
            "DELETE FROM settings WHERE user_id = ? AND key = ?", (user_id, key)
        )
        self._conn.commit()

    def has_key(self, user_id: str, provider: str) -> bool:
        return self.get(user_id, f"api_key:{provider}") is not None


# Module-level singleton — lazy-initialised on first use.
_store: UserSettingsStore | None = None


def _get_store() -> UserSettingsStore:
    global _store
    if _store is None:
        _store = UserSettingsStore()
    return _store


# ── Public API ─────────────────────────────────────────────────────────────────

def get_llm_config(researcher_id: str) -> LLMConfig:
    """Return LLMConfig for *researcher_id*.

    Raises :class:`LLMNotConfiguredError` when no provider is active or the
    API key is missing — callers should map this to HTTP 412.
    """
    store = _get_store()
    provider = store.get(researcher_id, "active_provider")
    if not provider or provider not in PROVIDERS:
        raise LLMNotConfiguredError(
            f"No LLM provider configured for {researcher_id!r}. "
            "Open Settings and add an API key."
        )
    api_key = store.get(researcher_id, f"api_key:{provider}")
    if not api_key:
        raise LLMNotConfiguredError(
            f"API key missing for provider {provider!r}. "
            "Open Settings to enter your key."
        )
    spec = PROVIDERS[provider]
    return LLMConfig(
        provider=provider,
        scoring_model=spec["scoring_model"],
        reasoning_model=spec["reasoning_model"],
        api_key=api_key,
        api_base=spec.get("api_base"),
        api_version=spec.get("api_version"),
    )
