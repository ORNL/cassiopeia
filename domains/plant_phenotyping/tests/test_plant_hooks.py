# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""APPL-specific behaviour: scoring wording, instrument context, feasibility."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import litellm
import pytest

from models.schemas import CredibilityLevel, PaperMetadata, RelevanceScore, ResearcherProfile
from utils.llm_critic import build_critic_prompt
from utils.llm_scorer import build_score_prompt
from utils.paper_scorer import PaperScorer

INSTRUMENTS = {"instruments": ["VNIR hyperspectral imaging", "thermal imaging"]}


def _profile(**kw) -> ResearcherProfile:
    return ResearcherProfile(
        researcher_id="r1",
        name="R",
        facets={"species": ["poplar"], "stress": ["drought"], "method": ["thermal_imaging"]},
        **kw,
    )


def _paper(**kw) -> PaperMetadata:
    kw.setdefault("source", "pubmed")
    return PaperMetadata(paper_id="p1", title=kw.pop("title", ""), authors=[], abstract="", **kw)


# ── context ──────────────────────────────────────────────────────────────────

def test_facility_equipment_env_fills_instrument_context(plant_pack, monkeypatch):
    monkeypatch.setenv("FACILITY_EQUIPMENT", "VNIR hyperspectral imaging, thermal imaging")
    assert plant_pack.load_context() == INSTRUMENTS


def test_feasibility_only_applies_with_instruments(plant_pack):
    assert [e.key for e in plant_pack.active_evaluators(INSTRUMENTS)] == ["feasibility"]
    assert plant_pack.active_evaluators({"instruments": []}) == []


# ── prompts ──────────────────────────────────────────────────────────────────

def test_score_prompt_carries_instruments_and_method_guidance(plant_pack):
    prompt = build_score_prompt(plant_pack, _profile(context=INSTRUMENTS), _paper(title="T"))
    assert "Available instruments : VNIR hyperspectral imaging, thermal imaging" in prompt
    assert 'Score "method" based on how well' in prompt
    assert '"method": <float 0-1, how well the paper\'s methods match the available instruments>' in prompt


def test_score_prompt_defaults_to_standard_equipment(plant_pack):
    prompt = build_score_prompt(plant_pack, _profile(), _paper(title="T"))
    assert "Available instruments : standard laboratory equipment" in prompt


def test_critic_prompt_asks_for_feasibility_concerns(plant_pack):
    prompt = build_critic_prompt({"suggestion": "s"}, [], INSTRUMENTS, plant_pack)
    assert "proposed plant phenotyping experiment" in prompt
    assert "Available instruments:\n  - VNIR hyperspectral imaging" in prompt
    assert '"feasibility_concerns": [' in prompt


# ── scoring ──────────────────────────────────────────────────────────────────

def test_weighted_score_matches_the_pre_pack_formula(plant_pack):
    profile = _profile(priority_novelty=0.7, priority_relevance=0.8,
                       priority_methodology=0.5, priority_reproducibility=0.6)
    r = RelevanceScore(
        facet_scores={"species": 0.9, "stress": 0.3, "method": 0.4},
        novelty=0.2, credibility=0.6,
    )
    expected = (0.8 * (0.9 + 0.3) / 2 + 0.7 * 0.2 + 0.5 * 0.4 + 0.6 * 0.6) / (0.8 + 0.7 + 0.5 + 0.6)
    assert PaperScorer().overall(r, profile) == pytest.approx(expected)


def test_hint_sentences_are_unchanged():
    paper = _paper(title="Salinity and hyperspectral imaging of poplar")
    profile = ResearcherProfile(
        researcher_id="r", name="R",
        facets={"stress": ["drought"], "method": ["thermal_imaging"]},
    )
    assert PaperScorer()._suggest_combinations(paper, profile) == [
        "Paper explores salinity stress — consider combining with your drought focus",
        "Paper uses hyperspectral — could complement your thermal imaging approach",
    ]


def test_biorxiv_is_preliminary_and_phytologist_is_high():
    scorer = PaperScorer()
    assert scorer._assess_credibility(_paper(source="biorxiv", citation_count=99)) == CredibilityLevel.PRELIMINARY
    assert scorer._assess_credibility(_paper(source="new_phytologist", citation_count=6)) == CredibilityLevel.HIGH


def test_annotations_use_species_and_stress_aliases(plant_pack):
    tags = plant_pack.annotate("Populus under water deficit and NaCl")
    assert tags == {"species": ["poplar"], "stress": ["drought", "salinity"]}


# ── feasibility evaluator ────────────────────────────────────────────────────

def _evaluator(plant_pack):
    return plant_pack.evaluators[0]


@pytest.mark.asyncio
async def test_feasibility_evaluator_returns_llm_assessment(plant_pack):
    payload = {"feasible": "partial", "confidence": 0.6, "missing_equipment": ["LiDAR"],
               "adaptation": "use RGB", "note": "Mostly doable."}
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps(payload)
    mock = AsyncMock(return_value=resp)
    with patch("litellm.acompletion", new=mock):
        result = await _evaluator(plant_pack).evaluate(
            {"suggestion": "Scan canopies"}, INSTRUMENTS, {"model": "m"},
        )
    assert result == payload
    prompt = mock.call_args.kwargs["messages"][0]["content"]
    assert "  - VNIR hyperspectral imaging\n  - thermal imaging" in prompt
    assert "Scan canopies" in prompt


@pytest.mark.asyncio
async def test_feasibility_evaluator_degrades_on_llm_error(plant_pack):
    error = litellm.APIError(status_code=500, message="down", llm_provider="x", model="m")
    with patch("litellm.acompletion", new=AsyncMock(side_effect=error)):
        result = await _evaluator(plant_pack).evaluate({"suggestion": "s"}, INSTRUMENTS, {"model": "m"})
    assert result["feasible"] is None
    assert result["note"] == "Assessment unavailable."
