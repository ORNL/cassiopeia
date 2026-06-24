# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""MCP server interface for Cassiopeia.

Exposes literature mining and RAG capabilities as MCP tools so that
APPL-Agent (or any other MCP-compatible orchestrator) can call them via
the Streamable HTTP transport.

The tool return convention mirrors APPL-Agent's ``ToolResult`` schema so
that the caller can parse responses uniformly regardless of which APPL
subsystem is being invoked.

Run with:
    uvicorn mcp_server:asgi_app --port 8002

Or programmatically:
    python mcp_server.py
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Awaitable, TypeVar, Union

_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(Path(_PROJECT_ROOT) / ".env")

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel

from academy.exchange import LocalExchangeFactory
from academy.logging import init_logging
from academy.manager import Manager

from agents.literature_mining_agent import LiteratureMiningAgent
from agents.rag_agent import RAGAgent
from utils.persistence import PaperStore

init_logging(logging.INFO)
logger = logging.getLogger(__name__)

T = TypeVar("T")

# ── Global state (populated in lifespan) ─────────────────────────────────────

_mining_handle = None
_rag_handle = None
_agent_ctx: contextvars.Context | None = None
_paper_store: PaperStore | None = None


# ── Standardised cross-system result wrapper ─────────────────────────────────


class ToolResult(BaseModel):
    """Return envelope matching APPL-Agent's ToolResult schema.

    Status code convention (same as APPL-Agent):
    - 2xx  success, ``result`` is a plain string
    - 3xx  success, ``result`` is a dict
    - 4xx  client error
    - 503  agent/store not ready
    """

    code: int | None = None
    result: Union[str, dict, list] = ""
    extra: dict | str | None = None
    tool_name: str = ""


# ── Academy context bridge ────────────────────────────────────────────────────


def _call(coro: Awaitable[T]) -> asyncio.Future[T]:
    """Schedule a coroutine inside the Academy exchange context.

    Academy resolves its exchange client via a ContextVar that is only set
    inside ``async with manager:``.  FastMCP tool handlers run in separate
    tasks, so we capture that context at startup and re-enter it here.
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


# ── Lifespan (start / stop Academy agents) ───────────────────────────────────


@asynccontextmanager
async def _lifespan(app: Any):  # app is the FastMCP ASGI app
    global _mining_handle, _rag_handle, _agent_ctx, _paper_store

    scan_hours = float(os.environ.get("SCAN_INTERVAL_HOURS", "24"))
    scan_seconds = int(scan_hours * 3600)

    _paper_store = PaperStore(
        os.environ.get("DB_PATH") or str(Path(_PROJECT_ROOT) / "cassiopeia.db")
    )

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
            "MCP server agents launched — mining: %s  rag: %s",
            _mining_handle.agent_id,
            _rag_handle.agent_id,
        )
        yield

        await manager.shutdown(_mining_handle, blocking=True)
        await manager.shutdown(_rag_handle, blocking=True)

    _paper_store.close()


# ── FastMCP server ────────────────────────────────────────────────────────────

_MCP_HOST = os.environ.get("MCP_HOST", "localhost")
_MCP_PORT = int(os.environ.get("MCP_PORT", "8002"))

mcp = FastMCP(
    name="Cassiopeia",
    stateless_http=True,
    lifespan=_lifespan,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{_MCP_HOST}:*", "localhost:*", "127.0.0.1:*"],
        allowed_origins=[
            f"http://{_MCP_HOST}:*",
            "http://localhost:*",
            "http://127.0.0.1:*",
        ],
    ),
)

# ASGI app exposed for ``uvicorn mcp_server:asgi_app``
asgi_app = mcp.streamable_http_app()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _facility_equipment() -> list[str]:
    raw = os.environ.get("FACILITY_EQUIPMENT", "")
    return [e.strip() for e in raw.split(",") if e.strip()]


_RAG_AGENT = "RAG agent"


def _not_ready(tool_name: str, component: str = "Mining agent") -> str:
    return ToolResult(
        code=503, result=f"{component} not ready", tool_name=tool_name
    ).model_dump_json()


# ── MCP tools ─────────────────────────────────────────────────────────────────


@mcp.tool()
async def search_literature(
    researcher_id: str,
    name: str,
    plant_species: list[str],
    stress_types: list[str],
    phenotyping_methods: list[str],
    expertise_keywords: list[str],
    time_range_months: int = 12,
    priority_novelty: float = 0.5,
    priority_relevance: float = 0.5,
    priority_methodology: float = 0.5,
    priority_reproducibility: float = 0.5,
    limit: int = 20,
    with_critique: bool = False,
) -> str:
    """Run a full literature search cycle for a researcher profile.

    Returns a ToolResult (code 301) whose ``result`` dict contains:

    - ``papers``: top scored papers
    - ``combos``: per-paper hypotheses
    - ``rag_combos``: cross-paper proposals with feasibility, verification, and
      (when ``with_critique=True``) critic annotations. Each proposal shape::

          {
            "proposal_id": str,
            "schema_version": int,     # see version table below
            "theme": str,
            "suggestion": str,
            "rationale": str,          # may contain [paper_id] citation tags
            "key_insights": [          # one entry per supporting paper
              {"paper_id": str, "insight": str}
            ],
            "supporting_papers": [str],
            "novelty_warning": str,
            "feasibility": {...},      # present after assess_feasibility
            "verification": {          # Augmentation A — always present
              "checked_claims": int,
              "supported": int,
              "unsupported": int,
              "flagged": bool,         # True iff unsupported/checked > 1/3
              "details": [
                {"claim": str, "paper_id": str,
                 "supported": bool | None, "confidence": float | None,
                 "reason": str}
              ]
            },
            "critique": {              # Augmentation D — present iff with_critique=True
              "novelty": {
                "assessment": "novel" | "incremental" | "duplicative",
                "reasoning": str,
                "closest_prior_work": str | None
              },
              "confounds": [{"concern": str, "severity": "low"|"medium"|"high"}],
              "evidence_strength": {
                "assessment": "well_supported" | "partial" | "overreaching",
                "reasoning": str
              },
              "feasibility_concerns": [{"concern": str, "severity": "low"|"medium"|"high"}],
              "overall_recommendation": "pursue" | "refine" | "deprioritize",
              "summary": str
            }                          # None if the critic LLM call failed
          }

    Each ``rag_combos`` item carries a ``schema_version`` integer so clients
    can branch without guessing what fields are present:

    ======  ===================================================================
    v1      Legacy — no longer produced. ``key_insights`` used ``{paper, insight}``.
            No ``verification`` field.
    v2      ``key_insights`` is ``[{paper_id, insight}]``.
            ``verification`` field always present.  No longer produced.
    v3      Current. All v2 fields plus ``critique`` when ``with_critique=True``.
            Branch on ``schema_version >= 3`` and check ``critique is not None``
            before reading critique sub-fields.
    ======  ===================================================================

    Cost note (v3): one synthesis call (LLM_CHAT_MODEL) + ~3N LLM_SCORING_MODEL
    verification calls + N LLM_CHAT_MODEL critique calls per cycle.  For N=5
    proposals that is ~21 LLM calls.  Pass ``with_critique=False`` (the
    default) to skip the critic pass when latency or cost is a concern.

    .. note::
        ``key_insights`` changed shape in Augmentation A from
        ``{"paper": str, "insight": str}`` to ``{"paper_id": str, "insight": str}``.
        Any APPL-Agent tool wrappers or response parsers that read
        ``key_insights[n]["paper"]`` must be updated to use ``"paper_id"``.
        Branch on ``schema_version >= 2`` to apply this logic safely.
    """
    if _mining_handle is None or _agent_ctx is None:
        return _not_ready("search_literature")

    equipment = _facility_equipment()

    await _call(
        _mining_handle.register_researcher(
            researcher_id=researcher_id,
            name=name,
            plant_species=plant_species,
            stress_types=stress_types,
            phenotyping_methods=phenotyping_methods,
            expertise_keywords=expertise_keywords,
            available_equipment=equipment,
            priority_novelty=priority_novelty,
            priority_relevance=priority_relevance,
            priority_methodology=priority_methodology,
            priority_reproducibility=priority_reproducibility,
            time_range_months=time_range_months,
        )
    )

    search_result = await _call(_mining_handle.trigger_search(researcher_id))
    papers = await _call(_mining_handle.get_top_papers(researcher_id, limit=limit))
    combos = await _call(_mining_handle.get_combinations(researcher_id))

    rag_combos: list = []
    if _rag_handle is not None:
        try:
            await _call(_rag_handle.index_new_papers())

            liked: list[dict] = []
            if _paper_store is not None:
                liked = _paper_store.get_liked_proposals(researcher_id)

            rag_combos = await _call(
                _rag_handle.synthesize_combinations(
                    researcher_id=researcher_id,
                    species=plant_species,
                    stresses=stress_types,
                    methods=phenotyping_methods,
                    keywords=expertise_keywords,
                    liked_proposals=liked or None,
                    with_critique=with_critique,
                    instruments=equipment,
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
            logger.warning("RAG synthesis failed in MCP tool: %s", exc)

    if _paper_store is not None:
        _paper_store.save_session(
            session_id=str(uuid.uuid4()),
            researcher_id=researcher_id,
            profile_snap={
                "name": name,
                "plant_species": plant_species,
                "stress_types": stress_types,
                "phenotyping_methods": phenotyping_methods,
                "expertise_keywords": expertise_keywords,
            },
            n_papers=len(papers),
            n_proposals=len(rag_combos) + len(combos),
        )

    return ToolResult(
        code=301,
        result={
            "search_result": search_result,
            "papers": papers,
            "combos": combos,
            "rag_combos": rag_combos,
        },
        tool_name="search_literature",
    ).model_dump_json()


@mcp.tool()
async def get_top_papers(researcher_id: str, limit: int = 20) -> str:
    """Return the top scored papers from the last search cycle for a researcher."""
    if _mining_handle is None or _agent_ctx is None:
        return _not_ready("get_top_papers")
    papers = await _call(_mining_handle.get_top_papers(researcher_id, limit=limit))
    return ToolResult(
        code=301, result={"papers": papers}, tool_name="get_top_papers"
    ).model_dump_json()


@mcp.tool()
async def detect_contradictions(
    researcher_id: str,
    n_papers_per_pass: int = 20,
    n_passes: int = 3,
) -> str:
    """Scan indexed abstracts for conflicting claims across papers.

    Runs ``n_passes`` LLM calls over different semantic slices of
    ``n_papers_per_pass`` abstracts each, then deduplicates results.

    Returns a ToolResult (code 301) whose ``result`` dict contains a
    ``contradictions`` list of claim pairs with suggested explanations.
    """
    if _rag_handle is None or _agent_ctx is None:
        return _not_ready("detect_contradictions", _RAG_AGENT)
    contradictions = await _call(
        _rag_handle.detect_contradictions(
            researcher_id,
            n_papers_per_pass=n_papers_per_pass,
            n_passes=n_passes,
        )
    )
    return ToolResult(
        code=301,
        result={"contradictions": contradictions},
        tool_name="detect_contradictions",
    ).model_dump_json()


@mcp.tool()
async def anchor_search(
    doi_or_title: str,
    researcher_id: str | None = None,
    n_results: int = 10,
) -> str:
    """Find papers in the knowledge base semantically similar to an anchor paper.

    ``doi_or_title`` can be a DOI (e.g. ``10.1093/plphys/kiad123``) or a
    free-text title fragment.  The agent fetches the abstract from Europe PMC
    and uses it as a semantic query seed against ChromaDB.
    """
    if _rag_handle is None or _agent_ctx is None:
        return _not_ready("anchor_search", _RAG_AGENT)
    results = await _call(
        _rag_handle.find_similar_to_anchor(
            doi_or_title=doi_or_title,
            researcher_id=researcher_id,
            n_results=n_results,
        )
    )
    return ToolResult(
        code=301, result={"papers": results}, tool_name="anchor_search"
    ).model_dump_json()


@mcp.tool()
async def ask_knowledge_base(
    question: str, researcher_id: str | None = None
) -> str:
    """Answer a natural-language question using RAG over indexed paper abstracts.

    Uses a LangGraph ReAct agent to retrieve relevant passages from ChromaDB
    and synthesise a grounded answer.  Returns a plain-text answer string
    (ToolResult code 201).
    """
    if _rag_handle is None or _agent_ctx is None:
        return _not_ready("ask_knowledge_base", _RAG_AGENT)
    answer = await _call(_rag_handle.synthesize(question, researcher_id))
    return ToolResult(
        code=201, result=answer, tool_name="ask_knowledge_base"
    ).model_dump_json()


@mcp.tool()
async def litminer_status() -> str:
    """Return the operational status of both Academy agents.

    Returns a ToolResult (code 301) whose ``result`` dict has ``mining``
    and ``rag`` sub-objects.  Returns code 503 if agents are not yet ready.
    """
    if _mining_handle is None or _rag_handle is None or _agent_ctx is None:
        return _not_ready("litminer_status", "Agents")
    mining_status = await _call(_mining_handle.get_agent_status())
    rag_status = await _call(_rag_handle.get_rag_status())
    return ToolResult(
        code=301,
        result={"mining": mining_status, "rag": rag_status},
        tool_name="litminer_status",
    ).model_dump_json()


# ── Entrypoint ────────────────────────────────────────────────────────────────


def main() -> None:
    import uvicorn

    uvicorn.run(asgi_app, host=_MCP_HOST, port=_MCP_PORT, log_level="info")


if __name__ == "__main__":
    main()
