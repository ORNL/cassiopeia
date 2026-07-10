# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for Augmentation D: critic pass in RAGAgent."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_LLM = {"model": "test/mock-model"}


def _make_mock_config():
    config = MagicMock()
    config.for_scoring.return_value = _MOCK_LLM
    config.for_reasoning.return_value = _MOCK_LLM
    return config


def _make_agent():
    """Return a RAGAgent with stubbed store and RAG store."""
    from agents.rag_agent import RAGAgent

    agent = RAGAgent.__new__(RAGAgent)
    agent._store = MagicMock()
    agent._store.get_verify_cache.return_value = None
    agent._store.set_verify_cache.return_value = None
    agent._store.get_paper_metadata.return_value = {"title": "Test Paper"}
    agent._rag = MagicMock()
    agent._rag.query.return_value = []
    return agent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_GOOD_CRITIQUE = {
    "novelty": {"assessment": "novel", "reasoning": "No prior work found.", "closest_prior_work": None},
    "confounds": [],
    "evidence_strength": {"assessment": "well_supported", "reasoning": "Claims match abstracts."},
    "feasibility_concerns": [],
    "overall_recommendation": "pursue",
    "summary": "Strong proposal.",
}

_PROPOSAL = {
    "schema_version": 2,
    "proposal_id": "abc123",
    "theme": "test",
    "suggestion": "Do X combining A and B.",
    "rationale": "Because [p1] shows Y.",
    "key_insights": [{"paper_id": "p1", "insight": "Y was observed."}],
    "supporting_papers": ["p1"],
    "novelty_warning": "",
    "verification": {
        "checked_claims": 1,
        "supported": 1,
        "unsupported": 0,
        "flagged": False,
        "details": [],
    },
}


# ---------------------------------------------------------------------------
# critique_proposals — unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_critique_proposals_adds_critique_field():
    """critique_proposals attaches the critique dict to each proposal."""
    agent = _make_agent()

    with (
        patch("agents.rag_agent.get_llm_config", return_value=_make_mock_config()),
        patch("utils.llm_critic.critique_proposal", new=AsyncMock(return_value=_GOOD_CRITIQUE)),
    ):
        result = await agent.critique_proposals([_PROPOSAL], researcher_id="r1", instruments=["VNIR"])

    assert len(result) == 1
    assert result[0]["critique"] is not None
    assert result[0]["critique"]["overall_recommendation"] == "pursue"


@pytest.mark.asyncio
async def test_critique_proposals_none_on_failure():
    """When critique_proposal returns None, the critique field is set to None."""
    agent = _make_agent()

    with (
        patch("agents.rag_agent.get_llm_config", return_value=_make_mock_config()),
        patch("utils.llm_critic.critique_proposal", new=AsyncMock(return_value=None)),
    ):
        result = await agent.critique_proposals([_PROPOSAL], researcher_id="r1", instruments=[])

    assert result[0]["critique"] is None


@pytest.mark.asyncio
async def test_critique_proposals_concurrent():
    """critique_proposals calls critique_proposal once per proposal concurrently."""
    agent = _make_agent()
    proposals = [_PROPOSAL, _PROPOSAL, _PROPOSAL]

    mock_critique = AsyncMock(return_value=_GOOD_CRITIQUE)
    with (
        patch("agents.rag_agent.get_llm_config", return_value=_make_mock_config()),
        patch("utils.llm_critic.critique_proposal", new=mock_critique),
    ):
        result = await agent.critique_proposals(proposals, researcher_id="r1", instruments=["VNIR"])

    assert mock_critique.call_count == 3


@pytest.mark.asyncio
async def test_critique_proposals_preserves_existing_fields():
    """critique_proposals does not overwrite any existing proposal fields."""
    agent = _make_agent()

    with (
        patch("agents.rag_agent.get_llm_config", return_value=_make_mock_config()),
        patch("utils.llm_critic.critique_proposal", new=AsyncMock(return_value=_GOOD_CRITIQUE)),
    ):
        result = await agent.critique_proposals([_PROPOSAL], researcher_id="r1", instruments=[])

    assert result[0]["suggestion"] == _PROPOSAL["suggestion"]
    assert result[0]["verification"] == _PROPOSAL["verification"]


# ---------------------------------------------------------------------------
# synthesize_combinations integration — with_critique flag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesize_combinations_with_critique_false():
    """synthesize_combinations with with_critique=False must not attach critique field."""
    from agents.rag_agent import RAGAgent

    agent = RAGAgent.__new__(RAGAgent)
    agent._store = MagicMock()
    agent._store.get_paper_metadata.return_value = {"title": "Test"}
    agent._store.get_verify_cache.return_value = None
    agent._store.set_verify_cache.return_value = None
    agent._rag = MagicMock()
    agent._rag.count.return_value = 5
    agent._rag.query.return_value = [
        {"paper_id": "p1", "document": "Abstract text.", "distance": 0.5}
    ]

    fake_proposal = {
        "theme": "test theme",
        "suggestion": "Do an experiment combining A and B.",
        "rationale": "Because [p1] shows something.",
        "key_insights": [{"paper_id": "p1", "insight": "A finding."}],
        "supporting_papers": ["p1"],
    }

    llm_verify_result = {"supported": True, "confidence": 0.9, "reason": "Matches."}

    with (
        patch("agents.rag_agent.get_llm_config", return_value=_make_mock_config()),
        patch.object(agent, "index_new_papers", new=AsyncMock(return_value={})),
        patch.object(agent, "_llm_proposals", new=AsyncMock(return_value=[fake_proposal])),
        patch.object(agent, "_check_novelty", new=AsyncMock(return_value=(True, ""))),
        patch("utils.llm_verifier.verify_claim", new=AsyncMock(return_value=llm_verify_result)),
    ):
        result = await agent.synthesize_combinations(
            researcher_id="r1",
            species=["poplar"],
            stresses=["drought"],
            methods=["hyperspectral_imaging"],
            with_critique=False,
            max_iterations=0,
        )

    assert len(result) == 1
    assert "critique" not in result[0]


@pytest.mark.asyncio
async def test_synthesize_combinations_with_critique_true():
    """synthesize_combinations with with_critique=True attaches critique to every proposal."""
    from agents.rag_agent import RAGAgent

    agent = RAGAgent.__new__(RAGAgent)
    agent._store = MagicMock()
    agent._store.get_paper_metadata.return_value = {"title": "Test"}
    agent._store.get_verify_cache.return_value = None
    agent._store.set_verify_cache.return_value = None
    agent._rag = MagicMock()
    agent._rag.count.return_value = 5
    agent._rag.query.return_value = [
        {"paper_id": "p1", "document": "Abstract text.", "distance": 0.5}
    ]

    fake_proposal = {
        "theme": "test theme",
        "suggestion": "Do an experiment combining A and B.",
        "rationale": "Because [p1] shows something.",
        "key_insights": [{"paper_id": "p1", "insight": "A finding."}],
        "supporting_papers": ["p1"],
    }

    llm_verify_result = {"supported": True, "confidence": 0.9, "reason": "Matches."}

    with (
        patch("agents.rag_agent.get_llm_config", return_value=_make_mock_config()),
        patch.object(agent, "index_new_papers", new=AsyncMock(return_value={})),
        patch.object(agent, "_llm_proposals", new=AsyncMock(return_value=[fake_proposal])),
        patch.object(agent, "_check_novelty", new=AsyncMock(return_value=(True, ""))),
        patch("utils.llm_verifier.verify_claim", new=AsyncMock(return_value=llm_verify_result)),
        patch("utils.llm_critic.critique_proposal", new=AsyncMock(return_value=_GOOD_CRITIQUE)),
    ):
        result = await agent.synthesize_combinations(
            researcher_id="r1",
            species=["poplar"],
            stresses=["drought"],
            methods=["hyperspectral_imaging"],
            with_critique=True,
            max_iterations=0,
        )

    assert len(result) == 1
    assert "critique" in result[0]
    assert result[0]["critique"]["overall_recommendation"] == "pursue"
