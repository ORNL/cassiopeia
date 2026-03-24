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

# ── Resolve project root ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load .env so we can read port overrides
set -o allexport
[[ -f "$SCRIPT_DIR/.env" ]] && source "$SCRIPT_DIR/.env"
set +o allexport

API_PORT="${API_PORT:-8000}"
CHAINLIT_PORT="${CHAINLIT_PORT:-8001}"

# ── Create session (detached) ────────────────────────────────────────────────
tmux new-session -d -s "$SESSION" -x 220 -y 50

# Window 0 — API server
tmux rename-window -t "$SESSION:0" "api"
tmux send-keys -t "$SESSION:0" "cd '$SCRIPT_DIR' && uvicorn api_server:app --port $API_PORT --reload" Enter

# Window 1 — Chainlit chat
tmux new-window -t "$SESSION" -n "chat"
tmux send-keys -t "$SESSION:chat" "cd '$SCRIPT_DIR' && chainlit run chainlit_app.py --port $CHAINLIT_PORT --headless" Enter

# Window 2 — React dashboard
tmux new-window -t "$SESSION" -n "dashboard"
tmux send-keys -t "$SESSION:dashboard" "cd '$SCRIPT_DIR/frontend' && npm run dev" Enter

# Focus the api window
tmux select-window -t "$SESSION:api"

echo "Cassiopeia started in tmux session '$SESSION'."
echo "  API server  → http://localhost:$API_PORT"
echo "  Chat        → http://localhost:$CHAINLIT_PORT"
echo "  Dashboard   → http://localhost:5173"
echo ""

# Open the dashboard in the default browser after a short delay
(sleep 3 && \
    if command -v wslview &>/dev/null; then wslview "http://localhost:5173"; \
    elif command -v xdg-open &>/dev/null; then xdg-open "http://localhost:5173"; \
    elif command -v open &>/dev/null; then open "http://localhost:5173"; \
    fi) &

echo "Attaching (Ctrl-b d to detach, ./launch.sh stop to kill)..."
tmux attach-session -t "$SESSION"
