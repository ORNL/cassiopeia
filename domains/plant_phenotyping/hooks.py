# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""APPL hooks: instrument feasibility assessment of synthesized proposals."""

from __future__ import annotations

import json
import logging
from typing import Any

import litellm

from utils.json_utils import parse_json_response

logger = logging.getLogger(__name__)

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

_UNAVAILABLE = {
    "feasible": None,
    "confidence": 0.0,
    "missing_equipment": [],
    "adaptation": "",
    "note": "Assessment unavailable.",
}


class FeasibilityEvaluator:
    """Checks each proposal against the facility instrument list."""

    key = "feasibility"
    label = "Assessing equipment feasibility…"

    def applies(self, context: dict[str, list[str]]) -> bool:
        return bool(context.get("instruments"))

    def summary(self, result: dict[str, Any] | None) -> str:
        feasible = (result or {}).get("feasible")
        key = feasible if isinstance(feasible, str) else str(feasible).lower()
        return {"true": "✓", "partial": "~", "false": "✗"}.get(key, "")

    async def evaluate(
        self,
        proposal: dict[str, Any],
        context: dict[str, list[str]],
        llm_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = _FEASIBILITY_PROMPT.format(
            equipment="\n".join(f"  - {e}" for e in context["instruments"]),
            suggestion=proposal["suggestion"],
        )
        try:
            response = await litellm.acompletion(
                **llm_kwargs,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
                response_format={"type": "json_object"},
                temperature=0.1,
                timeout=120,
            )
            return parse_json_response(response.choices[0].message.content.strip())
        except (litellm.APIError, json.JSONDecodeError) as exc:
            logger.warning("Feasibility assessment failed for proposal: %s", exc)
            return dict(_UNAVAILABLE)


EVALUATORS = (FeasibilityEvaluator(),)
