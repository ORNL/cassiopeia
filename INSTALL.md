# Installing and testing Cassiopeia

## Prerequisites

| Tool | Minimum version | Check |
|------|-----------------|-------|
| Python | 3.11 | `python3 --version` |
| Node.js | 18 | `node --version` |
| npm | 9 | `npm --version` |
| tmux | any | `tmux -V` |
| git | any | `git --version` |

### macOS (Homebrew)

If any prerequisite is missing, install [Homebrew](https://brew.sh) first, then:

```bash
brew install python@3.11 node tmux
```

Python 3.11 will be available as `python3.11`; create your virtual environment with:

```bash
python3.11 -m venv .venv
```

---

You also need an API key for at least one supported LLM provider:
Anthropic, OpenAI, Azure OpenAI, Google Gemini, or Mistral.
(Alternatively, a local [Ollama](https://ollama.com) installation works without a cloud key.)

---

## Installation

### 1. Clone the repository

```bash
git clone git@code.ornl.gov:opal/cassiopeia.git
cd cassiopeia
```

### 2. Create and activate a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
```

### 3. Install Python dependencies

```bash
pip install -e .
```

Add `[dev]` for linting/testing tools, `[mcp]` for the MCP server:

```bash
pip install -e ".[dev,mcp]"
```

### 4. Install frontend dependencies

```bash
cd frontend
npm install
cd ..
```

### 5. Configure the environment

```bash
cp .env.example .env
```

Open `.env` in a text editor and fill in at minimum:

- **`ANTHROPIC_API_KEY`** (or whichever provider you use)
- **`LLM_SCORING_MODEL`** and **`LLM_CHAT_MODEL`** — already set to sensible defaults
- **`FACILITY_EQUIPMENT`** — comma-separated list of instruments at your facility

Everything else can be left at its default value for a first run.

---

## Running

```bash
./launch.sh
```

`launch.sh` will:
1. Validate that prerequisites and the `.env` file are in place.
2. Start three services inside a tmux session named `cassiopeia`:
   - **API server** (port 8000)
   - **Chat interface** (port 8001)
   - **Dashboard** (port 5173)
3. Open the dashboard in your default browser after a few seconds.

Navigate between tmux windows with **Ctrl-b n** / **Ctrl-b p**, and detach with **Ctrl-b d**.
Stop everything with:

```bash
./launch.sh stop
```

---

## Quick smoke test

Once all three services are up:

1. Open **http://localhost:8001** (chat interface).
2. The assistant will ask about your research interests. Give a short description, e.g.:
   > *"I study drought stress responses in maize using hyperspectral imaging."*
3. After a brief profile-extraction dialogue, say **"Search"** (or similar).
4. The system will generate queries, search literature sources, score results, and report back. Expect results within 30–60 seconds.
5. Click the dashboard link in the chat, or open **http://localhost:5173** directly, to explore scored papers.

If you see papers listed with relevance scores the installation is working correctly.

---

## Troubleshooting

**`launch.sh` exits with "Missing required tools"**
Install the listed tool (Python 3.11+, npm, or tmux) and retry.

**`launch.sh` exits with "No LLM API key found"**
Open `.env` and set at least one API key variable.

**No papers returned after a search**
- Check that your `.env` API key is valid.
- Confirm the scoring model name matches what your provider expects (see `.env` comments).
- The chat window in tmux shows live logs — look for error messages there.

**Port already in use**
Change `API_PORT`, `CHAINLIT_PORT`, and/or `DASHBOARD_URL` in `.env`, then restart.

**Frontend shows a blank page**
Run `cd frontend && npm install` to ensure dependencies are present, then restart.

**ChromaDB errors on first run**
The `chroma_db/` directory is created automatically on first launch; no action needed.
If you see permission errors, check that the project directory is writable.
