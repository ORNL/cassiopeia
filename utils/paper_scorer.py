# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Relevance scoring and ranking for retrieved papers."""

from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher

from domains import DomainPack, Facet, current_domain, term_text
from models.schemas import (
    CredibilityLevel,
    PaperMetadata,
    RelevanceScore,
    ResearcherProfile,
    ScoredPaper,
)


class PaperScorer:
    """Scores and ranks papers against a researcher's profile.

    Scoring dimensions:
    - one match score per facet declared by the domain pack
    - recency:        Preference for recent publications
    - credibility:    Based on journal, citation count, open-access status
    - novelty:        Uniqueness relative to already-scored papers
    """

    def __init__(self, domain: DomainPack | None = None) -> None:
        self._domain = domain

    @property
    def domain(self) -> DomainPack:
        return self._domain or current_domain()

    def score_paper(
        self,
        paper: PaperMetadata,
        profile: ResearcherProfile,
        existing_papers: list[ScoredPaper] | None = None,
    ) -> ScoredPaper:
        """Score a single paper against a researcher profile."""
        relevance = RelevanceScore(
            facet_scores=self.facet_scores(paper, profile),
            recency=self._score_recency(paper),
            credibility=self._score_credibility(paper),
            novelty=self._score_novelty(paper, existing_papers or []),
        )
        relevance.overall = self.overall(relevance, profile)

        return ScoredPaper(
            paper=paper,
            relevance=relevance,
            credibility=self._assess_credibility(paper),
            suggested_combinations=self._suggest_combinations(paper, profile),
        )

    def overall(self, relevance: RelevanceScore, profile: ResearcherProfile) -> float:
        priorities = {f.key: f.priority for f in self.domain.facets}
        return relevance.weighted_score(profile, priorities)

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

    def facet_scores(
        self,
        paper: PaperMetadata,
        profile: ResearcherProfile,
    ) -> dict[str, float]:
        text = f"{paper.title} {paper.abstract}".lower()
        return {
            f.key: self._score_facet(text, f, profile.terms(f.key))
            for f in self.domain.facets
        }

    @staticmethod
    def _score_facet(text: str, facet: Facet, values: list[str]) -> float:
        """Fraction of the selected terms found in the paper (0.5 when none selected)."""
        if not values:
            return 0.5
        matches = sum(1 for v in values if term_text(v).lower() in text)
        return min(matches / len(values), 1.0)

    def _score_recency(self, paper: PaperMetadata) -> float:
        if not paper.published_date:
            return 0.3
        age_days = (datetime.now(timezone.utc).replace(tzinfo=None) - paper.published_date).days
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
        if journal in self.domain.high_impact_journals:
            score += 0.4
        elif journal in self.domain.mid_impact_journals:
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
        source = self.domain.sources.get(paper.source)
        if source is not None and source.preprint:
            return CredibilityLevel.PRELIMINARY
        journal = (paper.journal or "").lower()
        impact = source.impact if source is not None else None
        high_journal = journal in self.domain.high_impact_journals or impact == "high"
        mid_journal = journal in self.domain.mid_impact_journals or impact == "mid"
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
        """Suggest combinations based on paper content.

        For each facet with hint terms, a term the paper mentions but the
        researcher did not select produces the facet's hint sentence.
        """
        suggestions: list[str] = []
        text = f"{paper.title} {paper.abstract}".lower()

        for facet in self.domain.facets:
            if not facet.hint_terms:
                continue
            selected = {term_text(v) for v in profile.terms(facet.key)}
            for term in facet.hint_terms:
                if term in text and term not in selected:
                    suggestions.append(
                        facet.hint_template.format(term=term, selected=", ".join(selected))
                    )

        return suggestions[:5]
