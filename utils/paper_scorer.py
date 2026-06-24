# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Relevance scoring and ranking for retrieved papers."""

from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher

from models.schemas import (
    CredibilityLevel,
    PaperMetadata,
    RelevanceScore,
    ResearcherProfile,
    ScoredPaper,
    SourceType,
)
from utils.source_fetchers import SOURCE_REGISTRY


class PaperScorer:
    """Scores and ranks papers against a researcher's profile.

    Scoring dimensions:
    - species_match:  How well the paper matches target species
    - stress_match:   How well the paper matches target stress types
    - method_match:   Alignment with preferred phenotyping methods
    - recency:        Preference for recent publications
    - credibility:    Based on journal, citation count, open-access status
    - novelty:        Uniqueness relative to already-scored papers
    """

    # Journal impact tiers for credibility scoring
    HIGH_IMPACT_JOURNALS = {
        "nature", "science", "cell", "nature communications",
        "new phytologist", "plant cell", "plant physiology",
        "the plant journal", "nature plants",
    }

    MID_IMPACT_JOURNALS = {
        "frontiers in plant science", "plos one", "bmc plant biology",
        "plant methods", "journal of experimental botany",
        "annals of botany",
    }

    # Source-level tier fallback used when the journal field is not populated.
    # Derived from SOURCE_REGISTRY so adding new sources only requires one change.
    _HIGH_IMPACT_SOURCES = frozenset(
        src for src, info in SOURCE_REGISTRY.items() if info.impact == "high"
    )
    _MID_IMPACT_SOURCES = frozenset(
        src for src, info in SOURCE_REGISTRY.items() if info.impact == "mid"
    )

    def score_paper(
        self,
        paper: PaperMetadata,
        profile: ResearcherProfile,
        existing_papers: list[ScoredPaper] | None = None,
    ) -> ScoredPaper:
        """Score a single paper against a researcher profile."""
        relevance = RelevanceScore(
            species_match=self._score_species(paper, profile),
            stress_match=self._score_stress(paper, profile),
            method_match=self._score_method(paper, profile),
            recency=self._score_recency(paper),
            credibility=self._score_credibility(paper),
            novelty=self._score_novelty(paper, existing_papers or []),
        )
        relevance.overall = relevance.weighted_score(profile)

        credibility = self._assess_credibility(paper)
        combinations = self._suggest_combinations(paper, profile)

        return ScoredPaper(
            paper=paper,
            relevance=relevance,
            credibility=credibility,
            suggested_combinations=combinations,
        )

    def rank_papers(
        self,
        papers: list[ScoredPaper],
    ) -> list[ScoredPaper]:
        """Rank papers by overall weighted score, descending."""
        return sorted(
            papers,
            key=lambda sp: sp.relevance.overall,
            reverse=True,
        )

    # ── Individual scoring dimensions ──────────────────

    def _score_species(
        self,
        paper: PaperMetadata,
        profile: ResearcherProfile,
    ) -> float:
        text = f"{paper.title} {paper.abstract}".lower()
        if not profile.plant_species:
            return 0.5
        matches = sum(
            1 for sp in profile.plant_species if sp.lower() in text
        )
        return min(matches / len(profile.plant_species), 1.0)

    def _score_stress(
        self,
        paper: PaperMetadata,
        profile: ResearcherProfile,
    ) -> float:
        text = f"{paper.title} {paper.abstract}".lower()
        if not profile.stress_types:
            return 0.5
        matches = sum(
            1
            for st in profile.stress_types
            if st.value.replace("_", " ") in text
        )
        return min(matches / len(profile.stress_types), 1.0)

    def _score_method(
        self,
        paper: PaperMetadata,
        profile: ResearcherProfile,
    ) -> float:
        text = f"{paper.title} {paper.abstract}".lower()
        if not profile.phenotyping_methods:
            return 0.5
        matches = sum(
            1
            for m in profile.phenotyping_methods
            if m.value.replace("_", " ") in text
        )
        return min(matches / len(profile.phenotyping_methods), 1.0)

    def _score_recency(self, paper: PaperMetadata) -> float:
        if not paper.published_date:
            return 0.3
        age_days = (datetime.now() - paper.published_date).days
        if age_days < 90:
            return 1.0
        if age_days < 180:
            return 0.8
        if age_days < 365:
            return 0.6
        if age_days < 730:
            return 0.3
        return 0.1

    def _score_credibility(self, paper: PaperMetadata) -> float:
        score = 0.3  # baseline
        journal = (paper.journal or "").lower()
        if journal in self.HIGH_IMPACT_JOURNALS:
            score += 0.4
        elif journal in self.MID_IMPACT_JOURNALS:
            score += 0.2
        if paper.is_open_access:
            score += 0.1
        if paper.citation_count > 50:
            score += 0.2
        elif paper.citation_count > 10:
            score += 0.1
        return min(score, 1.0)

    def _score_novelty(
        self,
        paper: PaperMetadata,
        existing: list[ScoredPaper],
    ) -> float:
        if not existing:
            return 0.8
        max_sim = 0.0
        for sp in existing:
            sim = SequenceMatcher(
                None,
                paper.title.lower(),
                sp.paper.title.lower(),
            ).ratio()
            max_sim = max(max_sim, sim)
        return max(1.0 - max_sim, 0.0)

    # ── Credibility assessment ─────────────────────────

    def _assess_credibility(self, paper: PaperMetadata) -> CredibilityLevel:
        if paper.source == SourceType.BIORXIV:
            return CredibilityLevel.PRELIMINARY
        journal = (paper.journal or "").lower()
        high_journal = (
            journal in self.HIGH_IMPACT_JOURNALS
            or paper.source in self._HIGH_IMPACT_SOURCES
        )
        mid_journal = (
            journal in self.MID_IMPACT_JOURNALS
            or paper.source in self._MID_IMPACT_SOURCES
        )
        if high_journal and paper.citation_count > 5:
            return CredibilityLevel.HIGH
        if high_journal or (mid_journal and paper.citation_count > 5):
            return CredibilityLevel.MODERATE
        if paper.citation_count > 5:
            return CredibilityLevel.MODERATE
        return CredibilityLevel.PRELIMINARY

    # ── Combination suggestions ────────────────────────

    def _suggest_combinations(
        self,
        paper: PaperMetadata,
        profile: ResearcherProfile,
    ) -> list[str]:
        """Suggest stress/method combinations based on paper content.

        Identifies intersections between what the paper describes and
        what the researcher's profile targets that could yield novel
        experimental designs.
        """
        suggestions: list[str] = []
        text = f"{paper.title} {paper.abstract}".lower()

        # Find stresses mentioned in paper but NOT in researcher's focus
        all_stresses = ["drought", "nutrient", "temperature", "pathogen",
                        "heavy metal", "salinity", "light", "flooding"]
        researcher_stresses = {
            s.value.replace("_", " ") for s in profile.stress_types
        }
        for stress in all_stresses:
            if stress in text and stress not in researcher_stresses:
                suggestions.append(
                    f"Paper explores {stress} stress — consider combining "
                    f"with your {', '.join(researcher_stresses)} focus"
                )

        # Find methods mentioned that researcher doesn't use
        all_methods = [
            "hyperspectral", "thermal", "fluorescence", "root imaging",
            "lidar", "multispectral", "gas exchange",
        ]
        researcher_methods = {
            m.value.replace("_", " ") for m in profile.phenotyping_methods
        }
        for method in all_methods:
            if method in text and method not in researcher_methods:
                suggestions.append(
                    f"Paper uses {method} — could complement your "
                    f"{', '.join(researcher_methods)} approach"
                )

        return suggestions[:5]
