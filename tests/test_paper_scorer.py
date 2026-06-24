# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for utils/paper_scorer.py — scoring dimensions, weighted_score, ranking."""

from __future__ import annotations

from datetime import datetime, timedelta

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
from utils.paper_scorer import PaperScorer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def scorer():
    return PaperScorer()


def _paper(
    *,
    title: str = "",
    abstract: str = "",
    journal: str | None = None,
    published_date: datetime | None = None,
    citation_count: int = 0,
    is_open_access: bool = False,
    source: SourceType = SourceType.PUBMED,
    paper_id: str = "p1",
    doi: str | None = "10.1/x",
) -> PaperMetadata:
    return PaperMetadata(
        paper_id=paper_id,
        title=title,
        authors=[],
        abstract=abstract,
        source=source,
        doi=doi,
        published_date=published_date,
        journal=journal,
        citation_count=citation_count,
        is_open_access=is_open_access,
    )


def _profile(
    *,
    species: list[str] | None = None,
    stresses: list[StressType] | None = None,
    methods: list[PhenotypingMethod] | None = None,
) -> ResearcherProfile:
    return ResearcherProfile(
        researcher_id="r1",
        name="Test",
        plant_species=species or [],
        stress_types=stresses or [],
        phenotyping_methods=methods or [],
    )


# ---------------------------------------------------------------------------
# _score_species
# ---------------------------------------------------------------------------

def test_score_species_returns_half_when_no_species_in_profile(scorer):
    paper = _paper(title="Drought response in plants", abstract="")
    profile = _profile(species=[])
    assert scorer._score_species(paper, profile) == pytest.approx(0.5)


def test_score_species_returns_one_when_species_found(scorer):
    paper = _paper(title="Poplar root growth", abstract="poplar seedlings were used")
    profile = _profile(species=["poplar"])
    assert scorer._score_species(paper, profile) == pytest.approx(1.0)


def test_score_species_returns_zero_when_species_missing(scorer):
    paper = _paper(title="Maize drought tolerance", abstract="maize was studied")
    profile = _profile(species=["poplar"])
    assert scorer._score_species(paper, profile) == pytest.approx(0.0)


def test_score_species_partial_match(scorer):
    paper = _paper(title="Poplar and maize comparison", abstract="poplar is drought tolerant")
    profile = _profile(species=["poplar", "arabidopsis"])
    score = scorer._score_species(paper, profile)
    assert 0.0 < score < 1.0


# ---------------------------------------------------------------------------
# _score_stress
# ---------------------------------------------------------------------------

def test_score_stress_returns_half_when_no_stresses_in_profile(scorer):
    paper = _paper(title="Drought signaling", abstract="")
    profile = _profile(stresses=[])
    assert scorer._score_stress(paper, profile) == pytest.approx(0.5)


def test_score_stress_returns_one_when_stress_found(scorer):
    paper = _paper(title="Drought response", abstract="water deficit leads to ABA")
    profile = _profile(stresses=[StressType.DROUGHT])
    assert scorer._score_stress(paper, profile) == pytest.approx(1.0)


def test_score_stress_returns_zero_when_stress_missing(scorer):
    paper = _paper(title="Nutrient uptake", abstract="nitrogen and phosphorus")
    profile = _profile(stresses=[StressType.DROUGHT])
    assert scorer._score_stress(paper, profile) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _score_recency
# ---------------------------------------------------------------------------

def test_score_recency_returns_low_when_no_date(scorer):
    paper = _paper(published_date=None)
    assert scorer._score_recency(paper) == pytest.approx(0.3)


def test_score_recency_returns_one_for_recent_paper(scorer):
    paper = _paper(published_date=datetime.now() - timedelta(days=30))
    assert scorer._score_recency(paper) == pytest.approx(1.0)


def test_score_recency_returns_low_for_old_paper(scorer):
    paper = _paper(published_date=datetime.now() - timedelta(days=800))
    assert scorer._score_recency(paper) == pytest.approx(0.1)


def test_score_recency_brackets(scorer):
    cases = [
        (60,  1.0),
        (150, 0.8),
        (300, 0.6),
        (500, 0.3),
        (900, 0.1),
    ]
    for days, expected in cases:
        paper = _paper(published_date=datetime.now() - timedelta(days=days))
        assert scorer._score_recency(paper) == pytest.approx(expected), f"days={days}"


# ---------------------------------------------------------------------------
# _score_credibility
# ---------------------------------------------------------------------------

def test_score_credibility_high_for_top_journal(scorer):
    paper = _paper(journal="nature")
    score = scorer._score_credibility(paper)
    assert score >= 0.7


def test_score_credibility_mid_for_mid_journal(scorer):
    paper = _paper(journal="plos one")
    high_paper = _paper(journal="nature")
    assert scorer._score_credibility(paper) < scorer._score_credibility(high_paper)


def test_score_credibility_boosted_by_open_access(scorer):
    closed = _paper(is_open_access=False)
    open_access = _paper(is_open_access=True)
    assert scorer._score_credibility(open_access) > scorer._score_credibility(closed)


def test_score_credibility_boosted_by_citation_count(scorer):
    low_cited = _paper(citation_count=0)
    high_cited = _paper(citation_count=50)
    assert scorer._score_credibility(high_cited) > scorer._score_credibility(low_cited)


# ---------------------------------------------------------------------------
# weighted_score
# ---------------------------------------------------------------------------

def test_weighted_score_between_zero_and_one():
    r = RelevanceScore(
        species_match=0.8,
        stress_match=0.6,
        method_match=0.5,
        recency=0.9,
        credibility=0.7,
        novelty=0.4,
    )
    profile = _profile()
    profile.priority_novelty = 0.5
    profile.priority_relevance = 0.5
    profile.priority_methodology = 0.5
    profile.priority_reproducibility = 0.5
    score = r.weighted_score(profile)
    assert 0.0 <= score <= 1.0


def test_weighted_score_higher_when_matching_dimensions_high():
    low = RelevanceScore(species_match=0.1, stress_match=0.1, novelty=0.1,
                         method_match=0.1, credibility=0.1)
    high = RelevanceScore(species_match=0.9, stress_match=0.9, novelty=0.9,
                          method_match=0.9, credibility=0.9)
    profile = _profile()
    assert high.weighted_score(profile) > low.weighted_score(profile)


# ---------------------------------------------------------------------------
# rank_papers
# ---------------------------------------------------------------------------

def test_rank_papers_descending_by_overall(scorer):
    def _sp(pid, overall):
        sp = ScoredPaper(
            paper=_paper(paper_id=pid),
            relevance=RelevanceScore(overall=overall),
        )
        return sp

    papers = [_sp("p1", 0.3), _sp("p2", 0.9), _sp("p3", 0.6)]
    ranked = scorer.rank_papers(papers)
    scores = [sp.relevance.overall for sp in ranked]
    assert scores == sorted(scores, reverse=True)
    assert ranked[0].paper.paper_id == "p2"


def test_rank_papers_empty_list(scorer):
    assert scorer.rank_papers([]) == []
