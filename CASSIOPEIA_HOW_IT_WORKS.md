# CASSIOPEIA — How It Works

**Context-Aware Semantic Search for Inspiring Original Plant Experiments and Investigations at APPL**

---

## 1. What Is CASSIOPEIA?

CASSIOPEIA is an automated literature monitoring and experiment-design assistant for plant biologists. You describe your research profile once — which plants you study, which stresses interest you, which methods you use — and the system continuously scans eight scientific databases, scores every paper it finds against your profile, and surfaces the most relevant ones. It also generates cross-paper experiment proposals and can detect contradictions between papers in its knowledge base.

The key design principle is a strict separation of roles:

- **AI language models** handle conversation, understanding, and reasoning tasks.
- **Deterministic code** drives all data retrieval, storage, and result delivery.
- **Nothing is hallucinated.** Every paper shown to you was fetched from a real database, and every score or proposal is traceable back to a real abstract.

---

## 2. System Components

CASSIOPEIA has five main components that work together:

```
┌───────────────────────────────────────────────────────────────────┐
│  User interfaces                                                   │
│  ┌──────────────┐   ┌──────────────────┐   ┌────────────────────┐ │
│  │ Landing Page │   │ Dashboard (React) │   │  Chat (Chainlit)   │ │
│  └──────┬───────┘   └────────┬─────────┘   └────────┬───────────┘ │
└─────────┼────────────────────┼─────────────────────-┼─────────────┘
          │                    │                       │
          └────────────────────┼───────────────────────┘
                               │ HTTP (REST API)
                       ┌───────▼────────┐
                       │   API Server   │   ← FastAPI, port 8000
                       └───────┬────────┘
                               │ Academy RPC
              ┌────────────────┴────────────────┐
              │                                 │
   ┌──────────▼───────────┐        ┌────────────▼────────┐
   │  LiteratureMiningAgent│        │      RAGAgent        │
   │  (data engine)        │        │  (semantic layer)    │
   └──────────┬────────────┘        └──────────┬──────────┘
              │                                │
   ┌──────────▼────────────────────────────────▼──────────┐
   │                Persistent storage                      │
   │   SQLite (cassiopeia.db)   ChromaDB (chroma_db/)   │
   └───────────────────────────────────────────────────────┘
```

### 2.1 The API Server

The coordination layer that sits between the interfaces you see and the intelligence running in the background. When you click a button in the dashboard or send a message in the chat, the API server receives the request, forwards it to the appropriate backend component, and hands the result back to the interface. Think of it as the front desk: it does not make any decisions itself, it just makes sure requests reach the right place and responses come back in a usable form.

### 2.2 The LiteratureMiningAgent

The data engine. It:

- Saves researcher profiles, scored papers, scoring results, and session history to a database so everything survives restarts.
- Asks an LLM to generate scientifically varied search queries from each profile — synonyms, mechanistic terms, related concepts — one or more queries per target database.
- Sends those queries to two external bibliographic APIs: the **Europe PMC REST API** (which covers bioRxiv/medRxiv, PubMed, PLOS ONE, Frontiers, Nature Communications, New Phytologist, and Plant Physiology through a single endpoint) and the **arXiv Atom API**. No journal websites are contacted directly.
- Scores each returned paper using an LLM (species match, stress match, method match, one hypothesis sentence) plus three rule-based metrics (recency, journal credibility, novelty vs. already-seen papers).
- Runs continuously in the background to pick up new papers over time without requiring you to trigger anything manually.

> **Profile recognition across sessions:** Your identity is established by the name you enter on the landing page — "Alex Doe" always maps to the same internal ID (`alex_doe`). From your **second session onwards** (once at least one search has completed and your profile has been saved), the system retrieves your last-used settings from the database and shows them straight away, asking only "shall I run again with these settings?" Your paper knowledge base and all scoring results from previous sessions are also available immediately on reconnect.

### 2.3 The Semantic Search Agent

The semantic layer. It:

- Maintains a vector store — a database that finds papers by meaning rather than by exact keywords.
- Keeps that store in sync as new scored papers arrive from the data engine.
- Handles free-form questions about the knowledge base by retrieving relevant abstracts and synthesising an answer.
- Generates cross-paper experiment proposals by reasoning over multiple abstracts at once.
- Detects contradictions between papers.
- Finds papers semantically similar to an anchor paper you specify.

### 2.4 The Dashboard

A browser-based structured interface. You fill in forms to set your species, stresses, methods, scoring weights, and preferred sources. The dashboard then shows ranked papers, experiment proposals, feasibility assessments, contradiction detection, and semantic search. Useful when you know exactly what you want to configure.

### 2.5 The Chat Interface

A conversational interface that lets you describe your research in plain language. An AI assistant asks follow-up questions to build your profile, then triggers the search pipeline for you. Useful for a first-time setup or when you prefer to think out loud rather than fill in forms.

---

## 3. Data That Persists Between Sessions

Everything CASSIOPEIA learns is saved to disk. If you stop and restart the system, nothing is lost. Four categories of information are kept:

**Your profile.** Species, stresses, keywords, source preferences, and scoring weights — everything you configured, ready to be reused next session.

**Your paper collection.** Every paper that was fetched and scored for you: title, authors, journal, DOI, abstract, all six relevance scores, credibility label, and the one-sentence hypothesis the AI generated for it.

**AI scoring results.** The first time a paper is evaluated by the AI, the result is saved. If the same paper is encountered again — in a later scan, or for a different search — the saved result is reused directly. No repeated AI calls, no extra cost.

**Your feedback.** Thumbs-up ratings on experiment proposals are remembered and used to steer future suggestions away from directions you have already explored.

In addition, the semantic search index (which allows finding papers by meaning rather than keywords) is rebuilt automatically from the paper collection whenever new papers arrive, so it is always up to date without any manual action.

---

## 4. The Complete Workflow

### Step 1 — You submit a research profile

**Via the Dashboard:** You fill in structured fields — species checkboxes, stress type selectors, source toggles, and sliders for four scoring dimensions (novelty, relevance, methodology, reproducibility).

**Via the Chat:** You describe your research in natural language. The chat assistant extracts your profile information using an LLM and asks about topics you have not yet covered. This happens in any order — you can answer all four topics at once or spread them across multiple messages.

The four topics the chat collects are:
1. **Plant species & stress conditions** — which organisms and which stresses
2. **Research keywords & time range** — your niche terms and how far back to search
3. **Literature sources & scoring priorities** — which databases and how to weight the four scoring dimensions
4. **Anchor paper** — optionally, a DOI or title of a paper that defines your research direction; the pipeline will also fetch papers semantically similar to it

Once all four topics are covered, the assistant shows a plain-text summary and asks you to confirm or correct it. No search has happened yet at this point.

### Step 2 — Profile is registered

Your confirmed profile is saved to SQLite under your researcher ID. If you registered before, the existing profile is overwritten. The system immediately generates a count of search queries (shown in the chat confirmation) to reassure you that the pipeline understood your profile.

### Step 3 — Search queries are generated

This is the first time an LLM touches your scientific content. A fast, inexpensive model receives your profile and generates a set of search queries, one or more per database. The model is instructed to use precise scientific synonyms (e.g. "water deficit" for drought, "osmotic stress", "ABA signalling"), avoid naive repetition of species+stress, and produce meaningfully different queries across the allowed databases. If the LLM fails or returns nothing usable, the system falls back to a simpler cross-product of species × stress combinations.

**Query diversity across repeated searches.** Every time queries are generated, the full list of queries used in all previous searches is passed to the LLM with the instruction not to repeat them verbatim, but to use synonyms, related mechanisms, or different phenotypic angles instead. This steers each successive scan towards unexplored corners of the literature rather than reproducing the same set of searches.

### Step 4 — Papers are fetched

The queries are sent in parallel to the configured sources. Two external bibliographic APIs are used: one that covers preprint servers and several plant biology journals through a single endpoint, and one dedicated to computational preprints. No journal websites are contacted directly. No API keys are required. Papers are returned with their title, authors, abstract, DOI, journal, publication date, open-access flag, and citation count.

Results are always retrieved in **reverse chronological order** (newest first). Each query returns up to 25 papers.

**How the system avoids re-processing known papers.** Before scoring, every retrieved paper is checked against the DOIs already stored in your knowledge base. Papers you have already seen are silently skipped — they are not re-scored and do not consume any AI quota. If most of the 25 results in a given query are already known, the system does not attempt to fetch a second page of older results: those would be less recent than what you already have, and therefore less useful for a monitoring tool whose goal is to track what is *newly published*. New papers will appear at the top of the next scan naturally as they are published.

### Step 5 — Each paper is scored against your profile

For open-access papers the system retrieves the full text before scoring: arXiv papers via their HTML rendering, PubMed Central papers via their open full-text endpoint. For paywalled journals the abstract from the search result is used instead. The first 6000 characters of whatever text is available are passed to the AI — enough to cover the abstract, introduction, and the beginning of the methods section in most papers. Papers with no text at all (rare) are scored by keyword matching only. The AI receives your profile alongside the text and scores three dimensions:

- **Species match** (0–1): how well the organisms in the paper match your species of interest
- **Stress match** (0–1): how well the paper's stress conditions match yours
- **Method match** (0–1): how well the paper's methods can be reproduced or extended with your available instruments

The model also generates one concrete hypothesis sentence per paper: a specific experiment combining insights from that paper with your research context.

Two further dimensions are computed by rule-based code (no LLM):

- **Recency** (0–1): newer papers score higher, calibrated to your time range window
- **Credibility** (0–1): higher-impact journals and open-access status score higher; preprints and unknown journals score lower

And one dimension uses text similarity against already-seen papers:

- **Novelty** (0–1): papers whose titles are unlike everything already in the knowledge base score higher

The six dimensions are combined using your four priority weights (novelty, relevance, methodology, reproducibility) into a single **overall score**, and a **credibility label** (High / Moderate / Preliminary / Conflicting) is assigned.

The result is saved to the knowledge base and queued for semantic indexing.

### Step 6 — Papers are made searchable by meaning

Newly scored papers are added to an internal index that captures the meaning of each paper's text, not just its words. This index runs entirely on the server — no data leaves your infrastructure. It is not a search box you interact with directly; instead, it powers the post-search features described in Section 5: the system uses it internally when generating experiment proposals, detecting contradictions, finding papers similar to a given anchor, and answering free-form questions in the chat. Because meaning rather than exact keywords is used, a paper discussing "reactive oxygen species" or "antioxidant defence" will be found when the system is looking for context on oxidative stress, even if neither phrase appears verbatim.

### Step 7 — Results are displayed

The dashboard (or chat) shows the ranked paper list. For each paper you see:
- Title, authors, journal, publication date, DOI link
- Source database and open-access status
- Six individual scores and an overall relevance score
- Credibility label (colour-coded: green=High, yellow=Moderate, red=Preliminary, orange=Conflicting)
- A one-sentence hypothesis generated during scoring

### Step 8 — The background monitor keeps scanning

After the initial search, a background loop continues to run for all registered researchers. At a configurable interval (default: every 24 hours), it regenerates queries using the LLM, fetches new papers from all sources, and scores only papers not already in the database (identified by DOI). This means your knowledge base grows automatically without you having to manually trigger searches.

**Seeing what is new since your last visit.** CASSIOPEIA records the time of each login. When you return — either by opening the dashboard or starting a chat session — the system compares the current knowledge base against what existed at your previous visit and reports how many papers arrived in the meantime. In the dashboard, a **🆕 New (N)** button appears in the Results filter bar; clicking it shows only those papers. In the chat, the welcome message tells you how many new papers were collected since your last session. Papers marked as new carry a small **NEW** badge in the results list so they remain easy to spot even when you are browsing the full collection.

---

## 5. Post-Search Capabilities

Once you have search results, both the dashboard and the chat offer further analysis.

### 5.1 Experiment proposal generation

Accessible from the dashboard's "Combinations" panel or by typing `combos` in the chat.

The system:

1. Ensures the internal semantic index is up to date.
2. Retrieves the 12 papers most relevant to your profile by meaning (not just keywords).
3. Sends **all 12 papers together** to the capable AI model, using up to 6,000 characters per paper — enough to cover the abstract, introduction, and beginning of the methods section for open-access papers, and the abstract alone for paywalled ones. The prompt specifically asks for cross-paper synergies: experiments that combine findings, methods, or observations from *multiple* papers.
4. Returns 4–5 proposals, each with: a 2–4 word theme label, a 1–2 sentence experiment description, a rationale explaining what gap it fills, which papers it draws from, and which specific findings motivate it.
5. Checks each proposal against the knowledge base to flag if a very similar experiment has already been described (novelty warning).

If you have previously rated proposals with thumbs-up, those liked proposals are fed back into the prompt so the LLM steers towards unexplored territory.

### 5.2 Feasibility assessment

For each proposal, the dashboard can ask the RAGAgent to assess whether the proposed experiment is executable with the facility's instruments. The LLM checks for synonym mappings (e.g. "canopy reflectance spectroscopy" may map to the facility's VNIR hyperspectral camera), identifies missing equipment, and suggests adaptations where a partial match exists. Each assessment returns: feasible (true/false/partial), confidence score, missing equipment list, adaptation suggestion, and a plain-language summary.

### 5.3 Contradiction detection

Accessible from the dashboard or by typing `contradictions` in the chat.

The system retrieves the 10 most semantically central papers in your knowledge base and sends them all to the capable AI model with a prompt asking for contradictory or conflicting findings — opposite effects of a treatment, disagreements about a mechanism, incompatible quantitative claims. Each reported contradiction includes which papers are involved, what each claims, and a possible resolution (e.g. "this discrepancy may reflect species-specific responses under different growth conditions").

### 5.4 Anchor-based similarity search

If you specify a paper DOI or title (during profile collection or at any time in the chat), the system:

1. Fetches that paper's abstract from the literature database.
2. Uses it as a starting point to search the internal semantic index for related papers.
3. Returns the most semantically similar papers from your knowledge base.

This is useful for finding papers that study the same mechanism or use similar methods as a paper you already know and trust.

### 5.5 Free-form Q&A

Via the chat (type any question about the papers), the AI reasons iteratively over your knowledge base: it decides what to search for, reads the results, and either searches again if it needs more context or synthesises an answer from what it found. This is the only place where the AI reasons in multiple steps — everywhere else, it is called once and produces a result directly.

---

## 6. Where AI Is Involved — Summary

| Role | When | What it does |
|---|---|---|
| Intent classification | Chat: your first message | Determines whether you want to run a search, find related papers, ask a question, or just chat |
| Profile extraction | Chat: during profile setup | Extracts structured fields from your natural-language descriptions |
| Query generation | Before each search | Generates diverse, synonym-rich search terms tailored to each database |
| Paper scoring | After each fetch | Scores species, stress, and method match; generates one hypothesis sentence per paper |
| Combination synthesis | On demand | Reasons over 12 papers at once to find cross-paper experiment ideas |
| Feasibility assessment | On demand | Checks whether each proposal is executable with your available instruments |
| Contradiction detection | On demand | Identifies conflicting claims across the top 10 papers |
| Q&A | On demand | Iteratively searches the knowledge base to answer your questions |

A fast, inexpensive model handles the high-volume tasks that run on every paper (query generation and scoring). A more capable model handles conversation and complex multi-paper reasoning (proposals, contradictions, Q&A).

**What the AI is explicitly not allowed to do:**

- Name, list, or describe papers (the chat assistant is instructed never to do this — papers only come from the literature databases)
- Fetch papers from databases (all retrieval is done by direct API calls, not the AI)
- Decide which papers are shown (ranking is a deterministic weighted formula, not an AI judgement)

---

## 7. The Two AI Reasoning Tasks in Detail

### Per-paper scoring

For each paper, the AI receives your profile (species, stresses, methods, keywords, available instruments) alongside the paper's text. It scores three dimensions on a 0–1 scale: how well the organisms match yours, how well the stress conditions match, and how well the methods could be reproduced or extended with your equipment. It also generates one concrete hypothesis sentence.

If a paper has no text, or if the AI call fails, the system falls back to keyword matching — counting overlapping terms between the paper and your profile. Less precise, but always available.

Scoring results are cached: if the same paper is encountered again in a future scan, the stored scores are reused and no AI call is made.

### Cross-paper proposal generation

Rather than evaluating papers one by one, the AI receives up to 12 papers simultaneously and is asked to find connections *between* them — what could you discover by combining the method from one paper with the finding from another? Previously liked proposals are shown to the AI so it steers towards unexplored directions.

---

## 8. The Eight Literature Sources

| Source | Type | Filter used |
|---|---|---|
| bioRxiv / medRxiv | Preprints | Europe PMC `SRC:PPR` |
| PubMed / MEDLINE | Peer-reviewed | Europe PMC `SRC:MED` |
| PLOS ONE | Open-access journal | Europe PMC journal filter |
| Frontiers journals | Open-access journals | Europe PMC publisher filter |
| Nature Communications | High-impact journal | Europe PMC journal filter |
| New Phytologist | Plant biology journal | Europe PMC journal filter |
| Plant Physiology | Plant biology journal | Europe PMC journal filter |
| arXiv | Preprints (computational) | arXiv Atom API |

Open-access sources (bioRxiv, PLOS ONE, Frontiers, arXiv) receive a credibility bonus in scoring, reflecting the higher findability of open abstracts and full texts.

---

## 9. What Happens on Restart

Everything is stored between restarts. On startup, the system reloads all researcher profiles, scored papers, AI scoring results, and the semantic index. No paper already scored will be re-scored, and no data needs to be re-fetched. The background monitor resumes and will run its next scan after the configured interval.

**In short: the system picks up exactly where it left off.** A researcher who registered a month ago and has been accumulating papers since will see all of those papers on reconnect, ranked and ready.
