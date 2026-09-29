#!/usr/bin/env python
"""
Store Master reconciliation — scripts/store_master_reconcile.py

    # READ-ONLY report against the local database (default: writes nothing)
    python scripts/store_master_reconcile.py

    # READ-ONLY report from the Store Infor.pdf transcription (no database)
    python scripts/store_master_reconcile.py --snapshot tests/fixtures/store_master/pilot_stores_2026-09-29.json

    # apply, after review — local, or a named and verified pilot
    python scripts/store_master_reconcile.py --apply --reconciled-by data-team:<name>
    python scripts/store_master_reconcile.py --apply --reconciled-by data-team:<name> \
        --pilot-seed --expect-database postgres --expect-host-contains supabase

Reconciles the Store Master to the operator's authoritative CStorePro store
directory (data/store_master/cstorepro_store_directory.json) through the
existing stores + store_identifiers mechanism. The rules live in
app/services/store_master_service.py.

Default is a READ-ONLY report (read-only session, SELECTs only) of: current
stores, canonical targets, exact matches, records to reconcile, duplicate
risks, evidence preserved, unresolved source identities, invoice/document/
proposal/mapping associations and Product Master mappings per store.

--apply, in ONE transaction after verifying the target (pilot_seed_guard):
  * re-reads and re-plans, and refuses if anything changed since the report;
  * refuses outright if the plan has a duplicate risk or a refusal;
  * INSERTs the new canonical stores and the new identifiers;
  * UPDATEs exactly two things on exactly the mandated records: display name
    and address columns (and appends a notes stamp) — guarded by the previous
    display name, so a changed record fails instead of being overwritten;
  * never deletes, never touches identity_status, never changes a store_id on
    any invoice, document, proposal, mapping or Product Master row, and never
    touches Item Sales store 47708760;
  * verifies all of that before COMMIT.
Writes analysis/master-data/store_master_reconciliation_report.json.
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
    ACTION_CREATE,
    ACTION_DUPLICATE,
    ACTION_REFUSE,
    compare_snapshots,
    load_directory,
    plan_findings,
    reconcile,
)
from scripts.pilot_seed_guard import add_pilot_arguments, resolve_seed_target  # noqa: E402

ALIAS_EVIDENCE = ROOT / "data" / "reference" / "operator_store_evidence.json"
REPORT = ROOT / "analysis" / "master-data" / "store_master_reconciliation_report.json"
# An apply run reports to its own file so it can never overwrite the read-only report it was approved from.
APPLY_REPORT = ROOT / "analysis" / "master-data" / "store_master_reconciliation_apply_report.json"

STORE_COLUMNS = ("id", "display_name", "customer_name", "address_line_1", "address_line_2", "city", "state",
                 "postal_code", "status", "identity_status", "notes")
SELECT_STORES = f"SELECT {', '.join(STORE_COLUMNS)} FROM stores ORDER BY created_at, id"
SELECT_IDENTIFIERS = ("SELECT store_id, source_system, identifier_type, identifier_value, evidence "
                      "FROM store_identifiers ORDER BY store_id, source_system, identifier_type, identifier_value")
# Read-only association counts per store (what reconciliation must leave where it is).
COUNT_QUERIES = {
    "invoices": "SELECT store_id, count(*) FROM invoices GROUP BY store_id",
    "documents": "SELECT store_id, count(*) FROM documents GROUP BY store_id",
    "proposals": "SELECT store_id, count(*) FROM product_data_proposals GROUP BY store_id",
    "pending_proposals": "SELECT store_id, count(*) FROM product_data_proposals WHERE status = 'PENDING' GROUP BY store_id",
    "case_mappings": "SELECT store_id, count(*) FROM product_case_mappings GROUP BY store_id",
    "catalogue_rows": "SELECT store_id, count(*) FROM store_product_references GROUP BY store_id",
    "master_commercial_mappings": "SELECT store_id, count(*) FROM master_commercial_mappings GROUP BY store_id",
}
# Read-only relationship detail for the report (never used to plan or apply).
DETAIL_QUERIES = {
    "invoices": "SELECT store_id, id, invoice_number, status, document_id FROM invoices ORDER BY created_at, id",
    "documents": "SELECT store_id, status, count(*) FROM documents GROUP BY store_id, status",
    "proposals": ("SELECT store_id, id, entity_type, entity_key, status, source FROM product_data_proposals "
                  "ORDER BY created_at, id"),
    "commercial_mappings": ("SELECT store_id, approval_state, count(*) FROM master_commercial_mappings "
                            "GROUP BY store_id, approval_state"),
}
PDF_SNAPSHOT = ROOT / "tests" / "fixtures" / "store_master" / "pilot_stores_2026-09-29.json"

INSERT_STORE = ("INSERT INTO stores (id, display_name, customer_name, address_line_1, address_line_2, city, state, "
                "postal_code, status, identity_status, notes) VALUES (%(id)s, %(display_name)s, %(customer_name)s, "
                "%(address_line_1)s, %(address_line_2)s, %(city)s, %(state)s, %(postal_code)s, %(status)s, "
                "%(identity_status)s, %(notes)s)")
INSERT_IDENTIFIER = ("INSERT INTO store_identifiers (id, store_id, source_system, identifier_type, identifier_value, "
                     "evidence) VALUES (%(id)s, %(store_id)s, %(source_system)s, %(identifier_type)s, "
                     "%(identifier_value)s, %(evidence)s::jsonb)")
UPDATE_ADOPTED = ("UPDATE stores SET display_name = %(display_name)s, address_line_1 = %(address_line_1)s, "
                  "address_line_2 = %(address_line_2)s, city = %(city)s, state = %(state)s, "
                  "postal_code = %(postal_code)s, notes = %(notes)s "
                  "WHERE id = %(id)s AND display_name = %(expected_display_name)s")


def read_snapshot(cursor) -> list[dict]:
    cursor.execute(SELECT_STORES)
    stores = {str(r[0]): {**dict(zip(STORE_COLUMNS, r, strict=True)), "id": str(r[0]), "identifiers": [], "counts": {}}
              for r in cursor.fetchall()}
    cursor.execute(SELECT_IDENTIFIERS)
    for store_id, source, id_type, value, evidence in cursor.fetchall():
        if str(store_id) in stores:
            stores[str(store_id)]["identifiers"].append({"source_system": source, "identifier_type": id_type,
                                                         "identifier_value": value, "evidence": evidence or {}})
    for key, sql in COUNT_QUERIES.items():
        cursor.execute(sql)
        for store_id, n in cursor.fetchall():
            if str(store_id) in stores:
                stores[str(store_id)]["counts"][key] = n
    return list(stores.values())


def read_relationships(cursor) -> dict[str, Any]:
    """Per-store invoices, documents, proposals and commercial mappings. SELECTs only."""
    out: dict[str, Any] = {}

    def bucket(store_id) -> dict[str, Any]:
        return out.setdefault(str(store_id) if store_id else "(no store)",
                              {"invoices": [], "documents_by_status": {}, "proposals": [],
                               "commercial_mappings_by_state": {}})
    cursor.execute(DETAIL_QUERIES["invoices"])
    for store_id, invoice_id, number, status, document_id in cursor.fetchall():
        bucket(store_id)["invoices"].append({"id": str(invoice_id), "invoice_number": number, "status": status,
                                             "document_id": str(document_id) if document_id else None})
    cursor.execute(DETAIL_QUERIES["documents"])
    for store_id, status, n in cursor.fetchall():
        bucket(store_id)["documents_by_status"][str(status)] = n
    cursor.execute(DETAIL_QUERIES["proposals"])
    for store_id, proposal_id, entity_type, entity_key, status, source in cursor.fetchall():
        bucket(store_id)["proposals"].append({"id": str(proposal_id), "entity_type": entity_type,
                                              "entity_key": entity_key, "status": status, "source": source})
    cursor.execute(DETAIL_QUERIES["commercial_mappings"])
    for store_id, state, n in cursor.fetchall():
        bucket(store_id)["commercial_mappings_by_state"][state] = n
    return out


def comparable(actions) -> list[tuple]:
    """What must be identical between two plans (created ids aside, which are generated fresh)."""
    rows = [a if isinstance(a, dict) else a.__dict__ for a in actions]
    return [(a["action"], a["canonical"], a["store_id"] if a["action"] != ACTION_CREATE else None,
             sorted((i["source_system"], i["identifier_type"], i["identifier_value"]) for i in a["identifiers"]),
             sorted((k, v) for k, v in a["updates"].items() if k != "notes")) for a in rows]


APPROVED_REPORT = ROOT / "analysis" / "master-data" / "store_master_reconciliation_report.prewrite.json"


def check_against_approved(planned, approved: dict[str, Any]) -> None:
    """An apply run writes only the plan a person approved from a read-only report — or nothing."""
    if approved.get("applied") or not str(approved.get("source", "")).startswith("database ("):
        raise RuntimeError("the approved report must be a read-only database report")
    if comparable(planned) != comparable(approved["actions"]):
        raise RuntimeError("the live plan differs from the approved read-only report; nothing was written")


def apply(connection, directory, planned, reconciled_by: str) -> dict[str, Any]:
    """One transaction. Refuses on any drift, duplicate risk or refusal."""
    blocking = [a for a in planned if a.action in (ACTION_DUPLICATE, ACTION_REFUSE)]
    if blocking:
        raise RuntimeError(f"the plan has {len(blocking)} duplicate risk(s)/refusal(s); nothing was written")
    connection.set_session(readonly=False, autocommit=False)
    cursor = connection.cursor()
    try:
        before = read_snapshot(cursor)
        again = reconcile(before, directory, reconciled_by=reconciled_by)
        if comparable(again) != comparable(planned):
            raise RuntimeError("the store master changed since the report; nothing was written")
        written: dict[str, list] = {"stores_created": [], "stores_adopted": [], "identifiers": []}
        for a in again:
            if a.action == ACTION_CREATE and a.new_store:
                cursor.execute(INSERT_STORE, a.new_store)
                written["stores_created"].append(a.new_store["id"])
            if a.action == ACTION_ADOPT:
                expected = next(s["display_name"] for s in before if s["id"] == a.store_id)
                cursor.execute(UPDATE_ADOPTED, {**a.updates, "id": a.store_id, "expected_display_name": expected})
                if cursor.rowcount != 1:
                    raise RuntimeError(f"adopting {a.store_id} matched {cursor.rowcount} rows; nothing was written")
                written["stores_adopted"].append(a.store_id)
            for ident in a.identifiers:
                cursor.execute(INSERT_IDENTIFIER, {**ident, "evidence": json.dumps(ident["evidence"], sort_keys=True)})
                written["identifiers"].append(ident["id"])
        after = read_snapshot(cursor)
        _verify(before, after, again)
        connection.commit()
        # What was actually written (these are the real ids), and the state either side of it.
        written["actions"] = [a.__dict__ for a in again]
        written["before"], written["after"] = before, after
        return written
    except Exception:
        connection.rollback()
        raise


def _verify(before: list[dict], after: list[dict], actions) -> None:
    """Nothing but the planned changes happened."""
    adopted = {a.store_id: a for a in actions if a.action == ACTION_ADOPT}
    after_by_id = {s["id"]: s for s in after}
    for store in before:
        now = after_by_id.get(store["id"])
        if now is None:
            raise RuntimeError(f"store {store['id']} disappeared")
        if now["identity_status"] != store["identity_status"]:
            raise RuntimeError(f"identity status of {store['id']} changed")
        if now["counts"] != store["counts"]:
            raise RuntimeError(f"associations of {store['id']} changed")
        kept = {(i["source_system"], i["identifier_type"], i["identifier_value"]) for i in store["identifiers"]}
        if not kept <= {(i["source_system"], i["identifier_type"], i["identifier_value"]) for i in now["identifiers"]}:
            raise RuntimeError(f"an identifier of {store['id']} was lost")
        if store["id"] not in adopted and any(now[k] != store[k] for k in ("display_name", "address_line_1", "city",
                                                                            "state", "postal_code", "notes")):
            raise RuntimeError(f"store {store['id']} changed but was not an adoption")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile the Store Master to the CStorePro store directory.")
    parser.add_argument("--snapshot", type=Path, default=None,
                        help="Plan against a JSON snapshot instead of the database (read-only, offline).")
    parser.add_argument("--apply", action="store_true", help="Apply the plan in one transaction (after review).")
    parser.add_argument("--reconciled-by", default=None, help="Who is applying the reconciliation (recorded).")
    parser.add_argument("--approved-report", type=Path, default=APPROVED_REPORT,
                        help="The read-only report the write was approved from; --apply refuses any other plan.")
    add_pilot_arguments(parser)
    args = parser.parse_args()
    directory = load_directory(alias_evidence_path=ALIAS_EVIDENCE)
    if args.apply and args.snapshot:
        sys.exit("--apply works on a database, not a snapshot")
    if args.apply and not (args.reconciled_by or "").strip():
        sys.exit("--apply needs --reconciled-by (who is applying it; recorded on every change)")

    written: dict[str, Any] = {}
    relationships: dict[str, Any] = {}
    if args.snapshot:
        stores = json.loads(args.snapshot.read_text())["stores"]
        source = f"snapshot {args.snapshot.name} (NOT a database read)"
    else:
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
            source = f"database ({target.target}, read-only)"
            if args.apply:
                planned = reconcile(stores, directory, reconciled_by=args.reconciled_by)
                check_against_approved(planned, json.loads(args.approved_report.read_text()))
                written = apply(connection, directory, planned, args.reconciled_by)
                print(f"COMMITTED: {len(written['stores_created'])} store(s) created, "
                      f"{len(written['stores_adopted'])} adopted in place {written['stores_adopted']}, "
                      f"{len(written['identifiers'])} identifier(s) added; nothing deleted.")
        finally:
            connection.close()

    actions = reconcile(stores, directory, reconciled_by=args.reconciled_by)
    findings = plan_findings(stores, actions, directory)
    findings["relationships"] = relationships
    if not args.snapshot and PDF_SNAPSHOT.exists():
        findings["differences_from_pdf_snapshot"] = compare_snapshots(
            stores, json.loads(PDF_SNAPSHOT.read_text())["stores"])
    for a in actions:
        print(f"  {a.action:34} {a.canonical or '-':18} store={a.store_id or '-'} "
              f"+ids={len(a.identifiers)} {'; '.join(a.reasons)[:160]}")
    report_path = APPLY_REPORT if args.apply else REPORT
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({
        "at": datetime.now(UTC).isoformat(), "source": source, "applied": bool(written), "written": written,
        "findings": findings,
        # In an apply run the actions that matter are written["actions"]; these are the pre-write plan.
        "actions": [a.__dict__ for a in actions]}, indent=2, default=str))
    print(f"Source: {source}. Report: "
          f"{report_path.relative_to(ROOT) if report_path.is_relative_to(ROOT) else report_path}")


if __name__ == "__main__":
    main()
