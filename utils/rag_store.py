# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""ChromaDB-backed vector store for semantic paper search."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction


_DEFAULT_PERSIST = Path(__file__).parent.parent / "chroma_db"


def preload_embedding_model() -> None:
    """Download the ONNX embedding model if it is not already cached.

    Called by the ``cassiopeia-preload`` console script (see pyproject.toml)
    and by the Docker build step so the model is available without a network
    connection at runtime.
    """
    import sys
    print("Checking embedding model cache…", flush=True)
    DefaultEmbeddingFunction()
    print("Embedding model ready.", flush=True)
    sys.exit(0)


class RAGStore:
    """Wraps a ChromaDB collection for semantic retrieval over paper abstracts."""

    def __init__(
        self,
        persist_dir: str | Path = _DEFAULT_PERSIST,
        collection_name: str = "papers",
    ) -> None:
        persist_dir = Path(persist_dir)
        persist_dir.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(
            path=str(persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        self._ef = DefaultEmbeddingFunction()  # all-MiniLM-L6-v2 via ONNX (no PyTorch needed)
        self._col = self._client.get_or_create_collection(
            name=collection_name,
            embedding_function=self._ef,
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add_paper(
        self,
        paper_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Upsert a paper abstract (and optional metadata) into ChromaDB."""
        self._col.upsert(
            ids=[paper_id],
            documents=[text],
            metadatas=[metadata or {}],
        )

    def add_papers_batch(
        self,
        items: list[tuple[str, str, dict[str, Any]]],
    ) -> None:
        """Batch upsert: items is list of (paper_id, text, metadata)."""
        if not items:
            return
        ids, docs, metas = zip(*items)
        self._col.upsert(
            ids=list(ids),
            documents=list(docs),
            metadatas=list(metas),
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def query(
        self,
        text: str,
        n_results: int = 5,
        where: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to n_results semantically similar papers."""
        count = self._col.count()
        if count == 0:
            return []
        n = min(n_results, count)
        kwargs: dict[str, Any] = {"query_texts": [text], "n_results": n}
        if where:
            kwargs["where"] = where
        result = self._col.query(
            include=["documents", "metadatas", "distances"],
            **kwargs,
        )
        hits = []
        for i in range(len(result["ids"][0])):
            chroma_id = result["ids"][0][i]
            meta = result["metadatas"][0][i]
            # Prefer paper_id from metadata (set explicitly since P1#7) so that
            # when augmentation B switches ChromaDB ids to chunk ids the
            # paper_id lookup continues to work without changing callers.
            paper_id = meta.get("paper_id") or chroma_id
            hits.append(
                {
                    "paper_id": paper_id,
                    "document": result["documents"][0][i],
                    "metadata": meta,
                    "distance": result["distances"][0][i],
                }
            )
        return hits

    def delete_paper(self, paper_id: str) -> None:
        """Remove a paper's abstract entry from ChromaDB before chunk upgrade."""
        try:
            self._col.delete(ids=[paper_id])
        except Exception:
            pass  # not in collection — fine

    def count(self) -> int:
        return self._col.count()
