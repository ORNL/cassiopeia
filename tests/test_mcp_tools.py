# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for mcp_server.py — _not_ready guard and ToolResult envelope."""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Helpers — access the raw functions bypassing the @mcp.tool() decorator
# ---------------------------------------------------------------------------

def _import_mcp():
    import mcp_server
    return mcp_server


# ---------------------------------------------------------------------------
# _not_ready helper
# ---------------------------------------------------------------------------

def test_not_ready_returns_503_json():
    mcp = _import_mcp()
    raw = mcp._not_ready("my_tool")
    data = json.loads(raw)
    assert data["code"] == 503
    assert data["tool_name"] == "my_tool"
    assert "not ready" in data["result"].lower()


def test_not_ready_custom_component():
    mcp = _import_mcp()
    raw = mcp._not_ready("search_literature", "RAG agent")
    data = json.loads(raw)
    assert data["code"] == 503
    assert "RAG agent" in data["result"]


# ---------------------------------------------------------------------------
# Guard conditions — tool functions return 503 when handles are None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_top_papers_returns_503_when_handle_none(monkeypatch):
    import mcp_server
    monkeypatch.setattr(mcp_server, "_mining_handle", None)
    result = json.loads(await mcp_server.get_top_papers("r1"))
    assert result["code"] == 503


@pytest.mark.asyncio
async def test_detect_contradictions_returns_503_when_rag_none(monkeypatch):
    import mcp_server
    monkeypatch.setattr(mcp_server, "_rag_handle", None)
    result = json.loads(await mcp_server.detect_contradictions("r1"))
    assert result["code"] == 503


@pytest.mark.asyncio
async def test_anchor_search_returns_503_when_rag_none(monkeypatch):
    import mcp_server
    monkeypatch.setattr(mcp_server, "_rag_handle", None)
    result = json.loads(await mcp_server.anchor_search("10.1/test"))
    assert result["code"] == 503


@pytest.mark.asyncio
async def test_ask_knowledge_base_returns_503_when_rag_none(monkeypatch):
    import mcp_server
    monkeypatch.setattr(mcp_server, "_rag_handle", None)
    result = json.loads(await mcp_server.ask_knowledge_base("What did poplar show?"))
    assert result["code"] == 503


@pytest.mark.asyncio
async def test_litminer_status_returns_503_when_handles_none(monkeypatch):
    import mcp_server
    monkeypatch.setattr(mcp_server, "_mining_handle", None)
    monkeypatch.setattr(mcp_server, "_rag_handle", None)
    result = json.loads(await mcp_server.litminer_status())
    assert result["code"] == 503


# ---------------------------------------------------------------------------
# ToolResult schema
# ---------------------------------------------------------------------------

def test_tool_result_has_required_fields():
    from mcp_server import ToolResult
    tr = ToolResult(code=301, result={"papers": []}, tool_name="get_top_papers")
    data = json.loads(tr.model_dump_json())
    assert "code" in data
    assert "result" in data
    assert "tool_name" in data
