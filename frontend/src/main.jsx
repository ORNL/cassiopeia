// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

import { StrictMode, useState, useEffect } from "react";
import { createRoot } from "react-dom/client";
import PropTypes from "prop-types";
import Dashboard from "./Dashboard.jsx";
import LandingPage from "./LandingPage.jsx";

// ── Priority config ──────────────────────────────────────────────────────────

const DEFAULT_PRIORITIES = {
  novelty: 0.7, relevance: 0.8, methodology: 0.5, reproducibility: 0.6,
};

const PRIORITY_LABELS = [
  { key: "novelty",         label: "Novelty",         desc: "Prefer unique, unexplored approaches" },
  { key: "relevance",       label: "Relevance",       desc: "Match to your species and stress focus" },
  { key: "methodology",     label: "Methodology",     desc: "Alignment with your phenotyping methods" },
  { key: "reproducibility", label: "Reproducibility", desc: "Evidence quality and source credibility" },
];

function loadPriorities(id) {
  try {
    const raw = localStorage.getItem(`cassiopeia:priorities:${id}`);
    return raw ? { ...DEFAULT_PRIORITIES, ...JSON.parse(raw) } : null;
  } catch { return null; }
}

function storePriorities(id, prefs) {
  try { localStorage.setItem(`cassiopeia:priorities:${id}`, JSON.stringify(prefs)); } catch {}
}

// ── Slider (shared by PriorityModal and PrioritySetupStep) ───────────────────

function priorityColor(v) {
  if (v > 0.7) return "#4ade80";
  if (v > 0.4) return "#fbbf24";
  return "#94a3b8";
}

function Slider({ label, value, onChange, description }) {
  const c = priorityColor(value);
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: "#e2e8f0" }}>{label}</div>
        <span style={{ fontSize: 13, fontWeight: 700, color: c }}>{(value * 100).toFixed(0)}%</span>
      </div>
      {description && <p style={{ fontSize: 11, color: "#64748b", margin: "2px 0 8px" }}>{description}</p>}
      <input
        type="range" min="0" max="100" value={value * 100}
        onChange={(e) => onChange(Number(e.target.value) / 100)}
        style={{ width: "100%", height: 4, cursor: "pointer", appearance: "auto", accentColor: c }}
      />
    </div>
  );
}

Slider.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.number.isRequired,
  onChange: PropTypes.func.isRequired,
  description: PropTypes.string,
};

// ── PriorityModal ─────────────────────────────────────────────────────────────

function PriorityModal({ priorities, onSave, onClose }) {
  const [local, setLocal] = useState({ ...priorities });
  const set = (k) => (v) => setLocal((p) => ({ ...p, [k]: v }));

  return (
    <div style={MS.overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div style={MS.box}>
        <div style={MS.head}>
          <span style={MS.title}>Priority Weights</span>
          <button style={MS.closeBtn} onClick={onClose}>✕</button>
        </div>
        <p style={MS.sub}>Adjust how papers are ranked relative to your interests.</p>
        {PRIORITY_LABELS.map(({ key, label, desc }) => (
          <Slider key={key} label={label} value={local[key]} onChange={set(key)} description={desc} />
        ))}
        <div style={MS.actions}>
          <button style={MS.cancelBtn} onClick={onClose}>Cancel</button>
          <button style={MS.saveBtn} onClick={() => onSave(local)}>Save</button>
        </div>
      </div>
    </div>
  );
}

PriorityModal.propTypes = {
  priorities: PropTypes.object.isRequired,
  onSave: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};

// ── PrioritySetupStep ─────────────────────────────────────────────────────────

function PrioritySetupStep({ name, onSave }) {
  const [prefs, setPrefs] = useState({ ...DEFAULT_PRIORITIES });
  const set = (k) => (v) => setPrefs((p) => ({ ...p, [k]: v }));

  return (
    <div style={PS.root}>
      <div style={PS.card}>
        <div style={PS.badge}>CASSIOPEIA</div>
        <h2 style={PS.title}>Welcome, {name}</h2>
        <p style={PS.sub}>
          Set your priority weights before your first search. These control how
          papers are ranked against your profile. You can adjust them any time
          via the ⚙ settings button in the top bar.
        </p>
        {PRIORITY_LABELS.map(({ key, label, desc }) => (
          <Slider key={key} label={label} value={prefs[key]} onChange={set(key)} description={desc} />
        ))}
        <button style={PS.btn} onClick={() => onSave(prefs)}>Save &amp; Continue →</button>
      </div>
    </div>
  );
}

PrioritySetupStep.propTypes = {
  name: PropTypes.string.isRequired,
  onSave: PropTypes.func.isRequired,
};

// ── ChatView ──────────────────────────────────────────────────────────────────

function ChatView({ onBack, researcherName, onOpenSettings }) {
  const [chainlitUrl, setChainlitUrl] = useState(null);

  useEffect(() => {
    fetch("/api/config")
      .then((r) => r.json())
      .then((cfg) => {
        const base = cfg.chainlit_url || "http://localhost:8001";
        const url = researcherName
          ? `${base}?${new URLSearchParams({ name: researcherName }).toString()}`
          : base;
        setChainlitUrl(url);
      })
      .catch(() => setChainlitUrl("http://localhost:8001"));
  }, [researcherName]);

  return (
    <div style={S.root}>
      <div style={S.bar}>
        <button style={S.back} onClick={onBack}>← Back</button>
        <span style={S.barTitle}>CASSIOPEIA</span>
        <button style={S.cogBtn} onClick={onOpenSettings} title="Priority settings">⚙</button>
        {chainlitUrl && (
          <a style={S.open} href={chainlitUrl} target="_blank" rel="noreferrer">
            Open in new tab ↗
          </a>
        )}
      </div>
      {chainlitUrl ? (
        <iframe
          src={chainlitUrl}
          style={S.frame}
          title="CASSIOPEIA Chat"
          allow="microphone"
        />
      ) : (
        <div style={S.loading}>Connecting to chat server…</div>
      )}
    </div>
  );
}

ChatView.propTypes = {
  onBack: PropTypes.func.isRequired,
  researcherName: PropTypes.string,
  onOpenSettings: PropTypes.func.isRequired,
};

// ── App ───────────────────────────────────────────────────────────────────────

function App() {
  const [mode, setMode] = useState(null); // null | "setup" | "dashboard" | "chat"
  const [researcher, setResearcher] = useState(null);
  const [priorities, setPriorities] = useState(DEFAULT_PRIORITIES);
  const [showSettings, setShowSettings] = useState(false);

  const handleLogin = (name) => {
    if (!name) { setResearcher(null); setMode(null); return; }
    const id = name.toLowerCase().replaceAll(/\s+/g, "_");
    const saved = loadPriorities(id);
    setResearcher({ name, id });
    if (saved) {
      setPriorities(saved);
      // mode stays null → LandingPage shows the interface-choice cards
    } else {
      setMode("setup");
    }
  };

  const handleSavePriorities = (prefs) => {
    setPriorities(prefs);
    if (researcher) storePriorities(researcher.id, prefs);
    setShowSettings(false);
    if (mode === "setup") setMode(null); // proceed to interface choice
  };

  const openSettings = () => setShowSettings(true);
  const closeSettings = () => setShowSettings(false);

  if (mode === "setup" && researcher)
    return <PrioritySetupStep name={researcher.name} onSave={handleSavePriorities} />;

  if (mode === "dashboard" && researcher)
    return (
      <>
        <Dashboard
          onBack={() => setMode(null)}
          researcherName={researcher.name}
          researcherId={researcher.id}
          priorities={priorities}
          onOpenSettings={openSettings}
        />
        {showSettings && (
          <PriorityModal priorities={priorities} onSave={handleSavePriorities} onClose={closeSettings} />
        )}
      </>
    );

  if (mode === "chat" && researcher)
    return (
      <>
        <ChatView
          onBack={() => setMode(null)}
          researcherName={researcher.name}
          onOpenSettings={openSettings}
        />
        {showSettings && (
          <PriorityModal priorities={priorities} onSave={handleSavePriorities} onClose={closeSettings} />
        )}
      </>
    );

  return <LandingPage researcher={researcher} onLogin={handleLogin} onSelect={setMode} />;
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>
);

// ── ChatView styles ───────────────────────────────────────────────────────────

const S = {
  root: {
    height: "100vh",
    display: "flex",
    flexDirection: "column",
    background: "#0c0f1a",
    fontFamily: "'IBM Plex Sans', system-ui, sans-serif",
  },
  bar: {
    display: "flex",
    alignItems: "center",
    gap: 16,
    padding: "10px 20px",
    background: "#0f1626",
    borderBottom: "1px solid #1e293b",
    flexShrink: 0,
  },
  back: {
    background: "none",
    border: "1px solid #334155",
    color: "#94a3b8",
    borderRadius: 8,
    padding: "6px 14px",
    fontSize: 13,
    cursor: "pointer",
  },
  barTitle: { color: "#e2e8f0", fontSize: 14, fontWeight: 600, flex: 1 },
  cogBtn: {
    background: "none",
    border: "1px solid #334155",
    borderRadius: 8,
    color: "#94a3b8",
    fontSize: 16,
    padding: "4px 10px",
    cursor: "pointer",
    lineHeight: 1,
  },
  open: { color: "#22d3ee", fontSize: 12, textDecoration: "none" },
  frame: { flex: 1, border: "none", width: "100%" },
  loading: {
    flex: 1,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "#64748b",
    fontSize: 14,
  },
};

// ── Modal styles ──────────────────────────────────────────────────────────────

const MS = {
  overlay: {
    position: "fixed",
    inset: 0,
    background: "rgba(0,0,0,0.7)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
  },
  box: {
    background: "#111827",
    border: "1px solid #334155",
    borderRadius: 14,
    padding: "28px 32px",
    width: "100%",
    maxWidth: 440,
    maxHeight: "90vh",
    overflowY: "auto",
    boxSizing: "border-box",
  },
  head: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 },
  title: { fontSize: 16, fontWeight: 700, color: "#f1f5f9" },
  closeBtn: { background: "none", border: "none", color: "#64748b", fontSize: 16, cursor: "pointer", padding: "2px 6px" },
  sub: { fontSize: 13, color: "#64748b", margin: "0 0 20px", lineHeight: 1.6 },
  actions: { display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 8 },
  cancelBtn: {
    background: "none", border: "1px solid #334155", borderRadius: 8,
    color: "#94a3b8", fontSize: 13, padding: "8px 18px", cursor: "pointer",
  },
  saveBtn: {
    background: "#166534", border: "1px solid #4ade80", borderRadius: 8,
    color: "#4ade80", fontSize: 13, fontWeight: 600, padding: "8px 20px", cursor: "pointer",
  },
};

// ── Setup step styles ─────────────────────────────────────────────────────────

const PS = {
  root: {
    minHeight: "100vh",
    background: "#0c0f1a",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: "40px 24px",
    fontFamily: "'IBM Plex Sans', system-ui, sans-serif",
    color: "#e2e8f0",
  },
  card: {
    width: "100%",
    maxWidth: 480,
    background: "#111827",
    border: "1px solid #1e293b",
    borderRadius: 16,
    padding: "36px 40px",
  },
  badge: {
    display: "inline-block",
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: "0.14em",
    textTransform: "uppercase",
    color: "#4ade80",
    background: "#0f2d1f",
    border: "1px solid #166534",
    borderRadius: 8,
    padding: "4px 12px",
    marginBottom: 20,
  },
  title: { fontSize: 22, fontWeight: 700, margin: "0 0 12px", letterSpacing: "-0.01em" },
  sub: { fontSize: 13, color: "#64748b", lineHeight: 1.65, margin: "0 0 28px" },
  btn: {
    width: "100%",
    padding: "12px 24px",
    background: "linear-gradient(135deg, #4ade80, #22d3ee)",
    color: "#0c0f1a",
    border: "none",
    borderRadius: 10,
    fontSize: 14,
    fontWeight: 700,
    cursor: "pointer",
    marginTop: 8,
  },
};
