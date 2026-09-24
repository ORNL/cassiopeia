// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

// Form editor for one domain pack's domain.yaml. It edits the parsed YAML as a
// plain object, so fields the form does not show are kept as they are.

import { useState } from "react";
import PropTypes from "prop-types";
import {
  C, S, Check, Field, Notice, NumberInput, Select, TextArea, TextInput,
  fromList, getIn, setIn, setupApi, toList,
} from "./ui.jsx";

const ROLES = [
  ["subject", "subject — the thing studied (in queries)"],
  ["condition", "condition — what is varied (in queries)"],
  ["technique", "technique — how it is measured (scored only)"],
];
const WIDGETS = [["chips", "chips"], ["cards", "cards (shows detail)"], ["none", "none (hidden)"]];

export const SECTIONS = [
  ["general", "General"],
  ["facets", "Facets"],
  ["sources", "Sources"],
  ["credibility", "Credibility"],
  ["prompts", "Prompts"],
  ["ui", "Dashboard text"],
  ["advanced", "Advanced"],
];

// ── General ──────────────────────────────────────────────────────────────────

function General({ data, set, summary }) {
  return (
    <>
      <Field label="Title" help="Shown in the pack list and as a default page title.">
        <TextInput value={data.title} onChange={(v) => set(["title"], v)} />
      </Field>
      <Field label="Pack name" help="The directory under domains/ and the value of DOMAIN_PACK. It cannot change.">
        <TextInput value={data.name} onChange={() => {}} disabled mono />
      </Field>
      {(summary?.has_hooks || summary?.has_ui) && (
        <Notice>
          This pack also has code the wizard does not edit:
          {summary.has_hooks && <> <code style={S.mono}>hooks.py</code> (proposal evaluators, custom backends)</>}
          {summary.has_hooks && summary.has_ui && " and"}
          {summary.has_ui && <> <code style={S.mono}>ui/index.jsx</code> (dashboard components)</>}.
          Edit those files directly; dashboard components need a rebuild.
        </Notice>
      )}
    </>
  );
}

General.propTypes = { data: PropTypes.object.isRequired, set: PropTypes.func.isRequired, summary: PropTypes.object };

// ── Facets ───────────────────────────────────────────────────────────────────

function move(list, i, d) {
  const j = i + d;
  if (j < 0 || j >= list.length) return list;
  const next = [...list];
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}

function Vocabulary({ facet, path, set, used }) {
  const items = facet.vocabulary || [];
  const closed = facet.open_vocabulary === false;
  const put = (list) => set([...path, "vocabulary"], list);
  const cell = { padding: "4px 4px", verticalAlign: "top" };
  return (
    <div style={{ marginTop: 6 }}>
      <span style={S.label}>Vocabulary ({items.length})</span>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead>
            <tr style={{ color: C.dim, textAlign: "left" }}>
              {["Value", "Label", "Detail", "Icon", "Aliases (comma-separated)", "Hidden", ""].map((h) => (
                <th key={h} style={{ ...cell, fontWeight: 600 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((item, i) => {
              const n = used?.[item.value] || 0;
              const at = (k) => [...path, "vocabulary", i, k];
              return (
                <tr key={i} style={{ borderTop: `1px solid ${C.border}` }}>
                  <td style={{ ...cell, width: "16%" }}>
                    <TextInput value={item.value} mono disabled={n > 0} ariaLabel="Value"
                      title={n > 0 ? `Used by ${n} profile(s): values in use cannot be renamed` : undefined}
                      onChange={(v) => set(at("value"), v)} />
                    {n > 0 && <span style={{ ...S.help, color: C.accent2 }}>used by {n}</span>}
                  </td>
                  <td style={{ ...cell, width: "16%" }}><TextInput value={item.label} ariaLabel="Label" onChange={(v) => set(at("label"), v)} /></td>
                  <td style={{ ...cell, width: "18%" }}><TextInput value={item.detail} ariaLabel="Detail" onChange={(v) => set(at("detail"), v)} /></td>
                  <td style={{ ...cell, width: 54 }}><TextInput value={item.icon} ariaLabel="Icon" onChange={(v) => set(at("icon"), v)} /></td>
                  <td style={cell}><TextInput value={fromList(item.aliases)} ariaLabel="Aliases" onChange={(v) => set(at("aliases"), toList(v))} /></td>
                  <td style={{ ...cell, textAlign: "center", paddingTop: 10 }}>
                    <input type="checkbox" checked={!!item.hidden} aria-label="Hidden"
                      onChange={(e) => set(at("hidden"), e.target.checked || "")} style={{ accentColor: C.accent }} />
                  </td>
                  <td style={{ ...cell, whiteSpace: "nowrap" }}>
                    <button style={S.small} onClick={() => put(move(items, i, -1))} aria-label="Move up">↑</button>{" "}
                    <button style={S.small} onClick={() => put(move(items, i, 1))} aria-label="Move down">↓</button>{" "}
                    <button
                      style={{ ...S.small, ...(n > 0 && closed ? { opacity: 0.4, cursor: "not-allowed" } : {}) }}
                      disabled={n > 0 && closed}
                      title={n > 0 && closed ? "In use: tick Hidden instead" : "Remove"}
                      onClick={() => put(items.filter((_, j) => j !== i))} aria-label="Remove">✕</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <button style={{ ...S.small, marginTop: 8 }} onClick={() => put([...items, { value: "", label: "" }])}>+ Add value</button>
      <p style={S.help}>
        Value: stable id stored in profiles (lower-case, underscores). Aliases: lower-case stems that tag
        papers mentioning this value; empty means the value itself. Hidden values stay valid in saved
        profiles but are not offered.
      </p>
    </div>
  );
}

Vocabulary.propTypes = {
  facet: PropTypes.object.isRequired, path: PropTypes.array.isRequired,
  set: PropTypes.func.isRequired, used: PropTypes.object,
};

function FacetCard({ facet, index, set, remove, moveBy, locked, used }) {
  const [open, setOpen] = useState(index === 0);
  const [more, setMore] = useState(false);
  const path = ["facets", index];
  const f = (k) => [...path, k];
  const isLocked = locked.facets[facet.key] !== undefined;
  const inQueries = facet.role === "subject" || facet.role === "condition";
  return (
    <div style={{ ...S.card, padding: 0, marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 16px", cursor: "pointer" }} onClick={() => setOpen(!open)}>
        <span style={{ color: C.dim, width: 12 }}>{open ? "▾" : "▸"}</span>
        <span style={{ fontWeight: 600, color: C.text }}>{facet.label || "(untitled facet)"}</span>
        <code style={{ ...S.mono, color: C.dim }}>{facet.key}</code>
        <span style={S.badge(facet.role === "technique" ? C.warn : C.accent2)}>{facet.role}</span>
        <span style={{ fontSize: 14, color: C.dim }}>{(facet.vocabulary || []).length} values</span>
        {isLocked && <span style={S.badge(C.muted)} title="This pack has data: key and role are fixed">🔒 structure</span>}
        <span style={{ flex: 1 }} />
        <span onClick={(e) => e.stopPropagation()} style={{ display: "flex", gap: 6 }}>
          <button style={S.small} onClick={() => moveBy(-1)} aria-label="Move facet up">↑</button>
          <button style={S.small} onClick={() => moveBy(1)} aria-label="Move facet down">↓</button>
          <button style={{ ...S.small, ...(isLocked ? { opacity: 0.4, cursor: "not-allowed" } : {}) }} disabled={isLocked}
            title={isLocked ? "This pack has data: facets cannot be removed" : "Remove facet"} onClick={remove}>Remove</button>
        </span>
      </div>
      {open && (
        <div style={{ padding: "4px 16px 16px", borderTop: `1px solid ${C.border}` }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "0 14px", marginTop: 12 }}>
            <Field label="Key" help={isLocked ? "Fixed: saved profiles use it." : "Stable id stored in profiles and scores."}>
              <TextInput value={facet.key} mono disabled={isLocked} onChange={(v) => set(f("key"), v)} />
            </Field>
            <Field label="Label" help="Profile form title.">
              <TextInput value={facet.label} onChange={(v) => set(f("label"), v)} />
            </Field>
            <Field label="Short label" help="Score bars, filters, chat.">
              <TextInput value={facet.short_label} onChange={(v) => set(f("short_label"), v)} />
            </Field>
            <Field label="Role" help={isLocked ? "Fixed: scores depend on it." : undefined}>
              <Select value={facet.role} options={ROLES} disabled={isLocked} onChange={(v) => set(f("role"), v)} />
            </Field>
            <Field label="Widget">
              <Select value={facet.widget || "chips"} options={WIDGETS} onChange={(v) => set(f("widget"), v)} />
            </Field>
          </div>
          <Field label="Scoring description" help='Completes "Score … on …": what a good match means for this facet.'>
            <TextInput value={facet.description} onChange={(v) => set(f("description"), v)} />
          </Field>
          <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
            <Check label="Closed vocabulary" checked={facet.open_vocabulary === false}
              help="Researchers can only pick listed values."
              onChange={(v) => set(f("open_vocabulary"), v ? false : "")} />
            <Check label="Tag papers" checked={!!facet.annotate}
              help="Mark papers and proposals mentioning a value (filter bars)."
              onChange={(v) => set(f("annotate"), v || "")} />
            <Check label="Submit all values" checked={!!facet.select_all}
              help="Describes the facility, not the researcher."
              onChange={(v) => set(f("select_all"), v || "")} />
          </div>
          {inQueries && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: "0 14px" }}>
              <Field label="Default query term" help="Used when nothing is selected.">
                <TextInput value={facet.query_default} onChange={(v) => set(f("query_default"), v)} />
              </Field>
              <Field label="LLM synonyms" help="Added to each term (0 = none).">
                <NumberInput value={facet.synonyms} onChange={(v) => set(f("synonyms"), v)} />
              </Field>
              <Field label="Group size" help="Max terms per OR-group.">
                <NumberInput value={facet.group_size} min={1} onChange={(v) => set(f("group_size"), v)} />
              </Field>
            </div>
          )}
          {inQueries && (
            <Field label="Synonym guidance" help="Tells the LLM which synonyms to generate; examples help.">
              <TextArea value={facet.synonym_guidance} rows={3} onChange={(v) => set(f("synonym_guidance"), v)} />
            </Field>
          )}
          <Vocabulary facet={facet} path={path} set={set} used={used} />
          <button style={{ ...S.small, marginTop: 14 }} onClick={() => setMore(!more)}>{more ? "Hide" : "Show"} hints and sorting</button>
          {more && (
            <div style={{ marginTop: 12 }}>
              <Field label="Hint template" help="{term} and {selected} are filled in. Shown when a paper mentions a hint term outside the selection.">
                <TextInput value={getIn(facet, ["hints", "template"])} onChange={(v) => set([...f("hints"), "template"], v)} />
              </Field>
              <Field label="Hint terms (comma-separated)">
                <TextInput value={fromList(getIn(facet, ["hints", "terms"]))} onChange={(v) => set([...f("hints"), "terms"], toList(v))} />
              </Field>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: "0 14px" }}>
                <Field label="Sort label" help="Adds a results sort option.">
                  <TextInput value={getIn(facet, ["sort", "label"])} onChange={(v) => set([...f("sort"), "label"], v)} />
                </Field>
                <Field label="Sort tooltip">
                  <TextInput value={getIn(facet, ["sort", "title"])} onChange={(v) => set([...f("sort"), "title"], v)} />
                </Field>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

FacetCard.propTypes = {
  facet: PropTypes.object.isRequired, index: PropTypes.number.isRequired,
  set: PropTypes.func.isRequired, remove: PropTypes.func.isRequired, moveBy: PropTypes.func.isRequired,
  locked: PropTypes.object.isRequired, used: PropTypes.object,
};

function Facets({ data, set, locked }) {
  const facets = data.facets || [];
  return (
    <>
      <p style={S.sub}>
        Facets are the fields of a researcher profile. <b>Subject</b> and <b>condition</b> facets are
        combined into search queries; <b>technique</b> facets only weight the methodology score.
        At least one subject or condition facet is required.
      </p>
      {locked.has_data && (
        <Notice tone="warn">
          This pack already has data, so existing facet keys and roles are fixed, and values that saved
          profiles use cannot be renamed or removed from a closed vocabulary (tick <i>Hidden</i> instead).
          Labels, descriptions, new values and new facets are fine.
        </Notice>
      )}
      {facets.map((facet, i) => (
        <FacetCard
          key={i} facet={facet} index={i} set={set} locked={locked}
          used={locked.values?.[facet.key]}
          remove={() => set(["facets"], facets.filter((_, j) => j !== i))}
          moveBy={(d) => set(["facets"], move(facets, i, d))}
        />
      ))}
      <button style={S.btn} onClick={() => set(["facets"], [...facets, {
        key: "", label: "", role: "subject", widget: "chips", annotate: true, vocabulary: [],
      }])}>+ Add facet</button>
    </>
  );
}

Facets.propTypes = { data: PropTypes.object.isRequired, set: PropTypes.func.isRequired, locked: PropTypes.object.isRequired };

// ── Sources ──────────────────────────────────────────────────────────────────

function Sources({ data, set, backends, name }) {
  const sources = data.sources || [];
  const [tests, setTests] = useState(null);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState(null);
  const put = (list) => set(["sources"], list);

  async function runTests() {
    setTesting(true); setError(null); setTests(null);
    try {
      const res = await setupApi(`/packs/${name}/test-sources`, { method: "POST", body: { data } });
      setTests(Object.fromEntries(res.map((r) => [r.key, r])));
    } catch (e) { setError(e.message); }
    finally { setTesting(false); }
  }

  return (
    <>
      <p style={S.sub}>
        Where papers come from. <code style={S.mono}>europepmc</code> sources take a Europe PMC{" "}
        <b>filter</b> such as <code style={S.mono}>SRC:PPR</code> (preprints), <code style={S.mono}>SRC:MED</code>{" "}
        (PubMed) or <code style={S.mono}>JOURNAL:&quot;Nature Communications&quot;</code>.
      </p>
      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 14 }}>
        <button style={S.btn} onClick={runTests} disabled={testing}>{testing ? "Searching…" : "Test sources"}</button>
        <span style={{ fontSize: 14, color: C.dim }}>Runs one small search per source with the pack&apos;s fallback query.</span>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {sources.map((src, i) => {
        const at = (k) => ["sources", i, k];
        const t = tests?.[src.key];
        return (
          <div key={i} style={{ ...S.card, marginBottom: 12, padding: 14 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1.4fr 1fr 2.2fr", gap: "0 12px" }}>
              <Field label="Key"><TextInput value={src.key} mono onChange={(v) => set(at("key"), v)} /></Field>
              <Field label="Label"><TextInput value={src.label} onChange={(v) => set(at("label"), v)} /></Field>
              <Field label="Backend">
                <Select value={src.backend} options={[...new Set([...backends, src.backend].filter(Boolean))]} onChange={(v) => set(at("backend"), v)} />
              </Field>
              <Field label="Filter">
                <TextInput value={src.filter} mono placeholder={src.backend === "europepmc" ? 'JOURNAL:"…"' : "(not used)"} onChange={(v) => set(at("filter"), v)} />
              </Field>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 3fr", gap: "0 12px", alignItems: "end" }}>
              <Field label="Access"><Select value={src.access || "open"} options={["open", "paywall"]} onChange={(v) => set(at("access"), v)} /></Field>
              <Field label="Impact"><Select value={src.impact || "low"} options={["high", "mid", "low"]} onChange={(v) => set(at("impact"), v)} /></Field>
              <Field><Check label="Preprints" checked={!!src.preprint} onChange={(v) => set(at("preprint"), v || "")} /></Field>
              <Field label="Description"><TextInput value={src.description} onChange={(v) => set(at("description"), v)} /></Field>
            </div>
            <Field label="Synonym override (comma-separated)" help="Optional: terms used instead of LLM synonyms for this source's non-subject groups — for sources whose coverage differs from the rest.">
              <TextInput value={fromList(src.synonym_override)} onChange={(v) => set(at("synonym_override"), toList(v))} />
            </Field>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              {t && (
                <span style={{ fontSize: 14, color: t.ok ? C.accent : C.warn, flex: 1 }}>
                  {t.ok ? `✓ ${t.count} result(s) for “${t.query}” — e.g. ${t.titles[0]}` : `⚠ ${t.error}`}
                </span>
              )}
              <span style={{ flex: t ? 0 : 1 }} />
              <button style={S.small} onClick={() => put(move(sources, i, -1))} aria-label="Move up">↑</button>
              <button style={S.small} onClick={() => put(move(sources, i, 1))} aria-label="Move down">↓</button>
              <button style={S.small} onClick={() => put(sources.filter((_, j) => j !== i))}>Remove</button>
            </div>
          </div>
        );
      })}
      <button style={S.btn} onClick={() => put([...sources, { key: "", label: "", backend: "europepmc", access: "open", impact: "low" }])}>+ Add source</button>
    </>
  );
}

Sources.propTypes = {
  data: PropTypes.object.isRequired, set: PropTypes.func.isRequired,
  backends: PropTypes.array.isRequired, name: PropTypes.string.isRequired,
};

// ── Credibility ──────────────────────────────────────────────────────────────

function JournalList({ value, onChange }) {
  return (
    <TextArea rows={8} value={(value || []).join("\n")}
      onChange={(v) => onChange(v.split("\n").map((s) => s.trim()).filter(Boolean))} />
  );
}

JournalList.propTypes = { value: PropTypes.array, onChange: PropTypes.func.isRequired };

function Credibility({ data, set }) {
  return (
    <>
      <p style={S.sub}>Journal names (one per line, case-insensitive) that raise a paper&apos;s credibility level.</p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <Field label="High-impact journals">
          <JournalList value={getIn(data, ["credibility", "high_impact_journals"])} onChange={(v) => set(["credibility", "high_impact_journals"], v)} />
        </Field>
        <Field label="Mid-impact journals">
          <JournalList value={getIn(data, ["credibility", "mid_impact_journals"])} onChange={(v) => set(["credibility", "mid_impact_journals"], v)} />
        </Field>
      </div>
    </>
  );
}

Credibility.propTypes = { data: PropTypes.object.isRequired, set: PropTypes.func.isRequired };

// ── Prompts ──────────────────────────────────────────────────────────────────

const PROMPT_FIELDS = [
  ["field", "Field", "Names the discipline in every prompt (\"You are a … research strategist\").", 1],
  ["proposal_noun", "Proposal noun", "What a proposal is called: experiment, study, simulation…", 1],
  ["theme_example", "Theme example", "An example proposal theme.", 1],
  ["citation_example", "Citation example", "A sample sentence citing papers as [P_xxxx], showing the synthesis style.", 3],
  ["contradiction_hint", "Contradiction hint", "Typical explanation for conflicting findings.", 1],
  ["critique_subject", "Critique subject", "What the critic reviews (\"a … experiment\").", 1],
  ["synonym_rules", "Synonym rules", "Extra rules for LLM synonym generation.", 2],
  ["scoring_guidance", "Scoring guidance", "Extra rules for LLM paper scoring.", 3],
  ["fallback_query", "Fallback query", "Search used when a profile selects nothing; also used by “Test sources”.", 1],
];

function Prompts({ data, set }) {
  return (
    <>
      <p style={S.sub}>Wording that adapts the engine&apos;s LLM prompts to this community. Empty fields use neutral defaults.</p>
      {PROMPT_FIELDS.map(([k, label, help, rows]) => (
        <Field key={k} label={label} help={help}>
          {rows > 1
            ? <TextArea rows={rows} value={getIn(data, ["prompts", k])} onChange={(v) => set(["prompts", k], v)} />
            : <TextInput value={getIn(data, ["prompts", k])} onChange={(v) => set(["prompts", k], v)} />}
        </Field>
      ))}
    </>
  );
}

Prompts.propTypes = { data: PropTypes.object.isRequired, set: PropTypes.func.isRequired };

// ── Dashboard text ───────────────────────────────────────────────────────────

const UI_FIELDS = [
  ["app_name", "App name", 1],
  ["document_title", "Browser tab title", 1],
  ["subtitle", "Subtitle", 1],
  ["research_placeholder", "Research description placeholder", 3],
  ["anchor_placeholder", "Anchor paper placeholder", 1],
  ["keyword_examples", "Keyword examples", 1],
];

function DashboardText({ data, set }) {
  return (
    <>
      <p style={S.sub}>Strings the dashboard shows for this pack.</p>
      {UI_FIELDS.map(([k, label, rows]) => (
        <Field key={k} label={label}>
          {rows > 1
            ? <TextArea rows={rows} value={getIn(data, ["ui", k])} onChange={(v) => set(["ui", k], v)} />
            : <TextInput value={getIn(data, ["ui", k])} onChange={(v) => set(["ui", k], v)} />}
        </Field>
      ))}
      <Field label="Relevance priority description">
        <TextInput value={getIn(data, ["ui", "priority_descriptions", "relevance"])} onChange={(v) => set(["ui", "priority_descriptions", "relevance"], v)} />
      </Field>
      <Field label="Methodology priority description">
        <TextInput value={getIn(data, ["ui", "priority_descriptions", "methodology"])} onChange={(v) => set(["ui", "priority_descriptions", "methodology"], v)} />
      </Field>
    </>
  );
}

DashboardText.propTypes = { data: PropTypes.object.isRequired, set: PropTypes.func.isRequired };

// ── Advanced ─────────────────────────────────────────────────────────────────

function RowList({ title, help, items, columns, onChange, blank }) {
  return (
    <div style={{ marginBottom: 22 }}>
      <h3 style={S.h3}>{title}</h3>
      <p style={S.sub}>{help}</p>
      {(items || []).map((item, i) => (
        <div key={i} style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center" }}>
          {columns.map(([k, label, flex]) => (
            <div key={k} style={{ flex }}>
              <TextInput value={item[k]} placeholder={label} ariaLabel={label} mono={k === "key" || k === "env"}
                onChange={(v) => onChange(setIn(items, [i, k], v))} />
            </div>
          ))}
          <button style={S.small} onClick={() => onChange(items.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <button style={S.small} onClick={() => onChange([...(items || []), blank])}>+ Add</button>
    </div>
  );
}

RowList.propTypes = {
  title: PropTypes.string.isRequired, help: PropTypes.node, items: PropTypes.array,
  columns: PropTypes.array.isRequired, onChange: PropTypes.func.isRequired, blank: PropTypes.object.isRequired,
};

function Advanced({ data, set }) {
  return (
    <>
      <RowList
        title="Deployment context"
        help="Facts about the facility, read from environment variables in .env (comma-separated), e.g. the tools or datasets on site. They are added to prompts."
        items={data.context} onChange={(v) => set(["context"], v)}
        columns={[["key", "key", 1], ["label", "Label", 2], ["env", "ENV_VARIABLE", 1.5], ["default", "Default when unset", 2]]}
        blank={{ key: "", label: "", env: "", default: "" }}
      />
      <RowList
        title="Extra critique dimensions"
        help="Lists the critic reports alongside novelty, confounds and evidence."
        items={getIn(data, ["critique", "dimensions"])} onChange={(v) => set(["critique", "dimensions"], v)}
        columns={[["key", "key", 1], ["label", "Label", 1.5], ["description", "What the critic should list", 3]]}
        blank={{ key: "", label: "", description: "" }}
      />
      {data.legacy && (
        <Notice>This pack has a <code style={S.mono}>legacy</code> section (field renames applied once to old databases). The wizard keeps it unchanged.</Notice>
      )}
    </>
  );
}

Advanced.propTypes = { data: PropTypes.object.isRequired, set: PropTypes.func.isRequired };

// ── Editor ───────────────────────────────────────────────────────────────────

export default function PackEditor({ section, data, onChange, locked, summary, backends }) {
  const set = (path, value) => onChange(setIn(data, path, value));
  const props = { data, set };
  switch (section) {
    case "general": return <General {...props} summary={summary} />;
    case "facets": return <Facets {...props} locked={locked} />;
    case "sources": return <Sources {...props} backends={backends} name={data.name} />;
    case "credibility": return <Credibility {...props} />;
    case "prompts": return <Prompts {...props} />;
    case "ui": return <DashboardText {...props} />;
    case "advanced": return <Advanced {...props} />;
    default: return null;
  }
}

PackEditor.propTypes = {
  section: PropTypes.string.isRequired,
  data: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  locked: PropTypes.object.isRequired,
  summary: PropTypes.object,
  backends: PropTypes.array.isRequired,
};
