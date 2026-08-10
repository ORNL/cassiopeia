#!/bin/bash

# Cassiopeia Installation Script for macOS
# Run this once after installing Docker Desktop

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo ""
echo "  Cassiopeia — macOS installer"
echo "  Project directory: $SCRIPT_DIR"
echo ""

# ── Docker check ──────────────────────────────────────────────────────────────
echo "[1/4] Checking Docker..."

if ! command -v docker &> /dev/null; then
    echo "❌ ERROR: Docker not found."
    echo "   Install Docker Desktop from https://www.docker.com/products/docker-desktop/"
    echo "   Then run this installer again."
    exit 1
fi

if ! docker info > /dev/null 2>&1; then
    echo "📦 Docker is not running — starting Docker Desktop..."
    open -a Docker
    echo "⏳ Waiting for Docker to be ready (this may take up to 60 seconds)..."

    TRIES=0
    while ! docker info > /dev/null 2>&1; do
        sleep 5
        TRIES=$((TRIES + 1))
        if [ $TRIES -ge 12 ]; then
            echo "❌ ERROR: Docker did not start in time."
            echo "   Please start Docker Desktop manually and re-run this installer."
            exit 1
        fi
    done
fi
echo "✓ Docker is running."

# ── .env setup ────────────────────────────────────────────────────────────────
echo "[2/4] Configuring environment..."

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp ".env.example" ".env"
        echo "✓ Created .env from template"
    else
        echo "❌ ERROR: .env.example not found."
        exit 1
    fi
else
    echo "✓ .env already exists."
fi

# ── Build images ──────────────────────────────────────────────────────────────
echo "[3/4] Building Docker images (first run may take several minutes)..."
docker compose build

if [ $? -ne 0 ]; then
    echo "❌ ERROR: docker compose build failed."
    exit 1
fi
echo "✓ Images built successfully."

# ── Make scripts executable ───────────────────────────────────────────────────
echo "[4/4] Making scripts executable..."
chmod +x "$SCRIPT_DIR/cassiopeia-start.sh"
chmod +x "$SCRIPT_DIR/cassiopeia-stop.sh"
echo "✓ Scripts are ready."

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "  Installation complete!"
echo ""
echo "  To start Cassiopeia: run cassiopeia-start.sh"
echo "  To stop:             run cassiopeia-stop.sh"
echo ""
