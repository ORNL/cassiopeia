// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import basicSsl from "@vitejs/plugin-basic-ssl";

// The dev server speaks HTTPS because Globus requires HTTPS redirect URIs, and
// the registered redirect must match GLOBUS_REDIRECT_URI exactly. The
// certificate is self-signed and generated on first run, so the browser warns
// once per machine — accept it and the OAuth round trip works.
//
// The /api proxy target stays http: that hop is server-side, from the dev
// server to uvicorn on the same machine, and never crosses the network.
export default defineConfig({
  plugins: [react(), basicSsl()],
  server: {
    https: true,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
