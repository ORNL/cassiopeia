// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

// Globus Auth for the Cassiopeia dashboard — OAuth 2.0 authorization code
// flow with PKCE.
//
// Two modes, decided by the backend's /api/auth/config:
//
//   enabled  — sign in through Globus; tokens live in sessionStorage and are
//              sent as a Bearer on every request.
//   disabled — local development: the researcher types a name, which becomes
//              the X-User-ID header so several identities can be exercised
//              without a Globus client.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { apiJson, configureApi } from "../api";

const AuthContext = createContext(null);

const AUTH_KEY = "cassiopeia_globus_auth";
const VERIFIER_KEY = "cassiopeia_pkce_verifier";
const STATE_KEY = "cassiopeia_pkce_state";
const DEV_USER_KEY = "cassiopeia_dev_user";

// ── PKCE helpers ──────────────────────────────────────────────────────────────

function randomString(length) {
  const charset = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => charset[b % charset.length]).join("");
}

function base64Url(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  bytes.forEach((b) => { binary += String.fromCharCode(b); });
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll(/=+$/g, "");
}

async function codeChallenge(verifier) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(digest);
}

// ── Token storage ─────────────────────────────────────────────────────────────

function readStoredAuth() {
  try {
    const raw = sessionStorage.getItem(AUTH_KEY);
    if (!raw) return null;
    const stored = JSON.parse(raw);
    if (stored.expiresAt && Date.now() > stored.expiresAt) {
      sessionStorage.removeItem(AUTH_KEY);
      return null;
    }
    return stored;
  } catch {
    return null;
  }
}

// ── Provider ──────────────────────────────────────────────────────────────────

export function AuthProvider({ children }) {
  const [config, setConfig] = useState(null);
  const [user, setUser] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const callbackHandled = useRef(false);

  const clearSession = useCallback(() => {
    sessionStorage.removeItem(AUTH_KEY);
    configureApi({ token: null, idToken: null });
    setUser(null);
  }, []);

  // Re-authenticate rather than leaving the UI in a broken half-signed-in state.
  const handleUnauthorized = useCallback(() => {
    clearSession();
    setError("Your session expired — please sign in again.");
  }, [clearSession]);

  const applyTokens = useCallback((tokens) => {
    sessionStorage.setItem(AUTH_KEY, JSON.stringify(tokens));
    configureApi({
      token: tokens.accessToken,
      idToken: tokens.idToken || null,
      onUnauthorized: handleUnauthorized,
    });
  }, [handleUnauthorized]);

  // Load auth configuration, then resolve the current identity.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      const cfg = await apiJson("/api/auth/config", {}, { enabled: false });
      if (cancelled) return;
      setConfig(cfg);
      configureApi({ onUnauthorized: handleUnauthorized });

      if (!cfg.enabled) {
        // Development mode: restore the last dev identity, if any.
        const devUser = sessionStorage.getItem(DEV_USER_KEY);
        if (devUser) {
          configureApi({ devUserId: devUser });
          const me = await apiJson("/api/auth/me");
          if (!cancelled && me) setUser(me);
        }
        if (!cancelled) setIsLoading(false);
        return;
      }

      // Returning from Globus with an authorization code?
      const params = new URLSearchParams(window.location.search);
      const code = params.get("code");
      const state = params.get("state");
      if (code && !callbackHandled.current) {
        callbackHandled.current = true;
        const verifier = sessionStorage.getItem(VERIFIER_KEY);
        const expectedState = sessionStorage.getItem(STATE_KEY);
        sessionStorage.removeItem(VERIFIER_KEY);
        sessionStorage.removeItem(STATE_KEY);
        window.history.replaceState({}, "", window.location.pathname);

        if (!verifier || state !== expectedState) {
          if (!cancelled) {
            setError("Sign-in could not be verified. Please try again.");
            setIsLoading(false);
          }
          return;
        }
        const res = await apiJson("/api/auth/token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            code,
            code_verifier: verifier,
            redirect_uri: cfg.redirect_uri,
          }),
        });
        if (!res) {
          if (!cancelled) {
            setError("Could not complete sign-in with Globus.");
            setIsLoading(false);
          }
          return;
        }
        applyTokens({
          accessToken: res.access_token,
          idToken: res.id_token,
          expiresAt: Date.now() + (res.expires_in || 3600) * 1000,
        });
      } else {
        const stored = readStoredAuth();
        if (!stored) {
          if (!cancelled) setIsLoading(false);
          return;
        }
        applyTokens(stored);
      }

      const me = await apiJson("/api/auth/me");
      if (cancelled) return;
      if (me?.authenticated) setUser(me);
      else clearSession();
      setIsLoading(false);
    })();

    return () => { cancelled = true; };
  }, [applyTokens, clearSession, handleUnauthorized]);

  const login = useCallback(async () => {
    if (!config?.enabled) return;
    setError(null);
    const verifier = randomString(64);
    const state = randomString(32);
    sessionStorage.setItem(VERIFIER_KEY, verifier);
    sessionStorage.setItem(STATE_KEY, state);
    const params = new URLSearchParams({
      client_id: config.client_id,
      redirect_uri: config.redirect_uri,
      scope: config.scopes.join(" "),
      state,
      response_type: "code",
      code_challenge: await codeChallenge(verifier),
      code_challenge_method: "S256",
      access_type: "online",
    });
    window.location.href = `${config.auth_uri}?${params.toString()}`;
  }, [config]);

  // Development sign-in: no credential, just an identity for the backend.
  const devLogin = useCallback(async (name) => {
    const id = name.trim().toLowerCase().replaceAll(/\s+/g, "_");
    if (!id) return null;
    sessionStorage.setItem(DEV_USER_KEY, id);
    configureApi({ devUserId: id });
    const me = await apiJson("/api/auth/me");
    setUser(me);
    return me;
  }, []);

  const logout = useCallback(async () => {
    await apiJson("/api/auth/logout", { method: "POST" });
    sessionStorage.removeItem(DEV_USER_KEY);
    configureApi({ devUserId: null });
    clearSession();
    setError(null);
  }, [clearSession]);

  const value = {
    isLoading,
    isEnabled: config?.enabled === true,
    isAuthenticated: user !== null,
    user,
    error,
    login,
    devLogin,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

AuthProvider.propTypes = { children: PropTypes.node };

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside an AuthProvider");
  return ctx;
}
