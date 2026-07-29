# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for utils/persistence.py — PaperStore round-trips and helpers."""

from __future__ import annotations

from datetime import datetime

import pytest

from models.schemas import (
    CredibilityLevel,
    PaperMetadata,
    PhenotypingMethod,
    RelevanceScore,
    ResearcherProfile,
    ScoredPaper,
    SourceType,
    StressType,
)
from utils.persistence import PaperStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    s = PaperStore(tmp_path / "test.db")
    yield s
    s.close()


def _profile(rid: str = "r1") -> ResearcherProfile:
    return ResearcherProfile(
        researcher_id=rid,
        name="Dr. Test",
        plant_species=["poplar"],
        stress_types=[StressType.DROUGHT],
        phenotyping_methods=[PhenotypingMethod.HYPERSPECTRAL],
        expertise_keywords=["ABA", "stomata"],
        time_range_months=12,
    )


def _scored_paper(paper_id: str = "p1", doi: str | None = "10.1234/test") -> ScoredPaper:
    paper = PaperMetadata(
        paper_id=paper_id,
        title="Drought response in poplar",
        authors=["Smith J", "Jones K"],
        abstract="Poplar shows strong ABA signaling under drought.",
        source=SourceType.PUBMED,
        doi=doi,
        published_date=datetime(2025, 3, 1),
        journal="Plant Physiology",
        is_open_access=True,
        citation_count=12,
    )
    relevance = RelevanceScore(
        overall=0.8,
        species_match=1.0,
        stress_match=1.0,
        method_match=0.5,
        recency=0.8,
        credibility=0.7,
        novelty=0.6,
    )
    return ScoredPaper(
        paper=paper,
        relevance=relevance,
        credibility=CredibilityLevel.MODERATE,
        suggested_combinations=["Combine drought + imaging"],
        source_queries=["poplar drought"],
    )


# ---------------------------------------------------------------------------
# Profile round-trip
# ---------------------------------------------------------------------------

def test_save_and_load_profile_round_trip(store):
    p = _profile()
    store.save_profile(p)
    loaded = store.load_profiles()
    assert len(loaded) == 1
    lp = loaded[0]
    assert lp.researcher_id == "r1"
    assert lp.plant_species == ["poplar"]
    assert lp.stress_types == [StressType.DROUGHT]
    assert lp.phenotyping_methods == [PhenotypingMethod.HYPERSPECTRAL]
    assert lp.expertise_keywords == ["ABA", "stomata"]


def test_load_profile_returns_none_for_unknown(store):
    assert store.load_profile("does_not_exist") is None


def test_save_profile_overwrites_on_duplicate(store):
    p = _profile()
    store.save_profile(p)
    p2 = ResearcherProfile(researcher_id="r1", name="Dr. Updated", plant_species=["maize"])
    store.save_profile(p2)
    loaded = store.load_profiles()
    assert len(loaded) == 1
    assert loaded[0].name == "Dr. Updated"
    assert loaded[0].plant_species == ["maize"]


# ---------------------------------------------------------------------------
# Paper round-trip
# ---------------------------------------------------------------------------

def test_save_and_load_paper_round_trip(store):
    sp = _scored_paper()
    store.save_paper(sp, "r1")
    papers = store.load_papers("r1")
    assert len(papers) == 1
    p = papers[0]
    assert p.paper.paper_id == "p1"
    assert p.paper.title == "Drought response in poplar"
    assert p.paper.doi == "10.1234/test"
    assert p.relevance.species_match == pytest.approx(1.0)
    assert p.credibility == CredibilityLevel.MODERATE
    assert "Combine drought + imaging" in p.suggested_combinations


def test_save_paper_preserves_rag_indexed_flag(store):
    sp = _scored_paper()
    store.save_paper(sp, "r1")
    store.mark_indexed(["p1"])
    # saving again should not reset rag_indexed to 0
    store.save_paper(sp, "r1")
    unindexed = store.get_unindexed_papers()
    ids = [row[0] for row in unindexed]
    assert "p1" not in ids


def test_load_papers_only_returns_own_researcher(store):
    store.save_paper(_scored_paper("p1"), "r1")
    store.save_paper(_scored_paper("p2", doi=None), "r2")
    assert len(store.load_papers("r1")) == 1
    assert len(store.load_papers("r2")) == 1


def test_same_paper_collected_by_two_researchers_is_not_stolen(store):
    """Both researchers keep the paper, and neither inherits the other's scores.

    paper_id is assigned by the source (PubMed id, arXiv id, DOI), so it is
    identical for everyone who finds the same paper.
    """
    r1 = _scored_paper("shared")
    r1.relevance.overall = 0.9
    r1.suggested_combinations = ["r1 idea"]
    store.save_paper(r1, "r1")

    r2 = _scored_paper("shared")
    r2.relevance.overall = 0.2
    r2.suggested_combinations = ["r2 idea"]
    store.save_paper(r2, "r2")

    r1_papers = store.load_papers("r1")
    r2_papers = store.load_papers("r2")
    assert len(r1_papers) == 1, "r1 lost the paper when r2 collected it"
    assert len(r2_papers) == 1
    assert r1_papers[0].relevance.overall == pytest.approx(0.9)
    assert r2_papers[0].relevance.overall == pytest.approx(0.2)
    assert r1_papers[0].suggested_combinations == ["r1 idea"]
    assert r2_papers[0].suggested_combinations == ["r2 idea"]

    # The bibliographic record itself is stored once, not per researcher.
    n_rows = store._conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    assert n_rows == 1


def test_shared_corpus_is_indexed_once_for_all_researchers(store):
    store.save_paper(_scored_paper("shared"), "r1")
    store.save_paper(_scored_paper("shared"), "r2")
    assert len(store.get_unindexed_papers()) == 1
    store.mark_indexed(["shared"])
    assert store.get_unindexed_papers() == []


# ---------------------------------------------------------------------------
# known_dois / known_paper_ids
# ---------------------------------------------------------------------------

def test_known_dois_returns_saved_dois(store):
    store.save_paper(_scored_paper("p1", doi="10.1111/aaa"), "r1")
    store.save_paper(_scored_paper("p2", doi="10.2222/bbb"), "r1")
    dois = store.known_dois("r1")
    assert "10.1111/aaa" in dois
    assert "10.2222/bbb" in dois


def test_known_dois_excludes_null(store):
    store.save_paper(_scored_paper("p1", doi=None), "r1")
    dois = store.known_dois("r1")
    assert None not in dois
    assert "" not in dois


def test_known_paper_ids_returns_all_ids(store):
    store.save_paper(_scored_paper("p1"), "r1")
    store.save_paper(_scored_paper("p2", doi=None), "r1")
    ids = store.known_paper_ids("r1")
    assert "p1" in ids
    assert "p2" in ids


# ---------------------------------------------------------------------------
# get_unindexed_papers / mark_rag_indexed
# ---------------------------------------------------------------------------

def test_get_unindexed_papers_returns_new_papers(store):
    store.save_paper(_scored_paper("p1"), "r1")
    store.save_paper(_scored_paper("p2", doi=None), "r1")
    unindexed = store.get_unindexed_papers()
    ids = {row[0] for row in unindexed}   # row = (paper_id, abstract, meta)
    assert "p1" in ids
    assert "p2" in ids


def test_mark_indexed_removes_from_unindexed(store):
    store.save_paper(_scored_paper("p1"), "r1")
    store.mark_indexed(["p1"])
    unindexed = store.get_unindexed_papers()
    assert all(row[0] != "p1" for row in unindexed)
