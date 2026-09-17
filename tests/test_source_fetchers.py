# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for the fetcher backends and the pack-driven source registry.

Query-builder and registry tests need no network.  The backend tests at the
bottom make real requests; skip them offline with:
    pytest -m "not integration"
Each domain pack tests its own source list under ``domains/<pack>/tests``.
"""

from __future__ import annotations

import pytest

from domains import SourceInfo
from models.schemas import SearchQuery
from utils.source_fetchers import (
    ArxivFetcher,
    BaseFetcher,
    EuropePMCFetcher,
    _arxiv_query_terms,
    _epmc_query_terms,
    get_fetcher,
    register_backend,
)

integration = pytest.mark.integration


# ── Unit tests for OR-group query builders (no network) ───────────────────────

def _make_query(source: str, groups: list[list[str]]) -> SearchQuery:
    return SearchQuery(
        query_string="",
        source_target=source,
        researcher_id="test",
        term_groups=groups,
    )


def test_epmc_or_groups():
    q = _make_query("preprints", [
        ["graphite", "carbon anode"],
        ["capacity fade", "capacity loss", "degradation"],
    ])
    assert _epmc_query_terms(q) == (
        '(graphite OR "carbon anode") AND ("capacity fade" OR "capacity loss" OR degradation)'
    )


def test_epmc_single_term_groups():
    q = _make_query("flagship", [["graphite"], ["conductivity"]])
    assert _epmc_query_terms(q) == "graphite AND conductivity"


def test_epmc_fallback_to_base_terms():
    q = SearchQuery(
        query_string="",
        source_target="flagship",
        researcher_id="test",
        base_terms=["graphite", "conductivity"],
    )
    assert _epmc_query_terms(q) == "graphite AND conductivity"


def test_arxiv_or_groups():
    q = _make_query("arxiv", [
        ["graphite", "carbon anode"],
        ["simulation", "first-principles", "density functional"],
    ])
    assert _arxiv_query_terms(q) == (
        '(all:graphite OR all:"carbon anode") AND '
        '(all:simulation OR all:"first-principles" OR all:"density functional")'
    )


def test_arxiv_fallback_caps_at_two_terms():
    q = SearchQuery(
        query_string="",
        source_target="arxiv",
        researcher_id="test",
        base_terms=["graphite", "anode", "cycling"],
    )
    assert _arxiv_query_terms(q) == "all:graphite AND all:anode"


# ── Registry (no network) ─────────────────────────────────────────────────────

def test_get_fetcher_builds_the_pack_backend():
    fetcher = get_fetcher("flagship")
    assert isinstance(fetcher, EuropePMCFetcher)
    assert fetcher.source_key == "flagship"
    assert fetcher.source_filter == 'JOURNAL:"Nature Materials"'
    assert isinstance(get_fetcher("arxiv"), ArxivFetcher)


def test_get_fetcher_rejects_unknown_sources():
    with pytest.raises(ValueError, match="Unknown source"):
        get_fetcher("nope")


def test_packs_can_register_backends(_test_domain_pack, monkeypatch):
    from dataclasses import replace

    from domains import set_domain
    from utils import source_fetchers

    class _Custom(BaseFetcher):
        async def fetch(self, query, max_results=20):
            return []

        async def fetch_full_text(self, paper_id):
            return None

    monkeypatch.setattr(source_fetchers, "BACKENDS", dict(source_fetchers.BACKENDS))
    register_backend("custom", _Custom)
    info = SourceInfo(key="inhouse", label="In-house", backend="custom")
    set_domain(replace(_test_domain_pack, sources={"inhouse": info}))
    fetcher = get_fetcher("inhouse")
    assert isinstance(fetcher, _Custom)
    assert fetcher.source is info


# ── Integration tests (real network) ─────────────────────────────────────────

def _query(source: str, terms: list[str]) -> SearchQuery:
    return SearchQuery(
        query_string=" AND ".join(terms),
        source_target=source,
        researcher_id="test",
        base_terms=terms,
        contextual_modifiers={},
    )


@integration
@pytest.mark.asyncio
async def test_arxiv_backend_returns_papers():
    papers = await get_fetcher("arxiv").fetch(_query("arxiv", ["graphite", "anode"]), max_results=5)
    assert len(papers) >= 1, "arXiv returned 0 papers — check connectivity"
    assert papers[0].title
    assert papers[0].source == "arxiv"


@integration
@pytest.mark.asyncio
async def test_europepmc_backend_returns_papers():
    papers = await get_fetcher("preprints").fetch(
        _query("preprints", ["lithium", "battery"]), max_results=5
    )
    assert len(papers) >= 1, "Europe PMC returned 0 papers — check connectivity"
    assert papers[0].title
    assert papers[0].source == "preprints"


@integration
@pytest.mark.asyncio
async def test_europepmc_lookup_resolves_a_doi():
    # "Deep learning" (LeCun, Bengio, Hinton — Nature 2015)
    abstract = await get_fetcher("flagship").lookup_abstract("10.1038/nature14539")
    assert abstract
