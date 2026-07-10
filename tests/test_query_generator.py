# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for QueryGenerator.generate_queries_async.

These tests mirror a real dashboard submission:
  Researcher : Fred
  Species    : pennycress, poplar, arabidopsis
  Stresses   : heavy_metal
  Sources    : arxiv, biorxiv, plos_one, frontiers

The LLM call is mocked so no network is needed.  Assertions check that:
  - query structure is correct (OR-groups, not flat AND)
  - temporal ranges never appear inside search terms
  - arXiv queries carry bioinformatics framing in the stress group
  - stress synonyms propagate into non-arXiv queries
  - species synonyms propagate into group 1
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.schemas import ResearcherProfile, SourceType, StressType
from utils.query_generator import QueryGenerator
from utils.source_fetchers import _arxiv_query_terms, _epmc_query_terms

_MOCK_LLM = {"model": "test/mock-model"}


def _make_mock_config():
    config = MagicMock()
    config.for_scoring.return_value = _MOCK_LLM
    config.for_reasoning.return_value = _MOCK_LLM
    return config

# ── helpers ───────────────────────────────────────────────────────────────────

_MOCK_SYNONYMS = {
    "species": {
        "pennycress": ["Thlaspi arvense", "field pennycress"],
        "poplar": ["Populus", "Populus nigra"],
        "arabidopsis": ["Arabidopsis thaliana", "mouse-ear cress"],
    },
    "stresses": {
        "heavy_metal": [
            "metal stress", "cadmium", "zinc toxicity",
            "phytoremediation", "nickel",
        ],
    },
}

_ARXIV_BIO_TERMS = {"transcriptome", "RNA-seq", "gene expression", "GWAS", "genomics"}


def _mock_llm_response(payload: dict) -> MagicMock:
    msg = MagicMock()
    msg.content = json.dumps(payload)
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _fred_profile(sources: list[str] | None = None) -> ResearcherProfile:
    return ResearcherProfile(
        researcher_id="fred",
        name="Fred",
        plant_species=["pennycress", "poplar", "arabidopsis"],
        stress_types=[StressType.HEAVY_METAL],
        source_targets=sources or ["arxiv", "biorxiv", "plos_one", "frontiers"],
        time_range_months=84,
    )


# ── query structure tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_query_count_matches_species_x_stress_x_sources():
    """3 species × 1 stress × 4 sources = 12 queries."""
    profile = _fred_profile()
    with patch("litellm.acompletion", new=AsyncMock(
        return_value=_mock_llm_response(_MOCK_SYNONYMS)
    )):
        queries = await QueryGenerator().generate_queries_async(profile)
    assert len(queries) == 12


@pytest.mark.asyncio
async def test_all_queries_have_two_term_groups():
    profile = _fred_profile()
    with patch("litellm.acompletion", new=AsyncMock(
        return_value=_mock_llm_response(_MOCK_SYNONYMS)
    )):
        queries = await QueryGenerator().generate_queries_async(profile)
    for q in queries:
        assert len(q.term_groups) == 2, (
            f"Expected 2 term_groups, got {len(q.term_groups)}: {q.term_groups}"
        )


@pytest.mark.asyncio
async def test_no_temporal_range_in_search_terms():
    """Years like '2018..2026' must never appear as search terms."""
    profile = _fred_profile()
    with patch("litellm.acompletion", new=AsyncMock(
        return_value=_mock_llm_response(_MOCK_SYNONYMS)
    )):
        queries = await QueryGenerator().generate_queries_async(profile)
    for q in queries:
        for term in q.base_terms:
            assert ".." not in term, f"Temporal range leaked into base_terms: {term!r}"
        for group in q.term_groups:
            for term in group:
                assert ".." not in term, f"Temporal range leaked into term_groups: {term!r}"


@pytest.mark.asyncio
async def test_species_synonyms_in_group_1():
    """LLM-provided species synonyms must appear in group 1."""
    profile = _fred_profile()
    with (
        patch("utils.query_generator.get_llm_config", return_value=_make_mock_config()),
        patch("litellm.acompletion", new=AsyncMock(return_value=_mock_llm_response(_MOCK_SYNONYMS))),
    ):
        queries = await QueryGenerator().generate_queries_async(profile)
    pennycress_qs = [q for q in queries if q.base_terms[0] == "pennycress"]
    for q in pennycress_qs:
        assert "Thlaspi arvense" in q.term_groups[0], (
            f"Expected 'Thlaspi arvense' in group 1: {q.term_groups[0]}"
        )


@pytest.mark.asyncio
async def test_stress_synonyms_in_group_2_non_arxiv():
    """Stress synonyms must appear in group 2 for non-arXiv sources."""
    profile = _fred_profile()
    with (
        patch("utils.query_generator.get_llm_config", return_value=_make_mock_config()),
        patch("litellm.acompletion", new=AsyncMock(return_value=_mock_llm_response(_MOCK_SYNONYMS))),
    ):
        queries = await QueryGenerator().generate_queries_async(profile)
    biorxiv_qs = [q for q in queries if q.source_target == SourceType.BIORXIV]
    for q in biorxiv_qs:
        g2 = set(q.term_groups[1])
        assert "heavy metal" in g2, f"Primary stress term missing in group 2: {g2}"
        synonyms = {"metal stress", "cadmium", "zinc toxicity", "phytoremediation", "nickel"}
        assert g2 & synonyms, f"No stress synonym found in group 2: {g2}"


@pytest.mark.asyncio
async def test_arxiv_queries_have_bioinformatics_terms():
    """arXiv stress group must contain at least one bioinformatics term."""
    profile = _fred_profile()
    with patch("litellm.acompletion", new=AsyncMock(
        return_value=_mock_llm_response(_MOCK_SYNONYMS)
    )):
        queries = await QueryGenerator().generate_queries_async(profile)
    arxiv_qs = [q for q in queries if q.source_target == SourceType.ARXIV]
    assert arxiv_qs, "Expected arXiv queries to be generated"
    for q in arxiv_qs:
        g2 = set(q.term_groups[1])
        assert g2 & _ARXIV_BIO_TERMS, (
            f"No bioinformatics term in arXiv stress group: {g2}"
        )


# ── fetcher query string integration ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_epmc_query_string_uses_or():
    """OR-group queries sent to Europe PMC must contain OR operators."""
    profile = _fred_profile(sources=["biorxiv"])
    with (
        patch("utils.query_generator.get_llm_config", return_value=_make_mock_config()),
        patch("litellm.acompletion", new=AsyncMock(return_value=_mock_llm_response(_MOCK_SYNONYMS))),
    ):
        queries = await QueryGenerator().generate_queries_async(profile)
    for q in queries:
        s = _epmc_query_terms(q)
        assert " OR " in s, f"Expected OR in Europe PMC query: {s!r}"
        assert " AND " in s, f"Expected AND between groups: {s!r}"


@pytest.mark.asyncio
async def test_arxiv_query_string_uses_or():
    """OR-group queries sent to arXiv must contain OR operators."""
    profile = _fred_profile(sources=["arxiv"])
    with patch("litellm.acompletion", new=AsyncMock(
        return_value=_mock_llm_response(_MOCK_SYNONYMS)
    )):
        queries = await QueryGenerator().generate_queries_async(profile)
    for q in queries:
        s = _arxiv_query_terms(q)
        assert " OR " in s, f"Expected OR in arXiv query: {s!r}"


# ── fallback behaviour ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_falls_back_gracefully_on_llm_failure():
    """When the LLM is unavailable, bare-term queries (no synonyms) must still be returned.

    _build_from_synonyms produces [["species"], ["stress"]] groups even with an
    empty synonym map, so the result is still structurally valid — just without
    synonym OR-expansion.
    """
    profile = _fred_profile()
    with patch("litellm.acompletion", side_effect=Exception("LLM unavailable")):
        queries = await QueryGenerator().generate_queries_async(profile)
    assert len(queries) > 0, "Expected fallback queries even when LLM fails"
    for q in queries:
        assert len(q.term_groups) == 2, "Each query must still have 2 term_groups"
        # Without synonyms each group has exactly 1 term (species / stress)
        assert len(q.term_groups[0]) == 1
        assert len(q.term_groups[1]) >= 1
