"""
Tests — Store Master reconciliation to the authoritative CStorePro store directory.

No database is needed: the planner is pure, the plan is run against the
Store Infor.pdf transcription (the pilot's three records), and the apply path
runs against an in-memory fake database. What these pin:

  * the directory is the 14 stores exactly as supplied, with the two mandates,
    the three aliases and the protected source identity 47708760;
  * RCM and Apple Foods II are ADOPTED IN PLACE as LG - RCM and PB Wolf: same
    record and store id, display name and address only, previous values kept as
    evidence, every existing identifier kept, identity status untouched —
    nothing is reassigned;
  * 12 canonical stores are created unresolved; MIDLER / WOLF / TIKKI are
    aliases, never stores; emails are contact metadata;
  * 47708760 is never adopted, linked or renamed;
  * duplicate risks and inconsistent mandates stop the plan; reruns are idempotent;
  * alias and directory-name identifiers never feed invoice→store matching;
  * the Store Directory API separates physical stores from source identities;
  * the apply path is one guarded transaction: INSERTs, two guarded UPDATEs,
    no DELETE, and it verifies nothing else changed.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.store import (
    IDENTITY_UNRESOLVED,
    KIND_PHYSICAL,
    KIND_SOURCE_IDENTITY,
    NON_MATCHING_IDENTIFIER_TYPES,
    Store,
    StoreIdentifier,
)
from app.services.store_master_service import (
    ACTION_ADOPT,
    ACTION_CREATE,
    ACTION_DUPLICATE,
    ACTION_KEEP_SOURCE,
    ACTION_PRESENT,
    ACTION_REFUSE,
    ACTION_UNLISTED,
    DIRECTORY,
    load_directory,
    parse_directory,
    plan_findings,
    reconcile,
    street_key,
)

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "tests" / "fixtures" / "store_master" / "pilot_stores_2026-09-29.json"
SCRIPT = (ROOT / "scripts" / "store_master_reconcile.py").read_text()

CANONICAL_NAMES = ["LG - RCM", "AF 429", "PB Wolf", "AF Conklin", "LG - Singh Market", "AF McKinley", "AF Hooper",
                   "Tonopah Texaco", "LG - SGM", "AF 143", "Tonopah Shell", "AF North", "AF MV Tiki", "AF NV Midler"]
ALIAS_CONTACTS = {"aliases": [{"alias": "WOLF", "contact_email": "wolf@example.com", "supplied_at": "2026-09-29"}]}


def directory():
    return parse_directory(json.loads(DIRECTORY.read_text()), ALIAS_CONTACTS)


def pilot():
    return json.loads(SNAPSHOT.read_text())["stores"]


def by_canonical(actions):
    return {a.canonical: a for a in actions if a.canonical}


def ids(action):
    return [(i["source_system"], i["identifier_type"], i["identifier_value"]) for i in action.identifiers]


class TestDirectory:

    def test_the_directory_is_the_fourteen_stores_exactly_as_supplied(self):
        d = load_directory()
        assert [s.name for s in d.stores] == CANONICAL_NAMES
        assert d.canonical("AF McKinley").postal_code == "13760-2917"
        assert d.canonical("LG - RCM").address_line_1 == "1409 E Saint George Blvd"
        assert [(m[0], m[1]) for m in d.reconcile_existing] == [("RCM", "LG - RCM"), ("Apple Foods II", "PB Wolf")]
        assert d.aliases == (("MIDLER", "AF NV Midler"), ("WOLF", "PB Wolf"), ("TIKKI", "AF MV Tiki"))
        assert d.keep_source_identities == (("item_sales", "store_code", "47708760"),)

    def test_the_tracked_directory_holds_no_email_and_no_invented_store_number(self):
        text = DIRECTORY.read_text()
        assert "@" not in text
        for store in json.loads(text)["stores"]:
            assert set(store) == {"name", "address_line_1", "city", "state", "postal_code"}, store

    @pytest.mark.parametrize("mutate,message", [
        (lambda d: d["stores"].append(dict(d["stores"][0])), "duplicate canonical"),
        (lambda d: d["operator_aliases"].append({"alias": "AF 429", "canonical": "PB Wolf"}), "itself a directory"),
        (lambda d: d["operator_aliases"].append({"alias": "X", "canonical": "Nowhere"}), "not a directory store"),
        (lambda d: d["reconcile_existing"].append({"existing_display_name": "X", "canonical": "Nowhere"}), "not a directory"),
        (lambda d: d["stores"][1].update(postal_code="1379"), "ZIP"),
        (lambda d: d["stores"][1].update(address_line_1="800 Wolf Street", postal_code="13208"), "share an address"),
    ])
    def test_an_inconsistent_directory_is_refused(self, mutate, message):
        data = json.loads(DIRECTORY.read_text())
        mutate(data)
        with pytest.raises(ValueError, match=message):
            parse_directory(data)

    def test_street_equivalence(self):
        assert street_key("1409 E Saint George Blvd") == street_key("1409 EAST ST. GEORGE BLVD.") \
            == street_key("1409 E ST GEORGE BLVD")
        assert street_key("800 Wolf St") == street_key("800 WOLF STREET")


class TestPilotReconciliation:

    def plan(self):
        return reconcile(pilot(), directory(), reconciled_by="data-team:reviewer", today="2026-09-29")

    def test_the_plan_adopts_two_creates_twelve_and_keeps_47708760(self):
        actions = self.plan()
        counts = {}
        for a in actions:
            counts[a.action] = counts.get(a.action, 0) + 1
        assert counts == {ACTION_ADOPT: 2, ACTION_CREATE: 12, ACTION_KEEP_SOURCE: 1}
        plan = by_canonical(actions)
        assert (plan["LG - RCM"].store_id, plan["PB Wolf"].store_id) == ("pdf-placeholder:rcm",
                                                                         "pdf-placeholder:apple-foods-ii")

    def test_adoption_changes_only_name_and_address_and_keeps_the_previous_values(self):
        wolf = by_canonical(self.plan())["PB Wolf"]
        assert set(wolf.updates) == {"display_name", "address_line_1", "address_line_2", "city", "state",
                                     "postal_code", "notes"}
        assert wolf.updates["display_name"] == "PB Wolf" and wolf.updates["postal_code"] == "13208"
        assert wolf.previous == {"display_name": "Apple Foods II", "postal_code": "13208-1224"}
        assert "identity_status" not in wolf.updates and "customer_name" not in wolf.updates
        assert "Same record and store id" in wolf.updates["notes"]
        assert wolf.updates["notes"].startswith("Name and address observed"), "existing notes are kept"
        assert ids(wolf) == [("cstorepro", "directory_name", "PB Wolf"),
                             ("store_master", "store_alias", "Apple Foods II"),
                             ("operator", "store_alias", "WOLF")]
        legacy = wolf.identifiers[1]["evidence"]
        assert legacy["alias_kind"] == "legacy_display_name" and "not a physical store name" in legacy["note"]
        assert wolf.identifiers[2]["evidence"] == {"verified": False, "alias_kind": "operator_alias",
                                                   "supplied_at": "2026-09-29", "contact_email": "wolf@example.com"}

    def test_rcm_keeps_its_customer_name_and_every_document_identifier(self):
        actions = self.plan()
        findings = plan_findings(pilot(), actions, directory())
        preserved = findings["evidence_preserved"]["LG - RCM"]
        for value in ("RED CLIFF MARKET", "RED CLIFFS MARKET", "RED CLIFF TEXACO", "RED CLIFF PETROLEUM, LLC",
                      "1409 E ST GEORGE BLVD", "1409 EAST ST. GEORGE BLVD.", "84770", "84790"):
            assert any(p.endswith("/" + value) for p in preserved), value
        rcm = by_canonical(actions)["LG - RCM"]
        assert rcm.updates["city"] == "Saint George" and rcm.previous["city"] == "St George"
        assert findings["store_ids_reassigned"] == 0 and findings["schema_migration_required"] is False
        assert findings["associations_on_adopted_records"]["PB Wolf"]["pending_proposals"] == 7

    def test_new_stores_are_unresolved_with_the_directory_name_and_aliases_never_become_stores(self):
        actions = self.plan()
        created = [a for a in actions if a.action == ACTION_CREATE]
        assert sorted(a.new_store["display_name"] for a in created) == sorted(set(CANONICAL_NAMES) - {"LG - RCM", "PB Wolf"})
        assert {a.new_store["identity_status"] for a in created} == {IDENTITY_UNRESOLVED}
        names = {a.new_store["display_name"] for a in created}
        assert not names & {"MIDLER", "WOLF", "TIKKI", "Apple Foods II", "RCM"}
        plan = by_canonical(actions)
        assert ("operator", "store_alias", "MIDLER") in ids(plan["AF NV Midler"])
        assert ("operator", "store_alias", "TIKKI") in ids(plan["AF MV Tiki"])

    def test_47708760_is_never_adopted_linked_or_renamed(self):
        actions = self.plan()
        keep = [a for a in actions if a.action == ACTION_KEEP_SOURCE]
        assert [a.store_id for a in keep] == ["pdf-placeholder:store-47708760"]
        assert "protected" in keep[0].reasons[0]
        assert not [i for a in actions for i in a.identifiers if i["store_id"] == "pdf-placeholder:store-47708760"]

    def test_a_mandate_pointing_at_the_protected_source_identity_is_refused(self):
        stores = pilot()
        stores[2]["display_name"] = "RCM"                     # someone named the 47708760 record 'RCM'
        stores[0]["display_name"] = "Apple Foods II"
        stores[1]["display_name"] = "Red Cliff (old)"
        rcm = by_canonical(reconcile(stores, directory()))["LG - RCM"]
        assert rcm.action == ACTION_REFUSE

    def test_rerunning_after_apply_is_idempotent(self):
        stores = apply_in_memory(pilot(), self.plan())
        again = reconcile(stores, directory(), today="2026-09-30")
        assert {a.action for a in again if a.canonical} == {ACTION_PRESENT}
        assert [a for a in again if a.identifiers] == []
        assert [a.action for a in again if not a.canonical] == [ACTION_KEEP_SOURCE]

    def test_an_unmandated_store_at_a_canonical_address_is_a_duplicate_risk(self):
        stray = {"id": "stray", "display_name": "Apple Foods 429", "customer_name": None,
                 "address_line_1": "429 Riverside", "address_line_2": None, "city": "Johnson City", "state": "NY",
                 "postal_code": "13790", "status": "active", "identity_status": "unresolved", "notes": None,
                 "identifiers": []}
        actions = reconcile([*pilot(), stray], directory())
        plan = by_canonical(actions)
        assert plan["AF 429"].action == ACTION_DUPLICATE and plan["AF 429"].new_store is None
        assert [a.store_id for a in actions if a.action == ACTION_UNLISTED] == ["stray"], "reported for a person"

    def test_a_mandated_record_without_the_canonical_address_is_refused(self):
        stores = copy.deepcopy(pilot())
        stores[1]["address_line_1"] = "10 Somewhere Else"
        stores[1]["identifiers"] = [i for i in stores[1]["identifiers"] if i["identifier_type"] != "address_line"]
        assert by_canonical(reconcile(stores, directory()))["LG - RCM"].action == ACTION_REFUSE

    def test_a_missing_mandated_record_is_refused_not_recreated(self):
        stores = [s for s in pilot() if s["display_name"] != "RCM"]
        rcm = by_canonical(reconcile(stores, directory()))["LG - RCM"]
        assert rcm.action == ACTION_REFUSE and rcm.new_store is None

    def test_a_named_store_outside_the_directory_is_reported_and_left_alone(self):
        other = {"id": "other", "display_name": "Unknown Market", "customer_name": None,
                 "address_line_1": "1 Nowhere Rd", "address_line_2": None, "city": None, "state": None,
                 "postal_code": "00000", "status": "active", "identity_status": "unresolved", "notes": None,
                 "identifiers": []}
        actions = reconcile([*pilot(), other], directory())
        assert [a.store_id for a in actions if a.action == ACTION_UNLISTED] == ["other"]


def apply_in_memory(stores, actions):
    """What the apply path does, as data: adoptions updated, stores and identifiers inserted."""
    out = copy.deepcopy(stores)
    by_id = {s["id"]: s for s in out}
    for a in actions:
        if a.action == ACTION_CREATE:
            out.append({**a.new_store, "identifiers": []})
            by_id[a.store_id] = out[-1]
        if a.action == ACTION_ADOPT:
            by_id[a.store_id].update(a.updates)
        for i in a.identifiers:
            by_id[i["store_id"]]["identifiers"].append({k: i[k] for k in ("source_system", "identifier_type",
                                                                        "identifier_value", "evidence")})
    return out


class TestMatchingAndDirectoryApi:

    def test_aliases_and_directory_names_never_feed_invoice_matching(self):
        from app.services.store_identification_service import match_stores

        store = Store(id=uuid.uuid4(), identity_status=IDENTITY_UNRESOLVED)
        store.identifiers = [
            StoreIdentifier(source_system="operator", identifier_type="store_alias", identifier_value="TIKKI"),
            StoreIdentifier(source_system="store_master", identifier_type="store_alias",
                            identifier_value="APPLEFOODS2"),
            StoreIdentifier(source_system="cstorepro", identifier_type="directory_name", identifier_value="AFMVTIKI"),
        ]
        assert match_stores("TIKKI APPLEFOODS2 AFMVTIKI", [store]) == []
        assert {"store_alias", "directory_name"} == NON_MATCHING_IDENTIFIER_TYPES

    def test_kind_separates_physical_stores_from_source_identities(self):
        code = Store(id=uuid.uuid4(), identity_status=IDENTITY_UNRESOLVED)
        code.identifiers = [StoreIdentifier(source_system="item_sales", identifier_type="store_code",
                                            identifier_value="47708760")]
        assert (code.kind, code.in_store_directory) == (KIND_SOURCE_IDENTITY, False)
        listed = Store(id=uuid.uuid4(), identity_status=IDENTITY_UNRESOLVED, display_name="AF 429")
        listed.identifiers = [StoreIdentifier(source_system="cstorepro", identifier_type="directory_name",
                                              identifier_value="AF 429")]
        assert (listed.kind, listed.in_store_directory) == (KIND_PHYSICAL, True)

    def test_the_directory_api_carries_kind_and_directory_membership(self):
        from app.api.v1.stores import _entry

        code = Store(id=uuid.uuid4(), identity_status=IDENTITY_UNRESOLVED, status="active")
        code.identifiers = [StoreIdentifier(source_system="item_sales", identifier_type="store_code",
                                            identifier_value="47708760")]
        entry = _entry(code, {})
        assert (entry.kind, entry.in_store_directory, entry.label) == (
            "source_identity", False, "Store 47708760 (location not yet confirmed)")


class FakeDB:
    """A tiny in-memory stand-in for the two tables and the association counts."""

    def __init__(self, stores):
        self.stores = {s["id"]: {k: s.get(k) for k in ("id", "display_name", "customer_name", "address_line_1",
                                                       "address_line_2", "city", "state", "postal_code", "status",
                                                       "identity_status", "notes")} for s in stores}
        self.identifiers = [(s["id"], i["source_system"], i["identifier_type"], i["identifier_value"], i["evidence"])
                            for s in stores for i in s["identifiers"]]
        self.counts = {s["id"]: s.get("counts") or {} for s in stores}
        self.invoices: list[tuple] = []
        self.document_statuses: list[tuple] = []
        self.proposals: list[tuple] = []
        self.commercial: list[tuple] = []
        self.statements, self.committed, self.rolled_back = [], False, False

    def set_session(self, **kw):
        self.session = kw

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class FakeCursor:
    def __init__(self, db):
        self.db, self.rows, self.rowcount = db, [], 0

    def execute(self, sql, params=None):
        db = self.db
        db.statements.append(sql)
        cols = ("id", "display_name", "customer_name", "address_line_1", "address_line_2", "city", "state",
                "postal_code", "status", "identity_status", "notes")
        if sql.startswith("SELECT id, display_name"):
            self.rows = [tuple(s[c] for c in cols) for s in db.stores.values()]
        elif sql.startswith("SELECT store_id, source_system"):
            self.rows = list(db.identifiers)
        elif sql.startswith("SELECT store_id, id, invoice_number"):
            self.rows = list(db.invoices)
        elif sql.startswith("SELECT store_id, status, count(*) FROM documents"):
            self.rows = list(db.document_statuses)
        elif sql.startswith("SELECT store_id, id, entity_type"):
            self.rows = list(db.proposals)
        elif sql.startswith("SELECT store_id, approval_state"):
            self.rows = list(db.commercial)
        elif sql.startswith("SELECT store_id, count(*)"):
            key = next(k for k, q in __import__("scripts.store_master_reconcile", fromlist=["x"]).COUNT_QUERIES.items()
                       if q == sql)
            self.rows = [(sid, c[key]) for sid, c in db.counts.items() if key in c]
        elif sql.startswith("INSERT INTO stores"):
            db.stores[params["id"]] = {c: params[c] for c in cols}
        elif sql.startswith("INSERT INTO store_identifiers"):
            db.identifiers.append((params["store_id"], params["source_system"], params["identifier_type"],
                                   params["identifier_value"], json.loads(params["evidence"])))
        elif sql.startswith("UPDATE stores"):
            store = db.stores.get(params["id"])
            self.rowcount = 0
            if store and store["display_name"] == params["expected_display_name"]:
                for c in ("display_name", "address_line_1", "address_line_2", "city", "state", "postal_code", "notes"):
                    store[c] = params[c]
                self.rowcount = 1
        else:
            raise AssertionError(f"unexpected statement: {sql}")

    def fetchall(self):
        return self.rows


class TestApplyPath:

    def test_the_script_selects_inserts_and_only_guarded_updates_of_stores(self):
        statements = re.findall(r'"(SELECT|INSERT|UPDATE|DELETE)\b[^"]*', SCRIPT)
        assert set(statements) == {"SELECT", "INSERT", "UPDATE"}
        update = re.search(r'UPDATE_ADOPTED = \((.*?)\)\n', SCRIPT, re.S).group(1)
        assert "UPDATE stores SET" in update and "identity_status" not in update and "store_id" not in update
        assert "WHERE id = %(id)s AND display_name = %(expected_display_name)s" in update
        assert "UPDATE invoices" not in SCRIPT and "UPDATE documents" not in SCRIPT
        assert "resolve_seed_target" in SCRIPT and "--reconciled-by" in SCRIPT

    def test_apply_writes_the_plan_in_one_verified_transaction(self):
        import scripts.store_master_reconcile as script

        db = FakeDB(pilot())
        planned = reconcile(pilot(), directory(), reconciled_by="data-team:reviewer")
        written = script.apply(db, directory(), planned, "data-team:reviewer")
        assert db.committed and not db.rolled_back
        assert len(written["stores_created"]) == 12 and len(written["stores_adopted"]) == 2
        assert db.stores["pdf-placeholder:apple-foods-ii"]["display_name"] == "PB Wolf"
        assert db.stores["pdf-placeholder:apple-foods-ii"]["identity_status"] == "unresolved"
        assert db.stores["pdf-placeholder:store-47708760"]["display_name"] is None
        assert not [s for s in db.statements if s.startswith("DELETE")]

    def test_apply_refuses_a_plan_with_a_duplicate_risk_before_writing(self):
        import scripts.store_master_reconcile as script

        stray = {"id": "stray", "display_name": "Apple Foods 429", "customer_name": None,
                 "address_line_1": "429 Riverside", "address_line_2": None, "city": None, "state": None,
                 "postal_code": "13790", "status": "active", "identity_status": "unresolved", "notes": None,
                 "identifiers": []}
        stores = [*pilot(), stray]
        db = FakeDB(stores)
        with pytest.raises(RuntimeError, match="duplicate risk"):
            script.apply(db, directory(), reconcile(stores, directory()), "x")
        assert db.statements == []

    def test_apply_refuses_when_the_store_master_changed_since_the_report(self):
        import scripts.store_master_reconcile as script

        planned = reconcile(pilot(), directory(), reconciled_by="x")
        changed = copy.deepcopy(pilot())
        changed[0]["display_name"] = "Apple Foods II (renamed meanwhile)"
        db = FakeDB(changed)
        with pytest.raises(RuntimeError):
            script.apply(db, directory(), planned, "x")
        assert db.rolled_back and not db.committed
        assert not [s for s in db.statements if s.startswith(("INSERT", "UPDATE"))]

    def test_the_real_alias_evidence_stays_gitignored(self):
        assert subprocess.run(["git", "check-ignore", "-q", "data/reference/operator_store_evidence.json"],
                              cwd=ROOT).returncode == 0

    def test_the_superseded_operator_import_is_gone(self):
        assert not (ROOT / "scripts" / "store_master_operator_import.py").exists()


class TestLiveReadOnlyReport:

    def fake_db(self):
        db = FakeDB(pilot())
        db.invoices = [("pdf-placeholder:apple-foods-ii", uuid.UUID(int=11), "101497", "REVIEW_REQUIRED", None),
                       (None, uuid.UUID(int=12), "7174", "STORE_PENDING", None)]
        db.document_statuses = [("pdf-placeholder:apple-foods-ii", "COMPLETED", 2)]
        db.proposals = [("pdf-placeholder:apple-foods-ii", uuid.UUID(int=21), "case_mapping", "02800077212",
                         "PENDING", "operator_entered")]
        db.commercial = [("pdf-placeholder:store-47708760", "REVIEW_REQUIRED", 414)]
        return db

    def test_relationships_are_read_per_store(self):
        import scripts.store_master_reconcile as script

        rel = script.read_relationships(self.fake_db().cursor())
        assert rel["pdf-placeholder:apple-foods-ii"]["invoices"][0]["invoice_number"] == "101497"
        assert rel["(no store)"]["invoices"][0]["status"] == "STORE_PENDING"
        assert rel["pdf-placeholder:apple-foods-ii"]["proposals"][0]["status"] == "PENDING"
        assert rel["pdf-placeholder:store-47708760"]["commercial_mappings_by_state"] == {"REVIEW_REQUIRED": 414}

    def test_the_differences_from_the_pdf_snapshot_are_reported(self):
        from app.services.store_master_service import compare_snapshots

        live = copy.deepcopy(pilot())
        live[0]["postal_code"] = "13208"
        live[1]["identifiers"].append({"source_system": "document", "identifier_type": "customer_name",
                                       "identifier_value": "RCM TEXACO", "evidence": {}})
        diff = compare_snapshots(live, pilot())
        assert diff["differences"]["Apple Foods II"]["postal_code"] == {"live": "13208", "reference": "13208-1224"}
        assert diff["differences"]["RCM"]["identifiers"]["only_live"] == [("document", "customer_name", "RCM TEXACO")]
        assert diff["only_live"] == [] and diff["only_reference"] == []

    def test_a_live_report_run_is_read_only_and_never_applies(self, monkeypatch, tmp_path):
        import sys

        import psycopg2

        import scripts.store_master_reconcile as script

        db = self.fake_db()
        monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: db)
        monkeypatch.setattr(script, "resolve_seed_target",
                            lambda *a, **k: SimpleNamespace(target="pilot", host="db.x.supabase.co"))
        monkeypatch.setattr(script, "REPORT", tmp_path / "report.json")
        monkeypatch.setattr(script, "apply", lambda *a, **k: pytest.fail("the report must never apply"))
        monkeypatch.setattr(db, "close", lambda: None, raising=False)
        monkeypatch.setattr(sys, "argv", ["store_master_reconcile.py", "--pilot-seed", "--expect-database", "postgres",
                                          "--expect-host-contains", "supabase"])
        script.main()
        assert db.session == {"readonly": True, "autocommit": True}
        assert db.statements and all(sql.startswith("SELECT") for sql in db.statements)
        assert not db.committed
        report = json.loads((tmp_path / "report.json").read_text())
        assert report["applied"] is False and report["written"] == {}
        assert report["findings"]["relationships"]["pdf-placeholder:store-47708760"][
            "commercial_mappings_by_state"] == {"REVIEW_REQUIRED": 414}
        assert "differences_from_pdf_snapshot" in report["findings"]


class TestPostWriteAudit:

    def written_state(self):
        """Pre-write baseline (as the read-only report records it), then the approved write applied."""
        import scripts.store_master_reconcile as script

        db = TestLiveReadOnlyReport().fake_db()
        db.close = lambda: None
        stores = script.read_snapshot(db.cursor())
        actions = reconcile(stores, directory())
        findings = plan_findings(stores, actions, directory())
        findings["relationships"] = script.read_relationships(db.cursor())
        baseline = {"applied": False, "findings": findings, "actions": [a.__dict__ for a in actions]}
        script.apply(db, directory(), reconcile(stores, directory(), reconciled_by="shashwat"), "shashwat")
        return db, baseline

    def run_audit(self, db, baseline):
        import scripts.store_master_reconcile as script
        from scripts.store_master_post_write_audit import audit

        return audit(script.read_snapshot(db.cursor()), script.read_relationships(db.cursor()), baseline)

    def test_every_check_passes_after_the_approved_write(self):
        db, baseline = self.written_state()
        checks = self.run_audit(db, baseline)
        assert [n for n, r in checks.items() if not r["ok"]] == []
        assert len(checks) == 12

    def test_touching_47708760_fails_the_audit(self):
        db, baseline = self.written_state()
        db.stores["pdf-placeholder:store-47708760"]["display_name"] = "LG - RCM (linked)"
        assert not self.run_audit(db, baseline)["source_identity_47708760_unchanged_and_unlinked"]["ok"]

    def test_losing_a_pre_write_identifier_fails_the_audit(self):
        db, baseline = self.written_state()
        db.identifiers = [i for i in db.identifiers if i[3] != "RED CLIFF TEXACO"]
        assert not self.run_audit(db, baseline)["pre_write_identifiers_preserved"]["ok"]

    def test_a_moved_relationship_fails_the_audit(self):
        db, baseline = self.written_state()
        db.invoices = [(None if sid else sid, *rest) for sid, *rest in db.invoices]
        assert not self.run_audit(db, baseline)["relationships_intact"]["ok"]

    def test_apply_refuses_any_plan_other_than_the_approved_report(self):
        import scripts.store_master_reconcile as script

        stores = pilot()
        approved = {"applied": False, "source": "database (pilot, read-only)",
                    "actions": [a.__dict__ for a in reconcile(stores, directory())]}
        script.check_against_approved(reconcile(stores, directory(), reconciled_by="shashwat"), approved)
        changed = copy.deepcopy(stores)
        changed[1]["identifiers"].append({"source_system": "document", "identifier_type": "customer_name",
                                          "identifier_value": "NEW NAME", "evidence": {}})
        changed.append({"id": "late", "display_name": None, "customer_name": None, "address_line_1": None,
                        "address_line_2": None, "city": None, "state": None, "postal_code": None,
                        "status": "active", "identity_status": "unresolved", "notes": None,
                        "identifiers": [{"source_system": "item_sales", "identifier_type": "store_code",
                                         "identifier_value": "99999999", "evidence": {}}]})
        with pytest.raises(RuntimeError, match="differs from the approved"):
            script.check_against_approved(reconcile(changed, directory()), approved)
        with pytest.raises(RuntimeError, match="read-only database report"):
            script.check_against_approved(reconcile(stores, directory()), {**approved, "applied": True})

    def test_an_apply_run_never_overwrites_the_read_only_report(self):
        import scripts.store_master_reconcile as script

        assert script.APPLY_REPORT != script.REPORT
        assert "report_path = APPLY_REPORT if args.apply else REPORT" in SCRIPT
        assert 'written["actions"] = [a.__dict__ for a in again]' in SCRIPT


def test_no_production_path_calls_the_reconciliation():
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "app").rglob("*.py")
                 if p.name != "store_master_service.py" and re.search(r"store_master_service|reconcile\(", p.read_text())]
    assert offenders == []
    assert SimpleNamespace  # noqa: B018 — imported for the fakes above
