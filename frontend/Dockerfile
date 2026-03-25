# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0
#
# Two-stage build: Node builds the React app, nginx serves the static output.
# No Node.js or npm needed at runtime — avoids SSL proxy issues in the container.

# ── Stage 1: build ───────────────────────────────────────────────────────────
FROM node:22-alpine AS builder

# Disable TLS verification for corporate proxy environments
ENV NODE_TLS_REJECT_UNAUTHORIZED=0

WORKDIR /app
COPY package.json package-lock.json* ./
# --include=dev ensures vite and other devDependencies are installed
RUN npm install --include=dev
COPY . .
RUN npm run build && ls /app/dist

# ── Stage 2: serve ───────────────────────────────────────────────────────────
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 5173
