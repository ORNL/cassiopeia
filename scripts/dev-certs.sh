#!/usr/bin/env bash
# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0
#
# Generate a locally-trusted TLS certificate for the Vite dev server, so the
# browser stops warning about https://localhost:5173.
#
# Without this, vite.config.js falls back to @vitejs/plugin-basic-ssl, whose
# certificate is signed by nobody — functional, but the browser complains on
# every visit.
#
#   ./scripts/dev-certs.sh
#
# Writes frontend/certs/{ca.pem,localhost.pem,localhost-key.pem} (git-ignored)
# and prints the one-time command to trust the CA.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="$SCRIPT_DIR/frontend/certs"

command -v openssl >/dev/null || { echo "ERROR: openssl not found."; exit 1; }

mkdir -p "$CERT_DIR"
cd "$CERT_DIR"

# ── Local certificate authority (reused if it already exists) ────────────────
if [[ -f ca.pem && -f ca-key.pem ]]; then
    echo "Reusing existing local CA (ca.pem)."
else
    echo "Creating a local development CA..."
    openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
        -keyout ca-key.pem -out ca.pem \
        -subj "/CN=Cassiopeia local development CA" 2>/dev/null
fi

# ── Leaf certificate for localhost ───────────────────────────────────────────
# 397 days: browsers reject server certificates with longer lifetimes.
echo "Issuing a certificate for localhost..."
openssl req -newkey rsa:2048 -nodes -keyout localhost-key.pem \
    -out localhost.csr -subj "/CN=localhost" 2>/dev/null

openssl x509 -req -in localhost.csr -CA ca.pem -CAkey ca-key.pem \
    -CAcreateserial -out localhost.pem -days 397 -sha256 \
    -extfile <(printf 'subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1\nextendedKeyUsage=serverAuth\n') \
    2>/dev/null

rm -f localhost.csr ca.srl
chmod 600 ca-key.pem localhost-key.pem

echo
echo "Wrote $CERT_DIR/{ca.pem,localhost.pem,localhost-key.pem}"
echo "Vite will pick these up automatically on next start."
echo

# ── Trust the CA ─────────────────────────────────────────────────────────────
if grep -qi microsoft /proc/version 2>/dev/null; then
    WIN_CA="$(wslpath -w "$CERT_DIR/ca.pem" 2>/dev/null || echo "$CERT_DIR/ca.pem")"
    cat <<TRUST
One-time step — trust the CA in *Windows*, not WSL:

  The browser is a Windows process, so the CA must be in the Windows store.
  In an **Administrator** PowerShell or Command Prompt, run:

      certutil -addstore -f Root "$WIN_CA"

  Then fully restart the browser. Firefox keeps its own trust store: add the
  same file under Settings -> Privacy & Security -> Certificates -> View
  Certificates -> Authorities -> Import, and tick "Trust this CA to identify
  websites".
TRUST
else
    cat <<TRUST
One-time step — trust the CA:

      sudo cp "$CERT_DIR/ca.pem" /usr/local/share/ca-certificates/cassiopeia-dev.crt
      sudo update-ca-certificates

  Firefox keeps its own store: import ca.pem under Settings -> Privacy &
  Security -> Certificates -> View Certificates -> Authorities.
TRUST
fi
