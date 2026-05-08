# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for Augmentation A: verification pass in RAGAgent."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent():
    """Return a RAGAgent with stubbed store and RAG store."""
    from agents.rag_agent import RAGAgent

    agent = RAGAgent.__new__(RAGAgent)
    agent._store = MagicMock()
    agent._store.get_verify_cache.return_value = None
    agent._store.set_verify_cache.return_value = None
    agent._store.get_paper_metadata.return_value = {"title": "Test Paper"}
    agent._rag = MagicMock()
    agent._chat_model = "anthropic/claude-sonnet-4-6"
    return agent


def _ki(paper_id: str, insight: str) -> dict:
    return {"paper_id": paper_id, "insight": insight}


def _verify_result(supported: bool | None, confidence: float | None = 0.9) -> dict:
    reason = "ok" if supported else ("nope" if supported is False else "verification_failed")
    return {"supported": supported, "confidence": confidence, "reason": reason}


# ---------------------------------------------------------------------------
# _verify_proposal_claims — flagging rule
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("results,expected_flagged", [
    # 0 of 3 unsupported — not flagged
    ([True, True, True], False),
    # 1 of 3 unsupported — exactly 1/3, not flagged (> not >=)
    ([True, True, False], False),
    # 2 of 3 unsupported — flagged
    ([True, False, False], True),
    # 3 of 3 unsupported — flagged
    ([False, False, False], True),
])
async def test_flagging_rule(results, expected_flagged):
    agent = _make_agent()
    paper_text_by_id = {f"p{i}": "abstract text" for i in range(len(results))}
    insights = [_ki(f"p{i}", f"insight {i}") for i in range(len(results))]
    proposal = {"key_insights": insights}

    verify_returns = [_verify_result(r) for r in results]

    with patch(
        "agents.rag_agent.RAGAgent._verify_one_claim",
        new=AsyncMock(side_effect=[
            {"claim": ki["insight"], "paper_id": ki["paper_id"], **vr}
            for ki, vr in zip(insights, verify_returns)
        ]),
    ):
        v = await agent._verify_proposal_claims(proposal, paper_text_by_id)

    assert v["flagged"] == expected_flagged
    assert v["supported"] == sum(1 for r in results if r is True)
    assert v["unsupported"] == sum(1 for r in results if r is False)


@pytest.mark.asyncio
async def test_nulls_excluded_from_flagging_ratio():
    """Null (failed) entries must not count toward checked_claims."""
    agent = _make_agent()
    # 1 unsupported, 1 null — checked_claims=1, unsupported/1 = 100% — flagged
    # BUT if nulls were counted: 1/2 = 50% — also flagged, same answer here.
    # Use: 1 supported, 1 null — checked_claims=1, 0/1 = 0% — not flagged.
    insights = [_ki("p0", "i0"), _ki("p1", "i1")]
    proposal = {"key_insights": insights}
    paper_text_by_id = {"p0": "text", "p1": "text"}

    detail_returns = [
        {"claim": "i0", "paper_id": "p0", **_verify_result(True, 0.9)},
        {"claim": "i1", "paper_id": "p1", **_verify_result(None, None)},
    ]

    with patch(
        "agents.rag_agent.RAGAgent._verify_one_claim",
        new=AsyncMock(side_effect=detail_returns),
    ):
        v = await agent._verify_proposal_claims(proposal, paper_text_by_id)

    assert v["checked_claims"] == 1   # null excluded
    assert v["supported"] == 1
    assert v["unsupported"] == 0
    assert v["flagged"] is False


@pytest.mark.asyncio
async def test_empty_key_insights():
    agent = _make_agent()
    v = await agent._verify_proposal_claims({"key_insights": []}, {})
    assert v["checked_claims"] == 0
    assert v["flagged"] is False
    assert v["details"] == []


# ---------------------------------------------------------------------------
# _verify_one_claim — caching
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_one_claim_cache_hit():
    """If SQLite cache has a result, the LLM must not be called."""
    agent = _make_agent()
    cached = {"supported": True, "confidence": 0.99, "reason": "cached"}
    agent._store.get_verify_cache.return_value = cached

    with patch("utils.llm_verifier.verify_claim", new=AsyncMock()) as mock_llm:
        result = await agent._verify_one_claim("p1", "some insight", "abstract text")

    mock_llm.assert_not_called()
    assert result["supported"] is True
    assert result["claim"] == "some insight"
    assert result["paper_id"] == "p1"


@pytest.mark.asyncio
async def test_verify_one_claim_cache_miss_stores_result():
    """On cache miss, LLM is called and result is written to SQLite."""
    agent = _make_agent()
    agent._store.get_verify_cache.return_value = None
    llm_result = {"supported": False, "confidence": 0.75, "reason": "weak claim"}

    with patch("utils.llm_verifier.verify_claim", new=AsyncMock(return_value=llm_result)):
        result = await agent._verify_one_claim("p1", "some insight", "abstract text")

    agent._store.set_verify_cache.assert_called_once()
    assert result["supported"] is False
    assert result["paper_id"] == "p1"


# ---------------------------------------------------------------------------
# synthesize_combinations integration — verification field is populated
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesize_combinations_has_verification_field():
    """End-to-end stub: verification field is attached to every proposal."""
    from agents.rag_agent import RAGAgent

    agent = RAGAgent.__new__(RAGAgent)
    agent._store = MagicMock()
    agent._store.get_paper_metadata.return_value = {"title": "Test"}
    agent._store.get_verify_cache.return_value = None
    agent._store.set_verify_cache.return_value = None
    agent._rag = MagicMock()
    agent._rag.count.return_value = 5
    agent._rag.query.return_value = [
        {"paper_id": "abc123", "document": "Abstract text.", "distance": 0.5}
    ]
    agent._chat_model = "anthropic/claude-sonnet-4-6"

    fake_proposal = {
        "theme": "test theme",
        "suggestion": "Do an experiment combining A and B.",
        "rationale": "Because [abc123] shows something.",
        "key_insights": [{"paper_id": "abc123", "insight": "A finding."}],
        "supporting_papers": ["abc123"],
    }

    llm_verify_result = {"supported": True, "confidence": 0.9, "reason": "Matches."}

    with (
        patch.object(agent, "index_new_papers", new=AsyncMock(return_value={})),
        patch.object(agent, "_llm_proposals", new=AsyncMock(return_value=[fake_proposal])),
        patch.object(agent, "_check_novelty", new=AsyncMock(return_value=(True, ""))),
        patch("utils.llm_verifier.verify_claim", new=AsyncMock(return_value=llm_verify_result)),
    ):
        results = await agent.synthesize_combinations(
            researcher_id="r1",
            species=["poplar"],
            stresses=["drought"],
            methods=["hyperspectral_imaging"],
        )

    assert len(results) == 1
    proposal = results[0]
    assert "verification" in proposal
    v = proposal["verification"]
    assert v["checked_claims"] == 1
    assert v["supported"] == 1
    assert v["flagged"] is False
    assert len(v["details"]) == 1
    assert v["details"][0]["supported"] is True
