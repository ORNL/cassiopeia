# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for Augmentation B: full-text chunking and structured PMC fetching.

Unit tests are fully offline (no network).
Integration tests hit real Europe PMC and require a PMC paper ID:
    pytest tests/test_chunker.py -m integration -v
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from utils.chunker import (
    chunk_paper,
    fetch_and_chunk_paper,
    CHUNK_MIN_TOKENS,
    CHUNK_TARGET_TOKENS,
    CHUNK_OVERLAP_TOKENS,
    SECTION_PRIORITY,
)

_integration = pytest.mark.integration

# ── chunk_paper (pure, no network) ───────────────────────────────────────────

_SECTIONS = {
    "intro":      "Plants respond to drought stress through multiple physiological mechanisms. "
                  "Stomatal closure is among the earliest responses observed.",
    "methods":    "Arabidopsis thaliana seedlings were grown on MS agar for 7 days. "
                  "Drought was imposed by removing plants from agar and air-drying at 22 °C. "
                  "Leaf water potential was measured with a Scholander pressure bomb at 0, 2, 4, and 6 h.",
    "results":    "Leaf water potential declined from −0.3 MPa at baseline to −1.8 MPa at 6 h. "
                  "Stomatal aperture was reduced by 60% within 2 h of drought onset. "
                  "ABA levels increased 4-fold by 4 h.",
    "discussion": "These results confirm that stomatal closure precedes significant water loss. "
                  "The ABA kinetics are consistent with guard-cell signalling studies.",
}

_PAPER_ID = "PMC9876543"


def test_chunk_paper_returns_list():
    chunks = chunk_paper(_PAPER_ID, _SECTIONS)
    assert isinstance(chunks, list)
    assert len(chunks) > 0


def test_chunk_ids_are_unique():
    chunks = chunk_paper(_PAPER_ID, _SECTIONS)
    ids = [c["chunk_id"] for c in chunks]
    assert len(ids) == len(set(ids))


def test_chunk_ids_use_paper_id_prefix():
    chunks = chunk_paper(_PAPER_ID, _SECTIONS)
    for c in chunks:
        assert c["chunk_id"].startswith(_PAPER_ID)


def test_chunk_paper_id_field_matches():
    chunks = chunk_paper(_PAPER_ID, _SECTIONS)
    for c in chunks:
        assert c["paper_id"] == _PAPER_ID


def test_chunk_sections_are_recognised():
    chunks = chunk_paper(_PAPER_ID, _SECTIONS)
    sections_present = {c["section"] for c in chunks}
    assert sections_present <= set(SECTION_PRIORITY) | set(_SECTIONS)


def test_methods_chunks_come_before_intro_chunks():
    """methods must appear before intro in SECTION_PRIORITY ordering."""
    chunks = chunk_paper(_PAPER_ID, _SECTIONS)
    sections_in_order = [c["section"] for c in chunks]
    if "methods" in sections_in_order and "intro" in sections_in_order:
        assert sections_in_order.index("methods") < sections_in_order.index("intro")


def test_no_chunk_shorter_than_min_tokens():
    chunks = chunk_paper(_PAPER_ID, _SECTIONS)
    for c in chunks:
        token_count = c.get("token_count") or len(c["text"]) // 4
        assert token_count >= CHUNK_MIN_TOKENS, (
            f"Chunk {c['chunk_id']} has {token_count} tokens < {CHUNK_MIN_TOKENS}"
        )


def test_chunk_indices_are_sequential():
    chunks = chunk_paper(_PAPER_ID, _SECTIONS)
    indices = [c["chunk_index"] for c in chunks]
    assert indices == list(range(len(chunks)))


def test_empty_sections_returns_empty():
    assert chunk_paper(_PAPER_ID, {}) == []


def test_single_short_section_discarded():
    chunks = chunk_paper(_PAPER_ID, {"methods": "short"})
    assert chunks == [], "A 1-word section should not produce a chunk"


def test_is_abstract_only_false():
    chunks = chunk_paper(_PAPER_ID, _SECTIONS)
    for c in chunks:
        assert c["is_abstract_only"] is False


def test_overlap_produces_shared_words():
    """Two consecutive chunks from the same long section should share words (overlap)."""
    long_methods = " ".join([f"word{i}" for i in range(2000)])
    chunks = chunk_paper(_PAPER_ID, {"methods": long_methods})
    if len(chunks) >= 2:
        words_0 = set(chunks[0]["text"].split())
        words_1 = set(chunks[1]["text"].split())
        shared = words_0 & words_1
        assert shared, "Consecutive chunks should share words from the overlap window"


# ── fetch_and_chunk_paper (mocked) ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_and_chunk_uses_structured_for_pmc():
    """EPMC fetcher's fetch_full_text_structured should be called for PMC IDs."""
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch_full_text_structured = AsyncMock(return_value=_SECTIONS)
    mock_fetcher.fetch_full_text = AsyncMock(return_value=None)

    chunks = await fetch_and_chunk_paper("PMC123", "pubmed", mock_fetcher)

    mock_fetcher.fetch_full_text_structured.assert_called_once_with("PMC123")
    assert chunks is not None and len(chunks) > 0


@pytest.mark.asyncio
async def test_fetch_and_chunk_returns_none_when_no_full_text():
    """Paywalled paper (structured returns None) → None."""
    mock_fetcher = AsyncMock()
    mock_fetcher.fetch_full_text_structured = AsyncMock(return_value=None)

    chunks = await fetch_and_chunk_paper("PMC999", "pubmed", mock_fetcher)

    assert chunks is None


# ── fetch_full_text_structured (mocked HTTP) ──────────────────────────────────

_PMC_XML = """\
<?xml version="1.0"?>
<article>
  <body>
    <sec>
      <title>Introduction</title>
      <p>Plants sense drought through ABA signalling.</p>
    </sec>
    <sec>
      <title>Materials and Methods</title>
      <p>Seedlings were grown on MS agar at 22 °C for 7 days.</p>
      <p>Leaf water potential was measured with a Scholander bomb.</p>
    </sec>
    <sec>
      <title>Results</title>
      <p>Water potential declined from -0.3 to -1.8 MPa over 6 h.</p>
    </sec>
    <sec>
      <title>References</title>
      <p>Smith et al. 2020. Plant Cell.</p>
    </sec>
  </body>
</article>
"""


def _mock_epmc_session(xml_text: str, status: int = 200):
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=xml_text)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=None)
    return session_ctx


@pytest.mark.asyncio
async def test_structured_fetch_returns_section_dict():
    from utils.source_fetchers import BioRxivFetcher
    fetcher = BioRxivFetcher()
    with patch("utils.source_fetchers._session", return_value=_mock_epmc_session(_PMC_XML)):
        result = await fetcher.fetch_full_text_structured("PMC1234567")
    assert result is not None
    assert isinstance(result, dict)
    assert "methods" in result or "intro" in result or "results" in result


@pytest.mark.asyncio
async def test_structured_fetch_drops_references():
    from utils.source_fetchers import BioRxivFetcher
    fetcher = BioRxivFetcher()
    with patch("utils.source_fetchers._session", return_value=_mock_epmc_session(_PMC_XML)):
        result = await fetcher.fetch_full_text_structured("PMC1234567")
    assert result is not None
    for text in result.values():
        assert "Smith et al." not in text, "References section must be dropped"


@pytest.mark.asyncio
async def test_structured_fetch_maps_intro_label():
    from utils.source_fetchers import BioRxivFetcher
    fetcher = BioRxivFetcher()
    with patch("utils.source_fetchers._session", return_value=_mock_epmc_session(_PMC_XML)):
        result = await fetcher.fetch_full_text_structured("PMC1234567")
    assert result is not None
    assert "intro" in result


@pytest.mark.asyncio
async def test_structured_fetch_returns_none_for_non_pmc():
    from utils.source_fetchers import BioRxivFetcher
    fetcher = BioRxivFetcher()
    result = await fetcher.fetch_full_text_structured("PPR123456")
    assert result is None


@pytest.mark.asyncio
async def test_structured_fetch_returns_none_on_http_error():
    from utils.source_fetchers import BioRxivFetcher
    fetcher = BioRxivFetcher()
    with patch("utils.source_fetchers._session",
               return_value=_mock_epmc_session("", status=404)):
        result = await fetcher.fetch_full_text_structured("PMC1234567")
    assert result is None


_FLAT_XML = """\
<?xml version="1.0"?>
<article>
  <body>
    <p>All content in a flat paragraph without any sec elements.</p>
    <p>Second flat paragraph with more content here.</p>
  </body>
</article>
"""


@pytest.mark.asyncio
async def test_structured_fetch_flat_xml_fallback():
    """Flat XML (no <sec> elements) must return {"other": text}."""
    from utils.source_fetchers import BioRxivFetcher
    fetcher = BioRxivFetcher()
    with patch("utils.source_fetchers._session", return_value=_mock_epmc_session(_FLAT_XML)):
        result = await fetcher.fetch_full_text_structured("PMC9999999")
    assert result is not None
    assert "other" in result
    assert "flat paragraph" in result["other"]


# ── PaperStore chunk methods ──────────────────────────────────────────────────

def test_save_and_get_chunks(tmp_path):
    from utils.persistence import PaperStore
    store = PaperStore(tmp_path / "test.db")
    chunks = [
        {"chunk_id": "P1:chunk_0", "paper_id": "P1", "chunk_index": 0,
         "section": "methods", "text": "Methods text here.", "token_count": 10},
        {"chunk_id": "P1:chunk_1", "paper_id": "P1", "chunk_index": 1,
         "section": "results", "text": "Results text here.", "token_count": 8},
    ]
    store.save_chunks(chunks)
    retrieved = store.get_chunks_for_paper("P1")
    assert len(retrieved) == 2
    assert retrieved[0]["chunk_index"] == 0
    assert retrieved[1]["chunk_index"] == 1
    assert retrieved[0]["section"] == "methods"
    store.close()


def test_get_chunks_ordered_by_index(tmp_path):
    from utils.persistence import PaperStore
    store = PaperStore(tmp_path / "test.db")
    store.save_chunks([
        {"chunk_id": "P2:chunk_1", "paper_id": "P2", "chunk_index": 1,
         "section": "results", "text": "B", "token_count": 5},
        {"chunk_id": "P2:chunk_0", "paper_id": "P2", "chunk_index": 0,
         "section": "methods", "text": "A", "token_count": 5},
    ])
    chunks = store.get_chunks_for_paper("P2")
    assert chunks[0]["chunk_index"] == 0
    assert chunks[1]["chunk_index"] == 1
    store.close()


def test_get_chunks_empty_for_unknown_paper(tmp_path):
    from utils.persistence import PaperStore
    store = PaperStore(tmp_path / "test.db")
    assert store.get_chunks_for_paper("UNKNOWN") == []
    store.close()


def test_mark_full_text_indexed(tmp_path):
    from utils.persistence import PaperStore
    import json, datetime
    store = PaperStore(tmp_path / "test.db")
    store._conn.execute(
        "INSERT INTO papers (paper_id, data, rag_indexed, full_text_indexed, first_seen_at) "
        "VALUES (?, ?, 1, 0, ?)",
        ("P3", json.dumps({"paper_id": "P3"}), datetime.datetime.now(datetime.timezone.utc).isoformat()),
    )
    store._conn.commit()
    store.mark_full_text_indexed(["P3"])
    row = store._conn.execute(
        "SELECT full_text_indexed FROM papers WHERE paper_id = 'P3'"
    ).fetchone()
    assert row["full_text_indexed"] == 1
    store.close()


def test_clear_verify_cache_for_paper(tmp_path):
    from utils.persistence import PaperStore
    store = PaperStore(tmp_path / "test.db")
    store.set_verify_cache("P4::hash1", {"supported": True, "confidence": 0.9, "reason": "ok"})
    store.set_verify_cache("P4::hash2", {"supported": False, "confidence": 0.1, "reason": "no"})
    store.set_verify_cache("P5::hash1", {"supported": True, "confidence": 0.8, "reason": "yes"})
    store.clear_verify_cache_for_paper("P4")
    assert store.get_verify_cache("P4::hash1") is None
    assert store.get_verify_cache("P4::hash2") is None
    assert store.get_verify_cache("P5::hash1") is not None  # other paper untouched
    store.close()


def test_get_papers_needing_chunking(tmp_path):
    from utils.persistence import PaperStore
    import json, datetime
    store = PaperStore(tmp_path / "test.db")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    store._conn.executemany(
        "INSERT INTO papers (paper_id, data, rag_indexed, full_text_indexed, first_seen_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("A", json.dumps({"source": "pubmed"}), 1, 0, now),  # needs chunking
            ("B", json.dumps({"source": "pubmed"}), 1, 1, now),  # already chunked
            ("C", json.dumps({"source": "pubmed"}), 0, 0, now),  # not even indexed
        ],
    )
    store._conn.commit()
    needing = store.get_papers_needing_chunking()
    ids = [pid for pid, *_ in needing]
    assert "A" in ids
    assert "B" not in ids
    assert "C" not in ids
    store.close()


# ── Integration tests (real network) ─────────────────────────────────────────

@_integration
@pytest.mark.asyncio
async def test_fetch_full_text_structured_real_pmc():
    """PMC7468712 is an open-access Arabidopsis paper — must return section dict."""
    from utils.source_fetchers import PubMedFetcher
    fetcher = PubMedFetcher()
    result = await fetcher.fetch_full_text_structured("PMC7468712")
    assert result is not None, "Expected structured sections for an OA PMC paper"
    assert isinstance(result, dict)
    assert len(result) > 0
    assert any(v for v in result.values()), "Sections must not all be empty"


@_integration
@pytest.mark.asyncio
async def test_fetch_and_chunk_real_pmc():
    """End-to-end: fetch + chunk a real PMC paper. Chunks must be non-empty and unique."""
    from utils.source_fetchers import PubMedFetcher
    from utils.chunker import fetch_and_chunk_paper
    fetcher = PubMedFetcher()
    chunks = await fetch_and_chunk_paper("PMC7468712", "pubmed", fetcher)
    if chunks is None:
        pytest.skip("Full text not available for this paper (paywalled or fetch failed)")
    assert len(chunks) > 0
    ids = [c["chunk_id"] for c in chunks]
    assert len(ids) == len(set(ids)), "chunk_ids must be unique"
    for c in chunks:
        assert c["text"], "Chunk text must not be empty"
        assert c["paper_id"] == "PMC7468712"


@_integration
@pytest.mark.asyncio
async def test_structured_fetch_non_pmc_returns_none():
    """A preprint with no PMC ID must return None without raising."""
    from utils.source_fetchers import BioRxivFetcher
    fetcher = BioRxivFetcher()
    result = await fetcher.fetch_full_text_structured("PPR9999999")
    assert result is None
