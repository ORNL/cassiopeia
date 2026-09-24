// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

// Shared look, form controls and backend calls of the setup wizard.

import PropTypes from "prop-types";

export const C = {
  bg: "#0c0f1a",
  bar: "#0f1626",
  panel: "#111827",
  inset: "#0b1220",
  border: "#1e293b",
  border2: "#334155",
  text: "#e2e8f0",
  muted: "#94a3b8",
  dim: "#64748b",
  accent: "#4ade80",
  accent2: "#22d3ee",
  danger: "#f87171",
  warn: "#fbbf24",
  mono: "'IBM Plex Mono', ui-monospace, monospace",
  sans: "'IBM Plex Sans', system-ui, sans-serif",
};

// ── Backend ───────────────────────────────────────────────────────────────────

/** Call the setup server; resolves to parsed JSON, rejects with the server's message. */
export async function setupApi(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api/setup${path}`, {
    method,
    headers: {
      "X-Cassiopeia-Setup": "1",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((d) => d.msg).join("; ")
      : data.detail;
    throw new Error(detail || `Request failed (HTTP ${res.status})`);
  }
  return data;
}

// ── Immutable updates ────────────────────────────────────────────────────────

/** Copy of `obj` with `value` at `path` (array of keys / indices). */
export function setIn(obj, path, value) {
  if (path.length === 0) return value;
  const [head, ...rest] = path;
  const base = Array.isArray(obj) ? [...obj] : { ...(obj || {}) };
  base[head] = setIn(base[head], rest, value);
  return base;
}

export function getIn(obj, path) {
  return path.reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

export const toList = (text) =>
  text.split(",").map((s) => s.trim()).filter(Boolean);

export const fromList = (list) => (list || []).join(", ");

export const slugify = (text) =>
  text.toLowerCase().normalize("NFKD").replace(/[^\w\s]/g, "")
    .trim().replace(/\s+/g, "_").replace(/^[^a-z]+/, "").slice(0, 40);

// ── Controls ─────────────────────────────────────────────────────────────────

export const S = {
  label: { display: "block", fontSize: 14, fontWeight: 600, color: C.muted, marginBottom: 4 },
  help: { fontSize: 13, color: C.dim, margin: "3px 0 0", lineHeight: 1.4 },
  input: {
    width: "100%", boxSizing: "border-box", background: C.inset, color: C.text,
    border: `1px solid ${C.border2}`, borderRadius: 6, padding: "7px 9px",
    fontSize: 15, fontFamily: C.sans,
  },
  btn: {
    background: "none", border: `1px solid ${C.border2}`, color: C.muted,
    borderRadius: 8, padding: "7px 14px", fontSize: 15, cursor: "pointer",
    fontFamily: C.sans,
  },
  primary: {
    background: C.accent, border: `1px solid ${C.accent}`, color: "#052e16",
    borderRadius: 8, padding: "7px 16px", fontSize: 15, fontWeight: 600,
    cursor: "pointer", fontFamily: C.sans,
  },
  small: {
    background: "none", border: `1px solid ${C.border2}`, color: C.muted,
    borderRadius: 6, padding: "2px 8px", fontSize: 14, cursor: "pointer",
  },
  card: {
    background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10,
    padding: 18,
  },
  h2: { fontSize: 21, fontWeight: 600, color: C.text, margin: "0 0 4px" },
  h3: { fontSize: 16, fontWeight: 600, color: C.text, margin: "0 0 10px" },
  sub: { fontSize: 15, color: C.muted, margin: "0 0 16px", lineHeight: 1.5 },
  badge: (color) => ({
    display: "inline-block", fontSize: 13, fontWeight: 600, color,
    border: `1px solid ${color}55`, background: `${color}14`,
    borderRadius: 999, padding: "1px 8px",
  }),
  mono: { fontFamily: C.mono, fontSize: 14 },
};

const disabledStyle = { opacity: 0.55, cursor: "not-allowed" };

export function Field({ label, help, children, style }) {
  return (
    <div style={{ marginBottom: 14, ...style }}>
      {label && <span style={S.label}>{label}</span>}
      {children}
      {help && <p style={S.help}>{help}</p>}
    </div>
  );
}

Field.propTypes = {
  label: PropTypes.node,
  help: PropTypes.node,
  children: PropTypes.node,
  style: PropTypes.object,
};

export function TextInput({ value, onChange, disabled, placeholder, mono, title, ariaLabel }) {
  return (
    <input
      type="text" value={value ?? ""} placeholder={placeholder} disabled={disabled}
      title={title} aria-label={ariaLabel}
      onChange={(e) => onChange(e.target.value)}
      style={{ ...S.input, ...(mono ? { fontFamily: C.mono, fontSize: 14 } : {}), ...(disabled ? disabledStyle : {}) }}
    />
  );
}

TextInput.propTypes = {
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
  placeholder: PropTypes.string,
  mono: PropTypes.bool,
  title: PropTypes.string,
  ariaLabel: PropTypes.string,
};

export function TextArea({ value, onChange, rows = 3, placeholder, mono }) {
  return (
    <textarea
      value={value ?? ""} rows={rows} placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      style={{ ...S.input, resize: "vertical", lineHeight: 1.45, ...(mono ? { fontFamily: C.mono, fontSize: 14 } : {}) }}
    />
  );
}

TextArea.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  rows: PropTypes.number,
  placeholder: PropTypes.string,
  mono: PropTypes.bool,
};

export function NumberInput({ value, onChange, min = 0, max = 20 }) {
  return (
    <input
      type="number" min={min} max={max} value={value ?? ""}
      onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
      style={{ ...S.input, width: 90 }}
    />
  );
}

NumberInput.propTypes = {
  value: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  onChange: PropTypes.func.isRequired,
  min: PropTypes.number,
  max: PropTypes.number,
};

export function Select({ value, onChange, options, disabled, title }) {
  return (
    <select
      value={value ?? ""} disabled={disabled} title={title}
      onChange={(e) => onChange(e.target.value)}
      style={{ ...S.input, ...(disabled ? disabledStyle : {}) }}
    >
      {options.map((o) => {
        const [v, l] = Array.isArray(o) ? o : [o, o];
        return <option key={v} value={v}>{l}</option>;
      })}
    </select>
  );
}

Select.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  options: PropTypes.array.isRequired,
  disabled: PropTypes.bool,
  title: PropTypes.string,
};

export function Check({ label, checked, onChange, help }) {
  return (
    <label style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 15, color: C.text, marginBottom: 8, cursor: "pointer" }}>
      <input type="checkbox" checked={!!checked} onChange={(e) => onChange(e.target.checked)} style={{ marginTop: 3, accentColor: C.accent }} />
      <span>
        {label}
        {help && <span style={{ display: "block", fontSize: 13, color: C.dim }}>{help}</span>}
      </span>
    </label>
  );
}

Check.propTypes = {
  label: PropTypes.node.isRequired,
  checked: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
  help: PropTypes.node,
};

export function Notice({ tone = "info", children }) {
  const color = { info: C.accent2, warn: C.warn, error: C.danger, ok: C.accent }[tone];
  return (
    <div style={{
      border: `1px solid ${color}55`, background: `${color}10`, color: C.text,
      borderRadius: 8, padding: "10px 12px", fontSize: 15, lineHeight: 1.5, marginBottom: 14,
    }}>
      {children}
    </div>
  );
}

Notice.propTypes = {
  tone: PropTypes.oneOf(["info", "warn", "error", "ok"]),
  children: PropTypes.node,
};
