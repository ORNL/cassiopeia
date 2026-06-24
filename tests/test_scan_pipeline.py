# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for the _collect_new_pairs deduplication logic in LiteratureMiningAgent."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from models.schemas import PaperMetadata, SearchQuery, SourceType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent():
    """Return a LiteratureMiningAgent with stubbed dependencies."""
    from agents.literature_mining_agent import LiteratureMiningAgent

    agent = LiteratureMiningAgent.__new__(LiteratureMiningAgent)
    return agent


def _query(source: SourceType = SourceType.PUBMED) -> SearchQuery:
    return SearchQuery(
        query_string="poplar drought",
        source_target=source,
        researcher_id="r1",
    )


def _paper(paper_id: str, doi: str | None = None) -> PaperMetadata:
    return PaperMetadata(
        paper_id=paper_id,
        title=f"Paper {paper_id}",
        authors=[],
        abstract="Some abstract.",
        source=SourceType.PUBMED,
        doi=doi,
    )


# ---------------------------------------------------------------------------
# _collect_new_pairs tests
# ---------------------------------------------------------------------------

def test_collect_new_pairs_passes_unknown_papers():
    agent = _make_agent()
    q = _query()
    p = _paper("p1", doi="10.1/a")
    results = [(q, [p])]
    pairs = agent._collect_new_pairs("r1", results, known_dois=set(), known_ids=set())
    assert len(pairs) == 1
    assert pairs[0][1].paper_id == "p1"


def test_collect_new_pairs_excludes_known_doi():
    agent = _make_agent()
    q = _query()
    p = _paper("p1", doi="10.1/known")
    results = [(q, [p])]
    pairs = agent._collect_new_pairs("r1", results, known_dois={"10.1/known"}, known_ids=set())
    assert pairs == []


def test_collect_new_pairs_excludes_known_paper_id():
    """DOI-less paper already in the store is excluded via paper_id fallback."""
    agent = _make_agent()
    q = _query()
    p = _paper("p_arxiv_001", doi=None)
    results = [(q, [p])]
    pairs = agent._collect_new_pairs("r1", results, known_dois=set(), known_ids={"p_arxiv_001"})
    assert pairs == []


def test_collect_new_pairs_dedupes_within_batch():
    """Same paper appearing in two query results is only included once."""
    agent = _make_agent()
    q1, q2 = _query(), _query()
    p = _paper("p1", doi="10.1/dup")
    results = [(q1, [p]), (q2, [p])]
    pairs = agent._collect_new_pairs("r1", results, known_dois=set(), known_ids=set())
    assert len(pairs) == 1


def test_collect_new_pairs_dedupes_doi_less_within_batch():
    """DOI-less duplicate in same batch is also dropped."""
    agent = _make_agent()
    q1, q2 = _query(), _query()
    p = _paper("arx_001", doi=None)
    results = [(q1, [p]), (q2, [p])]
    pairs = agent._collect_new_pairs("r1", results, known_dois=set(), known_ids=set())
    assert len(pairs) == 1


def test_collect_new_pairs_skips_fetch_exceptions():
    """BaseException entries from asyncio.gather are logged and skipped."""
    agent = _make_agent()
    q = _query()
    p = _paper("p2", doi="10.1/b")
    results = [ValueError("network error"), (q, [p])]
    pairs = agent._collect_new_pairs("r1", results, known_dois=set(), known_ids=set())
    assert len(pairs) == 1
    assert pairs[0][1].paper_id == "p2"


def test_collect_new_pairs_mixed_known_and_new():
    """Only genuinely new papers pass; already-stored papers are filtered out."""
    agent = _make_agent()
    q = _query()
    known = _paper("old", doi="10.1/old")
    new = _paper("new", doi="10.1/new")
    results = [(q, [known, new])]
    pairs = agent._collect_new_pairs("r1", results, known_dois={"10.1/old"}, known_ids=set())
    assert len(pairs) == 1
    assert pairs[0][1].paper_id == "new"
