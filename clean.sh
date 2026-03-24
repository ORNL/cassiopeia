#!/usr/bin/env bash
# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0
#
# Reset Cassiopeia to a clean state.
#
# Usage:
#   ./clean.sh           # remove runtime data only (DB, vector store, caches)
#   ./clean.sh --full    # also remove .venv and frontend/node_modules

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FULL=false
[[ "${1}" == "--full" ]] && FULL=true

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'

echo ""
echo -e "${YELLOW}Cassiopeia — cleanup${NC}"
if $FULL; then
    echo "  Mode : FULL (runtime data + virtual environment + node_modules)"
else
    echo "  Mode : runtime data only"
    echo "  Tip  : run with --full to also remove .venv and frontend/node_modules"
fi
echo ""

# ── Confirm ───────────────────────────────────────────────────────────────────
read -r -p "Proceed? [y/N] " _answer
[[ "${_answer,,}" == "y" ]] || { echo "Aborted."; exit 0; }
echo ""

# ── Stop running session ─────────────────────────────────────────────────────
if tmux has-session -t cassiopeia 2>/dev/null; then
    echo "Stopping tmux session 'cassiopeia'..."
    tmux kill-session -t cassiopeia
fi

# ── Runtime data ─────────────────────────────────────────────────────────────
_removed=()

_rm() {
    local target="$1"
    if [[ -e "$target" || -d "$target" ]]; then
        rm -rf "$target"
        _removed+=("$(realpath --relative-to="$SCRIPT_DIR" "$target")")
    fi
}

# Databases
_rm "$SCRIPT_DIR/cassiopeia.db"
_rm "$SCRIPT_DIR/cassiopeia.db-shm"
_rm "$SCRIPT_DIR/cassiopeia.db-wal"
_rm "$SCRIPT_DIR/chroma_db"

# ChromaDB files that may have landed at root due to misconfiguration
_rm "$SCRIPT_DIR/chroma.sqlite3"
# UUID-named HNSW segment directories at root
find "$SCRIPT_DIR" -maxdepth 1 -type d \
    -regextype posix-extended \
    -regex '.*/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' \
    -exec rm -rf {} + 2>/dev/null

# Chainlit runtime
_rm "$SCRIPT_DIR/.chainlit"
_rm "$SCRIPT_DIR/chainlit.md"
_rm "$SCRIPT_DIR/.files"

# Python caches
find "$SCRIPT_DIR" -type d -name "__pycache__" -not -path "*/.venv/*" -not -path "*/node_modules/*" \
    -exec rm -rf {} + 2>/dev/null
find "$SCRIPT_DIR" -name "*.pyc" -not -path "*/.venv/*" -delete 2>/dev/null

# ── Full clean ────────────────────────────────────────────────────────────────
if $FULL; then
    _rm "$SCRIPT_DIR/.venv"
    _rm "$SCRIPT_DIR/frontend/node_modules"
    _rm "$SCRIPT_DIR/frontend/dist"
    # egg-info left by `pip install -e .`
    find "$SCRIPT_DIR" -maxdepth 2 -name "*.egg-info" -exec rm -rf {} + 2>/dev/null
fi

# ── Report ────────────────────────────────────────────────────────────────────
if [[ ${#_removed[@]} -gt 0 ]]; then
    echo -e "${GREEN}Removed:${NC}"
    for item in "${_removed[@]}"; do
        echo "  $item"
    done
    echo "  + __pycache__ directories and .pyc files"
    $FULL && echo "  + *.egg-info directories"
else
    echo "Nothing to remove — already clean."
fi

echo ""
if $FULL; then
    echo "To reinstall:"
    echo "  python3 -m venv .venv && source .venv/bin/activate"
    echo "  pip install -e ."
    echo "  cd frontend && npm install && cd .."
fi
echo "Done."
