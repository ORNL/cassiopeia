#!/usr/bin/env bash
# Launch all three Cassiopeia services in a tmux session.
#
# Usage:
#   ./launch.sh          # start (or reattach)
#   ./launch.sh stop     # kill the session

SESSION="cassiopeia"

# ── Stop ────────────────────────────────────────────────────────────────────
if [[ "${1}" == "stop" ]]; then
    tmux kill-session -t "$SESSION" 2>/dev/null && echo "Session '$SESSION' stopped." \
        || echo "No session '$SESSION' found."
    exit 0
fi

# ── Reattach if already running ─────────────────────────────────────────────
if tmux has-session -t "$SESSION" 2>/dev/null; then
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
(sleep 3 && \
    if command -v wslview &>/dev/null; then wslview "https://localhost:5173"; \
    elif command -v xdg-open &>/dev/null; then xdg-open "https://localhost:5173"; \
    elif command -v open &>/dev/null; then open "https://localhost:5173"; \
    fi) &

echo "Attaching (Ctrl-b d to detach, ./launch.sh stop to kill)..."
tmux attach-session -t "$SESSION"
