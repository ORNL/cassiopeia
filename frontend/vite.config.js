// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

import { existsSync, readdirSync, readFileSync } from "node:fs";
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

// Domain pack UI. Mirrors the backend rule: DOMAIN_PACK names a directory under
// domains/ (or a path); when unset, the only installed pack is used. A pack
// without ui/index.jsx gets the empty plugin set.
const domainsDir = resolve(here, "../domains");
function packDir() {
  const ref = process.env.DOMAIN_PACK?.trim();
  if (ref) return existsSync(ref) ? resolve(ref) : resolve(domainsDir, ref);
  const packs = existsSync(domainsDir)
    ? readdirSync(domainsDir).filter((d) => existsSync(resolve(domainsDir, d, "domain.yaml")))
    : [];
  if (packs.length !== 1) {
    throw new Error(`Set DOMAIN_PACK to one of the installed domain packs: ${packs.join(", ") || "(none found)"}`);
  }
  return resolve(domainsDir, packs[0]);
}
const packUiEntry = resolve(packDir(), "ui/index.jsx");
const domainUi = existsSync(packUiEntry) ? packUiEntry : resolve(here, "src/noDomainUi.js");

export default defineConfig({
  plugins: [react(), ...(hasLocalCert ? [] : [basicSsl()])],
  resolve: {
    alias: { "@domain-ui": domainUi },
    // Pack components live outside this directory; make their imports of
    // shared libraries resolve to this app's copies.
    dedupe: ["react", "react-dom", "prop-types"],
  },
  server: {
    fs: { allow: [here, dirname(domainUi)] },
    https: hasLocalCert
      ? { cert: readFileSync(certPath), key: readFileSync(keyPath) }
      : true,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
