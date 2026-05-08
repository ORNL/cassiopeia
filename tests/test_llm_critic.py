# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for utils/llm_critic.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.llm_critic import critique_proposal


def _mock_response(payload: dict) -> MagicMock:
    msg = MagicMock()
    msg.content = json.dumps(payload)
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


_GOOD_CRITIQUE = {
    "novelty": {"assessment": "novel", "reasoning": "No prior work found.", "closest_prior_work": None},
    "confounds": [],
    "evidence_strength": {"assessment": "well_supported", "reasoning": "Claims match abstracts."},
    "feasibility_concerns": [],
    "overall_recommendation": "pursue",
    "summary": "Strong proposal.",
}

_PROPOSAL = {
    "theme": "root-canopy coupling",
    "suggestion": "Combine drought stress with VNIR imaging.",
    "rationale": "Paper P1 shows X [P1].",
    "key_insights": [{"paper_id": "P1", "insight": "X was observed."}],
    "verification": {
        "checked_claims": 1,
        "supported": 1,
        "unsupported": 0,
        "flagged": False,
        "details": [
            {
                "claim": "X was observed.",
                "paper_id": "P1",
                "supported": True,
                "confidence": 0.9,
                "reason": "Direct match.",
            }
        ],
    },
}


@pytest.mark.asyncio
async def test_critique_proposal_pursue():
    resp = _mock_response(_GOOD_CRITIQUE)
    with patch("litellm.acompletion", new=AsyncMock(return_value=resp)):
        result = await critique_proposal(_PROPOSAL, similar_papers=[], instruments=["VNIR"])
    assert result["overall_recommendation"] == "pursue"
    assert result["summary"] == "Strong proposal."


@pytest.mark.asyncio
async def test_critique_proposal_refine():
    payload = {**_GOOD_CRITIQUE, "overall_recommendation": "refine"}
    resp = _mock_response(payload)
    with patch("litellm.acompletion", new=AsyncMock(return_value=resp)):
        result = await critique_proposal(_PROPOSAL, similar_papers=[], instruments=["VNIR"])
    assert result["overall_recommendation"] == "refine"


@pytest.mark.asyncio
async def test_critique_proposal_deprioritize():
    payload = {
        **_GOOD_CRITIQUE,
        "overall_recommendation": "deprioritize",
        "confounds": [{"concern": "Confounding environmental factor.", "severity": "high"}],
    }
    resp = _mock_response(payload)
    with patch("litellm.acompletion", new=AsyncMock(return_value=resp)):
        result = await critique_proposal(_PROPOSAL, similar_papers=[], instruments=[])
    assert result["overall_recommendation"] == "deprioritize"
    assert len(result["confounds"]) == 1


@pytest.mark.asyncio
async def test_critique_proposal_malformed_json_then_success():
    """First call returns invalid JSON; second call returns valid JSON — should succeed."""
    bad_resp = MagicMock()
    bad_resp.choices[0].message.content = "not json at all"
    good_resp = _mock_response(_GOOD_CRITIQUE)

    with patch("litellm.acompletion", new=AsyncMock(side_effect=[bad_resp, good_resp])):
        result = await critique_proposal(_PROPOSAL, similar_papers=[], instruments=[])
    assert result is not None
    assert result["overall_recommendation"] == "pursue"


@pytest.mark.asyncio
async def test_critique_proposal_persistent_failure():
    """All retries fail — must return None, not raise."""
    bad_resp = MagicMock()
    bad_resp.choices[0].message.content = "not json at all"

    with patch("litellm.acompletion", new=AsyncMock(return_value=bad_resp)):
        result = await critique_proposal(_PROPOSAL, similar_papers=[], instruments=[])
    assert result is None


@pytest.mark.asyncio
async def test_critique_proposal_llm_exception():
    """LLM raises an exception — must return None."""
    with patch("litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("timeout"))):
        result = await critique_proposal(_PROPOSAL, similar_papers=[], instruments=[])
    assert result is None


@pytest.mark.asyncio
async def test_critique_proposal_missing_required_key():
    """First call returns JSON missing 'novelty' key (triggers KeyError → retry); second succeeds."""
    incomplete = {
        "evidence_strength": {"assessment": "well_supported", "reasoning": "OK."},
        "overall_recommendation": "pursue",
        "summary": "Missing novelty key.",
    }
    bad_resp = _mock_response(incomplete)
    good_resp = _mock_response(_GOOD_CRITIQUE)

    with patch("litellm.acompletion", new=AsyncMock(side_effect=[bad_resp, good_resp])):
        result = await critique_proposal(_PROPOSAL, similar_papers=[], instruments=[])
    assert result is not None
    assert result["overall_recommendation"] == "pursue"
