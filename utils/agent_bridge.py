# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Shared Academy agent lifecycle and context bridge.

Used by both api_server.py and mcp_server.py to avoid duplicating the
_call helper and the Manager/agent startup/shutdown sequence.

Public API
----------
launch_agents(scan_seconds, db_path)
    Async context manager.  Starts both Academy agents, populates
    this module's globals, yields (mining_handle, rag_handle, store),
    then shuts agents down and closes the store on exit.

_call(coro)
    Schedule a coroutine inside the Academy exchange context and return
    an awaitable Future.  Must be called after launch_agents has started.

run_in_context(fn)
    Run a zero-argument callable inside the Academy exchange context.
    Used by api_server for background tasks that need the context but
    are not themselves coroutines (e.g. _schedule_contradictions).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, TypeVar

from academy.exchange import LocalExchangeFactory
from academy.manager import Manager

from agents.literature_mining_agent import LiteratureMiningAgent
from agents.rag_agent import RAGAgent
from utils.persistence import PaperStore

T = TypeVar("T")
logger = logging.getLogger(__name__)

# Populated by launch_agents(); read by _call and run_in_context.
_ctx: contextvars.Context | None = None


def _call(coro: Awaitable[T]) -> asyncio.Future[T]:
    """Schedule a coroutine inside the Academy exchange context."""
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

    _ctx.run(_schedule)
    return fut


def run_in_context(fn: Callable[[], None]) -> None:
    """Run a zero-argument callable inside the Academy exchange context."""
    _ctx.run(fn)


@asynccontextmanager
async def launch_agents(scan_seconds: int, db_path: str):
    """Start both Academy agents and yield (mining_handle, rag_handle, paper_store).

    Sets this module's ``_ctx`` so that ``_call`` and ``run_in_context``
    work for the duration of the context.  Shuts agents down and closes
    the store on exit.
    """
    global _ctx

    store = PaperStore(db_path)
    manager = await Manager.from_exchange_factory(
        factory=LocalExchangeFactory(),
        executors=ThreadPoolExecutor(max_workers=6),
    )
    async with manager:
        mining = await manager.launch(
            LiteratureMiningAgent,
            kwargs={"scan_interval_seconds": scan_seconds, "max_papers_per_query": 20},
        )
        rag = await manager.launch(RAGAgent)
        _ctx = contextvars.copy_context()
        logger.info(
            "Agents launched — mining: %s  rag: %s",
            mining.agent_id,
            rag.agent_id,
        )
        yield mining, rag, store
        await manager.shutdown(mining, blocking=True)
        await manager.shutdown(rag, blocking=True)

    store.close()
