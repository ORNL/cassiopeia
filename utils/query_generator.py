# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Dynamic query generation for literature mining."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from itertools import product as cartesian

import litellm

from utils.json_utils import parse_json_response
from utils.mesh_expander import expand_to_mesh, _NCBI_DELAY
from utils.source_fetchers import SOURCE_REGISTRY

from models.schemas import (
    ResearcherProfile,
    SearchQuery,
    SourceType,
)

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
You are a plant biology expert. List synonyms and closely related search terms for
these species and stresses. Terms must appear in scientific paper titles or abstracts.

Species: {species}
Stress types: {stresses}

Return ONLY valid JSON (no markdown):
{{
  "species": {{
    "<name>": ["<scientific_name>", "<genus>"],
    ...
  }},
  "stresses": {{
    "<stress_value>": ["<term1>", "<term2>", "<term3>", "<term4>"],
    ...
  }}
}}

Rules:
- Species: 2-3 synonyms (scientific name, genus, common variants).
- Stresses: 4-6 synonyms — mechanisms, phenotypes, specific ions.
  heavy_metal  → cadmium, zinc, nickel, lead, copper, metal stress, phytoremediation
  drought      → water deficit, water stress, osmotic stress, ABA, stomatal conductance
  salinity     → salt stress, NaCl, osmotic stress, ion toxicity
  temperature  → heat stress, cold stress, thermotolerance, chilling injury
  nutrient     → nitrogen, phosphorus, nutrient deficiency, fertilization
- Do NOT include years, date ranges, or non-biological terms.
"""

# Bioinformatics framing injected into the stress group for arXiv queries.
# arXiv covers computational/quantitative biology; plant physiology terms return 0.
_ARXIV_BIO_TERMS = ["transcriptome", "RNA-seq", "gene expression", "GWAS", "genomics"]


class QueryGenerator:
    """Generates search queries from researcher profiles.

    ``generate_queries`` — synchronous cross-product fallback, used by the
    preview endpoint and registration confirmation.

    ``generate_queries_async`` — LLM-driven synonym expansion + code-built
    OR-group queries, called by trigger_search and the background monitor.
    Falls back to ``generate_queries`` on any error.
    """

    def __init__(self, store=None) -> None:
        self._store = store

    OPEN_ACCESS_SOURCES = frozenset(
        src for src, info in SOURCE_REGISTRY.items() if info.access == "open"
    )
    PAYWALL_SOURCES = frozenset(
        src for src, info in SOURCE_REGISTRY.items() if info.access == "paywall"
    )

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

        Step 1: ask the LLM for a synonym map (species synonyms + stress synonyms).
        Step 2: build OR-group SearchQuery objects deterministically in code.

        The LLM only handles what it does reliably (listing synonyms); all query
        structure — AND/OR logic, quoting, temporal filters — is built in code so
        it can never appear as a stray search term.
        """
        synonyms = await self._expand_synonyms(profile)
        queries = self._build_from_synonyms(profile, synonyms, max_queries_per_source)
        if queries:
            await self._enrich_with_mesh(queries, profile)
            allowed = self._allowed_sources(profile)
            logger.info(
                "Generated %d queries for %s across %s",
                len(queries),
                profile.researcher_id,
                [s.value for s in allowed],
            )
            return queries
        logger.warning(
            "Synonym expansion produced no queries for %s — falling back to cross-product",
            profile.researcher_id,
        )
        return self.generate_queries(profile, max_queries_per_source=max_queries_per_source)

    # ── private helpers ───────────────────────────────────────────────────────

    async def _expand_synonyms(self, profile: ResearcherProfile) -> dict:
        """Call the LLM to get a synonym map. Returns empty dicts on failure."""
        model = os.environ["LLM_SCORING_MODEL"]
        prompt = _LLM_SYNONYM_PROMPT.format(
            species=", ".join(profile.plant_species) or "plant",
            stresses=", ".join(s.value for s in profile.stress_types) or "stress",
        )
        try:
            response = await litellm.acompletion(
                model=model,
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

        For each (source, species, stress) triple we construct:
          term_groups = [
            [species, *species_synonyms],       ← OR-joined by the fetcher
            [stress_str, *stress_synonyms],     ← OR-joined by the fetcher
          ]
        arXiv gets bioinformatics synonyms in group 2 instead of physiology terms.
        """
        allowed = self._allowed_sources(profile)
        modifiers = self._build_modifiers(profile)
        sp_syns: dict = synonyms.get("species", {})
        st_syns: dict = synonyms.get("stresses", {})
        kws = [k for k in (profile.expertise_keywords or []) if k][:3]
        queries: list[SearchQuery] = []

        for source in allowed:
            count = 0
            for species, stress in cartesian(
                profile.plant_species or ["plant"],
                profile.stress_types or [],
            ):
                if count >= max_per_source:
                    break
                term_groups = self._term_groups(source, species, stress, sp_syns, st_syns, kws)
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
                count += 1

        return queries

    def _term_groups(
        self,
        source: SourceType,
        species: str,
        stress,
        sp_syns: dict,
        st_syns: dict,
        kws: list[str],
    ) -> list[list[str]]:
        """Build the two OR-groups for one (source, species, stress) triple."""
        sp_group = [species] + [s for s in sp_syns.get(species, []) if s][:2]
        st_str = stress.value.replace("_", " ")
        extra = [s for s in st_syns.get(stress.value, []) if s][:4]
        if source == SourceType.ARXIV:
            # arXiv: use profile keywords when available, otherwise bioinformatics terms
            bio = [t for t in _ARXIV_BIO_TERMS if t not in extra]
            st_group = [st_str] + (kws or bio)[:4]
        else:
            # Stress synonyms first, then profile keywords to widen the OR group
            st_group = [st_str] + extra + [k for k in kws if k not in extra]
        return [sp_group[:3], st_group[:5]]

    async def _enrich_with_mesh(
        self,
        queries: list[SearchQuery],
        profile: ResearcherProfile,
    ) -> None:
        """Expand profile terms to MeSH headings and attach to EPMC queries.

        Skips enrichment when no store is available.  Terms are expanded
        sequentially with a small delay to respect the NCBI 3-req/s limit.
        arXiv-targeted queries receive no MeSH terms (arXiv does not use MeSH).
        """
        if self._store is None:
            return

        terms = [*{
            *profile.plant_species,
            *(s.value.replace("_", " ") for s in profile.stress_types),
            *profile.expertise_keywords,
        }]

        mesh: list[str] = []
        for term in terms:
            expanded = await expand_to_mesh(term, self._store)
            mesh.extend(expanded)
            await asyncio.sleep(_NCBI_DELAY)

        # Deduplicate preserving order
        seen: set[str] = set()
        mesh_terms = [h for h in mesh if not (h in seen or seen.add(h))]  # type: ignore[func-returns-value]

        for q in queries:
            if q.source_target != SourceType.ARXIV:
                q.mesh_terms = mesh_terms

        logger.debug(
            "MeSH enrichment for %s: %d headings across %d terms",
            profile.researcher_id, len(mesh_terms), len(terms),
        )

    def _allowed_sources(self, profile: ResearcherProfile) -> set[SourceType]:
        allowed = {SourceType(s) for s in (profile.source_targets or [])}
        return allowed or {s for s in SourceType if s != SourceType.OTHER}

    def _build_base_combinations(self, profile: ResearcherProfile) -> list[list[str]]:
        species = profile.plant_species or ["plant"]
        stresses = [s.value.replace("_", " ") for s in profile.stress_types] or ["stress"]
        combos = [list(c) for c in cartesian(species, stresses)]
        combos.sort(key=lambda c: len(" ".join(c)), reverse=True)
        return combos

    def _build_modifiers(self, profile: ResearcherProfile) -> dict[str, str]:
        now = datetime.now(timezone.utc)
        year_start = now.year - (profile.time_range_months // 12)
        return {"temporal": f"{year_start}..{now.year}"}
