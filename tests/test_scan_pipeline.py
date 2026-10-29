# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for the _collect_new_pairs deduplication logic in LiteratureMiningAgent."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from models.schemas import PaperMetadata, SearchQuery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent():
    """Return a LiteratureMiningAgent with stubbed dependencies."""
    from agents.literature_mining_agent import LiteratureMiningAgent

    agent = LiteratureMiningAgent.__new__(LiteratureMiningAgent)
    return agent


def _query(source: str = "flagship") -> SearchQuery:
    return SearchQuery(
        query_string="graphite capacity",
        source_target=source,
        researcher_id="r1",
    )


def _paper(paper_id: str, doi: str | None = None) -> PaperMetadata:
    return PaperMetadata(
        paper_id=paper_id,
        title=f"Paper {paper_id}",
        authors=[],
        abstract="Some abstract.",
        source="flagship",
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


# ---------------------------------------------------------------------------
# _scan_researcher: scheduled scans are recorded as sessions
# ---------------------------------------------------------------------------

async def test_scheduled_scan_records_a_session(tmp_path):
    from unittest.mock import AsyncMock

    from models.schemas import ResearcherProfile
    from utils.persistence import PaperStore

    agent = _make_agent()
    agent.store = PaperStore(tmp_path / "test.db")
    agent._run_scan = AsyncMock(return_value={"queries_executed": 7, "papers_found": 4, "total_papers": 4})
    profile = ResearcherProfile(researcher_id="r1", name="Ada", facets={"species": ["noccaea"]})

    await agent._scan_researcher("r1", profile)

    agent._run_scan.assert_awaited_once_with("r1", profile, update_last_scan=True)
    [session] = agent.store.get_sessions("r1")
    assert session["profile"]["trigger"] == "scheduled"
    assert session["profile"]["new_papers"] == 4
    assert session["profile"]["facets"] == {"species": ["noccaea"]}
    assert session["n_proposals"] == 0


async def test_scheduled_scan_survives_a_session_write_failure(tmp_path):
    from unittest.mock import AsyncMock

    from models.schemas import ResearcherProfile

    agent = _make_agent()
    agent.store = MagicMock()
    agent.store.save_session.side_effect = RuntimeError("db locked")
    agent._run_scan = AsyncMock(return_value={"queries_executed": 1, "papers_found": 0, "total_papers": 0})

    await agent._scan_researcher("r1", ResearcherProfile(researcher_id="r1", name="Ada"))
