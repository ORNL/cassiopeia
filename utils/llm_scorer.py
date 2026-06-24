# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""LiteLLM-backed paper scorer.

Replaces keyword matching with LLM reading comprehension for the three
text-dependent scoring dimensions (species_match, stress_match, method_match)
and generates concrete experimental hypotheses.

Falls back to the keyword-based PaperScorer when:
  - LLM_SCORING_ENABLED=false
  - The paper has no abstract
  - The LLM call raises an exception

Results are cached by paper_id so repeated scoring calls (e.g. from
get_top_papers) do not re-invoke the LLM.

Provider and model are controlled by the LLM_SCORING_MODEL environment
variable using LiteLLM's "<provider>/<model>" convention, e.g.:
  anthropic/claude-haiku-4-5-20251001   (default)
  gpt-4o-mini
  azure/my-deployment
  ollama/llama3.2
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from difflib import SequenceMatcher

import litellm

from models.schemas import (
    CredibilityLevel,
    PaperMetadata,
    RelevanceScore,
    ResearcherProfile,
    ScoredPaper,
    SourceType,
)
from utils.paper_scorer import PaperScorer
from utils.json_utils import parse_json_response

logger = logging.getLogger(__name__)

# Silence LiteLLM's own verbose logging
litellm.suppress_debug_info = True
litellm.set_verbose = False
# Drop unsupported params (e.g. response_format on gpt-4 base) instead of raising.
litellm.drop_params = True

_SCORE_PROMPT = """\
You are a scientific paper relevance evaluator for a plant biology researcher.

Researcher profile:
  Species of interest  : {species}
  Stress types         : {stresses}
  Phenotyping methods  : {methods}
  Expertise keywords   : {keywords}
  Available instruments: {equipment}

Paper to evaluate:
  Title    : {title}
  Abstract : {abstract}

Score "method_match" based on how well the paper's experimental methods can be \
reproduced or extended using the researcher's available instruments. A paper \
requiring equipment the researcher does not have should score lower.

CRITICAL OUTPUT RULE: your entire response must be exactly one valid JSON object. \
No markdown fences, no prose, no keys other than those listed below. \
Any deviation makes the response unusable.
{{
  "species_match"  : <float 0-1, how well this paper's organisms match the researcher's species>,
  "stress_match"   : <float 0-1, how well the paper's stresses match>,
  "method_match"   : <float 0-1, how well the paper's methods match the available instruments>,
  "hypothesis"     : "<one concrete sentence describing an experiment that combines
                       insights from this paper with the researcher's work, or empty string>"
}}
"""


class LLMPaperScorer:
    """LiteLLM-backed paper scorer with keyword fallback.

    Drop-in async replacement for PaperScorer.  The public interface is
    identical except that score_paper is a coroutine.
    """

    def __init__(self) -> None:
        self._model = os.environ["LLM_SCORING_MODEL"]
        self._enabled = (
            os.environ.get("LLM_SCORING_ENABLED", "true").lower() == "true"
        )
        self._fallback = PaperScorer()
        # Cache: paper_id → {"species_match", "stress_match", "method_match", "hypothesis"}
        self._cache: dict[str, dict[str, float | str]] = {}

        if self._enabled:
            logger.info("LLM scoring enabled — model: %s", self._model)
        else:
            logger.info("LLM scoring disabled — using keyword fallback")

    async def score_paper(
        self,
        paper: PaperMetadata,
        profile: ResearcherProfile,
        existing_papers: list[ScoredPaper] | None = None,
    ) -> ScoredPaper:
        """Score a single paper; returns a ScoredPaper."""
        if not self._enabled or not paper.abstract:
            return self._fallback.score_paper(paper, profile, existing_papers)

        llm_dims = await self._llm_dimensions(paper, profile)

        recency = self._fallback._score_recency(paper)
        credibility = self._fallback._score_credibility(paper)
        novelty = self._score_novelty(paper, existing_papers or [])

        relevance = RelevanceScore(
            species_match=llm_dims["species_match"],
            stress_match=llm_dims["stress_match"],
            method_match=llm_dims["method_match"],
            recency=recency,
            credibility=credibility,
            novelty=novelty,
        )
        relevance.overall = relevance.weighted_score(profile)

        hypothesis = llm_dims.get("hypothesis", "")
        return ScoredPaper(
            paper=paper,
            relevance=relevance,
            credibility=self._fallback._assess_credibility(paper),
            suggested_combinations=[hypothesis] if hypothesis else [],
        )

    def rank_papers(self, papers: list[ScoredPaper]) -> list[ScoredPaper]:
        return self._fallback.rank_papers(papers)

    def load_cache(self, cache: dict[str, dict]) -> None:
        """Restore a previously exported cache (e.g., from SQLite on startup)."""
        self._cache.update(cache)

    def export_cache(self) -> dict[str, dict]:
        """Return the in-memory cache for persistence."""
        return dict(self._cache)

    # ── Private helpers ────────────────────────────────────────────

    async def _llm_dimensions(
        self,
        paper: PaperMetadata,
        profile: ResearcherProfile,
    ) -> dict[str, float | str]:
        """Return cached LLM scores or invoke the model."""
        if paper.paper_id in self._cache:
            return self._cache[paper.paper_id]

        prompt = _SCORE_PROMPT.format(
            species=", ".join(profile.plant_species) or "any",
            stresses=", ".join(s.value.replace("_", " ") for s in profile.stress_types) or "any",
            methods=", ".join(m.value.replace("_", " ") for m in profile.phenotyping_methods) or "any",
            keywords=", ".join(profile.expertise_keywords) or "none",
            equipment=", ".join(profile.available_equipment) or "standard laboratory equipment",
            title=paper.title,
            abstract=paper.abstract[:6000],
        )

        result = await self._score_with_retry(prompt, paper.title)
        if result is None:
            fb = self._fallback.score_paper(paper, profile)
            result = {
                "species_match": fb.relevance.species_match,
                "stress_match": fb.relevance.stress_match,
                "method_match": fb.relevance.method_match,
                "hypothesis": next(iter(fb.suggested_combinations), ""),
            }

        self._cache[paper.paper_id] = result
        return result

    async def _score_with_retry(
        self, prompt: str, title: str
    ) -> dict[str, float | str] | None:
        """Attempt LLM scoring up to 4 times with backoff; return None on terminal failure."""
        _delays = [2, 8, 30]
        for attempt, delay in enumerate([0] + _delays):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await self._one_llm_call(prompt)
            except litellm.RateLimitError:
                if attempt < len(_delays):
                    logger.debug(
                        "Rate-limited scoring '%s' (attempt %d) — retrying in %ds",
                        title[:60], attempt + 1, _delays[attempt],
                    )
                else:
                    logger.warning("LLM scoring rate-limited for '%s' — keyword fallback", title[:60])
                    return None
            except litellm.ContentPolicyViolationError:
                # Azure's filter triggers on innocent plant biology text; fall back silently.
                logger.debug("Content policy blocked scoring for '%s' — keyword fallback", title[:60])
                return None
            except Exception as exc:
                logger.warning(
                    "LLM scoring failed for '%s', using keyword fallback: %s", title[:60], exc,
                )
                return None
        return None

    async def _one_llm_call(self, prompt: str) -> dict[str, float | str]:
        """Make a single LLM call and parse the JSON result."""
        response = await litellm.acompletion(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=384,
            response_format={"type": "json_object"},
            temperature=0.0,
            timeout=15,
        )
        raw = response.choices[0].message.content.strip()
        data = parse_json_response(raw)
        return {
            "species_match": float(data.get("species_match", 0.5)),
            "stress_match": float(data.get("stress_match", 0.5)),
            "method_match": float(data.get("method_match", 0.5)),
            "hypothesis": str(data.get("hypothesis", "")),
        }

    @staticmethod
    def _score_novelty(
        paper: PaperMetadata,
        existing: list[ScoredPaper],
    ) -> float:
        if not existing:
            return 0.8
        max_sim = max(
            SequenceMatcher(None, paper.title.lower(), sp.paper.title.lower()).ratio()
            for sp in existing
        )
        return max(1.0 - max_sim, 0.0)
