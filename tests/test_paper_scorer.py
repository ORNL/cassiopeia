# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for utils/paper_scorer.py — facet scoring, weighted_score, ranking."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from models.schemas import (
    CredibilityLevel,
    PaperMetadata,
    RelevanceScore,
    ResearcherProfile,
    ScoredPaper,
)
from utils.paper_scorer import PaperScorer

_PRIORITIES = {"material": "relevance", "property": "relevance", "technique": "methodology"}


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
    source: str = "flagship",
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


def _profile(**facets: list[str]) -> ResearcherProfile:
    return ResearcherProfile(researcher_id="r1", name="Test", facets=facets)


# ---------------------------------------------------------------------------
# facet scores
# ---------------------------------------------------------------------------

def test_every_pack_facet_is_scored(scorer):
    scores = scorer.facet_scores(_paper(title="x"), _profile())
    assert set(scores) == {"material", "property", "technique"}


def test_facet_score_is_half_when_nothing_selected(scorer):
    scores = scorer.facet_scores(_paper(title="Graphite anodes"), _profile())
    assert scores["material"] == pytest.approx(0.5)


def test_facet_score_is_one_when_all_terms_found(scorer):
    paper = _paper(title="Graphite anodes", abstract="graphite particles were cycled")
    assert scorer.facet_scores(paper, _profile(material=["graphite"]))["material"] == pytest.approx(1.0)


def test_facet_score_is_zero_when_terms_missing(scorer):
    paper = _paper(title="Silicon anodes", abstract="silicon swelling")
    assert scorer.facet_scores(paper, _profile(material=["graphite"]))["material"] == pytest.approx(0.0)


def test_facet_score_partial_match(scorer):
    paper = _paper(title="Graphite and silicon", abstract="")
    score = scorer.facet_scores(paper, _profile(material=["graphite", "lithium_iron_phosphate"]))["material"]
    assert score == pytest.approx(0.5)


def test_facet_values_match_as_display_terms(scorer):
    paper = _paper(title="Capacity fade in LFP cells")
    score = scorer.facet_scores(paper, _profile(property=["capacity_fade"]))["property"]
    assert score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _score_recency
# ---------------------------------------------------------------------------

def test_score_recency_returns_low_when_no_date(scorer):
    assert scorer._score_recency(_paper(published_date=None)) == pytest.approx(0.3)


def test_score_recency_brackets(scorer):
    cases = [(30, 1.0), (60, 1.0), (150, 0.8), (300, 0.6), (500, 0.3), (800, 0.1), (900, 0.1)]
    for days, expected in cases:
        paper = _paper(published_date=datetime.now() - timedelta(days=days))
        assert scorer._score_recency(paper) == pytest.approx(expected), f"days={days}"


# ---------------------------------------------------------------------------
# credibility
# ---------------------------------------------------------------------------

def test_score_credibility_uses_pack_journal_tiers(scorer):
    high = scorer._score_credibility(_paper(journal="Nature Materials"))
    mid = scorer._score_credibility(_paper(journal="Journal of Power Sources"))
    unknown = scorer._score_credibility(_paper(journal="Some Journal"))
    assert high >= 0.7
    assert high > mid > unknown


def test_score_credibility_boosted_by_open_access(scorer):
    assert scorer._score_credibility(_paper(is_open_access=True)) > scorer._score_credibility(_paper())


def test_score_credibility_boosted_by_citation_count(scorer):
    assert scorer._score_credibility(_paper(citation_count=50)) > scorer._score_credibility(_paper())


def test_preprint_sources_are_preliminary(scorer):
    paper = _paper(source="preprints", journal="Nature Materials", citation_count=100)
    assert scorer._assess_credibility(paper) == CredibilityLevel.PRELIMINARY


def test_high_impact_source_with_citations_is_high(scorer):
    paper = _paper(source="flagship", journal="", citation_count=10)
    assert scorer._assess_credibility(paper) == CredibilityLevel.HIGH


def test_unknown_source_falls_back_to_citations(scorer):
    assert scorer._assess_credibility(_paper(source="other", citation_count=10)) == CredibilityLevel.MODERATE
    assert scorer._assess_credibility(_paper(source="other")) == CredibilityLevel.PRELIMINARY


# ---------------------------------------------------------------------------
# hints
# ---------------------------------------------------------------------------

def test_hints_follow_pack_template(scorer):
    paper = _paper(title="Ionic conductivity and capacity fade")
    hints = scorer._suggest_combinations(paper, _profile(property=["capacity_fade"]))
    assert hints == ["Paper studies conductivity — relate it to your capacity fade work"]


# ---------------------------------------------------------------------------
# weighted_score
# ---------------------------------------------------------------------------

def test_weighted_score_between_zero_and_one():
    r = RelevanceScore(
        facet_scores={"material": 0.8, "property": 0.6, "technique": 0.5},
        recency=0.9, credibility=0.7, novelty=0.4,
    )
    assert 0.0 <= r.weighted_score(_profile(), _PRIORITIES) <= 1.0


def test_weighted_score_averages_facets_sharing_a_priority():
    profile = ResearcherProfile(
        researcher_id="r", name="R",
        priority_relevance=1.0, priority_methodology=0.0,
        priority_novelty=0.0, priority_reproducibility=0.0,
    )
    r = RelevanceScore(facet_scores={"material": 1.0, "property": 0.5, "technique": 0.0})
    assert r.weighted_score(profile, _PRIORITIES) == pytest.approx(0.75)


def test_weighted_score_skips_priorities_without_facets():
    profile = ResearcherProfile(
        researcher_id="r", name="R",
        priority_relevance=1.0, priority_methodology=1.0,
        priority_novelty=0.0, priority_reproducibility=0.0,
    )
    r = RelevanceScore(facet_scores={"material": 1.0})
    assert r.weighted_score(profile, {"material": "relevance"}) == pytest.approx(1.0)


def test_weighted_score_higher_when_matching_dimensions_high():
    low = RelevanceScore(facet_scores=dict.fromkeys(_PRIORITIES, 0.1), novelty=0.1, credibility=0.1)
    high = RelevanceScore(facet_scores=dict.fromkeys(_PRIORITIES, 0.9), novelty=0.9, credibility=0.9)
    assert high.weighted_score(_profile(), _PRIORITIES) > low.weighted_score(_profile(), _PRIORITIES)


def test_score_paper_fills_overall(scorer):
    sp = scorer.score_paper(_paper(title="Graphite capacity fade"), _profile(material=["graphite"]))
    assert 0.0 < sp.relevance.overall <= 1.0


# ---------------------------------------------------------------------------
# rank_papers
# ---------------------------------------------------------------------------

def test_rank_papers_descending_by_overall(scorer):
    papers = [
        ScoredPaper(paper=_paper(paper_id=pid), relevance=RelevanceScore(overall=o))
        for pid, o in (("p1", 0.3), ("p2", 0.9), ("p3", 0.6))
    ]
    ranked = scorer.rank_papers(papers)
    assert [sp.paper.paper_id for sp in ranked] == ["p2", "p3", "p1"]


def test_rank_papers_empty_list(scorer):
    assert scorer.rank_papers([]) == []
