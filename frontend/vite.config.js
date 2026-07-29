// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import basicSsl from "@vitejs/plugin-basic-ssl";

// The dev server speaks HTTPS because Globus requires HTTPS redirect URIs, and
// the registered redirect must match GLOBUS_REDIRECT_URI exactly.
//
// Certificates come from ./scripts/dev-certs.sh when they exist — those are
// signed by a local CA you can add to the trust store, so the browser stops
// warning. Otherwise we fall back to a throwaway self-signed certificate,
// which works but makes the browser complain on every visit.
//
// The /api proxy target stays http: that hop is server-side, from the dev
// server to uvicorn on the same machine, and never crosses the network.
const here = dirname(fileURLToPath(import.meta.url));
const certPath = resolve(here, "certs/localhost.pem");
const keyPath = resolve(here, "certs/localhost-key.pem");
const hasLocalCert = existsSync(certPath) && existsSync(keyPath);

export default defineConfig({
  plugins: [react(), ...(hasLocalCert ? [] : [basicSsl()])],
  server: {
    https: hasLocalCert
      ? { cert: readFileSync(certPath), key: readFileSync(keyPath) }
      : true,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
