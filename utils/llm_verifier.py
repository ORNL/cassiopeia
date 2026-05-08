# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""LiteLLM-backed insight verifier (Augmentation A).

Checks whether a stated insight is genuinely supported by a paper's abstract.
Mirrors the structure of utils/llm_scorer.py.

Uses the same LLM_SCORING_MODEL env var (defaults to Haiku) since verification
is a lightweight reading-comprehension task.

On persistent failure returns supported=None so infrastructure errors are not
counted as hallucinations in the flagging threshold.
"""

from __future__ import annotations

import json
import logging
import os

import litellm

logger = logging.getLogger(__name__)

_VERIFY_PROMPT = """\
You are checking whether a stated insight is genuinely supported by a paper's text.

Paper text:
\"\"\"
{paper_text}
\"\"\"

Stated insight (claimed to be from this paper):
"{insight}"

Reply with strict JSON only, no preamble, no code fences:
{{
  "supported": true,
  "confidence": 0.0,
  "reason": "<one sentence>"
}}

Rules:
- "supported" is true only if the paper's text directly supports the insight as stated.
- If the paper makes a related but weaker or different claim, return false.
- If the paper does not address the topic, return false.
- "confidence" is your confidence in the supported/unsupported judgment, not in the insight itself.
"""

_DEFAULT_MODEL = "anthropic/claude-haiku-4-5-20251001"
_MAX_RETRIES = 2


async def verify_claim(
    paper_text: str,
    insight: str,
    model: str | None = None,
) -> dict:
    """Check whether insight is supported by paper_text.

    Returns dict with keys: supported (bool|None), confidence (float|None), reason (str).
    On persistent failure: supported=None, confidence=None, reason="verification_failed".
    Retries up to _MAX_RETRIES times on JSON parse failures; stops early on LLM errors.
    """
    _model = model or os.environ.get("LLM_SCORING_MODEL", _DEFAULT_MODEL)
    prompt = _VERIFY_PROMPT.format(paper_text=paper_text[:6000], insight=insight)

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = await litellm.acompletion(
                model=_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                response_format={"type": "json_object"},
                temperature=0.0,
                timeout=15,
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()
            data = json.loads(raw)
            return {
                "supported": bool(data["supported"]),
                "confidence": float(data.get("confidence", 0.0)),
                "reason": str(data.get("reason", "")),
            }
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            last_exc = exc
            logger.debug("verify_claim parse error (attempt %d): %s", attempt + 1, exc)
            # Retry on parse failures — model may have wrapped JSON in prose
        except Exception as exc:
            last_exc = exc
            logger.warning("verify_claim LLM error (attempt %d): %s", attempt + 1, exc)
            break  # Non-parse errors don't benefit from retry

    logger.warning("verify_claim failed after %d attempts: %s", _MAX_RETRIES + 1, last_exc)
    return {"supported": None, "confidence": None, "reason": "verification_failed"}
