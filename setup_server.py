# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Setup wizard backend: choose, create and edit the deployment's domain pack.

Runs *instead of* the API server, before launch (``./launch.sh setup``), and
serves only ``/api/setup/*``.  It has no login: it writes files in this
checkout (``domains/<pack>/``, ``.env``) and makes git commits, so it only
answers requests from this machine, and every state-changing request must
carry the ``X-Cassiopeia-Setup`` header — which a cross-site page cannot send
without a CORS preflight this server never approves.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(_PROJECT_ROOT) / ".env")

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from domains import DomainPackError, pack_from_dict, selected_pack_name  # noqa: E402
from domains import editing  # noqa: E402
from models.schemas import SearchQuery  # noqa: E402
from utils.source_fetchers import BACKENDS  # noqa: E402

app = FastAPI(title="Cassiopeia setup", docs_url=None, redoc_url=None, openapi_url=None)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Serve this machine only, and require the wizard's header on writes."""
    client = request.client.host if request.client else ""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if client not in _LOCAL_HOSTS or host not in _LOCAL_HOSTS:
        return JSONResponse({"detail": "The setup wizard only serves this machine"}, 403)
    if request.method not in ("GET", "HEAD") and request.headers.get("x-cassiopeia-setup") != "1":
        return JSONResponse({"detail": "Missing X-Cassiopeia-Setup header"}, 403)
    return await call_next(request)


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


# ── Models ───────────────────────────────────────────────────────────────────

class NewPackRequest(BaseModel):
    name: str
    title: str = ""
    template: str | None = None


class DraftRequest(BaseModel):
    data: dict[str, Any]
    new: bool = False


class SelectRequest(BaseModel):
    name: str


class CommitRequest(BaseModel):
    message: str
    branch: str | None = None


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/api/setup/state")
async def state() -> dict[str, Any]:
    return {
        "packs": editing.list_packs(),
        "selected": selected_pack_name(),
        "backends": sorted(BACKENDS),
    }


@app.get("/api/setup/packs/{name}")
async def get_pack(name: str) -> dict[str, Any]:
    try:
        return {
            "data": editing.read_pack(name),
            "locked": editing.locked_fields(name),
            "git": editing.git_state(name),
            "summary": editing.pack_summary(name),
        }
    except DomainPackError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/setup/new")
async def new_pack(req: NewPackRequest) -> dict[str, Any]:
    try:
        editing.pack_dir(req.name)
        if editing.pack_file(req.name).exists():
            raise editing.PackEditError(f"A pack named {req.name!r} already exists")
        return {"data": editing.new_pack_data(req.name, req.title.strip(), req.template)}
    except DomainPackError as exc:
        raise _fail(exc) from exc


@app.post("/api/setup/packs/{name}/check")
async def check_pack(name: str, req: DraftRequest) -> dict[str, Any]:
    report = editing.check_pack(name, req.data, new=req.new)
    diff = ""
    if not report["errors"]:
        try:
            diff = editing.diff_pack(name, req.data, new=req.new)
        except Exception as exc:  # rendering must never hide the report
            report["errors"].append(f"Could not render domain.yaml: {exc}")
    return {**report, "diff": diff}


@app.put("/api/setup/packs/{name}")
async def save_pack(name: str, req: DraftRequest) -> dict[str, Any]:
    try:
        result = editing.save_pack(name, req.data, new=req.new)
    except DomainPackError as exc:
        raise _fail(exc) from exc
    return {**result, "git": editing.git_state(name)}


@app.post("/api/setup/packs/{name}/test-sources")
async def test_sources(name: str, req: DraftRequest) -> list[dict[str, Any]]:
    """Run one small search per source of the draft and report what came back."""
    try:
        pack = pack_from_dict(editing.prune(req.data), editing.pack_dir(name), run_hooks=False)
    except DomainPackError as exc:
        raise _fail(exc) from exc
    term = (
        pack.prompts.fallback_query
        or next((f.query_default for f in pack.query_facets if f.query_default), "")
        or pack.prompts.field
    )

    async def probe(info) -> dict[str, Any]:
        result: dict[str, Any] = {"key": info.key, "label": info.label, "query": term}
        cls = BACKENDS.get(info.backend)
        if cls is None:
            return {**result, "ok": False, "error": f"unknown backend {info.backend!r}"}
        # AND of up to three words: an exact multi-word phrase rarely matches.
        words = term.split()[:3]
        query = SearchQuery(
            query_string=term, source_target=info.key, researcher_id="setup-wizard",
            base_terms=words, term_groups=[[w] for w in words],
        )
        try:
            papers = await asyncio.wait_for(cls(info).fetch(query, max_results=5), timeout=45)
        except Exception as exc:
            return {**result, "ok": False, "error": str(exc) or type(exc).__name__}
        return {
            **result,
            "ok": bool(papers),
            "count": len(papers),
            "titles": [p.title for p in papers[:3]],
            "error": None if papers else "no results — check the filter",
        }

    return await asyncio.gather(*(probe(s) for s in pack.sources.values()))


@app.post("/api/setup/select")
async def select(req: SelectRequest) -> dict[str, Any]:
    try:
        editing.select_pack(req.name)
    except DomainPackError as exc:
        raise _fail(exc) from exc
    return {"selected": req.name}


@app.post("/api/setup/packs/{name}/commit")
async def commit(name: str, req: CommitRequest) -> dict[str, Any]:
    try:
        result = editing.commit_pack(name, message=req.message, branch=req.branch)
    except DomainPackError as exc:
        raise _fail(exc) from exc
    return {**result, "git": editing.git_state(name)}
