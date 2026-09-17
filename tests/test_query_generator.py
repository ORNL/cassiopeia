# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for QueryGenerator against the test domain pack.

The pack declares a ``material`` subject facet and a ``property`` condition
facet, so every query holds one OR-group per facet.  The LLM call is mocked.
Assertions check that:
  - queries cross every selected subject term with every condition term
  - temporal ranges never appear inside search terms
  - synonyms land in the group of the facet they were returned for
  - keywords widen the last group
  - a source with ``synonym_override`` swaps synonyms for its own terms
  - an empty condition facet falls back to the default-term cross-product
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.schemas import ResearcherProfile
from utils.query_generator import QueryGenerator
from utils.source_fetchers import _arxiv_query_terms, _epmc_query_terms

_MOCK_LLM = {"model": "test/mock-model"}

_MOCK_SYNONYMS = {
    "material": {
        "graphite": ["carbon anode", "C6"],
        "lithium_iron_phosphate": ["LiFePO4", "LFP"],
    },
    "property": {
        "capacity_fade": ["capacity loss", "cycle life", "degradation"],
    },
}

_OVERRIDE_TERMS = {"first principles", "simulation"}


def _mock_config():
    config = MagicMock()
    config.for_scoring.return_value = _MOCK_LLM
    config.for_reasoning.return_value = _MOCK_LLM
    return config


def _mock_llm_response(payload: dict) -> MagicMock:
    msg = MagicMock()
    msg.content = json.dumps(payload)
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _profile(sources=None, keywords=None, properties=("capacity_fade",)) -> ResearcherProfile:
    return ResearcherProfile(
        researcher_id="r1",
        name="R",
        facets={
            "material": ["graphite", "lithium_iron_phosphate"],
            "property": list(properties),
        },
        expertise_keywords=list(keywords or []),
        source_targets=sources or ["preprints", "flagship", "arxiv"],
        time_range_months=84,
    )


async def _generate(profile, payload=_MOCK_SYNONYMS):
    with (
        patch("utils.query_generator.get_llm_config", return_value=_mock_config()),
        patch("litellm.acompletion", new=AsyncMock(return_value=_mock_llm_response(payload))),
    ):
        return await QueryGenerator().generate_queries_async(profile)


# ── query structure ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_query_count_is_subjects_x_conditions_x_sources():
    """2 materials × 1 property × 3 sources = 6 queries."""
    assert len(await _generate(_profile())) == 6


@pytest.mark.asyncio
async def test_one_term_group_per_query_facet():
    for q in await _generate(_profile()):
        assert len(q.term_groups) == 2, q.term_groups


@pytest.mark.asyncio
async def test_group_heads_are_display_terms():
    for q in await _generate(_profile()):
        assert q.term_groups[1][0] == "capacity fade"
        assert q.base_terms[0] in {"graphite", "lithium iron phosphate"}


@pytest.mark.asyncio
async def test_no_temporal_range_in_search_terms():
    for q in await _generate(_profile()):
        for term in q.base_terms + [t for g in q.term_groups for t in g]:
            assert ".." not in term, f"Temporal range leaked into terms: {term!r}"


@pytest.mark.asyncio
async def test_subject_synonyms_in_first_group_capped_by_facet():
    queries = await _generate(_profile(sources=["preprints"]))
    graphite = [q for q in queries if q.base_terms[0] == "graphite"]
    assert graphite
    for q in graphite:
        assert q.term_groups[0] == ["graphite", "carbon anode", "C6"]


@pytest.mark.asyncio
async def test_keywords_fill_the_last_group_after_synonyms():
    payload = {"property": {"capacity_fade": ["capacity loss"]}}
    queries = await _generate(_profile(sources=["preprints"], keywords=["SEI growth"]), payload)
    for q in queries:
        assert q.term_groups[1] == ["capacity fade", "capacity loss", "SEI growth"]


@pytest.mark.asyncio
async def test_synonyms_take_precedence_over_keywords_when_group_is_full():
    queries = await _generate(_profile(sources=["preprints"], keywords=["SEI growth"]))
    for q in queries:
        assert q.term_groups[1] == ["capacity fade", "capacity loss", "cycle life", "degradation"]


@pytest.mark.asyncio
async def test_synonym_override_source_uses_its_own_terms():
    queries = await _generate(_profile(sources=["arxiv"]))
    assert queries
    for q in queries:
        assert set(q.term_groups[1]) & _OVERRIDE_TERMS, q.term_groups[1]
        assert "capacity loss" not in q.term_groups[1]
        # Subject groups keep their synonyms.
        assert len(q.term_groups[0]) == 3


@pytest.mark.asyncio
async def test_synonym_override_source_prefers_keywords():
    queries = await _generate(_profile(sources=["arxiv"], keywords=["SEI growth"]))
    for q in queries:
        assert q.term_groups[1] == ["capacity fade", "SEI growth"]


@pytest.mark.asyncio
async def test_unknown_sources_are_ignored():
    queries = await _generate(_profile(sources=["preprints", "not_a_source"]))
    assert {q.source_target for q in queries} == {"preprints"}


@pytest.mark.asyncio
async def test_synonym_prompt_uses_pack_wording():
    mock = AsyncMock(return_value=_mock_llm_response(_MOCK_SYNONYMS))
    with (
        patch("utils.query_generator.get_llm_config", return_value=_mock_config()),
        patch("litellm.acompletion", new=mock),
    ):
        await QueryGenerator().generate_queries_async(_profile())
    prompt = mock.call_args.kwargs["messages"][0]["content"]
    assert "electrochemistry expert" in prompt
    assert "Materials (material): graphite, lithium_iron_phosphate" in prompt
    assert "- material: 2-3 synonyms (chemical formula, common name)." in prompt
    assert "- property: up to 3 synonyms." in prompt


# ── fetcher query string integration ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_epmc_query_string_uses_or():
    for q in await _generate(_profile(sources=["preprints"])):
        s = _epmc_query_terms(q)
        assert " OR " in s, s
        assert " AND " in s, s


@pytest.mark.asyncio
async def test_arxiv_query_string_uses_or():
    for q in await _generate(_profile(sources=["arxiv"])):
        assert " OR " in _arxiv_query_terms(q)


# ── fallback behaviour ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_falls_back_to_bare_terms_on_llm_failure():
    profile = _profile()
    with (
        patch("utils.query_generator.get_llm_config", return_value=_mock_config()),
        patch("litellm.acompletion", side_effect=Exception("LLM unavailable")),
    ):
        queries = await QueryGenerator().generate_queries_async(profile)
    assert queries
    for q in queries:
        assert len(q.term_groups) == 2
        assert len(q.term_groups[0]) == 1


@pytest.mark.asyncio
async def test_empty_condition_facet_falls_back_to_cross_product():
    queries = await _generate(_profile(sources=["preprints"], properties=()))
    assert queries
    for q in queries:
        assert q.term_groups == []
        assert q.base_terms[1] == "property"


def test_sync_cross_product_uses_defaults_for_empty_facets():
    profile = ResearcherProfile(researcher_id="r", name="R", source_targets=["preprints"])
    queries = QueryGenerator().generate_queries(profile)
    assert [q.base_terms for q in queries] == [["material", "property"]]
    assert queries[0].query_string == "material AND property"
