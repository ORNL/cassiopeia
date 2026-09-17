# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Domain packs — the community-specific half of Cassiopeia.

The core pipeline (fetch → dedupe → enrich → score → index → retrieve →
synthesize → verify → critique) knows nothing about any science community.
A domain pack supplies everything that does:

* profile **facets** (what a researcher describes their work with), their
  controlled vocabularies and how they take part in queries and scoring;
* the literature **sources** to search and the journal credibility tiers;
* the wording of LLM prompts (field name, examples, extra rules);
* profile **context** such as a facility instrument list (optional);
* extra **critique dimensions** and proposal **evaluators** (optional);
* dashboard strings, and optionally React components (``ui/``).

A pack is a directory holding ``domain.yaml`` and, optionally, ``hooks.py``.
Select one with ``DOMAIN_PACK`` — a directory name under ``domains/`` or a
path.  When ``DOMAIN_PACK`` is unset and exactly one pack is installed under
``domains/``, that pack is used.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml

PACKS_DIR = Path(__file__).resolve().parent

FacetRole = Literal["subject", "condition", "technique"]

# Roles whose terms are combined into search queries, in declaration order.
QUERY_ROLES: tuple[str, ...] = ("subject", "condition")

# Which researcher priority weight each facet role contributes to.
ROLE_PRIORITY: dict[str, str] = {
    "subject": "relevance",
    "condition": "relevance",
    "technique": "methodology",
}


class DomainPackError(RuntimeError):
    """Raised when a domain pack cannot be found or is malformed."""


# ─────────────────────────────────────────────────────────────────────────────
# Pack schema
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VocabItem:
    value: str
    label: str
    icon: str = ""
    detail: str = ""
    aliases: tuple[str, ...] = ()
    hidden: bool = False

    def match_terms(self) -> tuple[str, ...]:
        return self.aliases or (term_text(self.value),)


@dataclass(frozen=True)
class Facet:
    key: str
    label: str
    role: FacetRole
    short_label: str = ""
    description: str = ""
    vocabulary: tuple[VocabItem, ...] = ()
    # False: values outside the vocabulary are dropped on registration.
    open_vocabulary: bool = True
    # Term used in place of an empty selection when building queries.
    query_default: str = ""
    # Number of LLM synonyms added to this facet's OR-group (0 = none).
    synonyms: int = 0
    synonym_guidance: str = ""
    group_size: int = 5
    # Tag papers/proposals with the vocabulary items whose aliases they mention.
    annotate: bool = False
    # Stems whose presence in a paper (outside the selection) triggers a hint.
    hint_terms: tuple[str, ...] = ()
    hint_template: str = ""
    # Dashboard hints — ignored by the backend.
    widget: str = "chips"
    select_all: bool = False
    sort_label: str = ""
    sort_title: str = ""

    @property
    def in_queries(self) -> bool:
        return self.role in QUERY_ROLES

    @property
    def priority(self) -> str:
        return ROLE_PRIORITY[self.role]

    def validate(self, values: list[str] | None) -> list[str]:
        values = [v for v in (values or []) if v]
        if self.open_vocabulary:
            return values
        known = {item.value for item in self.vocabulary}
        return [v for v in values if v in known]

    def matched(self, text: str) -> list[str]:
        """Visible vocabulary values whose aliases occur in *text* (already lower-cased)."""
        return [
            item.value for item in self.vocabulary
            if not item.hidden and any(t in text for t in item.match_terms())
        ]


@dataclass(frozen=True)
class SourceInfo:
    key: str
    label: str
    backend: str
    access: Literal["open", "paywall"] = "open"
    impact: Literal["high", "mid", "low"] = "low"
    preprint: bool = False
    description: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    # When set, non-subject OR-groups for this source use the researcher's
    # keywords (or these terms) instead of the LLM synonyms — for sources
    # whose coverage differs from the rest of the pack.
    synonym_override: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextSpec:
    """A profile context field filled by the deployment, not the researcher."""

    key: str
    label: str
    env: str = ""
    default: str = ""


@dataclass(frozen=True)
class CritiqueDimension:
    key: str
    label: str
    description: str = ""


class ProposalEvaluator(Protocol):
    """Pack-provided pass that annotates synthesized proposals.

    The result of ``evaluate`` is stored under ``proposal[key]``.
    """

    key: str
    label: str

    def applies(self, context: dict[str, list[str]]) -> bool: ...

    # Optional: ``summary(result) -> str`` gives a short badge for chat output.

    async def evaluate(
        self,
        proposal: dict[str, Any],
        context: dict[str, list[str]],
        llm_kwargs: dict[str, Any],
    ) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class Prompts:
    field: str = "science"
    proposal_noun: str = "study"
    theme_example: str = "cross-method validation"
    citation_example: str = ""
    contradiction_hint: str = "differences in study system or conditions"
    critique_subject: str = "research study"
    synonym_rules: str = ""
    scoring_guidance: str = ""
    fallback_query: str = ""


@dataclass
class DomainPack:
    name: str
    root: Path
    title: str
    facets: tuple[Facet, ...]
    sources: dict[str, SourceInfo]
    prompts: Prompts
    high_impact_journals: frozenset[str]
    mid_impact_journals: frozenset[str]
    context_specs: tuple[ContextSpec, ...] = ()
    critique_dimensions: tuple[CritiqueDimension, ...] = ()
    evaluators: tuple[ProposalEvaluator, ...] = ()
    ui: dict[str, Any] = field(default_factory=dict)
    legacy: dict[str, dict[str, str]] = field(default_factory=dict)

    # ── facet helpers ────────────────────────────────────────────────────────

    @cached_property
    def query_facets(self) -> tuple[Facet, ...]:
        return tuple(f for f in self.facets if f.in_queries)

    def validate_facets(self, facets: dict[str, list[str]] | None) -> dict[str, list[str]]:
        """Keep only declared facets and, for closed vocabularies, known values."""
        facets = facets or {}
        return {f.key: f.validate(facets.get(f.key)) for f in self.facets}

    def annotate(self, text: str) -> dict[str, list[str]]:
        text = text.lower()
        return {f.key: f.matched(text) for f in self.facets if f.annotate}

    # ── context helpers ──────────────────────────────────────────────────────

    def load_context(self) -> dict[str, list[str]]:
        """Deployment-provided context values (e.g. from environment variables)."""
        ctx: dict[str, list[str]] = {}
        for spec in self.context_specs:
            raw = os.environ.get(spec.env, "") if spec.env else ""
            ctx[spec.key] = [v.strip() for v in raw.split(",") if v.strip()]
        return ctx

    def context_lines(self, context: dict[str, list[str]] | None) -> list[tuple[str, str]]:
        """(label, comma-joined values) for every declared context field."""
        context = context or {}
        return [
            (spec.label, ", ".join(context.get(spec.key, [])) or spec.default or "none")
            for spec in self.context_specs
        ]

    def render_context_block(self, context: dict[str, list[str]] | None) -> str:
        """Bulleted context block for prompts; empty when nothing is declared."""
        context = context or {}
        blocks = []
        for spec in self.context_specs:
            values = context.get(spec.key, [])
            bullets = "\n".join(f"  - {v}" for v in values) or "  (none specified)"
            blocks.append(f"{spec.label}:\n{bullets}")
        return "\n\n".join(blocks)

    def active_evaluators(self, context: dict[str, list[str]] | None) -> list[ProposalEvaluator]:
        return [e for e in self.evaluators if e.applies(context or {})]

    # ── dashboard manifest ───────────────────────────────────────────────────

    def manifest(self) -> dict[str, Any]:
        """Everything a frontend needs to render this pack's profile and results."""
        return {
            "name": self.name,
            "title": self.title,
            "ui": self.ui,
            "proposal_noun": self.prompts.proposal_noun,
            "facets": [
                {
                    "key": f.key,
                    "label": f.label,
                    "short_label": f.short_label or f.label,
                    "role": f.role,
                    "priority": f.priority,
                    "widget": f.widget,
                    "select_all": f.select_all,
                    "annotate": f.annotate,
                    "sort_label": f.sort_label,
                    "sort_title": f.sort_title,
                    "open_vocabulary": f.open_vocabulary,
                    "vocabulary": [
                        {"value": v.value, "label": v.label, "icon": v.icon, "detail": v.detail}
                        for v in f.vocabulary if not v.hidden
                    ],
                }
                for f in self.facets
            ],
            "sources": [
                {
                    "value": s.key,
                    "label": s.label,
                    "type": s.access,
                    "desc": s.description,
                }
                for s in self.sources.values()
            ],
            "context": [
                {"key": c.key, "label": c.label, "values": self.load_context().get(c.key, [])}
                for c in self.context_specs
            ],
            "critique_dimensions": [
                {"key": d.key, "label": d.label} for d in self.critique_dimensions
            ],
            "evaluators": [{"key": e.key, "label": e.label} for e in self.evaluators],
        }


def term_text(value: str) -> str:
    """Search/display form of a vocabulary value (``heavy_metal`` → ``heavy metal``)."""
    return value.replace("_", " ")


# ─────────────────────────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────────────────────────

def _tuple(values: Any) -> tuple:
    return tuple(values or ())


def _facet(raw: dict) -> Facet:
    hints = raw.get("hints") or {}
    sort = raw.get("sort") or {}
    return Facet(
        key=raw["key"],
        label=raw["label"],
        role=raw["role"],
        short_label=raw.get("short_label", ""),
        description=raw.get("description", ""),
        vocabulary=tuple(
            VocabItem(
                value=v["value"],
                label=v.get("label", term_text(v["value"])),
                icon=v.get("icon", ""),
                detail=v.get("detail", ""),
                aliases=_tuple(v.get("aliases")),
                hidden=v.get("hidden", False),
            )
            for v in raw.get("vocabulary") or ()
        ),
        open_vocabulary=raw.get("open_vocabulary", True),
        query_default=raw.get("query_default", ""),
        synonyms=raw.get("synonyms", 0),
        synonym_guidance=raw.get("synonym_guidance", "").strip(),
        group_size=raw.get("group_size", 5),
        annotate=raw.get("annotate", False),
        hint_terms=_tuple(hints.get("terms")),
        hint_template=hints.get("template", ""),
        widget=raw.get("widget", "chips"),
        select_all=raw.get("select_all", False),
        sort_label=sort.get("label", ""),
        sort_title=sort.get("title", ""),
    )


def _source(raw: dict) -> SourceInfo:
    known = {"key", "label", "backend", "access", "impact", "preprint",
             "description", "synonym_override"}
    return SourceInfo(
        key=raw["key"],
        label=raw.get("label", raw["key"]),
        backend=raw["backend"],
        access=raw.get("access", "open"),
        impact=raw.get("impact", "low"),
        preprint=raw.get("preprint", False),
        description=raw.get("description", ""),
        options={k: v for k, v in raw.items() if k not in known},
        synonym_override=_tuple(raw.get("synonym_override")),
    )


def _load_hooks(root: Path, name: str):
    path = root / "hooks.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"cassiopeia_domain_{name}_hooks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pack_root(ref: str | os.PathLike) -> Path:
    root = Path(ref)
    if not root.is_absolute() and not root.exists():
        root = PACKS_DIR / str(ref)
    if not (root / "domain.yaml").is_file():
        raise DomainPackError(f"No domain.yaml found for domain pack {str(ref)!r} ({root})")
    return root


def _check(name: str, facets: tuple[Facet, ...], sources: dict[str, SourceInfo]) -> None:
    if not facets:
        raise DomainPackError(f"Domain pack {name!r} declares no facets")
    unknown = [f.key for f in facets if f.role not in ROLE_PRIORITY]
    if unknown:
        raise DomainPackError(f"Facet {unknown[0]!r} has unknown role")
    if not any(f.in_queries for f in facets):
        raise DomainPackError(f"Domain pack {name!r} needs at least one subject or condition facet")
    if not sources:
        raise DomainPackError(f"Domain pack {name!r} declares no sources")


def _prompts(raw: dict) -> Prompts:
    return Prompts(**{k: v.strip() if isinstance(v, str) else v for k, v in raw.items()})


def load_domain(ref: str | os.PathLike) -> DomainPack:
    """Load a pack from a directory name under ``domains/`` or from a path."""
    root = _pack_root(ref)
    raw = yaml.safe_load((root / "domain.yaml").read_text(encoding="utf-8")) or {}
    name = raw.get("name", root.name)
    facets = tuple(_facet(f) for f in raw.get("facets") or ())
    sources = {s.key: s for s in (_source(s) for s in raw.get("sources") or ())}
    _check(name, facets, sources)

    hooks = _load_hooks(root, name)
    if hooks is not None and hasattr(hooks, "setup"):
        hooks.setup()

    credibility = raw.get("credibility") or {}
    critique = raw.get("critique") or {}
    return DomainPack(
        name=name,
        root=root,
        title=raw.get("title", name),
        facets=facets,
        sources=sources,
        prompts=_prompts(raw.get("prompts") or {}),
        high_impact_journals=frozenset(j.lower() for j in credibility.get("high_impact_journals") or ()),
        mid_impact_journals=frozenset(j.lower() for j in credibility.get("mid_impact_journals") or ()),
        context_specs=tuple(ContextSpec(**c) for c in raw.get("context") or ()),
        critique_dimensions=tuple(CritiqueDimension(**d) for d in critique.get("dimensions") or ()),
        evaluators=tuple(getattr(hooks, "EVALUATORS", ())),
        ui=raw.get("ui") or {},
        legacy=raw.get("legacy") or {},
    )


def installed_packs() -> list[str]:
    return sorted(p.name for p in PACKS_DIR.iterdir() if (p / "domain.yaml").is_file())


_current: DomainPack | None = None


def current_domain() -> DomainPack:
    """The pack this deployment serves (loaded once, from ``DOMAIN_PACK``)."""
    global _current
    if _current is None:
        ref = os.environ.get("DOMAIN_PACK", "").strip()
        if not ref:
            packs = installed_packs()
            if len(packs) != 1:
                raise DomainPackError(
                    "Set DOMAIN_PACK to one of the installed domain packs: "
                    + (", ".join(packs) or "(none found)")
                )
            ref = packs[0]
        _current = load_domain(ref)
    return _current


def set_domain(pack: DomainPack | str | os.PathLike | None) -> DomainPack | None:
    """Replace the active pack (tests, tooling). ``None`` resets to lazy loading."""
    global _current
    _current = pack if pack is None or isinstance(pack, DomainPack) else load_domain(pack)
    return _current
