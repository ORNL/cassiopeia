# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Domain-neutral data models for the Cassiopeia literature pipeline.

What a profile's facets and context fields mean is defined by the active
domain pack (see ``domains/``); these models only carry them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

# ─────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────

class CredibilityLevel(str, Enum):
    HIGH = "high"           # >3 independent studies
    MODERATE = "moderate"   # 1-2 studies
    PRELIMINARY = "preliminary"  # single study/preprint
    CONFLICTING = "conflicting"  # conflicting evidence


# ─────────────────────────────────────────────────────
# Researcher Profile
# ─────────────────────────────────────────────────────

@dataclass
class ResearcherProfile:
    """Captures a researcher's interests and priorities for query generation."""

    researcher_id: str
    name: str
    # facet key → selected values (keys and vocabularies come from the pack)
    facets: dict[str, list[str]] = field(default_factory=dict)
    expertise_keywords: list[str] = field(default_factory=list)

    # Priority weights (0.0–1.0) for ranking results
    priority_novelty: float = 0.5
    priority_relevance: float = 0.5
    priority_methodology: float = 0.5
    priority_reproducibility: float = 0.5

    # Deployment-provided context (declared by the pack, filled by the deployment)
    context: dict[str, list[str]] = field(default_factory=dict)
    time_range_months: int = 12  # how far back to search
    source_targets: list[str] = field(default_factory=list)  # empty = all sources

    def terms(self, facet_key: str) -> list[str]:
        return self.facets.get(facet_key, [])


# ─────────────────────────────────────────────────────
# Search & Results
# ─────────────────────────────────────────────────────

@dataclass
class SearchQuery:
    """A generated literature search query with its context."""

    query_string: str
    source_target: str
    researcher_id: str
    base_terms: list[str] = field(default_factory=list)
    # OR-within-group, AND-between-groups structure produced by the LLM pass.
    # Each inner list is a synonym set; fetchers join with OR then AND the groups.
    # Falls back to base_terms when empty (cross-product / rescue pass queries).
    term_groups: list[list[str]] = field(default_factory=list)
    contextual_modifiers: dict[str, str] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PaperMetadata:
    """Metadata for a single retrieved paper."""

    paper_id: str
    title: str
    authors: list[str]
    abstract: str
    source: str
    doi: str | None = None
    url: str | None = None
    published_date: datetime | None = None
    journal: str | None = None
    keywords: list[str] = field(default_factory=list)
    is_open_access: bool = False
    citation_count: int = 0
    full_text: str | None = None


@dataclass
class RelevanceScore:
    """Multi-dimensional relevance scoring for a paper."""

    overall: float = 0.0
    # facet key → match score in [0, 1]
    facet_scores: dict[str, float] = field(default_factory=dict)
    recency: float = 0.0
    credibility: float = 0.0
    novelty: float = 0.0

    def weighted_score(
        self,
        profile: ResearcherProfile,
        facet_priorities: Mapping[str, str],
    ) -> float:
        """Combine dimensions with the researcher's priority weights.

        ``facet_priorities`` maps each facet key to the priority it feeds
        (``relevance`` or ``methodology``); facet scores sharing a priority are
        averaged.  A priority with no facets is left out of the weighting.
        """
        groups: dict[str, list[float]] = {}
        for key, priority in facet_priorities.items():
            groups.setdefault(priority, []).append(self.facet_scores.get(key, 0.0))

        weights = {
            "relevance": profile.priority_relevance,
            "methodology": profile.priority_methodology,
        }
        terms = [
            (weights[p], sum(vals) / len(vals)) for p, vals in groups.items() if vals
        ]
        terms.append((profile.priority_novelty, self.novelty))
        terms.append((profile.priority_reproducibility, self.credibility))

        total_weight = sum(w for w, _ in terms) or 1.0
        return sum(w * v for w, v in terms) / total_weight


@dataclass
class ScoredPaper:
    """A paper with its relevance scoring and credibility assessment."""

    paper: PaperMetadata
    relevance: RelevanceScore
    credibility: CredibilityLevel = CredibilityLevel.PRELIMINARY
    suggested_combinations: list[str] = field(default_factory=list)
    source_queries: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────
# Agent State
# ─────────────────────────────────────────────────────

@dataclass
class AgentState:
    """Internal state of the LiteratureMiningAgent."""

    researcher_profiles: dict[str, ResearcherProfile] = field(
        default_factory=dict,
    )
    scored_papers: list[ScoredPaper] = field(default_factory=list)
    query_history: list[SearchQuery] = field(default_factory=list)
    last_scan_time: dict[str, datetime] = field(default_factory=dict)
