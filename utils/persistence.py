# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""SQLite-backed persistence for profiles, papers, and LLM cache."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from models.schemas import (
    CredibilityLevel,
    PaperMetadata,
    RelevanceScore,
    ResearcherProfile,
    ScoredPaper,
    SourceType,
    StressType,
    PhenotypingMethod,
)


_DEFAULT_DB = Path(__file__).parent.parent / "cassiopeia.db"


class PaperStore:
    """Thread-safe SQLite store for profiles, papers, and LLM score cache."""

    def __init__(self, db_path: str | Path = _DEFAULT_DB) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_tables(self) -> None:
        self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS profiles (
                researcher_id TEXT PRIMARY KEY,
                data          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS papers (
                paper_id      TEXT PRIMARY KEY,
                researcher_id TEXT NOT NULL,
                doi           TEXT,
                data          TEXT NOT NULL,
                rag_indexed   INTEGER NOT NULL DEFAULT 0,
                added_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_papers_researcher
                ON papers (researcher_id);
            CREATE INDEX IF NOT EXISTS idx_papers_doi
                ON papers (doi);
            CREATE INDEX IF NOT EXISTS idx_papers_rag
                ON papers (rag_indexed);
            CREATE INDEX IF NOT EXISTS idx_papers_added_at
                ON papers (researcher_id, added_at);

            CREATE TABLE IF NOT EXISTS user_logins (
                researcher_id TEXT PRIMARY KEY,
                last_login    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS llm_cache (
                paper_id TEXT PRIMARY KEY,
                data     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS verify_cache (
                cache_key TEXT PRIMARY KEY,
                data      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                session_id    TEXT PRIMARY KEY,
                researcher_id TEXT NOT NULL,
                timestamp     TEXT NOT NULL,
                profile_snap  TEXT NOT NULL,
                n_papers      INTEGER NOT NULL DEFAULT 0,
                n_proposals   INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_researcher
                ON sessions (researcher_id, timestamp);

            CREATE TABLE IF NOT EXISTS ratings (
                proposal_id   TEXT NOT NULL,
                researcher_id TEXT NOT NULL,
                suggestion    TEXT NOT NULL,
                theme         TEXT,
                rating        INTEGER NOT NULL CHECK(rating IN (1, -1)),
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (proposal_id, researcher_id)
            );
            """
        )
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Apply forward-compatible schema migrations for existing databases."""
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(papers)")}
        if "added_at" not in cols:
            self._conn.execute(
                "ALTER TABLE papers ADD COLUMN added_at TEXT NOT NULL DEFAULT (datetime('now'))"
            )
            self._conn.commit()
        sess_cols = {row[1] for row in self._conn.execute("PRAGMA table_info(sessions)")}
        if "proposals_snap" not in sess_cols:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN proposals_snap TEXT")
            self._conn.commit()

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    def save_profile(self, profile: ResearcherProfile) -> None:
        data = {
            "researcher_id": profile.researcher_id,
            "name": profile.name,
            "plant_species": profile.plant_species,
            "stress_types": [s.value for s in profile.stress_types],
            "phenotyping_methods": [m.value for m in profile.phenotyping_methods],
            "expertise_keywords": profile.expertise_keywords,
            "methodology_preferences": profile.methodology_preferences,
            "priority_novelty": profile.priority_novelty,
            "priority_relevance": profile.priority_relevance,
            "priority_methodology": profile.priority_methodology,
            "priority_reproducibility": profile.priority_reproducibility,
            "available_equipment": profile.available_equipment,
            "time_range_months": profile.time_range_months,
            "source_targets": profile.source_targets,
        }
        self._conn.execute(
            "INSERT OR REPLACE INTO profiles (researcher_id, data) VALUES (?, ?)",
            (profile.researcher_id, json.dumps(data)),
        )
        self._conn.commit()

    def load_profiles(self) -> list[ResearcherProfile]:
        rows = self._conn.execute("SELECT data FROM profiles").fetchall()
        profiles = []
        for row in rows:
            d = json.loads(row["data"])
            profiles.append(
                ResearcherProfile(
                    researcher_id=d["researcher_id"],
                    name=d["name"],
                    plant_species=d.get("plant_species", []),
                    stress_types=[StressType(s) for s in d.get("stress_types", [])],
                    phenotyping_methods=[
                        PhenotypingMethod(m) for m in d.get("phenotyping_methods", [])
                    ],
                    expertise_keywords=d.get("expertise_keywords", []),
                    methodology_preferences=d.get("methodology_preferences", []),
                    priority_novelty=d.get("priority_novelty", 0.5),
                    priority_relevance=d.get("priority_relevance", 0.5),
                    priority_methodology=d.get("priority_methodology", 0.5),
                    priority_reproducibility=d.get("priority_reproducibility", 0.5),
                    available_equipment=d.get("available_equipment", []),
                    time_range_months=d.get("time_range_months", 12),
                    source_targets=d.get("source_targets", []),
                )
            )
        return profiles

    # ------------------------------------------------------------------
    # Papers
    # ------------------------------------------------------------------

    def save_paper(self, scored: ScoredPaper, researcher_id: str) -> None:
        """Insert or replace a scored paper (preserves rag_indexed flag and added_at)."""
        paper = scored.paper
        existing = self._conn.execute(
            "SELECT rag_indexed, added_at FROM papers WHERE paper_id = ?", (paper.paper_id,)
        ).fetchone()
        rag_indexed = existing["rag_indexed"] if existing else 0
        added_at = existing["added_at"] if existing else datetime.now().isoformat()

        pub = None
        if paper.published_date:
            pub = paper.published_date.isoformat()

        data = {
            "paper_id": paper.paper_id,
            "title": paper.title,
            "abstract": paper.abstract,
            "authors": paper.authors,
            "journal": paper.journal,
            "published": pub,
            "doi": paper.doi,
            "url": paper.url,
            "source": paper.source.value,
            "keywords": paper.keywords,
            "is_open_access": paper.is_open_access,
            "citation_count": paper.citation_count,
            "relevance": {
                "overall": scored.relevance.overall,
                "species_match": scored.relevance.species_match,
                "stress_match": scored.relevance.stress_match,
                "method_match": scored.relevance.method_match,
                "recency": scored.relevance.recency,
                "credibility": scored.relevance.credibility,
                "novelty": scored.relevance.novelty,
            },
            "credibility": scored.credibility.value,
            "suggested_combinations": scored.suggested_combinations,
            "source_queries": scored.source_queries,
            "appl_feasibility_notes": scored.appl_feasibility_notes,
        }
        self._conn.execute(
            """INSERT OR REPLACE INTO papers
               (paper_id, researcher_id, doi, data, rag_indexed, added_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (paper.paper_id, researcher_id, paper.doi, json.dumps(data), rag_indexed, added_at),
        )
        self._conn.commit()

    def load_papers(self, researcher_id: str) -> list[ScoredPaper]:
        rows = self._conn.execute(
            "SELECT data FROM papers WHERE researcher_id = ?", (researcher_id,)
        ).fetchall()
        return [_row_to_scored(json.loads(row["data"])) for row in rows]

    def known_dois(self, researcher_id: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT doi FROM papers WHERE researcher_id = ? AND doi IS NOT NULL",
            (researcher_id,),
        ).fetchall()
        return {row["doi"] for row in rows}

    def get_unindexed_papers(self) -> list[tuple[str, str, str, dict]]:
        """Return (paper_id, researcher_id, abstract, metadata) for un-indexed papers."""
        rows = self._conn.execute(
            "SELECT paper_id, researcher_id, data FROM papers WHERE rag_indexed = 0"
        ).fetchall()
        result = []
        for row in rows:
            d = json.loads(row["data"])
            abstract = d.get("abstract") or ""
            if abstract:
                meta = {
                    "title": d.get("title", ""),
                    "journal": d.get("journal") or "",
                    "researcher_id": row["researcher_id"],
                    "doi": d.get("doi") or "",
                }
                result.append((row["paper_id"], row["researcher_id"], abstract, meta))
        return result

    def get_new_papers_since(self, researcher_id: str, since: str) -> list[dict[str, Any]]:
        """Return paper data rows added after *since* (ISO timestamp) for a researcher."""
        rows = self._conn.execute(
            "SELECT data, added_at FROM papers WHERE researcher_id = ? AND added_at > ? ORDER BY added_at DESC",
            (researcher_id, since),
        ).fetchall()
        result = []
        for row in rows:
            d = json.loads(row["data"])
            d["added_at"] = row["added_at"]
            result.append(d)
        return result

    def record_login(self, researcher_id: str) -> None:
        """Record the current time as the researcher's last login."""
        self._conn.execute(
            "INSERT OR REPLACE INTO user_logins (researcher_id, last_login) VALUES (?, ?)",
            (researcher_id, datetime.now().isoformat()),
        )
        self._conn.commit()

    def get_last_login(self, researcher_id: str) -> str | None:
        """Return the researcher's previous last-login timestamp, or None if first visit."""
        row = self._conn.execute(
            "SELECT last_login FROM user_logins WHERE researcher_id = ?",
            (researcher_id,),
        ).fetchone()
        return row["last_login"] if row else None

    def get_paper_metadata(self, paper_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT data FROM papers WHERE paper_id = ?", (paper_id,)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def mark_indexed(self, paper_ids: list[str]) -> None:
        self._conn.executemany(
            "UPDATE papers SET rag_indexed = 1 WHERE paper_id = ?",
            [(pid,) for pid in paper_ids],
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # LLM cache
    # ------------------------------------------------------------------

    def save_llm_cache(self, cache: dict[str, dict]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO llm_cache (paper_id, data) VALUES (?, ?)",
            [(k, json.dumps(v)) for k, v in cache.items()],
        )
        self._conn.commit()

    def load_llm_cache(self) -> dict[str, dict]:
        rows = self._conn.execute("SELECT paper_id, data FROM llm_cache").fetchall()
        return {row["paper_id"]: json.loads(row["data"]) for row in rows}

    # ------------------------------------------------------------------
    # Verification cache (Augmentation A)
    # ------------------------------------------------------------------

    def get_verify_cache(self, cache_key: str) -> dict | None:
        """Return a cached verification result by (paper_id, insight_hash) key."""
        row = self._conn.execute(
            "SELECT data FROM verify_cache WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def set_verify_cache(self, cache_key: str, data: dict) -> None:
        """Persist a verification result."""
        self._conn.execute(
            "INSERT OR REPLACE INTO verify_cache (cache_key, data) VALUES (?, ?)",
            (cache_key, json.dumps(data)),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def save_session(
        self,
        session_id: str,
        researcher_id: str,
        profile_snap: dict,
        n_papers: int,
        n_proposals: int,
        proposals_snap: list | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO sessions
               (session_id, researcher_id, timestamp, profile_snap, n_papers, n_proposals, proposals_snap)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                researcher_id,
                datetime.now().isoformat(),
                json.dumps(profile_snap),
                n_papers,
                n_proposals,
                json.dumps(proposals_snap) if proposals_snap is not None else None,
            ),
        )
        self._conn.commit()

    def get_last_proposals(self, researcher_id: str) -> list[dict]:
        """Return the rag_combos from the researcher's most recent session."""
        row = self._conn.execute(
            """SELECT proposals_snap FROM sessions
               WHERE researcher_id = ? AND proposals_snap IS NOT NULL
               ORDER BY timestamp DESC LIMIT 1""",
            (researcher_id,),
        ).fetchone()
        if row and row["proposals_snap"]:
            return json.loads(row["proposals_snap"])
        return []

    def get_sessions(self, researcher_id: str, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            """SELECT session_id, timestamp, profile_snap, n_papers, n_proposals
               FROM sessions WHERE researcher_id = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (researcher_id, limit),
        ).fetchall()
        return [
            {
                "session_id": row["session_id"],
                "timestamp": row["timestamp"],
                "profile": json.loads(row["profile_snap"]),
                "n_papers": row["n_papers"],
                "n_proposals": row["n_proposals"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Ratings (proposal feedback)
    # ------------------------------------------------------------------

    def save_rating(
        self,
        proposal_id: str,
        researcher_id: str,
        suggestion: str,
        theme: str | None,
        rating: int,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO ratings
               (proposal_id, researcher_id, suggestion, theme, rating)
               VALUES (?, ?, ?, ?, ?)""",
            (proposal_id, researcher_id, suggestion, theme or "", rating),
        )
        self._conn.commit()

    def get_liked_proposals(self, researcher_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT suggestion, theme FROM ratings WHERE researcher_id = ? AND rating = 1",
            (researcher_id,),
        ).fetchall()
        return [{"suggestion": row["suggestion"], "theme": row["theme"]} for row in rows]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def _row_to_scored(d: dict) -> ScoredPaper:
    pub = None
    if d.get("published"):
        try:
            pub = datetime.fromisoformat(d["published"])
        except ValueError:
            pub = None

    try:
        source = SourceType(d.get("source", "other"))
    except ValueError:
        source = SourceType.OTHER

    paper = PaperMetadata(
        paper_id=d["paper_id"],
        title=d.get("title", ""),
        authors=d.get("authors", []),
        abstract=d.get("abstract", ""),
        source=source,
        doi=d.get("doi"),
        url=d.get("url"),
        published_date=pub,
        journal=d.get("journal"),
        keywords=d.get("keywords", []),
        is_open_access=d.get("is_open_access", False),
        citation_count=d.get("citation_count", 0),
    )

    r = d.get("relevance", {})
    relevance = RelevanceScore(
        overall=r.get("overall", 0.0),
        species_match=r.get("species_match", 0.0),
        stress_match=r.get("stress_match", 0.0),
        method_match=r.get("method_match", 0.0),
        recency=r.get("recency", 0.0),
        credibility=r.get("credibility", 0.0),
        novelty=r.get("novelty", 0.0),
    )

    try:
        cred = CredibilityLevel(d.get("credibility", "preliminary"))
    except ValueError:
        cred = CredibilityLevel.PRELIMINARY

    return ScoredPaper(
        paper=paper,
        relevance=relevance,
        credibility=cred,
        suggested_combinations=d.get("suggested_combinations", []),
        source_queries=d.get("source_queries", []),
        appl_feasibility_notes=d.get("appl_feasibility_notes", ""),
    )
