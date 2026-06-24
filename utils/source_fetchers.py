# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Source fetchers for literature repositories.

Each fetcher implements the same interface: given a SearchQuery, return a list
of PaperMetadata.

Two backends are used:
  - Europe PMC  (https://europepmc.org/RestfulWebService)
    Covers: bioRxiv preprints, PubMed/MEDLINE, Frontiers, PLoS ONE,
            Nature Communications, New Phytologist, Plant Physiology.
    Free, no API key required.  Returns full abstracts in search results.

  - arXiv Atom API  (https://arxiv.org/help/api)
    Covers: arXiv preprints.
    Free, no API key required.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import os

import re

import aiohttp

from models.schemas import PaperMetadata, SearchQuery, SourceType

# Matches tokens that need no quoting in Lucene-style query strings.
# Anything containing spaces, hyphens, slashes, etc. must be quoted.
_SIMPLE_TOKEN = re.compile(r"^\w+$")

logger = logging.getLogger(__name__)

_AND = " AND "
_OR = " OR "

# Set DISABLE_SSL_VERIFY=true in the environment to bypass SSL certificate
# verification — required when a corporate proxy intercepts HTTPS traffic
# with a self-signed certificate (e.g. inside Docker on an ORNL network).
_SSL_VERIFY = os.environ.get("DISABLE_SSL_VERIFY", "").lower() not in ("1", "true", "yes")

if not _SSL_VERIFY:
    logger.warning("SSL certificate verification is DISABLED (DISABLE_SSL_VERIFY=true).")


def _session(**kwargs) -> aiohttp.ClientSession:
    """Return an aiohttp ClientSession with the correct SSL settings."""
    if not _SSL_VERIFY:
        kwargs.setdefault("connector", aiohttp.TCPConnector(ssl=False))
    return aiohttp.ClientSession(**kwargs)


# ─────────────────────────────────────────────────────
# Base class
# ─────────────────────────────────────────────────────

class BaseFetcher(ABC):
    """Abstract base for all source fetchers."""

    source_type: SourceType

    @abstractmethod
    async def fetch(
        self,
        query: SearchQuery,
        max_results: int = 20,
    ) -> list[PaperMetadata]:
        """Execute a search query and return paper metadata."""
        ...

    @abstractmethod
    async def fetch_full_text(self, paper_id: str) -> str | None:
        """Retrieve full text if available (open-access only)."""
        ...


# ─────────────────────────────────────────────────────
# Europe PMC base
# ─────────────────────────────────────────────────────

class _EuropePMCFetcher(BaseFetcher):
    """Base fetcher backed by Europe PMC.

    Subclasses set `source_type` and `source_filter` to specialise the query.
    `source_filter` is prepended to the keyword terms, e.g.:
        'SRC:PPR'                        → bioRxiv / medRxiv preprints
        'SRC:MED'                        → PubMed / MEDLINE
        'JOURNAL:"PLOS ONE"'             → PLOS ONE only
        'PUBLISHER:"Frontiers Media SA"' → all Frontiers journals
    """

    BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    source_filter: str = ""

    async def fetch(
        self,
        query: SearchQuery,
        max_results: int = 20,
    ) -> list[PaperMetadata]:
        terms = _epmc_query_terms(query)
        if not terms:
            return []

        full_query = f"{self.source_filter} ({terms})" if self.source_filter else terms

        temporal = query.contextual_modifiers.get("temporal", "")
        if temporal and ".." in temporal:
            start_year, end_year = temporal.split("..", 1)
            full_query += (
                f" AND FIRST_PDATE:[{start_year.strip()}-01-01"
                f" TO {end_year.strip()}-12-31]"
            )

        params: dict[str, Any] = {
            "query": full_query,
            "format": "json",
            "pageSize": min(max_results, 25),
            "resultType": "core",
        }

        logger.info(
            "%s fetch [%s]: %s",
            type(self).__name__,
            query.researcher_id,
            full_query,
        )

        try:
            async with _session() as session:
                async with session.get(
                    self.BASE_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "Europe PMC returned HTTP %d for %s [%s]",
                            resp.status,
                            type(self).__name__,
                            query.researcher_id,
                        )
                        return []
                    data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("%s network error [%s]: %s", type(self).__name__, query.researcher_id, exc)
            return []

        hit_count = data.get("hitCount", 0)
        papers = self._parse_europepmc(data)
        log = logger.debug if hit_count == 0 else logger.info
        log("%s [%s]: %d hits, returning %d", type(self).__name__, query.researcher_id, hit_count, len(papers))
        return papers

    async def fetch_full_text(self, paper_id: str) -> str | None:
        """Fetch full text for PubMed Central open-access papers (PMC IDs only)."""
        if not paper_id.upper().startswith("PMC"):
            return None
        pmc_id = paper_id.upper()
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmc_id}/fullTextXML"
        try:
            async with _session() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        return None
                    xml_text = await resp.text()
            root = ET.fromstring(xml_text)
            parts: list[str] = []
            for elem in root.iter():
                if elem.tag in ("p", "title", "sec") and elem.text:
                    parts.append(elem.text.strip())
            text = " ".join(p for p in parts if p)
            return text if len(text) > 200 else None
        except (aiohttp.ClientError, asyncio.TimeoutError, ET.ParseError) as exc:
            logger.warning("PMC full text fetch failed for %s: %s", paper_id, exc)
            return None

    async def fetch_full_text_structured(self, paper_id: str) -> dict[str, str] | None:
        """Fetch PMC full text as {section_label: text} preserving section boundaries.

        Returns None for non-PMC papers or on fetch/parse failure.
        Falls back to {"other": plain_text} for flat XML with no <sec> elements.
        Recognised section labels: intro, methods, results, discussion, other.
        The References section is silently dropped.
        """
        if not paper_id.upper().startswith("PMC"):
            return None
        pmc_id = paper_id.upper()
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmc_id}/fullTextXML"
        try:
            async with _session() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        return None
                    xml_text = await resp.text()
            # Strip DOCTYPE — Python's expat rejects external DTD references in JATS XML.
            xml_clean = re.sub(
                r"<!DOCTYPE\b[^[>]*(?:\[[^\]]*\])?[^>]*>", "", xml_text, flags=re.DOTALL
            ).strip()
            root = ET.fromstring(xml_clean)
        except (aiohttp.ClientError, asyncio.TimeoutError, ET.ParseError) as exc:
            logger.warning("PMC structured fetch failed for %s: %s", paper_id, exc)
            return None

        _LABEL_MAP = {
            "intro": "intro", "introduction": "intro",
            "material": "methods", "method": "methods", "methods": "methods",
            "result": "results", "results": "results",
            "discussion": "discussion", "conclusion": "discussion",
            "conclusions": "discussion",
        }
        _SKIP = {"reference", "references", "ref", "supplementary", "supplemental",
                 "acknowledgement", "acknowledgements", "acknowledgment"}

        sections: dict[str, list[str]] = {}
        for sec in root.iter("sec"):
            title_el = sec.find("title")
            raw = (title_el.text or "").lower().strip() if title_el is not None else ""
            # Strip leading section numbers ("1.", "3.2.") common in JATS exports.
            title = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", raw)
            label = next(
                (mapped for key, mapped in _LABEL_MAP.items() if title.startswith(key)),
                "other",
            )
            if any(title.startswith(s) for s in _SKIP):
                continue
            parts = [p.text.strip() for p in sec.findall(".//p") if p.text and p.text.strip()]
            if parts:
                sections.setdefault(label, []).extend(parts)

        if sections:
            return {label: " ".join(texts) for label, texts in sections.items()}

        # Flat XML fallback
        all_text = " ".join(
            p.text.strip() for p in root.findall(".//p") if p.text and p.text.strip()
        )
        return {"other": all_text} if all_text else None

    def _parse_europepmc(self, data: dict[str, Any]) -> list[PaperMetadata]:
        papers: list[PaperMetadata] = []
        for item in data.get("resultList", {}).get("result", []):
            authors: list[str] = []
            if "authorList" in item:
                authors = [
                    a.get("fullName", "")
                    for a in item["authorList"].get("author", [])
                ]
            elif "authorString" in item:
                authors = [a.strip() for a in item["authorString"].split(",")][:5]

            doi = item.get("doi")
            papers.append(
                PaperMetadata(
                    paper_id=item.get("id") or doi or "",
                    title=item.get("title", "").rstrip("."),
                    authors=authors,
                    abstract=item.get("abstractText", ""),
                    source=self.source_type,
                    doi=doi,
                    url=f"https://doi.org/{doi}" if doi else None,
                    published_date=self._parse_date(
                        item.get("firstPublicationDate", "")
                    ),
                    journal=item.get("journalTitle", ""),
                    keywords=[
                        kw if isinstance(kw, str) else kw.get("keyword", "")
                        for kw in item.get("keywordList", {}).get("keyword", [])
                    ],
                    is_open_access=item.get("isOpenAccess", "N") == "Y",
                    citation_count=item.get("citedByCount", 0),
                )
            )
        return papers

    @staticmethod
    def _parse_date(date_str: str) -> datetime | None:
        for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
            try:
                return datetime.strptime(date_str[: len(fmt)], fmt)
            except (ValueError, TypeError):
                continue
        return None


# ─────────────────────────────────────────────────────
# Europe PMC — concrete fetchers
# ─────────────────────────────────────────────────────

class BioRxivFetcher(_EuropePMCFetcher):
    """Preprints (bioRxiv, medRxiv) via Europe PMC SRC:PPR filter."""

    source_type = SourceType.BIORXIV
    source_filter = "SRC:PPR"


class PubMedFetcher(_EuropePMCFetcher):
    """PubMed / MEDLINE via Europe PMC SRC:MED filter.

    The api_key parameter is retained for compatibility but is not used
    by the Europe PMC backend.
    """

    source_type = SourceType.PUBMED
    source_filter = "SRC:MED"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key  # unused, kept for API compatibility


class FrontiersFetcher(_EuropePMCFetcher):
    """All Frontiers journals via Europe PMC publisher filter."""

    source_type = SourceType.FRONTIERS
    source_filter = 'PUBLISHER:"Frontiers Media SA"'


class PlosOneFetcher(_EuropePMCFetcher):
    """PLOS ONE via Europe PMC journal filter."""

    source_type = SourceType.PLOS_ONE
    source_filter = 'JOURNAL:"PLOS ONE"'


class NatureCommsFetcher(_EuropePMCFetcher):
    """Nature Communications via Europe PMC journal filter."""

    source_type = SourceType.NATURE_COMMS
    source_filter = 'JOURNAL:"Nature Communications"'


class NewPhytologistFetcher(_EuropePMCFetcher):
    """New Phytologist via Europe PMC journal filter."""

    source_type = SourceType.NEW_PHYTOLOGIST
    source_filter = 'JOURNAL:"New Phytologist"'


class PlantPhysiologyFetcher(_EuropePMCFetcher):
    """Plant Physiology via Europe PMC journal filter."""

    source_type = SourceType.PLANT_PHYSIOLOGY
    source_filter = 'JOURNAL:"Plant Physiology"'


# ─────────────────────────────────────────────────────
# arXiv  (Atom API — distinct from Europe PMC)
# ─────────────────────────────────────────────────────

class ArxivFetcher(BaseFetcher):
    """Fetcher for arXiv preprints via the official Atom API.

    API: http://export.arxiv.org/api/query
    Searches title, abstract, and all fields.  No API key required.
    """

    source_type = SourceType.ARXIV
    BASE_URL = "https://export.arxiv.org/api/query"
    _NS = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
        "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
    }

    # arXiv rate-limits aggressively; retry with exponential backoff on 429/503
    _RETRY_DELAYS: list[int] = [15, 45, 120]

    async def fetch(
        self,
        query: SearchQuery,
        max_results: int = 20,
    ) -> list[PaperMetadata]:
        terms = _arxiv_query_terms(query)
        if not terms:
            return []

        params: dict[str, Any] = {
            "search_query": terms,
            "max_results": min(max_results, 25),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        logger.info("arXiv fetch [%s]: %s", query.researcher_id, terms)

        text = await self._fetch_with_retry(params, query.researcher_id)
        if text is None:
            return []

        papers = self._parse_atom(text, query.researcher_id)
        log = logger.debug if not papers else logger.info
        log("arXiv [%s]: %d papers — %s", query.researcher_id, len(papers), terms)
        return papers

    async def _fetch_with_retry(
        self, params: dict[str, Any], researcher_id: str
    ) -> str | None:
        """GET the arXiv API with exponential-backoff retries; return body text or None."""
        for attempt, delay in enumerate([0] + self._RETRY_DELAYS):
            if delay:
                logger.info(
                    "arXiv retry %d/%d in %d s…", attempt, len(self._RETRY_DELAYS), delay
                )
                await asyncio.sleep(delay)
            text, retry = await self._attempt_get(params, researcher_id, attempt)
            if text is not None:
                return text
            if not retry:
                return None
        return None

    async def _attempt_get(
        self, params: dict[str, Any], researcher_id: str, attempt: int
    ) -> tuple[str | None, bool]:
        """Single HTTP GET attempt. Returns (body, retry_on_failure)."""
        try:
            async with _session() as session:
                async with session.get(
                    self.BASE_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status in (429, 503):
                        logger.warning("arXiv returned %d [%s]", resp.status, researcher_id)
                        return None, attempt < len(self._RETRY_DELAYS)
                    if resp.status != 200:
                        logger.warning(
                            "arXiv returned HTTP %d [%s]", resp.status, researcher_id
                        )
                        return None, False
                    text = await resp.text()
                    logger.debug(
                        "arXiv HTTP 200 [%s]: response body %d bytes",
                        researcher_id, len(text),
                    )
                    return text, False
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning(
                "arXiv network error (attempt %d) [%s]: %s: %s",
                attempt + 1, researcher_id, type(exc).__name__, exc,
            )
            return None, attempt < len(self._RETRY_DELAYS)

    async def fetch_full_text(self, paper_id: str) -> str | None:
        """Fetch full text from arXiv HTML rendering (available for most post-2020 papers)."""
        # Strip version suffix (e.g. "2301.12345v2" → "2301.12345")
        base_id = paper_id.split("v")[0] if "v" in paper_id else paper_id
        url = f"https://arxiv.org/html/{base_id}"
        try:
            async with _session() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text()
            # Strip scripts/styles then all tags
            import re
            html = re.sub(
                r"<(script|style)[^>]*>.*?</\1>", "", html,
                flags=re.DOTALL | re.IGNORECASE,
            )
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()
            return text if len(text) > 200 else None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("arXiv full text fetch failed for %s: %s", paper_id, exc)
            return None

    def _parse_atom(self, xml_text: str, researcher_id: str = "") -> list[PaperMetadata]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            logger.warning("Failed to parse arXiv Atom XML [%s]", researcher_id)
            return []

        total_results = root.findtext("opensearch:totalResults", "?", self._NS)
        entries = root.findall("atom:entry", self._NS)

        papers: list[PaperMetadata] = []
        for entry in entries:
            raw_id = entry.findtext("atom:id", "", self._NS)
            arxiv_id = raw_id.split("/abs/")[-1].strip()

            title = (
                entry.findtext("atom:title", "", self._NS)
                .strip()
                .replace("\n", " ")
            )
            abstract = (
                entry.findtext("atom:summary", "", self._NS)
                .strip()
                .replace("\n", " ")
            )
            published_str = entry.findtext("atom:published", "", self._NS)

            authors = [
                a.findtext("atom:name", "", self._NS)
                for a in entry.findall("atom:author", self._NS)
            ]

            doi: str | None = None
            for link in entry.findall("atom:link", self._NS):
                if link.get("title") == "doi":
                    doi = link.get("href", "").replace("http://dx.doi.org/", "")

            published_date: datetime | None = None
            if published_str:
                try:
                    published_date = datetime.strptime(published_str[:10], "%Y-%m-%d")
                except ValueError:
                    pass

            papers.append(
                PaperMetadata(
                    paper_id=arxiv_id,
                    title=title,
                    authors=authors,
                    abstract=abstract,
                    source=SourceType.ARXIV,
                    doi=doi,
                    url=f"https://arxiv.org/abs/{arxiv_id}",
                    published_date=published_date,
                    journal="arXiv",
                    keywords=[],
                    is_open_access=True,
                    citation_count=0,
                )
            )
        return papers


# ─────────────────────────────────────────────────────
# Source registry — single source of truth for all source metadata
# ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class SourceInfo:
    """Metadata for a literature source."""
    fetcher: type[BaseFetcher]
    access: Literal["open", "paywall"]
    impact: Literal["high", "mid", "low"]
    label: str


SOURCE_REGISTRY: dict[SourceType, SourceInfo] = {
    SourceType.BIORXIV:          SourceInfo(BioRxivFetcher,           "open",    "low",  "bioRxiv"),
    SourceType.PUBMED:           SourceInfo(PubMedFetcher,            "paywall", "low",  "PubMed"),
    SourceType.FRONTIERS:        SourceInfo(FrontiersFetcher,         "open",    "mid",  "Frontiers"),
    SourceType.PLOS_ONE:         SourceInfo(PlosOneFetcher,           "open",    "mid",  "PLoS ONE"),
    SourceType.NATURE_COMMS:     SourceInfo(NatureCommsFetcher,       "paywall", "high", "Nature Comms"),
    SourceType.NEW_PHYTOLOGIST:  SourceInfo(NewPhytologistFetcher,    "paywall", "high", "New Phytologist"),
    SourceType.PLANT_PHYSIOLOGY: SourceInfo(PlantPhysiologyFetcher,   "paywall", "high", "Plant Physiology"),
    SourceType.ARXIV:            SourceInfo(ArxivFetcher,             "open",    "low",  "arXiv"),
}

FETCHER_REGISTRY: dict[SourceType, type[BaseFetcher]] = {
    src: info.fetcher for src, info in SOURCE_REGISTRY.items()
}


def _epmc_query_terms(query: SearchQuery) -> str:
    """Build Europe PMC keyword clause from term_groups (OR-within, AND-between).

    Falls back to the flat base_terms list when term_groups is empty (cross-product
    / rescue-pass queries).
    """
    if query.term_groups:
        parts: list[str] = []
        for group in query.term_groups:
            tokens = [f'"{t}"' if " " in t else t for t in group if t]
            if not tokens:
                continue
            parts.append(f"({_OR.join(tokens)})" if len(tokens) > 1 else tokens[0])
        if parts:
            return _AND.join(parts)
    return _AND.join(f'"{t}"' if " " in t else t for t in query.base_terms[:3] if t)


def _arxiv_query_terms(query: SearchQuery) -> str:
    """Build arXiv search_query clause from term_groups (OR-within, AND-between).

    Falls back to the flat base_terms list (capped at 2) for simple queries.
    Terms containing any non-word character (spaces, hyphens, slashes…) are
    quoted so arXiv's Lucene parser treats them as phrases, not operators.
    """
    def _tok(t: str) -> str:
        return f'all:"{t}"' if not _SIMPLE_TOKEN.match(t) else f"all:{t}"

    if query.term_groups:
        parts: list[str] = []
        for group in query.term_groups:
            tokens = [_tok(t) for t in group if t]
            if not tokens:
                continue
            parts.append(f"({_OR.join(tokens)})" if len(tokens) > 1 else tokens[0])
        if parts:
            return _AND.join(parts)
    # Cap at 2 terms: arXiv has sparse plant-biology coverage; 3-way ANDs
    # with rare species almost always return 0.
    return _AND.join(_tok(t) for t in query.base_terms[:2] if t)


def get_fetcher(source: SourceType, **kwargs: Any) -> BaseFetcher:
    """Get the appropriate fetcher for a source type."""
    cls = FETCHER_REGISTRY.get(source)
    if cls is None:
        raise ValueError(f"No fetcher registered for {source}")
    return cls(**kwargs)
