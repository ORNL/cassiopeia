// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

// APPL dashboard components: instrument feasibility of AI proposals.
// Bundled into the dashboard as `@domain-ui` (see frontend/vite.config.js).

import PropTypes from "prop-types";

const FEASIBILITY_STYLE = {
  true:      { color: "#4ade80", bg: "#0a1f12", border: "#1a4a2a", icon: "✓" },
  partial:   { color: "#fbbf24", bg: "#1a1204", border: "#4a3a0a", icon: "~" },
  false:     { color: "#f87171", bg: "#1a0808", border: "#4a1a1a", icon: "✗" },
};

const FEASIBILITY_LABEL = { true: "Feasible", partial: "Partially feasible", false: "Not feasible" };

function FeasibilityBadge({ result: f }) {
  if (f?.feasible == null) return null;
  let key;
  if (f.feasible === true) key = "true";
  else if (f.feasible === false) key = "false";
  else key = "partial";
  const { color, bg, border, icon } = FEASIBILITY_STYLE[key];
  return (
    <span title={f.note} style={{ fontSize: 10, fontWeight: 700, color, background: bg, border: `1px solid ${border}`, borderRadius: 10, padding: "2px 8px", whiteSpace: "nowrap", cursor: "help" }}>
      {icon} {FEASIBILITY_LABEL[key]}
      {f.confidence ? ` · ${(f.confidence * 100).toFixed(0)}%` : ""}
    </span>
  );
}

function FeasibilityDetail({ result: f }) {
  if (!f?.note) return null;
  return (
    <div style={{ marginTop: 8, background: "#0c0f1a", borderRadius: 6, padding: "8px 12px" }}>
      <div style={{ fontSize: 12, color: "#64748b", lineHeight: 1.5 }}>{f.note}</div>
      {f.missing_equipment?.length > 0 && (
        <div style={{ marginTop: 4, display: "flex", gap: 6, flexWrap: "wrap" }}>
          <span style={{ fontSize: 10, color: "#f87171", fontWeight: 600 }}>Missing:</span>
          {f.missing_equipment.map((e) => (
            <span key={e} style={{ fontSize: 10, color: "#f87171", background: "#1a0808", border: "1px solid #4a1a1a", borderRadius: 6, padding: "1px 6px" }}>{e}</span>
          ))}
        </div>
      )}
      {f.adaptation && (
        <div style={{ marginTop: 4, fontSize: 12, color: "#fbbf24", fontStyle: "italic" }}>Adaptation: {f.adaptation}</div>
      )}
    </div>
  );
}

FeasibilityBadge.propTypes = { result: PropTypes.object };
FeasibilityDetail.propTypes = { result: PropTypes.object };

export default {
  proposalBadges: { feasibility: FeasibilityBadge },
  proposalPanels: { feasibility: FeasibilityDetail },
};
