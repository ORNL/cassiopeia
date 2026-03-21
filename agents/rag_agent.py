"""RAG agent: ChromaDB owner and semantic Q&A synthesiser.

This Academy agent is the sole writer to ChromaDB.  It:
- Syncs newly-scored papers from SQLite → ChromaDB on demand
- Answers free-form questions via a LangGraph ReAct loop with a
  ``search_knowledge_base`` tool that queries ChromaDB
- Provides raw semantic search results for the dashboard
- Synthesises cross-paper experiment proposals with novelty checking
- Detects contradictions between retrieved papers
- Finds papers similar to a user-supplied anchor DOI or title

Provider / model is controlled by ``LLM_CHAT_MODEL`` (LiteLLM convention).
The embedding model is controlled by ``EMBEDDING_MODEL``
(default: ``all-MiniLM-L6-v2``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import aiohttp
import litellm

litellm.drop_params = True

from academy.agent import Agent, action

from utils.persistence import PaperStore
from utils.rag_store import RAGStore

logger = logging.getLogger(__name__)


def _proposal_id(suggestion: str) -> str:
    """Stable 16-char hex ID derived from the suggestion text."""
    return hashlib.sha256(suggestion.encode()).hexdigest()[:16]


_COMBINATIONS_PROMPT = """\
You are a plant biology research strategist.

Researcher profile:
  Species  : {species}
  Stresses : {stresses}
  Methods  : {methods}
  Keywords : {keywords}

{preference_block}\
Below are abstracts from papers retrieved for this researcher.
Your task is to identify CROSS-PAPER synergies — novel experiment designs \
that combine findings, methods, or observations from MULTIPLE papers above.
Do NOT just paraphrase a single paper.

Return a JSON object with a "proposals" array of 4-5 items:
{{
  "proposals": [
    {{
      "theme": "<2-4 word label, e.g. 'root-canopy coupling'>",
      "suggestion": "<one concrete experiment proposal, 1-2 sentences>",
      "rationale": "<why combining these papers is promising — what gap or opportunity this fills, 1-2 sentences>",
      "key_insights": [
        {{"paper": "<Paper N title or number>", "insight": "<the specific finding from that paper that motivates this proposal, 1 sentence>"}},
        ...
      ],
      "supporting_papers": ["<Paper N title or number>", ...]
    }}
  ]
}}

Paper abstracts:
{context}
"""

_CONTRADICTION_PROMPT = """\
You are a critical plant biology reviewer.

Below are abstracts from {n} papers retrieved for the same researcher profile.
Identify pairs or groups of papers that appear to present CONTRADICTORY or \
CONFLICTING findings — e.g. opposite effects of a treatment, disagreements \
about a mechanism, or incompatible quantitative claims.

Return a JSON object:
{{
  "contradictions": [
    {{
      "papers": ["<Paper A title>", "<Paper B title>"],
      "claim_a": "<what Paper A asserts, 1 sentence>",
      "claim_b": "<what Paper B asserts that contradicts Paper A, 1 sentence>",
      "resolution_hint": "<possible explanation for the discrepancy, e.g. species/condition difference, 1 sentence>"
    }}
  ]
}}

If no contradictions are found return {{"contradictions": []}}.

Paper abstracts:
{context}
"""

_FEASIBILITY_PROMPT = """\
You are an expert in plant phenotyping experimental design.

A researcher at a high-throughput plant phenotyping facility has proposed the \
following experiment. Assess whether it is executable given the instruments \
available at the facility.

Available instruments:
{equipment}

Proposed experiment:
{suggestion}

Assess feasibility on three axes:
1. Whether the required measurements can be made with the available instruments \
   (possibly under different names or synonyms — e.g. "canopy reflectance" maps \
   to VNIR hyperspectral imaging).
2. Whether any critical step requires equipment that is clearly absent.
3. Whether any adaptation or workaround exists that would make the experiment \
   executable with the available instruments.

Return a single JSON object:
{{
  "feasible": <true | false | "partial">,
  "confidence": <float 0-1>,
  "missing_equipment": ["<item>", ...],
  "adaptation": "<short description of any workaround, or empty string if fully feasible>",
  "note": "<1-2 sentence plain-language summary>"
}}
"""


class RAGAgent(Agent):
    """Academy agent that owns the ChromaDB vector store.

    Actions:
    - index_new_papers         — sync un-indexed papers from SQLite → ChromaDB
    - query                    — return raw semantic search hits
    - synthesize_combinations  — cross-paper experiment proposals
    - assess_feasibility       — annotate proposals with equipment feasibility
    - detect_contradictions    — find conflicting claims across papers
    - find_similar_to_anchor   — semantic search seeded from a DOI/title
    - synthesize               — answer a free-form question via LangGraph ReAct
    - get_rag_status           — store statistics
    """

    def __init__(
        self,
        db_path: str | None = None,
        rag_persist_dir: str | None = None,
        embedding_model: str | None = None,
    ) -> None:
        super().__init__()

        _db = db_path or os.environ.get(
            "DB_PATH",
            str(Path(__file__).parent.parent / "cassiopeia.db"),
        )
        _rag_dir = rag_persist_dir or os.environ.get(
            "RAG_PERSIST_DIR",
            str(Path(__file__).parent.parent / "chroma_db"),
        )
        _emb = embedding_model or os.environ.get(
            "EMBEDDING_MODEL", "all-MiniLM-L6-v2"
        )

        self._store = PaperStore(_db)
        self._rag = RAGStore(persist_dir=_rag_dir, embedding_model=_emb)
        self._chat_model = os.environ.get(
            "LLM_CHAT_MODEL", "anthropic/claude-sonnet-4-6"
        )

        logger.info(
            "RAGAgent ready — %d papers already in ChromaDB",
            self._rag.count(),
        )

    @action
    async def index_new_papers(self) -> dict[str, int]:
        """Sync papers marked ``rag_indexed=0`` in SQLite into ChromaDB."""
        unindexed = self._store.get_unindexed_papers()
        if not unindexed:
            return {"indexed": 0, "total": self._rag.count()}

        items = [
            (paper_id, abstract, meta)
            for paper_id, _researcher_id, abstract, meta in unindexed
        ]
        self._rag.add_papers_batch(items)

        paper_ids = [paper_id for paper_id, *_ in unindexed]
        self._store.mark_indexed(paper_ids)

        logger.info("Indexed %d new papers into ChromaDB", len(paper_ids))
        return {"indexed": len(paper_ids), "total": self._rag.count()}

    @action
    async def query(
        self,
        text: str,
        n_results: int = 5,
        researcher_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return semantic search hits from ChromaDB.

        Args:
            text: Free-form query text.
            n_results: Maximum number of results to return.
            researcher_id: If given, restrict results to that researcher's papers.
        """
        where = {"researcher_id": researcher_id} if researcher_id else None
        hits = self._rag.query(text, n_results=n_results, where=where)

        results = []
        for hit in hits:
            meta = self._store.get_paper_metadata(hit["paper_id"]) or {}
            results.append(
                {
                    "paper_id": hit["paper_id"],
                    "title": meta.get("title", ""),
                    "journal": meta.get("journal", ""),
                    "doi": meta.get("doi"),
                    "abstract_snippet": hit["document"][:300],
                    "similarity": round(1.0 - hit["distance"], 3),
                }
            )
        return results

    def _build_context_blocks(self, hits: list[dict]) -> str:
        """Format knowledge-base hits into numbered text blocks for LLM prompts.

        Uses up to 6000 characters per paper — consistent with the text window
        stored during scoring — covering the abstract and beginning of the
        methods section for open-access papers enriched with full text.
        """
        blocks = []
        for i, hit in enumerate(hits, 1):
            meta = self._store.get_paper_metadata(hit["paper_id"]) or {}
            title = meta.get("title", "Unknown")
            blocks.append(f"[Paper {i}] {title}\n{hit['document'][:6000]}")
        return "\n\n".join(blocks)

    @staticmethod
    def _build_preference_block(liked_proposals: list[dict] | None) -> str:
        """Format previously-liked proposals into an LLM steering block."""
        if not liked_proposals:
            return ""
        liked_lines = "\n".join(
            f"  - [{p.get('theme', '')}] {p['suggestion']}"
            for p in liked_proposals[:10]
        )
        return (
            "Previously liked proposals (steer towards NEW territory, "
            "do NOT repeat these):\n"
            f"{liked_lines}\n\n"
        )

    async def _llm_proposals(self, prompt: str) -> list[dict]:
        """Call the LLM with a combinations prompt and return parsed proposals."""
        response = await litellm.acompletion(
            model=self._chat_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1400,
            response_format={"type": "json_object"},
            temperature=0.4,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        return json.loads(raw).get("proposals", [])

    @action
    async def synthesize_combinations(
        self,
        researcher_id: str,
        species: list[str],
        stresses: list[str],
        methods: list[str],
        keywords: list[str] | None = None,
        n_papers: int = 12,
        liked_proposals: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Generate cross-paper experiment proposals by reasoning over multiple abstracts.

        Unlike the per-paper hypotheses produced during scoring, this action:
        1. Ensures ChromaDB is up to date (calls ``index_new_papers`` first)
        2. Retrieves the ``n_papers`` most relevant abstracts from ChromaDB
        3. Sends them **all together** in a single LLM call, asking for novel
           multi-paper experiment proposals that combine findings across papers
        4. Checks each proposal for novelty against already-liked proposals

        Args:
            researcher_id: Researcher to scope results to.
            species: Plant species of interest.
            stresses: Stress types of interest.
            methods: Phenotyping methods of interest.
            keywords: Additional free-text keywords.
            n_papers: Number of abstracts to retrieve from ChromaDB.
            liked_proposals: Previously liked proposals to avoid duplicating
                and to steer the LLM towards unexplored territory.

        Returns:
            List of dicts with keys: ``proposal_id``, ``theme``, ``suggestion``,
            ``rationale``, ``key_insights``, ``supporting_papers``,
            ``novelty_warning``.
        """
        await self.index_new_papers()

        if self._rag.count() == 0:
            return []

        query_text = " ".join(species + stresses + methods + (keywords or []))
        where = {"researcher_id": researcher_id} if researcher_id else None
        hits = self._rag.query(query_text, n_results=n_papers, where=where)
        if not hits:
            return []

        prompt = _COMBINATIONS_PROMPT.format(
            species=", ".join(species) or "unspecified",
            stresses=", ".join(stresses) or "unspecified",
            methods=", ".join(methods) or "unspecified",
            keywords=", ".join(keywords or []) or "none",
            context=self._build_context_blocks(hits),
            preference_block=self._build_preference_block(liked_proposals),
        )

        try:
            proposals = await self._llm_proposals(prompt)
        except Exception as exc:
            logger.warning("synthesize_combinations failed: %s", exc)
            return []

        return await self._enrich_proposals(proposals, researcher_id)

    async def _enrich_proposals(
        self, proposals: list[dict], researcher_id: str
    ) -> list[dict[str, Any]]:
        """Attach proposal_id, theme, and novelty_warning to raw LLM proposals."""
        results = []
        for p in proposals:
            suggestion = p.get("suggestion", "")
            if not suggestion:
                continue
            is_novel, novelty_warning = await self._check_novelty(suggestion, researcher_id)
            results.append(
                {
                    "proposal_id": _proposal_id(suggestion),
                    "theme": (p.get("theme") or "")[:40],
                    "suggestion": suggestion,
                    "rationale": p.get("rationale", ""),
                    "key_insights": p.get("key_insights", []),
                    "supporting_papers": p.get("supporting_papers", []),
                    "novelty_warning": novelty_warning if not is_novel else "",
                }
            )
        return results

    async def _check_novelty(
        self, suggestion: str, researcher_id: str
    ) -> tuple[bool, str]:
        """Check whether a proposal is genuinely novel vs. already-indexed content.

        Returns ``(is_novel, warning_message)``.  ``is_novel`` is False when a
        very similar abstract already exists in ChromaDB (distance < 0.3).
        """
        where = {"researcher_id": researcher_id}
        hits = self._rag.query(suggestion, n_results=1, where=where)
        if not hits:
            return True, ""
        distance = hits[0].get("distance", 1.0)
        if distance < 0.3:
            meta = self._store.get_paper_metadata(hits[0]["paper_id"]) or {}
            title = meta.get("title", "an existing paper")
            return False, f"Similar to: {title}"
        return True, ""

    @action
    async def assess_feasibility(
        self,
        proposals: list[dict[str, Any]],
        available_equipment: list[str],
    ) -> list[dict[str, Any]]:
        """Annotate experiment proposals with feasibility assessments.

        For each proposal the LLM checks whether the required measurements can
        be made with the instruments listed in ``available_equipment``.  The
        check is aware of synonyms (e.g. "canopy reflectance spectroscopy"
        maps to VNIR hyperspectral imaging) and can suggest adaptations when
        a partial match exists.

        Args:
            proposals: List of proposal dicts as returned by
                ``synthesize_combinations``.  Each must have a ``suggestion``
                key.  Other keys are passed through unchanged.
            available_equipment: Flat list of instrument / capability names
                that the facility provides (from the researcher profile).

        Returns:
            The same list of proposals, each extended with a ``feasibility``
            dict containing keys: ``feasible``, ``confidence``,
            ``missing_equipment``, ``adaptation``, ``note``.
        """
        if not proposals or not available_equipment:
            for p in proposals:
                p.setdefault("feasibility", None)
            return proposals

        equipment_str = "\n".join(f"  - {e}" for e in available_equipment)
        results = []
        for proposal in proposals:
            feasibility = await self._assess_one(proposal["suggestion"], equipment_str)
            results.append({**proposal, "feasibility": feasibility})
        return results

    async def _assess_one(self, suggestion: str, equipment_str: str) -> dict[str, Any]:
        """Run the feasibility LLM call for a single proposal."""
        prompt = _FEASIBILITY_PROMPT.format(
            equipment=equipment_str,
            suggestion=suggestion,
        )
        try:
            response = await litellm.acompletion(
                model=self._chat_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()
            return json.loads(raw)
        except Exception as exc:
            logger.warning("assess_feasibility failed for proposal: %s", exc)
            return {
                "feasible": None,
                "confidence": 0.0,
                "missing_equipment": [],
                "adaptation": "",
                "note": "Assessment unavailable.",
            }

    @action
    async def detect_contradictions(
        self,
        researcher_id: str,
        n_papers: int = 10,
    ) -> list[dict[str, Any]]:
        """Find conflicting claims across the top retrieved papers.

        Retrieves the most semantically central abstracts for the researcher's
        query space and sends them together to an LLM that identifies
        contradictory or conflicting findings.

        Returns a list of dicts with keys:
        ``papers``, ``claim_a``, ``claim_b``, ``resolution_hint``.
        """
        await self.index_new_papers()

        if self._rag.count() == 0:
            return []

        where = {"researcher_id": researcher_id}
        hits = self._rag.query(researcher_id, n_results=n_papers, where=where)
        if len(hits) < 2:
            return []

        context_blocks = []
        for i, hit in enumerate(hits, 1):
            meta = self._store.get_paper_metadata(hit["paper_id"]) or {}
            title = meta.get("title", "Unknown")
            context_blocks.append(f"[Paper {i}] {title}\n{hit['document'][:500]}")

        context = "\n\n".join(context_blocks)
        prompt = _CONTRADICTION_PROMPT.format(n=len(hits), context=context)

        try:
            response = await litellm.acompletion(
                model=self._chat_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800,
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()
            return json.loads(raw).get("contradictions", [])
        except Exception as exc:
            logger.warning("detect_contradictions failed: %s", exc)
            return []

    @action
    async def find_similar_to_anchor(
        self,
        doi_or_title: str,
        researcher_id: str | None = None,
        n_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Find papers semantically similar to an anchor paper.

        Fetches the abstract of the anchor paper from Europe PMC using the
        supplied DOI or title string, then uses that abstract as a ChromaDB
        query seed.

        Args:
            doi_or_title: A DOI (e.g. ``10.1093/jxb/erx456``) or a free-text
                title fragment to look up via Europe PMC.
            researcher_id: If given, restrict results to that researcher's papers.
            n_results: Maximum number of similar papers to return.

        Returns:
            List of dicts with keys: ``paper_id``, ``title``, ``journal``,
            ``doi``, ``abstract_snippet``, ``similarity``.
        """
        abstract = await self._fetch_anchor_abstract(doi_or_title)
        if not abstract:
            logger.warning(
                "find_similar_to_anchor: could not resolve '%s'", doi_or_title
            )
            return []

        where = {"researcher_id": researcher_id} if researcher_id else None
        hits = self._rag.query(abstract, n_results=n_results, where=where)

        results = []
        for hit in hits:
            meta = self._store.get_paper_metadata(hit["paper_id"]) or {}
            results.append(
                {
                    "paper_id": hit["paper_id"],
                    "title": meta.get("title", ""),
                    "journal": meta.get("journal", ""),
                    "doi": meta.get("doi"),
                    "abstract_snippet": hit["document"][:300],
                    "similarity": round(1.0 - hit["distance"], 3),
                }
            )
        return results

    async def _fetch_anchor_abstract(self, doi_or_title: str) -> str:
        """Resolve a DOI or title to an abstract via Europe PMC."""
        is_doi = doi_or_title.startswith("10.")
        if is_doi:
            query = f"DOI:{doi_or_title}"
        else:
            escaped = doi_or_title.replace('"', "")
            query = f'TITLE:"{escaped}"'

        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        params = {
            "query": query,
            "format": "json",
            "pageSize": "1",
            "resultType": "core",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return ""
                    data = await resp.json()
            results = data.get("resultList", {}).get("result", [])
            if not results:
                return ""
            return results[0].get("abstractText") or results[0].get("title", "")
        except Exception as exc:
            logger.warning("_fetch_anchor_abstract failed: %s", exc)
            return ""

    @action
    async def synthesize(self, question: str, researcher_id: str | None = None) -> str:
        """Answer a free-form question using RAG + LangGraph ReAct.

        Builds a ReAct agent with a ``search_knowledge_base`` tool that
        queries ChromaDB, then streams the final answer back.
        """
        try:
            return await self._run_react(question, researcher_id)
        except Exception as exc:
            logger.warning("ReAct synthesis failed, falling back to direct RAG: %s", exc)
            return await self._fallback_answer(question, researcher_id)

    @action
    async def get_rag_status(self) -> dict[str, Any]:
        """Return ChromaDB statistics."""
        unindexed = self._store.get_unindexed_papers()
        return {
            "papers_in_chromadb": self._rag.count(),
            "papers_pending_indexing": len(unindexed),
            "embedding_model": os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
            "chat_model": self._chat_model,
        }

    async def _run_react(self, question: str, researcher_id: str | None) -> str:
        """Run LangGraph create_react_agent with ChromaDB tool."""
        from langchain_core.tools import tool as lc_tool
        from langchain_community.chat_models import ChatLiteLLM
        from langgraph.prebuilt import create_react_agent

        rag = self._rag
        store = self._store

        @lc_tool
        def search_knowledge_base(query: str) -> str:
            """Search the plant biology paper knowledge base for relevant passages."""
            where = {"researcher_id": researcher_id} if researcher_id else None
            hits = rag.query(query, n_results=5, where=where)
            if not hits:
                return "No relevant papers found."
            parts = []
            for hit in hits:
                meta = store.get_paper_metadata(hit["paper_id"]) or {}
                title = meta.get("title", "Unknown")
                parts.append(f"[{title}]\n{hit['document'][:500]}")
            return "\n\n---\n\n".join(parts)

        llm = ChatLiteLLM(model=self._chat_model, temperature=0.3)
        agent = create_react_agent(llm, [search_knowledge_base])

        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": question}]}
        )
        last = result["messages"][-1]
        return last.content if hasattr(last, "content") else str(last)

    async def _fallback_answer(self, question: str, researcher_id: str | None) -> str:
        """Direct RAG answer without LangGraph (used if langgraph unavailable)."""
        hits = await self.query(question, n_results=5, researcher_id=researcher_id)
        if not hits:
            return "I don't have enough indexed papers to answer that question yet. Try triggering a search first."

        context = "\n\n".join(
            f"[{h['title']}]\n{h['abstract_snippet']}" for h in hits
        )
        prompt = (
            f"You are a plant biology research assistant. "
            f"Answer the following question based on these paper abstracts:\n\n"
            f"{context}\n\n"
            f"Question: {question}"
        )
        response = await litellm.acompletion(
            model=self._chat_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.3,
        )
        return response.choices[0].message.content
