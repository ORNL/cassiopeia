// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

import { useState, useCallback, useMemo, useEffect } from "react";
import PropTypes from "prop-types";

// ─────────────────────────────────────────────────
// Data constants (APPL-specific)
// ─────────────────────────────────────────────────

const PLANT_SPECIES = [
  { value: "poplar", label: "Poplar", latin: "Populus spp." },
  { value: "pennycress", label: "Pennycress", latin: "Thlaspi arvense" },
  { value: "arabidopsis", label: "Arabidopsis", latin: "Arabidopsis thaliana" },
  { value: "soybean", label: "Soybean", latin: "Glycine max" },
  { value: "sorghum", label: "Sorghum", latin: "Sorghum bicolor" },
  { value: "switchgrass", label: "Switchgrass", latin: "Panicum virgatum" },
  { value: "miscanthus", label: "Miscanthus", latin: "Miscanthus × giganteus" },
  { value: "brachypodium", label: "Brachypodium", latin: "Brachypodium distachyon" },
];

const STRESS_TYPES = [
  { value: "drought", label: "Drought", icon: "💧" },
  { value: "nutrient", label: "Nutrient", icon: "🧪" },
  { value: "temperature", label: "Temperature", icon: "🌡️" },
  { value: "pathogen", label: "Pathogen", icon: "🦠" },
  { value: "heavy_metal", label: "Heavy Metal", icon: "⚗️" },
  { value: "salinity", label: "Salinity", icon: "🧂" },
  { value: "light", label: "Light", icon: "☀️" },
  { value: "flooding", label: "Flooding", icon: "🌊" },
];

const PHENOTYPING_METHODS = [
  { value: "hyperspectral_imaging", label: "Hyperspectral Imaging", icon: "🌈" },
  { value: "rgb_imaging", label: "RGB Imaging", icon: "📷" },
  { value: "thermal_imaging", label: "Thermal Imaging", icon: "🔥" },
  { value: "chlorophyll_fluorescence", label: "Chlorophyll Fluorescence", icon: "🔬" },
  { value: "root_imaging", label: "Root Imaging", icon: "🌱" },
];

const ARTICLE_SOURCES = [
  { value: "biorxiv", label: "bioRxiv", type: "open", desc: "Preprints — daily scan, full text" },
  { value: "plos_one", label: "PLoS ONE", type: "open", desc: "Open access — weekly scan" },
  { value: "frontiers", label: "Frontiers", type: "open", desc: "Open access — RSS feeds" },
  { value: "arxiv", label: "arXiv", type: "open", desc: "Preprints — computational methods" },
  { value: "pubmed", label: "PubMed", type: "paywall", desc: "Abstracts & citation data" },
  { value: "nature_communications", label: "Nature Comms", type: "paywall", desc: "High-impact — abstracts only" },
  { value: "new_phytologist", label: "New Phytologist", type: "paywall", desc: "Plant science — abstracts" },
  { value: "plant_physiology", label: "Plant Physiology", type: "paywall", desc: "Plant science — abstracts" },
];

const CREDIBILITY_ICONS = {
  high: "🟢", moderate: "🟡", preliminary: "🔴", conflicting: "⚠️",
};

// ─────────────────────────────────────────────────
// Components
// ─────────────────────────────────────────────────

function SpeciesSelect({ selected, onChange }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={S.label}>Plant Species</div>
      <div style={S.speciesGrid}>
        {PLANT_SPECIES.map((sp) => {
          const on = selected.includes(sp.value);
          return (
            <button key={sp.value}
              onClick={() => onChange(on ? selected.filter((s) => s !== sp.value) : [...selected, sp.value])}
              style={{ ...S.speciesCard, ...(on ? S.speciesOn : {}) }}
            >
              <span style={S.speciesName}>{sp.label}</span>
              <span style={{ ...S.speciesLatin, ...(on ? { color: "#86efac" } : {}) }}>{sp.latin}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

SpeciesSelect.propTypes = {
  selected: PropTypes.arrayOf(PropTypes.string).isRequired,
  onChange: PropTypes.func.isRequired,
};

function ChipSelect({ label, options, selected, onChange, renderOption }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={S.label}>{label}</div>
      <div style={S.chipWrap}>
        {options.map((opt) => {
          const val = typeof opt === "string" ? opt : opt.value;
          const display = renderOption ? renderOption(opt) : opt.label;
          const on = selected.includes(val);
          return (
            <button key={val}
              onClick={() => onChange(on ? selected.filter((s) => s !== val) : [...selected, val])}
              style={{ ...S.chip, ...(on ? S.chipOn : {}) }}
            >{display}</button>
          );
        })}
      </div>
    </div>
  );
}

ChipSelect.propTypes = {
  label: PropTypes.string.isRequired,
  options: PropTypes.array.isRequired,
  selected: PropTypes.arrayOf(PropTypes.string).isRequired,
  onChange: PropTypes.func.isRequired,
  renderOption: PropTypes.func,
};

function SourceGroup({ title, subtitle, items, accent, selected, onToggle, onItemToggle }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <span style={{ fontSize: 10, fontWeight: 700, color: accent, textTransform: "uppercase", letterSpacing: "0.06em" }}>{title}</span>
        <span style={{ fontSize: 10, color: "#4b5563" }}>{subtitle}</span>
        <button onClick={onToggle} style={S.toggleBtn}>
          {items.every((s) => selected.includes(s.value)) ? "deselect all" : "select all"}
        </button>
      </div>
      <div style={S.sourceGrid}>
        {items.map((src) => {
          const on = selected.includes(src.value);
          return (
            <button key={src.value}
              onClick={() => onItemToggle(src)}
              style={{ ...S.sourceCard, ...(on ? { ...S.sourceOn, borderColor: accent } : {}) }}
            >
              <span style={{ ...S.sourceName, ...(on ? { color: "#f1f5f9" } : {}) }}>{src.label}</span>
              <span style={S.sourceDesc}>{src.desc}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

SourceGroup.propTypes = {
  title: PropTypes.string.isRequired,
  subtitle: PropTypes.string.isRequired,
  items: PropTypes.array.isRequired,
  accent: PropTypes.string.isRequired,
  selected: PropTypes.arrayOf(PropTypes.string).isRequired,
  onToggle: PropTypes.func.isRequired,
  onItemToggle: PropTypes.func.isRequired,
};

function SourceSelector({ selected, onChange }) {
  const open = ARTICLE_SOURCES.filter((s) => s.type === "open");
  const paywall = ARTICLE_SOURCES.filter((s) => s.type === "paywall");

  const toggleAll = (group) => {
    const vals = group.map((s) => s.value);
    const allOn = vals.every((v) => selected.includes(v));
    onChange(allOn ? selected.filter((s) => !vals.includes(s)) : [...new Set([...selected, ...vals])]);
  };

  const toggleItem = (src) => {
    const on = selected.includes(src.value);
    onChange(on ? selected.filter((s) => s !== src.value) : [...selected, src.value]);
  };

  return (
    <div style={{ marginBottom: 4 }}>
      <div style={S.label}>Article Sources</div>
      <SourceGroup title="Open Access" subtitle="Full-text search" items={open} accent="#4ade80" selected={selected} onToggle={() => toggleAll(open)} onItemToggle={toggleItem} />
      <SourceGroup title="Paywall" subtitle="Metadata & abstracts" items={paywall} accent="#fbbf24" selected={selected} onToggle={() => toggleAll(paywall)} onItemToggle={toggleItem} />
    </div>
  );
}

SourceSelector.propTypes = {
  selected: PropTypes.arrayOf(PropTypes.string).isRequired,
  onChange: PropTypes.func.isRequired,
};

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
        <div style={S.sliderLabel}>{label}</div>
        <span style={{ ...S.sliderVal, color: c }}>{(value * 100).toFixed(0)}%</span>
      </div>
      {description && <p style={S.sliderDesc}>{description}</p>}
      <input type="range" min="0" max="100" value={value * 100}
        onChange={(e) => onChange(Number(e.target.value) / 100)}
        style={{ ...S.range, accentColor: c }}
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

function ServerQueryPreview({ species, stresses, keywords, timeRange }) {
  const [queries, setQueries] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!species.length && !stresses.length) { setQueries([]); return; }
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await fetch("/api/preview_queries", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ plant_species: species, stress_types: stresses, expertise_keywords: keywords, time_range_months: timeRange }),
        });
        if (res.ok) setQueries(await res.json());
      } catch { /* silent */ } finally { setLoading(false); }
    }, 600);
    return () => clearTimeout(t);
  }, [species, stresses, keywords, timeRange]);

  if (!species.length && !stresses.length) return null;

  return (
    <div style={S.qPrev}>
      <div style={S.qHead}>
        <span style={{ fontSize: 16 }}>⚡</span>
        <span style={S.qTitle}>Live Query Preview</span>
        {loading && <span style={{ fontSize: 11, color: "#64748b" }}>updating…</span>}
        <span style={S.qBadge}>{queries.length} queries</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {queries.map((q) => (
          <div key={q.query + q.source} style={S.qItem}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
              <span style={{ ...S.qType, background: q.access_type === "open" ? "#16312b" : "#312e16", color: q.access_type === "open" ? "#4ade80" : "#fbbf24" }}>
                {q.access_type === "open" ? "full-text" : "abstract"}
              </span>
              <span style={{ fontSize: 10, color: "#4b5563" }}>→ {q.source}</span>
            </div>
            <code style={S.qCode}>{q.query}</code>
          </div>
        ))}
      </div>
    </div>
  );
}

ServerQueryPreview.propTypes = {
  species: PropTypes.array.isRequired,
  stresses: PropTypes.array.isRequired,
  keywords: PropTypes.array.isRequired,
  timeRange: PropTypes.number.isRequired,
};

function PaperCard({ paper, expanded, onToggle, isNew }) {
  const bw = (v) => `${Math.max(v * 100, 2)}%`;
  const bc = (v) => {
    if (v > 0.7) return "#4ade80";
    if (v > 0.4) return "#fbbf24";
    return "#ef4444";
  };
  const paperUrl = paper.doi ? `https://doi.org/${paper.doi}` : paper.url;

  return (
    <div style={{ ...S.pCard, ...(expanded ? { borderColor: "#334155" } : {}) }}>
      <div style={S.pHead}>
        <div style={S.pRank}>#{paper.rank}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h4 style={S.pTitle}>
            {isNew && <span style={S.newBadge}>NEW</span>}
            {paperUrl ? (
              <a href={paperUrl} target="_blank" rel="noreferrer"
                style={{ color: "inherit", textDecoration: "none" }}
              >{paper.title} <span style={{ fontSize: 10, opacity: 0.5 }}>↗</span></a>
            ) : paper.title}
          </h4>
          <div style={S.pMeta}>
            <span>{paper.authors.join(", ")}</span>
            <span style={S.dot}>·</span>
            <span style={{ fontStyle: "italic" }}>{paper.journal}</span>
            <span style={S.dot}>·</span>
            <span>{paper.published?.slice(0, 7)}</span>
          </div>
        </div>
        <button onClick={onToggle} style={S.pBadgeBtn} aria-label={expanded ? "Collapse" : "Expand details"}>
          <span style={{ fontSize: 18 }}>{paper.credibility_icon}</span>
          <span style={S.pScore}>{paper.scores.overall.toFixed(2)}</span>
          <span style={{ fontSize: 10, opacity: 0.5, marginLeft: 4 }}>{expanded ? "▲" : "▼"}</span>
        </button>
      </div>

      {expanded && (
        <div style={S.pExp}>
          <div style={S.sGrid}>
            {[["Species", paper.scores.species_match], ["Stress", paper.scores.stress_match],
              ["Method", paper.scores.method_match], ["Recency", paper.scores.recency],
              ["Credibility", paper.scores.credibility], ["Novelty", paper.scores.novelty],
            ].map(([l, v]) => (
              <div key={l} style={S.sRow}>
                <span style={S.sLbl}>{l}</span>
                <div style={S.sBarBg}><div style={{ ...S.sBar, width: bw(v), backgroundColor: bc(v) }} /></div>
                <span style={S.sVal}>{(v * 100).toFixed(0)}%</span>
              </div>
            ))}
          </div>
          {paper.suggested_combinations.length > 0 && (
            <div style={S.cBox}>
              <div style={S.cTitle}>💡 Suggested Combinations</div>
              {paper.suggested_combinations.map((c) => (
                <div key={c} style={S.cLine}>{c}</div>
              ))}
            </div>
          )}
          <div style={S.tags}>
            {paper.doi && <span style={S.tag}>DOI: {paper.doi}</span>}
            {paper.is_open_access && <span style={{ ...S.tag, ...S.tagOA }}>Open Access</span>}
            <span style={{ ...S.tag, ...S.tagSrc }}>{paper.source}</span>
          </div>
        </div>
      )}
    </div>
  );
}

PaperCard.propTypes = {
  paper: PropTypes.object.isRequired,
  expanded: PropTypes.bool.isRequired,
  onToggle: PropTypes.func.isRequired,
  isNew: PropTypes.bool,
};

// ─────────────────────────────────────────────────
// CombosTab — theme-grouped proposals with feedback
// ─────────────────────────────────────────────────

const FEASIBILITY_STYLE = {
  true:      { color: "#4ade80", bg: "#0a1f12", border: "#1a4a2a", icon: "✓" },
  partial:   { color: "#fbbf24", bg: "#1a1204", border: "#4a3a0a", icon: "~" },
  false:     { color: "#f87171", bg: "#1a0808", border: "#4a1a1a", icon: "✗" },
};

const FEASIBILITY_LABEL = { true: "Feasible", partial: "Partially feasible", false: "Not feasible" };

function FeasibilityBadge({ f }) {
  if (f?.feasible == null) return null;
  let key;
  if (f.feasible === true) key = "true";
  else if (f.feasible === false) key = "false";
  else key = "partial";
  const { color, bg, border, icon } = FEASIBILITY_STYLE[key];
  const feasibilityLabel = FEASIBILITY_LABEL[key];
  return (
    <span title={f.note} style={{ fontSize: 10, fontWeight: 700, color, background: bg, border: `1px solid ${border}`, borderRadius: 10, padding: "2px 8px", whiteSpace: "nowrap", cursor: "help" }}>
      {icon} {feasibilityLabel}
      {f.confidence ? ` · ${(f.confidence * 100).toFixed(0)}%` : ""}
    </span>
  );
}

function FeasibilityDetail({ f }) {
  if (!f?.note) return null;
  return (
    <div style={{ marginTop: 8, background: "#0c0f1a", borderRadius: 6, padding: "8px 12px" }}>
      <div style={{ fontSize: 11, color: "#64748b", lineHeight: 1.5 }}>{f.note}</div>
      {f.missing_equipment?.length > 0 && (
        <div style={{ marginTop: 4, display: "flex", gap: 6, flexWrap: "wrap" }}>
          <span style={{ fontSize: 10, color: "#f87171", fontWeight: 600 }}>Missing:</span>
          {f.missing_equipment.map((e) => (
            <span key={e} style={{ fontSize: 10, color: "#f87171", background: "#1a0808", border: "1px solid #4a1a1a", borderRadius: 6, padding: "1px 6px" }}>{e}</span>
          ))}
        </div>
      )}
      {f.adaptation && (
        <div style={{ marginTop: 4, fontSize: 11, color: "#fbbf24", fontStyle: "italic" }}>Adaptation: {f.adaptation}</div>
      )}
    </div>
  );
}

function VerificationPanel({ v }) {
  const [expanded, setExpanded] = useState(false);
  if (!v) return null;

  const total = v.supported + v.unsupported;
  const summaryText = total > 0 ? `✓ ${v.supported}/${total} claims verified` : null;

  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        {v.flagged && (
          <span style={S.verifyFlag} title="More than 1/3 of checked claims could not be verified against source papers">
            ⚠ Verification concern
          </span>
        )}
        {summaryText && (
          <button onClick={() => setExpanded((x) => !x)} style={S.verifyBtn}>
            {summaryText} {expanded ? "▲" : "▼"}
          </button>
        )}
      </div>
      {expanded && v.details && v.details.length > 0 && (
        <div style={S.verifyDetails}>
          {v.details.map((d, i) => (
            <div key={i} style={{ ...S.verifyDetail, borderColor: d.supported === true ? "#1a4a2a" : d.supported === false ? "#4a1a1a" : "#334155" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: d.supported === true ? "#4ade80" : d.supported === false ? "#f87171" : "#64748b" }}>
                  {d.supported === true ? "✓ Supported" : d.supported === false ? "✗ Unsupported" : "? Failed"}
                </span>
                <span style={{ fontSize: 10, color: "#475569" }}>{d.paper_id ? d.paper_id.slice(0, 8) : ""}</span>
                {d.confidence != null && (
                  <span style={{ fontSize: 10, color: "#64748b", marginLeft: "auto" }}>{(d.confidence * 100).toFixed(0)}% conf</span>
                )}
              </div>
              <div style={{ fontSize: 12, color: "#94a3b8", lineHeight: 1.5, marginBottom: 3 }}>{d.claim}</div>
              {d.reason && <div style={{ fontSize: 11, color: "#64748b", fontStyle: "italic" }}>{d.reason}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

VerificationPanel.propTypes = { v: PropTypes.object };

function RagComboCard({ c, rating, onRate }) {
  return (
    <div style={S.ragComboCard}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
        {c.theme && <span style={S.themeChip}>{c.theme}</span>}
        {c.novelty_warning && <span style={S.noveltyWarn}>⚠ {c.novelty_warning}</span>}
        {c.feasibility && <FeasibilityBadge f={c.feasibility} />}
        <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          <button onClick={(e) => { e.stopPropagation(); onRate(c, 1); }}
            style={{ ...S.rateBtn, ...(rating === 1 ? S.rateBtnUp : {}) }} title="Useful">👍</button>
          <button onClick={(e) => { e.stopPropagation(); onRate(c, -1); }}
            style={{ ...S.rateBtn, ...(rating === -1 ? S.rateBtnDown : {}) }} title="Not useful">👎</button>
        </div>
      </div>
      <div style={{ fontSize: 14, fontWeight: 600, color: "#e2e8f0", lineHeight: 1.6, marginBottom: 6 }}>
        {c.suggestion}
      </div>
      {c.rationale && (
        <div style={{ fontSize: 13, color: "#94a3b8", lineHeight: 1.5, marginBottom: 10 }}>
          {c.rationale}
        </div>
      )}
      {c.key_insights && c.key_insights.length > 0 && (
        <div style={S.insightBlock}>
          <div style={S.insightTitle}>Key insights</div>
          {c.key_insights.map((ki) => (
            <div key={ki.paper_id || ki.paper} style={S.insightRow}>
              <span style={S.insightPaper}>{ki.paper_id ? ki.paper_id.slice(0, 8) : ki.paper}</span>
              <span style={S.insightText}>{ki.insight}</span>
            </div>
          ))}
        </div>
      )}
      <VerificationPanel v={c.verification} />
      <FeasibilityDetail f={c.feasibility} />
    </div>
  );
}

FeasibilityBadge.propTypes = { f: PropTypes.object };
FeasibilityDetail.propTypes = { f: PropTypes.object };

function CombosTab({ ragCombos, combos, ratings, onRate }) {
  const grouped = useMemo(() => {
    const map = new Map();
    for (const c of ragCombos) {
      const key = c.theme || "Other";
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(c);
    }
    return [...map.entries()];
  }, [ragCombos]);

  if (ragCombos.length === 0 && combos.length === 0) {
    return (
      <div style={S.empty}>
        <span style={{ fontSize: 48 }}>💡</span>
        <p style={{ color: "#94a3b8", marginTop: 12 }}>No combination suggestions yet. Run a scan first.</p>
      </div>
    );
  }

  return (
    <div>
      {grouped.length > 0 && (
        <div style={{ ...S.card, marginBottom: 24 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
            <h3 style={S.cardH}>AI-Synthesised Proposals</h3>
            <span style={S.ragBadge}>RAG · cross-paper</span>
          </div>
          <p style={S.cardSub}>Novel experiment designs reasoned over multiple papers together — grouped by research theme</p>
          {grouped.map(([theme, items]) => (
            <div key={theme}>
              <div style={S.themeHeader}>{theme}</div>
              {items.map((c) => (
                <RagComboCard key={c.proposal_id || c.suggestion} c={c} rating={ratings[c.proposal_id]} onRate={onRate} />
              ))}
            </div>
          ))}
        </div>
      )}

      {combos.length > 0 && (
        <div style={S.card}>
          <h3 style={S.cardH}>Per-Paper Hypotheses</h3>
          <p style={S.cardSub}>One-sentence ideas generated per paper during scoring — quick signals, not cross-paper reasoning</p>
          {combos.map((c) => (
            <div key={c.source_doi || c.suggestion} style={S.comboCard}>
              <div style={{ fontSize: 14, color: "#e2e8f0", lineHeight: 1.5, marginBottom: 8 }}>{c.suggestion}</div>
              <div style={{ fontSize: 12, color: "#64748b", display: "flex", alignItems: "center", gap: 6 }}>
                <span>From: <em>{c.source_paper}</em></span>
                <span>{CREDIBILITY_ICONS[c.paper_credibility]} {c.paper_credibility}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

RagComboCard.propTypes = { c: PropTypes.object, rating: PropTypes.number, onRate: PropTypes.func };
CombosTab.propTypes = { ragCombos: PropTypes.array, combos: PropTypes.array, ratings: PropTypes.object, onRate: PropTypes.func };

// ─────────────────────────────────────────────────
// Main Dashboard
// ─────────────────────────────────────────────────

export default function Dashboard({ onBack, researcherName, researcherId }) {
  const [name, setName] = useState(researcherName || "Researcher");
  const [species, setSpecies] = useState(["poplar", "arabidopsis"]);
  const [stresses, setStresses] = useState(["drought", "nutrient"]);
  const methods = PHENOTYPING_METHODS.map((m) => m.value);
  const [researchPrompt, setResearchPrompt] = useState("");
  const [sources, setSources] = useState(["biorxiv", "pubmed", "plos_one", "frontiers", "plant_physiology"]);
  const [timeRange, setTimeRange] = useState(12);

  const [prioNovelty, setPrioNovelty] = useState(0.7);
  const [prioRelevance, setPrioRelevance] = useState(0.8);
  const [prioMethodology, setPrioMethodology] = useState(0.5);
  const [prioReprod, setPrioReprod] = useState(0.6);

  const [tab, setTab] = useState(() => {
    const p = new URLSearchParams(globalThis.location.search).get("tab");
    return p || "profile";
  });
  const [expPaper, setExpPaper] = useState(null);
  const [extractedKeywords, setExtractedKeywords] = useState([]);
  const [kwLoading, setKwLoading] = useState(false);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState(null);
  const [papers, setPapers] = useState([]);
  const [combos, setCombos] = useState([]);
  const [ragCombos, setRagCombos] = useState([]);
  const [contradictions, setContradictions] = useState([]);
  const [ratings, setRatings] = useState({});
  const [anchorInput, setAnchorInput] = useState("");
  const [anchorResults, setAnchorResults] = useState([]);
  const [anchorLoading, setAnchorLoading] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [credF, setCredF] = useState("all");
  const [showNewOnly, setShowNewOnly] = useState(false);
  const [newPaperIds, setNewPaperIds] = useState(new Set());
  const [newSince, setNewSince] = useState(null);
  const [agentStatus, setAgentStatus] = useState(null);

  const RESEARCHER_ID = researcherId || "researcher_001";

  const refreshSessions = useCallback(() => {
    fetch(`/api/sessions/${RESEARCHER_ID}`)
      .then((r) => r.ok ? r.json() : [])
      .then(setSessions)
      .catch(() => {});
  }, []);

  // Poll agent status every 10 seconds
  useEffect(() => {
    const poll = async () => {
      try {
        const res = await fetch("/api/status");
        if (res.ok) setAgentStatus(await res.json());
      } catch {
        // backend not reachable yet
      }
    };
    poll();
    const id = setInterval(poll, 10_000);
    return () => clearInterval(id);
  }, []);

  // Load session history
  useEffect(() => { refreshSessions(); }, [refreshSessions]);

  // Fetch papers added since last login
  useEffect(() => {
    fetch(`/api/researcher/${RESEARCHER_ID}/new-papers`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => {
        if (d && d.new_count > 0) {
          setNewPaperIds(new Set(d.new_papers.map((p) => p.paper_id)));
          setNewSince(d.new_since);
        }
      })
      .catch(() => {});
  }, [RESEARCHER_ID]);

  const doAnchorSearch = useCallback(async () => {
    if (!anchorInput.trim()) return;
    setAnchorLoading(true);
    try {
      const res = await fetch("/api/anchor_search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doi_or_title: anchorInput.trim(), researcher_id: RESEARCHER_ID, n_results: 8 }),
      });
      if (res.ok) setAnchorResults(await res.json());
    } catch {
      setAnchorResults([]);
    } finally {
      setAnchorLoading(false);
    }
  }, [anchorInput]);

  const doExtractKeywords = useCallback(async () => {
    if (!researchPrompt.trim() || kwLoading) return;
    setKwLoading(true);
    try {
      const res = await fetch("/api/extract_keywords", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: researchPrompt }),
      });
      if (res.ok) {
        const data = await res.json();
        setExtractedKeywords(data.keywords || []);
      }
    } catch { /* silent */ } finally {
      setKwLoading(false);
    }
  }, [researchPrompt, kwLoading]);

  const doRate = useCallback(async (proposal, rating) => {
    setRatings((prev) => ({ ...prev, [proposal.proposal_id]: rating }));
    try {
      await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          proposal_id: proposal.proposal_id,
          researcher_id: RESEARCHER_ID,
          suggestion: proposal.suggestion,
          theme: proposal.theme || null,
          rating,
        }),
      });
    } catch {
      // fire-and-forget
    }
  }, []);

  const doSearch = useCallback(async () => {
    setSearching(true);
    setSearchError(null);
    try {
      const res = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          researcher_id: RESEARCHER_ID,
          name,
          plant_species: species,
          stress_types: stresses,
          phenotyping_methods: methods,
          expertise_keywords: extractedKeywords,
          priority_novelty: prioNovelty,
          priority_relevance: prioRelevance,
          priority_methodology: prioMethodology,
          priority_reproducibility: prioReprod,
          time_range_months: timeRange,
          source_targets: sources,
          limit: 20,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || "Search failed");
      }
      const data = await res.json();
      setPapers(data.papers);
      setCombos(data.combos || []);
      setRagCombos(data.rag_combos || []);
      setContradictions(data.contradictions || []);
      setTab("results");
      // Refresh session history
      fetch(`/api/sessions/${RESEARCHER_ID}`).then((r) => r.ok ? r.json() : []).then(setSessions).catch(() => {});
    } catch (err) {
      setSearchError(err.message);
    } finally {
      setSearching(false);
    }
  }, [name, species, stresses, researchPrompt, extractedKeywords, prioNovelty, prioRelevance, prioMethodology, prioReprod, timeRange, sources]);

  const filtered = useMemo(() => {
    let result = credF === "all" ? papers : papers.filter((p) => p.credibility_level === credF);
    if (showNewOnly) result = result.filter((p) => newPaperIds.has(p.paper_id));
    return result;
  }, [papers, credF, showNewOnly, newPaperIds]);

  const isAgentReady = agentStatus?.status === "running";

  let statusText;
  if (agentStatus === null) statusText = "Connecting...";
  else if (isAgentReady) statusText = `Agent Active · ${agentStatus.total_papers_scored} papers · ${agentStatus.queries_executed} queries`;
  else statusText = "Agent Starting...";

  const comboCount = ragCombos.length + combos.length;
  const comboLabel = comboCount ? `Combinations (${comboCount})` : "Combinations";

  const timeRangeDecimals = timeRange % 12 === 0 ? 0 : 1;
  const timeRangeLabel = timeRange >= 12
    ? `${timeRange} mo (${(timeRange / 12).toFixed(timeRangeDecimals)} yr)`
    : `${timeRange} mo`;

  let searchBtnLabel;
  if (searching) searchBtnLabel = "⏳ Scanning Sources...";
  else if (isAgentReady) searchBtnLabel = `🔍 Run Literature Scan (${sources.length} sources)`;
  else searchBtnLabel = "⏳ Waiting for Agent...";

  const newInResults = papers.filter((p) => newPaperIds.has(p.paper_id)).length;
  const resultsLabel = papers.length
    ? `Results (${papers.length}${newInResults > 0 ? ` · ${newInResults} new` : ""})`
    : "Results";

  return (
    <div style={S.root}>
      <header style={S.header}>
        <div style={S.headerIn}>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            {onBack && (
              <button
                style={S.backBtn}
                onClick={onBack}
                title="Back to home"
              >←</button>
            )}
            <div>
              <h1 style={S.h1}>CASSIOPEIA</h1>
              <p style={S.sub}>Context-Aware Semantic Search for Inspiring Original Plant Experiments and Investigations at APPL</p>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
            {researcherName && (
              <span style={{ fontSize: 13, color: "#64748b" }}>
                {researcherName}
              </span>
            )}
            <div style={S.hStatus}>
              <div style={{ ...S.hDot, background: isAgentReady ? "#4ade80" : "#f59e0b", boxShadow: isAgentReady ? "0 0 8px #4ade8066" : "0 0 8px #f59e0b66" }} />
              <span style={S.hTxt}>{statusText}</span>
            </div>
          </div>
        </div>
      </header>

      <nav style={S.tabBar}>
        {[
          { id: "profile", label: "Research Profile", icon: "🧬" },
          { id: "results", label: resultsLabel, icon: "📊" },
          { id: "combos", label: comboLabel, icon: "💡" },
          { id: "contradictions", label: "Contradictions" + (contradictions.length ? ` (${contradictions.length})` : ""), icon: "⚠️" },
        ].map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            style={{ ...S.tabBtn, ...(tab === t.id ? S.tabOn : {}) }}
          ><span>{t.icon}</span> {t.label}</button>
        ))}
      </nav>

      <main style={S.main}>
        {/* ═══ PROFILE ═══ */}
        {tab === "profile" && (
          <div>
            <div style={S.grid2}>
              <div>
                <div style={S.card}>
                  <h3 style={S.cardH}>Research Focus</h3>
                  <div style={{ marginBottom: 20 }}>
                    <div style={S.label}>Researcher Name</div>
                    <input value={name} onChange={(e) => setName(e.target.value)} style={S.input} placeholder="Your name" />
                  </div>
                  <SpeciesSelect selected={species} onChange={setSpecies} />
                  <ChipSelect label="Stress Types" options={STRESS_TYPES} selected={stresses} onChange={setStresses} renderOption={(o) => `${o.icon} ${o.label}`} />
                </div>

                <div style={S.card}>
                  <h3 style={S.cardH}>Research Interest</h3>
                  <p style={S.cardSub}>Describe your research focus in natural language. Keywords are extracted by the LLM when you finish typing and used to refine both scoring and queries.</p>
                  <div style={{ position: "relative" }}>
                    <textarea value={researchPrompt}
                      onChange={(e) => { setResearchPrompt(e.target.value); setExtractedKeywords([]); }}
                      onBlur={doExtractKeywords}
                      style={S.textarea} rows={6}
                      placeholder="e.g. I want to explore how drought-induced changes in poplar root architecture relate to above-ground spectral signatures..."
                    />
                    <span style={S.charCt}>{researchPrompt.length} chars</span>
                  </div>
                  {kwLoading && <div style={{ fontSize: 11, color: "#64748b", marginTop: 8 }}>⏳ Extracting keywords…</div>}
                  {extractedKeywords.length > 0 && (
                    <div style={{ marginTop: 10 }}>
                      <div style={{ fontSize: 11, fontWeight: 700, color: "#94a3b8", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>Extracted keywords</div>
                      <div style={S.chipWrap}>
                        {extractedKeywords.map((kw) => (
                          <button key={kw} onClick={() => setExtractedKeywords(extractedKeywords.filter((k) => k !== kw))}
                            style={{ ...S.chipOn, display: "flex", alignItems: "center", gap: 5 }}>
                            {kw} <span style={{ fontSize: 10, opacity: 0.6 }}>✕</span>
                          </button>
                        ))}
                      </div>
                      <div style={{ fontSize: 11, color: "#475569", marginTop: 5 }}>Click a keyword to remove it before searching.</div>
                    </div>
                  )}
                </div>

              </div>

              <div>
                <div style={S.card}>
                  <h3 style={S.cardH}>Priority Weights</h3>
                  <p style={S.cardSub}>Adjust how papers are ranked relative to your interests</p>
                  <Slider label="Novelty" value={prioNovelty} onChange={setPrioNovelty} description="Prefer unique, unexplored approaches" />
                  <Slider label="Relevance" value={prioRelevance} onChange={setPrioRelevance} description="Match to your species and stress focus" />
                  <Slider label="Methodology" value={prioMethodology} onChange={setPrioMethodology} description="Alignment with your phenotyping methods" />
                  <Slider label="Reproducibility" value={prioReprod} onChange={setPrioReprod} description="Evidence quality and source credibility" />
                </div>

                <div style={S.card}>
                  <SourceSelector selected={sources} onChange={setSources} />
                  <div style={{ marginTop: 16 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div style={{ ...S.label, marginBottom: 0 }}>Search Time Range</div>
                      <span style={S.sliderVal}>
                        {timeRangeLabel}
                      </span>
                    </div>
                    <input type="range" min="3" max="120" step="3" value={timeRange} onChange={(e) => setTimeRange(Number(e.target.value))} style={{ ...S.range, marginTop: 8, width: "100%" }} />
                  </div>
                </div>
              </div>
            </div>

            {/* Anchor paper search */}
            <div style={S.card}>
              <h3 style={S.cardH}>Anchor Paper Search</h3>
              <p style={S.cardSub}>Enter a DOI or title fragment to find similar papers in your knowledge base</p>
              <div style={{ display: "flex", gap: 10 }}>
                <input
                  value={anchorInput}
                  onChange={(e) => setAnchorInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && doAnchorSearch()}
                  style={{ ...S.input, flex: 1 }}
                  placeholder="10.1093/jxb/erx456  or  Poplar root architecture under drought"
                />
                <button onClick={doAnchorSearch} disabled={anchorLoading || !anchorInput.trim()} style={{ ...S.goBtn, width: "auto", padding: "10px 20px", fontSize: 13 }}>
                  {anchorLoading ? "⏳" : "Find Similar"}
                </button>
              </div>
              {anchorResults.length > 0 && (
                <div style={{ marginTop: 14 }}>
                  {anchorResults.map((r) => (
                    <div key={r.paper_id} style={{ ...S.comboCard, marginBottom: 8 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, color: "#e2e8f0", marginBottom: 4 }}>{r.title}</div>
                      <div style={{ fontSize: 11, color: "#64748b" }}>{r.journal} {r.doi && `· ${r.doi}`} · similarity {(r.similarity * 100).toFixed(0)}%</div>
                      <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 6 }}>{r.abstract_snippet}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <ServerQueryPreview species={species} stresses={stresses} keywords={extractedKeywords} timeRange={timeRange} />

            {/* Session history */}
            {sessions.length > 0 && (
              <div style={S.card}>
                <h3 style={S.cardH}>Previous Sessions</h3>
                <p style={S.cardSub}>Recent searches for this profile</p>
                {sessions.slice(0, 8).map((s) => (
                  <div key={s.session_id} style={S.sessionRow}>
                    <span style={S.sessionDate}>{s.timestamp.slice(0, 16).replace("T", " ")}</span>
                    <span style={S.sessionMeta}>{s.n_papers} papers · {s.n_proposals} proposals</span>
                    <span style={{ fontSize: 11, color: "#475569" }}>{(s.profile.plant_species || []).join(", ")}</span>
                  </div>
                ))}
              </div>
            )}

            {searchError && (
              <div style={S.errBox}>{searchError}</div>
            )}

            <button onClick={doSearch} disabled={searching || !isAgentReady || (!species.length && !stresses.length)}
              style={{ ...S.goBtn, ...((searching || !isAgentReady || (!species.length && !stresses.length)) ? S.goDis : {}) }}
            >
              {searchBtnLabel}
            </button>
          </div>
        )}

        {/* ═══ RESULTS ═══ */}
        {tab === "results" && (
          <div>
            {papers.length === 0 ? (
              <div style={S.empty}><span style={{ fontSize: 48 }}>📚</span><p style={{ color: "#94a3b8", marginTop: 12 }}>No results yet. Configure your profile and run a literature scan.</p></div>
            ) : (<>
              <div style={S.fBar}>
                <span style={S.fLabel}>Credibility:</span>
                {["all", "high", "moderate", "preliminary", "conflicting"].map((f) => (
                  <button key={f} onClick={() => setCredF(f)} style={{ ...S.fBtn, ...(credF === f ? S.fOn : {}) }}>
                    {f === "all" ? "All" : `${CREDIBILITY_ICONS[f]} ${f.charAt(0).toUpperCase() + f.slice(1)}`}
                  </button>
                ))}
                {newInResults > 0 && (
                  <button
                    onClick={() => setShowNewOnly((v) => !v)}
                    style={{ ...S.fBtn, ...(showNewOnly ? S.fNewOn : S.fNew), marginLeft: 12 }}
                    title={newSince ? `Papers added since ${new Date(newSince).toLocaleString()}` : "Papers added since your last visit"}
                  >
                    🆕 New ({newInResults})
                  </button>
                )}
                <span style={{ marginLeft: "auto", fontSize: 12, color: "#4b5563" }}>{filtered.length} papers</span>
              </div>
              {filtered.map((p) => (
                <PaperCard key={p.rank} paper={p} expanded={expPaper === p.rank} onToggle={() => setExpPaper(expPaper === p.rank ? null : p.rank)} isNew={newPaperIds.has(p.paper_id)} />
              ))}
            </>)}
          </div>
        )}

        {/* ═══ COMBINATIONS ═══ */}
        {tab === "combos" && (
          <CombosTab
            ragCombos={ragCombos}
            combos={combos}
            ratings={ratings}
            onRate={doRate}
          />
        )}

        {/* ═══ CONTRADICTIONS ═══ */}
        {tab === "contradictions" && (
          <div>
            {contradictions.length === 0 ? (
              <div style={S.empty}>
                <span style={{ fontSize: 48 }}>⚠️</span>
                <p style={{ color: "#94a3b8", marginTop: 12 }}>No contradictions detected yet. Run a scan with enough papers to populate the knowledge base.</p>
              </div>
            ) : (
              <div style={S.card}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
                  <h3 style={S.cardH}>Detected Contradictions</h3>
                  <span style={{ ...S.ragBadge, borderColor: "#7c3a1a", color: "#fb923c", background: "#1a0e06" }}>{contradictions.length} found</span>
                </div>
                <p style={S.cardSub}>Papers in your knowledge base that present conflicting or irreconcilable findings</p>
                {contradictions.map((c) => (
                  <div key={[...c.papers].sort((a, b) => a.localeCompare(b)).join("|")} style={{ ...S.ragComboCard, borderColor: "#3a1a0a", background: "#120a04", marginBottom: 14 }}>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
                      {c.papers.map((p) => <span key={p} style={{ fontSize: 11, fontWeight: 600, color: "#fb923c", background: "#1f0c04", border: "1px solid #7c3a1a", borderRadius: 6, padding: "2px 8px" }}>{p}</span>)}
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 10 }}>
                      <div style={{ background: "#0c1a08", borderRadius: 6, padding: "8px 12px" }}>
                        <div style={{ fontSize: 10, fontWeight: 700, color: "#4ade80", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>Claim A</div>
                        <div style={{ fontSize: 12, color: "#94a3b8", lineHeight: 1.5 }}>{c.claim_a}</div>
                      </div>
                      <div style={{ background: "#1a0c08", borderRadius: 6, padding: "8px 12px" }}>
                        <div style={{ fontSize: 10, fontWeight: 700, color: "#f87171", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>Claim B</div>
                        <div style={{ fontSize: 12, color: "#94a3b8", lineHeight: 1.5 }}>{c.claim_b}</div>
                      </div>
                    </div>
                    {c.resolution_hint && (
                      <div style={{ fontSize: 12, color: "#64748b", fontStyle: "italic" }}>
                        Possible resolution: {c.resolution_hint}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </main>

      <footer style={S.footer}>APPL Facility · Academy Agent Framework · CASSIOPEIA v0.1</footer>
    </div>
  );
}

// ─────────────────────────────────────────────────
// Styles
// ─────────────────────────────────────────────────

const S = {
  root: { fontFamily: "'IBM Plex Sans', 'SF Pro Display', -apple-system, sans-serif", background: "#0c0f1a", color: "#e2e8f0", minHeight: "100vh", display: "flex", flexDirection: "column" },
  header: { background: "linear-gradient(135deg, #0f172a 0%, #1a1f3a 100%)", borderBottom: "1px solid #1e293b", padding: "20px 24px" },
  headerIn: { maxWidth: 1200, margin: "0 auto", display: "flex", justifyContent: "space-between", alignItems: "center" },
  h1: { fontSize: 22, fontWeight: 700, margin: 0, background: "linear-gradient(135deg, #4ade80, #22d3ee)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent", letterSpacing: "-0.02em" },
  sub: { fontSize: 13, color: "#64748b", margin: "4px 0 0" },
  hStatus: { display: "flex", alignItems: "center", gap: 8 },
  hDot: { width: 8, height: 8, borderRadius: "50%", flexShrink: 0 },
  hTxt: { fontSize: 12, color: "#94a3b8" },

  tabBar: { display: "flex", gap: 2, padding: "0 24px", background: "#0f1225", borderBottom: "1px solid #1e293b", maxWidth: 1200, margin: "0 auto", width: "100%", boxSizing: "border-box" },
  tabBtn: { padding: "12px 20px", border: "none", background: "transparent", color: "#64748b", fontSize: 13, fontWeight: 500, cursor: "pointer", borderBottom: "2px solid transparent", display: "flex", alignItems: "center", gap: 6, transition: "all 0.2s" },
  tabOn: { color: "#4ade80", borderBottomColor: "#4ade80" },

  main: { flex: 1, maxWidth: 1200, margin: "0 auto", padding: 24, width: "100%", boxSizing: "border-box" },
  grid2: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24, alignItems: "start" },
  card: { background: "#111827", border: "1px solid #1e293b", borderRadius: 12, padding: 24, marginBottom: 20 },
  cardH: { fontSize: 16, fontWeight: 600, margin: "0 0 4px", color: "#f1f5f9" },
  cardSub: { fontSize: 13, color: "#64748b", margin: "0 0 20px" },

  label: { display: "block", fontSize: 11, fontWeight: 700, color: "#94a3b8", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 8 },
  input: { width: "100%", padding: "10px 14px", background: "#1e293b", border: "1px solid #334155", borderRadius: 8, color: "#e2e8f0", fontSize: 14, outline: "none", boxSizing: "border-box" },

  speciesGrid: { display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 },
  speciesCard: { padding: "10px 6px", borderRadius: 8, border: "1px solid #1e293b", background: "#0c0f1a", cursor: "pointer", textAlign: "center", transition: "all 0.15s", display: "flex", flexDirection: "column", gap: 2 },
  speciesOn: { background: "#164e3f", borderColor: "#4ade80" },
  speciesName: { fontSize: 12, fontWeight: 600, color: "#e2e8f0" },
  speciesLatin: { fontSize: 9, color: "#4b5563", fontStyle: "italic" },

  chipWrap: { display: "flex", flexWrap: "wrap", gap: 6 },
  chip: { padding: "7px 14px", borderRadius: 20, border: "1px solid #334155", background: "#1e293b", color: "#94a3b8", fontSize: 12, cursor: "pointer", transition: "all 0.15s", whiteSpace: "nowrap" },
  chipOn: { background: "#164e3f", borderColor: "#4ade80", color: "#4ade80" },

  sourceGrid: { display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 6 },
  sourceCard: { padding: "8px 12px", borderRadius: 8, border: "1px solid #1e293b", background: "#0c0f1a", cursor: "pointer", textAlign: "left", transition: "all 0.15s" },
  sourceOn: { background: "#111e1a" },
  sourceName: { fontSize: 12, fontWeight: 600, color: "#94a3b8", display: "block" },
  sourceDesc: { fontSize: 10, color: "#4b5563" },
  toggleBtn: { marginLeft: "auto", fontSize: 10, color: "#64748b", background: "none", border: "none", cursor: "pointer", textDecoration: "underline", padding: 0 },

  textarea: { width: "100%", padding: "14px 16px", background: "#0c0f1a", border: "1px solid #334155", borderRadius: 10, color: "#e2e8f0", fontSize: 13, lineHeight: 1.7, resize: "vertical", outline: "none", boxSizing: "border-box", fontFamily: "inherit", minHeight: 120 },
  charCt: { position: "absolute", bottom: 10, right: 14, fontSize: 10, color: "#334155" },

  sliderLabel: { fontSize: 13, fontWeight: 600, color: "#e2e8f0" },
  sliderVal: { fontSize: 13, fontWeight: 700, color: "#4ade80" },
  sliderDesc: { fontSize: 11, color: "#64748b", margin: "2px 0 8px" },
  range: { width: "100%", height: 4, cursor: "pointer", appearance: "auto" },

  qPrev: { background: "#111827", border: "1px solid #1e293b", borderRadius: 12, padding: 16, marginBottom: 20 },
  qHead: { display: "flex", alignItems: "center", gap: 8, marginBottom: 12 },
  qTitle: { fontSize: 13, fontWeight: 600, color: "#fbbf24" },
  qBadge: { marginLeft: "auto", fontSize: 11, color: "#64748b", background: "#1e293b", padding: "2px 8px", borderRadius: 10 },
  qItem: { background: "#0c0f1a", borderRadius: 6, padding: "8px 12px", border: "1px solid #1e293b" },
  qType: { fontSize: 9, fontWeight: 700, padding: "2px 6px", borderRadius: 4, textTransform: "uppercase", letterSpacing: "0.04em" },
  qCode: { fontSize: 11, color: "#22d3ee", fontFamily: "'IBM Plex Mono', monospace", wordBreak: "break-all" },

  errBox: { background: "#1f0f0f", border: "1px solid #7f1d1d", borderRadius: 8, padding: "12px 16px", color: "#fca5a5", fontSize: 13, marginBottom: 16 },

  goBtn: { width: "100%", padding: "14px 24px", background: "linear-gradient(135deg, #4ade80, #22d3ee)", color: "#0c0f1a", border: "none", borderRadius: 10, fontSize: 15, fontWeight: 700, cursor: "pointer", letterSpacing: "-0.01em", transition: "opacity 0.2s" },
  goDis: { opacity: 0.5, cursor: "not-allowed" },

  fBar: { display: "flex", alignItems: "center", gap: 8, marginBottom: 20, flexWrap: "wrap" },
  fLabel: { fontSize: 12, color: "#64748b", fontWeight: 600 },
  fBtn: { padding: "6px 12px", borderRadius: 16, border: "1px solid #334155", background: "#1e293b", color: "#94a3b8", fontSize: 12, cursor: "pointer", transition: "all 0.15s" },
  fOn: { background: "#164e3f", borderColor: "#4ade80", color: "#4ade80" },
  fNew: { borderColor: "#7c3aed", color: "#a78bfa" },
  fNewOn: { background: "#1e1033", borderColor: "#7c3aed", color: "#a78bfa" },
  newBadge: { display: "inline-block", fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", padding: "2px 6px", borderRadius: 8, background: "#1e1033", border: "1px solid #7c3aed", color: "#a78bfa", marginRight: 6, verticalAlign: "middle" },

  pCard: { background: "#111827", border: "1px solid #1e293b", borderRadius: 10, padding: 16, marginBottom: 10, cursor: "pointer", transition: "border-color 0.2s" },
  pHead: { display: "flex", alignItems: "flex-start", gap: 14 },
  pRank: { fontSize: 13, fontWeight: 700, color: "#4ade80", minWidth: 28, paddingTop: 2 },
  pTitle: { fontSize: 14, fontWeight: 600, margin: 0, lineHeight: 1.4, color: "#f1f5f9" },
  pMeta: { fontSize: 12, color: "#64748b", marginTop: 4, display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap" },
  dot: { color: "#334155" },
  pBadge: { display: "flex", flexDirection: "column", alignItems: "center", gap: 2, minWidth: 48 },
  pBadgeBtn: { display: "flex", flexDirection: "column", alignItems: "center", gap: 2, minWidth: 48, background: "none", border: "none", cursor: "pointer", padding: "4px 6px", borderRadius: 6 },
  pScore: { fontSize: 13, fontWeight: 700, color: "#fbbf24" },
  pExp: { marginTop: 16, paddingTop: 16, borderTop: "1px solid #1e293b" },
  sGrid: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 24px", marginBottom: 16 },
  sRow: { display: "flex", alignItems: "center", gap: 8 },
  sLbl: { fontSize: 11, color: "#94a3b8", minWidth: 64 },
  sBarBg: { flex: 1, height: 6, borderRadius: 3, background: "#1e293b", overflow: "hidden" },
  sBar: { height: "100%", borderRadius: 3, transition: "width 0.4s ease" },
  sVal: { fontSize: 11, color: "#e2e8f0", minWidth: 32, textAlign: "right" },
  cBox: { background: "#0c0f1a", borderRadius: 8, padding: 12, marginBottom: 12 },
  cTitle: { fontSize: 12, fontWeight: 600, color: "#fbbf24", marginBottom: 8 },
  cLine: { fontSize: 12, color: "#94a3b8", padding: "4px 0", lineHeight: 1.5 },
  tags: { display: "flex", gap: 8, flexWrap: "wrap" },
  tag: { fontSize: 11, padding: "3px 10px", borderRadius: 12, background: "#1e293b", color: "#94a3b8", border: "1px solid #334155" },
  tagOA: { background: "#164e3f", borderColor: "#4ade80", color: "#4ade80" },
  tagSrc: { color: "#22d3ee" },

  comboCard: { background: "#0c0f1a", border: "1px solid #1e293b", borderRadius: 10, padding: 16, marginBottom: 10 },

  ragBadge: { fontSize: 10, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", padding: "3px 10px", borderRadius: 12, background: "#0c1f2d", border: "1px solid #0e4a6e", color: "#22d3ee" },
  ragComboCard: { background: "#0c1422", border: "1px solid #0e2a4a", borderRadius: 10, padding: 18, marginBottom: 12 },
  insightBlock: { background: "#0c0f1a", borderRadius: 8, padding: "10px 14px", marginTop: 4 },
  insightTitle: { fontSize: 10, fontWeight: 700, color: "#475569", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 },
  insightRow: { display: "flex", gap: 10, marginBottom: 6, alignItems: "flex-start" },
  insightPaper: { fontSize: 11, fontWeight: 600, color: "#22d3ee", whiteSpace: "nowrap", flexShrink: 0, paddingTop: 1 },
  insightText: { fontSize: 12, color: "#64748b", lineHeight: 1.5 },

  empty: { textAlign: "center", padding: "60px 24px" },
  footer: { textAlign: "center", padding: "16px 24px", fontSize: 11, color: "#334155", borderTop: "1px solid #1e293b" },
  backBtn: { background: "none", border: "1px solid #334155", color: "#64748b", borderRadius: 8, padding: "6px 12px", fontSize: 16, cursor: "pointer", lineHeight: 1 },

  themeHeader: { fontSize: 11, fontWeight: 700, color: "#22d3ee", textTransform: "uppercase", letterSpacing: "0.08em", padding: "10px 0 6px", borderBottom: "1px solid #0e2a4a", marginBottom: 12 },
  themeChip: { fontSize: 10, fontWeight: 700, color: "#22d3ee", background: "#0c1f2d", border: "1px solid #0e3a5e", borderRadius: 10, padding: "2px 10px", whiteSpace: "nowrap" },
  noveltyWarn: { fontSize: 10, fontWeight: 600, color: "#fbbf24", background: "#1a1204", border: "1px solid #7c5a0a", borderRadius: 10, padding: "2px 10px", whiteSpace: "nowrap" },
  rateBtn: { background: "none", border: "1px solid #334155", borderRadius: 6, padding: "3px 7px", cursor: "pointer", fontSize: 14, lineHeight: 1, opacity: 0.5, transition: "all 0.15s" },
  rateBtnUp: { borderColor: "#4ade80", background: "#0a1f12", opacity: 1 },
  rateBtnDown: { borderColor: "#f87171", background: "#1a0808", opacity: 1 },

  sessionRow: { display: "flex", alignItems: "center", gap: 16, padding: "8px 0", borderBottom: "1px solid #1e293b", flexWrap: "wrap" },
  sessionDate: { fontSize: 12, color: "#64748b", fontVariantNumeric: "tabular-nums", minWidth: 130 },
  sessionMeta: { fontSize: 12, color: "#94a3b8", fontWeight: 600 },

  verifyFlag: { fontSize: 10, fontWeight: 700, color: "#fbbf24", background: "#1a1204", border: "1px solid #7c5a0a", borderRadius: 10, padding: "2px 10px", whiteSpace: "nowrap" },
  verifyBtn: { fontSize: 10, fontWeight: 600, color: "#64748b", background: "none", border: "1px solid #334155", borderRadius: 10, padding: "2px 10px", cursor: "pointer" },
  verifyDetails: { marginTop: 8, display: "flex", flexDirection: "column", gap: 6 },
  verifyDetail: { background: "#0c0f1a", borderRadius: 6, padding: "8px 12px", border: "1px solid #334155" },
};

Dashboard.propTypes = { onBack: PropTypes.func, researcherName: PropTypes.string, researcherId: PropTypes.string };
