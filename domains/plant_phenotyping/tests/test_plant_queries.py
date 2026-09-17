# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Query generation for a real APPL dashboard submission.

  Researcher : Fred
  Species    : pennycress, poplar, arabidopsis
  Stresses   : heavy_metal
  Sources    : arxiv, biorxiv, plos_one, frontiers

The LLM call is mocked.  These pin the behaviour APPL relied on before
domain packs: species × stress queries, stress synonyms in non-arXiv queries,
and bioinformatics framing for arXiv.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.schemas import ResearcherProfile
from utils.query_generator import QueryGenerator

_MOCK_SYNONYMS = {
    "species": {
        "pennycress": ["Thlaspi arvense", "field pennycress"],
        "poplar": ["Populus", "Populus nigra"],
        "arabidopsis": ["Arabidopsis thaliana", "mouse-ear cress"],
    },
    "stress": {
        "heavy_metal": ["metal stress", "cadmium", "zinc toxicity", "phytoremediation", "nickel"],
    },
}

_ARXIV_BIO_TERMS = {"transcriptome", "RNA-seq", "gene expression", "GWAS", "genomics"}


def _config():
    config = MagicMock()
    config.for_scoring.return_value = {"model": "test/mock-model"}
    return config


def _response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps(payload)
    return resp


def _fred(keywords=()) -> ResearcherProfile:
    return ResearcherProfile(
        researcher_id="fred",
        name="Fred",
        facets={"species": ["pennycress", "poplar", "arabidopsis"], "stress": ["heavy_metal"]},
        expertise_keywords=list(keywords),
        source_targets=["arxiv", "biorxiv", "plos_one", "frontiers"],
        time_range_months=84,
    )


async def _generate(profile):
    with (
        patch("utils.query_generator.get_llm_config", return_value=_config()),
        patch("litellm.acompletion", new=AsyncMock(return_value=_response(_MOCK_SYNONYMS))),
    ):
        return await QueryGenerator().generate_queries_async(profile)


@pytest.mark.asyncio
async def test_species_x_stress_x_sources():
    assert len(await _generate(_fred())) == 12


@pytest.mark.asyncio
async def test_species_synonyms_in_group_1():
    for q in await _generate(_fred()):
        if q.base_terms[0] == "pennycress":
            assert q.term_groups[0] == ["pennycress", "Thlaspi arvense", "field pennycress"]


@pytest.mark.asyncio
async def test_stress_synonyms_in_group_2_non_arxiv():
    for q in await _generate(_fred()):
        if q.source_target == "biorxiv":
            assert q.term_groups[1] == [
                "heavy metal", "metal stress", "cadmium", "zinc toxicity", "phytoremediation",
            ]


@pytest.mark.asyncio
async def test_arxiv_queries_have_bioinformatics_terms():
    arxiv = [q for q in await _generate(_fred()) if q.source_target == "arxiv"]
    assert arxiv
    for q in arxiv:
        assert q.term_groups[1][0] == "heavy metal"
        assert set(q.term_groups[1][1:]) <= _ARXIV_BIO_TERMS
        assert len(q.term_groups[1]) == 5


@pytest.mark.asyncio
async def test_arxiv_queries_prefer_profile_keywords():
    arxiv = [q for q in await _generate(_fred(["hyperaccumulation"])) if q.source_target == "arxiv"]
    for q in arxiv:
        assert q.term_groups[1] == ["heavy metal", "hyperaccumulation"]


def test_rescue_pass_defaults_match_previous_behaviour():
    profile = ResearcherProfile(researcher_id="r", name="R", source_targets=["pubmed"])
    [query] = QueryGenerator().generate_queries(profile)
    assert query.base_terms == ["plant", "stress"]


def test_closed_stress_vocabulary_is_enforced(plant_pack):
    facets = plant_pack.validate_facets({"stress": ["drought", "heat wave"], "species": ["maize"]})
    assert facets["stress"] == ["drought"]
    assert facets["species"] == ["maize"]
