"""APPL Literature Mining & Hypothesis Generation Agent.

An Academy-compatible agent that:
1. Accepts researcher profiles (via @action)
2. Generates dynamic, context-aware search queries
3. Continuously monitors literature sources (via @loop)
4. Scores, ranks, and suggests promising paper combinations
5. Exposes ranked results and suggestions to the dashboard / peer agents

Compatible with: academy-py >= 0.2
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from academy.agent import Agent, action, loop

from models.schemas import (
    AgentState,
    CredibilityLevel,
    PaperMetadata,
    ResearcherProfile,
    ScoredPaper,
    SearchQuery,
    SourceType,
    StressType,
    PhenotypingMethod,
)
from utils.llm_scorer import LLMPaperScorer
from utils.persistence import PaperStore
from utils.query_generator import QueryGenerator
from utils.source_fetchers import get_fetcher, FETCHER_REGISTRY

logger = logging.getLogger(__name__)


class LiteratureMiningAgent(Agent):
    """Stateful Academy agent for APPL literature mining.

    State:
    - Researcher profiles with their priorities
    - Pending and historical search queries
    - Scored paper collection with credibility metadata
    - Per-source scan timestamps

    Actions (invokable by dashboard / peer agents):
    - register_researcher:    Add or update a researcher profile
    - get_researcher:         Retrieve a stored profile
    - trigger_search:         Force an immediate literature scan
    - get_top_papers:         Return ranked papers for a researcher
    - get_combinations:       Return suggested experiment combinations
    - get_agent_status:       Health / stats endpoint

    Loops (autonomous):
    - monitor_sources:        Periodic literature scanning
    """

    def __init__(
        self,
        scan_interval_seconds: int = 86400,
        max_papers_per_query: int = 20,
        db_path: str | None = None,
    ) -> None:
        super().__init__()
        self.state = AgentState()
        self.query_gen = QueryGenerator()
        self.scorer = LLMPaperScorer()
        self.scan_interval = scan_interval_seconds
        self.max_papers_per_query = max_papers_per_query

        # Persistent storage
        _db = db_path or os.environ.get("DB_PATH", str(Path(__file__).parent.parent / "cassiopeia.db"))
        self.store = PaperStore(_db)

        # Restore state from SQLite
        for profile in self.store.load_profiles():
            self.state.researcher_profiles[profile.researcher_id] = profile
            for paper in self.store.load_papers(profile.researcher_id):
                self.state.scored_papers.append(paper)
        self.scorer.load_cache(self.store.load_llm_cache())

        logger.info(
            "LiteratureMiningAgent restored: %d profiles, %d papers",
            len(self.state.researcher_profiles),
            len(self.state.scored_papers),
        )

    @action
    async def register_researcher(
        self,
        researcher_id: str,
        name: str,
        plant_species: list[str] | None = None,
        stress_types: list[str] | None = None,
        phenotyping_methods: list[str] | None = None,
        expertise_keywords: list[str] | None = None,
        priority_novelty: float = 0.5,
        priority_relevance: float = 0.5,
        priority_methodology: float = 0.5,
        priority_reproducibility: float = 0.5,
        available_equipment: list[str] | None = None,
        time_range_months: int = 12,
        source_targets: list[str] | None = None,
    ) -> dict[str, Any]:
        """Register or update a researcher profile.

        Called by the dashboard when a researcher submits their preferences.
        Returns the generated query count for confirmation.
        """
        profile = ResearcherProfile(
            researcher_id=researcher_id,
            name=name,
            plant_species=plant_species or [],
            stress_types=[
                StressType(s) for s in (stress_types or [])
                if s in StressType.__members__.values()
                or s in [e.value for e in StressType]
            ],
            phenotyping_methods=[
                PhenotypingMethod(m) for m in (phenotyping_methods or [])
                if m in PhenotypingMethod.__members__.values()
                or m in [e.value for e in PhenotypingMethod]
            ],
            expertise_keywords=expertise_keywords or [],
            priority_novelty=priority_novelty,
            priority_relevance=priority_relevance,
            priority_methodology=priority_methodology,
            priority_reproducibility=priority_reproducibility,
            available_equipment=available_equipment or [],
            time_range_months=time_range_months,
            source_targets=source_targets or [],
        )

        self.state.researcher_profiles[researcher_id] = profile
        self.store.save_profile(profile)

        queries = self.query_gen.generate_queries(profile)
        self.state.pending_queries.extend(queries)

        logger.info(
            "Registered researcher %s (%s) — %d queries generated",
            researcher_id,
            name,
            len(queries),
        )

        return {
            "status": "registered",
            "researcher_id": researcher_id,
            "queries_generated": len(queries),
            "sample_queries": [q.query_string for q in queries[:3]],
        }

    @action
    async def get_researcher(
        self,
        researcher_id: str,
    ) -> dict[str, Any] | None:
        """Retrieve a researcher's current profile."""
        profile = self.state.researcher_profiles.get(researcher_id)
        if profile is None:
            return None
        return {
            "researcher_id": profile.researcher_id,
            "name": profile.name,
            "plant_species": profile.plant_species,
            "stress_types": [s.value for s in profile.stress_types],
            "phenotyping_methods": [m.value for m in profile.phenotyping_methods],
            "expertise_keywords": profile.expertise_keywords,
            "source_targets": profile.source_targets,
            "time_range_months": profile.time_range_months,
            "priorities": {
                "novelty": profile.priority_novelty,
                "relevance": profile.priority_relevance,
                "methodology": profile.priority_methodology,
                "reproducibility": profile.priority_reproducibility,
            },
        }

    @action
    async def trigger_search(
        self,
        researcher_id: str,
    ) -> dict[str, Any]:
        """Force an immediate literature scan for a researcher.

        Generates fresh queries from the profile and runs them
        against all configured sources.
        """
        profile = self.state.researcher_profiles.get(researcher_id)
        if profile is None:
            return {"error": f"Unknown researcher: {researcher_id}"}

        past = [q.query_string for q in self.state.query_history]
        queries = await self.query_gen.generate_queries_async(profile, past_queries=past)
        papers_found = 0

        async def _fetch(query: SearchQuery) -> tuple[SearchQuery, list[PaperMetadata]]:
            fetcher = get_fetcher(query.source_target)
            return query, await fetcher.fetch(query, max_results=self.max_papers_per_query)

        fetch_results = await asyncio.gather(
            *(_fetch(q) for q in queries),
            return_exceptions=True,
        )

        # Collect only papers not already in the store (same deduplication as background monitor)
        known_dois = self.store.known_dois(researcher_id)
        snapshot = list(self.state.scored_papers)
        pairs = self._collect_new_pairs(researcher_id, fetch_results, known_dois)

        await self._enrich_abstracts(pairs)
        logger.info("Fetched %d papers across %d queries — scoring…", len(pairs), len(queries))

        # Score papers with bounded concurrency so Azure keep-alive connections
        # are reused rather than opening many new sockets simultaneously.
        _sem = asyncio.Semaphore(int(os.environ.get("LLM_CONCURRENCY", "3")))
        _scored_count = 0

        async def _score(q: SearchQuery, p: PaperMetadata) -> ScoredPaper:
            nonlocal _scored_count
            async with _sem:
                scored = await self.scorer.score_paper(p, profile, snapshot)
            scored.source_queries.append(q.query_string)
            _scored_count += 1
            if _scored_count % 10 == 0 or _scored_count == len(pairs):
                logger.info("Scored %d / %d papers", _scored_count, len(pairs))
            return scored

        score_results = await asyncio.gather(
            *(_score(q, p) for q, p in pairs),
            return_exceptions=True,
        )
        for scored in score_results:
            if not isinstance(scored, BaseException):
                self.state.scored_papers.append(scored)
                self.store.save_paper(scored, researcher_id)
                papers_found += 1

        logger.info("Saving LLM cache…")
        self.store.save_llm_cache(self.scorer.export_cache())
        self.state.query_history.extend(queries)
        logger.info("trigger_search complete — %d papers found", papers_found)

        return {
            "status": "completed",
            "queries_executed": len(queries),
            "papers_found": papers_found,
            "total_papers": len(self.state.scored_papers),
        }

    @action
    async def get_top_papers(
        self,
        researcher_id: str,
        limit: int = 20,
        min_credibility: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return top-ranked papers for a researcher.

        Papers are scored using the researcher's priority weights and
        filtered by minimum credibility if specified.
        """
        profile = self.state.researcher_profiles.get(researcher_id)
        if profile is None:
            return []

        # Load only papers scored for this researcher — avoids mixing results from
        # other researcher profiles stored in the same agent instance.
        rescored = self.store.load_papers(researcher_id)

        # Apply credibility filter
        if min_credibility:
            try:
                min_level = CredibilityLevel(min_credibility)
                level_order = {
                    CredibilityLevel.HIGH: 3,
                    CredibilityLevel.MODERATE: 2,
                    CredibilityLevel.PRELIMINARY: 1,
                    CredibilityLevel.CONFLICTING: 0,
                }
                threshold = level_order.get(min_level, 0)
                rescored = [
                    sp
                    for sp in rescored
                    if level_order.get(sp.credibility, 0) >= threshold
                ]
            except ValueError:
                pass

        ranked = self.scorer.rank_papers(rescored)

        return [
            {
                "rank": i + 1,
                "title": sp.paper.title,
                "authors": sp.paper.authors[:3],
                "journal": sp.paper.journal,
                "doi": sp.paper.doi,
                "url": sp.paper.url,
                "published": (
                    sp.paper.published_date.isoformat()
                    if sp.paper.published_date
                    else None
                ),
                "source": sp.paper.source.value,
                "is_open_access": sp.paper.is_open_access,
                "scores": {
                    "overall": round(sp.relevance.overall, 3),
                    "species_match": round(sp.relevance.species_match, 3),
                    "stress_match": round(sp.relevance.stress_match, 3),
                    "method_match": round(sp.relevance.method_match, 3),
                    "recency": round(sp.relevance.recency, 3),
                    "credibility": round(sp.relevance.credibility, 3),
                    "novelty": round(sp.relevance.novelty, 3),
                },
                "credibility_level": sp.credibility.value,
                "credibility_icon": {
                    CredibilityLevel.HIGH: "🟢",
                    CredibilityLevel.MODERATE: "🟡",
                    CredibilityLevel.PRELIMINARY: "🔴",
                    CredibilityLevel.CONFLICTING: "⚠️",
                }.get(sp.credibility, "❓"),
                "suggested_combinations": sp.suggested_combinations,
            }
            for i, sp in enumerate(ranked[:limit])
        ]

    @action
    async def get_combinations(
        self,
        researcher_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return novel experiment combination suggestions.

        Aggregates suggestions across top papers and deduplicates.
        """
        top = await self.get_top_papers(researcher_id, limit=50)
        seen: set[str] = set()
        combos: list[dict[str, Any]] = []

        for paper_info in top:
            for suggestion in paper_info.get("suggested_combinations", []):
                if suggestion not in seen:
                    seen.add(suggestion)
                    combos.append(
                        {
                            "suggestion": suggestion,
                            "source_paper": paper_info["title"],
                            "source_doi": paper_info["doi"],
                            "paper_credibility": paper_info[
                                "credibility_level"
                            ],
                        },
                    )
                    if len(combos) >= limit:
                        return combos

        return combos

    @action
    async def get_agent_status(self) -> dict[str, Any]:
        """Return current agent health and statistics."""
        return {
            "status": "running",
            "researchers_registered": len(self.state.researcher_profiles),
            "total_papers_scored": len(self.state.scored_papers),
            "pending_queries": len(self.state.pending_queries),
            "queries_executed": len(self.state.query_history),
            "last_scan_times": {
                k: v.isoformat()
                for k, v in self.state.last_scan_time.items()
            },
            "sources_available": list(FETCHER_REGISTRY.keys()),
        }

    @loop
    async def monitor_sources(self, shutdown: asyncio.Event) -> None:
        """Continuously monitor literature sources every `scan_interval` seconds."""
        logger.info("Literature monitor started (interval=%ds)", self.scan_interval)

        while not shutdown.is_set():
            for rid, profile in self.state.researcher_profiles.items():
                if shutdown.is_set():
                    return
                await self._scan_researcher(rid, profile)

            logger.info(
                "Monitor cycle complete — %d total papers",
                len(self.state.scored_papers),
            )
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=self.scan_interval)
            except asyncio.TimeoutError:
                pass

    async def _scan_researcher(
        self, rid: str, profile: ResearcherProfile
    ) -> None:
        """Fetch and score new papers for one researcher."""
        past = [q.query_string for q in self.state.query_history]
        queries = await self.query_gen.generate_queries_async(
            profile, max_queries_per_source=3, past_queries=past
        )

        async def _fetch(q: SearchQuery) -> tuple[SearchQuery, list[PaperMetadata]]:
            fetcher = get_fetcher(q.source_target)
            return q, await fetcher.fetch(q, max_results=self.max_papers_per_query)

        fetch_results = await asyncio.gather(
            *(_fetch(q) for q in queries), return_exceptions=True
        )

        known_dois = self.store.known_dois(rid)
        snapshot = list(self.state.scored_papers)
        pairs = self._collect_new_pairs(rid, fetch_results, known_dois)
        await self._enrich_abstracts(pairs)

        _sem = asyncio.Semaphore(int(os.environ.get("LLM_CONCURRENCY", "3")))

        async def _score(q: SearchQuery, p: PaperMetadata) -> ScoredPaper:
            async with _sem:
                scored = await self.scorer.score_paper(p, profile, snapshot)
            scored.source_queries.append(q.query_string)
            return scored

        score_results = await asyncio.gather(
            *(_score(q, p) for q, p in pairs), return_exceptions=True
        )
        for scored in score_results:
            if not isinstance(scored, BaseException):
                self.state.scored_papers.append(scored)
                self.store.save_paper(scored, rid)

        self.store.save_llm_cache(self.scorer.export_cache())
        self.state.last_scan_time[rid] = datetime.now(timezone.utc)

    async def _enrich_abstracts(
        self, pairs: list[tuple[SearchQuery, PaperMetadata]]
    ) -> None:
        """Replace or supply paper text with full content where available.

        All papers are tried in parallel — fetchers that cannot provide full
        text (paywalled journals, unsupported sources) return None immediately
        with no network call.  For arXiv and PubMed Central open-access papers,
        the full text is fetched and replaces the abstract so the scorer has
        richer input.  Papers where full text is unavailable keep their original
        abstract (or remain without one, falling back to keyword scoring).
        """
        if not pairs:
            return

        async def _try_enrich(query: SearchQuery, paper: PaperMetadata) -> None:
            try:
                fetcher = get_fetcher(query.source_target)
                full_text = await fetcher.fetch_full_text(paper.paper_id)
                if full_text:
                    paper.abstract = full_text[:6000]
            except Exception as exc:
                logger.debug(
                    "Full text enrichment failed for %s: %s", paper.paper_id, exc
                )

        await asyncio.gather(
            *(_try_enrich(q, p) for q, p in pairs), return_exceptions=True
        )
        enriched = sum(1 for _, p in pairs if p.abstract)
        logger.info(
            "Full text enrichment: %d / %d papers have text for scoring",
            enriched, len(pairs),
        )

    def _collect_new_pairs(
        self,
        rid: str,
        fetch_results: list,
        known_dois: set[str],
    ) -> list[tuple[SearchQuery, PaperMetadata]]:
        """Extract (query, paper) pairs for papers not already in the store."""
        pairs: list[tuple[SearchQuery, PaperMetadata]] = []
        seen: set[str] = set(known_dois)
        for result in fetch_results:
            if isinstance(result, BaseException):
                logger.error("Monitor fetch error for %s: %s", rid, result)
                continue
            query, papers = result
            for paper in papers:
                doi = paper.doi or ""
                if doi and doi in seen:
                    continue
                pairs.append((query, paper))
                if doi:
                    seen.add(doi)
        return pairs
