# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0
#
# Loaded automatically by Python at startup (before any user code).
# When DISABLE_SSL_VERIFY=true, patches every SSL entry-point so that ALL
# libraries — requests, urllib3, httpx (used by LiteLLM for LLM API calls), … — skip
# certificate verification on networks with a corporate SSL proxy.

import os
import ssl

if os.getenv("DISABLE_SSL_VERIFY", "").lower() in ("1", "true", "yes"):

    # ── urllib / requests ─────────────────────────────────────────────────────
    ssl._create_default_https_context = ssl._create_unverified_context

    # ── httpx (used by huggingface_hub for model downloads) ───────────────────
    # httpx calls ssl.create_default_context() directly, so we patch it to
    # return a context that skips hostname and certificate verification.
    _orig_create_default_context = ssl.create_default_context

    def _unverified_context(*args, **kwargs):
        ctx = _orig_create_default_context(*args, **kwargs)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # NOSONAR — intentional, gated by DISABLE_SSL_VERIFY
        return ctx

    ssl.create_default_context = _unverified_context

    # ── urllib3 warning suppression ───────────────────────────────────────────
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass
