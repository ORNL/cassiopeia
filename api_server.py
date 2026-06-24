# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""FastAPI bridge between the APPL dashboard and the Academy agents.

Run with:
    uvicorn api_server:app --reload --port 8000
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(Path(_PROJECT_ROOT) / ".env")

import asyncio
import contextvars
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Awaitable, TypeVar

T = TypeVar("T")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from academy.exchange import LocalExchangeFactory
from academy.logging import init_logging
from academy.manager import Manager

from agents.literature_mining_agent import LiteratureMiningAgent
from agents.rag_agent import RAGAgent
from models.schemas import ResearcherProfile, StressType
from utils.json_utils import parse_json_response, strip_json_fence
from utils.persistence import PaperStore
from utils.query_generator import QueryGenerator

init_logging(logging.INFO)
logger = logging.getLogger(__name__)

# Silence noisy third-party loggers
for _noisy in ("LiteLLM", "litellm", "sentence_transformers", "huggingface_hub",
               "huggingface_hub.utils._http", "chromadb", "httpx", "httpcore",
               "uvicorn.access"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

_mining_handle = None
_rag_handle = None
_agent_ctx: contextvars.Context | None = None
_paper_store: PaperStore | None = None

# Per-researcher progress state — updated synchronously by the search handler.
# Lightweight in-memory store; resets on server restart.
_search_progress: dict[str, dict] = {}

# Per-researcher contradiction results — populated by the background detection task.
_contradictions_store: dict[str, list] = {}


def _set_progress(
    researcher_id: str,
    stage: str,
    detail: str,
    pct: int,
    *,
    done: bool = False,
    error: str | None = None,
) -> None:
    _search_progress[researcher_id] = {
        "stage": stage,
        "detail": detail,
        "pct": pct,
        "done": done,
        "error": error,
        "ts": time.time(),
    }


def _call(coro: Awaitable[T]) -> asyncio.Future[T]:
    """Schedule a coroutine inside the agent's exchange context.

    Academy resolves its exchange client via a ContextVar set only inside
    ``async with manager:``.  FastAPI handlers run in separate tasks, so we
    capture that context at startup and re-enter it here for every agent call.
    """
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[T] = loop.create_future()

    def _schedule() -> None:
        task = loop.create_task(coro)

        def _done(t: asyncio.Task) -> None:
            if fut.done():
                return
            if t.cancelled():
                fut.cancel()
            elif t.exception() is not None:
                fut.set_exception(t.exception())
            else:
                fut.set_result(t.result())

        task.add_done_callback(_done)

    _agent_ctx.run(_schedule)
    return fut


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mining_handle, _rag_handle, _agent_ctx, _paper_store

    scan_hours = float(os.environ.get("SCAN_INTERVAL_HOURS", "24"))
    scan_seconds = int(scan_hours * 3600)

    _paper_store = PaperStore(
        os.environ.get("DB_PATH") or str(Path(_PROJECT_ROOT) / "cassiopeia.db")
    )

    app.state.bg_tasks: set[asyncio.Task] = set()

    manager = await Manager.from_exchange_factory(
        factory=LocalExchangeFactory(),
        executors=ThreadPoolExecutor(max_workers=6),
    )
    async with manager:
        _mining_handle = await manager.launch(
            LiteratureMiningAgent,
            kwargs={"scan_interval_seconds": scan_seconds, "max_papers_per_query": 20},
        )
        _rag_handle = await manager.launch(RAGAgent)
        _agent_ctx = contextvars.copy_context()
        logger.info(
            "Agents launched — mining: %s  rag: %s",
            _mining_handle.agent_id,
            _rag_handle.agent_id,
        )
        yield
        await manager.shutdown(_mining_handle, blocking=True)
        await manager.shutdown(_rag_handle, blocking=True)

        # Ask litellm's background LoggingWorker to drain and stop cleanly.
        try:
            from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER
            await GLOBAL_LOGGING_WORKER.stop()
        except Exception:
            pass
        # Brief yield so any coroutines scheduled by stop() can reach their next await.
        await asyncio.sleep(0)
        # Cancel remaining tasks and suppress the asyncio "Task destroyed but it is
        # pending!" GC warning — litellm's queue.get() leaves an internal Future that
        # triggers it even after a clean CancelledError.
        pending = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
        if pending:
            logger.debug("Cancelling %d lingering task(s) at shutdown", len(pending))
            for t in pending:
                t.cancel()
                t._log_destroy_pending = False  # noqa: SLF001
            await asyncio.gather(*pending, return_exceptions=True)

    _paper_store.close()


app = FastAPI(title="APPL Literature Mining API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SearchRequest(BaseModel):
    researcher_id: str = "researcher_001"
    name: str
    plant_species: list[str] = []
    stress_types: list[str] = []
    phenotyping_methods: list[str] = []
    expertise_keywords: list[str] = []
    priority_novelty: float = 0.5
    priority_relevance: float = 0.5
    priority_methodology: float = 0.5
    priority_reproducibility: float = 0.5
    time_range_months: int = 12
    source_targets: list[str] = []
    limit: int = 20
    with_critique: bool = False
    max_iterations: int = 3


class SynthesizeRequest(BaseModel):
    question: str
    researcher_id: str | None = None


class FeedbackRequest(BaseModel):
    proposal_id: str
    researcher_id: str
    suggestion: str
    theme: str | None = None
    rating: int  # 1 = thumbs up, -1 = thumbs down


class AnchorSearchRequest(BaseModel):
    doi_or_title: str
    researcher_id: str | None = None
    n_results: int = 10


class KeywordExtractRequest(BaseModel):
    text: str


class PreviewQueriesRequest(BaseModel):
    plant_species: list[str] = []
    stress_types: list[str] = []
    expertise_keywords: list[str] = []
    time_range_months: int = 12


_KEYWORD_EXTRACT_PROMPT = """\
Extract 3-6 concise search keywords or short phrases from the research \
description below that would be most useful as PubMed / Google Scholar \
query terms.  Prefer specific scientific concepts over generic words.
Return ONLY a JSON object: {{"keywords": ["...", ...]}}

Research description:
{text}
"""


@app.post("/api/extract_keywords")
async def extract_keywords(req: KeywordExtractRequest) -> dict[str, list[str]]:
    """Extract search-relevant keywords from a free-text research description."""
    if not req.text.strip():
        return {"keywords": []}
    model = os.environ["LLM_CHAT_MODEL"]
    try:
        import litellm, json as _json
        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": _KEYWORD_EXTRACT_PROMPT.format(text=req.text[:1500])}],
            max_tokens=120,
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        raw = strip_json_fence(response.choices[0].message.content.strip())
        # If the model truncated mid-JSON, extract the keywords array directly.
        import re as _re
        m = _re.search(r'"keywords"\s*:\s*(\[[^\]]*\])', raw)
        if m:
            keywords = _json.loads(m.group(1))
        else:
            keywords = _json.loads(raw).get("keywords", [])
        return {"keywords": [k for k in keywords if isinstance(k, str)]}
    except Exception as exc:
        logger.warning("keyword extraction failed: %s", exc)
        return {"keywords": []}


_OPEN_SOURCES = {"biorxiv", "plos_one", "frontiers", "arxiv"}


@app.post("/api/preview_queries")
async def preview_queries(req: PreviewQueriesRequest) -> list[dict[str, str]]:
    """Return the actual query strings that would be sent to each source."""
    valid_stresses = [s for s in req.stress_types if s in StressType._value2member_map_]
    profile = ResearcherProfile(
        researcher_id="preview",
        name="preview",
        plant_species=req.plant_species,
        stress_types=[StressType(s) for s in valid_stresses],
        expertise_keywords=req.expertise_keywords,
        time_range_months=req.time_range_months,
    )
    queries = QueryGenerator().generate_queries(profile, max_queries_per_source=2)
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for q in queries:
        if q.query_string in seen:
            continue
        seen.add(q.query_string)
        src = q.source_target.value
        result.append({
            "query": q.query_string,
            "source": src,
            "access_type": "open" if src in _OPEN_SOURCES else "paywall",
        })
    return result[:12]


def _n_proposals_for(papers_found: int) -> int:
    """Scale proposal count with the number of newly fetched papers.

    papers_found  →  n_proposals
    0 – 24        →  2
    25 – 49       →  3
    50 – 74       →  4
    75 – 99       →  5
    100 – 124     →  6
    …
    200+          →  10  (cap)
    """
    return max(2, min(10, papers_found // 25 + 2))


async def _run_rag_synthesis(req: SearchRequest, equipment: list[str], n_proposals: int = 5) -> list:
    """Index papers, synthesise proposals (optionally with critique), and assess feasibility."""
    rag_combos: list = []
    try:
        _set_progress(req.researcher_id, "indexing", "Indexing papers into knowledge base…", 52)
        await asyncio.sleep(0)
        logger.info("Indexing papers into ChromaDB…")
        await _call(_rag_handle.index_new_papers())

        liked: list[dict] = (
            _paper_store.get_liked_proposals(req.researcher_id) if _paper_store else []
        )

        if req.max_iterations > 0:
            plural = "s" if req.max_iterations != 1 else ""
            iter_note = f"up to {req.max_iterations} iteration{plural}"
        else:
            iter_note = "single-shot"
        critique_note = " + critique" if req.with_critique else ""
        _set_progress(
            req.researcher_id, "synthesizing",
            f"Synthesising cross-paper proposals ({iter_note}{critique_note})…", 62,
        )
        await asyncio.sleep(0)
        logger.info(
            "Synthesizing RAG proposals (with_critique=%s, max_iterations=%d)…",
            req.with_critique, req.max_iterations,
        )
        rag_combos = await _call(
            _rag_handle.synthesize_combinations(
                researcher_id=req.researcher_id,
                species=req.plant_species,
                stresses=req.stress_types,
                methods=req.phenotyping_methods,
                keywords=req.expertise_keywords,
                n_proposals=n_proposals,
                liked_proposals=liked or None,
                with_critique=req.with_critique,
                instruments=equipment,
                max_iterations=req.max_iterations,
            )
        )
        if rag_combos and equipment:
            _set_progress(req.researcher_id, "feasibility", "Assessing equipment feasibility…", 90)
            await asyncio.sleep(0)
            rag_combos = await _call(
                _rag_handle.assess_feasibility(
                    proposals=rag_combos,
                    available_equipment=equipment,
                )
            )
        logger.info(
            "_run_rag_synthesis done — %d proposal(s) after synthesis + feasibility",
            len(rag_combos),
        )
    except Exception as exc:
        logger.warning("RAG combination synthesis failed: %s", exc)
    return rag_combos


def _schedule_contradictions(researcher_id: str) -> None:
    """Start contradiction detection in the background, inside the agent context."""
    def _bg() -> None:
        task = asyncio.get_running_loop().create_task(
            _rag_handle.detect_contradictions(researcher_id)
        )
        def _done(t: asyncio.Task) -> None:
            app.state.bg_tasks.discard(t)
            if not t.cancelled() and t.exception() is None:
                results = _annotate_contradictions(t.result())
                _contradictions_store[researcher_id] = results
                if _paper_store:
                    _paper_store.save_contradictions(researcher_id, results)
        task.add_done_callback(_done)
        app.state.bg_tasks.add(task)

    _agent_ctx.run(_bg)


_SPECIES_TERMS: dict[str, list[str]] = {
    "poplar":       ["poplar", "populus"],
    "pennycress":   ["pennycress", "thlaspi"],
    "arabidopsis":  ["arabidopsis"],
    "soybean":      ["soybean", "glycine max"],
    "sorghum":      ["sorghum"],
    "switchgrass":  ["switchgrass", "panicum virgatum"],
    "miscanthus":   ["miscanthus"],
    "brachypodium": ["brachypodium"],
}

_STRESS_TERMS: dict[str, list[str]] = {
    "drought":      ["drought", "water deficit", "water stress", "osmotic stress"],
    "nutrient":     ["nutrient", "nitrogen", "phosphorus", "fertiliz"],
    "temperature":  ["temperature", "heat stress", "cold stress", "thermotoler"],
    "pathogen":     ["pathogen", "disease resistance", "fungal", "bacterial"],
    "heavy_metal":  ["heavy metal", "cadmium", "zinc toxicity", "metal stress"],
    "salinity":     ["salin", "salt stress", "nacl"],
    "light":        ["light stress", "photoinhibition", "shade", "photoperiod"],
    "flooding":     ["flood", "waterlog", "submerg", "anaerobic"],
}


def _match_terms(haystack: str, key: str, term_map: dict[str, list[str]]) -> bool:
    return any(t in haystack for t in term_map.get(key, [key]))


def _load_papers_and_combos(researcher_id: str) -> tuple[list[dict], list[dict]]:
    """Load all stored papers for a researcher, sorted by relevance, plus per-paper combos.

    Bypasses the agent's in-memory limit so the full accumulated library is
    always returned regardless of what ``limit`` was set at search time.
    """
    if _paper_store is None:
        return [], []
    scored = _paper_store.load_papers(researcher_id)
    if not scored:
        return [], []
    scored.sort(key=lambda sp: sp.relevance.overall, reverse=True)
    added_at_map = _paper_store.get_added_at_map(researcher_id)
    papers: list[dict[str, Any]] = []
    for i, sp in enumerate(scored):
        haystack = " ".join(filter(None, [
            sp.paper.title,
            sp.paper.abstract,
            " ".join(sp.paper.keywords or []),
        ])).lower()
        papers.append({
            "rank": i + 1,
            "paper_id": sp.paper.paper_id,
            "title": sp.paper.title,
            "authors": sp.paper.authors[:3],
            "journal": sp.paper.journal,
            "doi": sp.paper.doi,
            "url": sp.paper.url,
            "published": sp.paper.published_date.isoformat() if sp.paper.published_date else None,
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
            "added_at": added_at_map.get(sp.paper.paper_id),
            "credibility_level": sp.credibility.value,
            "credibility_icon": _CRED_ICONS.get(sp.credibility.value, "❓"),
            "suggested_combinations": sp.suggested_combinations,
            "matched_species":  [s  for s  in _SPECIES_TERMS if _match_terms(haystack, s,  _SPECIES_TERMS)],
            "matched_stresses": [st for st in _STRESS_TERMS  if _match_terms(haystack, st, _STRESS_TERMS)],
        })
    seen: set[str] = set()
    combos: list[dict[str, Any]] = []
    for p in papers:
        for suggestion in p.get("suggested_combinations", []):
            if suggestion not in seen:
                seen.add(suggestion)
                combos.append({
                    "suggestion": suggestion,
                    "source_paper": p["title"],
                    "source_doi": p["doi"],
                    "paper_credibility": p["credibility_level"],
                    "matched_species":  p.get("matched_species", []),
                    "matched_stresses": p.get("matched_stresses", []),
                })
    return papers, combos


def _annotate_proposals(proposals: list[dict]) -> list[dict]:
    """Add matched_species / matched_stresses to each RAG proposal via text matching."""
    for p in proposals:
        text = " ".join(filter(None, [p.get("suggestion", ""), p.get("rationale", "")])).lower()
        p["matched_species"]  = [s  for s  in _SPECIES_TERMS if _match_terms(text, s,  _SPECIES_TERMS)]
        p["matched_stresses"] = [st for st in _STRESS_TERMS  if _match_terms(text, st, _STRESS_TERMS)]
    return proposals


def _annotate_contradictions(contradictions: list[dict]) -> list[dict]:
    """Add matched_species / matched_stresses to each contradiction via text matching."""
    for c in contradictions:
        text = " ".join(filter(None, [
            c.get("claim_a", ""), c.get("claim_b", ""), c.get("resolution_hint", ""),
        ])).lower()
        c["matched_species"]  = [s  for s  in _SPECIES_TERMS if _match_terms(text, s,  _SPECIES_TERMS)]
        c["matched_stresses"] = [st for st in _STRESS_TERMS  if _match_terms(text, st, _STRESS_TERMS)]
    return contradictions


def _build_synthesis_paper_meta(rag_combos: list) -> dict[str, dict]:
    """Collect title/DOI metadata for every paper referenced in proposals.

    Ensures the frontend can render titles and links for papers used in
    synthesis that didn't make the top-N scored results list.
    """
    if _paper_store is None:
        return {}
    ref_ids: set[str] = set()
    for combo in rag_combos:
        ref_ids.update(combo.get("supporting_papers", []))
        ref_ids.update(
            ki["paper_id"] for ki in combo.get("key_insights", []) if ki.get("paper_id")
        )
    result: dict[str, dict] = {}
    for pid in ref_ids:
        meta = _paper_store.get_paper_metadata(pid)
        if meta:
            result[pid] = {"title": meta.get("title", ""), "doi": meta.get("doi")}
    return result


@app.get("/api/search/progress/{researcher_id}")
async def search_progress(researcher_id: str) -> dict[str, Any]:
    """Return the current search progress for a researcher (polled by the frontend modal)."""
    return _search_progress.get(
        researcher_id,
        {"stage": "idle", "detail": "", "pct": 0, "done": False, "error": None, "ts": 0},
    )


async def _rag_synthesis_or_cached(
    req: SearchRequest,
    equipment: list[str],
    papers_found: int,
    n_proposals: int,
) -> list:
    """Run RAG synthesis or return cached proposals; schedule contradiction detection."""
    if _rag_handle is None:
        return []
    existing_proposals = (
        _paper_store.get_last_proposals(req.researcher_id) if _paper_store else []
    )
    if papers_found == 0 and existing_proposals:
        logger.info(
            "Skipping RAG synthesis — no new papers and %d proposals already exist",
            len(existing_proposals),
        )
        rag_combos = existing_proposals
    else:
        try:
            rag_combos = await asyncio.wait_for(
                _run_rag_synthesis(req, equipment, n_proposals), timeout=600
            )
            logger.info("RAG synthesis complete — %d proposals generated", len(rag_combos))
        except asyncio.TimeoutError:
            logger.warning("RAG synthesis timed out after 600 s — returning papers without proposals")
            rag_combos = []
    _contradictions_store.pop(req.researcher_id, None)
    _schedule_contradictions(req.researcher_id)
    return rag_combos


@app.post("/api/search", responses={503: {"description": "Mining agent not ready"}})
async def search(req: SearchRequest) -> dict[str, Any]:
    if _mining_handle is None or _agent_ctx is None:
        raise HTTPException(status_code=503, detail="Agent not ready")

    logger.info(
        "Search request — researcher=%s species=%s stresses=%s sources=%s",
        req.researcher_id, req.plant_species, req.stress_types, req.source_targets,
    )
    equipment = _facility_equipment()

    _set_progress(req.researcher_id, "registering", "Registering researcher profile…", 5)
    await asyncio.sleep(0)  # yield — let queued GET polls see this stage
    await _call(_mining_handle.register_researcher(
        researcher_id=req.researcher_id,
        name=req.name,
        plant_species=req.plant_species,
        stress_types=req.stress_types,
        phenotyping_methods=req.phenotyping_methods,
        expertise_keywords=req.expertise_keywords,
        available_equipment=equipment,
        priority_novelty=req.priority_novelty,
        priority_relevance=req.priority_relevance,
        priority_methodology=req.priority_methodology,
        priority_reproducibility=req.priority_reproducibility,
        time_range_months=req.time_range_months,
        source_targets=req.source_targets,
    ))

    n_sources = len(req.source_targets) if req.source_targets else "all"
    _set_progress(req.researcher_id, "searching", f"Querying {n_sources} source(s) and scoring papers…", 12)
    await asyncio.sleep(0)  # yield before the long trigger_search
    logger.info("Triggering search…")
    search_result = await _call(_mining_handle.trigger_search(req.researcher_id))

    _set_progress(req.researcher_id, "fetching", "Loading ranked papers…", 42)
    await asyncio.sleep(0)
    papers_found = search_result.get("papers_found", 0)
    logger.info(
        "Search done: %s — %s",
        search_result,
        f"{papers_found} new paper(s) found this run" if papers_found > 0
        else "no new papers found this run (all already in store or no results from sources)",
    )
    papers, combos = _load_papers_and_combos(req.researcher_id)
    logger.info(
        "Loaded %d papers and %d per-paper hypotheses from store (cumulative across all searches)",
        len(papers), len(combos),
    )

    n_proposals = _n_proposals_for(papers_found)
    rag_combos = await _rag_synthesis_or_cached(req, equipment, papers_found, n_proposals)

    if _paper_store is not None:
        _paper_store.save_session(
            session_id=str(uuid.uuid4()),
            researcher_id=req.researcher_id,
            profile_snap={
                "name": req.name,
                "plant_species": req.plant_species,
                "stress_types": req.stress_types,
                "phenotyping_methods": req.phenotyping_methods,
                "expertise_keywords": req.expertise_keywords,
                "source_targets": req.source_targets,
                "time_range_months": req.time_range_months,
            },
            n_papers=len(papers),
            n_proposals=len(rag_combos) + len(combos),
            proposals_snap=rag_combos if rag_combos else None,
        )

    n_props = len(rag_combos) + len(combos)
    _set_progress(
        req.researcher_id, "done",
        f"Scan complete — {len(papers)} papers · {n_props} proposals",
        100, done=True,
    )
    return {
        "search_result": search_result,
        "papers": papers,
        "combos": combos,
        "rag_combos": _annotate_proposals(rag_combos),
        "contradictions": [],
        "synthesis_paper_meta": _build_synthesis_paper_meta(rag_combos),
    }


@app.post("/api/feedback", responses={503: {"description": "Store not ready"}, 422: {"description": "rating must be 1 or -1"}})
async def feedback(req: FeedbackRequest) -> dict[str, str]:
    if _paper_store is None:
        raise HTTPException(status_code=503, detail="Store not ready")
    if req.rating not in (1, -1):
        raise HTTPException(status_code=422, detail="rating must be 1 or -1")
    _paper_store.save_rating(
        proposal_id=req.proposal_id,
        researcher_id=req.researcher_id,
        suggestion=req.suggestion,
        theme=req.theme,
        rating=req.rating,
    )
    return {"status": "ok"}


@app.get("/api/sessions/{researcher_id}")
async def get_sessions(researcher_id: str, limit: int = 20) -> list[dict]:
    if _paper_store is None:
        return []
    return _paper_store.get_sessions(researcher_id, limit=limit)


@app.get("/api/researcher/{researcher_id}/new-papers")
async def get_new_papers(researcher_id: str) -> dict:
    """Return papers added since the researcher's last login, then record this visit."""
    if _paper_store is None:
        return {"new_since": None, "new_count": 0, "new_papers": []}
    last_login = _paper_store.get_last_login(researcher_id)
    _paper_store.record_login(researcher_id)
    if last_login is None:
        return {"new_since": None, "new_count": 0, "new_papers": []}
    new_papers = _paper_store.get_new_papers_since(researcher_id, last_login)
    return {"new_since": last_login, "new_count": len(new_papers), "new_papers": new_papers}


_CRED_ICONS = {
    "high": "🟢", "moderate": "🟡", "preliminary": "🔴", "conflicting": "⚠️",
}


@app.get("/api/contradictions/{researcher_id}")
async def get_contradictions(researcher_id: str) -> list[dict]:
    """Return all accumulated contradiction-detection results, newest first."""
    db_results = _paper_store.get_all_contradictions(researcher_id) if _paper_store else []
    in_memory  = _contradictions_store.get(researcher_id, [])
    seen: set[str] = set()
    merged: list[dict] = []
    for c in in_memory + db_results:
        key = "|".join(sorted(c.get("papers", [])))
        if key not in seen:
            seen.add(key)
            merged.append(c)
    return merged


@app.get("/api/researcher/{researcher_id}/results")
async def get_researcher_results(researcher_id: str) -> dict[str, Any]:
    """Return the last persisted papers and proposals for session restore."""
    papers, combos = _load_papers_and_combos(researcher_id)
    if not papers:
        return {"papers": [], "combos": [], "rag_combos": [], "synthesis_paper_meta": {}}
    rag_combos = _paper_store.get_last_proposals(researcher_id) if _paper_store else []
    return {
        "papers": papers,
        "combos": combos,
        "rag_combos": _annotate_proposals(rag_combos),
        "synthesis_paper_meta": _build_synthesis_paper_meta(rag_combos),
    }


@app.get("/api/researcher/{researcher_id}")
async def get_researcher(researcher_id: str) -> dict | None:
    """Return the stored profile for a researcher, or null if not found."""
    if _mining_handle is not None and _agent_ctx is not None:
        try:
            result = await _call(_mining_handle.get_researcher(researcher_id))
            if result is not None:
                return result
        except Exception:
            pass
    # Fall back to DB for returning users whose profile isn't in agent memory yet.
    if _paper_store is not None:
        return _paper_store.load_profile(researcher_id)
    return None


@app.post("/api/anchor_search", responses={503: {"description": "RAG agent not ready"}})
async def anchor_search(req: AnchorSearchRequest) -> list[dict]:
    if _rag_handle is None or _agent_ctx is None:
        raise HTTPException(status_code=503, detail="RAG agent not ready")
    return await _call(
        _rag_handle.find_similar_to_anchor(
            doi_or_title=req.doi_or_title,
            researcher_id=req.researcher_id,
            n_results=req.n_results,
        )
    )


@app.post("/api/rag/synthesize", responses={503: {"description": "RAG agent not ready"}})
async def rag_synthesize(req: SynthesizeRequest) -> dict[str, str]:
    if _rag_handle is None or _agent_ctx is None:
        raise HTTPException(status_code=503, detail="RAG agent not ready")
    answer = await _call(_rag_handle.synthesize(req.question, req.researcher_id))
    return {"answer": answer}


@app.get("/api/rag/status")
async def rag_status() -> dict[str, Any]:
    if _rag_handle is None or _agent_ctx is None:
        return {"status": "starting"}
    return await _call(_rag_handle.get_rag_status())


@app.get("/api/status")
async def status() -> dict[str, Any]:
    if _mining_handle is None or _agent_ctx is None:
        return {"status": "starting"}
    return await _call(_mining_handle.get_agent_status())


def _facility_equipment() -> list[str]:
    raw = os.environ.get("FACILITY_EQUIPMENT", "")
    return [e.strip() for e in raw.split(",") if e.strip()]


@app.get("/api/config")
async def config() -> dict[str, Any]:
    return {
        "chainlit_url": os.environ.get("CHAINLIT_URL", "http://localhost:8001"),
        "facility_equipment": _facility_equipment(),
    }


_dist = Path(__file__).parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="static")
