# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Chainlit conversational interface for Cassiopeia.

Architecture
------------
- Intent classification (LangGraph): routes first message to profile / qa / anchor / general.
- Profile collection (Python state machine): drives 3 stages + confirmation.
  The LLM is only used for (a) one-sentence acknowledgment and (b) JSON extraction.
  It CANNOT generate paper lists or skip stages.
- Search execution (Python): calls /api/search directly.
- QA / anchor: delegate to /api/rag/synthesize and /api/anchor_search.

Run alongside the API server:
    uvicorn api_server:app --port 8000
    chainlit run chainlit_app.py --port 8001
"""

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Annotated, TypedDict
from urllib.parse import parse_qs, urlparse

import chainlit as cl
import httpx
import litellm
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).parent / ".env")

litellm.suppress_debug_info = True
litellm.drop_params = True

_MODEL = os.environ.get("LLM_CHAT_MODEL", "anthropic/claude-haiku-4-5-20251001")
_API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")
_DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5173")


# ──────────────────────────────────────────────────────────────────────────────
# Prompts (LangGraph nodes only — profile collection is pure Python)
# ──────────────────────────────────────────────────────────────────────────────

_CLASSIFY_SYSTEM = """\
You are a routing classifier for an automated plant biology literature search tool.
Classify the user's latest message into exactly one of:

  "search"  — mentions plant species, stress conditions, research topics, or
               anything related to plant biology literature, OR wants to run a search.
               When in doubt, prefer "search" over "general".

  "anchor"  — wants to find papers SIMILAR TO a specific paper, identified by
               DOI or title. Phrases: "similar to", "papers like", "anchor on".

  "qa"      — asks about papers ALREADY retrieved in this conversation.

  "general" — ONLY clear off-topic messages: greetings, thanks, unrelated questions.

Reply with ONLY the single word (no punctuation, no explanation).
"""

_ANCHOR_EXTRACT_SYSTEM = """\
Extract the anchor paper identifier from the user's message.
The identifier is a DOI (e.g. "10.1016/j.celrep.2023.112345") or a paper title.
Return ONLY JSON: {"doi_or_title": "..."}
If no clear identifier, return: {"doi_or_title": ""}
"""

_GENERAL_SYSTEM = """\
You are the conversational front-end of the APPL Literature Mining tool.
All actual searches are performed by a backend pipeline — you CANNOT retrieve,
list, or summarise papers yourself.

Rules (never break them):
- NEVER list, name, or describe any papers, authors, journals, or search results.
- NEVER pretend to run a search or claim one is in progress.
- If the user mentions species, stresses, or research topics, tell them to say
  something like "run a search" or "set up a search" so the system can collect
  their profile and trigger the real pipeline.
- For greetings or off-topic messages, reply in one or two sentences and redirect
  towards running a literature scan.
"""


# ──────────────────────────────────────────────────────────────────────────────
# Profile collection — any-order, single combined extractor
# ──────────────────────────────────────────────────────────────────────────────

# Human-readable labels for each topic
_TOPIC_LABELS = {
    "s1": "Plant species & stress conditions",
    "s2": "Research keywords & time range",
    "s3": "Literature sources & scoring priorities",
    "s4": "Anchor paper",
}

# Questions shown for each topic when it is still pending
_TOPIC_QUESTIONS = {
    "s1": (
        "Which **plant species** do you work with, and what **stress conditions** "
        "are you interested in?\n"
        "**Species:** poplar · arabidopsis · soybean · sorghum · switchgrass · "
        "miscanthus · pennycress · brachypodium\n"
        "**Stresses:** drought · nutrient · temperature · pathogen · heavy_metal · "
        "salinity · light · flooding"
    ),
    "s2": (
        "What are **2–4 research keywords** for your specific niche "
        "*(e.g. root architecture, nitrogen uptake, canopy reflectance)*"
        " — and how far back should we search? *(default: last 12 months)*"
    ),
    "s3": (
        "Which **literature sources** to include?\n"
        "**Open access:** pubmed · biorxiv · plos_one · frontiers · arxiv\n"
        "**Paywalled:** nature_communications · new_phytologist · plant_physiology\n"
        "*(say 'all' for all sources)*\n"
        "How should papers be **scored**? *(0.0–1.0, default 0.5)*: "
        "novelty · relevance · methodology · reproducibility"
    ),
    "s4": (
        "Do you want to **anchor on a specific paper**? "
        "Share a DOI or title and the pipeline will also retrieve semantically similar papers.\n"
        "*(say 'skip' or 'no' to proceed without an anchor)*"
    ),
}

_ALL_TOPICS = ("s1", "s2", "s3", "s4")

# One combined extractor — null means "not mentioned in this message"
_FULL_EXTRACT_PROMPT = """\
Extract research profile fields from the researcher's message.
Return ONLY a JSON object. Use null for any field NOT explicitly mentioned — never invent defaults.

{
  "plant_species": null or list of species names,
  "stress_types":  null or list using ONLY: drought, nutrient, temperature, pathogen,
                   heavy_metal, salinity, light, flooding,
  "expertise_keywords": null or list of 2-6 short keyword phrases,
  "time_range_months":  null or integer (months to look back, e.g. 12),
  "source_targets": null or list using ONLY: pubmed, biorxiv, plos_one, frontiers, arxiv,
                    nature_communications, new_phytologist, plant_physiology.
                    Use [] (empty list) if the user says "all" or "any",
  "priority_novelty":         null or float 0.0-1.0 (high=0.8, medium=0.5, low=0.3),
  "priority_relevance":       null or float 0.0-1.0,
  "priority_methodology":     null or float 0.0-1.0,
  "priority_reproducibility": null or float 0.0-1.0,
  "anchor_paper": null if not mentioned,
                  "" (empty string) if the user says skip/no/none,
                  otherwise the DOI or paper title string
}"""

_CORRECTION_EXTRACT = """\
The researcher wants to correct their search profile. Extract only what changed.
Return ONLY JSON with the fields to update (omit unchanged fields).
Valid fields: plant_species (list), stress_types (list), expertise_keywords (list),
source_targets (list), time_range_months (int), priority_novelty (float),
priority_relevance (float), priority_methodology (float), priority_reproducibility (float),
anchor_paper (string — empty string means no anchor).
stress_types values: drought, nutrient, temperature, pathogen, heavy_metal, salinity, light, flooding
source_targets values: pubmed, biorxiv, plos_one, frontiers, arxiv,
  nature_communications, new_phytologist, plant_physiology. Empty list = all sources."""

_ACK_SYSTEM = """\
Acknowledge what the researcher just said in ONE short sentence (max 12 words).
Do NOT ask questions. Do NOT mention papers, results, databases, or searches."""


async def _ack(text: str) -> str:
    """One-sentence acknowledgment of what the user said."""
    try:
        resp = await litellm.acompletion(
            model=_MODEL,
            messages=[{"role": "system", "content": _ACK_SYSTEM},
                      {"role": "user", "content": text}],
            max_tokens=40, temperature=0.5,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "Got it."


async def _extract_profile(text: str) -> dict:
    """Single combined extractor — returns null for fields not mentioned."""
    try:
        resp = await litellm.acompletion(
            model=_MODEL,
            messages=[{"role": "system", "content": _FULL_EXTRACT_PROMPT},
                      {"role": "user", "content": text}],
            max_tokens=300, temperature=0.0,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as exc:
        logger.warning("Profile extraction failed: %s", exc)
        return {}


async def _extract_correction(text: str) -> dict:
    """Extract profile corrections from user message."""
    try:
        resp = await litellm.acompletion(
            model=_MODEL,
            messages=[{"role": "system", "content": _CORRECTION_EXTRACT},
                      {"role": "user", "content": text}],
            max_tokens=200, temperature=0.0,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as exc:
        logger.warning("Correction extraction failed: %s", exc)
        return {}


def _topics_covered_by(extracted: dict) -> set[str]:
    """Return which topics were addressed in a single extraction result."""
    covered = set()
    if extracted.get("plant_species") is not None or extracted.get("stress_types") is not None:
        covered.add("s1")
    if (extracted.get("expertise_keywords") is not None
            or extracted.get("time_range_months") is not None):
        covered.add("s2")
    if (extracted.get("source_targets") is not None
            or any(extracted.get(f"priority_{p}") is not None
                   for p in ("novelty", "relevance", "methodology", "reproducibility"))):
        covered.add("s3")
    if extracted.get("anchor_paper") is not None:
        covered.add("s4")
    return covered


def _merge_into_profile(profile: dict, extracted: dict) -> None:
    """Update profile in-place with non-null fields from extraction."""
    for k, v in extracted.items():
        if v is None:
            continue
        # Guard against nonsensical zero values that indicate the LLM
        # returned a default instead of null for an unspecified field.
        if k == "time_range_months" and v == 0:
            continue
        profile[k] = v


def _pending_questions(covered: set) -> str:
    """Return formatted questions for topics not yet covered."""
    pending = [t for t in _ALL_TOPICS if t not in covered]
    if not pending:
        return ""
    lines = []
    for t in pending:
        lines.append(f"**{_TOPIC_LABELS[t]}**\n{_TOPIC_QUESTIONS[t]}")
    return "\n\n".join(lines)


def _all_topics_prompt() -> str:
    """Full checklist shown at the start of profile collection."""
    lines = ["Please tell me about the following — you can answer all at once or one by one:\n"]
    for t in _ALL_TOPICS:
        lines.append(f"**{_TOPIC_LABELS[t]}**\n{_TOPIC_QUESTIONS[t]}")
    return "\n\n".join(lines)


def _profile_summary(profile: dict) -> str:
    """Python-generated confirmation summary (no LLM involved)."""
    species = ", ".join(profile.get("plant_species") or []) or "—"
    stresses = ", ".join(profile.get("stress_types") or []) or "—"
    keywords = ", ".join(profile.get("expertise_keywords") or []) or "—"
    sources = ", ".join(profile.get("source_targets") or []) or "all"
    months = profile.get("time_range_months") or 12  # 0 months is nonsensical, default to 12
    pn = profile.get("priority_novelty", 0.5)
    pr = profile.get("priority_relevance", 0.5)
    pm = profile.get("priority_methodology", 0.5)
    prep = profile.get("priority_reproducibility", 0.5)
    anchor = profile.get("anchor_paper") or "—"
    return (
        "Here is what I have:\n\n"
        f"• **Species**       : {species}\n"
        f"• **Stresses**      : {stresses}\n"
        f"• **Keywords**      : {keywords}\n"
        f"• **Sources**       : {sources}\n"
        f"• **Time range**    : last {months} months\n"
        f"• **Anchor paper**  : {anchor}\n\n"
        "**Shall I run this search again or do you want to start a new one?**"
    )


async def _handle_collecting(text: str) -> None:
    """Any-order profile collection: extract → merge → prompt for what's still missing."""
    profile = cl.user_session.get("pending_profile") or {}
    covered = set(cl.user_session.get("topics_covered") or [])

    extracted = await _extract_profile(text)
    newly = _topics_covered_by(extracted)
    _merge_into_profile(profile, extracted)
    covered |= newly

    cl.user_session.set("pending_profile", profile)
    cl.user_session.set("topics_covered", list(covered))

    ack = await _ack(text)

    if covered >= set(_ALL_TOPICS):
        cl.user_session.set("profile_stage", "confirm")
        await cl.Message(content=f"{ack}\n\n{_profile_summary(profile)}").send()
    else:
        remaining = _pending_questions(covered)
        await cl.Message(content=f"{ack}\n\n{remaining}").send()


async def _handle_confirm_stage(text: str, text_lower: str) -> None:
    """Handle user input at the confirmation step: confirm → search, reset → new collection,
    else apply correction."""
    if _is_confirmation(text_lower):
        cl.user_session.set("profile_stage", None)
        profile = dict(cl.user_session.get("pending_profile") or {})
        await _do_search(profile)
    elif _is_reset_request(text_lower):
        rname = cl.user_session.get("researcher_name") or "researcher"
        cl.user_session.set("profile_stage", "collecting")
        cl.user_session.set("pending_profile", {})
        cl.user_session.set("topics_covered", [])
        await cl.Message(
            content=f"Sure! Let's set up a fresh search, **{rname}**.\n\n{_all_topics_prompt()}"
        ).send()
    else:
        correction = await _extract_correction(text)
        profile = cl.user_session.get("pending_profile") or {}
        for k, v in correction.items():
            if v not in (None, []):
                profile[k] = v
        cl.user_session.set("pending_profile", profile)
        ack = await _ack(text)
        await cl.Message(content=f"{ack}\n\n{_profile_summary(profile)}").send()


# ──────────────────────────────────────────────────────────────────────────────
# LangGraph — classification and non-profile nodes only
# ──────────────────────────────────────────────────────────────────────────────

class ConvState(TypedDict):
    messages: Annotated[list, add_messages]
    mode: str  # "profile" | "qa" | "general" | "anchor"


_checkpointer = MemorySaver()


async def _classify_node(state: ConvState) -> dict:
    last = state["messages"][-1]
    text = last.content if hasattr(last, "content") else str(last)
    resp = await litellm.acompletion(
        model=_MODEL,
        messages=[{"role": "system", "content": _CLASSIFY_SYSTEM},
                  {"role": "user", "content": text}],
        max_tokens=5, temperature=0.0,
    )
    label = resp.choices[0].message.content.strip().lower()
    if label not in ("search", "anchor", "qa", "general"):
        label = "general"
    return {"mode": {"search": "profile", "anchor": "anchor",
                     "qa": "qa", "general": "general"}[label]}


def _profile_node(state: ConvState) -> dict:
    """Start profile collection — Python takes over from on_message."""
    rname = cl.user_session.get("researcher_name") or "researcher"
    cl.user_session.set("profile_stage", "collecting")
    cl.user_session.set("pending_profile", {})
    cl.user_session.set("topics_covered", [])
    return {"messages": [AIMessage(
        content=f"Let's set up your search, **{rname}**!\n\n{_all_topics_prompt()}"
    )]}


async def _qa_node(state: ConvState) -> dict:
    last = state["messages"][-1]
    question = last.content if hasattr(last, "content") else str(last)
    researcher_id = cl.user_session.get("researcher_id")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{_API_BASE}/api/rag/synthesize",
                json={"question": question, "researcher_id": researcher_id},
            )
            r.raise_for_status()
            answer = r.json().get("answer", "No answer returned.")
    except Exception as exc:
        answer = f"Could not reach the knowledge base: {exc}"
    return {"messages": [AIMessage(content=answer)]}


async def _extract_anchor_id(text: str) -> str:
    resp = await litellm.acompletion(
        model=_MODEL,
        messages=[{"role": "system", "content": _ANCHOR_EXTRACT_SYSTEM},
                  {"role": "user", "content": text}],
        max_tokens=80, temperature=0.0,
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(resp.choices[0].message.content).get("doi_or_title", "").strip()
    except Exception:
        return ""


def _format_anchor_entry(i: int, p: dict) -> list[str]:
    doi = p.get("doi")
    url = p.get("url")
    paper_url = f"https://doi.org/{doi}" if doi else url
    title = p.get("title", "Untitled")
    title_md = f"[{title}]({paper_url})" if paper_url else title
    score = p.get("similarity_score") or p.get("score")
    score_str = f" · similarity {score:.2f}" if score is not None else ""
    lines = [f"**{i}. {title_md}**{score_str}"]
    authors = p.get("authors", [])
    if authors:
        lines.append(f"*{', '.join(authors[:3])}{'  et al.' if len(authors) > 3 else ''}*")
    lines.append("")
    return lines


async def _anchor_node(state: ConvState) -> dict:
    last = state["messages"][-1]
    text = last.content if hasattr(last, "content") else str(last)
    researcher_id = cl.user_session.get("researcher_id")

    doi_or_title = await _extract_anchor_id(text)
    if not doi_or_title:
        return {"messages": [AIMessage(content=(
            "I couldn't identify a paper. Please provide a DOI "
            "(e.g. `10.1016/j.celrep.2023.112345`) or a paper title."
        ))]}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{_API_BASE}/api/anchor_search",
                json={"doi_or_title": doi_or_title,
                      "researcher_id": researcher_id, "n_results": 10},
            )
            if not r.is_success:
                raise RuntimeError(f"API returned {r.status_code}: {r.text[:200]}")
            similar = r.json()
    except Exception as exc:
        return {"messages": [AIMessage(content=f"Anchor search failed: {exc}")]}

    if not similar:
        return {"messages": [AIMessage(content=(
            f"No similar papers found for **{doi_or_title}**. "
            "Run a full search first to populate the knowledge base."
        ))]}

    lines = [f"## 🔗 Papers similar to *{doi_or_title}*\n"]
    for i, p in enumerate(similar, 1):
        lines.extend(_format_anchor_entry(i, p))
    return {"messages": [AIMessage(content="\n".join(lines))]}


async def _general_node(state: ConvState) -> dict:
    history = [{"role": "system", "content": _GENERAL_SYSTEM},
               *_to_oai(state["messages"])]
    resp = await litellm.acompletion(
        model=_MODEL, messages=history, max_tokens=400, temperature=0.7
    )
    return {"messages": [AIMessage(content=resp.choices[0].message.content)]}


def _to_oai(messages: list) -> list[dict]:
    result = []
    for m in messages:
        if isinstance(m, HumanMessage):
            result.append({"role": "user", "content": m.content})
        elif isinstance(m, AIMessage):
            result.append({"role": "assistant", "content": m.content})
        elif isinstance(m, SystemMessage):
            result.append({"role": "system", "content": m.content})
    return result


def _build_graph() -> object:
    g = StateGraph(ConvState)
    g.add_node("classify", _classify_node)
    g.add_node("profile_chat", _profile_node)
    g.add_node("anchor", _anchor_node)
    g.add_node("qa", _qa_node)
    g.add_node("general", _general_node)
    g.add_edge(START, "classify")
    g.add_conditional_edges(
        "classify",
        lambda s: s["mode"],
        {"profile": "profile_chat", "anchor": "anchor",
         "qa": "qa", "general": "general"},
    )
    g.add_edge("profile_chat", END)
    g.add_edge("anchor", END)
    g.add_edge("qa", END)
    g.add_edge("general", END)
    return g.compile(checkpointer=_checkpointer)


_graph = _build_graph()


# ──────────────────────────────────────────────────────────────────────────────
# Search execution
# ──────────────────────────────────────────────────────────────────────────────

_CONFIRM_WORDS = frozenset({
    "yes", "yeah", "yep", "sure", "ok", "okay", "go", "go ahead", "proceed",
    "run it", "run", "start", "search", "confirmed", "confirm", "looks good",
    "that's correct", "correct", "sounds good", "perfect", "great", "do it",
    "let's go", "fire away", "all good", "right",
})

_RESET_PHRASES = (
    "new search", "start over", "start fresh", "restart", "reset",
    "set up a new", "setup a new", "different search", "fresh search",
    "from scratch",
)


def _is_confirmation(text: str) -> bool:
    t = text.strip().lower().rstrip("!.,")
    return t in _CONFIRM_WORDS or any(
        w in t for w in ("yes", "go ahead", "looks good", "run it", "proceed")
    )


def _is_reset_request(text_lower: str) -> bool:
    """Return True if the user wants to discard the current profile and start over."""
    return any(phrase in text_lower for phrase in _RESET_PHRASES)


async def _do_search(profile: dict) -> None:
    """Send the profile to /api/search and display results."""
    rid = cl.user_session.get("researcher_id") or "chat_user"
    rname = cl.user_session.get("researcher_name") or rid
    profile["researcher_id"] = rid
    profile.setdefault("name", rname)

    species = ", ".join(profile.get("plant_species") or []) or "—"
    stresses = ", ".join(profile.get("stress_types") or []) or "—"
    keywords = ", ".join(profile.get("expertise_keywords") or []) or "—"
    sources = ", ".join(profile.get("source_targets") or []) or "all sources"
    months = profile.get("time_range_months", 12)

    await cl.Message(content=(
        f"🔍 **Searching literature for {rname}…**\n"
        f"- Species: {species}\n"
        f"- Stresses: {stresses}\n"
        f"- Keywords: {keywords}\n"
        f"- Sources: {sources} · last {months} months\n\n"
        "*Fetching and scoring papers — this may take a few minutes.*"
    )).send()

    logger.info(
        "Sending search to API: researcher=%s species=%s stresses=%s "
        "sources=%s keywords=%s months=%s priorities=n%.1f/r%.1f/m%.1f/rep%.1f",
        rid, profile.get("plant_species"), profile.get("stress_types"),
        profile.get("source_targets"), profile.get("expertise_keywords"), months,
        profile.get("priority_novelty", 0.5), profile.get("priority_relevance", 0.5),
        profile.get("priority_methodology", 0.5), profile.get("priority_reproducibility", 0.5),
    )
    anchor_paper = profile.pop("anchor_paper", None) or ""
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(f"{_API_BASE}/api/search", json=profile)
            if not r.is_success:
                raise RuntimeError(f"API returned {r.status_code}: {r.text[:200]}")
        await _display_results(r.json())
    except Exception as exc:
        logger.error("Search failed: %s", exc)
        await cl.Message(content=f"Search failed: {exc}").send()
        return

    if anchor_paper:
        await _run_anchor_search(anchor_paper, rid)


async def _run_anchor_search(doi_or_title: str, researcher_id: str) -> None:
    """Run /api/anchor_search and display results as a supplementary section."""
    await cl.Message(
        content=f"🔗 **Finding papers similar to:** *{doi_or_title}*…"
    ).send()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{_API_BASE}/api/anchor_search",
                json={"doi_or_title": doi_or_title,
                      "researcher_id": researcher_id, "n_results": 10},
            )
            if not r.is_success:
                raise RuntimeError(f"API returned {r.status_code}: {r.text[:200]}")
            similar = r.json()
    except Exception as exc:
        await cl.Message(content=f"Anchor search failed: {exc}").send()
        return

    if not similar:
        await cl.Message(
            content=f"No similar papers found for **{doi_or_title}** yet — "
                    "the knowledge base will grow as the background monitor runs."
        ).send()
        return

    lines = [f"## 🔗 Papers similar to *{doi_or_title}*\n"]
    for i, p in enumerate(similar, 1):
        lines.extend(_format_anchor_entry(i, p))
    await cl.Message(content="\n".join(lines)).send()


async def _get_sessions(researcher_id: str, limit: int = 3) -> list:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{_API_BASE}/api/sessions/{researcher_id}", params={"limit": limit}
            )
            r.raise_for_status()
            return r.json()
    except Exception:
        return []


async def _send_feedback(proposal_id: str, researcher_id: str,
                         suggestion: str, theme: str | None, rating: int) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{_API_BASE}/api/feedback",
            json={"proposal_id": proposal_id, "researcher_id": researcher_id,
                  "suggestion": suggestion, "theme": theme, "rating": rating},
        )
        r.raise_for_status()


# ──────────────────────────────────────────────────────────────────────────────
# Chainlit handlers
# ──────────────────────────────────────────────────────────────────────────────

@cl.on_chat_start
async def on_start() -> None:
    cl.user_session.set("thread_id", cl.user_session.get("id", "default"))
    cl.user_session.set("last_results", None)
    cl.user_session.set("profile_stage", None)
    cl.user_session.set("pending_profile", {})

    # Name may be passed from the landing page as a URL query parameter.
    name: str = ""
    try:
        env = cl.context.session.environ
        # Try WSGI-style key first, then ASGI-style (lowercase bytes).
        qs = env.get("QUERY_STRING", "")
        if not qs:
            raw = env.get("query_string", b"")
            qs = raw.decode() if isinstance(raw, bytes) else str(raw)
        name = (parse_qs(qs).get("name", [""])[0]).strip()
        # Last resort: parse the name from the HTTP Referer header.
        if not name:
            referer = env.get("HTTP_REFERER", "") or env.get("http_referer", "")
            if referer:
                name = (parse_qs(urlparse(referer).query).get("name", [""])[0]).strip()
        logger.info("on_start: qs=%r  name=%r", qs, name)
    except Exception as exc:
        logger.warning("on_start: could not read query params: %s", exc)

    if name:
        rid = name.lower().replace(" ", "_")
        cl.user_session.set("researcher_name", name)
        cl.user_session.set("researcher_id", rid)
        cl.user_session.set("awaiting_name", False)
        await _welcome(name, rid)
    else:
        cl.user_session.set("researcher_id", None)
        cl.user_session.set("researcher_name", None)
        cl.user_session.set("awaiting_name", True)
        await cl.Message(
            content="Hi! I'm your plant biology literature assistant.\n\nWhat's your name?"
        ).send()


async def _fetch_new_papers_since_login(researcher_id: str) -> dict:
    """Call the new-papers endpoint; returns {new_since, new_count, new_papers}."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_API_BASE}/api/researcher/{researcher_id}/new-papers")
            if r.is_success:
                return r.json()
    except Exception as exc:
        logger.warning("Could not fetch new papers for %s: %s", researcher_id, exc)
    return {"new_since": None, "new_count": 0, "new_papers": []}


async def _fetch_stored_profile(researcher_id: str) -> dict | None:
    """Fetch a previously saved profile from the API, or return None."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_API_BASE}/api/researcher/{researcher_id}")
            if r.is_success and r.json():
                return r.json()
    except Exception as exc:
        logger.warning("Could not fetch stored profile for %s: %s", researcher_id, exc)
    return None


async def _welcome(name: str, researcher_id: str) -> None:
    sessions, stored, new_info = await asyncio.gather(
        _get_sessions(researcher_id),
        _fetch_stored_profile(researcher_id),
        _fetch_new_papers_since_login(researcher_id),
    )

    if sessions:
        last = sessions[0]
        date = (last.get("timestamp") or "")[:10]
        new_count = new_info.get("new_count", 0)
        if new_count > 0:
            plural = "s" if new_count != 1 else ""
            new_note = f" The background monitor collected **{new_count} new paper{plural}** since then."
        else:
            new_note = ""
        greeting = (
            f"Welcome back, **{name}**! Your last search was on {date} "
            f"({last.get('n_papers', '?')} papers, {last.get('n_proposals', '?')} proposals).{new_note}"
        )
    else:
        greeting = f"Nice to meet you, **{name}**!"

    if stored:
        # Pre-fill profile from stored data and mark all topics covered.
        # ResearcherProfile stores priorities as flat keys (priority_novelty etc.),
        # not under a nested "priorities" dict.
        pending = {
            "plant_species":            stored.get("plant_species") or [],
            "stress_types":             stored.get("stress_types") or [],
            "expertise_keywords":       stored.get("expertise_keywords") or [],
            "source_targets":           stored.get("source_targets") or [],
            "time_range_months":        stored.get("time_range_months", 12),
            "priority_novelty":         stored.get("priority_novelty", 0.5),
            "priority_relevance":       stored.get("priority_relevance", 0.5),
            "priority_methodology":     stored.get("priority_methodology", 0.5),
            "priority_reproducibility": stored.get("priority_reproducibility", 0.5),
        }
        cl.user_session.set("profile_stage", "confirm")
        cl.user_session.set("pending_profile", pending)
        cl.user_session.set("topics_covered", list(_ALL_TOPICS))
        await cl.Message(
            content=f"{greeting}\n\nI found your previous settings. "
                    f"{_profile_summary(pending)}"
        ).send()
    else:
        cl.user_session.set("profile_stage", "collecting")
        cl.user_session.set("pending_profile", {})
        cl.user_session.set("topics_covered", [])
        first_q = f"**{_TOPIC_LABELS['s1']}**\n{_TOPIC_QUESTIONS['s1']}"
        await cl.Message(
            content=(
                f"Hi, **{name}**! I'm your plant biology literature assistant.\n\n"
                f"Let's start a new search.\n\n{first_q}"
            )
        ).send()


_COMBO_TRIGGERS = frozenset({
    "combos", "combinations", "proposals",
    "show combos", "show combinations", "show proposals",
})
_CONTRADICTION_TRIGGERS = frozenset({
    "contradictions", "conflicts",
    "show contradictions", "show conflicts",
})


@cl.action_callback("feedback_up")
async def on_feedback_up(action: cl.Action) -> None:
    await _handle_feedback_action(action)


@cl.action_callback("feedback_down")
async def on_feedback_down(action: cl.Action) -> None:
    await _handle_feedback_action(action)


async def _handle_feedback_action(action: cl.Action) -> None:
    p = action.payload
    try:
        await _send_feedback(
            proposal_id=p["proposal_id"], researcher_id=p["researcher_id"],
            suggestion=p["suggestion"], theme=p.get("theme"), rating=p["rating"],
        )
        icon = "👍" if p["rating"] == 1 else "👎"
        await cl.Message(
            content=f"{icon} Feedback saved — this will personalise future proposals "
                    f"for **{p['researcher_id']}**."
        ).send()
    except Exception as exc:
        await cl.Message(content=f"Could not save feedback: {exc}").send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    # ── 1. Name collection ────────────────────────────────────────────────
    if cl.user_session.get("awaiting_name"):
        name = message.content.strip() or "Researcher"
        rid = name.lower().replace(" ", "_")
        cl.user_session.set("researcher_name", name)
        cl.user_session.set("researcher_id", rid)
        cl.user_session.set("awaiting_name", False)
        await _welcome(name, rid)
        return

    text = message.content.strip()
    text_lower = text.lower()

    # ── 2. Combo / contradiction shortcuts ────────────────────────────────
    last_results = cl.user_session.get("last_results")
    if last_results:
        if text_lower in _COMBO_TRIGGERS:
            await _display_combos(last_results)
            return
        if text_lower in _CONTRADICTION_TRIGGERS:
            await _display_contradictions(last_results)
            return

    # ── 3. Profile collection ─────────────────────────────────────────────
    stage = cl.user_session.get("profile_stage")

    if stage == "collecting":
        await _handle_collecting(text)
        return

    if stage == "confirm":
        await _handle_confirm_stage(text, text_lower)
        return

    # ── 4. LangGraph (qa / anchor / general / start-profile) ─────────────
    thread_id = cl.user_session.get("thread_id", "default")
    config = {"configurable": {"thread_id": thread_id}}

    result = await _graph.ainvoke(
        {"messages": [HumanMessage(content=message.content)]},
        config=config,
    )

    last_ai = next(
        (m for m in reversed(result["messages"]) if isinstance(m, AIMessage)), None
    )
    if last_ai:
        await cl.Message(content=last_ai.content).send()


# ──────────────────────────────────────────────────────────────────────────────
# Display helpers
# ──────────────────────────────────────────────────────────────────────────────

_FEASIBILITY_ICONS = {"true": "✓", "partial": "~", "false": "✗"}
_CREDIBILITY_ICONS = {
    "high": "🟢", "moderate": "🟡", "preliminary": "🔴", "conflicting": "⚠️"
}


def _format_paper_rich(p: dict) -> str:
    pub_year = (p.get("published") or "")[:4] or "?"
    oa = " · 🔓 OA" if p.get("is_open_access") else ""
    doi = p.get("doi")
    url = p.get("url")
    paper_url = f"https://doi.org/{doi}" if doi else url
    title = p.get("title", "Untitled")
    title_md = f"[{title}]({paper_url})" if paper_url else title
    authors = p.get("authors", [])
    author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
    cred_icon = _CREDIBILITY_ICONS.get(p.get("credibility_level", ""), "❓")
    cred_label = (p.get("credibility_level") or "?").capitalize()
    scores = p.get("scores", {})
    score_str = (
        f"Overall **{scores.get('overall', 0):.2f}** · "
        f"Species {scores.get('species_match', 0):.2f} · "
        f"Stress {scores.get('stress_match', 0):.2f} · "
        f"Method {scores.get('method_match', 0):.2f} · "
        f"Novelty {scores.get('novelty', 0):.2f}"
    )
    return (
        f"**{p.get('rank', '?')}. {title_md}**\n"
        f"*{author_str}* · {p.get('journal', '?')} · {pub_year}{oa} · "
        f"`{p.get('source', '?')}`\n"
        f"{cred_icon} {cred_label} · {score_str}\n"
    )


def _format_insights(raw_insights: list) -> str:
    if not raw_insights:
        return ""
    parts = []
    for ki in raw_insights:
        label = ki.get("paper_id") or ki.get("paper") or ""
        prefix = label[:8] + " " if label else ""
        parts.append(f"{prefix}*{ki.get('insight', '')}*")
    return "\n  **Key insights:** " + " · ".join(parts)


def _format_verification(v: dict | None) -> str:
    if v is None:
        return ""
    total = v.get("supported", 0) + v.get("unsupported", 0)
    if v.get("flagged"):
        return f"\n  ⚠ *Verification concern — {v.get('unsupported', 0)}/{total} claims unsupported*"
    if total > 0:
        return f"\n  ✓ *{v.get('supported', 0)}/{total} claims verified*"
    return ""


def _format_rag_combo(c: dict) -> str:
    theme = f"**[{c['theme']}]** " if c.get("theme") else ""
    fdata = c.get("feasibility") or {}
    feasible = fdata.get("feasible")
    fkey = feasible if isinstance(feasible, str) else str(feasible).lower()
    ficon = _FEASIBILITY_ICONS.get(fkey, "")
    fstr = f" `{ficon}`" if ficon else ""
    warn = f"\n  ⚠ *{c['novelty_warning']}*" if c.get("novelty_warning") else ""
    rationale = f"\n  > {c['rationale']}" if c.get("rationale") else ""
    insights_str = _format_insights(c.get("key_insights") or [])
    verify_str = _format_verification(c.get("verification"))
    return f"- {theme}{c['suggestion']}{fstr}{warn}{rationale}{insights_str}{verify_str}\n"


def _format_per_paper_combo(c: dict) -> str:
    src = c.get("source_paper", "")[:70]
    return f"- {c['suggestion']}\n  *{src}…*\n"


def _proposal_id(suggestion: str) -> str:
    return hashlib.md5(suggestion.encode()).hexdigest()[:16]


async def _display_combos(results: dict) -> None:
    researcher_id = cl.user_session.get("researcher_id") or "chat_user"
    rag_combos = results.get("rag_combos", [])
    combos = results.get("combos", [])
    if not rag_combos and not combos:
        await cl.Message(
            content="No combination suggestions were found in the last search."
        ).send()
        return

    if rag_combos:
        await cl.Message(
            content="## 💡 AI-Synthesised Experiment Proposals\n"
                    "*Cross-paper reasoning — grouped by theme. "
                    "React with 👍/👎 to personalise future searches.*"
        ).send()
        for c in rag_combos[:10]:
            pid = _proposal_id(c["suggestion"])
            actions = [
                cl.Action(
                    name="feedback_up",
                    payload={"proposal_id": pid, "researcher_id": researcher_id,
                             "suggestion": c["suggestion"], "theme": c.get("theme"),
                             "rating": 1},
                    label="👍 Useful",
                ),
                cl.Action(
                    name="feedback_down",
                    payload={"proposal_id": pid, "researcher_id": researcher_id,
                             "suggestion": c["suggestion"], "theme": c.get("theme"),
                             "rating": -1},
                    label="👎 Not useful",
                ),
            ]
            await cl.Message(content=_format_rag_combo(c), actions=actions).send()

    if combos:
        lines = [
            "## 📎 Per-paper Hypotheses\n"
            "*One signal per paper — quick ideas, not cross-paper reasoning*\n"
        ]
        lines += [_format_per_paper_combo(c) for c in combos[:10]]
        await cl.Message(content="\n".join(lines)).send()


async def _display_contradictions(results: dict) -> None:
    contradictions = results.get("contradictions", [])
    if not contradictions:
        await cl.Message(content=(
            "No contradictions detected yet. "
            "This improves as more papers accumulate in the knowledge base."
        )).send()
        return
    lines = ["## ⚠️ Detected Contradictions\n"]
    for c in contradictions:
        papers_str = " · ".join(c.get("papers", [])[:2])
        lines.append(f"**{papers_str}**\n")
        lines.append(f"- Claim A: {c.get('claim_a', '?')}\n")
        lines.append(f"- Claim B: {c.get('claim_b', '?')}\n")
        if c.get("resolution_hint"):
            lines.append(f"  *Possible resolution: {c['resolution_hint']}*\n")
        lines.append("")
    await cl.Message(content="\n".join(lines)).send()


async def _display_results(results: dict) -> None:
    papers = results.get("papers", [])
    rag_combos = results.get("rag_combos", [])
    combos = results.get("combos", [])
    contradictions = results.get("contradictions", [])
    sr = results.get("search_result", {})

    cl.user_session.set("last_results", results)

    if not papers:
        await cl.Message(content="No papers found for that profile.").send()
        return

    n_q = sr.get("queries_executed", "?")
    n_found = sr.get("papers_found", "?")
    n_total = sr.get("total_papers", "?")
    summary_line = (
        f"*{n_q} queries executed · {n_found} new papers retrieved · "
        f"{n_total} papers total in knowledge base*"
    )

    first_batch = [f"## 📚 Found {len(papers)} papers\n{summary_line}\n"]
    for p in papers[:10]:
        first_batch.append(_format_paper_rich(p))
    await cl.Message(content="\n".join(first_batch)).send()

    if len(papers) > 10:
        await cl.Message(
            content="\n".join(_format_paper_rich(p) for p in papers[10:])
        ).send()

    options = []
    if rag_combos or combos:
        n = len(rag_combos) + len(combos)
        options.append(f"type **combos** to see {n} experiment proposal(s)")
    if contradictions:
        options.append(f"type **contradictions** to see {len(contradictions)} conflict(s)")

    lines = [f"Open the **[dashboard]({_DASHBOARD_URL}?tab=results)** to explore the full results interactively."]
    if options:
        lines.append("Here in chat you can also: " + ", or ".join(options) + ".")
    await cl.Message(content="\n".join(lines)).send()
