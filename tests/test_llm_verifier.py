# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for utils/llm_verifier.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.llm_verifier import verify_claim


def _mock_response(payload: dict) -> MagicMock:
    msg = MagicMock()
    msg.content = json.dumps(payload)
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


@pytest.mark.asyncio
async def test_verify_claim_supported():
    resp = _mock_response({"supported": True, "confidence": 0.95, "reason": "Direct match."})
    with patch("litellm.acompletion", new=AsyncMock(return_value=resp)):
        result = await verify_claim("Some paper text.", "The paper reports X.")
    assert result["supported"] is True
    assert result["confidence"] == pytest.approx(0.95)
    assert result["reason"] == "Direct match."


@pytest.mark.asyncio
async def test_verify_claim_unsupported():
    resp = _mock_response({"supported": False, "confidence": 0.80, "reason": "Not mentioned."})
    with patch("litellm.acompletion", new=AsyncMock(return_value=resp)):
        result = await verify_claim("Some paper text.", "The paper reports Y.")
    assert result["supported"] is False
    assert result["confidence"] == pytest.approx(0.80)


@pytest.mark.asyncio
async def test_verify_claim_malformed_json_then_success():
    """First call returns invalid JSON; second call returns valid JSON — should succeed."""
    bad_resp = _mock_response({})  # missing "supported" key → KeyError
    bad_resp.choices[0].message.content = "not json at all"
    good_resp = _mock_response({"supported": True, "confidence": 0.7, "reason": "OK."})

    with patch("litellm.acompletion", new=AsyncMock(side_effect=[bad_resp, good_resp])):
        result = await verify_claim("text", "insight")
    assert result["supported"] is True


@pytest.mark.asyncio
async def test_verify_claim_persistent_failure():
    """All retries fail — must return null entry, not raise."""
    bad_resp = MagicMock()
    bad_resp.choices[0].message.content = "```not json```"

    with patch("litellm.acompletion", new=AsyncMock(return_value=bad_resp)):
        result = await verify_claim("text", "insight")
    assert result["supported"] is None
    assert result["confidence"] is None
    assert result["reason"] == "verification_failed"


@pytest.mark.asyncio
async def test_verify_claim_llm_exception():
    """LLM raises an exception — must return null entry."""
    with patch("litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("timeout"))):
        result = await verify_claim("text", "insight")
    assert result["supported"] is None
    assert result["reason"] == "verification_failed"
