#!/bin/bash

# Cassiopeia Start Script for macOS
# Starts Docker (if needed) and launches Cassiopeia via Docker Compose

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "🚀 Starting Cassiopeia..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "📦 Docker is not running. Starting Docker Desktop..."
    open -a Docker
    echo "⏳ Waiting for Docker to be ready..."

    # Wait up to 60 seconds for Docker to be ready
    TRIES=0
    while ! docker info > /dev/null 2>&1; do
        sleep 5
        TRIES=$((TRIES + 1))
        if [ $TRIES -ge 12 ]; then
            echo "❌ Docker did not start in time. Please start Docker Desktop manually and try again."
            exit 1
        fi
    done
    echo "✓ Docker is ready."
fi

# Start services
echo "🐳 Starting Docker containers..."
docker compose up -d

if [ $? -ne 0 ]; then
    echo "❌ Failed to start Cassiopeia."
    exit 1
fi

echo "✓ Cassiopeia is starting..."
echo "   Dashboard → http://localhost:5173"
echo "   API       → http://localhost:8000"
echo ""
echo "⏳ Waiting for services to be ready (20 seconds)..."
sleep 20

echo "🌐 Opening dashboard in browser..."
open http://localhost:5173

echo "✅ Done! Cassiopeia is running."
echo "   To stop: run cassiopeia-stop.sh"
