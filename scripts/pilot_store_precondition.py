"""
Pilot store precondition — scripts/pilot_store_precondition.py

Gives Item Sales store code 47708760 a store on the pilot, so the Product
Master seed's commercial candidates can resolve to one.

    # show exactly what would be written; writes nothing
    python scripts/pilot_store_precondition.py --pilot-seed \
        --expect-database postgres --expect-host-contains supabase --dry-run

    # write it
    python scripts/pilot_store_precondition.py --pilot-seed \
        --expect-database postgres --expect-host-contains supabase

Why a new store and not an existing one: locally, 47708760 belongs to its
own unresolved store, which migration 0013 created from the store codes it
found in the data. The pilot was migrated from empty, so 0013 found none. The
two pilot stores, Apple Foods II and RCM, are each recorded as NOT linked to
any Item Sales store code, and no admissible evidence pairs 47708760 with
either (docs/store-resolution-phase-2a.md). Attaching the code to one of them
would be a physical store identification, which only a person makes.

So this does what 0013 does for a code it finds, and nothing else:

  * one new store: identity `unresolved`, no name, no address, no customer —
    the UI shows it as "Store 47708760 (location not yet confirmed)";
  * one identifier on it: item_sales / store_code / 47708760.

It never updates or deletes a row, never touches an existing store or its
identifiers, and writes no Product Master, invoice, document or legacy
table. It is separate from the Product Master seed and runs before it.

Idempotent. If the identifier already names a store that looks exactly like
the one this would create, it reports the precondition satisfied and writes
nothing. If it names any other store, it refuses. The target is verified by
pilot_seed_guard exactly as the seed verifies it, and there is no override.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.store import (  # noqa: E402
    IDENTITY_UNRESOLVED,
    SOURCE_ITEM_SALES,
    STATUS_ACTIVE,
    TYPE_STORE_CODE,
)
from scripts.pilot_seed_guard import (  # noqa: E402
    TARGET_PILOT,
    add_pilot_arguments,
    resolve_seed_target,
)

STORE_CODE = "47708760"
EVIDENCE_DIR = ROOT / "data" / "reference" / f"store_{STORE_CODE}"
REPORT = ROOT / "analysis" / "master-data" / "pilot_store_precondition_report.json"
COMMAND = "scripts/pilot_store_precondition.py"

# Human-identity columns. The new store has none of them, and a store that
# has any of them is never the right owner for a bare source code.
IDENTITY_FIELDS = ("display_name", "customer_name", "address_line_1", "address_line_2",
                   "city", "state", "postal_code")

# The only statements this command issues. Nothing else is sent.
SELECT_STORES = (
    "SELECT id, display_name, customer_name, address_line_1, address_line_2, city, state, "
    "postal_code, status, identity_status, notes FROM stores ORDER BY created_at, id"
)
SELECT_IDENTIFIERS = (
    "SELECT id, store_id, source_system, identifier_type, identifier_value "
    "FROM store_identifiers ORDER BY store_id, source_system, identifier_type, identifier_value"
)
INSERT_STORE = (
    "INSERT INTO stores (id, status, identity_status, notes) "
    "VALUES (%(id)s, %(status)s, %(identity_status)s, %(notes)s)"
)
INSERT_IDENTIFIER = (
    "INSERT INTO store_identifiers "
    "(id, store_id, source_system, identifier_type, identifier_value, evidence) "
    "VALUES (%(id)s, %(store_id)s, %(source_system)s, %(identifier_type)s, "
    "%(identifier_value)s, %(evidence)s::jsonb)"
)

STORE_COLUMNS = ("id",) + IDENTITY_FIELDS + ("status", "identity_status", "notes")
IDENTIFIER_COLUMNS = ("id", "store_id", "source_system", "identifier_type", "identifier_value")


class PreconditionRefused(SystemExit):
    """Raised instead of writing when the target is not in the expected state."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"REFUSING: {reason}")
        self.reason = reason


@dataclass
class StoreState:
    stores: list[dict] = field(default_factory=list)
    identifiers: list[dict] = field(default_factory=list)


@dataclass
class Plan:
    action: str                       # "create" | "already_satisfied"
    store: dict | None = None
    identifier: dict | None = None
    existing_owner: dict | None = None
    evidence: list[dict] = field(default_factory=list)
    checked_stores: list[dict] = field(default_factory=list)
    checked_identifiers: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Evidence and state — read only
# ---------------------------------------------------------------------------

def find_evidence(directory: Path = EVIDENCE_DIR) -> list[dict]:
    """Item Sales exports whose own preamble says `Store: 47708760`."""
    from app.services.store_reference_import import parse_item_sales_summary

    found: list[dict] = []
    for path in sorted(directory.glob("*.xlsx")):
        try:
            parsed = parse_item_sales_summary(path)
        except Exception:  # noqa: BLE001 — not an Item Sales export (e.g. Beer Inventory)
            continue
        if parsed.store_number == STORE_CODE:
            found.append({"file": str(path.relative_to(ROOT)),
                          "preamble": f"Store: {parsed.store_number}"})
    return found


def read_state(connection) -> StoreState:
    cursor = connection.cursor()
    cursor.execute(SELECT_STORES)
    stores = [dict(zip(STORE_COLUMNS, row, strict=True)) for row in cursor.fetchall()]
    cursor.execute(SELECT_IDENTIFIERS)
    identifiers = [dict(zip(IDENTIFIER_COLUMNS, row, strict=True))
                   for row in cursor.fetchall()]
    return StoreState(stores=stores, identifiers=identifiers)


# ---------------------------------------------------------------------------
# The decision — pure
# ---------------------------------------------------------------------------

def new_store_row(evidence: list[dict]) -> dict:
    """The store 0013 would have created for this code, and nothing more."""
    files = ", ".join(e["file"].rsplit("/", 1)[-1] for e in evidence)
    return {
        **dict.fromkeys(IDENTITY_FIELDS),
        "status": STATUS_ACTIVE,
        "identity_status": IDENTITY_UNRESOLVED,
        "notes": (
            f"Known only as Item Sales store code {STORE_CODE}. Name and address not yet "
            "confirmed by a person; nothing in the reference data names the location. "
            f"Created on the pilot by {COMMAND}, mirroring migration 0013's backfill of "
            "source store codes, because the pilot was migrated without the store_number "
            "data that backfill reads. NOT linked to Apple Foods II or RCM — whether this "
            f"code is either location is a human decision that has not been made. "
            f"Source: {files}."
        ),
    }


def new_identifier_row(evidence: list[dict]) -> dict:
    return {
        "source_system": SOURCE_ITEM_SALES,
        "identifier_type": TYPE_STORE_CODE,
        "identifier_value": STORE_CODE,
        "evidence": {
            "origin": f"Item Sales Summary export preamble (Store: {STORE_CODE})",
            "files": [e["file"] for e in evidence],
            "created_by": COMMAND,
            "mirrors": "alembic 0013 store_number backfill",
            "verified": False,
        },
    }


def is_bare_code_store(store: dict, identifiers: list[dict]) -> bool:
    """
    A store that is exactly what this command would create: unresolved, no
    human identity, and identified only by this one source code.
    """
    own = [i for i in identifiers if str(i["store_id"]) == str(store["id"])]
    return (
        store["identity_status"] == IDENTITY_UNRESOLVED
        and not any(store.get(name) for name in IDENTITY_FIELDS)
        and len(own) == 1
        and (own[0]["source_system"], own[0]["identifier_type"],
             own[0]["identifier_value"]) == (SOURCE_ITEM_SALES, TYPE_STORE_CODE, STORE_CODE)
    )


def plan_precondition(state: StoreState, evidence: list[dict]) -> Plan:
    """Decide what, if anything, to write. Refuses rather than guesses."""
    if not evidence:
        raise PreconditionRefused(
            f"no Item Sales export under {EVIDENCE_DIR.relative_to(ROOT)} states "
            f"'Store: {STORE_CODE}'; there is no evidence to create a store from")

    stores_by_id = {str(s["id"]): s for s in state.stores}
    claims = [i for i in state.identifiers
              if (i["source_system"], i["identifier_type"], str(i["identifier_value"]).strip())
              == (SOURCE_ITEM_SALES, TYPE_STORE_CODE, STORE_CODE)]
    if len(claims) > 1:
        # The unique constraint makes this unreachable; refuse anyway.
        raise PreconditionRefused(f"{STORE_CODE} is claimed by {len(claims)} identifiers")
    if claims:
        owner = stores_by_id.get(str(claims[0]["store_id"]))
        if owner is not None and is_bare_code_store(owner, state.identifiers):
            return Plan("already_satisfied", existing_owner=owner, evidence=evidence,
                        checked_stores=state.stores, checked_identifiers=state.identifiers)
        raise PreconditionRefused(
            f"item_sales/store_code/{STORE_CODE} already names store {claims[0]['store_id']} "
            f"({(owner or {}).get('display_name') or 'unnamed'}), which is not a bare "
            "unresolved code store. Refusing to create a second owner or to move it.")

    # A nameless, identifier-less store is ambiguous: it could be a code store
    # whose identifier went missing. Do not add a second one beside it.
    identified = {str(i["store_id"]) for i in state.identifiers}
    orphans = [s for s in state.stores
               if str(s["id"]) not in identified and not any(s.get(n) for n in IDENTITY_FIELDS)]
    if orphans:
        raise PreconditionRefused(
            f"{len(orphans)} existing store(s) have no name and no identifiers "
            f"({', '.join(str(s['id']) for s in orphans)}); resolve that before adding "
            "another unresolved store")

    return Plan("create", store=new_store_row(evidence),
                identifier=new_identifier_row(evidence), evidence=evidence,
                checked_stores=state.stores, checked_identifiers=state.identifiers)


# ---------------------------------------------------------------------------
# The write — one transaction, verified before commit
# ---------------------------------------------------------------------------

def apply_plan(connection, plan: Plan, evidence: list[dict]) -> dict:
    """
    Insert the planned store and identifier in one transaction. The state is
    re-read and re-planned inside it, and the result checked before commit:
    exactly one new store, exactly one new identifier, every existing store
    and identifier unchanged. Anything else rolls back.
    """
    if plan.action != "create":
        return {"written": False}
    assert plan.store is not None and plan.identifier is not None
    connection.set_session(readonly=False, autocommit=False)
    try:
        before = read_state(connection)
        again = plan_precondition(before, evidence)
        if again.action != "create" or again.store != plan.store \
                or again.identifier != plan.identifier:
            raise PreconditionRefused("the store tables changed since the plan was made")

        store_id = str(uuid.uuid4())
        identifier_id = str(uuid.uuid4())
        cursor = connection.cursor()
        cursor.execute(INSERT_STORE, {
            "id": store_id, "status": plan.store["status"],
            "identity_status": plan.store["identity_status"], "notes": plan.store["notes"]})
        cursor.execute(INSERT_IDENTIFIER, {
            "id": identifier_id, "store_id": store_id,
            "source_system": plan.identifier["source_system"],
            "identifier_type": plan.identifier["identifier_type"],
            "identifier_value": plan.identifier["identifier_value"],
            "evidence": json.dumps(plan.identifier["evidence"], sort_keys=True)})

        after = read_state(connection)
        verify_write(before, after, store_id, identifier_id, plan)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return {"written": True, "store_id": store_id, "identifier_id": identifier_id}


def verify_write(before: StoreState, after: StoreState, store_id: str, identifier_id: str,
                 plan: Plan) -> None:
    key = lambda row: str(row["id"])  # noqa: E731
    old_stores = {key(s): s for s in before.stores}
    new_stores = {key(s): s for s in after.stores}
    if set(new_stores) - set(old_stores) != {store_id} or set(old_stores) - set(new_stores):
        raise PreconditionRefused("store rows other than the new one appeared or vanished")
    for sid, row in old_stores.items():
        if new_stores[sid] != row:
            raise PreconditionRefused(f"existing store {sid} changed; rolled back")
    created = new_stores[store_id]
    if created["identity_status"] != IDENTITY_UNRESOLVED or any(
            created.get(n) for n in IDENTITY_FIELDS):
        raise PreconditionRefused("the new store is not a bare unresolved store; rolled back")

    old_ids = {key(i): i for i in before.identifiers}
    new_ids = {key(i): i for i in after.identifiers}
    if set(new_ids) - set(old_ids) != {identifier_id} or set(old_ids) - set(new_ids):
        raise PreconditionRefused("identifier rows other than the new one appeared or vanished")
    for iid, row in old_ids.items():
        if new_ids[iid] != row:
            raise PreconditionRefused(f"existing identifier {iid} changed; rolled back")
    added = new_ids[identifier_id]
    if (str(added["store_id"]), added["source_system"], added["identifier_type"],
            added["identifier_value"]) != (store_id, SOURCE_ITEM_SALES, TYPE_STORE_CODE,
                                           STORE_CODE):
        raise PreconditionRefused("the new identifier is not the planned one; rolled back")


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def print_plan(plan: Plan, *, dry_run: bool) -> None:
    print("Evidence supporting a store for this code:")
    for item in plan.evidence:
        print(f"  {item['file']}  ({item['preamble']})")
    print(f"\nExisting stores checked ({len(plan.checked_stores)}):")
    for store in plan.checked_stores:
        print(f"  {store['id']}  {store['display_name'] or '(no name)'}  "
              f"identity={store['identity_status']}")
    print(f"\nExisting identifiers checked ({len(plan.checked_identifiers)}):")
    for ident in plan.checked_identifiers:
        print(f"  {ident['store_id']}  {ident['source_system']}/{ident['identifier_type']}"
              f" = {ident['identifier_value']!r}")
    if plan.action == "already_satisfied":
        assert plan.existing_owner is not None
        print(f"\nALREADY SATISFIED: {SOURCE_ITEM_SALES}/{TYPE_STORE_CODE}/{STORE_CODE} "
              f"names bare unresolved store {plan.existing_owner['id']}. Nothing to write.")
        return
    assert plan.store is not None and plan.identifier is not None
    print("\nProposed store row (id generated at write time; created_at/updated_at by the database):")
    for name, value in plan.store.items():
        print(f"  {name:<16} {value!r}")
    print("\nProposed store_identifier row (store_id = the new store):")
    for name, value in plan.identifier.items():
        print(f"  {name:<16} {json.dumps(value) if isinstance(value, dict) else repr(value)}")
    print("\nNot touched: Apple Foods II, RCM, every existing identifier, invoices, documents, "
          "Product Master tables, product_case_mappings.")
    print("\nDRY RUN — nothing was written." if dry_run else "")


def write_report(payload: dict) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(payload, indent=2, default=str))
    print(f"Report: {REPORT.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"Give Item Sales store code {STORE_CODE} an unresolved store on the pilot.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show exactly what would be written. Writes nothing.")
    add_pilot_arguments(parser)
    args = parser.parse_args()

    from app.core.config import get_settings

    settings = get_settings()
    target = resolve_seed_target(settings.database_url, settings.database_url_sync, args)
    if target.target != TARGET_PILOT:
        sys.exit("This command prepares the pilot. Pass --pilot-seed with the expected "
                 "database identity.")
    print(f"Verified pilot target: database {target.database!r} on a host matching "
          f"{args.expect_host_contains!r}, Alembic revision {target.revision}.")
    print("Mode: DRY RUN (read only)\n" if args.dry_run else "Mode: WRITE\n")

    import psycopg2

    evidence = find_evidence()
    dsn = settings.database_url_sync.replace("postgresql+psycopg2://", "postgresql://")
    connection = psycopg2.connect(dsn, connect_timeout=10)
    try:
        connection.set_session(readonly=True, autocommit=True)
        plan = plan_precondition(read_state(connection), evidence)
        print_plan(plan, dry_run=args.dry_run)
        result = {"written": False}
        if not args.dry_run and plan.action == "create":
            result = apply_plan(connection, plan, evidence)
            print(f"\nCommitted: store {result['store_id']} with identifier "
                  f"{SOURCE_ITEM_SALES}/{TYPE_STORE_CODE}/{STORE_CODE} "
                  f"({result['identifier_id']}).")
    finally:
        connection.close()

    write_report({
        "at": datetime.now(UTC).isoformat(), "dry_run": bool(args.dry_run),
        "database": target.database, "revision": target.revision,
        "action": plan.action, "store": plan.store, "identifier": plan.identifier,
        "existing_owner": plan.existing_owner, "evidence": plan.evidence,
        "checked_stores": plan.checked_stores, "checked_identifiers": plan.checked_identifiers,
        "result": result,
    })


if __name__ == "__main__":
    main()
