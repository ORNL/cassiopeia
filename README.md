# Cassiopeia
## Context-Aware Semantic Search for Inspiring Original Plant Experiments and Investigations at APPL  

An [Academy](https://github.com/proxystore/academy)-based agent that continuously monitors plant biology literature, scores retrieved papers against a researcher's profile, and proposes novel experiment combinations.

---

## What it does

### Sources

The agent queries eight literature sources in parallel on every search cycle:

| Source | Access | Backend |
| --- | --- | --- |
| bioRxiv | Open | Europe PMC (preprint filter) |
| PubMed / MEDLINE | Open (abstracts) | Europe PMC |
| PLoS ONE | Open | Europe PMC |
| Frontiers | Open | Europe PMC |
| Nature Communications | Abstracts | Europe PMC |
| New Phytologist | Abstracts | Europe PMC |
| Plant Physiology | Abstracts | Europe PMC |
| arXiv | Open | arXiv Atom API |

All sources are free and require no API keys.

### Query generation

Queries are generated from a **researcher profile** that captures:

- Plant species of interest (poplar, arabidopsis, soybean, …)
- Stress types (drought, salinity, nutrient, temperature, …)
- Phenotyping methods (hyperspectral imaging, root imaging, …)
- Free-text expertise keywords
- A time window (default: last 12 months, configurable up to 10 years)

The `QueryGenerator` builds **species × stress** combinations and dispatches one query per source. Up to 20 papers are retrieved per query.

> **Design note — why methods are excluded from queries.** Phenotyping methods are assessed *post-retrieval* by the LLM scorer, which handles synonyms and paraphrases (e.g. *"VNIR spectroscopy"* → hyperspectral imaging). Pre-filtering at query time would silently discard relevant papers whose abstracts use different but equivalent terminology.

### Scoring pipeline

Each retrieved paper goes through a six-dimensional relevance score:

| Dimension | How it is computed |
| --- | --- |
| `species_match` | **LLM** reads the abstract and assesses organism overlap |
| `stress_match` | **LLM** assesses stress-type alignment |
| `method_match` | **LLM** assesses methodological overlap **against the researcher's available instruments** |
| `recency` | Deterministic: age of publication (days) |
| `credibility` | Deterministic: journal tier + citation count + open-access |
| `novelty` | Deterministic: title similarity against already-scored papers |

The final `overall` score is a researcher-weighted combination:

```text
overall = (relevance_weight × (species + stress) / 2
         + novelty_weight   × novelty
         + method_weight    × method_match
         + reprod_weight    × credibility) / total_weight
```

### Where the LLM is used

The three text-dependent scoring dimensions (species, stress, method) are evaluated by an LLM that reads the abstract. This handles synonyms, genus–species variations, and methodological paraphrases that literal string matching cannot resolve (e.g. *"Populus under water deficit"* or *"VNIR spectroscopy"*).

Importantly, **search queries are kept broad** — equipment is never injected into them. Papers requiring instruments not available at the facility are retrieved just as any other paper would be; the equipment constraint is applied *after* retrieval, at the scoring and feasibility stages. This avoids pre-filtering out potentially relevant science simply because the method name in the abstract doesn't exactly match an instrument name.

One LLM call is made per paper:

```text
Researcher profile:
  Species  : poplar, arabidopsis
  Stresses : drought, nutrient
  Methods  : hyperspectral_imaging, root_imaging
  Keywords : root architecture, nitrogen uptake
  Available instruments: VNIR hyperspectral imaging, SWIR hyperspectral
                         imaging, 3D laser scanning, root imaging, ...

Paper title    : "Canopy reflectance signatures of water-stressed Populus ..."
Paper abstract : "... We applied VNIR spectroscopy to quantify ..."

→ species_match : 0.92
→ stress_match  : 0.85
→ method_match  : 0.91   ← elevated because VNIR maps to available instrument
→ hypothesis    : "Combining VNIR canopy reflectance with root architecture
                   phenotyping could reveal above-ground proxies for
                   below-ground nitrogen dynamics under drought."
```

The LLM also generates a **one-sentence experiment hypothesis** per paper — a concrete proposal for combining the paper's findings with the researcher's work, returned in the same call at no extra cost.

Results are **cached by `paper_id`** inside `LLMPaperScorer`, so repeated ranking calls (e.g. when the dashboard re-sorts) do not re-invoke the model. Papers without abstracts fall back to deterministic keyword scoring automatically.

**Model:** configured via `LLM_SCORING_MODEL` (default: `anthropic/claude-haiku-4-5-20251001`). Haiku is chosen for speed and cost (~$0.0002 per paper). At 20 papers × 8 sources, a full search cycle costs roughly **$0.03** in LLM API calls.

### Cross-paper proposal synthesis

After every search cycle, the `RAGAgent` retrieves the most semantically relevant abstracts from ChromaDB and sends them **all together** in a single LLM call. The model reasons across papers to produce 4–5 experiment proposals that draw on findings from multiple sources simultaneously — not just per-paper hints.

Each proposal includes:

- **Theme** — a 2–4 word label used to group proposals by research axis
- **Suggestion** — a concrete, one-paragraph experiment design
- **Rationale** — what gap or opportunity the combination addresses
- **Key insights** — the specific finding from each supporting paper that motivates the proposal
- **Supporting papers** — the papers the proposal draws from
- **Novelty warning** — flagged if the proposal is semantically similar to already-indexed content (ChromaDB distance < 0.3)

Previously liked proposals (see *Feedback loop* below) are injected as a steering context so the model explores new territory rather than repeating familiar suggestions.

The per-paper `suggested_combinations` field (populated during scoring at no extra LLM cost) is shown in a separate panel as a quick-signal complement.

### Feedback loop

Users can rate proposals with 👍 / 👎 directly in the dashboard. Ratings are stored in SQLite (`ratings` table). On the next search, liked proposals are loaded from the database and passed to `synthesize_combinations` to steer generation away from already-approved directions.

### Contradiction detection

After indexing, the `RAGAgent.detect_contradictions()` action sends the top retrieved abstracts together to an LLM reviewer. It identifies pairs of papers with conflicting claims (e.g. opposite effects of a treatment, disagreeing mechanistic models) and suggests possible explanations for the discrepancy. Results appear in the **Contradictions** tab.

### Feasibility filter

After cross-paper proposals are generated, `RAGAgent.assess_feasibility()` passes each proposal back to the LLM together with the facility's instrument list. The LLM is asked whether the proposed measurements can be made with the available instruments, accounting for synonyms (e.g. *"canopy reflectance spectroscopy"* → VNIR hyperspectral imaging). Each proposal is annotated with:

- `feasible` — `true`, `false`, or `"partial"`
- `missing_equipment` — list of instruments the proposal needs but the facility lacks
- `adaptation` — suggested workaround if a partial match exists
- `note` — plain-language summary shown in the dashboard

Proposals are shown with a coloured badge (✓ green / ~ amber / ✗ red) and a confidence percentage. This is purely an annotation layer — no proposals are hidden, leaving the researcher to decide whether an "infeasible" proposal is still worth pursuing via collaboration or equipment acquisition.

### Anchor-paper search

Users can supply a DOI or title fragment in the profile panel. The agent fetches the abstract from Europe PMC, then uses it as a semantic query seed against ChromaDB to surface the most similar papers in the knowledge base — useful for finding what has already been indexed on a paper you just read.

### Session history

Every `/api/search` call saves a session record (timestamp, profile snapshot, paper and proposal counts) to SQLite. The last 8 sessions are displayed in the profile panel, giving a quick overview of how the search has evolved over time.

### MCP server (APPL-Agent integration)

`mcp_server.py` wraps the same Academy agents as the FastAPI server but exposes them as [MCP](https://pypi.org/project/mcp/) tools via the Streamable HTTP transport. This makes Cassiopeia callable from APPL-Agent (or any other MCP-compatible orchestrator) using the same `session.call_tool()` pattern.

All tools return a `ToolResult` JSON envelope that matches APPL-Agent's schema:

```python
{"code": 301, "result": {...}, "extra": null, "tool_name": "search_literature"}
```

Status code convention (same as APPL-Agent): `2xx` = string result, `3xx` = dict result, `503` = agent not ready.

| MCP tool | Equivalent API endpoint | Description |
| --- | --- | --- |
| `search_literature` | `POST /api/search` | Full search cycle — returns papers, per-paper combos, RAG proposals with feasibility |
| `get_top_papers` | `GET /api/papers/{id}` | Top scored papers from last cycle |
| `detect_contradictions` | async after `/api/search` | Conflicting claims across indexed abstracts |
| `anchor_search` | `POST /api/anchor_search` | Papers similar to a DOI or title fragment |
| `ask_knowledge_base` | `POST /api/rag/synthesize` | Natural-language Q&A over indexed abstracts |
| `litminer_status` | `GET /api/status` | Operational status of both Academy agents |

**Install the MCP extra:**

```bash
pip install -e ".[mcp]"
```

**Start the MCP server:**

```bash
uvicorn mcp_server:asgi_app --port 8002
```

The MCP endpoint is then reachable at `http://localhost:8002/mcp`.  Point APPL-Agent at it by setting `MCP_HOST` / `MCP_PORT` in each system's `.env`.

### Autonomous monitoring

A background `@loop` (`monitor_sources`) re-executes all queries once per `SCAN_INTERVAL_HOURS` (default: 24 h). Deduplication is by DOI — papers already persisted in SQLite are skipped. New papers are scored and written to both the in-memory collection and the database.

### Persistence

All state survives process restarts:

| Store | Technology | What it holds |
| --- | --- | --- |
| Relational | SQLite (`cassiopeia.db`) | Researcher profiles, scored papers, LLM score cache, sessions, proposal ratings |
| Vector | ChromaDB (`chroma_db/`) | Paper abstracts as embeddings for semantic search |

The `LiteratureMiningAgent` writes exclusively to SQLite. The `RAGAgent` owns ChromaDB and syncs newly-scored papers from SQLite on each `index_new_papers()` call (triggered automatically after every search).

### RAG & conversational Q&A

The `RAGAgent` embeds paper abstracts with `sentence-transformers` (`all-MiniLM-L6-v2` by default) and stores them in ChromaDB. When a user asks a question in the Chat interface, a LangGraph `create_react_agent` loop uses a `search_knowledge_base` tool to retrieve relevant passages, then synthesises an answer with the configured `LLM_CHAT_MODEL`.

---

## Architecture

```text
┌──────────────────────────────────────────────────────┐
│  APPL-Agent (MCP orchestrator, external system)      │
│  calls tools via Streamable HTTP                     │
└──────────────────────┬───────────────────────────────┘
                       │ session.call_tool(...)
┌──────────────────────▼───────────────────────────────┐
│  MCP server (port 8002)   mcp_server.py              │
│  FastMCP — tools: search_literature, anchor_search,  │
│  detect_contradictions, ask_knowledge_base, …        │
│  ToolResult envelope (mirrors APPL-Agent schema)     │
└──────────────────────┬───────────────────────────────┘
                       │ same Academy @action calls
                       │ (shares agents with api_server.py
                       │  or runs its own instance)
┌──────────────────────▼───────────────────────────────┐
│  Frontend (React + Vite, port 5173 in dev)           │
│                                                      │
│  Landing page                                        │
│  ├── Dashboard mode  →  structured profile form      │
│  │                      results panel                │
│  │                      combinations panel           │
│  └── Chat mode       →  iframe → Chainlit (8001)     │
└──────────────────┬───────────────────────────────────┘
                   │ POST /api/search
                   │ POST /api/preview_queries
                   │ POST /api/extract_keywords
                   │ POST /api/feedback
                   │ POST /api/anchor_search
                   │ POST /api/rag/synthesize
                   │ GET  /api/status  /api/rag/status
                   │ GET  /api/sessions/{researcher_id}
                   │ GET  /api/config
┌──────────────────▼───────────────────────────────────┐
│  FastAPI server (port 8000)   api_server.py          │
│  • loads .env                                        │
│  • bridges HTTP ↔ Academy context vars               │
└────────┬─────────────────────────┬───────────────────┘
         │ Academy @action calls   │
┌────────▼──────────────┐ ┌───────▼───────────────────┐
│ LiteratureMiningAgent │ │ RAGAgent                  │
│                       │ │                           │
│ @action               │ │ @action index_new_papers  │
│   register_researcher │ │   SQLite → ChromaDB sync  │
│   trigger_search      │ │ @action query             │
│   ├─ QueryGenerator   │ │   semantic search         │
│   ├─ parallel fetch   │ │ @action synthesize        │
│   │  (Europe PMC /    │ │   LangGraph ReAct +       │
│   │   arXiv)          │ │   search_knowledge_base   │
│   └─ parallel LLM     │ │   tool  ──────────────────────→ LLM API
│      scoring          │ │ @action get_rag_status    │
│   get_top_papers      │ └───────────────────────────┘
│   get_combinations    │         │         │
│ @loop monitor_sources │   ChromaDB    SQLite
│   (every 24 h)        │   chroma_db/  cassiopeia.db
│   writes → SQLite     │
└───────────────────────┘

┌──────────────────────────────────────────────────────┐
│  Chainlit server (port 8001)   chainlit_app.py       │
│  LangGraph StateGraph (MemorySaver)                  │
│  ├─ classify node  → intent routing                  │
│  ├─ profile_chat   → gather profile, trigger search  │
│  ├─ qa             → POST /api/rag/synthesize        │
│  └─ general        → conversational fallback         │
└──────────────────────────────────────────────────────┘
```

---

## Configuration

Copy `.env.example` to `.env` and fill in the values for the provider(s) you use.

```bash
cp .env.example .env
```

### Key variables

| Variable | Default | Description |
| --- | --- | --- |
| `LLM_SCORING_MODEL` | `anthropic/claude-haiku-4-5-20251001` | Model for per-paper scoring. Prefer fast/cheap. |
| `LLM_SCORING_ENABLED` | `true` | Set to `false` to fall back to keyword matching (no API key needed). |
| `LLM_CHAT_MODEL` | `anthropic/claude-sonnet-4-6` | Model for the Chainlit conversation. Prefer a capable model. |
| `CHAINLIT_URL` | `http://localhost:8001` | URL where the browser reaches the Chainlit server. |
| `API_PORT` | `8000` | FastAPI port. |
| `CHAINLIT_PORT` | `8001` | Chainlit port. |

### Provider examples

Model strings follow [LiteLLM's convention](https://docs.litellm.ai/docs/providers): `<provider>/<model>` or just `<model>` for OpenAI-compatible endpoints.

#### Anthropic (default)

```env
LLM_SCORING_MODEL=anthropic/claude-haiku-4-5-20251001
LLM_CHAT_MODEL=anthropic/claude-sonnet-4-6
ANTHROPIC_API_KEY=sk-ant-...
```

#### OpenAI

```env
LLM_SCORING_MODEL=gpt-4o-mini
LLM_CHAT_MODEL=gpt-4o
OPENAI_API_KEY=sk-...
```

#### Azure OpenAI

```env
LLM_SCORING_MODEL=azure/my-gpt4o-mini-deployment
LLM_CHAT_MODEL=azure/my-gpt4o-deployment
AZURE_API_KEY=...
AZURE_API_BASE=https://my-org.openai.azure.com/
AZURE_API_VERSION=2024-02-01
```

#### Local (Ollama, no API key)

```env
LLM_SCORING_MODEL=ollama/llama3.2
LLM_CHAT_MODEL=ollama/llama3.1:70b
LLM_SCORING_ENABLED=true
# No API key needed
```

#### Disable LLM scoring entirely

```env
LLM_SCORING_ENABLED=false
# Keyword matching is used; no API key required at all.
```

---

## Setup

### Python environment

```bash
cd cassiopeia
python -m venv academy
source academy/bin/activate          # Windows: academy\Scripts\activate
pip install -e ".[dev]"
```

This installs all runtime dependencies: `academy-py`, `aiohttp`, `fastapi`, `uvicorn`, `litellm`, `chainlit`, `python-dotenv`, `httpx`, and `pydantic`.

### Frontend

```bash
cd frontend
npm install
```

---

## Starting the system

Two processes are always required (API server + frontend). A third is needed only if you want the Chat mode tab, which renders Chainlit in an iframe inside the frontend.

### Terminal 1 — API server

```bash
uvicorn api_server:app --port 8000 --reload
```

### Terminal 2 — Chainlit chat interface *(optional — only needed for Chat mode)*

```bash
chainlit run chainlit_app.py --port 8001 --headless
```

### Terminal 3 — Frontend dev server

```bash
cd frontend
npm run dev
# Opens at http://localhost:5173
```

Open [http://localhost:5173](http://localhost:5173). The landing page lets you choose between the structured Dashboard and the AI Chat interface.

### Production build

```bash
cd frontend
npm run build          # outputs to frontend/dist/
```

FastAPI automatically serves the built frontend at `/` when `frontend/dist/` exists, so only the API server and (optionally) Chainlit need to run in production.

---

## Project layout

```text
cassiopeia/
├── agents/
│   ├── literature_mining_agent.py   # Retrieval + scoring Academy agent (@action, @loop)
│   └── rag_agent.py                 # ChromaDB RAG, synthesis, contradictions, feasibility
├── models/
│   └── schemas.py                   # Dataclasses: Profile, Paper, Score, …
├── utils/
│   ├── query_generator.py           # species × stress combinatorics → SearchQuery list
│   ├── source_fetchers.py           # Europe PMC + arXiv HTTP clients
│   ├── persistence.py               # SQLite PaperStore (profiles, papers, sessions, ratings)
│   ├── paper_scorer.py              # Keyword-based fallback scorer
│   └── llm_scorer.py                # LiteLLM-backed scorer (primary)
├── frontend/
│   └── src/
│       ├── main.jsx                 # App root + landing page routing
│       ├── LandingPage.jsx          # Mode picker (Dashboard vs Chat)
│       └── Dashboard.jsx        # Structured profile + results UI
├── api_server.py                    # FastAPI bridge (HTTP ↔ Academy)
├── chainlit_app.py                  # Conversational profile interface (LangGraph)
├── pyproject.toml
└── .env.example                     # All configurable env vars
```
