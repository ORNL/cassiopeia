# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Full-text chunking for Augmentation B.

`chunk_paper` splits a structured {section: text} dict into overlapping
token-budget windows per section, ordered by scientific relevance
(methods/results first).

`fetch_and_chunk_paper` wraps fetcher calls so RAGAgent.index_new_papers
stays clean: one call returns chunks or None (paywalled/unavailable).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

CHUNK_TARGET_TOKENS  = 400
CHUNK_OVERLAP_TOKENS = 80
CHUNK_MIN_TOKENS     = 60
MAX_FULL_TEXT_CHARS  = 500_000

# Section labels in priority order for verification and context building.
SECTION_PRIORITY = ["methods", "results", "discussion", "intro", "other", "abstract"]


def _count_tokens(text: str, model: str) -> int:
    """Token count via litellm (accurate for Claude). Falls back to char estimate."""
    try:
        import litellm
        return litellm.token_counter(model=model, text=text)
    except Exception:
        return max(1, len(text) // 4)


def chunk_paper(paper_id: str, sections: dict[str, str]) -> list[dict]:
    """Split structured full text into overlapping token-budget chunks.

    Each chunk: {chunk_id, paper_id, chunk_index, section, text, token_count}.
    Sections are processed in SECTION_PRIORITY order; any unlisted sections follow.
    Chunks shorter than CHUNK_MIN_TOKENS are discarded.
    """
    model = os.environ.get("LLM_SCORING_MODEL", "gpt-4o")
    # words-per-token approximation: ~5 chars/word, ~4 chars/token → ~1.25 words/token
    target_words  = CHUNK_TARGET_TOKENS * 5 // 4   # ≈ 500 words
    overlap_words = CHUNK_OVERLAP_TOKENS * 5 // 4  # ≈ 100 words
    advance       = max(1, target_words - overlap_words)

    ordered_sections = [s for s in SECTION_PRIORITY if s in sections]
    ordered_sections += [s for s in sections if s not in SECTION_PRIORITY]

    all_chunks: list[dict] = []
    idx = 0

    for section in ordered_sections:
        text = sections[section][:MAX_FULL_TEXT_CHARS]
        words = text.split()
        i = 0
        while i < len(words):
            end = min(i + target_words, len(words))
            chunk_text = " ".join(words[i:end])
            token_count = _count_tokens(chunk_text, model)
            if token_count >= CHUNK_MIN_TOKENS:
                all_chunks.append({
                    "chunk_id":    f"{paper_id}:chunk_{idx}",
                    "paper_id":    paper_id,
                    "chunk_index": idx,
                    "section":     section,
                    "text":        chunk_text,
                    "token_count": token_count,
                    "is_abstract_only": False,
                })
                idx += 1
            i += advance

    return all_chunks


async def fetch_and_chunk_paper(
    paper_id: str,
    source_value: str,
    fetcher,
) -> list[dict] | None:
    """Fetch full text via the appropriate fetcher method, then chunk it.

    For EPMC-backed fetchers (PMC papers): calls fetch_full_text_structured,
    which preserves section boundaries.
    For arXiv: falls back to fetch_full_text (plain HTML), labelled "other".
    Returns None for paywalled papers, non-PMC/non-arXiv sources, or fetch failures.
    """
    from utils.source_fetchers import SourceType

    try:
        source = SourceType(source_value)
    except ValueError:
        return None

    if hasattr(fetcher, "fetch_full_text_structured"):
        sections = await fetcher.fetch_full_text_structured(paper_id)
        if sections:
            logger.debug("Chunking %s via structured XML (%d sections)", paper_id, len(sections))
            return chunk_paper(paper_id, sections)

    if source == SourceType.ARXIV:
        text = await fetcher.fetch_full_text(paper_id)
        if text:
            logger.debug("Chunking %s via arXiv plain text", paper_id)
            return chunk_paper(paper_id, {"other": text})

    return None
