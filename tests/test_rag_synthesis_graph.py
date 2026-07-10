# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for Augmentation C: iterative gather-evidence in synthesize_combinations."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import litellm
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
    agent._store.get_paper_metadata.return_value = {"title": "Test Paper"}
    agent._store.get_verify_cache.return_value = None
    agent._store.set_verify_cache.return_value = None
    agent._rag = MagicMock()
    agent._rag.query.return_value = []
    return agent


def _make_state(**overrides) -> dict:
    base = {
        "profile": {
            "species": ["poplar"],
            "stresses": ["drought"],
            "methods": ["imaging"],
            "keywords": [],
        },
        "initial_papers": [{"paper_id": "p1", "document": "Abstract of paper 1."}],
        "additional_papers": [],
        "draft_proposals": [],
        "sub_queries_run": [],
        "pending_sub_queries": [],
        "iteration": 0,
        "done": False,
        "liked_proposals": [],
        "researcher_id": "r1",
        "max_iterations": 3,
        "n_proposals": 5,
        "llm_s": _MOCK_LLM,
        "llm_r": _MOCK_LLM,
    }
    return {**base, **overrides}


def _fake_proposal(suffix: str = "") -> dict:
    return {
        "theme": f"root-canopy coupling{suffix}",
        "suggestion": f"Combine A and B to test coupling{suffix}.",
        "rationale": f"Because [p1] shows X{suffix}.",
        "key_insights": [{"paper_id": "p1", "insight": f"X was observed{suffix}."}],
        "supporting_papers": ["p1"],
    }


def _litellm_gap_response(content: dict) -> MagicMock:
    """Wrap a dict as a fake litellm completion response."""
    mock = MagicMock()
    mock.choices[0].message.content = json.dumps(content)
    return mock


# ---------------------------------------------------------------------------
# _propose_node — unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_propose_node_first_iteration_uses_initial_papers_only():
    """First iteration (iteration=0): proposals are generated from initial_papers alone.

    No refinement addendum should appear in the prompt.
    """
    agent = _make_agent()
    state = _make_state(
        iteration=0,
        initial_papers=[{"paper_id": "p1", "document": "Abstract 1"}],
    )

    proposals = [_fake_proposal()]
    mock_llm = AsyncMock(return_value=proposals)

    with patch.object(agent, "_llm_proposals", new=mock_llm):
        result = await agent._propose_node(state)

    assert result["draft_proposals"] == proposals
    assert result["iteration"] == 1  # incremented by node
    prompt_used = mock_llm.call_args[0][0]
    assert "You have already drafted" not in prompt_used


@pytest.mark.asyncio
async def test_propose_node_second_iteration_includes_refinement_addendum():
    """Second iteration: the prompt includes the previous draft and the additional papers."""
    agent = _make_agent()
    prev_proposals = [_fake_proposal(" v1")]
    state = _make_state(
        iteration=1,
        initial_papers=[{"paper_id": "p1", "document": "Abstract 1"}],
        additional_papers=[{"paper_id": "p2", "document": "Abstract 2"}],
        draft_proposals=prev_proposals,
    )

    refined = [_fake_proposal(" v2")]
    mock_llm = AsyncMock(return_value=refined)

    with patch.object(agent, "_llm_proposals", new=mock_llm):
        result = await agent._propose_node(state)

    assert result["draft_proposals"] == refined
    assert result["iteration"] == 2
    prompt_used = mock_llm.call_args[0][0]
    assert "You have already drafted" in prompt_used
    assert "root-canopy coupling v1" in prompt_used


@pytest.mark.asyncio
async def test_propose_node_keeps_previous_draft_on_llm_failure():
    """When _llm_proposals raises, the previous draft proposals are preserved unchanged."""
    agent = _make_agent()
    prev_proposals = [_fake_proposal()]
    state = _make_state(iteration=1, draft_proposals=prev_proposals)

    api_err = litellm.APIError(status_code=500, message="timeout", llm_provider="mock", model="mock")
    with patch.object(agent, "_llm_proposals", new=AsyncMock(side_effect=api_err)):
        result = await agent._propose_node(state)

    assert result["draft_proposals"] == prev_proposals


# ---------------------------------------------------------------------------
# _identify_gaps_node — unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_identify_gaps_returns_done_when_evidence_sufficient():
    """done=True with no sub-queries: state must be marked done, pending list empty."""
    agent = _make_agent()
    state = _make_state(draft_proposals=[_fake_proposal()])

    gap_response = {"sub_queries": [], "done": True, "reasoning": "Well-grounded proposals."}

    with patch("litellm.acompletion", new=AsyncMock(return_value=_litellm_gap_response(gap_response))):
        result = await agent._identify_gaps_node(state)

    assert result["done"] is True
    assert result["pending_sub_queries"] == []


@pytest.mark.asyncio
async def test_identify_gaps_returns_sub_queries_when_gaps_exist():
    """When the LLM identifies gaps, sub-queries are placed in pending_sub_queries."""
    agent = _make_agent()
    state = _make_state(draft_proposals=[_fake_proposal()])

    gap_response = {
        "sub_queries": ["poplar drought ABA signaling", "VNIR reflectance stomata"],
        "done": False,
        "reasoning": "Need transporter expression data.",
    }

    with patch("litellm.acompletion", new=AsyncMock(return_value=_litellm_gap_response(gap_response))):
        result = await agent._identify_gaps_node(state)

    assert result["done"] is False
    assert result["pending_sub_queries"] == ["poplar drought ABA signaling", "VNIR reflectance stomata"]


@pytest.mark.asyncio
async def test_identify_gaps_treats_done_false_with_empty_sub_queries_as_done():
    """Malformed response (done=false, sub_queries=[]) is treated as done defensively."""
    agent = _make_agent()
    state = _make_state(draft_proposals=[_fake_proposal()])

    gap_response = {"sub_queries": [], "done": False, "reasoning": "Should not loop."}

    with patch("litellm.acompletion", new=AsyncMock(return_value=_litellm_gap_response(gap_response))):
        result = await agent._identify_gaps_node(state)

    assert result["done"] is True
    assert result["pending_sub_queries"] == []


@pytest.mark.asyncio
async def test_identify_gaps_caps_sub_queries_at_maximum():
    """Sub-queries beyond _MAX_SUB_QUERIES_PER_ITERATION are silently dropped."""
    from agents.rag_agent import _MAX_SUB_QUERIES_PER_ITERATION

    agent = _make_agent()
    state = _make_state(draft_proposals=[_fake_proposal()])

    too_many = [f"query {i}" for i in range(_MAX_SUB_QUERIES_PER_ITERATION + 5)]
    gap_response = {"sub_queries": too_many, "done": False, "reasoning": "Many gaps."}

    with patch("litellm.acompletion", new=AsyncMock(return_value=_litellm_gap_response(gap_response))):
        result = await agent._identify_gaps_node(state)

    assert len(result["pending_sub_queries"]) <= _MAX_SUB_QUERIES_PER_ITERATION


@pytest.mark.asyncio
async def test_identify_gaps_fails_safe_on_llm_error():
    """When the LLM call raises, identify_gaps sets done=True so the graph does not loop."""
    agent = _make_agent()
    state = _make_state(draft_proposals=[_fake_proposal()])

    api_err = litellm.APIError(status_code=500, message="network error", llm_provider="mock", model="mock")
    with patch("litellm.acompletion", new=AsyncMock(side_effect=api_err)):
        result = await agent._identify_gaps_node(state)

    assert result["done"] is True
    assert result["pending_sub_queries"] == []


# ---------------------------------------------------------------------------
# _retrieve_node — unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retrieve_node_dedupes_against_initial_and_additional_papers():
    """Papers already in initial_papers or additional_papers must not be added again."""
    agent = _make_agent()
    state = _make_state(
        initial_papers=[{"paper_id": "p1", "document": "abstract 1"}],
        additional_papers=[{"paper_id": "p2", "document": "abstract 2"}],
        pending_sub_queries=["drought signaling"],
    )

    # p1 already in initial, p2 already in additional, p3 is genuinely new
    agent._rag.query.return_value = [
        {"paper_id": "p1", "document": "abstract 1"},
        {"paper_id": "p2", "document": "abstract 2"},
        {"paper_id": "p3", "document": "abstract 3 — new"},
    ]

    result = await agent._retrieve_node(state)

    # p2 was already in additional_papers and must be preserved exactly once;
    # p3 is newly found and must appear; p1 (in initial_papers) must not move here.
    result_ids = [p["paper_id"] for p in result["additional_papers"]]
    assert "p3" in result_ids                                   # newly found
    assert result_ids.count("p2") == 1                         # preserved, not duplicated
    assert "p1" not in result_ids                              # stays in initial_papers


@pytest.mark.asyncio
async def test_retrieve_node_sets_done_when_zero_new_papers():
    """When all retrieved papers are already known, retrieve sets done=True."""
    agent = _make_agent()
    state = _make_state(
        initial_papers=[{"paper_id": "p1", "document": "abstract 1"}],
        additional_papers=[],
        pending_sub_queries=["drought signaling"],
    )

    agent._rag.query.return_value = [{"paper_id": "p1", "document": "abstract 1"}]

    result = await agent._retrieve_node(state)

    assert result["done"] is True
    assert result["additional_papers"] == []


@pytest.mark.asyncio
async def test_retrieve_node_accumulates_new_papers_across_sub_queries():
    """New papers from multiple sub-queries are all appended to additional_papers."""
    agent = _make_agent()
    state = _make_state(
        initial_papers=[{"paper_id": "p1", "document": "A1"}],
        additional_papers=[],
        pending_sub_queries=["q1", "q2"],
    )

    agent._rag.query.side_effect = [
        [{"paper_id": "p2", "document": "A2"}],  # q1 finds p2
        [{"paper_id": "p3", "document": "A3"}],  # q2 finds p3
    ]

    result = await agent._retrieve_node(state)

    new_ids = {p["paper_id"] for p in result["additional_papers"]}
    assert new_ids == {"p2", "p3"}
    assert result["done"] is False  # new papers found → continue


@pytest.mark.asyncio
async def test_retrieve_node_moves_pending_queries_to_run_log():
    """Executed sub-queries are appended to sub_queries_run and pending list is cleared."""
    agent = _make_agent()
    state = _make_state(
        initial_papers=[{"paper_id": "p1", "document": "A1"}],
        sub_queries_run=["previous query"],
        pending_sub_queries=["new query A", "new query B"],
    )
    agent._rag.query.return_value = []

    result = await agent._retrieve_node(state)

    assert "new query A" in result["sub_queries_run"]
    assert "new query B" in result["sub_queries_run"]
    assert "previous query" in result["sub_queries_run"]
    assert result["pending_sub_queries"] == []


# ---------------------------------------------------------------------------
# Graph-level tests — real LangGraph, node methods mocked on agent instance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_graph_terminates_on_done_true_after_one_iteration():
    """The graph reaches finalize when identify_gaps returns done=True on the first pass."""
    agent = _make_agent()

    proposals = [_fake_proposal()]
    propose_call_count = 0

    async def _mock_propose(state):
        nonlocal propose_call_count
        propose_call_count += 1
        await asyncio.sleep(0)
        return {**state, "draft_proposals": proposals, "iteration": state["iteration"] + 1}

    async def _mock_identify_gaps(state):
        await asyncio.sleep(0)
        return {**state, "pending_sub_queries": [], "done": True}

    with (
        patch.object(agent, "_propose_node", new=_mock_propose),
        patch.object(agent, "_identify_gaps_node", new=_mock_identify_gaps),
    ):
        final = await agent._run_synthesis_graph(
            profile={"species": ["poplar"], "stresses": ["drought"], "methods": [], "keywords": []},
            initial_papers=[{"paper_id": "p1", "document": "A1"}],
            liked_proposals=[],
            researcher_id="r1",
            max_iterations=3,
            llm_s=_MOCK_LLM,
            llm_r=_MOCK_LLM,
        )

    assert final["draft_proposals"] == proposals
    assert propose_call_count == 1  # only one propose step was needed


@pytest.mark.asyncio
async def test_graph_terminates_on_max_iterations_cap():
    """The graph terminates at finalize when iteration count reaches max_iterations."""
    agent = _make_agent()

    propose_call_count = 0

    async def _mock_propose(state):
        nonlocal propose_call_count
        propose_call_count += 1
        await asyncio.sleep(0)
        return {**state, "draft_proposals": [_fake_proposal()], "iteration": state["iteration"] + 1}

    async def _mock_identify_gaps(state):
        await asyncio.sleep(0)
        # Always says not done and returns a sub-query — only iteration cap should stop the loop
        return {**state, "pending_sub_queries": ["some query"], "done": False}

    async def _mock_retrieve(state):
        await asyncio.sleep(0)
        # Always finds a new paper so done stays False from retrieve
        new = {"paper_id": f"pX{propose_call_count}", "document": "AX"}
        return {
            **state,
            "additional_papers": state["additional_papers"] + [new],
            "sub_queries_run": state["sub_queries_run"] + state["pending_sub_queries"],
            "pending_sub_queries": [],
            "done": False,
        }

    with (
        patch.object(agent, "_propose_node", new=_mock_propose),
        patch.object(agent, "_identify_gaps_node", new=_mock_identify_gaps),
        patch.object(agent, "_retrieve_node", new=_mock_retrieve),
    ):
        await agent._run_synthesis_graph(
            profile={"species": ["poplar"], "stresses": ["drought"], "methods": [], "keywords": []},
            initial_papers=[{"paper_id": "p1", "document": "A1"}],
            liked_proposals=[],
            researcher_id="r1",
            max_iterations=2,
            llm_s=_MOCK_LLM,
            llm_r=_MOCK_LLM,
        )

    # With max_iterations=2:
    #   propose (iter→1) → gaps → retrieve → propose (iter→2) → gaps (2 >= 2) → finalize
    assert propose_call_count == 2


@pytest.mark.asyncio
async def test_graph_terminates_on_zero_new_papers_from_retrieve():
    """The graph terminates when _retrieve_node finds no new papers (sets done=True)."""
    agent = _make_agent()

    propose_call_count = 0

    async def _mock_propose(state):
        nonlocal propose_call_count
        propose_call_count += 1
        await asyncio.sleep(0)
        return {**state, "draft_proposals": [_fake_proposal()], "iteration": state["iteration"] + 1}

    async def _mock_identify_gaps(state):
        await asyncio.sleep(0)
        return {**state, "pending_sub_queries": ["a query"], "done": False}

    async def _mock_retrieve(state):
        await asyncio.sleep(0)
        # 0 new papers → retrieve sets done=True
        return {
            **state,
            "sub_queries_run": state["sub_queries_run"] + state["pending_sub_queries"],
            "pending_sub_queries": [],
            "done": True,
        }

    with (
        patch.object(agent, "_propose_node", new=_mock_propose),
        patch.object(agent, "_identify_gaps_node", new=_mock_identify_gaps),
        patch.object(agent, "_retrieve_node", new=_mock_retrieve),
    ):
        final = await agent._run_synthesis_graph(
            profile={"species": ["poplar"], "stresses": ["drought"], "methods": [], "keywords": []},
            initial_papers=[{"paper_id": "p1", "document": "A1"}],
            liked_proposals=[],
            researcher_id="r1",
            max_iterations=3,
            llm_s=_MOCK_LLM,
            llm_r=_MOCK_LLM,
        )

    assert propose_call_count == 1
    assert final["done"] is True


# ---------------------------------------------------------------------------
# max_iterations=0 — single-shot bypass tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_iterations_zero_skips_run_synthesis_graph():
    """max_iterations=0 must bypass _run_synthesis_graph entirely."""
    agent = _make_agent()
    agent._rag.count.return_value = 5
    agent._rag.query.return_value = [{"paper_id": "p1", "document": "A1", "distance": 0.5}]

    mock_graph = AsyncMock()
    llm_verify = {"supported": True, "confidence": 0.9, "reason": "ok"}

    with (
        patch("agents.rag_agent.get_llm_config", return_value=_make_mock_config()),
        patch.object(agent, "index_new_papers", new=AsyncMock(return_value={})),
        patch.object(agent, "_llm_proposals", new=AsyncMock(return_value=[_fake_proposal()])),
        patch.object(agent, "_run_synthesis_graph", new=mock_graph),
        patch.object(agent, "_check_novelty", new=AsyncMock(return_value=(True, ""))),
        patch("utils.llm_verifier.verify_claim", new=AsyncMock(return_value=llm_verify)),
    ):
        result = await agent.synthesize_combinations(
            researcher_id="r1",
            species=["poplar"],
            stresses=["drought"],
            methods=[],
            max_iterations=0,
        )

    mock_graph.assert_not_called()
    assert len(result) == 1


@pytest.mark.asyncio
async def test_max_iterations_zero_produces_enriched_proposals():
    """max_iterations=0 still returns proposals with schema_version, verification, and no critique."""
    agent = _make_agent()
    agent._rag.count.return_value = 5
    agent._rag.query.return_value = [{"paper_id": "p1", "document": "A1", "distance": 0.5}]

    llm_verify = {"supported": True, "confidence": 0.9, "reason": "ok"}

    with (
        patch("agents.rag_agent.get_llm_config", return_value=_make_mock_config()),
        patch.object(agent, "index_new_papers", new=AsyncMock(return_value={})),
        patch.object(agent, "_llm_proposals", new=AsyncMock(return_value=[_fake_proposal()])),
        patch.object(agent, "_check_novelty", new=AsyncMock(return_value=(True, ""))),
        patch("utils.llm_verifier.verify_claim", new=AsyncMock(return_value=llm_verify)),
    ):
        result = await agent.synthesize_combinations(
            researcher_id="r1",
            species=["poplar"],
            stresses=["drought"],
            methods=[],
            max_iterations=0,
            with_critique=False,
        )

    assert len(result) == 1
    assert result[0]["schema_version"] == 4
    assert "verification" in result[0]
    assert "critique" not in result[0]
    assert result[0]["suggestion"] == _fake_proposal()["suggestion"]


# ---------------------------------------------------------------------------
# Integration tests — real LangGraph, node methods mocked on agent instance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integration_final_proposals_reference_both_initial_and_additional_papers():
    """End-to-end: after one gap+retrieve cycle, proposals cite papers from both pools."""
    agent = _make_agent()
    agent._rag.count.return_value = 5
    agent._rag.query.return_value = [{"paper_id": "p1", "document": "Abstract of p1.", "distance": 0.5}]

    propose_calls = 0

    async def _mock_propose(state):
        nonlocal propose_calls
        propose_calls += 1
        await asyncio.sleep(0)
        all_ids = [p["paper_id"] for p in state["initial_papers"] + state["additional_papers"]]
        return {
            **state,
            "draft_proposals": [{
                "theme": "multi-paper",
                "suggestion": f"Combine {' and '.join(all_ids)}.",
                "rationale": " ".join(f"[{pid}] supports this." for pid in all_ids),
                "key_insights": [{"paper_id": pid, "insight": f"Finding in {pid}."} for pid in all_ids],
                "supporting_papers": all_ids,
            }],
            "iteration": state["iteration"] + 1,
        }

    gap_calls = 0

    async def _mock_identify_gaps(state):
        nonlocal gap_calls
        gap_calls += 1
        await asyncio.sleep(0)
        if gap_calls == 1:
            return {**state, "pending_sub_queries": ["extra query"], "done": False}
        return {**state, "pending_sub_queries": [], "done": True}

    async def _mock_retrieve(state):
        await asyncio.sleep(0)
        new_paper = {"paper_id": "p2", "document": "Abstract of p2 — from sub-query."}
        return {
            **state,
            "additional_papers": state["additional_papers"] + [new_paper],
            "sub_queries_run": state["sub_queries_run"] + state["pending_sub_queries"],
            "pending_sub_queries": [],
            "done": False,
        }

    llm_verify = {"supported": True, "confidence": 0.9, "reason": "ok"}

    with (
        patch("agents.rag_agent.get_llm_config", return_value=_make_mock_config()),
        patch.object(agent, "index_new_papers", new=AsyncMock(return_value={})),
        patch.object(agent, "_propose_node", new=_mock_propose),
        patch.object(agent, "_identify_gaps_node", new=_mock_identify_gaps),
        patch.object(agent, "_retrieve_node", new=_mock_retrieve),
        patch.object(agent, "_check_novelty", new=AsyncMock(return_value=(True, ""))),
        patch("utils.llm_verifier.verify_claim", new=AsyncMock(return_value=llm_verify)),
    ):
        result = await agent.synthesize_combinations(
            researcher_id="r1",
            species=["poplar"],
            stresses=["drought"],
            methods=[],
            max_iterations=3,
        )

    assert len(result) == 1
    supporting = result[0]["supporting_papers"]
    assert "p1" in supporting  # from initial retrieval
    assert "p2" in supporting  # from sub-query retrieval
    assert propose_calls == 2  # two propose steps: one before gap, one after retrieve


@pytest.mark.asyncio
async def test_integration_verification_attached_on_iterative_path():
    """verification (Aug A) is correctly attached to proposals produced by the iterative path."""
    agent = _make_agent()
    agent._rag.count.return_value = 5
    agent._rag.query.return_value = [{"paper_id": "p1", "document": "A1", "distance": 0.5}]

    async def _mock_propose(state):
        await asyncio.sleep(0)
        return {**state, "draft_proposals": [_fake_proposal()], "iteration": state["iteration"] + 1}

    async def _mock_identify_gaps(state):
        await asyncio.sleep(0)
        return {**state, "pending_sub_queries": [], "done": True}

    llm_verify = {"supported": True, "confidence": 0.9, "reason": "ok"}

    with (
        patch("agents.rag_agent.get_llm_config", return_value=_make_mock_config()),
        patch.object(agent, "index_new_papers", new=AsyncMock(return_value={})),
        patch.object(agent, "_propose_node", new=_mock_propose),
        patch.object(agent, "_identify_gaps_node", new=_mock_identify_gaps),
        patch.object(agent, "_check_novelty", new=AsyncMock(return_value=(True, ""))),
        patch("utils.llm_verifier.verify_claim", new=AsyncMock(return_value=llm_verify)),
    ):
        result = await agent.synthesize_combinations(
            researcher_id="r1",
            species=["poplar"],
            stresses=["drought"],
            methods=[],
            max_iterations=3,
            with_critique=False,
        )

    assert len(result) == 1
    assert "verification" in result[0]
    v = result[0]["verification"]
    assert v["checked_claims"] >= 1
    assert v["supported"] >= 1


@pytest.mark.asyncio
async def test_integration_critique_attached_on_iterative_path_when_requested():
    """critique (Aug D) is attached to iterative-path proposals when with_critique=True."""
    from tests.test_rag_agent_aug_d import _GOOD_CRITIQUE

    agent = _make_agent()
    agent._rag.count.return_value = 5
    agent._rag.query.return_value = [{"paper_id": "p1", "document": "A1", "distance": 0.5}]

    async def _mock_propose(state):
        await asyncio.sleep(0)
        return {**state, "draft_proposals": [_fake_proposal()], "iteration": state["iteration"] + 1}

    async def _mock_identify_gaps(state):
        await asyncio.sleep(0)
        return {**state, "pending_sub_queries": [], "done": True}

    llm_verify = {"supported": True, "confidence": 0.9, "reason": "ok"}

    with (
        patch("agents.rag_agent.get_llm_config", return_value=_make_mock_config()),
        patch.object(agent, "index_new_papers", new=AsyncMock(return_value={})),
        patch.object(agent, "_propose_node", new=_mock_propose),
        patch.object(agent, "_identify_gaps_node", new=_mock_identify_gaps),
        patch.object(agent, "_check_novelty", new=AsyncMock(return_value=(True, ""))),
        patch("utils.llm_verifier.verify_claim", new=AsyncMock(return_value=llm_verify)),
        patch("utils.llm_critic.critique_proposal", new=AsyncMock(return_value=_GOOD_CRITIQUE)),
    ):
        result = await agent.synthesize_combinations(
            researcher_id="r1",
            species=["poplar"],
            stresses=["drought"],
            methods=[],
            max_iterations=3,
            with_critique=True,
        )

    assert len(result) == 1
    assert "critique" in result[0]
    assert result[0]["critique"]["overall_recommendation"] == "pursue"
