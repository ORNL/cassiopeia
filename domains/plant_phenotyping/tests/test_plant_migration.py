# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Databases written before domain packs keep working after the upgrade."""

from __future__ import annotations

import json
import sqlite3

from utils.persistence import PaperStore

_OLD_PROFILE = {
    "researcher_id": "r1",
    "name": "Dr. Old",
    "plant_species": ["poplar"],
    "stress_types": ["drought"],
    "phenotyping_methods": ["thermal_imaging"],
    "expertise_keywords": ["ABA"],
    "priority_novelty": 0.7,
    "priority_relevance": 0.8,
    "priority_methodology": 0.5,
    "priority_reproducibility": 0.6,
    "available_equipment": ["VNIR hyperspectral imaging"],
    "time_range_months": 24,
    "source_targets": ["biorxiv"],
}

_OLD_RELEVANCE = {
    "overall": 0.71, "species_match": 1.0, "stress_match": 0.5, "method_match": 0.25,
    "recency": 0.8, "credibility": 0.4, "novelty": 0.6,
}


def _old_database(path):
    PaperStore(path).close()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 0")
    conn.execute("INSERT INTO profiles VALUES (?, ?)", ("r1", json.dumps(_OLD_PROFILE)))
    conn.execute(
        "INSERT INTO papers (paper_id, doi, data) VALUES (?, ?, ?)",
        ("p1", "10.1/x", json.dumps({"paper_id": "p1", "title": "Poplar drought", "source": "biorxiv"})),
    )
    conn.execute(
        "INSERT INTO user_papers (researcher_id, paper_id, relevance) VALUES (?, ?, ?)",
        ("r1", "p1", json.dumps(_OLD_RELEVANCE)),
    )
    conn.execute(
        "INSERT INTO llm_cache VALUES (?, ?)",
        ("p1", json.dumps({"species_match": 0.9, "stress_match": 0.8, "method_match": 0.7, "hypothesis": "h"})),
    )
    snap = {k: _OLD_PROFILE[k] for k in ("name", "plant_species", "stress_types", "source_targets")}
    conn.execute(
        "INSERT INTO sessions (session_id, researcher_id, timestamp, profile_snap) VALUES (?, ?, ?, ?)",
        ("s1", "r1", "2026-01-01T00:00:00", json.dumps(snap)),
    )
    conn.commit()
    conn.close()


def test_old_profiles_scores_cache_and_sessions_are_migrated(tmp_path):
    db = tmp_path / "cassiopeia.db"
    _old_database(db)

    store = PaperStore(db)
    try:
        [profile] = store.load_profiles()
        assert profile.facets == {
            "species": ["poplar"], "stress": ["drought"], "method": ["thermal_imaging"],
        }
        assert profile.context == {"instruments": ["VNIR hyperspectral imaging"]}
        assert profile.priority_relevance == 0.8
        assert profile.source_targets == ["biorxiv"]

        [paper] = store.load_papers("r1")
        assert paper.relevance.facet_scores == {"species": 1.0, "stress": 0.5, "method": 0.25}
        assert paper.relevance.overall == 0.71
        assert paper.paper.source == "biorxiv"

        assert store.load_llm_cache()["p1"] == {
            "facet_scores": {"species": 0.9, "stress": 0.8, "method": 0.7},
            "hypothesis": "h",
        }

        [session] = store.get_sessions("r1")
        assert session["profile"]["facets"] == {"species": ["poplar"], "stress": ["drought"]}
        assert "plant_species" not in session["profile"]
    finally:
        store.close()


def test_reopening_a_migrated_database_is_stable(tmp_path):
    db = tmp_path / "cassiopeia.db"
    _old_database(db)
    PaperStore(db).close()
    store = PaperStore(db)
    try:
        assert store.load_profiles()[0].facets["species"] == ["poplar"]
    finally:
        store.close()
