# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""LiteLLM-backed paper scorer.

Replaces keyword matching with LLM reading comprehension for the per-facet
scoring dimensions declared by the domain pack, and generates a concrete
one-sentence hypothesis per paper.

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

from domains import DomainPack, current_domain, term_text
from models.schemas import (
    PaperMetadata,
    RelevanceScore,
    ResearcherProfile,
    ScoredPaper,
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
You are a scientific paper relevance evaluator for a {field} researcher.

Researcher profile:
{profile_lines}

Paper to evaluate:
  Title    : {{title}}
  Abstract : {{abstract}}
{guidance}
CRITICAL OUTPUT RULE: your entire response must be exactly one valid JSON object. \
No markdown fences, no prose, no keys other than those listed below. \
Any deviation makes the response unusable.
{{{{
  "scores": {{{{
{score_lines}
  }}}},
  "hypothesis"     : "<one concrete sentence describing {article} {noun} that combines
                       insights from this paper with the researcher's work, or empty string>"
}}}}
"""


def _profile_lines(domain: DomainPack, profile: ResearcherProfile) -> str:
    rows = [
        (f.label, ", ".join(term_text(v) for v in profile.terms(f.key)) or "any")
        for f in domain.facets
    ]
    rows.append(("Expertise keywords", ", ".join(profile.expertise_keywords) or "none"))
    rows.extend(domain.context_lines(profile.context))
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"  {label:<{width}} : {value}" for label, value in rows)


def _facet_description(facet) -> str:
    return facet.description or f"how well the paper matches the researcher's {facet.label.lower()}"


def _esc(text: str) -> str:
    """Protect pack/profile text from the second ``str.format`` pass."""
    return text.replace("{", "{{").replace("}", "}}")


def build_score_prompt(domain: DomainPack, profile: ResearcherProfile, paper: PaperMetadata) -> str:
    noun = domain.prompts.proposal_noun
    guidance = domain.prompts.scoring_guidance
    template = _SCORE_PROMPT.format(
        field=_esc(domain.prompts.field),
        profile_lines=_esc(_profile_lines(domain, profile)),
        guidance=f"\n{_esc(guidance)}\n" if guidance else "",
        score_lines=_esc(",\n".join(
            f'    "{f.key}": <float 0-1, {_facet_description(f)}>' for f in domain.facets
        )),
        article="an" if noun[:1].lower() in "aeiou" else "a",
        noun=_esc(noun),
    )
    return template.format(title=paper.title, abstract=paper.abstract[:6000])


class LLMPaperScorer:
    """LiteLLM-backed paper scorer with keyword fallback.

    Drop-in async replacement for PaperScorer.  The public interface is
    identical except that score_paper is a coroutine.
    """

    def __init__(self, domain: DomainPack | None = None) -> None:
        self._enabled = (
            os.environ.get("LLM_SCORING_ENABLED", "true").lower() == "true"
        )
        self._domain = domain
        self._fallback = PaperScorer(domain)
        # Cache: paper_id → {"facet_scores": {facet: float}, "hypothesis": str}
        self._cache: dict[str, dict] = {}

        if self._enabled:
            logger.info("LLM scoring enabled")
        else:
            logger.info("LLM scoring disabled — using keyword fallback")

    @property
    def domain(self) -> DomainPack:
        return self._domain or current_domain()

    async def score_paper(
        self,
        paper: PaperMetadata,
        profile: ResearcherProfile,
        existing_papers: list[ScoredPaper] | None = None,
        llm_kwargs: dict | None = None,
    ) -> ScoredPaper:
        """Score a single paper; returns a ScoredPaper."""
        if not self._enabled or not paper.abstract or not llm_kwargs:
            return self._fallback.score_paper(paper, profile, existing_papers)

        llm_dims = await self._llm_dimensions(paper, profile, llm_kwargs)

        recency = self._fallback._score_recency(paper)
        credibility = self._fallback._score_credibility(paper)
        novelty = self._score_novelty(paper, existing_papers or [])

        relevance = RelevanceScore(
            facet_scores=dict(llm_dims["facet_scores"]),
            recency=recency,
            credibility=credibility,
            novelty=novelty,
        )
        relevance.overall = self._fallback.overall(relevance, profile)

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
        llm_kwargs: dict,
    ) -> dict:
        """Return cached LLM scores or invoke the model."""
        if paper.paper_id in self._cache:
            return self._cache[paper.paper_id]

        prompt = build_score_prompt(self.domain, profile, paper)
        result = await self._score_with_retry(prompt, paper.title, llm_kwargs)
        if result is None:
            fb = self._fallback.score_paper(paper, profile)
            result = {
                "facet_scores": fb.relevance.facet_scores,
                "hypothesis": next(iter(fb.suggested_combinations), ""),
            }

        self._cache[paper.paper_id] = result
        return result

    async def _score_with_retry(
        self, prompt: str, title: str, llm_kwargs: dict
    ) -> dict | None:
        """Attempt LLM scoring up to 4 times with backoff; return None on terminal failure."""
        _delays = [2, 8, 30]
        for attempt, delay in enumerate([0] + _delays):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await self._one_llm_call(prompt, llm_kwargs)
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
                # Azure's filter can trigger on innocent scientific text; fall back silently.
                logger.debug("Content policy blocked scoring for '%s' — keyword fallback", title[:60])
                return None
            except Exception as exc:
                logger.warning(
                    "LLM scoring failed for '%s', using keyword fallback: %s", title[:60], exc,
                )
                return None
        return None

    async def _one_llm_call(self, prompt: str, llm_kwargs: dict) -> dict:
        """Make a single LLM call and parse the JSON result."""
        response = await litellm.acompletion(
            **llm_kwargs,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=384,
            response_format={"type": "json_object"},
            temperature=0.0,
            timeout=15,
        )
        raw = response.choices[0].message.content.strip()
        data = parse_json_response(raw)
        scores = data.get("scores") or {}
        return {
            "facet_scores": {
                f.key: float(scores.get(f.key, 0.5)) for f in self.domain.facets
            },
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
