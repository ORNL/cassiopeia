// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react";
import PropTypes from "prop-types";
import logo from "./assets/logo.png";

export default function LandingPage({ onLogin, pending }) {
  const [input, setInput] = useState("");

  const handleLogin = () => {
    const name = input.trim();
    if (name && !pending) onLogin(name);
  };

  const disabled = !input.trim() || pending;

  return (
    <div style={S.root}>
      <div style={S.hero}>
        <img src={logo} alt="Cassiopeia" style={S.logo} />
        <h1 style={S.title}>Find the papers that {"\n"}<br/> matter to your research</h1>
        <p style={S.sub}>
          An Opal service
        </p>
      </div>

      <div style={S.loginBox}>
        <p style={S.loginLabel}>Enter your name to get started</p>
        <div style={S.loginRow}>
          <input
            style={S.loginInput}
            type="text"
            placeholder="Your name…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleLogin()}
            autoFocus
            disabled={pending}
          />
          <button
            style={{ ...S.loginBtn, ...(disabled ? S.loginBtnDisabled : {}) }}
            onClick={handleLogin}
            disabled={disabled}
          >
            {pending ? "⏳" : "Enter →"}
          </button>
        </div>
      </div>

      <div style={S.footer}>
        Powered by Academy · Europe PMC · arXiv · LiteLLM
      </div>
    </div>
  );
}

const S = {
  root: {
    minHeight: "100vh",
    background: "#0c0f1a",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    padding: "40px 24px",
    fontFamily: "'IBM Plex Sans', system-ui, sans-serif",
    color: "#e2e8f0",
  },
  hero: {
    textAlign: "center",
    maxWidth: 600,
    marginBottom: 40,
  },
  logo: {
    height: 300,
    width: "auto",
    marginBottom: 24,
  },
  title: {
    fontSize: 32,
    fontWeight: 800,
    margin: "0 0 16px",
    lineHeight: 1.25,
    letterSpacing: "-0.02em",
    background: "linear-gradient(135deg, #f1f5f9, #94a3b8)",
    WebkitBackgroundClip: "text",
    WebkitTextFillColor: "transparent",
  },
  sub: {
    fontSize: 15,
    color: "#64748b",
    lineHeight: 1.7,
    margin: 0,
  },
  loginBox: {
    textAlign: "center",
    marginBottom: 40,
  },
  loginLabel: {
    fontSize: 14,
    color: "#94a3b8",
    marginBottom: 16,
  },
  loginRow: {
    display: "flex",
    gap: 10,
    justifyContent: "center",
  },
  loginInput: {
    background: "#111827",
    border: "1px solid #334155",
    borderRadius: 8,
    color: "#e2e8f0",
    fontSize: 15,
    padding: "10px 16px",
    outline: "none",
    width: 260,
  },
  loginBtn: {
    background: "#166534",
    border: "1px solid #4ade80",
    borderRadius: 8,
    color: "#4ade80",
    fontSize: 14,
    fontWeight: 600,
    padding: "10px 20px",
    cursor: "pointer",
  },
  loginBtnDisabled: {
    opacity: 0.4,
    cursor: "default",
  },
  userBar: {
    display: "flex",
    alignItems: "center",
    gap: 16,
    marginBottom: 32,
    fontSize: 14,
    color: "#94a3b8",
  },
  userGreet: {
    color: "#e2e8f0",
  },
  logoutBtn: {
    background: "none",
    border: "1px solid #334155",
    borderRadius: 6,
    color: "#64748b",
    fontSize: 11,
    padding: "3px 10px",
    cursor: "pointer",
  },
  cards: {
    display: "flex",
    gap: 24,
    flexWrap: "wrap",
    justifyContent: "center",
    width: "100%",
    maxWidth: 820,
  },
  card: {
    flex: "1 1 340px",
    maxWidth: 380,
    background: "#111827",
    border: "1px solid #1e293b",
    borderRadius: 14,
    padding: "32px 28px",
    textAlign: "left",
    cursor: "pointer",
    transition: "border-color 0.2s, transform 0.15s",
    display: "flex",
    flexDirection: "column",
    gap: 12,
    color: "#e2e8f0",
    outline: "none",
  },
  cardIcon: {
    fontSize: 28,
  },
  cardTitle: {
    fontSize: 18,
    fontWeight: 700,
    letterSpacing: "-0.01em",
  },
  cardDesc: {
    fontSize: 13,
    color: "#64748b",
    lineHeight: 1.65,
    flex: 1,
  },
  pill: {
    display: "inline-block",
    fontSize: 11,
    fontWeight: 600,
    padding: "3px 10px",
    borderRadius: 12,
    marginTop: 4,
    width: "fit-content",
  },
  pillGreen: {
    background: "#0f2d1f",
    border: "1px solid #166534",
    color: "#4ade80",
  },
  pillCyan: {
    background: "#0c1f2d",
    border: "1px solid #0e4a6e",
    color: "#22d3ee",
  },
  footer: {
    marginTop: 56,
    fontSize: 11,
    color: "#334155",
    letterSpacing: "0.05em",
  },
};

LandingPage.propTypes = {
  onLogin: PropTypes.func.isRequired,
  pending: PropTypes.bool,
};
