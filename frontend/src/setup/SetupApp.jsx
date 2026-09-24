// Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
// SPDX-License-Identifier: Apache-2.0

// Setup wizard: choose the domain pack this deployment serves, create a new
// one, or edit an existing one — before launch, without login.

import { useCallback, useEffect, useState } from "react";
import PropTypes from "prop-types";
import PackEditor, { SECTIONS } from "./PackEditor.jsx";
import { C, S, Check, Field, Notice, Select, TextInput, setupApi, slugify } from "./ui.jsx";
import logo from "../assets/logo.png";

// ── Pack list ────────────────────────────────────────────────────────────────

function PackCard({ pack, onEdit, onSelect, busy }) {
  const data = pack.papers || pack.profiles;
  return (
    <div style={{ ...S.card, display: "flex", flexDirection: "column", gap: 10, borderColor: pack.selected ? `${C.accent}88` : C.border }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 18, fontWeight: 600, color: C.text }}>{pack.title}</span>
        {pack.selected && <span style={S.badge(C.accent)}>selected</span>}
      </div>
      <code style={{ ...S.mono, color: C.dim }}>domains/{pack.name}</code>
      {pack.error
        ? <Notice tone="error">{pack.error}</Notice>
        : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {pack.facets.map((f) => (
              <span key={f.key} style={{ fontSize: 14, color: C.muted, border: `1px solid ${C.border2}`, borderRadius: 6, padding: "2px 8px" }}>
                {f.label} <span style={{ color: C.dim }}>· {f.values}</span>
              </span>
            ))}
            <span style={{ fontSize: 14, color: C.muted, border: `1px solid ${C.border2}`, borderRadius: 6, padding: "2px 8px" }}>
              {pack.sources} sources
            </span>
          </div>
        )}
      <div style={{ fontSize: 14, color: C.dim, lineHeight: 1.5 }}>
        {data ? `${pack.papers} papers · ${pack.profiles} profile(s) collected` : "No data yet"}
        {(pack.has_hooks || pack.has_ui) && (
          <> · custom code: {[pack.has_hooks && "hooks.py", pack.has_ui && "ui/"].filter(Boolean).join(", ")}</>
        )}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: "auto" }}>
        <button style={S.btn} onClick={onEdit} disabled={!!pack.error}>Edit</button>
        {!pack.selected && (
          <button style={S.primary} onClick={onSelect} disabled={busy}>Use this pack</button>
        )}
      </div>
    </div>
  );
}

PackCard.propTypes = {
  pack: PropTypes.object.isRequired, onEdit: PropTypes.func.isRequired,
  onSelect: PropTypes.func.isRequired, busy: PropTypes.bool,
};

function NewPackForm({ packs, onCreated }) {
  const [title, setTitle] = useState("");
  const [name, setName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [template, setTemplate] = useState("");
  const [error, setError] = useState(null);

  async function create() {
    setError(null);
    try {
      const res = await setupApi("/new", { method: "POST", body: { name, title, template: template || null } });
      onCreated(name, res.data);
    } catch (e) { setError(e.message); }
  }

  return (
    <div style={{ ...S.card, borderStyle: "dashed" }}>
      <h3 style={S.h3}>New pack</h3>
      <Field label="Title">
        <TextInput value={title} placeholder="e.g. Soil microbiome (EMSL)"
          onChange={(v) => { setTitle(v); if (!nameEdited) setName(slugify(v)); }} />
      </Field>
      <Field label="Name" help="Directory under domains/ — lower-case letters, digits, underscores.">
        <TextInput value={name} mono onChange={(v) => { setName(v); setNameEdited(true); }} />
      </Field>
      <Field label="Start from" help="Copying an existing pack gives you working facets, sources and prompts to adapt. Its hooks.py and ui/ are not copied.">
        <Select value={template} onChange={setTemplate}
          options={[["", "Blank pack"], ...packs.map((p) => [p.name, `Copy of ${p.title}`])]} />
      </Field>
      {error && <Notice tone="error">{error}</Notice>}
      <button style={S.primary} onClick={create} disabled={!name}>Create draft</button>
    </div>
  );
}

NewPackForm.propTypes = { packs: PropTypes.array.isRequired, onCreated: PropTypes.func.isRequired };

function NextSteps({ name }) {
  return (
    <Notice tone="ok">
      <b>{name}</b> is selected (<code style={S.mono}>DOMAIN_PACK={name}</code> in <code style={S.mono}>.env</code>).
      Stop this wizard with <b>Ctrl-C</b>, then start Cassiopeia with <code style={S.mono}>./launch.sh</code>,
      or for Docker, <code style={S.mono}>docker compose build &amp;&amp; docker compose up -d</code>.
      Each pack keeps its own data in <code style={S.mono}>data/{name}/</code>.
    </Notice>
  );
}

NextSteps.propTypes = { name: PropTypes.string.isRequired };

function Home({ state, reload, openPack, openDraft }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [justSelected, setJustSelected] = useState(null);

  async function select(name) {
    setBusy(true); setError(null);
    try {
      await setupApi("/select", { method: "POST", body: { name } });
      setJustSelected(name);
      await reload();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "28px 24px" }}>
      <h2 style={S.h2}>Domain pack</h2>
      <p style={S.sub}>
        A deployment serves one science community, described by a <i>domain pack</i>: profile facets,
        literature sources, prompt wording and dashboard text. Choose the pack this deployment uses,
        edit one, or create a new one.
      </p>
      {!state.selected && <Notice tone="warn">No pack is selected yet: several are installed, so choose one.</Notice>}
      {justSelected && <NextSteps name={justSelected} />}
      {error && <Notice tone="error">{error}</Notice>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 16 }}>
        {state.packs.map((p) => (
          <PackCard key={p.name} pack={p} busy={busy} onEdit={() => openPack(p.name)} onSelect={() => select(p.name)} />
        ))}
        <NewPackForm packs={state.packs} onCreated={openDraft} />
      </div>
    </div>
  );
}

Home.propTypes = {
  state: PropTypes.object.isRequired, reload: PropTypes.func.isRequired,
  openPack: PropTypes.func.isRequired, openDraft: PropTypes.func.isRequired,
};

// ── Review, save, commit ─────────────────────────────────────────────────────

function Diff({ text }) {
  if (!text) return <p style={{ ...S.sub, margin: 0 }}>No changes.</p>;
  return (
    <pre style={{ background: C.inset, border: `1px solid ${C.border}`, borderRadius: 8, padding: 12, overflow: "auto", maxHeight: 420, margin: 0, fontSize: 14, fontFamily: C.mono, lineHeight: 1.45 }}>
      {text.split("\n").map((line, i) => {
        const color = line.startsWith("+++") || line.startsWith("---") ? C.dim
          : line.startsWith("+") ? C.accent
            : line.startsWith("-") ? C.danger
              : line.startsWith("@@") ? C.accent2 : C.muted;
        return <div key={i} style={{ color, whiteSpace: "pre" }}>{line || " "}</div>;
      })}
    </pre>
  );
}

Diff.propTypes = { text: PropTypes.string };

function GitPanel({ name, git, isNew, onCommitted }) {
  const [message, setMessage] = useState(`domain pack ${name}: ${isNew ? "add" : "update"} via setup wizard`);
  const [newBranch, setNewBranch] = useState(git.on_default);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  if (!git.available) return <Notice tone="warn">Git is not available here ({git.error}); commit domains/{name}/ yourself.</Notice>;

  async function commit() {
    setBusy(true); setError(null);
    try {
      const res = await setupApi(`/packs/${name}/commit`, {
        method: "POST", body: { message, branch: newBranch ? git.suggested_branch : null },
      });
      setResult(res);
      onCommitted(res.git);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  if (result) {
    return (
      <Notice tone="ok">
        Committed <code style={S.mono}>{result.commit}</code> on branch <code style={S.mono}>{result.branch}</code>.
        Push it and open a merge request to share the pack.
      </Notice>
    );
  }
  if (!git.dirty) {
    return <p style={{ ...S.sub, margin: 0 }}>domains/{name}/ has no uncommitted changes (branch <code style={S.mono}>{git.branch}</code>).</p>;
  }
  return (
    <div>
      <p style={S.sub}>
        Commits only <code style={S.mono}>domains/{name}/</code>; anything else you have staged or
        modified is left alone. Current branch: <code style={S.mono}>{git.branch}</code>.
      </p>
      <Field label="Commit message"><TextInput value={message} onChange={setMessage} /></Field>
      {git.branch !== git.suggested_branch && (
        <Check
          label={<>Create and switch to branch <code style={S.mono}>{git.suggested_branch}</code> first</>}
          help="Your uncommitted work moves along with you; nothing is lost."
          checked={newBranch} onChange={setNewBranch}
        />
      )}
      {error && <Notice tone="error">{error}</Notice>}
      <button style={S.btn} onClick={commit} disabled={busy || !message.trim()}>{busy ? "Committing…" : "Commit"}</button>
    </div>
  );
}

GitPanel.propTypes = {
  name: PropTypes.string.isRequired, git: PropTypes.object.isRequired,
  isNew: PropTypes.bool, onCommitted: PropTypes.func.isRequired,
};

function Review({ name, data, isNew, onSaved, selected }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(null);   // { path, git }
  const [selectedNow, setSelectedNow] = useState(false);

  useEffect(() => {
    let live = true;
    setReport(null); setError(null);
    setupApi(`/packs/${name}/check`, { method: "POST", body: { data, new: isNew } })
      .then((r) => { if (live) setReport(r); })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [name, data, isNew]);

  async function save() {
    setSaving(true); setError(null);
    try {
      const res = await setupApi(`/packs/${name}`, { method: "PUT", body: { data, new: isNew } });
      setSaved(res);
      onSaved();
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  }

  async function select() {
    try {
      await setupApi("/select", { method: "POST", body: { name } });
      setSelectedNow(true);
    } catch (e) { setError(e.message); }
  }

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!report) return <p style={S.sub}>Checking…</p>;

  if (saved) {
    return (
      <>
        <Notice tone="ok">Saved <code style={S.mono}>{saved.path}</code>.</Notice>
        <div style={{ ...S.card, marginBottom: 16 }}>
          <h3 style={S.h3}>Commit to git</h3>
          <GitPanel name={name} git={saved.git} isNew={isNew} onCommitted={() => {}} />
        </div>
        <div style={S.card}>
          <h3 style={S.h3}>Use for this deployment</h3>
          {selected === name || selectedNow
            ? <NextSteps name={name} />
            : (
              <>
                <p style={S.sub}>Currently selected: <code style={S.mono}>{selected || "none"}</code>.</p>
                <button style={S.primary} onClick={select}>Use {name}</button>
              </>
            )}
          {!isNew && selected === name && (
            <p style={{ ...S.help, marginTop: 8 }}>A running Cassiopeia picks up the change after a restart.</p>
          )}
        </div>
      </>
    );
  }

  return (
    <>
      {report.errors.map((e) => <Notice key={e} tone="error">{e}</Notice>)}
      {report.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
      {!report.errors.length && (
        <>
          <p style={S.sub}>Changes to <code style={S.mono}>domains/{name}/domain.yaml</code>:</p>
          <Diff text={report.diff} />
          <div style={{ marginTop: 16, display: "flex", gap: 10 }}>
            <button style={S.primary} onClick={save} disabled={saving || (!isNew && !report.diff)}>
              {saving ? "Saving…" : isNew ? "Create pack" : "Save"}
            </button>
          </div>
        </>
      )}
    </>
  );
}

Review.propTypes = {
  name: PropTypes.string.isRequired, data: PropTypes.object.isRequired, isNew: PropTypes.bool,
  onSaved: PropTypes.func.isRequired, selected: PropTypes.string,
};

// ── Editor page ──────────────────────────────────────────────────────────────

function EditorPage({ draft, setDraft, state, onBack, onSaved }) {
  const [section, setSection] = useState("general");
  const { name, data, isNew, locked, summary, dirty } = draft;
  const nav = [...SECTIONS, ["review", isNew ? "Review & create" : "Review & save"]];

  function back() {
    if (dirty && !window.confirm("Discard unsaved changes?")) return;
    onBack();
  }

  return (
    <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
      <nav style={{ width: 240, flexShrink: 0, borderRight: `1px solid ${C.border}`, padding: "20px 12px", background: C.bar }}>
        <button style={{ ...S.btn, width: "100%", marginBottom: 18 }} onClick={back}>← All packs</button>
        <div style={{ fontSize: 17, fontWeight: 600, color: C.text, padding: "0 6px" }}>{data.title || name}</div>
        <code style={{ ...S.mono, color: C.dim, padding: "0 6px" }}>{name}</code>
        <div style={{ margin: "8px 6px 16px", display: "flex", gap: 6, flexWrap: "wrap" }}>
          {isNew && <span style={S.badge(C.accent2)}>new</span>}
          {dirty && <span style={S.badge(C.warn)}>unsaved</span>}
          {locked.has_data && <span style={S.badge(C.muted)} title="Facet keys and roles are fixed">has data</span>}
        </div>
        {nav.map(([key, label]) => (
          <button key={key} onClick={() => setSection(key)} style={{
            display: "block", width: "100%", textAlign: "left", background: section === key ? "#1e293b" : "none",
            border: "none", color: section === key ? C.text : C.muted, borderRadius: 6, padding: "7px 10px",
            fontSize: 15, cursor: "pointer", marginBottom: 2, fontWeight: key === "review" ? 600 : 400,
          }}>{label}</button>
        ))}
      </nav>
      <main style={{ flex: 1, overflowY: "auto", padding: "24px 28px" }}>
        <div style={{ maxWidth: 1000 }}>
          <h2 style={{ ...S.h2, marginBottom: 16 }}>{nav.find(([k]) => k === section)[1]}</h2>
          {section === "review"
            ? <Review name={name} data={data} isNew={isNew} selected={state.selected} onSaved={onSaved} />
            : (
              <PackEditor section={section} data={data} locked={locked} summary={summary}
                backends={state.backends} onChange={(d) => setDraft({ ...draft, data: d, dirty: true })} />
            )}
        </div>
      </main>
    </div>
  );
}

EditorPage.propTypes = {
  draft: PropTypes.object.isRequired, setDraft: PropTypes.func.isRequired, state: PropTypes.object.isRequired,
  onBack: PropTypes.func.isRequired, onSaved: PropTypes.func.isRequired,
};

// ── App ──────────────────────────────────────────────────────────────────────

const NO_LOCKS = { has_data: false, facets: {}, values: {} };

export default function SetupApp() {
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const [draft, setDraft] = useState(null);   // { name, data, isNew, locked, summary, dirty }

  const reload = useCallback(async () => {
    try { setState(await setupApi("/state")); setError(null); }
    catch (e) { setError(e.message); }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  async function openPack(name) {
    try {
      const res = await setupApi(`/packs/${name}`);
      setDraft({ name, data: res.data, isNew: false, locked: res.locked, summary: res.summary, dirty: false });
    } catch (e) { setError(e.message); }
  }

  function openDraft(name, data) {
    setDraft({ name, data, isNew: true, locked: NO_LOCKS, summary: null, dirty: true });
  }

  async function onSaved() {
    await reload();
    setDraft((d) => ({ ...d, isNew: false, dirty: false }));
  }

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: C.bg, color: C.text, fontFamily: C.sans }}>
      <header style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 20px", background: C.bar, borderBottom: `1px solid ${C.border}`, flexShrink: 0 }}>
        <img src={logo} alt="" style={{ height: 28 }} />
        <span style={{ fontWeight: 700, letterSpacing: 1.5, fontSize: 16 }}>CASSIOPEIA</span>
        <span style={{ color: C.dim, fontSize: 15 }}>Setup</span>
        <span style={{ flex: 1 }} />
        <span style={{ color: C.dim, fontSize: 14 }}>Runs before launch · this machine only</span>
      </header>
      {error && <div style={{ padding: "16px 24px 0" }}><Notice tone="error">{error}</Notice></div>}
      {!state && !error && <p style={{ ...S.sub, padding: 24 }}>Loading…</p>}
      {state && !draft && <div style={{ flex: 1, overflowY: "auto" }}><Home state={state} reload={reload} openPack={openPack} openDraft={openDraft} /></div>}
      {state && draft && (
        <EditorPage draft={draft} setDraft={setDraft} state={state}
          onBack={() => { setDraft(null); reload(); }} onSaved={onSaved} />
      )}
    </div>
  );
}
