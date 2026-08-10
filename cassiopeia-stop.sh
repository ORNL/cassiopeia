#!/bin/bash

# Cassiopeia Stop Script for macOS
# Stops all Docker containers for Cassiopeia

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "🛑 Stopping Cassiopeia..."
docker compose down

if [ $? -eq 0 ]; then
    echo "✅ Cassiopeia stopped."
else
    echo "❌ Failed to stop Cassiopeia."
    exit 1
fi
