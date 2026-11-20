# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""How many proposals a synthesis asks for, and how many abstracts it reads.

Both follow the researcher's library, not the papers one search happened to add:
a library of several hundred papers supports more cross-paper proposals than the
few that a search finding a handful of new papers would otherwise get.

    library      proposals   abstracts read
    0 – 49           2            12
    100              4            16
    200              6            24
    360              9            36
    400+            10            40   (caps)
"""
from __future__ import annotations

MIN_PROPOSALS, MAX_PROPOSALS = 2, 10
PAPERS_PER_PROPOSAL = 50          # one more proposal per this many papers in the library
ABSTRACTS_PER_PROPOSAL = 4        # so each proposal has distinct papers to draw on
MIN_ABSTRACTS, MAX_ABSTRACTS = 12, 40


def n_proposals_for(library_size: int) -> int:
    return max(MIN_PROPOSALS, min(MAX_PROPOSALS, 2 + library_size // PAPERS_PER_PROPOSAL))


def n_abstracts_for(n_proposals: int) -> int:
    return max(MIN_ABSTRACTS, min(MAX_ABSTRACTS, ABSTRACTS_PER_PROPOSAL * n_proposals))
