# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""LiteLLM-backed proposal critic.

Evaluates a proposed plant phenotyping experiment from a skeptical reviewer
perspective, checking novelty, confounds, evidence strength, feasibility, and
providing an overall recommendation.

Uses LLM_CHAT_MODEL since critique is a substantive reasoning task that
benefits from a stronger model than verification.

On persistent failure returns None so the caller can omit the critique field
rather than propagating a dict with nulls.
"""

from __future__ import annotations

import json
import logging
import os

import litellm

from utils.json_utils import parse_json_response

logger = logging.getLogger(__name__)

_CRITIC_PROMPT = """\
You are a skeptical reviewer evaluating a proposed plant phenotyping experiment.
Be specific and concrete. Avoid generic concerns. If a dimension has no
substantive concern, say so explicitly rather than inventing one.

Proposed experiment:
Theme: {theme}
Suggestion: {suggestion}
Rationale: {rationale}
Key insights from prior work:
{key_insights_bullets}

Verification of insights against source papers:
{verification_bullets}

Most semantically similar papers in the knowledge base (for novelty check):
{similar_papers_bullets}

Available instruments at the facility:
{instruments_list}

Reply with strict JSON only, no preamble, no code fences:
{{
  "novelty": {{
    "assessment": "novel",
    "reasoning": "<one to three sentences>",
    "closest_prior_work": null
  }},
  "confounds": [
    {{"concern": "<specific confound>", "severity": "low"}}
  ],
  "evidence_strength": {{
    "assessment": "well_supported",
    "reasoning": "<one to three sentences>"
  }},
  "feasibility_concerns": [
    {{"concern": "<specific concern>", "severity": "low"}}
  ],
  "overall_recommendation": "pursue",
  "summary": "<one sentence summarizing the critique>"
}}

Valid values: novelty.assessment in {{novel, incremental, duplicative}},
evidence_strength.assessment in {{well_supported, partial, overreaching}},
severity in {{low, medium, high}},
overall_recommendation in {{pursue, refine, deprioritize}}.
If a list dimension has no concerns, return an empty list.
"""

_MAX_RETRIES = 2


def _format_key_insights(key_insights: list[dict]) -> str:
    """Format key_insights list into a bullet string for the critic prompt."""
    if not key_insights:
        return "  (none)"
    lines = []
    for ki in key_insights:
        paper_id = ki.get("paper_id", "")
        insight = ki.get("insight", "")
        lines.append(f"  - [{paper_id}] {insight}")
    return "\n".join(lines)


def _format_verification(verification: dict | None) -> str:
    """Format verification dict into a bullet string for the critic prompt."""
    if verification is None:
        return "  (not available)"
    details = verification.get("details", [])
    if not details:
        return "  (no claims checked)"
    lines = []
    for d in details:
        supported = d.get("supported")
        if supported is True:
            symbol = "✓"
        elif supported is False:
            symbol = "✗"
        else:
            symbol = "?"
        paper_id = d.get("paper_id", "")
        claim = d.get("claim", "")
        reason = d.get("reason", "")
        lines.append(f"  - {symbol} [{paper_id}] {claim} — {reason}")
    return "\n".join(lines)


def _format_similar_papers(similar_papers: list[dict]) -> str:
    """Format similar_papers list into a bullet string for the critic prompt.

    Uses up to 5 papers. Title from ``title`` or ``paper_id``; snippet from
    ``document`` or ``abstract_snippet``.
    """
    if not similar_papers:
        return "  (none retrieved)"
    lines = []
    for p in similar_papers[:5]:
        title = p.get("title", p.get("paper_id", ""))
        snippet = p.get("document", p.get("abstract_snippet", ""))
        lines.append(f"  - {title}\n    {snippet[:300]}")
    return "\n".join(lines)


async def critique_proposal(
    proposal: dict,
    similar_papers: list[dict],
    instruments: list[str],
    model: str | None = None,
) -> dict | None:
    """Critique a single experiment proposal using a LLM_SCORING_MODEL call.

    Makes one LLM call per proposal; returns a critique dict on success or
    None on persistent failure. Augmentation D depends on Augmentation A
    because it feeds verification.details from the verifier into the critic
    prompt, allowing the critic to reason about evidence quality.

    Args:
        proposal: Proposal dict as returned by synthesize_combinations (v2+).
            Must have keys: theme, suggestion, rationale, key_insights,
            verification.
        similar_papers: List of semantically similar paper dicts from RAG
            (for novelty assessment). Each may have title/paper_id and
            document/abstract_snippet keys.
        instruments: Flat list of instrument names available at the facility.
        model: Override the LiteLLM model string. Defaults to LLM_CHAT_MODEL env var.

    Returns:
        Critique dict with keys: novelty, confounds, evidence_strength,
        feasibility_concerns, overall_recommendation, summary. Returns None
        if all retry attempts fail so the caller can omit the critique field.
    """
    _model = model or os.environ["LLM_CHAT_MODEL"]


    theme = proposal.get("theme", "")
    suggestion = proposal.get("suggestion", "")
    rationale = proposal.get("rationale", "")
    key_insights = proposal.get("key_insights", [])
    verification = proposal.get("verification", None)

    instruments_list = (
        "\n".join(f"  - {inst}" for inst in instruments)
        if instruments
        else "  (none specified)"
    )

    prompt = _CRITIC_PROMPT.format(
        theme=theme,
        suggestion=suggestion,
        rationale=rationale,
        key_insights_bullets=_format_key_insights(key_insights),
        verification_bullets=_format_verification(verification),
        similar_papers_bullets=_format_similar_papers(similar_papers),
        instruments_list=instruments_list,
    )

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = await litellm.acompletion(
                model=_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1500,
                response_format={"type": "json_object"},
                temperature=0.3,
                timeout=120,
            )
            raw = response.choices[0].message.content.strip()
            data = parse_json_response(raw)
            # Validate required keys — raises KeyError to trigger retry
            for key in ("novelty", "evidence_strength", "overall_recommendation", "summary"):
                if key not in data:
                    raise KeyError(f"Missing required key: {key!r}")
            data.setdefault("confounds", [])
            data.setdefault("feasibility_concerns", [])
            return data
        except (json.JSONDecodeError, KeyError) as exc:
            last_exc = exc
            logger.debug("critique_proposal parse error (attempt %d): %s", attempt + 1, exc)
            # Retry on parse failures — model may have wrapped JSON in prose
        except Exception as exc:
            last_exc = exc
            logger.warning("critique_proposal LLM error (attempt %d): %s", attempt + 1, exc)
            break  # Non-parse errors don't benefit from retry

    logger.warning("critique_proposal failed after %d attempts: %s", _MAX_RETRIES + 1, last_exc)
    return None
