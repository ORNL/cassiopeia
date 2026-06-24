# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Shared JSON parsing helpers for LLM responses."""

from __future__ import annotations

import json
import re


# Matches an opening ```json or ``` fence (case-insensitive, optional language tag).
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*", re.MULTILINE)


def strip_json_fence(raw: str) -> str:
    """Strip markdown code fences from an LLM response without parsing.

    Use when you need the cleaned string before calling json.loads yourself
    (e.g. when a regex fallback runs between stripping and parsing).
    """
    text = raw.strip()
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text, count=1)
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()
    return text


def parse_json_response(raw: str) -> object:
    """Parse JSON from an LLM response, stripping markdown code fences if present.

    Handles all of:
        ```json\\n{...}\\n```
        ```\\n{...}\\n```
        {...}   (no fence)

    Raises json.JSONDecodeError if the content is not valid JSON after stripping.
    """
    text = raw.strip()
    if text.startswith("```"):
        # Remove opening fence line (```json, ```, etc.)
        text = _FENCE_RE.sub("", text, count=1)
        # Remove closing fence if present
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()
    return json.loads(text)
