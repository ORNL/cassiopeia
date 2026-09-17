# Domain packs

Cassiopeia has two halves:

- **The engine** is domain-neutral: fetch → dedupe → enrich → score → index →
  retrieve → synthesize → verify → critique, plus storage, auth, the API and the
  dashboard shell.
- **A domain pack** carries everything that belongs to one science community:
  what researchers describe their work with, where to look for papers, how to
  word the prompts, and any community-specific checks.

Each deployment serves exactly one pack, with its own database, vector store
and Globus client. Corpora are never shared between communities.

```
domains/
└── <pack>/
    ├── domain.yaml      required — facets, sources, prompts, UI strings
    ├── hooks.py         optional — proposal evaluators, custom fetchers
    ├── ui/index.jsx     optional — React components for the dashboard
    └── tests/           optional — the pack's own tests
```

Select the pack with `DOMAIN_PACK` (a directory name under `domains/`, or a
path). While only one pack is installed, it is used by default. The dashboard
build reads the same variable to bundle the pack's `ui/` components.

`domains/plant_phenotyping/` (APPL) is the reference pack.
`tests/fixtures/materials_pack/` is a minimal one with no instruments and no
evaluators; the core test suite runs against it.

---

## `domain.yaml`

### Facets

Facets are the fields of a researcher profile. The engine gives them meaning
only through their **role**:

| Role | Used in queries | Weighted by priority |
| --- | --- | --- |
| `subject` | yes — one term per query; falls back to `query_default` when nothing is selected | relevance |
| `condition` | yes — one term per query; must be selected for synonym queries | relevance |
| `technique` | no — scored after retrieval | methodology |

A pack needs at least one `subject` or `condition` facet. Queries cross the
selected terms of every query facet, in declaration order, and each term is
widened into an OR-group of LLM synonyms. Researcher keywords widen the last
group.

```yaml
facets:
  - key: material                 # stable id — stored in profiles and scores
    label: Materials              # profile form title
    short_label: Material         # score bars, filters, chat summaries
    role: subject
    widget: cards                 # cards (shows `detail`) | chips | none
    select_all: false             # true: the dashboard submits every visible value
    annotate: true                # tag papers/proposals for the filter bars
    open_vocabulary: true         # false: unknown values are dropped
    query_default: material
    synonyms: 2                   # LLM synonyms added to the OR-group
    group_size: 3                 # max terms in the OR-group
    description: how well the paper's materials match   # LLM scoring rubric
    synonym_guidance: 2-3 synonyms (formula, common name).
    sort: {label: Material Fit, title: ...}             # extra sort button
    hints:                        # per-paper hints when the paper mentions an
      template: "Paper studies {term} — relate it to your {selected} work"
      terms: [conductivity, capacity fade]              # unselected term
    vocabulary:
      - value: lithium_iron_phosphate   # "_" becomes a space in queries/prompts
        label: LFP
        icon: "🔋"
        detail: LiFePO4                 # shown under the label by `cards`
        aliases: [lifepo4, lithium iron phosphate]   # annotation matches
        hidden: false                   # true: valid, but not offered in the UI
```

### Sources

```yaml
sources:
  - key: preprints                # stored on every paper
    label: Preprints
    backend: europepmc            # europepmc | arxiv | anything registered in hooks.py
    filter: "SRC:PPR"             # backend option (Europe PMC query prefix)
    access: open                  # open | paywall — dashboard grouping
    impact: low                   # high | mid | low — credibility fallback
    preprint: true                # always rated "preliminary"
    description: Preprints
    synonym_override: [first principles, simulation]
```

`synonym_override` is for a source whose coverage differs from the rest. There,
non-subject groups use the researcher's keywords, or these terms, instead of
the LLM synonyms. The plant pack uses it for arXiv.

### Credibility, prompts, context, critique, UI

```yaml
credibility:
  high_impact_journals: [nature materials]      # lower-case journal titles
  mid_impact_journals: [journal of power sources]

prompts:
  field: electrochemistry          # "You are a {field} research strategist"
  proposal_noun: study             # "novel study designs", "Study idea from this paper"
  theme_example: interface stability
  citation_example: "..."          # example rationale with [paper_id] tags
  contradiction_hint: cell chemistry differences
  critique_subject: battery materials study
  synonym_rules: Do NOT include instrument names.
  scoring_guidance: ...            # extra paragraph for the scoring prompt
  fallback_query: battery materials

# Filled by the deployment, not the researcher; shown to the scoring and
# critique prompts, and available to evaluators.
context:
  - key: beamlines
    label: Available beamlines
    env: FACILITY_BEAMLINES        # comma-separated values
    default: none

critique:
  dimensions:                      # extra list dimensions next to "confounds"
    - key: safety_concerns
      label: Safety
      description: lab safety risks

ui:
  app_name: CASSIOPEIA
  document_title: ...
  subtitle: ...
  research_placeholder: ...
  anchor_placeholder: ...
  keyword_examples: ...            # chat prompt
  priority_descriptions: {relevance: ..., methodology: ...}

# Only for packs that predate domain packs: field renames applied once to an
# existing database.
legacy:
  facets: {old_profile_field: facet_key}
  scores: {old_score_field: facet_key}
  context: {old_profile_field: context_key}
```

---

## `hooks.py`

The loader imports it once. It may define:

- **`EVALUATORS`**: passes run over synthesized proposals. The result is
  stored under `proposal[evaluator.key]`.

  ```python
  class ReviewEvaluator:
      key = "review"
      label = "Reviewing proposals…"          # progress text

      def applies(self, context):             # skipped when False
          return bool(context.get("beamlines"))

      async def evaluate(self, proposal, context, llm_kwargs):
          ...                                 # return a dict, or None on failure

      def summary(self, result):              # optional short badge for chat
          return "✓"

  EVALUATORS = (ReviewEvaluator(),)
  ```

- **`setup()`**: called on load. Use it to register a fetcher backend for a
  repository the engine doesn't know:

  ```python
  from utils.source_fetchers import BaseFetcher, register_backend

  class InspireFetcher(BaseFetcher):
      async def fetch(self, query, max_results=20): ...
      async def fetch_full_text(self, paper_id): ...
      # optional: fetch_full_text_structured, lookup_abstract

  def setup():
      register_backend("inspire", InspireFetcher)
  ```

  A fetcher receives its `SourceInfo` as `self.source`; pack options such as
  `filter` are in `self.source.options`.

## `ui/index.jsx`

The dashboard renders facets, sources, labels, critique dimensions and
progress stages from `GET /api/domain`, so a pack needs no UI code to work.
To render evaluator results richly, default-export components keyed by
evaluator key:

```jsx
export default {
  proposalBadges: { review: ReviewBadge },   // card header
  proposalPanels: { review: ReviewPanel },   // below the critique
};
```

Each component receives the evaluator result as `result`. Without a component,
the panel shows `result.note`. Imports of `react` and `prop-types` resolve to
the dashboard's own copies.

---

## Checklist for a new community

1. Copy `tests/fixtures/materials_pack/` to `domains/<pack>/` and edit
   `domain.yaml`.
2. Set `DOMAIN_PACK=<pack>` in `.env`. Once more than one pack is installed,
   every deployment must set it.
3. Run the tests: `pytest` (core suite plus the pack's tests), and
   `pytest -m integration` to check that the sources answer.
4. Build and start: `docker compose build && docker compose up`. The dashboard
   image bundles the pack's `ui/` for the `DOMAIN_PACK` passed at build time.
5. Give the deployment its own Globus client and group allowlist.

`domains/plant_phenotyping/tests/test_core_neutrality.py` fails the build if
plant vocabulary leaks into the engine or dashboard. Consider a similar guard
for your pack.
