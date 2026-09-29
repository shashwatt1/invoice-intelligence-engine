#!/usr/bin/env python
"""
Store Master post-write audit — scripts/store_master_post_write_audit.py

    python scripts/store_master_post_write_audit.py --pilot-seed \
        --expect-database postgres --expect-host-contains supabase

READ-ONLY. After scripts/store_master_reconcile.py --apply, checks the live
store master against the pre-write baseline
(analysis/master-data/store_master_reconciliation_report.prewrite.json — the
read-only report the write was approved from) and the CStorePro directory:

  * exactly 14 directory stores, with exact names and addresses, all unresolved;
  * the two adopted records kept their store ids (LG - RCM, PB Wolf);
  * the protected source identity 47708760 is unchanged, unlinked and still
    holds exactly its Product Master commercial mappings;
  * every existing store's invoices, documents, proposals and commercial
    mappings are exactly as before; no store row was deleted;
  * every pre-write identifier is still present, and the aliases were recorded;
  * no physical store is named MIDLER, WOLF or TIKKI;
  * a reconciliation rerun finds nothing left to do.

Writes analysis/master-data/store_master_post_write_audit.json and exits
non-zero if any check fails. Never writes to the database.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.store_master_service import (  # noqa: E402
    ACTION_ADOPT,
    ACTION_KEEP_SOURCE,
    ACTION_PRESENT,
    load_directory,
    reconcile,
)
from scripts.pilot_seed_guard import add_pilot_arguments, resolve_seed_target  # noqa: E402
from scripts.store_master_reconcile import read_relationships, read_snapshot  # noqa: E402

BASELINE = ROOT / "analysis" / "master-data" / "store_master_reconciliation_report.prewrite.json"
AUDIT = ROOT / "analysis" / "master-data" / "store_master_post_write_audit.json"


def _key(i: dict) -> tuple[str, str, str]:
    return (i["source_system"], i["identifier_type"], i["identifier_value"])


def audit(stores: list[dict], relationships: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Every check, as (name -> {ok, detail}). Pure."""
    directory = load_directory()
    findings = baseline["findings"]
    checks: dict[str, dict[str, Any]] = {}

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks[name] = {"ok": bool(ok), "detail": detail}

    by_id = {s["id"]: s for s in stores}
    listed = {i["identifier_value"]: s for s in stores for i in s["identifiers"]
              if i["source_system"] == "cstorepro" and i["identifier_type"] == "directory_name"}
    canonical = {c.name: c for c in directory.stores}
    check("exactly_14_directory_stores", len(listed) == 14 and set(listed) == set(canonical), sorted(listed))
    wrong = {}
    for name, c in canonical.items():
        s = listed.get(name)
        if s is None:
            continue
        expected = {"display_name": c.name, "address_line_1": c.address_line_1, "address_line_2": None,
                    "city": c.city, "state": c.state, "postal_code": c.postal_code}
        diff = {k: s.get(k) for k, v in expected.items() if s.get(k) != v}
        if diff:
            wrong[name] = diff
    check("canonical_names_and_addresses_exact", not wrong, wrong)
    check("all_14_unresolved", all(s["identity_status"] == "unresolved" for s in listed.values()),
          {n: s["identity_status"] for n, s in listed.items() if s["identity_status"] != "unresolved"})

    adopted = {a["canonical"]: a["store_id"] for a in baseline["actions"] if a["action"] == ACTION_ADOPT}
    check("adopted_store_ids_preserved",
          all(listed.get(n, {}).get("id") == sid for n, sid in adopted.items()) and len(adopted) == 2, adopted)

    base_stores = {s["id"]: s for s in findings["current_stores"]}
    check("no_store_deleted", set(base_stores) <= set(by_id), sorted(set(base_stores) - set(by_id)))
    check("store_rows_before_after", len(by_id) == len(base_stores) + 12,
          {"before": len(base_stores), "after": len(by_id)})
    check("no_store_named_after_an_alias",
          not [s for s in stores if (s.get("display_name") or "").upper() in {"MIDLER", "WOLF", "TIKKI"}])

    protected = [a["store_id"] for a in baseline["actions"] if a["action"] == ACTION_KEEP_SOURCE]
    p_detail = {}
    for sid in protected:
        s = by_id.get(sid)
        before = base_stores.get(sid, {})
        p_detail[sid] = {
            "exists": s is not None,
            "unresolved": s is not None and s["identity_status"] == "unresolved",
            "no_name_or_address": s is not None and not s.get("display_name") and not s.get("address_line_1"),
            "identifiers_unchanged": s is not None and sorted("/".join(_key(i)) for i in s["identifiers"])
            == sorted(before.get("identifiers", [])),
            "commercial_mappings_unchanged": relationships.get(sid, {}).get("commercial_mappings_by_state")
            == findings["relationships"].get(sid, {}).get("commercial_mappings_by_state"),
        }
    check("source_identity_47708760_unchanged_and_unlinked",
          bool(protected) and all(all(v.values()) for v in p_detail.values()), p_detail)

    rel_diff = {}
    for sid, before in findings["relationships"].items():
        now = relationships.get(sid, {"invoices": [], "documents_by_status": {}, "proposals": [],
                                      "commercial_mappings_by_state": {}})
        if before != now:
            rel_diff[sid] = {"before": before, "after": now}
    new_rel = {sid: r for sid, r in relationships.items() if sid not in findings["relationships"]
               and (r["invoices"] or r["proposals"] or r["commercial_mappings_by_state"])}
    check("relationships_intact", not rel_diff and not new_rel, {"changed": rel_diff, "new": new_rel})

    lost = {}
    for sid, before in base_stores.items():
        now = {"/".join(_key(i)) for i in by_id.get(sid, {"identifiers": []})["identifiers"]}
        missing = sorted(set(before["identifiers"]) - now)
        if missing:
            lost[sid] = missing
    check("pre_write_identifiers_preserved", not lost, lost)

    expected_aliases = {("operator", "store_alias", a): canonical_name for a, canonical_name in directory.aliases}
    expected_aliases.update({("store_master", "store_alias", "RCM"): "LG - RCM",
                             ("store_master", "store_alias", "Apple Foods II"): "PB Wolf"})
    alias_state = {}
    for key, name in expected_aliases.items():
        holders = [s["id"] for s in stores for i in s["identifiers"] if _key(i) == key]
        alias_state["/".join(key)] = {"on": holders, "expected": listed.get(name, {}).get("id")}
    check("aliases_recorded_on_the_right_store",
          all(v["on"] == [v["expected"]] for v in alias_state.values()), alias_state)

    rerun = reconcile(stores, directory)
    check("rerun_finds_nothing_to_do",
          {a.action for a in rerun if a.canonical} == {ACTION_PRESENT} and not [a for a in rerun if a.identifiers]
          and [a.store_id for a in rerun if a.action == ACTION_KEEP_SOURCE] == protected,
          [(a.action, a.canonical) for a in rerun if a.action not in (ACTION_PRESENT, ACTION_KEEP_SOURCE)])
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only post-write audit of the Store Master reconciliation.")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    add_pilot_arguments(parser)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text())
    if baseline.get("applied"):
        sys.exit("the baseline must be the read-only pre-write report")

    from app.core.config import get_settings

    settings = get_settings()
    target = resolve_seed_target(settings.database_url, settings.database_url_sync, args)
    import psycopg2

    dsn = settings.database_url_sync.replace("postgresql+psycopg2://", "postgresql://")
    connection = psycopg2.connect(dsn, connect_timeout=10)
    try:
        connection.set_session(readonly=True, autocommit=True)
        stores = read_snapshot(connection.cursor())
        relationships = read_relationships(connection.cursor())
    finally:
        connection.close()

    checks = audit(stores, relationships, baseline)
    for name, result in checks.items():
        print(f"  {'PASS' if result['ok'] else 'FAIL'}  {name}")
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps({"at": datetime.now(UTC).isoformat(), "target": target.target,
                                 "all_passed": all(r["ok"] for r in checks.values()), "checks": checks,
                                 "stores": stores, "relationships": relationships}, indent=2, default=str))
    print(f"Audit: {AUDIT.relative_to(ROOT)}")
    if not all(r["ok"] for r in checks.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
