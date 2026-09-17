# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""The generic engine and frontend must not know about plant phenotyping.

Everything specific to this community belongs in ``domains/plant_phenotyping``.
This scans the core code for the pack's vocabulary so a leak fails the build.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

CORE = [
    "agents", "api", "models", "utils", "docker",
    "api_server.py", "mcp_server.py", "chainlit_app.py",
    "frontend/src", "frontend/index.html",
]

_WORDS = [
    r"plants?\b", r"phenotyp", r"species", r"stress", r"drought", r"salinity",
    r"equipment", r"instrument", r"feasib", r"botan", r"phytolog",
    r"hyperspectral", r"chlorophyll", r"arabidopsis", r"poplar",
]


def _core_files():
    for entry in CORE:
        path = ROOT / entry
        files = [path] if path.is_file() else sorted(path.rglob("*"))
        for f in files:
            if f.is_file() and f.suffix in {".py", ".js", ".jsx", ".html", ".css"} and "__pycache__" not in f.parts:
                yield f


def _pack_terms(pack) -> list[str]:
    terms = set()
    for facet in pack.facets:
        terms.add(facet.key)
        for item in facet.vocabulary:
            terms.add(item.value)
    terms.update(pack.sources)
    # Terms that are also ordinary engineering words.
    terms -= {"method", "other", "light", "temperature", "nutrient", "arxiv", "pubmed", "mechanical"}
    return sorted(t for t in terms if len(t) > 4)


def test_core_code_has_no_plant_vocabulary(plant_pack):
    patterns = [re.compile(w, re.IGNORECASE) for w in _WORDS]
    patterns += [re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE) for t in _pack_terms(plant_pack)]
    leaks = []
    for f in _core_files():
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for pattern in patterns:
                if pattern.search(line):
                    leaks.append(f"{f.relative_to(ROOT)}:{lineno}: {line.strip()[:100]}")
                    break
    if leaks:
        pytest.fail("Plant-specific terms in core code:\n  " + "\n  ".join(leaks))
