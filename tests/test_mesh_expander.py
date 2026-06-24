# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for utils/mesh_expander.py.

All tests mock the NCBI HTTP calls so no network is needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.mesh_expander import expand_to_mesh, _efetch, _esearch


# ── helpers ───────────────────────────────────────────────────────────────────

_ESEARCH_RESPONSE = {
    "esearchresult": {"idlist": ["68001234"]}
}

_EFETCH_XML = """\
<?xml version="1.0"?>
<DescriptorRecordSet>
  <DescriptorRecord>
    <DescriptorName><String>Droughts</String></DescriptorName>
    <ConceptList>
      <Concept PreferredConceptYN="Y">
        <TermList>
          <Term ConceptPreferredTermYN="Y"><String>Droughts</String></Term>
          <Term><String>Drought</String></Term>
          <Term><String>Water Deficit</String></Term>
        </TermList>
      </Concept>
    </ConceptList>
  </DescriptorRecord>
</DescriptorRecordSet>
"""


def _mock_session(json_data=None, xml_text=None, status=200):
    """Return an aiohttp.ClientSession mock for one GET request."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {})
    resp.text = AsyncMock(return_value=xml_text or "")
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=None)
    session = AsyncMock()
    session.get = MagicMock(return_value=ctx)
    session_ctx = AsyncMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=None)
    return session_ctx


# ── _esearch ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_esearch_returns_uid():
    with patch("utils.mesh_expander.aiohttp.ClientSession", return_value=_mock_session(json_data=_ESEARCH_RESPONSE)):
        uid = await _esearch("drought")
    assert uid == "68001234"


@pytest.mark.asyncio
async def test_esearch_returns_none_on_empty():
    empty = {"esearchresult": {"idlist": []}}
    with patch("utils.mesh_expander.aiohttp.ClientSession", return_value=_mock_session(json_data=empty)):
        uid = await _esearch("unknownterm_xyz")
    assert uid is None


@pytest.mark.asyncio
async def test_esearch_returns_none_on_http_error():
    with patch("utils.mesh_expander.aiohttp.ClientSession", return_value=_mock_session(status=500)):
        uid = await _esearch("drought")
    assert uid is None


# ── _efetch ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_efetch_extracts_descriptor_and_terms():
    with patch("utils.mesh_expander.aiohttp.ClientSession", return_value=_mock_session(xml_text=_EFETCH_XML)):
        headings = await _efetch("68001234", "drought")
    assert headings[0] == "Droughts"
    assert "Drought" in headings
    assert "Water Deficit" in headings


@pytest.mark.asyncio
async def test_efetch_deduplicates():
    with patch("utils.mesh_expander.aiohttp.ClientSession", return_value=_mock_session(xml_text=_EFETCH_XML)):
        headings = await _efetch("68001234", "drought")
    assert len(headings) == len(set(headings))


@pytest.mark.asyncio
async def test_efetch_returns_original_on_http_error():
    with patch("utils.mesh_expander.aiohttp.ClientSession", return_value=_mock_session(status=503)):
        headings = await _efetch("68001234", "my term")
    assert headings == ["my term"]


# ── expand_to_mesh (full flow) ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_expand_to_mesh_full_flow():
    with (
        patch("utils.mesh_expander._esearch", new=AsyncMock(return_value="68001234")),
        patch("utils.mesh_expander._efetch", new=AsyncMock(return_value=["Droughts", "Drought", "Water Deficit"])),
    ):
        result = await expand_to_mesh("drought")
    assert result == ["Droughts", "Drought", "Water Deficit"]


@pytest.mark.asyncio
async def test_expand_to_mesh_returns_term_on_failure():
    import aiohttp
    with patch("utils.mesh_expander._esearch", side_effect=aiohttp.ClientError()):
        result = await expand_to_mesh("drought")
    assert result == ["drought"]


@pytest.mark.asyncio
async def test_expand_to_mesh_cache_hit(tmp_path):
    from utils.persistence import PaperStore

    store = PaperStore(tmp_path / "test.db")
    store.set_mesh_cache("drought", ["Droughts", "Drought"])

    with patch("utils.mesh_expander._ncbi_lookup") as mock_lookup:
        result = await expand_to_mesh("drought", store=store)

    mock_lookup.assert_not_called()
    assert result == ["Droughts", "Drought"]
    store.close()


@pytest.mark.asyncio
async def test_expand_to_mesh_populates_cache(tmp_path):
    from utils.persistence import PaperStore

    store = PaperStore(tmp_path / "test.db")

    with (
        patch("utils.mesh_expander._esearch", new=AsyncMock(return_value="68001234")),
        patch("utils.mesh_expander._efetch", new=AsyncMock(return_value=["Droughts", "Drought"])),
    ):
        result = await expand_to_mesh("drought", store=store)

    assert result == ["Droughts", "Drought"]
    cached = store.get_mesh_cache("drought")
    assert cached == ["Droughts", "Drought"]
    store.close()
