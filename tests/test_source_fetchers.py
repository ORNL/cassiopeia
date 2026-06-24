# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Integration tests — each source fetcher must reach its upstream API and return at least one paper.

These tests make real network requests.  Run them with:
    pytest tests/test_source_fetchers.py -v

Skip when offline:
    pytest -m "not integration"
"""

from __future__ import annotations

import pytest

from models.schemas import SearchQuery, SourceType
from utils.source_fetchers import (
    _arxiv_query_terms,
    _epmc_mesh_clause,
    _epmc_query_terms,
    ArxivFetcher,
    BioRxivFetcher,
    FrontiersFetcher,
    NatureCommsFetcher,
    NewPhytologistFetcher,
    PlantPhysiologyFetcher,
    PlosOneFetcher,
    PubMedFetcher,
)

_integration = pytest.mark.integration


# ── Unit tests for OR-group query builders (no network) ───────────────────────

def _make_query(source: SourceType, groups: list[list[str]]) -> SearchQuery:
    return SearchQuery(
        query_string="",
        source_target=source,
        researcher_id="test",
        term_groups=groups,
    )


def test_epmc_or_groups():
    q = _make_query(SourceType.BIORXIV, [
        ["pennycress", "Thlaspi arvense"],
        ["heavy metal", "cadmium", "phytoremediation"],
    ])
    result = _epmc_query_terms(q)
    assert result == '(pennycress OR "Thlaspi arvense") AND ("heavy metal" OR cadmium OR phytoremediation)'


def test_epmc_single_term_groups():
    q = _make_query(SourceType.PUBMED, [["arabidopsis"], ["drought"]])
    assert _epmc_query_terms(q) == "arabidopsis AND drought"


def test_epmc_fallback_to_base_terms():
    q = SearchQuery(
        query_string="",
        source_target=SourceType.PUBMED,
        researcher_id="test",
        base_terms=["arabidopsis", "drought"],
    )
    assert _epmc_query_terms(q) == "arabidopsis AND drought"


def test_arxiv_or_groups():
    q = _make_query(SourceType.ARXIV, [
        ["arabidopsis", "Arabidopsis thaliana"],
        ["transcriptome", "RNA-seq", "gene expression"],
    ])
    result = _arxiv_query_terms(q)
    assert result == '(all:arabidopsis OR all:"Arabidopsis thaliana") AND (all:transcriptome OR all:"RNA-seq" OR all:"gene expression")'


def test_arxiv_fallback_caps_at_two_terms():
    q = SearchQuery(
        query_string="",
        source_target=SourceType.ARXIV,
        researcher_id="test",
        base_terms=["arabidopsis", "drought", "stomata"],
    )
    assert _arxiv_query_terms(q) == "all:arabidopsis AND all:drought"


# ── MeSH clause builder (no network) ─────────────────────────────────────────

def test_epmc_mesh_clause_empty():
    assert _epmc_mesh_clause([]) == ""


def test_epmc_mesh_clause_single():
    assert _epmc_mesh_clause(["Droughts"]) == '"Droughts"[MeSH Terms]'


def test_epmc_mesh_clause_multiple():
    result = _epmc_mesh_clause(["Droughts", "Arabidopsis thaliana"])
    assert result == '("Droughts"[MeSH Terms] OR "Arabidopsis thaliana"[MeSH Terms])'


def test_epmc_query_terms_with_mesh():
    q = SearchQuery(
        query_string="",
        source_target=SourceType.PUBMED,
        researcher_id="test",
        term_groups=[["arabidopsis"], ["drought"]],
        mesh_terms=["Droughts", "Arabidopsis thaliana"],
    )
    result = _epmc_query_terms(q)
    assert result.startswith("(arabidopsis AND drought) AND")
    assert '"Droughts"[MeSH Terms]' in result
    assert '"Arabidopsis thaliana"[MeSH Terms]' in result


def test_epmc_query_terms_mesh_only():
    q = SearchQuery(
        query_string="",
        source_target=SourceType.PUBMED,
        researcher_id="test",
        mesh_terms=["Droughts"],
    )
    result = _epmc_query_terms(q)
    assert result == '"Droughts"[MeSH Terms]'


def test_epmc_query_terms_no_mesh_unchanged():
    q = SearchQuery(
        query_string="",
        source_target=SourceType.PUBMED,
        researcher_id="test",
        term_groups=[["arabidopsis"], ["drought"]],
    )
    assert _epmc_query_terms(q) == "arabidopsis AND drought"


# ── Integration tests (real network) ─────────────────────────────────────────

def _query(fetcher_cls, terms: list[str]) -> SearchQuery:
    return SearchQuery(
        query_string=" AND ".join(terms),
        source_target=fetcher_cls.source_type,
        researcher_id="test",
        base_terms=terms,
        contextual_modifiers={},
    )


# arXiv is a CS/physics server; use bioinformatics terms that reliably appear there.
# Plant-specific terms (pennycress, nickel stress) return 0 on arXiv — that is
# expected behaviour, not a fetcher bug.
@_integration
@pytest.mark.asyncio
async def test_arxiv_returns_papers():
    fetcher = ArxivFetcher()
    papers = await fetcher.fetch(_query(ArxivFetcher, ["arabidopsis", "genome"]), max_results=5)
    assert len(papers) >= 1, "ArxivFetcher returned 0 papers — check connectivity"
    assert papers[0].title
    assert papers[0].source == SourceType.ARXIV


@_integration
@pytest.mark.asyncio
async def test_biorxiv_returns_papers():
    fetcher = BioRxivFetcher()
    papers = await fetcher.fetch(_query(BioRxivFetcher, ["arabidopsis", "drought"]), max_results=5)
    assert len(papers) >= 1, "BioRxivFetcher returned 0 papers — check connectivity"
    assert papers[0].title
    assert papers[0].source == SourceType.BIORXIV


@_integration
@pytest.mark.asyncio
async def test_pubmed_returns_papers():
    fetcher = PubMedFetcher()
    papers = await fetcher.fetch(_query(PubMedFetcher, ["arabidopsis", "stress"]), max_results=5)
    assert len(papers) >= 1, "PubMedFetcher returned 0 papers — check connectivity"
    assert papers[0].title
    assert papers[0].source == SourceType.PUBMED


@_integration
@pytest.mark.asyncio
async def test_frontiers_returns_papers():
    fetcher = FrontiersFetcher()
    papers = await fetcher.fetch(_query(FrontiersFetcher, ["arabidopsis", "stress"]), max_results=5)
    assert len(papers) >= 1, "FrontiersFetcher returned 0 papers — check connectivity"
    assert papers[0].title
    assert papers[0].source == SourceType.FRONTIERS


@_integration
@pytest.mark.asyncio
async def test_plos_one_returns_papers():
    fetcher = PlosOneFetcher()
    papers = await fetcher.fetch(_query(PlosOneFetcher, ["arabidopsis"]), max_results=5)
    assert len(papers) >= 1, "PlosOneFetcher returned 0 papers — check connectivity"
    assert papers[0].title
    assert papers[0].source == SourceType.PLOS_ONE


@_integration
@pytest.mark.asyncio
async def test_nature_comms_returns_papers():
    fetcher = NatureCommsFetcher()
    papers = await fetcher.fetch(_query(NatureCommsFetcher, ["arabidopsis", "thaliana"]), max_results=5)
    assert len(papers) >= 1, "NatureCommsFetcher returned 0 papers — check connectivity"
    assert papers[0].title
    assert papers[0].source == SourceType.NATURE_COMMS


@_integration
@pytest.mark.asyncio
async def test_new_phytologist_returns_papers():
    fetcher = NewPhytologistFetcher()
    papers = await fetcher.fetch(_query(NewPhytologistFetcher, ["arabidopsis"]), max_results=5)
    assert len(papers) >= 1, "NewPhytologistFetcher returned 0 papers — check connectivity"
    assert papers[0].title
    assert papers[0].source == SourceType.NEW_PHYTOLOGIST


@_integration
@pytest.mark.asyncio
async def test_plant_physiology_returns_papers():
    fetcher = PlantPhysiologyFetcher()
    papers = await fetcher.fetch(_query(PlantPhysiologyFetcher, ["arabidopsis"]), max_results=5)
    assert len(papers) >= 1, "PlantPhysiologyFetcher returned 0 papers — check connectivity"
    assert papers[0].title
    assert papers[0].source == SourceType.PLANT_PHYSIOLOGY
