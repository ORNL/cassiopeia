# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Every APPL source must reach its upstream API and return at least one paper.

These make real network requests; skip them offline with:
    pytest -m "not integration"
"""

from __future__ import annotations

import pytest

from models.schemas import SearchQuery
from utils.source_fetchers import get_fetcher

pytestmark = pytest.mark.integration

# arXiv is a CS/physics server: plant-specific terms (pennycress, nickel stress)
# return nothing there, so it gets bioinformatics terms.
_TERMS = {
    "arxiv": ["arabidopsis", "genome"],
    "biorxiv": ["arabidopsis", "drought"],
    "pubmed": ["arabidopsis", "stress"],
    "frontiers": ["arabidopsis", "stress"],
    "plos_one": ["arabidopsis"],
    "nature_communications": ["arabidopsis", "thaliana"],
    "new_phytologist": ["arabidopsis"],
    "plant_physiology": ["arabidopsis"],
}


def test_every_pack_source_has_search_terms(plant_pack):
    assert set(_TERMS) == set(plant_pack.sources)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", sorted(_TERMS))
async def test_source_returns_papers(source):
    terms = _TERMS[source]
    query = SearchQuery(
        query_string=" AND ".join(terms),
        source_target=source,
        researcher_id="test",
        base_terms=terms,
    )
    papers = await get_fetcher(source).fetch(query, max_results=5)
    assert papers, f"{source} returned 0 papers — check connectivity"
    assert papers[0].title
    assert papers[0].source == source


@pytest.mark.asyncio
async def test_structured_full_text_for_an_open_access_pmc_paper():
    """PMC7468712 is an open-access Arabidopsis paper."""
    result = await get_fetcher("pubmed").fetch_full_text_structured("PMC7468712")
    assert result
    assert any(result.values())
