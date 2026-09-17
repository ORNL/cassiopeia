# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for the domain-pack loader and the pack-driven agent hooks."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from domains import (
    DomainPackError,
    current_domain,
    installed_packs,
    load_domain,
    set_domain,
)

# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _write_pack(tmp_path, **overrides):
    raw = {
        "name": "tiny",
        "facets": [{"key": "topic", "label": "Topics", "role": "subject"}],
        "sources": [{"key": "pre", "backend": "europepmc", "filter": "SRC:PPR"}],
        **overrides,
    }
    (tmp_path / "domain.yaml").write_text(yaml.safe_dump(raw))
    return tmp_path


def test_minimal_pack_loads_with_defaults(tmp_path):
    pack = load_domain(_write_pack(tmp_path))
    assert pack.name == "tiny"
    assert pack.sources["pre"].options == {"filter": "SRC:PPR"}
    assert pack.context_specs == ()
    assert pack.evaluators == ()
    assert pack.prompts.proposal_noun == "study"


def test_missing_pack_is_reported(tmp_path):
    with pytest.raises(DomainPackError, match="No domain.yaml"):
        load_domain(tmp_path / "nowhere")


def test_pack_without_query_facet_is_rejected(tmp_path):
    facets = [{"key": "tech", "label": "Techniques", "role": "technique"}]
    with pytest.raises(DomainPackError, match="subject or condition"):
        load_domain(_write_pack(tmp_path, facets=facets))


def test_unknown_role_is_rejected(tmp_path):
    facets = [{"key": "x", "label": "X", "role": "organism"}]
    with pytest.raises(DomainPackError, match="unknown role"):
        load_domain(_write_pack(tmp_path, facets=facets))


def test_pack_without_sources_is_rejected(tmp_path):
    with pytest.raises(DomainPackError, match="no sources"):
        load_domain(_write_pack(tmp_path, sources=[]))


def test_hooks_provide_evaluators(tmp_path):
    _write_pack(tmp_path)
    (tmp_path / "hooks.py").write_text(
        "class E:\n"
        "    key = 'review'\n"
        "    label = 'Reviewing'\n"
        "    def applies(self, context): return True\n"
        "    async def evaluate(self, proposal, context, llm_kwargs): return {}\n"
        "EVALUATORS = (E(),)\n"
    )
    pack = load_domain(tmp_path)
    assert [e.key for e in pack.evaluators] == ["review"]


def test_single_installed_pack_is_the_default(monkeypatch):
    monkeypatch.delenv("DOMAIN_PACK", raising=False)
    set_domain(None)
    if len(installed_packs()) != 1:
        pytest.skip("default selection only applies with exactly one installed pack")
    assert current_domain().name == installed_packs()[0]


def test_domain_pack_env_selects_a_path(monkeypatch, tmp_path):
    monkeypatch.setenv("DOMAIN_PACK", str(_write_pack(tmp_path)))
    set_domain(None)
    assert current_domain().name == "tiny"


# ---------------------------------------------------------------------------
# Facet helpers and manifest
# ---------------------------------------------------------------------------

def test_validate_facets_drops_unknown_facets_and_closed_vocabulary_values():
    pack = current_domain()
    facets = pack.validate_facets({
        "material": ["graphite", "anything goes"],
        "property": ["capacity_fade", "made_up"],
        "not_a_facet": ["x"],
    })
    assert facets == {
        "material": ["graphite", "anything goes"],
        "property": ["capacity_fade"],
        "technique": [],
    }


def test_annotate_matches_aliases_for_annotated_facets_only():
    tags = current_domain().annotate("LiFePO4 cells show capacity loss and DFT trends")
    assert tags == {"material": ["lithium_iron_phosphate"], "property": ["capacity_fade"]}


def test_load_context_reads_declared_env_vars(monkeypatch):
    from domains import ContextSpec

    pack = replace(current_domain(), context_specs=(ContextSpec(key="tools", label="Tools", env="TEST_TOOLS"),))
    monkeypatch.setenv("TEST_TOOLS", " a , b ,,")
    assert pack.load_context() == {"tools": ["a", "b"]}


def test_manifest_hides_hidden_vocabulary_and_exposes_roles():
    manifest = current_domain().manifest()
    prop = next(f for f in manifest["facets"] if f["key"] == "property")
    assert [v["value"] for v in prop["vocabulary"]] == ["ionic_conductivity", "capacity_fade"]
    assert prop["priority"] == "relevance"
    assert manifest["evaluators"] == []
    assert manifest["context"] == []
    assert {s["value"] for s in manifest["sources"]} == {"preprints", "flagship", "arxiv"}


# ---------------------------------------------------------------------------
# RAG agent: pack evaluators
# ---------------------------------------------------------------------------

class _Evaluator:
    key = "review"
    label = "Reviewing…"

    def __init__(self, result=None, error=None):
        self.result, self.error = result, error
        self.calls = 0

    def applies(self, context):
        return bool(context.get("tools"))

    async def evaluate(self, proposal, context, llm_kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return {"ok": proposal["suggestion"], "tools": context["tools"]}


def _agent():
    from agents.rag_agent import RAGAgent

    agent = RAGAgent.__new__(RAGAgent)
    agent._store = MagicMock()
    agent._rag = MagicMock()
    return agent


def _config():
    config = MagicMock()
    config.for_reasoning.return_value = {"model": "mock"}
    return config


@pytest.mark.asyncio
async def test_evaluate_proposals_runs_applicable_evaluators():
    evaluator = _Evaluator()
    set_domain(replace(current_domain(), evaluators=(evaluator,)))
    with patch("agents.rag_agent.get_llm_config", return_value=_config()):
        result = await _agent().evaluate_proposals(
            [{"suggestion": "s1"}], researcher_id="r1", context={"tools": ["t"]},
        )
    assert result == [{"suggestion": "s1", "review": {"ok": "s1", "tools": ["t"]}}]


@pytest.mark.asyncio
async def test_evaluate_proposals_skips_when_no_evaluator_applies():
    evaluator = _Evaluator()
    set_domain(replace(current_domain(), evaluators=(evaluator,)))
    proposals = [{"suggestion": "s1"}]
    result = await _agent().evaluate_proposals(proposals, researcher_id="r1", context={})
    assert result == proposals
    assert evaluator.calls == 0


@pytest.mark.asyncio
async def test_evaluate_proposals_records_failures_as_none():
    set_domain(replace(current_domain(), evaluators=(_Evaluator(error=RuntimeError("boom")),)))
    with patch("agents.rag_agent.get_llm_config", return_value=_config()):
        result = await _agent().evaluate_proposals(
            [{"suggestion": "s1"}], researcher_id="r1", context={"tools": ["t"]},
        )
    assert result[0]["review"] is None


@pytest.mark.asyncio
async def test_detect_contradictions_queries_from_query_facets():
    agent = _agent()
    agent._rag.count.return_value = 3
    agent._store.load_profile.return_value = {
        "facets": {"material": ["lithium_iron_phosphate"], "technique": ["dft"]},
        "expertise_keywords": ["SEI"],
    }
    agent._store.known_paper_ids.return_value = {"p1"}
    passes = AsyncMock(return_value=[])
    with (
        patch("agents.rag_agent.get_llm_config", return_value=_config()),
        patch.object(agent, "index_new_papers", new=AsyncMock(return_value={})),
        patch.object(agent, "_contradiction_pass", new=passes),
    ):
        await agent.detect_contradictions("r1", n_passes=3)
    queries = [c.args[0] for c in passes.call_args_list]
    assert queries == ["lithium iron phosphate", "SEI", "lithium iron phosphate"]


@pytest.mark.asyncio
async def test_anchor_lookup_tries_each_backend_once():
    agent = _agent()
    fetchers = {}

    def _fake_get_fetcher(key):
        fetcher = MagicMock()
        fetcher.lookup_abstract = AsyncMock(return_value="" if key == "preprints" else "found")
        fetchers[key] = fetcher
        return fetcher

    with patch("agents.rag_agent.get_fetcher", side_effect=_fake_get_fetcher):
        assert await agent._fetch_anchor_abstract("10.1/x") == "found"
    # preprints (europepmc) then arxiv; flagship shares the europepmc backend.
    assert list(fetchers) == ["preprints", "arxiv"]
