# Cassiopeia — Feature Roadmap

Ideas not yet implemented, ordered roughly by estimated value / effort ratio.

---

## Recently completed

| Feature | Notes |
| --- | --- |
| Feasibility filter | `RAGAgent.assess_feasibility()` — badges on every proposal, non-hiding policy |
| Contradiction detection | `RAGAgent.detect_contradictions()` — Contradictions tab in dashboard |
| Anchor-paper search | `RAGAgent.find_similar_to_anchor()` — DOI / title → similar papers |
| Session history | SQLite `sessions` table, displayed in profile panel |
| Feedback loop | 👍/👎 on proposals, liked proposals steer next synthesis |
| Equipment in `.env` | `FACILITY_EQUIPMENT` served via `/api/config`, not injected into queries |
| LLM keyword extraction | `/api/extract_keywords` endpoint, editable chip UI |
| Real query preview | `/api/preview_queries` calls actual `QueryGenerator` — replaces client-side approximation |
| Extended time range | Slider up to 10 years (120 months) |

---

## High value, moderate effort

### Literature gap detection
Invert the RAG query: instead of finding papers *similar* to the profile, ask the LLM "given these papers and this profile, what important questions are NOT yet addressed?". Surface these gaps as a separate panel alongside the proposals.

### Protocol sketching
Extend the `synthesize_combinations` prompt to also output a rough experimental protocol (materials, controls, expected readouts) for the top-rated proposals. Triggered on demand (thumbs-up + "Generate protocol" button).

### Citation export
Add a `/api/export` endpoint that returns a BibTeX or RIS file for the papers backing the top-N proposals. Wire a download button in the dashboard.

---

## Medium value, lower effort

### Email / Slack digest
A scheduled action (`@loop`) that formats the top-5 new proposals since the last session and sends them via email (SMTP) or Slack webhook. Configurable via `.env`.

### Confidence interval on scores
Show a ± range on LLM-derived scores (species/stress/method match) by sampling the model twice with slightly different prompts and reporting the spread. Cheap proxy for uncertainty.

### Multi-researcher collaboration
Allow multiple researcher profiles to share a ChromaDB collection. Add a `/api/compare` endpoint that returns proposals relevant to *both* profiles — useful for cross-lab collaboration.

---

## Lower priority / exploratory

### Ontology-grounded vocabulary
Replace free-text species/stress/method fields with lookups from plant ontology (PO), trait ontology (TO), and CHEBI. Improves query precision and enables synonym expansion at query time.

### Interactive hypothesis refinement via chat
After a proposal is generated, let the user ask follow-up questions in the Chainlit interface ("What if we used sorghum instead of poplar?") and regenerate just that proposal rather than the full set.

### Auto-update profile from liked proposals
When the user consistently likes proposals from a particular theme (e.g. "root-canopy coupling"), automatically add those keywords to their `expertise_keywords` for the next search cycle.

### arXiv cs.LG / q-bio cross-domain sweep
Extend source fetchers to include computational biology and ML preprints from arXiv, scored for applicability to the researcher's phenotyping methods. Useful for discovering new image analysis techniques.
