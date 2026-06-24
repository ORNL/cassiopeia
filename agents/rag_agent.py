# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

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
Embeddings use ``all-MiniLM-L6-v2`` via ChromaDB's built-in ONNX runtime.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, TypedDict

import aiohttp
import litellm

litellm.drop_params = True

from academy.agent import Agent, action

from utils.json_utils import parse_json_response
from utils.persistence import PaperStore
from utils.rag_store import RAGStore
from utils.source_fetchers import _session as _aiohttp_session

logger = logging.getLogger(__name__)


def _proposal_id(suggestion: str) -> str:
    """Stable 16-char hex ID derived from the suggestion text."""
    return hashlib.sha256(suggestion.encode()).hexdigest()[:16]


# Bump this when the proposal dict shape changes in a breaking way:
#   1 — original shape (key_insights: [{paper, insight}], no verification)
#   2 — Augmentation A (key_insights: [{paper_id, insight}], +verification)
#   3 — Augmentation D (+critique, when with_critique=True)
_PROPOSAL_SCHEMA_VERSION = 3

# Augmentation C — iterative gather-evidence constants
_MAX_ITERATIONS = 3
_MAX_SUB_QUERIES_PER_ITERATION = 3
_SUB_QUERY_TOP_K = 5


_COMBINATIONS_PROMPT = """\
You are a plant biology research strategist.

Researcher profile:
  Species  : {species}
  Stresses : {stresses}
  Methods  : {methods}
  Keywords : {keywords}

PRIORITY: focus your proposals on the species and stress types listed above. \
Proposals that directly involve those organisms and conditions will be most \
useful to this researcher. Use the keywords as additional lens — proposals \
that combine the researcher's focus species/stresses with insights related \
to those keywords are especially valuable.

{preference_block}\
Below are abstracts from papers retrieved for this researcher. Each paper \
header shows its exact paper_id — use these IDs when citing papers.
Your task is to identify CROSS-PAPER synergies — novel experiment designs \
that combine findings, methods, or observations from MULTIPLE papers above.
Do NOT just paraphrase a single paper.

CITATION RULE — rationale field:
In "rationale", every factual claim attributed to prior work must be followed \
by a tag of the form [paper_id] referring to one of the supporting papers. \
Claims about what the *proposed* experiment would discover or test do NOT need \
tags — only claims about what is already known. If a claim cannot be tied to a \
specific paper in the provided set, do not make it.

Example rationale:
"Cd uptake in Brassica napus is dose-dependent up to 50 µM [P_a3f2], but VNIR \
red-edge shifts have only been characterised at lower concentrations [P_91bc]. \
Combining these would test whether reflectance saturates above the linear uptake range."

KEY INSIGHTS RULE:
"key_insights" must be a list where each entry is a dict with exactly two keys: \
"paper_id" (the exact ID from the paper header) and "insight" \
(one sentence stating the specific finding from that paper). There must be \
exactly one entry per supporting paper, and each insight must be a finding \
from that specific paper — not a synthesis across papers.

Return a JSON object with a "proposals" array of {n_proposals} items:
{{
  "proposals": [
    {{
      "theme": "<2-4 word label, e.g. 'root-canopy coupling'>",
      "suggestion": "<one concrete experiment proposal, 1-2 sentences>",
      "rationale": "<why combining these papers is promising, with [paper_id] tags on factual claims>",
      "key_insights": [
        {{"paper_id": "<exact paper_id from header>", "insight": "<specific finding from that paper, 1 sentence>"}},
        ...
      ],
      "supporting_papers": ["<exact paper_id from header>", ...]
    }}
  ]
}}

Paper abstracts:
{context}
"""

_CONTRADICTION_PROMPT = """\
You are a critical plant biology reviewer.

Below are abstracts from {n} papers retrieved for the same researcher profile.
Each paper is labelled with its exact paper_id on the header line.
Identify pairs or groups of papers that appear to present CONTRADICTORY or \
CONFLICTING findings — e.g. opposite effects of a treatment, disagreements \
about a mechanism, or incompatible quantitative claims.

Return a JSON object:
{{
  "contradictions": [
    {{
      "papers": ["<exact paper_id from header>", "<exact paper_id from header>"],
      "claim_a": "<what the first paper asserts, 1 sentence>",
      "claim_b": "<what the second paper asserts that contradicts it, 1 sentence>",
      "resolution_hint": "<possible explanation for the discrepancy, e.g. species/condition difference, 1 sentence>"
    }}
  ]
}}

Use ONLY the paper_id values shown in the headers — do not invent or paraphrase them.
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

_GAP_IDENTIFICATION_PROMPT = """\
You are reviewing draft experiment proposals to identify what additional \
evidence from the literature would strengthen them.

Draft proposals:
{draft_proposals_bullets}

Sub-queries already run (do NOT repeat these or close paraphrases):
{sub_queries_run_bullets}

Reply with strict JSON only, no preamble, no code fences:
{{
  "sub_queries": ["<short search-query string>", ...],
  "done": true,
  "reasoning": "<one sentence>"
}}

Rules:
- Each sub-query should target a specific factual gap, not a vague topic.
- Maximum {max_sub_queries} sub-queries. Often 0 or 1 is correct.
- Set "done": true if the proposals are well-grounded and no targeted sub-query \
would meaningfully improve them. In that case "sub_queries" must be [].
- Set "done": false only if at least one sub-query is provided.
"""

_REFINEMENT_ADDENDUM = """\

You have already drafted the following proposals in a previous pass. Review them \
in light of the additional papers above and refine, expand, or replace them. \
You may keep a proposal unchanged if it is already well-grounded.

Previous draft proposals:
{previous_proposals}
"""


class SynthesisState(TypedDict):
    """State for the Augmentation C iterative synthesis graph."""
    profile: dict                   # researcher profile: species, stresses, methods, keywords
    initial_papers: list[dict]      # top-N from initial retrieval: {paper_id, document}
    additional_papers: list[dict]   # accumulated across sub-queries, deduped
    draft_proposals: list[dict]     # best proposals from most recent propose step
    sub_queries_run: list[str]      # sub-queries already executed (avoid repeats)
    pending_sub_queries: list[str]  # sub-queries from identify_gaps, to run in retrieve
    iteration: int                  # 0-indexed; incremented by _propose_node
    done: bool                      # True when LLM signals satisfaction or cap hit
    liked_proposals: list[dict]     # passthrough from caller for steering
    researcher_id: str              # for researcher-scoped ChromaDB queries
    max_iterations: int             # hard cap, from caller
    n_proposals: int                # how many proposals to request from the LLM


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
    ) -> None:
        super().__init__()

        _db = db_path or os.environ.get("DB_PATH") or str(Path(__file__).parent.parent / "cassiopeia.db")
        _rag_dir = rag_persist_dir or os.environ.get("RAG_PERSIST_DIR") or str(Path(__file__).parent.parent / "chroma_db")

        self._store = PaperStore(_db)
        self._rag = RAGStore(persist_dir=_rag_dir)
        self._chat_model = os.environ["LLM_CHAT_MODEL"]

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
        hits = self._rag_query(text, n_results, researcher_id)

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

    def _rag_query(
        self, text: str, n_results: int, researcher_id: str | None = None
    ) -> list[dict]:
        """Query ChromaDB, scoping to researcher when given.

        Single point of entry for all RAG retrieval so augmentation B can
        add chunk-level deduplication here without touching every caller.
        """
        where = {"researcher_id": researcher_id} if researcher_id else None
        return self._rag.query(text, n_results=n_results, where=where)

    def _format_context_blocks(self, papers: list[dict], cap: int = 6000) -> str:
        """Format a list of {paper_id, document} dicts into numbered LLM context blocks.

        Each block header includes paper_id so the LLM can use it in [paper_id]
        citation tags. ``cap`` limits characters per paper (use a lower value for
        passes that scan many papers, e.g. contradiction detection at 500).
        """
        blocks = []
        for i, p in enumerate(papers, 1):
            paper_id = p["paper_id"]
            meta = self._store.get_paper_metadata(paper_id) or {}
            title = meta.get("title", "Unknown")
            blocks.append(
                f"[Paper {i} | paper_id: {paper_id}] {title}\n{p['document'][:cap]}"
            )
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
            max_tokens=4000,
            response_format={"type": "json_object"},
            temperature=0.4,
            timeout=180,
        )
        raw = response.choices[0].message.content.strip()
        return parse_json_response(raw).get("proposals", [])

    @action
    async def synthesize_combinations(
        self,
        researcher_id: str,
        species: list[str],
        stresses: list[str],
        methods: list[str],
        keywords: list[str] | None = None,
        n_papers: int = 12,
        n_proposals: int = 5,
        liked_proposals: list[dict] | None = None,
        with_critique: bool = False,
        instruments: list[str] | None = None,
        max_iterations: int = _MAX_ITERATIONS,
    ) -> list[dict[str, Any]]:
        """Generate cross-paper experiment proposals by reasoning over multiple abstracts.

        Unlike the per-paper hypotheses produced during scoring, this action:
        1. Ensures ChromaDB is up to date (calls ``index_new_papers`` first)
        2. Retrieves the ``n_papers`` most relevant abstracts from ChromaDB
        3. Sends them **all together** in a single LLM call, asking for novel
           multi-paper experiment proposals that combine findings across papers
        4. Checks each proposal for novelty against already-liked proposals
        5. Verifies key_insights against source abstracts
        6. Optionally runs a critic pass over each proposal

        Args:
            researcher_id: Researcher to scope results to.
            species: Plant species of interest.
            stresses: Stress types of interest.
            methods: Phenotyping methods of interest.
            keywords: Additional free-text keywords.
            n_papers: Number of abstracts to retrieve from ChromaDB.
            liked_proposals: Previously liked proposals to avoid duplicating
                and to steer the LLM towards unexplored territory.
            with_critique: When True, runs ``critique_proposals`` (Augmentation D)
                before returning. Default False — existing callers are unaffected.
            instruments: Available facility instruments, forwarded to the critic
                for feasibility_concerns assessment. Only used when with_critique=True.
            max_iterations: Number of propose → identify_gaps → retrieve iterations. 
                ``0`` reverts to the pre-C single-shot behavior (one LLM_CHAT_MODEL
                call, no gap-finding loop). Default: ``_MAX_ITERATIONS`` (3).

        Returns:
            List of proposal dicts. Check ``schema_version`` to know the shape:

            * **v3** (current) — all v2 fields plus ``critique`` when
              ``with_critique=True`` (None if the critic call failed).
            * **v2** — ``proposal_id``, ``theme``, ``suggestion``,
              ``rationale`` (with ``[paper_id]`` citation tags),
              ``key_insights`` (list of ``{paper_id, insight}`` dicts),
              ``supporting_papers``, ``novelty_warning``, ``verification``.
            * **v1** (legacy, no longer produced) — same minus ``verification``,
              ``key_insights`` used ``{paper, insight}`` dicts.
        """
        await self.index_new_papers()

        if self._rag.count() == 0:
            return []

        query_text = " ".join(species + stresses + methods + (keywords or []))
        hits = self._rag_query(query_text, n_papers, researcher_id)
        if not hits:
            return []

        paper_text_by_id: dict[str, str] = {h["paper_id"]: h["document"] for h in hits}
        profile = {
            "species": species,
            "stresses": stresses,
            "methods": methods,
            "keywords": keywords or [],
        }

        if max_iterations == 0:
            proposals = await self._single_shot_proposals(
                hits, species, stresses, methods, keywords, liked_proposals, n_proposals,
            )
        else:
            proposals, extra = await self._iterative_proposals(
                hits, profile, liked_proposals, researcher_id, max_iterations, n_proposals,
            )
            for p in extra:
                paper_text_by_id.setdefault(p["paper_id"], p["document"])

        if proposals is None:
            return []
        enriched = await self._enrich_proposals(proposals, researcher_id)
        return await self._annotate_proposals(
            enriched, paper_text_by_id, with_critique, instruments or []
        )

    async def _single_shot_proposals(
        self,
        hits: list[dict],
        species: list[str],
        stresses: list[str],
        methods: list[str],
        keywords: list[str] | None,
        liked_proposals: list[dict] | None,
        n_proposals: int,
    ) -> list[dict] | None:
        prompt = _COMBINATIONS_PROMPT.format(
            species=", ".join(species) or "unspecified",
            stresses=", ".join(stresses) or "unspecified",
            methods=", ".join(methods) or "unspecified",
            keywords=", ".join(keywords or []) or "none",
            context=self._format_context_blocks(hits),
            preference_block=self._build_preference_block(liked_proposals),
            n_proposals=n_proposals,
        )
        try:
            return await self._llm_proposals(prompt)
        except (litellm.APIError, json.JSONDecodeError) as exc:
            logger.warning("synthesize_combinations (single-shot) failed: %s", exc)
            return None

    async def _iterative_proposals(
        self,
        hits: list[dict],
        profile: dict,
        liked_proposals: list[dict] | None,
        researcher_id: str,
        max_iterations: int,
        n_proposals: int,
    ) -> tuple[list[dict] | None, list[dict]]:
        """Returns (proposals | None, additional_papers)."""
        initial_papers = [{"paper_id": h["paper_id"], "document": h["document"]} for h in hits]
        try:
            final_state = await self._run_synthesis_graph(
                profile=profile,
                initial_papers=initial_papers,
                liked_proposals=liked_proposals or [],
                researcher_id=researcher_id,
                max_iterations=max_iterations,
                n_proposals=n_proposals,
            )
            return final_state["draft_proposals"], final_state["additional_papers"]
        except Exception as exc:
            logger.warning("synthesize_combinations (iterative) failed: %s", exc)
            return None, []

    async def _annotate_proposals(
        self,
        proposals: list[dict],
        paper_text_by_id: dict[str, str],
        with_critique: bool,
        instruments: list[str],
    ) -> list[dict]:
        """Attach verification (Aug A) and optionally critique (Aug D) to enriched proposals.

        Each step degrades gracefully on failure — proposals are still returned
        without the missing field rather than propagating an exception.
        """
        try:
            verifications = await asyncio.gather(
                *[self._verify_proposal_claims(p, paper_text_by_id) for p in proposals]
            )
            for proposal, verification in zip(proposals, verifications):
                proposal["verification"] = verification
        except Exception as exc:
            logger.warning("Verification failed — proposals returned without it: %s", exc)

        if with_critique:
            try:
                proposals = await self.critique_proposals(proposals, instruments)
            except Exception as exc:
                logger.warning("Critique failed — proposals returned without it: %s", exc)

        return proposals

    async def _enrich_proposals(
        self, proposals: list[dict], researcher_id: str
    ) -> list[dict[str, Any]]:
        """Attach proposal_id, schema_version, theme, and novelty_warning to raw LLM proposals."""
        results = []
        for p in proposals:
            suggestion = p.get("suggestion", "")
            if not suggestion:
                continue
            is_novel, novelty_warning = await self._check_novelty(suggestion, researcher_id)
            results.append(
                {
                    "schema_version": _PROPOSAL_SCHEMA_VERSION,
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

    async def _verify_one_claim(
        self,
        paper_id: str,
        insight: str,
        paper_text: str,
    ) -> dict:
        """Verify one key_insight against its source paper, with SQLite caching.

        Cache key: "{paper_id}::{sha256(insight)}" — unique per (paper, insight) pair.
        On cache hit the LLM is not called. On failure returns a null entry that
        does not count toward the flagging threshold.
        """
        from utils.llm_verifier import verify_claim

        insight_hash = hashlib.sha256(insight.encode()).hexdigest()
        cache_key = f"{paper_id}::{insight_hash}"

        cached = self._store.get_verify_cache(cache_key)
        if cached is not None:
            return {"claim": insight, "paper_id": paper_id, **cached}

        result = await verify_claim(paper_text, insight)
        self._store.set_verify_cache(cache_key, result)
        return {"claim": insight, "paper_id": paper_id, **result}

    async def _verify_proposal_claims(
        self,
        proposal: dict,
        paper_text_by_id: dict[str, str],
    ) -> dict:
        """Verify each key_insight against its source paper concurrently.

        Returns the verification dict to attach to the proposal. Null entries
        (infrastructure failures) are excluded from the flagging ratio so they
        do not look like hallucinations.

        Reusable from Augmentation D.
        """
        key_insights = proposal.get("key_insights", [])
        if not key_insights:
            return {
                "checked_claims": 0,
                "supported": 0,
                "unsupported": 0,
                "flagged": False,
                "details": [],
            }

        tasks = [
            self._verify_one_claim(
                ki.get("paper_id", ""),
                ki.get("insight", ""),
                paper_text_by_id.get(ki.get("paper_id", ""), ""),
            )
            for ki in key_insights
        ]
        details = list(await asyncio.gather(*tasks))

        supported_count = sum(1 for d in details if d["supported"] is True)
        unsupported_count = sum(1 for d in details if d["supported"] is False)
        checked_claims = supported_count + unsupported_count
        flagged = (
            (unsupported_count / max(1, checked_claims)) > (1 / 3)
            if checked_claims > 0
            else False
        )

        return {
            "checked_claims": checked_claims,
            "supported": supported_count,
            "unsupported": unsupported_count,
            "flagged": flagged,
            "details": details,
        }

    @action
    async def critique_proposals(
        self,
        proposals: list[dict[str, Any]],
        instruments: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Annotate proposals with structured critique from a critic LLM (Augmentation D).

        One LLM_CHAT_MODEL call per proposal, run concurrently via ``asyncio.gather``.
        Each proposal is annotated with a ``critique`` dict covering:

        - ``novelty`` — is the experiment genuinely novel, with a semantic search
          for the closest prior work in ChromaDB?
        - ``confounds`` — specific experimental confounds and their severity.
        - ``evidence_strength`` — does the rationale stretch beyond what the cited
          papers actually show?
        - ``feasibility_concerns`` — practical concerns *beyond* instrument
          availability (sample size, time horizon, statistical power, ethics).
          Instrument-level feasibility is handled separately by ``assess_feasibility``.
        - ``overall_recommendation`` — pursue / refine / deprioritize.
        - ``summary`` — one-sentence overview of the critique.

        If the LLM call for a proposal fails, that proposal's ``critique`` is None.

        Args:
            proposals: List of proposal dicts from ``synthesize_combinations``.
                Should already have ``verification`` attached.
            instruments: Available facility instruments for feasibility assessment.
        """
        _instruments = instruments or []
        critiques = await asyncio.gather(
            *[self._critique_one_proposal(p, _instruments) for p in proposals]
        )
        return [{**p, "critique": c} for p, c in zip(proposals, critiques)]

    async def _critique_one_proposal(
        self,
        proposal: dict,
        instruments: list[str],
    ) -> dict | None:
        """Retrieve semantically similar papers, then run one critique call."""
        from utils.llm_critic import critique_proposal

        suggestion = proposal.get("suggestion", "")
        similar_hits = self._rag_query(suggestion, 5) if suggestion else []

        similar_papers = []
        for hit in similar_hits:
            meta = self._store.get_paper_metadata(hit["paper_id"]) or {}
            similar_papers.append({
                "paper_id": hit["paper_id"],
                "title": meta.get("title", ""),
                "document": hit["document"][:500],
            })

        return await critique_proposal(proposal, similar_papers, instruments)


    async def _propose_node(self, state: dict) -> dict:
        """Propose step: one LLM_CHAT_MODEL call to generate or refine draft proposals."""
        profile = state["profile"]
        all_papers = state["initial_papers"] + state["additional_papers"]
        context = self._format_context_blocks(all_papers)
        preference_block = self._build_preference_block(state["liked_proposals"])

        refinement_block = ""
        if state["iteration"] > 0 and state["draft_proposals"]:
            prev = "\n".join(
                f"  - [{p.get('theme', '')}] {p.get('suggestion', '')}"
                for p in state["draft_proposals"]
            )
            refinement_block = _REFINEMENT_ADDENDUM.format(previous_proposals=prev)

        prompt = _COMBINATIONS_PROMPT.format(
            species=", ".join(profile.get("species", [])) or "unspecified",
            stresses=", ".join(profile.get("stresses", [])) or "unspecified",
            methods=", ".join(profile.get("methods", [])) or "unspecified",
            keywords=", ".join(profile.get("keywords", [])) or "none",
            context=context,
            preference_block=preference_block,
            n_proposals=state.get("n_proposals", 5),
        ) + refinement_block

        try:
            proposals = await self._llm_proposals(prompt)
        except (litellm.APIError, json.JSONDecodeError) as exc:
            logger.warning("_propose_node failed: %s", exc)
            proposals = state["draft_proposals"]  # keep previous draft on failure

        return {**state, "draft_proposals": proposals, "iteration": state["iteration"] + 1}

    async def _identify_gaps_node(self, state: dict) -> dict:
        """Gap identification step: one LLM_SCORING_MODEL call to find evidence gaps in draft proposals."""
        proposals_bullets = "\n".join(
            f"  - [{p.get('theme', '')}] {p.get('suggestion', '')}"
            for p in state["draft_proposals"]
        ) or "  (none yet)"
        queries_bullets = (
            "\n".join(f"  - {q}" for q in state["sub_queries_run"])
            if state["sub_queries_run"] else "  (none yet)"
        )

        prompt = _GAP_IDENTIFICATION_PROMPT.format(
            draft_proposals_bullets=proposals_bullets,
            sub_queries_run_bullets=queries_bullets,
            max_sub_queries=_MAX_SUB_QUERIES_PER_ITERATION,
        )
        scoring_model = os.environ["LLM_SCORING_MODEL"]

        try:
            response = await litellm.acompletion(
                model=scoring_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                response_format={"type": "json_object"},
                temperature=0.0,
                timeout=60,
            )
            raw = response.choices[0].message.content.strip()
            data = parse_json_response(raw)
            sub_queries = [str(q) for q in data.get("sub_queries", [])][:_MAX_SUB_QUERIES_PER_ITERATION]
            done = bool(data.get("done", False))
            if not sub_queries:  # defensive: done: false but no queries → treat as done
                done = True
        except (litellm.APIError, json.JSONDecodeError) as exc:
            logger.warning("_identify_gaps_node failed: %s", exc)
            sub_queries = []
            done = True  # fail safe: don't loop on error

        return {**state, "pending_sub_queries": sub_queries, "done": done}

    async def _retrieve_node(self, state: dict) -> dict:
        """Retrieval step: run pending sub-queries against ChromaDB, dedupe, accumulate."""
        existing_ids = (
            {p["paper_id"] for p in state["initial_papers"]}
            | {p["paper_id"] for p in state["additional_papers"]}
        )
        rid = state["researcher_id"]
        new_papers: list[dict] = []
        for query in state["pending_sub_queries"]:
            hits = self._rag_query(query, _SUB_QUERY_TOP_K, rid)
            for hit in hits:
                if hit["paper_id"] not in existing_ids:
                    existing_ids.add(hit["paper_id"])
                    new_papers.append({"paper_id": hit["paper_id"], "document": hit["document"]})

        done = len(new_papers) == 0  # no new evidence → no point re-proposing

        return {
            **state,
            "additional_papers": state["additional_papers"] + new_papers,
            "sub_queries_run": state["sub_queries_run"] + state["pending_sub_queries"],
            "pending_sub_queries": [],
            "done": done,
        }

    async def _finalize_node(self, state: dict) -> dict:
        """Finalize step: passthrough. Downstream enrichment reads draft_proposals."""
        return state

    async def _run_synthesis_graph(
        self,
        *,
        profile: dict,
        initial_papers: list[dict],
        liked_proposals: list[dict],
        researcher_id: str,
        max_iterations: int,
        n_proposals: int = 5,
    ) -> dict:
        """Build and run the Augmentation C iterative synthesis StateGraph.

        Returns the final state dict. Caller reads ``draft_proposals`` and
        ``additional_papers`` to extend ``paper_text_by_id`` for verification.

        Reuses the same ChromaDB query tool as ``synthesize`` — ``_retrieve_node``
        calls ``self._rag.query()`` directly rather than building a parallel path.
        """
        from langgraph.graph import StateGraph, END

        def _after_gaps(s: dict) -> str:
            if s["done"] or not s["pending_sub_queries"] or s["iteration"] >= s["max_iterations"]:
                return "finalize"
            return "retrieve"

        def _after_retrieve(s: dict) -> str:
            return "finalize" if s["done"] else "propose"

        graph: StateGraph = StateGraph(SynthesisState)
        graph.add_node("propose", self._propose_node)
        graph.add_node("identify_gaps", self._identify_gaps_node)
        graph.add_node("retrieve", self._retrieve_node)
        graph.add_node("finalize", self._finalize_node)

        graph.set_entry_point("propose")
        graph.add_edge("propose", "identify_gaps")
        graph.add_conditional_edges("identify_gaps", _after_gaps)
        graph.add_conditional_edges("retrieve", _after_retrieve)
        graph.add_edge("finalize", END)

        compiled = graph.compile()

        initial_state: SynthesisState = {
            "profile": profile,
            "initial_papers": initial_papers,
            "additional_papers": [],
            "draft_proposals": [],
            "sub_queries_run": [],
            "pending_sub_queries": [],
            "iteration": 0,
            "done": False,
            "liked_proposals": liked_proposals,
            "researcher_id": researcher_id,
            "max_iterations": max_iterations,
            "n_proposals": n_proposals,
        }
        return await compiled.ainvoke(initial_state)

    async def _check_novelty(
        self, suggestion: str, researcher_id: str
    ) -> tuple[bool, str]:
        """Check whether a proposal is genuinely novel vs. already-indexed content.

        Returns ``(is_novel, warning_message)``.  ``is_novel`` is False when a
        very similar abstract already exists in ChromaDB (distance < 0.3).
        """
        hits = self._rag_query(suggestion, 1, researcher_id)
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
                max_tokens=600,
                response_format={"type": "json_object"},
                temperature=0.1,
                timeout=120,
            )
            raw = response.choices[0].message.content.strip()
            return parse_json_response(raw)
        except (litellm.APIError, json.JSONDecodeError) as exc:
            logger.warning("assess_feasibility failed for proposal: %s", exc)
            return {
                "feasible": None,
                "confidence": 0.0,
                "missing_equipment": [],
                "adaptation": "",
                "note": "Assessment unavailable.",
            }

    async def _contradiction_pass(
        self, query: str, n_papers: int, where: dict
    ) -> list[dict]:
        """Run one contradiction-detection LLM call over papers matching ``query``."""
        hits = self._rag.query(query, n_results=n_papers, where=where)
        if len(hits) < 2:
            return []

        # Label each block with the paper_id so the LLM can reference it exactly.
        id_to_meta: dict[str, dict] = {}
        context_parts: list[str] = []
        for h in hits:
            pid = h["paper_id"]
            meta = self._store.get_paper_metadata(pid) or {}
            id_to_meta[pid] = {
                "title": meta.get("title") or pid,
                "doi":   meta.get("doi"),
                "url":   meta.get("url"),
            }
            context_parts.append(
                f"[paper_id: {pid}] {id_to_meta[pid]['title']}\n{h['document'][:500]}"
            )
        context = "\n\n".join(context_parts)

        try:
            response = await litellm.acompletion(
                model=self._chat_model,
                messages=[{"role": "user", "content": _CONTRADICTION_PROMPT.format(n=len(hits), context=context)}],
                max_tokens=1200,
                response_format={"type": "json_object"},
                temperature=0.2,
                timeout=150,
            )
            raw = response.choices[0].message.content.strip()
            contradictions = parse_json_response(raw).get("contradictions", [])
        except (litellm.APIError, json.JSONDecodeError) as exc:
            logger.warning("detect_contradictions pass '%s' failed: %s", query, exc)
            return []

        # Replace the raw paper_id list with full metadata for the frontend.
        for c in contradictions:
            c["paper_meta"] = [
                id_to_meta[pid]
                for pid in c.get("papers", [])
                if pid in id_to_meta
            ]
            # Keep papers as human-readable titles (for filters / legacy display).
            c["papers"] = [m["title"] for m in c["paper_meta"]]
        return contradictions

    @action
    async def detect_contradictions(
        self,
        researcher_id: str,
        n_papers_per_pass: int = 20,
        n_passes: int = 3,
    ) -> list[dict[str, Any]]:
        """Find conflicting claims across the researcher's paper corpus.

        Runs ``n_passes`` LLM calls, each over a different semantically-focused
        slice of ``n_papers_per_pass`` abstracts, then deduplicates the results.
        Each pass uses a distinct query term derived from the researcher profile
        so different parts of the corpus are sampled.

        Returns a list of dicts with keys:
        ``papers``, ``claim_a``, ``claim_b``, ``resolution_hint``.
        """
        await self.index_new_papers()

        if self._rag.count() == 0:
            return []

        profile  = self._store.load_profile(researcher_id) or {}
        terms    = (
            list(profile.get("plant_species", []))
            + [s.replace("_", " ") for s in profile.get("stress_types", [])]
            + profile.get("expertise_keywords", [])[:4]
        ) or ["plant biology stress response"]
        queries  = (terms * n_passes)[:n_passes]
        where    = {"researcher_id": researcher_id}

        seen_pairs: set[frozenset] = set()
        all_contradictions: list[dict] = []
        for query in queries:
            for c in await self._contradiction_pass(query, n_papers_per_pass, where):
                pair = frozenset(c.get("papers", []))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    all_contradictions.append(c)

        return all_contradictions

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

        hits = self._rag_query(abstract, n_results, researcher_id)

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
            async with _aiohttp_session() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return ""
                    data = await resp.json()
            results = data.get("resultList", {}).get("result", [])
            if not results:
                return ""
            return results[0].get("abstractText") or results[0].get("title", "")
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
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
            "embedding_model": "all-MiniLM-L6-v2 (ONNX)",
            "chat_model": self._chat_model,
        }

    async def _run_react(self, question: str, researcher_id: str | None) -> str:
        """Run LangGraph create_react_agent with ChromaDB tool."""
        from langchain_core.tools import tool as lc_tool
        from langchain_community.chat_models import ChatLiteLLM
        from langgraph.prebuilt import create_react_agent

        @lc_tool
        def search_knowledge_base(query: str) -> str:
            """Search the plant biology paper knowledge base for relevant passages."""
            hits = self._rag_query(query, 5, researcher_id)
            if not hits:
                return "No relevant papers found."
            parts = []
            for hit in hits:
                meta = self._store.get_paper_metadata(hit["paper_id"]) or {}
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
            max_tokens=800,
            temperature=0.3,
            timeout=120,
        )
        return response.choices[0].message.content
