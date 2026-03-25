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

Install [Homebrew](https://brew.sh) if not already present, then:

```bash
brew install python@3.11 node tmux
```

Python 3.11 will be available as `python3.11`; create your virtual environment with:

```bash
python3.11 -m venv .venv
```

### Linux (Debian / Ubuntu)

```bash
sudo apt update
sudo apt install python3.11 python3.11-venv python3-pip tmux
```

Install Node.js 18+ via the [NodeSource](https://github.com/nodesource/distributions) setup script:

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

### Windows

#### Option A — WSL 2

WSL 2 gives you a full Linux environment and lets you use `launch.sh` normally.

```powershell
# Run in PowerShell as Administrator
wsl --install
```

Reboot when prompted, open the Ubuntu terminal, and follow the Linux instructions above.

#### Option B — Docker Compose (recommended for native Windows)

Docker runs the services in Linux containers, sidestepping all Windows compatibility issues. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) first, then right-click `cassiopeia-install.bat` and choose **Run as administrator**.

The installer will:

1. Verify Docker is running
2. Create `.env` from `.env.example` and open it for editing
3. Build the Docker images (several minutes on first run; subsequent builds are fast)
4. Place **Start Cassiopeia** and **Stop Cassiopeia** shortcuts on the desktop for all users

After installation, double-click **Start Cassiopeia** — the dashboard opens in your browser automatically. Use **Stop Cassiopeia** (or `cassiopeia-stop.bat`) to shut everything down.

> **Note:** Cassiopeia depends on `academy-py`, which uses the Unix-only `fcntl` module
> and does not run natively on Windows outside of Docker or WSL 2.

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

## Running without tmux (native Windows or minimal environments)

Start each service in a separate terminal, from the project root:

```bash
# Terminal 1 — API server
uvicorn api_server:app --port 8000 --reload

# Terminal 2 — Chat interface
chainlit run chainlit_app.py --port 8001 --headless

# Terminal 3 — Dashboard
cd frontend && npm run dev
```

Then open **[http://localhost:5173](http://localhost:5173)** in your browser.

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

## Resetting to a clean state

```bash
./clean.sh          # remove runtime data (database, vector store, caches)
./clean.sh --full   # also remove .venv and frontend/node_modules
```

Both modes ask for confirmation before deleting anything, and stop the running tmux session first if one is active.

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
