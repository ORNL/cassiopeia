# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Data models for the APPL Literature Mining Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ─────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────

class StressType(str, Enum):
    DROUGHT = "drought"
    NUTRIENT = "nutrient"
    TEMPERATURE = "temperature"
    PATHOGEN = "pathogen"
    HEAVY_METAL = "heavy_metal"
    SALINITY = "salinity"
    LIGHT = "light"
    FLOODING = "flooding"
    OZONE = "ozone"
    MECHANICAL = "mechanical"


class CredibilityLevel(str, Enum):
    HIGH = "high"           # >3 independent studies
    MODERATE = "moderate"   # 1-2 studies
    PRELIMINARY = "preliminary"  # single study/preprint
    CONFLICTING = "conflicting"  # conflicting evidence


class SourceType(str, Enum):
    BIORXIV = "biorxiv"
    PUBMED = "pubmed"
    PLOS_ONE = "plos_one"
    FRONTIERS = "frontiers"
    NATURE_COMMS = "nature_communications"
    NEW_PHYTOLOGIST = "new_phytologist"
    PLANT_PHYSIOLOGY = "plant_physiology"
    ARXIV = "arxiv"
    OTHER = "other"


class PhenotypingMethod(str, Enum):
    HYPERSPECTRAL = "hyperspectral_imaging"
    RGB = "rgb_imaging"
    THERMAL = "thermal_imaging"
    FLUORESCENCE = "chlorophyll_fluorescence"
    ROOT_IMAGING = "root_imaging"
    LIDAR = "lidar"
    MULTISPECTRAL = "multispectral_imaging"
    GRAVIMETRIC = "gravimetric"
    GAS_EXCHANGE = "gas_exchange"
    OTHER = "other"


# ─────────────────────────────────────────────────────
# Researcher Profile
# ─────────────────────────────────────────────────────

@dataclass
class ResearcherProfile:
    """Captures a researcher's interests and priorities for query generation."""

    researcher_id: str
    name: str
    plant_species: list[str] = field(default_factory=list)
    stress_types: list[StressType] = field(default_factory=list)
    phenotyping_methods: list[PhenotypingMethod] = field(default_factory=list)
    expertise_keywords: list[str] = field(default_factory=list)
    methodology_preferences: list[str] = field(default_factory=list)

    # Priority weights (0.0–1.0) for ranking results
    priority_novelty: float = 0.5
    priority_relevance: float = 0.5
    priority_methodology: float = 0.5
    priority_reproducibility: float = 0.5

    # Timeline constraints
    experiment_start: datetime | None = None
    experiment_end: datetime | None = None

    # Facility constraints
    available_equipment: list[str] = field(default_factory=list)
    time_range_months: int = 12  # how far back to search
    source_targets: list[str] = field(default_factory=list)  # empty = all sources


# ─────────────────────────────────────────────────────
# Search & Results
# ─────────────────────────────────────────────────────

@dataclass
class SearchQuery:
    """A generated literature search query with its context."""

    query_string: str
    source_target: SourceType
    researcher_id: str
    base_terms: list[str] = field(default_factory=list)
    contextual_modifiers: dict[str, str] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PaperMetadata:
    """Metadata for a single retrieved paper."""

    paper_id: str
    title: str
    authors: list[str]
    abstract: str
    source: SourceType
    doi: str | None = None
    url: str | None = None
    published_date: datetime | None = None
    journal: str | None = None
    keywords: list[str] = field(default_factory=list)
    is_open_access: bool = False
    citation_count: int = 0


@dataclass
class RelevanceScore:
    """Multi-dimensional relevance scoring for a paper."""

    overall: float = 0.0
    species_match: float = 0.0
    stress_match: float = 0.0
    method_match: float = 0.0
    recency: float = 0.0
    credibility: float = 0.0
    novelty: float = 0.0

    def weighted_score(self, profile: ResearcherProfile) -> float:
        """Compute weighted score based on researcher priorities."""
        weights = {
            "relevance": profile.priority_relevance,
            "novelty": profile.priority_novelty,
            "methodology": profile.priority_methodology,
            "reproducibility": profile.priority_reproducibility,
        }
        total_weight = sum(weights.values()) or 1.0
        return (
            weights["relevance"] * (self.species_match + self.stress_match) / 2
            + weights["novelty"] * self.novelty
            + weights["methodology"] * self.method_match
            + weights["reproducibility"] * self.credibility
        ) / total_weight


@dataclass
class ScoredPaper:
    """A paper with its relevance scoring and credibility assessment."""

    paper: PaperMetadata
    relevance: RelevanceScore
    credibility: CredibilityLevel = CredibilityLevel.PRELIMINARY
    suggested_combinations: list[str] = field(default_factory=list)
    appl_feasibility_notes: str = ""
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
    pending_queries: list[SearchQuery] = field(default_factory=list)
    scored_papers: list[ScoredPaper] = field(default_factory=list)
    query_history: list[SearchQuery] = field(default_factory=list)
    last_scan_time: dict[str, datetime] = field(default_factory=dict)
    knowledge_base_stats: dict[str, Any] = field(default_factory=dict)
