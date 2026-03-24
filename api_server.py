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
    model = os.environ.get("LLM_CHAT_MODEL", "anthropic/claude-sonnet-4-6")
    try:
        import litellm, json as _json
        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": _KEYWORD_EXTRACT_PROMPT.format(text=req.text[:1500])}],
            max_tokens=120,
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        data = _json.loads(response.choices[0].message.content)
        return {"keywords": data.get("keywords", [])}
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


@app.post("/api/search", responses={503: {"description": "Mining agent not ready"}})
async def search(req: SearchRequest) -> dict[str, Any]:
    if _mining_handle is None or _agent_ctx is None:
        raise HTTPException(status_code=503, detail="Agent not ready")

    logger.info(
        "Search request — researcher=%s species=%s stresses=%s sources=%s",
        req.researcher_id, req.plant_species, req.stress_types, req.source_targets,
    )
    equipment = _facility_equipment()

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

    logger.info("Triggering search…")
    search_result = await _call(_mining_handle.trigger_search(req.researcher_id))
    logger.info("Search done: %s — fetching top papers…", search_result)
    papers = await _call(_mining_handle.get_top_papers(req.researcher_id, limit=req.limit))
    logger.info("Got %d papers — fetching combinations…", len(papers))
    combos = await _call(_mining_handle.get_combinations(req.researcher_id))
    logger.info("Got %d combinations", len(combos))

    rag_combos: list = []
    contradictions: list = []
    if _rag_handle is not None:
        try:
            logger.info("Indexing papers into ChromaDB…")
            await _call(_rag_handle.index_new_papers())

            liked: list[dict] = []
            if _paper_store is not None:
                liked = _paper_store.get_liked_proposals(req.researcher_id)

            logger.info("Synthesizing RAG proposals…")
            rag_combos = await _call(
                _rag_handle.synthesize_combinations(
                    researcher_id=req.researcher_id,
                    species=req.plant_species,
                    stresses=req.stress_types,
                    methods=req.phenotyping_methods,
                    keywords=req.expertise_keywords,
                    liked_proposals=liked or None,
                )
            )

            if rag_combos and equipment:
                rag_combos = await _call(
                    _rag_handle.assess_feasibility(
                        proposals=rag_combos,
                        available_equipment=equipment,
                    )
                )
        except Exception as exc:
            logger.warning("RAG combination synthesis failed: %s", exc)

        def _bg_contradictions() -> None:
            # Already inside _agent_ctx — create the task directly.
            task = asyncio.get_running_loop().create_task(
                _rag_handle.detect_contradictions(req.researcher_id)
            )

            def _discard(t: asyncio.Task) -> None:
                app.state.bg_tasks.discard(t)

            task.add_done_callback(_discard)
            app.state.bg_tasks.add(task)

        _agent_ctx.run(_bg_contradictions)

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
            },
            n_papers=len(papers),
            n_proposals=len(rag_combos) + len(combos),
        )

    return {
        "search_result": search_result,
        "papers": papers,
        "combos": combos,
        "rag_combos": rag_combos,
        "contradictions": contradictions,
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


@app.get("/api/researcher/{researcher_id}")
async def get_researcher(researcher_id: str) -> dict | None:
    """Return the stored profile for a researcher, or null if not found."""
    if _mining_handle is None or _agent_ctx is None:
        return None
    return await _call(_mining_handle.get_researcher(researcher_id))


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
