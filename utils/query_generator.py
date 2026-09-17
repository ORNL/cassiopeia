# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Dynamic query generation for literature mining.

A query holds one term from each *query facet* of the active domain pack
(facets with role ``subject`` or ``condition``), in declaration order.  The
researcher's facet selections are crossed, and each term is widened into an
OR-group of LLM-provided synonyms.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from itertools import product as cartesian

import litellm

from domains import DomainPack, Facet, current_domain, term_text
from utils.json_utils import parse_json_response
from utils.user_settings import get_llm_config, LLMNotConfiguredError

from models.schemas import ResearcherProfile, SearchQuery

logger = logging.getLogger(__name__)

_AND = " AND "

# ──────────────────────────────────────────────────────────────────────────────
# LLM prompt — synonym expansion only.
#
# The LLM's job is to list synonyms; building the actual query structure is done
# in _build_from_synonyms so it can never hallucinate year ranges, extra terms,
# or malformed query syntax into the search strings.
# ──────────────────────────────────────────────────────────────────────────────

_LLM_SYNONYM_PROMPT = """\
You are a {field} expert. List synonyms and closely related search terms for
the terms below. Terms must appear in scientific paper titles or abstracts.

{term_lines}

Return ONLY valid JSON (no markdown):
{{
  "<facet key>": {{
    "<term>": ["<synonym>", "<synonym>", ...],
    ...
  }},
  ...
}}

Rules:
{rules}
- Do NOT include years or date ranges.{extra_rules}
"""


def _selected(profile: ResearcherProfile, facet: Facet, *, use_default: bool) -> list[str]:
    values = profile.terms(facet.key)
    if values or not use_default or not facet.query_default:
        return values
    return [facet.query_default]


class QueryGenerator:
    """Generates search queries from researcher profiles.

    ``generate_queries`` — synchronous cross-product fallback, used by the
    preview endpoint and registration confirmation.

    ``generate_queries_async`` — LLM-driven synonym expansion + code-built
    OR-group queries, called by trigger_search and the background monitor.
    Falls back to ``generate_queries`` on any error.
    """

    def __init__(self, domain: DomainPack | None = None) -> None:
        self._domain = domain

    @property
    def domain(self) -> DomainPack:
        return self._domain or current_domain()

    # ── public sync API (preview / registration count) ────────────────────────

    def generate_queries(
        self,
        profile: ResearcherProfile,
        *,
        max_queries_per_source: int = 5,
    ) -> list[SearchQuery]:
        """Cross-product fallback — one query per combination of query-facet terms."""
        base_combos = self._build_base_combinations(profile)
        modifiers = self._build_modifiers(profile)
        queries: list[SearchQuery] = []

        for source in self._allowed_sources(profile):
            for combo in base_combos[:max_queries_per_source]:
                queries.append(SearchQuery(
                    query_string=_AND.join(
                        f'"{t}"' if " " in t else t for t in combo
                    ),
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
    ) -> list[SearchQuery]:
        """Synonym-expansion + code-built OR-group queries.

        Step 1: ask the LLM for a synonym map for every expandable query facet.
        Step 2: build OR-group SearchQuery objects deterministically in code.

        The LLM only handles what it does reliably (listing synonyms); all query
        structure — AND/OR logic, quoting, temporal filters — is built in code so
        it can never appear as a stray search term.
        """
        synonyms = await self._expand_synonyms(profile)
        queries = self._build_from_synonyms(profile, synonyms, max_queries_per_source)
        if queries:
            logger.info(
                "Generated %d queries for %s across %s",
                len(queries),
                profile.researcher_id,
                sorted(self._allowed_sources(profile)),
            )
            return queries
        logger.warning(
            "Synonym expansion produced no queries for %s — falling back to cross-product",
            profile.researcher_id,
        )
        return self.generate_queries(profile, max_queries_per_source=max_queries_per_source)

    # ── private helpers ───────────────────────────────────────────────────────

    def _synonym_prompt(self, profile: ResearcherProfile) -> str | None:
        facets = [f for f in self.domain.query_facets if f.synonyms > 0]
        if not facets:
            return None
        term_lines = "\n".join(
            f"{f.label} ({f.key}): "
            + (", ".join(_selected(profile, f, use_default=True)) or "any")
            for f in facets
        )
        rules = "\n".join(
            f"- {f.key}: {f.synonym_guidance or f'up to {f.synonyms} synonyms.'}"
            for f in facets
        )
        extra = self.domain.prompts.synonym_rules
        return _LLM_SYNONYM_PROMPT.format(
            field=self.domain.prompts.field,
            term_lines=term_lines,
            rules=rules,
            extra_rules=f"\n- {extra}" if extra else "",
        )

    async def _expand_synonyms(self, profile: ResearcherProfile) -> dict:
        """Call the LLM to get a {facet: {term: [synonyms]}} map. Empty on failure."""
        prompt = self._synonym_prompt(profile)
        if prompt is None:
            return {}
        try:
            llm_kwargs = get_llm_config(profile.researcher_id).for_scoring()
        except LLMNotConfiguredError:
            return {}
        try:
            response = await litellm.acompletion(
                **llm_kwargs,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content.strip()
            data = parse_json_response(raw)
            logger.debug(
                "Synonym expansion for %s: %s",
                profile.researcher_id,
                json.dumps(data, ensure_ascii=False)[:300],
            )
            return data
        except Exception as exc:
            logger.warning(
                "Synonym expansion failed for %s: %s — using bare terms",
                profile.researcher_id,
                exc,
            )
            return {}

    def _build_from_synonyms(
        self,
        profile: ResearcherProfile,
        synonyms: dict,
        max_per_source: int,
    ) -> list[SearchQuery]:
        """Build OR-group SearchQuery objects from the LLM synonym map.

        For each source and each combination of query-facet terms we build one
        OR-group per facet: ``[term, *synonyms]``.  The researcher's keywords
        widen the last group.  Subject facets fall back to their default term
        when nothing is selected; other facets must be selected — an empty one
        yields no queries, and the caller falls back to the cross-product.
        """
        facets = self.domain.query_facets
        modifiers = self._build_modifiers(profile)
        kws = [k for k in (profile.expertise_keywords or []) if k][:3]
        selections = [
            _selected(profile, f, use_default=f.role == "subject") for f in facets
        ]
        queries: list[SearchQuery] = []

        for source in self._allowed_sources(profile):
            override = self.domain.sources[source].synonym_override
            for count, combo in enumerate(cartesian(*selections)):
                if count >= max_per_source:
                    break
                last = len(facets) - 1
                term_groups = [
                    self._term_group(facet, value, synonyms, kws if i == last else [], override)
                    for i, (facet, value) in enumerate(zip(facets, combo))
                ]
                flat = [grp[0] for grp in term_groups]
                queries.append(SearchQuery(
                    query_string=_AND.join(
                        f'"{t}"' if " " in t else t for t in flat
                    ),
                    source_target=source,
                    researcher_id=profile.researcher_id,
                    base_terms=flat,
                    term_groups=term_groups,
                    contextual_modifiers=modifiers,
                ))

        return queries

    @staticmethod
    def _term_group(
        facet: Facet,
        value: str,
        synonyms: dict,
        kws: list[str],
        override: tuple[str, ...],
    ) -> list[str]:
        """OR-group for one facet term: the term, its synonyms, then keywords."""
        head = term_text(value)
        syns = [s for s in synonyms.get(facet.key, {}).get(value, []) if s][:facet.synonyms]
        if override and facet.role != "subject":
            # This source covers different ground: keywords or the source's own
            # terms replace the synonyms.
            extra = [t for t in override if t not in syns]
            return ([head] + (kws or extra)[:4])[:facet.group_size]
        return ([head] + syns + [k for k in kws if k not in syns])[:facet.group_size]

    def _allowed_sources(self, profile: ResearcherProfile) -> set[str]:
        known = self.domain.sources
        allowed = {s for s in (profile.source_targets or []) if s in known}
        return allowed or set(known)

    def _build_base_combinations(self, profile: ResearcherProfile) -> list[list[str]]:
        selections = [
            [term_text(v) for v in _selected(profile, f, use_default=True)]
            for f in self.domain.query_facets
        ]
        combos = [list(c) for c in cartesian(*selections)]
        combos.sort(key=lambda c: len(" ".join(c)), reverse=True)
        return combos

    def _build_modifiers(self, profile: ResearcherProfile) -> dict[str, str]:
        now = datetime.now(timezone.utc)
        year_start = now.year - (profile.time_range_months // 12)
        return {"temporal": f"{year_start}..{now.year}"}
