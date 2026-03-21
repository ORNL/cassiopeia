"""ChromaDB-backed vector store for semantic paper search."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction


_DEFAULT_PERSIST = Path(__file__).parent.parent / "chroma_db"
_DEFAULT_MODEL = "all-MiniLM-L6-v2"


class RAGStore:
    """Wraps a ChromaDB collection for semantic retrieval over paper abstracts."""

    def __init__(
        self,
        persist_dir: str | Path = _DEFAULT_PERSIST,
        embedding_model: str = _DEFAULT_MODEL,
        collection_name: str = "papers",
    ) -> None:
        persist_dir = Path(persist_dir)
        persist_dir.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(
            path=str(persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        self._ef = SentenceTransformerEmbeddingFunction(model_name=embedding_model)
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
            hits.append(
                {
                    "paper_id": result["ids"][0][i],
                    "document": result["documents"][0][i],
                    "metadata": result["metadatas"][0][i],
                    "distance": result["distances"][0][i],
                }
            )
        return hits

    def count(self) -> int:
        return self._col.count()
