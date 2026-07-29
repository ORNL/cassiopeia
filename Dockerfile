# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0
#
# Python backend image — used by both the API server and the Chainlit chat service.

FROM python:3.13-slim

# Trust PyPI hosts even when a corporate SSL proxy intercepts the connection
ENV PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org pypi.python.org"

# Build tools needed for native extensions (ChromaDB, onnxruntime)
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Layer 1: Python dependencies (cached unless pyproject.toml changes) ───────
# sitecustomize.py is installed first so pip (and all subsequent steps) benefit
# from the SSL patch when running behind a corporate proxy.
COPY docker/sitecustomize.py docker/preload.py pyproject.toml ./
RUN python -c "import site, shutil; shutil.copy('sitecustomize.py', site.getsitepackages()[0] + '/sitecustomize.py')" \
    && rm sitecustomize.py \
    && mkdir -p agents utils models \
    && touch agents/__init__.py utils/__init__.py models/__init__.py \
    && pip install --no-cache-dir -e ".[auth]" \
    && DISABLE_SSL_VERIFY=true python preload.py \
    && rm preload.py

# ── Layer 3: copy source (invalidates only this layer on code changes) ─────────
COPY agents/   agents/
COPY utils/    utils/
COPY models/   models/
COPY api/      api/
COPY api_server.py chainlit_app.py mcp_server.py ./
