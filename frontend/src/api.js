// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

// Single entry point for backend calls.
//
// Every request carries the caller's identity: a Globus Bearer token when
// authentication is enabled, or an X-User-ID development header when it is
// not. No route takes a researcher_id any more — the backend derives it from
// whichever of those it receives.

let _auth = {
  token: null,      // Globus access token
  idToken: null,    // Globus id_token (JWT) — accepted by AmSC / MAG
  devUserId: null,  // development identity, used only when auth is disabled
  onUnauthorized: () => {},
};

/** Called by AuthProvider whenever the credentials change. */
export function configureApi(next) {
  _auth = { ..._auth, ...next };
}

export async function apiFetch(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (_auth.token) {
    headers.Authorization = `Bearer ${_auth.token}`;
    if (_auth.idToken) headers["X-Id-Token"] = _auth.idToken;
  } else if (_auth.devUserId) {
    headers["X-User-ID"] = _auth.devUserId;
  }
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(path, { ...options, headers });
  if (res.status === 401) _auth.onUnauthorized();
  return res;
}

/** apiFetch + JSON parse. Returns `fallback` instead of throwing on failure. */
export async function apiJson(path, options = {}, fallback = null) {
  try {
    const res = await apiFetch(path, options);
    if (!res.ok) return fallback;
    return await res.json();
  } catch {
    return fallback;
  }
}

export function postJson(path, body) {
  return apiFetch(path, { method: "POST", body: JSON.stringify(body) });
}
