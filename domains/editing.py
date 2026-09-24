# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Create, edit, select and commit domain packs — the setup wizard's backend.

Editing rules
-------------
A pack whose database already holds data keeps its *structure*: facet keys and
roles are fixed, and a closed-vocabulary value that saved profiles use cannot
be removed (hide it with ``hidden: true`` instead).  Everything else — labels,
prompts, sources, new facets, new vocabulary values — can change at any time.
The same rule is enforced at startup by ``PaperStore._check_domain``.

Saving an existing pack edits its ``domain.yaml`` in place: comments, block
text and the layout of unchanged entries are kept, so the git diff shows only
what changed.
"""

from __future__ import annotations

import difflib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import FoldedScalarString, LiteralScalarString, ScalarString

from domains import PACKS_DIR, DomainPackError, installed_packs, pack_from_dict, selected_pack_name

PACK_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

PROJECT_ROOT = PACKS_DIR.parent
ENV_FILE = PROJECT_ROOT / ".env"

class PackEditError(DomainPackError):
    """A pack cannot be saved, selected or committed as requested."""


# ─────────────────────────────────────────────────────────────────────────────
# Reading
# ─────────────────────────────────────────────────────────────────────────────

def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096
    return y


def to_plain(node: Any) -> Any:
    """ruamel round-trip objects → plain JSON-compatible Python values."""
    if isinstance(node, dict):
        return {str(k): to_plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [to_plain(v) for v in node]
    if isinstance(node, ScalarString):
        return str(node)
    if isinstance(node, bool) or node is None:
        return node
    if isinstance(node, int):
        return int(node)
    if isinstance(node, float):
        return float(node)
    return node


def pack_dir(name: str) -> Path:
    if not PACK_NAME_RE.match(name or ""):
        raise PackEditError(
            f"Invalid pack name {name!r}: use lower-case letters, digits and underscores"
        )
    return PACKS_DIR / name


def pack_file(name: str) -> Path:
    return pack_dir(name) / "domain.yaml"


def read_pack(name: str) -> dict:
    path = pack_file(name)
    if not path.is_file():
        raise PackEditError(f"No domain pack {name!r}")
    return to_plain(_yaml().load(path.read_text(encoding="utf-8"))) or {}


# ─────────────────────────────────────────────────────────────────────────────
# Data already collected with a pack
# ─────────────────────────────────────────────────────────────────────────────

def pack_database(name: str) -> Path | None:
    """The database *name* would use by default, if one exists (never moves it)."""
    from utils.data_paths import DB_NAME, PROJECT_ROOT as DATA_PROJECT_ROOT, pack_data_dir, stamped_pack

    db = pack_data_dir(name, migrate=False) / DB_NAME
    if db.is_file():
        return db
    legacy = DATA_PROJECT_ROOT / DB_NAME
    if legacy.is_file() and (stamped_pack(legacy) or selected_pack_name()) == name:
        return legacy
    return None


def pack_usage(name: str) -> dict[str, Any]:
    """How much data a pack has, and how many profiles use each facet value."""
    usage: dict[str, Any] = {"db_path": None, "profiles": 0, "papers": 0, "values": {}}
    db = pack_database(name)
    if db is None:
        return usage
    usage["db_path"] = str(db)
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            usage["papers"] = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
            rows = conn.execute("SELECT data FROM profiles").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return usage
    usage["profiles"] = len(rows)
    values: dict[str, dict[str, int]] = {}
    for (raw,) in rows:
        try:
            facets = json.loads(raw).get("facets") or {}
        except (ValueError, AttributeError):
            continue
        for key, selected in facets.items():
            for v in selected or ():
                values.setdefault(key, {}).setdefault(v, 0)
                values[key][v] += 1
    usage["values"] = values
    return usage


def has_data(usage: dict[str, Any]) -> bool:
    return bool(usage["profiles"] or usage["papers"])


# ─────────────────────────────────────────────────────────────────────────────
# Listing and templates
# ─────────────────────────────────────────────────────────────────────────────

def pack_summary(name: str) -> dict[str, Any]:
    root = pack_dir(name)
    try:
        raw = read_pack(name)
        error = None
    except Exception as exc:  # a broken pack must still be listed
        raw, error = {}, str(exc)
    usage = pack_usage(name)
    return {
        "name": name,
        "title": raw.get("title", name),
        "facets": [
            {"key": f.get("key"), "label": f.get("label"), "role": f.get("role"),
             "values": len(f.get("vocabulary") or [])}
            for f in raw.get("facets") or [] if isinstance(f, dict)
        ],
        "sources": len(raw.get("sources") or []),
        "has_hooks": (root / "hooks.py").is_file(),
        "has_ui": (root / "ui" / "index.jsx").is_file(),
        "selected": selected_pack_name() == name,
        "profiles": usage["profiles"],
        "papers": usage["papers"],
        "error": error,
    }


def list_packs() -> list[dict[str, Any]]:
    return [pack_summary(n) for n in installed_packs()]


def blank_pack(name: str, title: str) -> dict:
    """A minimal valid pack to start from."""
    return {
        "name": name,
        "title": title or name,
        "facets": [
            {"key": "topic", "label": "Research Topics", "short_label": "Topic",
             "role": "subject", "widget": "chips", "annotate": True,
             "query_default": "research", "synonyms": 2, "group_size": 3,
             "description": "how well the paper's topics match the researcher's topics",
             "vocabulary": []},
        ],
        "sources": [
            {"key": "preprints", "label": "Preprints", "backend": "europepmc",
             "filter": "SRC:PPR", "access": "open", "impact": "low", "preprint": True,
             "description": "Preprint servers via Europe PMC"},
            {"key": "pubmed", "label": "PubMed", "backend": "europepmc",
             "filter": "SRC:MED", "access": "paywall", "impact": "low",
             "description": "Abstracts & citation data"},
        ],
        "credibility": {"high_impact_journals": ["nature", "science"], "mid_impact_journals": []},
        "prompts": {"field": "science", "proposal_noun": "study"},
        "ui": {"app_name": "CASSIOPEIA", "document_title": title or name},
    }


def new_pack_data(name: str, title: str, template: str | None) -> dict:
    """Starting content for a new pack: a copy of *template*, or a blank pack."""
    if not template:
        return blank_pack(name, title)
    data = read_pack(template)
    data.pop("legacy", None)  # migrations of the template's old databases
    data["name"] = name
    data["title"] = title or name
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def prune(node: Any) -> Any:
    """Drop empty optional fields (None, "", [], {}) the form leaves behind."""
    if isinstance(node, dict):
        out = {k: prune(v) for k, v in node.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(node, list):
        return [prune(v) for v in node]
    return node


def _vocab(facet: dict) -> set[str]:
    return {v.get("value") for v in facet.get("vocabulary") or [] if isinstance(v, dict)}


def check_pack(name: str, data: dict, *, new: bool) -> dict[str, list[str]]:
    """Errors (block saving) and warnings for a draft of pack *name*."""
    from utils.source_fetchers import BACKENDS

    errors: list[str] = []
    warnings: list[str] = []
    data = prune(data)
    try:
        root = pack_dir(name)
    except PackEditError as exc:
        return {"errors": [str(exc)], "warnings": []}
    if data.get("name") != name:
        errors.append(f"The pack's name field must be {name!r}")
    if new and root.exists():
        errors.append(f"A pack named {name!r} already exists")
    if not new and not (root / "domain.yaml").is_file():
        errors.append(f"No domain pack {name!r}")

    try:
        pack_from_dict(data, root, run_hooks=False)
    except DomainPackError as exc:
        errors.append(str(exc))

    has_hooks = (root / "hooks.py").is_file()
    for src in data.get("sources") or []:
        backend = src.get("backend") if isinstance(src, dict) else None
        if backend and backend not in BACKENDS:
            msg = f"Source {src.get('key')!r} uses unknown backend {backend!r}"
            if has_hooks:
                warnings.append(msg + " (fine if hooks.py registers it)")
            else:
                errors.append(msg + f"; available: {', '.join(sorted(BACKENDS))}")

    if not new and not errors:
        _check_locked(name, data, errors, warnings)
    return {"errors": errors, "warnings": warnings}


def locked_fields(name: str) -> dict[str, Any]:
    """What the wizard must not let the user change for an existing pack."""
    usage = pack_usage(name)
    if not has_data(usage):
        return {"has_data": False, "facets": {}, "values": {}}
    old = read_pack(name)
    return {
        "has_data": True,
        "facets": {f["key"]: f["role"] for f in old.get("facets") or []},
        "values": usage["values"],  # facet → value → number of profiles using it
    }


def _check_locked(name: str, data: dict, errors: list[str], warnings: list[str]) -> None:
    usage = pack_usage(name)
    if not has_data(usage):
        return
    old = read_pack(name)
    new_facets = {f.get("key"): f for f in data.get("facets") or []}
    for f in old.get("facets") or []:
        key = f["key"]
        nf = new_facets.get(key)
        if nf is None:
            errors.append(f"Facet {key!r} cannot be removed or renamed: this pack already has data")
            continue
        if nf.get("role") != f.get("role"):
            errors.append(f"Facet {key!r} must keep role {f.get('role')!r}: this pack already has data")
        used = usage["values"].get(key, {})
        closed = nf.get("open_vocabulary", True) is False
        if closed:
            for value in sorted(_vocab(f) - _vocab(nf)):
                if used.get(value):
                    errors.append(
                        f"{key}: value {value!r} is used by {used[value]} profile(s); "
                        "hide it (hidden: true) instead of removing it"
                    )
            outside = sorted(v for v in used if v not in _vocab(nf))
            if outside and f.get("open_vocabulary", True) is not False:
                warnings.append(
                    f"{key}: closing the vocabulary drops values saved profiles use: "
                    + ", ".join(outside)
                )
    removed = {s.get("key") for s in old.get("sources") or []} - {
        s.get("key") for s in data.get("sources") or []
    }
    if removed:
        warnings.append(
            "Removed source(s) " + ", ".join(sorted(removed))
            + ": papers already fetched from them stay in the corpus"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Rendering domain.yaml
# ─────────────────────────────────────────────────────────────────────────────

def _fresh(value: Any, *, flow: bool = False) -> Any:
    """Plain value → ruamel node (flow style for one-line entries)."""
    if isinstance(value, dict):
        node = CommentedMap((k, _fresh(v)) for k, v in value.items())
        if flow:
            node.fa.set_flow_style()
        return node
    if isinstance(value, list):
        return CommentedSeq(_fresh(v) for v in value)
    if isinstance(value, str) and "\n" in value:
        return LiteralScalarString(value if value.endswith("\n") else value + "\n")
    return value


def _merge_scalar(old: Any, new: Any) -> Any:
    if type(old) is type(new) and old == new:
        return old
    if isinstance(old, ScalarString) and isinstance(new, str):
        if old == new:
            return old
        if new.strip() == str(old).strip():  # block scalars keep their newline
            return old
        if isinstance(old, (FoldedScalarString, LiteralScalarString)):
            # Keep the block's chomping (``>-`` vs ``>``) and, when folded,
            # wrap it again at the width of the original.
            new = new.rstrip("\n") + ("\n" if str(old).endswith("\n") else "")
            node = type(old)(new)
            if isinstance(old, FoldedScalarString):
                bounds = [-1, *getattr(old, "fold_pos", []), len(old)]
                width = max((b - a - 1 for a, b in zip(bounds, bounds[1:])), default=80)
                node.fold_pos = _fold_positions(new, min(width, 100))
            return node
        return type(old)(new)
    if isinstance(old, bool) or isinstance(new, bool):
        return old if old is new else new
    if isinstance(old, (int, float)) and isinstance(new, (int, float)) and old == new:
        return old
    return _fresh(new)


def _fold_positions(text: str, width: int) -> list[int]:
    """Indices of the spaces where a folded scalar should break its lines."""
    positions, line_start = [], 0
    last_space = None
    for i, ch in enumerate(text):
        if ch == " ":
            if i - line_start > width and last_space is not None:
                positions.append(last_space)
                line_start = last_space + 1
            last_space = i
    if len(text) - line_start > width and last_space is not None and last_space > line_start:
        positions.append(last_space)
    return positions


def _identity(old: CommentedSeq, new: list) -> str | None:
    items = [*old, *new]
    for key in ("key", "value"):
        if items and all(isinstance(i, dict) and key in i for i in items):
            return key
    return None


def _merge(old: Any, new: Any, path: tuple[str, ...] = ()) -> Any:
    """Apply *new* (plain data) onto *old* (round-trip node), keeping comments."""
    if isinstance(old, CommentedMap) and isinstance(new, dict):
        keys = list(old.keys())
        for i, k in enumerate(keys):
            if k in new:
                continue
            # A comment after a removed key usually introduces what follows.
            if k in old.ca.items and i > 0 and keys[i - 1] in new and keys[i - 1] not in old.ca.items:
                old.ca.items[keys[i - 1]] = old.ca.items[k]
            del old[k]
        for k, v in new.items():
            old[k] = _merge(old[k], v, (*path, k)) if k in old else _fresh(v)
        return old
    if isinstance(old, CommentedSeq) and isinstance(new, list):
        flow = any(isinstance(i, CommentedMap) and i.fa.flow_style() for i in old)
        ident = _identity(old, new)
        if ident:
            by_id = {item[ident]: (n, item) for n, item in enumerate(old)}
            pairs = []
            for v in new:
                hit = by_id.get(v[ident])
                pairs.append(
                    (hit[0], _merge(hit[1], v, path)) if hit else (None, _fresh(v, flow=flow))
                )
        else:
            pairs = [
                (n, _merge(old[n], v, path)) if n < len(old) else (None, _fresh(v, flow=flow))
                for n, v in enumerate(new)
            ]
        comments = dict(old.ca.items)
        kept = {src for src, _ in pairs if src is not None}
        # Hand a removed entry's trailing comment to the kept entry before it.
        for src in sorted(set(comments) - kept):
            prev = max((k for k in kept if k < src), default=None)
            if prev is not None and prev not in comments:
                comments[prev] = comments[src]
        old.ca.items.clear()
        del old[:]
        for n, (src, item) in enumerate(pairs):
            old.append(item)
            if src is not None and src in comments:
                old.ca.items[n] = comments[src]
        return old
    return _merge_scalar(old, new)


def _flow_node(data: dict) -> CommentedMap:
    """A brand-new pack as a round-trip node; vocabulary and sources one per line."""
    node = _fresh(data)
    for facet in node.get("facets") or []:
        for item in facet.get("vocabulary") or []:
            item.fa.set_flow_style()
    for item in node.get("sources") or []:
        item.fa.set_flow_style()
    return node


# ── Keeping the layout of unchanged one-line entries ────────────────────────

def _flow_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """(start, end) line ranges of ``- {...}`` entries, which may span lines."""
    blocks = []
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("- {"):
            depth, quote, j = 0, None, i
            while j < len(lines):
                for ch in lines[j]:
                    if quote:
                        if ch == quote:
                            quote = None
                    elif ch in "\"'":
                        quote = ch
                    elif ch in "{[":
                        depth += 1
                    elif ch in "}]":
                        depth -= 1
                if depth <= 0:
                    break
                j += 1
            blocks.append((i, j))
            i = j + 1
        else:
            i += 1
    return blocks


def _canon(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _split(text: str) -> list[str]:
    """Split ``key: value`` fields at top-level commas."""
    fields, depth, quote, cur = [], 0, None, ""
    for ch in text:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == "," and depth == 0:
            fields.append(cur.strip())
            cur = ""
            continue
        cur += ch
    if cur.strip():
        fields.append(cur.strip())
    return fields


def _fields(entry: str) -> list[str]:
    """Top-level fields of a one-line ``- {...}`` entry."""
    return _split(entry.strip()[3:-1])


def _field_key(field: str) -> str:
    return field.split(":", 1)[0].strip()


def _layout_like(entry: str, ref: list[str]) -> list[str]:
    """Lay out a one-line *entry* like the neighbouring original entry *ref*."""
    indent = entry[: len(entry) - len(entry.lstrip())]
    fields = _fields(entry)
    if len(ref) > 1:
        # Break the entry after the same keys the reference breaks after.
        cont = ref[1][: len(ref[1]) - len(ref[1].lstrip())]
        breaks = set()
        for line in ref[:-1]:
            text = line.strip().removeprefix("- {").rstrip(",")
            breaks.add(_field_key(_split(text)[-1]))
        out, cur = [], indent + "- {"
        for i, f in enumerate(fields):
            last = i == len(fields) - 1
            cur += f + ("}" if last else ",")
            if not last and _field_key(f) in breaks:
                out.append(cur)
                cur = cont
            elif not last:
                cur += " "
        out.append(cur)
        return out
    # Pad each field to the column where the reference starts the same field,
    # if the reference is column-aligned at all.
    ref_line = ref[0]
    if not re.search(r",\s{2,}\S", ref_line):
        return [indent + "- {" + ", ".join(fields) + "}"]
    cols, pos = [], len(indent) + 3
    for f in _fields(ref_line):
        at = ref_line.find(f, pos)
        cols.append((_field_key(f), at))
        pos = at + len(f)
    line = indent + "- {"
    for i, f in enumerate(fields):
        if i:
            line += ","
            if i < len(cols) and cols[i][0] == _field_key(f) and cols[i][1] > len(line):
                line = line.ljust(cols[i][1])
            else:
                line += " "
        line += f
    return [line + "}"]


def _restore_layout(original: str, dumped: str) -> str:
    """Reuse the original text of unchanged ``- {...}`` entries in *dumped*."""
    olines = original.splitlines()
    known = {
        _canon("\n".join(olines[s:e + 1])): olines[s:e + 1] for s, e in _flow_blocks(olines)
    }
    dlines = dumped.splitlines()
    blocks = _flow_blocks(dlines)
    replaced: dict[int, list[str]] = {}
    for s, e in blocks:
        orig = known.get(_canon("\n".join(dlines[s:e + 1])))
        if orig is not None:
            replaced[s] = orig
    for idx, (s, e) in enumerate(blocks):
        if s in replaced or s != e:
            continue
        indent = len(dlines[s]) - len(dlines[s].lstrip())
        # Nearest unchanged neighbour at the same depth, looking back then ahead.
        neighbours = [*reversed(blocks[:idx]), *blocks[idx + 1:]]
        ref = next(
            (replaced[ns] for ns, _ in neighbours
             if ns in replaced and len(dlines[ns]) - len(dlines[ns].lstrip()) == indent
             and _field_key(_fields(dlines[ns])[0]) == _field_key(_fields(dlines[s])[0])),
            None,
        )
        if ref is not None:
            replaced[s] = _layout_like(dlines[s], ref)
    out: list[str] = []
    ends = dict(blocks)
    i = 0
    while i < len(dlines):
        if i in replaced:
            out.extend(replaced[i])
            i = ends[i] + 1
        else:
            out.append(dlines[i])
            i += 1
    return "\n".join(out) + "\n"


_NEW_HEADER = """\
# Domain pack: {title}
# Created with the Cassiopeia setup wizard.  See docs/DOMAIN_PACKS.md for the format.

"""


def render_pack(name: str, data: dict, *, new: bool) -> str:
    """The domain.yaml text that saving *data* would write."""
    data = prune(data)
    y = _yaml()
    out = io.StringIO()
    if new:
        y.dump(_flow_node(data), out)
        # A blank line between top-level sections, as in hand-written packs.
        body = re.sub(r"\n(?=[a-z_]+:)", "\n\n", out.getvalue())
        return _NEW_HEADER.format(title=data.get("title", name)) + body
    original = pack_file(name).read_text(encoding="utf-8")
    node = y.load(original)
    _merge(node, data)
    y.dump(node, out)
    return _restore_layout(original, out.getvalue())


def diff_pack(name: str, data: dict, *, new: bool) -> str:
    before = "" if new else pack_file(name).read_text(encoding="utf-8")
    after = render_pack(name, data, new=new)
    path = f"domains/{name}/domain.yaml"
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="/dev/null" if new else f"a/{path}", tofile=f"b/{path}",
    ))


def save_pack(name: str, data: dict, *, new: bool) -> dict[str, Any]:
    report = check_pack(name, data, new=new)
    if report["errors"]:
        raise PackEditError("; ".join(report["errors"]))
    diff = diff_pack(name, data, new=new)
    text = render_pack(name, data, new=new)
    path = pack_file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"path": str(path.relative_to(PROJECT_ROOT)), "diff": diff, **report}


def delete_new_pack(name: str) -> None:
    """Remove a pack that has never been committed and holds no data."""
    root = pack_dir(name)
    if has_data(pack_usage(name)):
        raise PackEditError(f"Pack {name!r} has data; not deleting it")
    if _git("ls-files", f"domains/{name}").strip():
        raise PackEditError(f"Pack {name!r} is tracked by git; remove it with git")
    shutil.rmtree(root)


# ─────────────────────────────────────────────────────────────────────────────
# Selecting the deployment's pack
# ─────────────────────────────────────────────────────────────────────────────

_ENV_LINE = re.compile(r"^\s*#?\s*DOMAIN_PACK\s*=.*$", re.MULTILINE)


def select_pack(name: str) -> None:
    """Write ``DOMAIN_PACK=<name>`` to ``.env`` (created from .env.example if needed)."""
    if name not in installed_packs():
        raise PackEditError(f"No domain pack {name!r}")
    if not ENV_FILE.exists():
        example = PROJECT_ROOT / ".env.example"
        ENV_FILE.write_text(example.read_text(encoding="utf-8") if example.exists() else "",
                            encoding="utf-8")
    text = ENV_FILE.read_text(encoding="utf-8")
    line = f"DOMAIN_PACK={name}"
    active = re.search(r"^\s*DOMAIN_PACK\s*=.*$", text, re.MULTILINE)
    match = active or _ENV_LINE.search(text)
    if match:
        text = text[:match.start()] + line + text[match.end():]
    else:
        text = text.rstrip("\n") + f"\n\n# Domain pack served by this deployment (see docs/DOMAIN_PACKS.md).\n{line}\n"
    ENV_FILE.write_text(text, encoding="utf-8")
    os.environ["DOMAIN_PACK"] = name


# ─────────────────────────────────────────────────────────────────────────────
# Git
# ─────────────────────────────────────────────────────────────────────────────

def _git(*args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
        )
    except FileNotFoundError as exc:
        raise PackEditError("git is not installed") from exc
    if proc.returncode != 0:
        raise PackEditError(proc.stderr.strip() or f"git {args[0]} failed")
    return proc.stdout


def _current_branch() -> str:
    try:
        return _git("symbolic-ref", "--quiet", "--short", "HEAD").strip()
    except PackEditError:
        return "HEAD"  # detached


def git_state(name: str) -> dict[str, Any]:
    """Current branch and whether the pack has uncommitted changes."""
    try:
        _git("rev-parse", "--git-dir")
        branch = _current_branch()
        status = _git("status", "--porcelain", "--", f"domains/{name}")
    except PackEditError as exc:
        return {"available": False, "error": str(exc)}
    return {
        "available": True,
        "branch": branch,
        "suggested_branch": f"pack/{name}",
        "on_default": branch in ("main", "master"),
        "dirty": bool(status.strip()),
        "changes": status.splitlines(),
    }


def commit_pack(name: str, *, message: str, branch: str | None = None) -> dict[str, Any]:
    """Commit ``domains/<name>/`` alone, optionally on a new branch.

    ``git switch -c`` keeps every uncommitted change in the working tree, and
    the path-limited commit leaves anything else that is staged untouched.
    """
    pack_dir(name)
    if not message.strip():
        raise PackEditError("A commit message is required")
    current = _current_branch()
    if branch and branch != current:
        if not re.match(r"^[\w./-]+$", branch):
            raise PackEditError(f"Invalid branch name {branch!r}")
        exists = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=PROJECT_ROOT, capture_output=True, check=False,
        ).returncode == 0
        if exists:
            raise PackEditError(
                f"Branch {branch!r} already exists; switch to it yourself, then commit"
            )
        _git("switch", "-c", branch)
        current = branch
    path = f"domains/{name}"
    _git("add", "-A", "--", path)
    if not _git("diff", "--cached", "--name-only", "--", path).strip():
        raise PackEditError("Nothing to commit: the pack has no changes")
    _git("commit", "-m", message.strip(), "--", path)
    return {"branch": current, "commit": _git("rev-parse", "--short", "HEAD").strip()}
