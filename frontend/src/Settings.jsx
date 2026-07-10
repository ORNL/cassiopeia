// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect, useCallback } from "react";
import PropTypes from "prop-types";

// ── LLM Settings modal — per-researcher provider selection & key storage ──────

const PROVIDER_DESCRIPTIONS = {
  anthropic: "Claude models via Anthropic's public API.",
  amsc:      "Anthropic models via the American Science Cloud Model Access Gateway.",
  azure:     "GPT-4 via ORNL Azure OpenAI deployment.",
};

export default function Settings({ researcherId, onClose }) {
  const [providers, setProviders] = useState([]);
  const [loading, setLoading]     = useState(true);
  const [keyInputs, setKeyInputs] = useState({});    // provider → string
  const [showKey, setShowKey]     = useState({});    // provider → bool
  const [saving, setSaving]       = useState({});    // provider → bool
  const [removing, setRemoving]   = useState({});    // provider → bool
  const [activating, setActivating] = useState({}); // provider → bool
  const [flash, setFlash]         = useState({});    // provider → {ok, msg}

  const fetchProviders = useCallback(async () => {
    try {
      const r = await fetch(`/api/settings/providers?researcher_id=${encodeURIComponent(researcherId)}`);
      if (r.ok) setProviders(await r.json());
    } finally {
      setLoading(false);
    }
  }, [researcherId]);

  useEffect(() => { fetchProviders(); }, [fetchProviders]);

  // Close on Escape
  useEffect(() => {
    const h = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, [onClose]);

  function showFlash(provider, ok, msg) {
    setFlash((f) => ({ ...f, [provider]: { ok, msg } }));
    setTimeout(() => setFlash((f) => { const n = { ...f }; delete n[provider]; return n; }), 3000);
  }

  async function handleSave(provider) {
    const key = (keyInputs[provider] || "").trim();
    if (!key) { showFlash(provider, false, "Enter an API key first."); return; }
    setSaving((s) => ({ ...s, [provider]: true }));
    try {
      const r = await fetch(
        `/api/settings/api-key?researcher_id=${encodeURIComponent(researcherId)}`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider, api_key: key }) },
      );
      if (r.ok) {
        setKeyInputs((k) => { const n = { ...k }; delete n[provider]; return n; });
        showFlash(provider, true, "Key saved.");
        await fetchProviders();
      } else {
        const d = await r.json().catch(() => ({}));
        showFlash(provider, false, d.detail || "Save failed.");
      }
    } finally {
      setSaving((s) => ({ ...s, [provider]: false }));
    }
  }

  async function handleRemove(provider) {
    setRemoving((s) => ({ ...s, [provider]: true }));
    try {
      const r = await fetch(
        `/api/settings/api-key/${encodeURIComponent(provider)}?researcher_id=${encodeURIComponent(researcherId)}`,
        { method: "DELETE" },
      );
      if (r.ok) { showFlash(provider, true, "Key removed."); await fetchProviders(); }
      else { showFlash(provider, false, "Remove failed."); }
    } finally {
      setRemoving((s) => ({ ...s, [provider]: false }));
    }
  }

  async function handleSetActive(provider) {
    setActivating((s) => ({ ...s, [provider]: true }));
    try {
      const r = await fetch(
        `/api/settings/active-provider?researcher_id=${encodeURIComponent(researcherId)}`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider }) },
      );
      if (r.ok) { showFlash(provider, true, "Active provider updated."); await fetchProviders(); }
      else { const d = await r.json().catch(() => ({})); showFlash(provider, false, d.detail || "Failed."); }
    } finally {
      setActivating((s) => ({ ...s, [provider]: false }));
    }
  }

  return (
    <div style={S.overlay}>
      <dialog open aria-label="LLM Provider Settings" style={S.box}>
        {/* Header */}
        <div style={S.head}>
          <div>
            <span style={S.title}>LLM Provider</span>
            <p style={S.sub}>Choose which AI provider Cassiopeia uses for scoring and synthesis.</p>
          </div>
          <button style={S.closeBtn} onClick={onClose} aria-label="Close">✕</button>
        </div>

        {loading ? (
          <p style={{ color: "#64748b", fontSize: 13 }}>Loading…</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {providers.map((p) => {
              const isActive    = p.is_active;
              const configured  = p.configured;
              const keyVal      = keyInputs[p.provider] || "";
              const visible     = showKey[p.provider] || false;
              const f           = flash[p.provider];

              return (
                <div key={p.provider} style={{ ...S.card, ...(isActive ? S.cardActive : {}) }}>
                  {/* Card header */}
                  <div style={S.cardHead}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <span style={S.provName}>{p.display_name}</span>
                      {isActive && <span style={S.badgeActive}>ACTIVE</span>}
                      {configured && !isActive && <span style={S.badgeConfigured}>CONFIGURED</span>}
                    </div>
                  </div>

                  {/* Description */}
                  <p style={S.provDesc}>{PROVIDER_DESCRIPTIONS[p.provider] || ""}</p>

                  {/* Key input */}
                  <div style={S.inputRow}>
                    <div style={S.inputWrap}>
                      <input
                        type={visible ? "text" : "password"}
                        placeholder={configured ? "••••••••  (stored — enter new key to replace)" : "Paste API key…"}
                        value={keyVal}
                        onChange={(e) => setKeyInputs((k) => ({ ...k, [p.provider]: e.target.value }))}
                        style={S.input}
                        autoComplete="off"
                        spellCheck={false}
                      />
                      <button
                        style={S.showBtn}
                        onClick={() => setShowKey((s) => ({ ...s, [p.provider]: !visible }))}
                        aria-label={visible ? "Hide key" : "Show key"}
                        tabIndex={-1}
                      >
                        {visible ? "Hide" : "Show"}
                      </button>
                    </div>
                    <button
                      style={{ ...S.saveBtn, opacity: saving[p.provider] ? 0.6 : 1 }}
                      onClick={() => handleSave(p.provider)}
                      disabled={saving[p.provider]}
                    >
                      {saving[p.provider] ? "Saving…" : "Save"}
                    </button>
                  </div>

                  {/* Flash message */}
                  {f && (
                    <p style={{ ...S.flash, color: f.ok ? "#4ade80" : "#f87171" }}>{f.msg}</p>
                  )}

                  {/* Secondary actions */}
                  {configured && (
                    <div style={S.actions}>
                      {!isActive && (
                        <button
                          style={{ ...S.actionBtn, ...S.activateBtn, opacity: activating[p.provider] ? 0.6 : 1 }}
                          onClick={() => handleSetActive(p.provider)}
                          disabled={activating[p.provider]}
                        >
                          {activating[p.provider] ? "Activating…" : "Set as active"}
                        </button>
                      )}
                      <button
                        style={{ ...S.actionBtn, ...S.removeBtn, opacity: removing[p.provider] ? 0.6 : 1 }}
                        onClick={() => handleRemove(p.provider)}
                        disabled={removing[p.provider]}
                      >
                        {removing[p.provider] ? "Removing…" : "Remove key"}
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <div style={S.footer}>
          <button style={S.doneBtn} onClick={onClose}>Done</button>
        </div>
      </dialog>
    </div>
  );
}

Settings.propTypes = {
  researcherId: PropTypes.string.isRequired,
  onClose: PropTypes.func.isRequired,
};

// ── Styles ────────────────────────────────────────────────────────────────────

const S = {
  overlay: {
    position: "fixed", inset: 0,
    background: "rgba(0,0,0,0.75)",
    display: "flex", alignItems: "center", justifyContent: "center",
    zIndex: 1100,
  },
  box: {
    background: "#111827",
    border: "1px solid #334155",
    borderRadius: 16,
    padding: "28px 32px",
    width: "100%", maxWidth: 500,
    maxHeight: "90vh", overflowY: "auto",
    boxSizing: "border-box",
    fontFamily: "'IBM Plex Sans', system-ui, sans-serif",
    color: "#e2e8f0",
  },
  head: {
    display: "flex", justifyContent: "space-between", alignItems: "flex-start",
    marginBottom: 20,
  },
  title: { fontSize: 16, fontWeight: 700, color: "#f1f5f9" },
  sub: { fontSize: 12, color: "#64748b", margin: "4px 0 0", lineHeight: 1.5 },
  closeBtn: {
    background: "none", border: "none", color: "#64748b",
    fontSize: 16, cursor: "pointer", padding: "2px 6px", flexShrink: 0,
  },
  card: {
    background: "#0f1626",
    border: "1px solid #1e293b",
    borderRadius: 12,
    padding: "16px 18px",
  },
  cardActive: {
    border: "1px solid #166534",
    background: "#0a1a0f",
  },
  cardHead: { marginBottom: 6 },
  provName: { fontSize: 14, fontWeight: 600, color: "#e2e8f0" },
  provDesc: { fontSize: 12, color: "#64748b", margin: "0 0 12px", lineHeight: 1.5 },
  badgeActive: {
    fontSize: 10, fontWeight: 700, letterSpacing: "0.08em",
    color: "#4ade80", background: "#0f2d1f", border: "1px solid #166534",
    borderRadius: 6, padding: "2px 8px",
  },
  badgeConfigured: {
    fontSize: 10, fontWeight: 700, letterSpacing: "0.08em",
    color: "#94a3b8", background: "#1e293b", border: "1px solid #334155",
    borderRadius: 6, padding: "2px 8px",
  },
  inputRow: { display: "flex", gap: 8, alignItems: "stretch" },
  inputWrap: { flex: 1, position: "relative", display: "flex" },
  input: {
    flex: 1,
    background: "#0c0f1a", border: "1px solid #334155", borderRadius: 8,
    color: "#e2e8f0", fontSize: 12, padding: "8px 60px 8px 10px",
    outline: "none", fontFamily: "monospace",
  },
  showBtn: {
    position: "absolute", right: 6, top: "50%", transform: "translateY(-50%)",
    background: "none", border: "none", color: "#64748b",
    fontSize: 11, cursor: "pointer", padding: "2px 4px",
  },
  saveBtn: {
    background: "#1e3a5f", border: "1px solid #3b82f6",
    color: "#93c5fd", borderRadius: 8,
    fontSize: 12, fontWeight: 600, padding: "0 16px",
    cursor: "pointer", whiteSpace: "nowrap",
  },
  flash: { fontSize: 12, margin: "6px 0 0" },
  actions: { display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" },
  actionBtn: {
    fontSize: 12, borderRadius: 7, padding: "5px 12px", cursor: "pointer", border: "1px solid",
  },
  activateBtn: {
    background: "#0f2d1f", borderColor: "#166534", color: "#4ade80",
  },
  removeBtn: {
    background: "#1f0a0a", borderColor: "#7f1d1d", color: "#f87171",
  },
  footer: { display: "flex", justifyContent: "flex-end", marginTop: 20 },
  doneBtn: {
    background: "none", border: "1px solid #334155", borderRadius: 8,
    color: "#94a3b8", fontSize: 13, padding: "8px 20px", cursor: "pointer",
  },
};
