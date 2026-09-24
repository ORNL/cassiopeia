# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for the setup wizard backend: editing, locking, selecting, committing."""

from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import subprocess

import pytest
import yaml
from fastapi.testclient import TestClient

import domains
from domains import DomainPackError, editing, load_domain, set_domain
from tests.conftest import TEST_PACK_DIR
from utils import data_paths
from utils.persistence import PaperStore

PACK = "materials_test"


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A throwaway project root holding the test pack under domains/."""
    packs = tmp_path / "domains"
    shutil.copytree(TEST_PACK_DIR, packs / PACK)
    monkeypatch.setattr(domains, "PACKS_DIR", packs)
    monkeypatch.setattr(editing, "PACKS_DIR", packs)
    monkeypatch.setattr(editing, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(editing, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(data_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(data_paths, "DATA_ROOT", tmp_path / "data")
    monkeypatch.delenv("DOMAIN_PACK", raising=False)
    return tmp_path


def _with_profile(project, facets):
    """Give the pack a database with one saved profile."""
    db = project / "data" / PACK / "cassiopeia.db"
    PaperStore(db)._conn.close()
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO profiles (researcher_id, data) VALUES (?, ?)",
        ("r1", json.dumps({"researcher_id": "r1", "name": "R", "facets": facets})),
    )
    conn.commit()
    conn.close()


# ── Rendering ────────────────────────────────────────────────────────────────

def test_unchanged_pack_saves_without_diff(project):
    data = editing.read_pack(PACK)
    assert editing.diff_pack(PACK, data, new=False) == ""


def test_reference_pack_round_trips_without_diff():
    for name in domains.installed_packs():
        assert editing.diff_pack(name, editing.read_pack(name), new=False) == ""


def test_edit_keeps_comments_and_changes_only_what_changed(project):
    data = editing.read_pack(PACK)
    data["facets"][0]["vocabulary"].append({"value": "silicon", "label": "Silicon", "aliases": ["silicon"]})
    diff = editing.diff_pack(PACK, data, new=False)
    added = [ln for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    removed = [ln for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    assert added == ["+      - {value: silicon, label: Silicon, aliases: [silicon]}"]
    assert removed == []
    text = editing.render_pack(PACK, data, new=False)
    assert "# Test-only domain pack." in text
    assert yaml.safe_load(text) == editing.prune(data)


def test_new_entry_is_aligned_like_its_neighbours(project):
    path = domains.PACKS_DIR / PACK / "domain.yaml"
    path.write_text(path.read_text().replace(
        "      - {value: graphite, label: Graphite, aliases: [graphite]}",
        "      - {value: graphite,    label: Graphite,    aliases: [graphite]}",
    ))
    data = editing.read_pack(PACK)
    data["facets"][0]["vocabulary"].append({"value": "tin", "label": "Tin", "aliases": ["tin"]})
    text = editing.render_pack(PACK, data, new=False)
    assert "      - {value: tin,         label: Tin,         aliases: [tin]}" in text


def test_removed_fields_disappear(project):
    data = editing.read_pack(PACK)
    data["prompts"]["theme_example"] = ""
    text = editing.render_pack(PACK, data, new=False)
    assert "theme_example" not in text


def test_new_pack_from_blank_and_from_template_are_valid(project):
    for template in (None, PACK):
        data = editing.new_pack_data("new_one", "New one", template)
        assert "legacy" not in data
        assert editing.check_pack("new_one", data, new=True) == {"errors": [], "warnings": []}
        editing.save_pack("new_one", data, new=True)
        pack = load_domain(domains.PACKS_DIR / "new_one")
        assert pack.name == "new_one" and pack.title == "New one"
        shutil.rmtree(domains.PACKS_DIR / "new_one")


# ── Validation and locking ──────────────────────────────────────────────────

def test_malformed_drafts_are_reported(project):
    data = editing.read_pack(PACK)
    data["facets"][0].pop("role")
    assert "missing required field 'role'" in editing.check_pack(PACK, data, new=False)["errors"][0]
    data = editing.read_pack(PACK)
    data["sources"][0]["backend"] = "nowhere"
    assert "unknown backend 'nowhere'" in editing.check_pack(PACK, data, new=False)["errors"][0]
    assert editing.check_pack("Bad-Name", {}, new=True)["errors"]
    assert "already exists" in editing.check_pack(PACK, editing.read_pack(PACK), new=True)["errors"][0]


def test_structure_is_free_without_data(project):
    data = editing.read_pack(PACK)
    data["facets"][1]["role"] = "subject"
    assert editing.check_pack(PACK, data, new=False)["errors"] == []
    assert editing.locked_fields(PACK)["has_data"] is False


def test_structure_is_locked_once_profiles_exist(project):
    _with_profile(project, {"material": ["graphite"], "property": ["capacity_fade"]})
    locked = editing.locked_fields(PACK)
    assert locked["has_data"] and locked["values"]["property"] == {"capacity_fade": 1}

    data = editing.read_pack(PACK)
    data["facets"][1]["role"] = "subject"                       # role change
    data["facets"].pop(2)                                       # facet removed
    data["facets"][1]["vocabulary"] = [                         # used value removed
        v for v in data["facets"][1]["vocabulary"] if v["value"] != "capacity_fade"
    ]
    errors = editing.check_pack(PACK, data, new=False)["errors"]
    assert any("must keep role" in e for e in errors)
    assert any("cannot be removed or renamed" in e for e in errors)
    assert any("'capacity_fade' is used by 1 profile" in e for e in errors)


def test_safe_edits_stay_allowed_with_data(project):
    _with_profile(project, {"material": ["graphite"], "property": ["capacity_fade"]})
    data = editing.read_pack(PACK)
    data["facets"][1]["vocabulary"].append({"value": "porosity", "label": "Porosity"})
    data["facets"][1]["vocabulary"][1]["hidden"] = True         # hide the used value
    data["facets"][1]["vocabulary"] = [                         # unused value removed
        v for v in data["facets"][1]["vocabulary"] if v["value"] != "thermal_runaway"
    ]
    data["facets"].append({"key": "scale", "label": "Scale", "role": "technique"})
    data["title"] = "Renamed"
    assert editing.check_pack(PACK, data, new=False)["errors"] == []


# ── Database stamp ──────────────────────────────────────────────────────────

def test_database_refuses_another_pack(tmp_path):
    db = tmp_path / "x.db"
    PaperStore(db)._conn.close()
    assert data_paths.stamped_pack(db) == PACK
    other = copy.copy(load_domain(TEST_PACK_DIR))
    other.name = "someone_else"
    set_domain(other)
    with pytest.raises(DomainPackError, match="belongs to domain pack 'materials_test'"):
        PaperStore(db)


def test_database_refuses_a_removed_facet_but_accepts_a_new_one(tmp_path):
    from dataclasses import replace

    from domains import Facet

    db = tmp_path / "x.db"
    PaperStore(db)._conn.close()
    pack = load_domain(TEST_PACK_DIR)
    set_domain(replace(pack, facets=(*pack.facets, Facet(key="scale", label="Scale", role="technique"))))
    PaperStore(db)._conn.close()
    set_domain(replace(pack, facets=pack.facets[:2]))
    with pytest.raises(DomainPackError, match="technique"):
        PaperStore(db)


def test_legacy_database_moves_into_the_pack_directory(project):
    legacy = project / "cassiopeia.db"
    sqlite3.connect(legacy).close()
    (project / "chroma_db").mkdir()
    target = data_paths.pack_data_dir(PACK)   # the only installed pack owns it
    assert (target / "cassiopeia.db").is_file() and (target / "chroma_db").is_dir()
    assert not legacy.exists()


def test_legacy_database_of_another_pack_stays(project):
    legacy = project / "cassiopeia.db"
    PaperStore(legacy)._conn.close()                      # stamped materials_test
    data_paths.pack_data_dir("other_pack")
    assert legacy.exists()


# ── Selecting ────────────────────────────────────────────────────────────────

def test_select_pack_writes_env(project):
    env = project / ".env"
    env.write_text("A=1\n# DOMAIN_PACK=old\nB=2\n")
    editing.select_pack(PACK)
    assert env.read_text() == f"A=1\nDOMAIN_PACK={PACK}\nB=2\n"
    env.write_text("DOMAIN_PACK=x\n# DOMAIN_PACK=y\n")
    editing.select_pack(PACK)
    assert env.read_text() == f"DOMAIN_PACK={PACK}\n# DOMAIN_PACK=y\n"
    env.write_text("A=1\n")
    editing.select_pack(PACK)
    assert env.read_text().endswith(f"DOMAIN_PACK={PACK}\n")
    with pytest.raises(DomainPackError):
        editing.select_pack("missing")


# ── Git ──────────────────────────────────────────────────────────────────────

def _git(root, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout


def test_commit_only_touches_the_pack_and_can_branch(project, monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    _git(project, "init", "-q", "-b", "main")
    (project / "other.txt").write_text("unrelated")
    _git(project, "add", "other.txt")                     # staged, must stay staged
    state = editing.git_state(PACK)
    assert state["on_default"] and state["dirty"]

    result = editing.commit_pack(PACK, message="add pack", branch=f"pack/{PACK}")
    assert result["branch"] == f"pack/{PACK}"
    assert _git(project, "show", "--name-only", "--format=", "HEAD").split() == [
        f"domains/{PACK}/domain.yaml"
    ]
    assert _git(project, "diff", "--cached", "--name-only").split() == ["other.txt"]
    assert not editing.git_state(PACK)["dirty"]
    with pytest.raises(DomainPackError, match="Nothing to commit"):
        editing.commit_pack(PACK, message="again")


# ── Setup server ─────────────────────────────────────────────────────────────

def test_setup_server_serves_this_machine_only(project, monkeypatch):
    import setup_server

    client = TestClient(setup_server.app)                 # client host "testclient"
    assert client.get("/api/setup/state").status_code == 403

    monkeypatch.setattr(setup_server, "_LOCAL_HOSTS", {"testclient", "localhost"})
    local = TestClient(setup_server.app, base_url="http://localhost")
    state = local.get("/api/setup/state").json()
    assert [p["name"] for p in state["packs"]] == [PACK]
    body = {"name": PACK}
    assert local.post("/api/setup/select", json=body).status_code == 403
    ok = local.post("/api/setup/select", json=body, headers={"X-Cassiopeia-Setup": "1"})
    assert ok.status_code == 200
