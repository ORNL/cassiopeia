#!/usr/bin/env bash
# Launch all three Cassiopeia services in a tmux session.
#
# Usage:
#   ./launch.sh          # start (or reattach)
#   ./launch.sh stop     # kill the session
#   ./launch.sh setup    # choose, create or edit the domain pack (before launch)

SESSION="cassiopeia"

open_browser() {
    if command -v wslview &>/dev/null; then wslview "$1"
    elif command -v xdg-open &>/dev/null; then xdg-open "$1"
    elif command -v open &>/dev/null; then open "$1"
    fi
}

# ── Stop ────────────────────────────────────────────────────────────────────
if [[ "${1}" == "stop" ]]; then
    tmux kill-session -t "$SESSION" 2>/dev/null && echo "Session '$SESSION' stopped." \
        || echo "No session '$SESSION' found."
    exit 0
fi

# ── Reattach if already running ─────────────────────────────────────────────
if [[ "${1}" != "setup" ]] && tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session '$SESSION' already running — attaching."
    tmux attach-session -t "$SESSION"
    exit 0
fi

# ── Prerequisites ────────────────────────────────────────────────────────────
_missing=()
command -v python3 &>/dev/null || _missing+=("python3 (≥3.11)")
command -v npm    &>/dev/null || _missing+=("npm (Node.js ≥18)")
command -v tmux   &>/dev/null || _missing+=("tmux")
if [[ ${#_missing[@]} -gt 0 ]]; then
    echo "ERROR: Missing required tools: ${_missing[*]}"
    echo "  Install them and retry. See INSTALL.md for details."
    exit 1
fi
# Python version check (need ≥3.11)
_pyver=$(python3 -c "import sys; print(sys.version_info[:2])" 2>/dev/null)
if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
    echo "ERROR: Python 3.11 or later is required (found $_pyver)."
    exit 1
fi

# ── Resolve project root ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Setup wizard ─────────────────────────────────────────────────────────────
# Serves the domain-pack wizard on this machine only, in place of the API
# server (the dashboard dev server proxies /api to port 8000 either way).
if [[ "${1}" == "setup" ]]; then
    if [[ -f "$SCRIPT_DIR/.env" ]]; then
        set -o allexport; source "$SCRIPT_DIR/.env"; set +o allexport
    fi
    if ss -tlnH "sport = :8000" 2>/dev/null | grep -q . || ss -tlnH "sport = :5173" 2>/dev/null | grep -q .; then
        echo "ERROR: port 8000 or 5173 is in use. Stop Cassiopeia first: ./launch.sh stop"
        exit 1
    fi
    echo "Installing frontend dependencies..."
    (cd "$SCRIPT_DIR/frontend" && npm install --silent) || {
        echo "ERROR: npm install failed in frontend/."
        exit 1
    }
    chmod -R +x "$SCRIPT_DIR/frontend/node_modules/.bin/" 2>/dev/null || true

    # Stop the dashboard dev server with the wizard (Ctrl-C).
    trap 'trap - EXIT INT TERM; kill 0 2>/dev/null' EXIT INT TERM
    (cd "$SCRIPT_DIR/frontend" && CASSIOPEIA_SETUP=1 npm run dev -- --strictPort >/dev/null 2>&1) &
    (sleep 3 && open_browser "https://localhost:5173/setup.html") &
    echo ""
    echo "Setup wizard → https://localhost:5173/setup.html  (self-signed cert — accept the browser warning)"
    echo "Press Ctrl-C when done, then start Cassiopeia with ./launch.sh"
    echo ""
    cd "$SCRIPT_DIR" && python3 -m uvicorn setup_server:app --host 127.0.0.1 --port 8000 --log-level warning
    exit 0
fi

# Load .env so we can read port overrides
if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
    echo "ERROR: .env not found. Copy .env.example to .env and fill in your API key(s)."
    exit 1
fi
set -o allexport
source "$SCRIPT_DIR/.env"
set +o allexport

# LLM API keys are configured per-researcher in the Settings UI and stored
# encrypted, so an env key is only an optional admin-level override — not a
# precondition for starting.
if [[ -z "$ANTHROPIC_API_KEY" && -z "$OPENAI_API_KEY" && -z "$AZURE_API_KEY" && -z "$GEMINI_API_KEY" && -z "$MISTRAL_API_KEY" ]]; then
    echo "NOTE: no LLM API key in .env — configure one per researcher in the"
    echo "      dashboard Settings (🔑) after launch."
fi

API_PORT="${API_PORT:-8000}"
# CHAINLIT_PORT="${CHAINLIT_PORT:-8001}"  # Chainlit disabled

# ── Authentication configuration ─────────────────────────────────────────────
# Run the server's own startup check here, in this terminal. Otherwise a
# misconfiguration kills uvicorn inside a tmux pane and the only symptom the
# user sees is the Vite proxy reporting ECONNREFUSED on port 8000.
_auth_check=$(cd "$SCRIPT_DIR" && python3 - <<'PYCHECK' 2>&1
import sys
sys.argv = ["uvicorn", "--host", "127.0.0.1"]   # what launch.sh runs below
try:
    from utils.auth import assert_safe_configuration
except Exception:
    sys.exit(0)          # import trouble surfaces later, with a real traceback
try:
    assert_safe_configuration()
except RuntimeError as exc:
    print(exc)
    sys.exit(1)
PYCHECK
) || {
    echo "ERROR: $_auth_check"
    exit 1
}

# ── Port conflict check ───────────────────────────────────────────────────────
for _port in "$API_PORT"; do
    if ss -tlnH "sport = :$_port" 2>/dev/null | grep -q .; then
        echo "ERROR: port $_port is already in use."
        echo "  If the Docker stack is running: docker compose down"
        echo "  Or set a different port: API_PORT=8080 ./launch.sh"
        exit 1
    fi
done

# ── Domain pack ──────────────────────────────────────────────────────────────
# Same idea: catch a missing pack selection, or a database filled by another
# pack, here rather than as a crash inside a tmux pane.
_pack_check=$(cd "$SCRIPT_DIR" && python3 - <<'PYCHECK' 2>&1
import sys
from pathlib import Path
try:
    from domains import DomainPackError, current_domain
    from utils.data_paths import default_db_path, stamped_pack
except Exception:
    sys.exit(0)          # import trouble surfaces later, with a real traceback
try:
    pack = current_domain()
except DomainPackError as exc:
    print(exc)
    sys.exit(1)
db = default_db_path()
owner = stamped_pack(Path(db))
if owner and owner != pack.name:
    print(f"{db} belongs to domain pack {owner!r}, but {pack.name!r} is selected.")
    sys.exit(1)
PYCHECK
) || {
    echo "ERROR: $_pack_check"
    echo "  Run ./launch.sh setup to choose or edit the domain pack."
    exit 1
}

# ── Install / refresh frontend dependencies ──────────────────────────────────
echo "Installing frontend dependencies..."
(cd "$SCRIPT_DIR/frontend" && npm install --silent) || {
    echo "ERROR: npm install failed in frontend/. Check Node.js version (≥18 required)."
    exit 1
}
# WSL may strip execute bits from node_modules binaries depending on mount options.
chmod -R +x "$SCRIPT_DIR/frontend/node_modules/.bin/" 2>/dev/null || true

# ── Pre-load embedding model ─────────────────────────────────────────────────
cassiopeia-preload || echo "WARNING: could not pre-load embedding model — will retry at startup."

# ── Create session (detached) ────────────────────────────────────────────────
tmux new-session -d -s "$SESSION" -x 220 -y 50

# Window 0 — API server
tmux rename-window -t "$SESSION:0" "api"
tmux send-keys -t "$SESSION:0" "cd '$SCRIPT_DIR' && uvicorn api_server:app --port $API_PORT" Enter

# Window 1 — Chainlit chat (disabled)
# tmux new-window -t "$SESSION" -n "chat"
# tmux send-keys -t "$SESSION:chat" "cd '$SCRIPT_DIR' && chainlit run chainlit_app.py --port $CHAINLIT_PORT --headless" Enter

# Window 1 — React dashboard
tmux new-window -t "$SESSION" -n "dashboard"
tmux send-keys -t "$SESSION:dashboard" "cd '$SCRIPT_DIR/frontend' && npm run dev" Enter

# Focus the api window
tmux select-window -t "$SESSION:api"

echo "Cassiopeia started in tmux session '$SESSION'."
echo "  API server  → http://localhost:$API_PORT"
echo "  Dashboard   → https://localhost:5173  (self-signed cert — accept the browser warning)"
# echo "  Chat        → http://localhost:$CHAINLIT_PORT"  (Chainlit disabled)
echo ""

# Open the dashboard in the default browser after a short delay
(sleep 3 && open_browser "https://localhost:5173") &

echo "Attaching (Ctrl-b d to detach, ./launch.sh stop to kill)..."
tmux attach-session -t "$SESSION"
