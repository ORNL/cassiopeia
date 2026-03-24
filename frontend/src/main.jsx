// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

import { StrictMode, useState, useEffect } from "react";
import { createRoot } from "react-dom/client";
import PropTypes from "prop-types";
import Dashboard from "./Dashboard.jsx";
import LandingPage from "./LandingPage.jsx";

function ChatView({ onBack, researcherName }) {
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

ChatView.propTypes = { onBack: PropTypes.func.isRequired, researcherName: PropTypes.string };

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
  barTitle: {
    color: "#e2e8f0",
    fontSize: 14,
    fontWeight: 600,
    flex: 1,
  },
  open: {
    color: "#22d3ee",
    fontSize: 12,
    textDecoration: "none",
  },
  frame: {
    flex: 1,
    border: "none",
    width: "100%",
  },
  loading: {
    flex: 1,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "#64748b",
    fontSize: 14,
  },
};

function App() {
  const [mode, setMode] = useState(null); // null | "dashboard" | "chat"
  const [researcher, setResearcher] = useState(null); // { name, id } | null

  const handleLogin = (name) => {
    if (!name) { setResearcher(null); return; }
    setResearcher({ name, id: name.toLowerCase().replaceAll(/\s+/g, "_") });
  };

  if (mode === "dashboard" && researcher)
    return <Dashboard onBack={() => setMode(null)} researcherName={researcher.name} researcherId={researcher.id} />;
  if (mode === "chat" && researcher)
    return <ChatView onBack={() => setMode(null)} researcherName={researcher.name} />;
  return <LandingPage researcher={researcher} onLogin={handleLogin} onSelect={setMode} />;
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>
);
