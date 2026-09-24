# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Where a deployment keeps its SQLite database and ChromaDB store.

Each domain pack gets its own directory, ``data/<pack>/``, so selecting a
different pack never mixes corpora.  ``DB_PATH`` and ``RAG_PERSIST_DIR``
override the defaults (the Docker image sets both to a per-deployment volume).

Databases created before per-pack directories lived at the project root.  The
first time the pack that owns such a database starts, it is moved into place.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from domains import current_domain, selected_pack_name

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = PROJECT_ROOT / "data"

DB_NAME = "cassiopeia.db"
RAG_DIR_NAME = "chroma_db"
_SQLITE_SIDECARS = ("-wal", "-shm")


def stamped_pack(db_path: Path) -> str | None:
    """The domain pack recorded in a database, or None (unstamped or unreadable)."""
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM deployment WHERE key = 'domain_pack'"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _migrate_legacy(pack: str, target: Path) -> None:
    """Move a pre-pack-directory database belonging to *pack* into *target*."""
    legacy_db = PROJECT_ROOT / DB_NAME
    if not legacy_db.is_file() or (target / DB_NAME).exists():
        return
    # An unstamped database predates domain packs: it belongs to whichever
    # pack the deployment has selected.
    owner = stamped_pack(legacy_db) or selected_pack_name()
    if owner != pack:
        return
    try:
        # Fold the WAL into the main file so only one file needs moving.
        conn = sqlite3.connect(str(legacy_db))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("Not moving %s into %s (database busy: %s)", legacy_db, target, exc)
        return
    target.mkdir(parents=True, exist_ok=True)
    for suffix in ("", *_SQLITE_SIDECARS):
        src = legacy_db.with_name(DB_NAME + suffix)
        if src.exists():
            src.rename(target / src.name)
    legacy_rag = PROJECT_ROOT / RAG_DIR_NAME
    if legacy_rag.is_dir() and not (target / RAG_DIR_NAME).exists():
        legacy_rag.rename(target / RAG_DIR_NAME)
    logger.info("Moved the %s database and vector store into %s", pack, target)


def pack_data_dir(pack: str, *, migrate: bool = True) -> Path:
    """Data directory of *pack* (``data/<pack>/``), adopting a legacy database."""
    target = DATA_ROOT / pack
    if migrate:
        _migrate_legacy(pack, target)
    return target


def default_db_path() -> str:
    """``DB_PATH``, else the active pack's ``data/<pack>/cassiopeia.db``."""
    return os.environ.get("DB_PATH") or str(pack_data_dir(current_domain().name) / DB_NAME)


def default_rag_dir() -> str:
    """``RAG_PERSIST_DIR``, else the active pack's ``data/<pack>/chroma_db``."""
    return os.environ.get("RAG_PERSIST_DIR") or str(
        pack_data_dir(current_domain().name) / RAG_DIR_NAME
    )
