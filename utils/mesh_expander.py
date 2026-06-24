# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""MeSH term expansion via NCBI E-utilities.

`expand_to_mesh(term, store)` maps a plain keyword to its preferred MeSH
heading plus entry-term synonyms.  Results are cached in SQLite for 30 days
so first-run expansion (~20 terms, ~2 s) never repeats within a month.

NCBI rate limit: 3 requests/second without an API key.  Each term requires
2 sequential HTTP calls (esearch → efetch); the calls are made one term at a
time inside `enrich_with_mesh` so we stay well within that limit.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

import aiohttp

logger = logging.getLogger(__name__)

_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_EFETCH_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_NCBI_DELAY  = 0.35   # seconds between terms to stay under 3 req/s


async def expand_to_mesh(term: str, store=None) -> list[str]:
    """Return MeSH headings for *term*: preferred heading first, then synonyms.

    Checks the SQLite mesh_cache (30-day TTL) before hitting NCBI.
    Returns ``[term]`` unchanged on any lookup failure so the caller always
    gets a usable list.
    """
    if store is not None:
        cached = store.get_mesh_cache(term)
        if cached is not None:
            logger.debug("MeSH cache hit for %r: %s", term, cached)
            return cached

    try:
        headings = await _ncbi_lookup(term)
    except (aiohttp.ClientError, asyncio.TimeoutError, ET.ParseError) as exc:
        logger.debug("MeSH lookup failed for %r: %s", term, exc)
        return [term]

    if store is not None:
        store.set_mesh_cache(term, headings)
    return headings


async def _ncbi_lookup(term: str) -> list[str]:
    """Two-step NCBI call: esearch to get UID, efetch to get the full record."""
    uid = await _esearch(term)
    if uid is None:
        logger.debug("MeSH esearch: no match for %r", term)
        return [term]
    return await _efetch(uid, term)


async def _esearch(term: str) -> str | None:
    """Return the MeSH UID for *term*, or None if not found."""
    params = urlencode({"db": "mesh", "term": term, "retmode": "json", "retmax": "1"})
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{_ESEARCH_URL}?{params}",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    ids = data.get("esearchresult", {}).get("idlist", [])
    return ids[0] if ids else None


async def _efetch(uid: str, original_term: str) -> list[str]:
    """Fetch the full MeSH record for *uid* and extract headings."""
    params = urlencode({"db": "mesh", "id": uid, "retmode": "xml"})
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{_EFETCH_URL}?{params}",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return [original_term]
            xml_text = await resp.text()

    root = ET.fromstring(xml_text)

    # Preferred heading from DescriptorName
    descriptor = root.findtext(".//DescriptorName/String") or ""

    # Entry terms: all Term/String values across all concepts
    entry_terms = [
        el.text.strip()
        for el in root.findall(".//Term/String")
        if el.text and el.text.strip()
    ]

    # Preferred heading first, then unique synonyms (preserving order)
    seen: set[str] = set()
    result: list[str] = []
    for h in ([descriptor] if descriptor else [original_term]) + entry_terms:
        if h and h not in seen:
            seen.add(h)
            result.append(h)
    return result
