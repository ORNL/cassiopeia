# Copyright (c) 2026, OPAL, ORNL, UT-Battelle, LLC
# SPDX-License-Identifier: Apache-2.0

"""Fill in the journal of papers stored without one, then rescore their credibility.

Until the Europe PMC fetcher read `journalInfo`, every paper it returned was stored
with an empty journal, so the pack's journal tiers never applied. This looks those
papers up again by id, stores their journal title and ISSNs, and recomputes each
paper's credibility level and, for every researcher holding it, the credibility
score and the overall score it feeds. Nothing else about a paper changes.

Stop the API first (it keeps papers in memory and would write the old ones back):

    docker compose ... stop api
    docker compose ... run --rm --no-deps api python -m utils.backfill_journals
    docker compose ... up -d api
"""
from __future__ import annotations

import argparse
import json
import ssl
import time
import urllib.parse
import urllib.request

from domains import current_domain
from utils.data_paths import default_db_path
from utils.paper_scorer import PaperScorer
from utils.persistence import PaperStore, _row_to_scored
from utils.source_fetchers import _SSL_VERIFY, EuropePMCFetcher

BATCH = 40


def _lookup(ids: list[str], tries: int = 4) -> dict[str, dict]:
    """Europe PMC id -> its journalInfo.journal, for one batch."""
    query = "EXT_ID:(" + " OR ".join(ids) + ")"
    params = urllib.parse.urlencode({"query": query, "format": "json", "resultType": "core",
                                     "pageSize": 1000})
    context = None if _SSL_VERIFY else ssl._create_unverified_context()
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(f"{EuropePMCFetcher.BASE_URL}?{params}",
                                        timeout=90, context=context) as resp:
                items = json.load(resp)["resultList"]["result"]
            out: dict[str, dict] = {}
            for item in items:
                journal = (item.get("journalInfo") or {}).get("journal") or {}
                if journal.get("title") and item.get("id") not in out:
                    out[item["id"]] = journal
            return out
        except (OSError, ValueError, KeyError):
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    return {}


def backfill(store: PaperStore, *, dry_run: bool = False) -> dict[str, int]:
    pack = current_domain()
    scorer = PaperScorer(pack)
    epmc = {k for k, s in pack.sources.items() if s.backend == "europepmc"}
    conn = store._conn
    rows = conn.execute("SELECT paper_id, data FROM papers").fetchall()
    todo = {r["paper_id"]: json.loads(r["data"]) for r in rows
            if not json.loads(r["data"]).get("journal") and json.loads(r["data"]).get("source") in epmc}

    found: dict[str, dict] = {}
    ids = sorted(todo)
    for i in range(0, len(ids), BATCH):
        found.update(_lookup(ids[i:i + BATCH]))

    profiles = {p.researcher_id: p for p in store.load_profiles()}
    tiers = {"high": 0, "mid": 0, None: 0}
    rescored = 0
    for pid, data in todo.items():
        journal = found.get(pid)
        if not journal:
            continue
        data["journal"] = journal["title"]
        data["journal_issns"] = [i for i in (journal.get("issn"), journal.get("essn")) if i]
        paper = _row_to_scored(data).paper
        tiers[pack.journal_tier(paper.journal, paper.journal_issns)] += 1
        if dry_run:
            continue
        conn.execute("UPDATE papers SET data = ?, credibility = ? WHERE paper_id = ?",
                     (json.dumps(data), scorer._assess_credibility(paper).value, pid))
        for held in conn.execute("SELECT researcher_id, relevance FROM user_papers WHERE paper_id = ?",
                                 (pid,)).fetchall():
            rel = json.loads(held["relevance"])
            rel["credibility"] = scorer._score_credibility(paper)
            profile = profiles.get(held["researcher_id"])
            if profile is not None:
                scored = _row_to_scored({**data, "relevance": rel})
                rel["overall"] = scorer.overall(scored.relevance, profile)
            conn.execute("UPDATE user_papers SET relevance = ? WHERE researcher_id = ? AND paper_id = ?",
                         (json.dumps(rel), held["researcher_id"], pid))
            rescored += 1
    if not dry_run:
        conn.commit()
    return {"without_journal": len(todo), "found": sum(p in found for p in todo),
            "high_tier": tiers["high"], "mid_tier": tiers["mid"], "untiered": tiers[None],
            "library_entries_rescored": rescored}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="look up and report, write nothing")
    args = ap.parse_args()
    print(json.dumps(backfill(PaperStore(default_db_path()), dry_run=args.dry_run), indent=1))


if __name__ == "__main__":
    main()
