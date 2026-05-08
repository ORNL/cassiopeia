# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Dynamic query generation for literature mining."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from itertools import product as cartesian

import litellm

from models.schemas import (
    ResearcherProfile,
    SearchQuery,
    SourceType,
)

logger = logging.getLogger(__name__)

_AND = " AND "

# ──────────────────────────────────────────────────────────────────────────────
# LLM prompt
# ──────────────────────────────────────────────────────────────────────────────

_LLM_QUERY_PROMPT = """\
You are a plant biology literature search expert.

Generate diverse, semantically rich search queries for the following researcher profile.
Each query is a SHORT LIST OF TERMS (2–4 words/phrases) that will be AND-joined and sent
to a bibliographic database. Use precise scientific terminology, synonyms, and related
concepts — do NOT just repeat species+stress verbatim.

Researcher profile:
  Plant species : {species}
  Stress types  : {stresses}
  Keywords      : {keywords}
  Time range    : last {months} months

Target sources (use exact values): {sources}

{past_block}\
Rules:
- Generate at most {n_per_source} queries per source.
- Each query must be meaningfully different from the others (no duplicates, no near-duplicates).
- Include biologically relevant synonyms (e.g. "water deficit" for drought, "osmotic stress",
  "ABA signaling", "stomatal closure", "reactive oxygen species", etc.).
- Prefer specific mechanistic or phenotypic terms that appear in paper titles/abstracts.
- Keep each terms list to 2–4 entries; the fetcher AND-joins them.

Return ONLY valid JSON, no markdown:
{{
  "queries": [
    {{"source": "<source_value>", "terms": ["<term1>", "<term2>", ...]}},
    ...
  ]
}}
"""

_PAST_QUERIES_BLOCK = """\
Previously used queries (do NOT repeat these verbatim — use synonyms, related mechanisms,
or different phenotypic angles to cover new ground):
{lines}

"""


class QueryGenerator:
    """Generates search queries from researcher profiles.

    ``generate_queries`` — synchronous cross-product fallback, used by the
    preview endpoint and registration confirmation.

    ``generate_queries_async`` — LLM-driven, called by trigger_search and the
    background monitor.  Falls back to ``generate_queries`` on any error.
    """

    OPEN_ACCESS_SOURCES = {
        SourceType.BIORXIV,
        SourceType.PLOS_ONE,
        SourceType.FRONTIERS,
        SourceType.ARXIV,
    }

    PAYWALL_SOURCES = {
        SourceType.PUBMED,
        SourceType.NATURE_COMMS,
        SourceType.NEW_PHYTOLOGIST,
        SourceType.PLANT_PHYSIOLOGY,
    }

    # ── public sync API (preview / registration count) ────────────────────────

    def generate_queries(
        self,
        profile: ResearcherProfile,
        *,
        max_queries_per_source: int = 5,
    ) -> list[SearchQuery]:
        """Cross-product fallback — species × stress, one query per combination."""
        base_combos = self._build_base_combinations(profile)
        modifiers = self._build_modifiers(profile)
        queries: list[SearchQuery] = []

        for source in self._allowed_sources(profile):
            for combo in base_combos[:max_queries_per_source]:
                query_str = self._assemble_query(combo, profile, source)
                queries.append(SearchQuery(
                    query_string=query_str,
                    source_target=source,
                    researcher_id=profile.researcher_id,
                    base_terms=combo,
                    contextual_modifiers=modifiers,
                ))
        return queries

    # ── public async API (trigger_search / monitor) ───────────────────────────

    async def generate_queries_async(
        self,
        profile: ResearcherProfile,
        *,
        max_queries_per_source: int = 3,
        past_queries: list[str] | None = None,
    ) -> list[SearchQuery]:
        """LLM-driven query generation with cross-product fallback."""
        model = os.environ["LLM_SCORING_MODEL"]
        allowed = self._allowed_sources(profile)
        source_values = [s.value for s in allowed]

        if past_queries:
            lines = "\n".join(f"  - {q}" for q in past_queries[-30:])
            past_block = _PAST_QUERIES_BLOCK.format(lines=lines)
        else:
            past_block = ""

        prompt = _LLM_QUERY_PROMPT.format(
            species=", ".join(profile.plant_species) or "plant",
            stresses=", ".join(s.value.replace("_", " ") for s in profile.stress_types)
                     or "stress",
            keywords=", ".join(profile.expertise_keywords[:5]) or "none",
            months=profile.time_range_months,
            sources=", ".join(source_values),
            n_per_source=max_queries_per_source,
            past_block=past_block,
        )

        try:
            response = await litellm.acompletion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800,
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()
            data = json.loads(raw)
            queries = self._parse_llm_queries(
                data.get("queries", []), profile, allowed, max_queries_per_source
            )
            if queries:
                logger.info(
                    "LLM generated %d queries for %s across %s",
                    len(queries), profile.researcher_id, source_values,
                )
                return queries
            logger.warning("LLM returned no valid queries; falling back to cross-product")
        except Exception as exc:
            logger.warning(
                "LLM query generation failed for %s, falling back: %s",
                profile.researcher_id, exc,
            )

        return self.generate_queries(profile, max_queries_per_source=max_queries_per_source)

    # ── private helpers ───────────────────────────────────────────────────────

    def _allowed_sources(self, profile: ResearcherProfile) -> set[SourceType]:
        allowed = {SourceType(s) for s in (profile.source_targets or [])}
        return allowed or {s for s in SourceType if s != SourceType.OTHER}

    def _parse_llm_queries(
        self,
        raw: list[dict],
        profile: ResearcherProfile,
        allowed: set[SourceType],
        max_per_source: int,
    ) -> list[SearchQuery]:
        modifiers = self._build_modifiers(profile)
        per_source: dict[SourceType, int] = {}
        queries: list[SearchQuery] = []
        seen_terms: set[str] = set()

        for item in raw:
            src_str = item.get("source", "")
            terms = [t.strip() for t in (item.get("terms") or []) if t.strip()]
            if not terms:
                continue
            try:
                source = SourceType(src_str)
            except ValueError:
                continue
            if source not in allowed:
                continue
            if per_source.get(source, 0) >= max_per_source:
                continue
            key = "|".join(terms)
            if key in seen_terms:
                continue
            seen_terms.add(key)
            per_source[source] = per_source.get(source, 0) + 1
            query_str = self._assemble_query(terms, profile, source)
            queries.append(SearchQuery(
                query_string=query_str,
                source_target=source,
                researcher_id=profile.researcher_id,
                base_terms=terms,
                contextual_modifiers=modifiers,
            ))
        return queries

    def _build_base_combinations(self, profile: ResearcherProfile) -> list[list[str]]:
        species = profile.plant_species or ["plant"]
        stresses = [s.value.replace("_", " ") for s in profile.stress_types] or ["stress"]
        combos = [list(c) for c in cartesian(species, stresses)]
        combos.sort(key=lambda c: len(" ".join(c)), reverse=True)
        return combos

    def _build_modifiers(self, profile: ResearcherProfile) -> dict[str, str]:
        now = datetime.now(timezone.utc)
        year_start = now.year - (profile.time_range_months // 12)
        modifiers: dict[str, str] = {"temporal": f"{year_start}..{now.year}"}
        if profile.expertise_keywords:
            modifiers["expertise"] = _AND.join(profile.expertise_keywords[:3])
        return modifiers

    def _assemble_query(
        self,
        base_terms: list[str],
        profile: ResearcherProfile,
        source: SourceType,
    ) -> str:
        parts = [f'"{t}"' if " " in t else t for t in base_terms]
        now = datetime.now(timezone.utc)
        year_start = now.year - (profile.time_range_months // 12)
        temporal = f"{year_start}..{now.year}"

        if source in self.PAYWALL_SOURCES:
            return _AND.join(parts) + _AND + temporal

        query = _AND.join(parts) + _AND + temporal
        if profile.expertise_keywords:
            kw = _AND.join(profile.expertise_keywords[:2])
            query += f" AND ({kw})"
        return query
