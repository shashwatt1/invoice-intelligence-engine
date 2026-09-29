"""
Store Master — reconciliation to the authoritative store directory — app/services/store_master_service.py

Pure planning for the governed Store Master workflow. It compares a read-only
snapshot of the stores (and their identifiers) with the operator's
authoritative CStorePro store directory (data/store_master/
cstorepro_store_directory.json) and decides, store by store, what may be
written. It writes nothing; scripts/store_master_reconcile.py reports the plan
read-only and applies it only on request, in one transaction.

It uses the existing mechanism — stores + store_identifiers — not a second
Store Master and not a parallel alias table:

  * a canonical store carries the identifier cstorepro / directory_name /
    <directory name>, which is how the directory names it (no store numbers
    were supplied and none are invented);
  * the name a record was previously shown under (e.g. "Apple Foods II") and
    the operator's aliases (MIDLER, WOLF, TIKKI) are store_alias identifiers,
    with evidence saying which kind — the invoice matcher never reads them;
  * every existing identifier stays exactly where it is.

Rules, all enforced here:

  * An existing record the operator mandated (RCM -> LG - RCM, Apple Foods II
    -> PB Wolf) is ADOPTED IN PLACE: its display name and address take the
    directory's values, its previous values are kept as evidence, and nothing
    else moves. Its store id — and so every invoice, document, proposal and
    mapping pointing at it — is unchanged; no store_id is reassigned anywhere.
    Adoption is refused unless the record's own address evidence carries the
    canonical street and postal code.
  * Identity status is never changed. Adopted records keep theirs; new stores
    are `unresolved`. Confirmation stays the existing, explicit workflow.
  * Any other existing store whose street line matches a canonical store is a
    duplicate risk: nothing is created for that canonical store until a
    person decides.
  * A record known only by a source-system code — Item Sales store 47708760 —
    is a source identity, not a physical store. It is never adopted, linked,
    renamed or used as a target; Product Master mappings on it are untouched.
  * Aliases never create stores. An email is contact metadata only.
  * Reruns are idempotent: a store already holding its directory name is left
    as it is (drift is reported, not overwritten).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.store import (
    IDENTITY_UNRESOLVED,
    SOURCE_CSTOREPRO,
    SOURCE_OPERATOR,
    SOURCE_STORE_MASTER,
    STATUS_ACTIVE,
    TYPE_ADDRESS_LINE,
    TYPE_DIRECTORY_NAME,
    TYPE_POSTAL_CODE,
    TYPE_STORE_ALIAS,
)
from app.services.store_identification_service import normalize_text

DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "store_master" / "cstorepro_store_directory.json"

ACTION_CREATE = "create_canonical_store"
ACTION_ADOPT = "adopt_existing_record"
ACTION_PRESENT = "already_reconciled"
ACTION_DUPLICATE = "duplicate_risk_needs_decision"
ACTION_REFUSE = "refused"
ACTION_KEEP_SOURCE = "keep_unresolved_source_identity"
ACTION_UNLISTED = "named_store_not_in_directory"

ADDRESS_COLUMNS = ("address_line_1", "address_line_2", "city", "state", "postal_code")

# Equivalent street words — for MATCHING only; stored values keep the operator's text.
_STREET_WORDS = {"AVENUE": "AVE", "AV": "AVE", "STREET": "ST", "SAINT": "ST", "ROAD": "RD", "HIGHWAY": "HWY",
                 "DRIVE": "DR", "BOULEVARD": "BLVD", "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
                 "LANE": "LN", "ROUTE": "RT", "PLACE": "PL", "COURT": "CT"}


def street_key(text: str | None) -> str:
    """'1409 E Saint George Blvd' ~ '1409 EAST ST. GEORGE BLVD.' — for matching only."""
    return " ".join(_STREET_WORDS.get(w, w) for w in normalize_text(text).split())


def postal_key(text: str | None) -> str:
    return re.sub(r"\D", "", text or "")[:5]


@dataclass(frozen=True)
class CanonicalStore:
    name: str
    address_line_1: str
    city: str
    state: str
    postal_code: str


@dataclass(frozen=True)
class Directory:
    source: str
    supplied_at: str
    stores: tuple[CanonicalStore, ...]
    reconcile_existing: tuple[tuple[str, str, str], ...]          # (existing display name, canonical, why)
    keep_source_identities: tuple[tuple[str, str, str], ...]      # (source_system, identifier_type, value)
    aliases: tuple[tuple[str, str], ...]                          # (alias, canonical)
    alias_contacts: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def canonical(self, name: str) -> CanonicalStore:
        return next(s for s in self.stores if s.name == name)


def parse_directory(data: Mapping[str, Any], alias_evidence: Mapping[str, Any] | None = None) -> Directory:
    """Validate the directory. Anything inconsistent is an error, never a default."""
    stores = []
    for row in data["stores"]:
        for required in ("name", "address_line_1", "city", "state", "postal_code"):
            if not str(row.get(required) or "").strip():
                raise ValueError(f"directory store is missing {required!r}: {row}")
        if not re.fullmatch(r"\d{5}(?:-\d{4})?", row["postal_code"]):
            raise ValueError(f"postal_code must be a US ZIP: {row['postal_code']!r}")
        stores.append(CanonicalStore(row["name"], row["address_line_1"], row["city"], row["state"], row["postal_code"]))
    names = [s.name for s in stores]
    if len(names) != len(set(names)):
        raise ValueError("duplicate canonical store names")
    addresses = [(street_key(s.address_line_1), postal_key(s.postal_code)) for s in stores]
    if len(addresses) != len(set(addresses)):
        raise ValueError("two canonical stores share an address")
    mandates = tuple((m["existing_display_name"], m["canonical"], m.get("why", "")) for m in data.get("reconcile_existing", ()))
    aliases = tuple((a["alias"], a["canonical"]) for a in data.get("operator_aliases", ()))
    for _, canonical, _ in mandates:
        if canonical not in names:
            raise ValueError(f"reconciliation target {canonical!r} is not a directory store")
    for alias, canonical in aliases:
        if canonical not in names:
            raise ValueError(f"alias {alias!r} names {canonical!r}, which is not a directory store")
        if alias in names:
            raise ValueError(f"alias {alias!r} is itself a directory store name")
    keep = tuple((k["source_system"], k["identifier_type"], k["identifier_value"])
                 for k in data.get("keep_unresolved_source_identities", ()))
    contacts = {row["alias"]: row for row in (alias_evidence or {}).get("aliases", ())}
    return Directory(source=data.get("source", "store directory"), supplied_at=data.get("supplied_at", ""),
                     stores=tuple(stores), reconcile_existing=mandates, keep_source_identities=keep,
                     aliases=aliases, alias_contacts=contacts)


def load_directory(path: Path = DIRECTORY, alias_evidence_path: Path | None = None) -> Directory:
    evidence = json.loads(alias_evidence_path.read_text()) if alias_evidence_path and alias_evidence_path.exists() else None
    return parse_directory(json.loads(path.read_text()), evidence)


@dataclass
class StoreAction:
    action: str
    canonical: str | None = None
    store_id: str | None = None
    updates: dict[str, Any] = field(default_factory=dict)
    previous: dict[str, Any] = field(default_factory=dict)
    new_store: dict[str, Any] | None = None
    identifiers: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def _is_source_identity(store: Mapping[str, Any]) -> bool:
    has_directory = any(i["source_system"] == SOURCE_CSTOREPRO and i["identifier_type"] == TYPE_DIRECTORY_NAME
                        for i in store["identifiers"])
    return not (has_directory or store.get("display_name") or store.get("address_line_1"))


def _streets_postals(store: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    streets = {street_key(i["identifier_value"]) for i in store["identifiers"] if i["identifier_type"] == TYPE_ADDRESS_LINE}
    postals = {postal_key(i["identifier_value"]) for i in store["identifiers"] if i["identifier_type"] == TYPE_POSTAL_CODE}
    if store.get("address_line_1"):
        streets.add(street_key(store["address_line_1"]))
    if store.get("postal_code"):
        postals.add(postal_key(store["postal_code"]))
    return streets - {""}, postals - {""}


def _ident(store_id: str, source: str, id_type: str, value: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(uuid.uuid4()), "store_id": store_id, "source_system": source, "identifier_type": id_type,
            "identifier_value": value, "evidence": evidence}


def reconcile(
    stores: Sequence[Mapping[str, Any]], directory: Directory, *, reconciled_by: str | None = None,
    today: str | None = None,
) -> list[StoreAction]:
    """
    The reconciliation plan. `stores` is a snapshot: dicts with id, display_name,
    customer_name, the address columns, identity_status, notes, identifiers
    [{source_system, identifier_type, identifier_value, evidence}] and optional counts.
    """
    today = today or datetime.now(UTC).date().isoformat()
    by_id = {str(s["id"]): s for s in stores}
    taken = {(i["source_system"], i["identifier_type"], i["identifier_value"]): str(s["id"])
             for s in stores for i in s["identifiers"]}
    protected = {str(s["id"]) for s in stores
                 if any((i["source_system"], i["identifier_type"], i["identifier_value"]) in directory.keep_source_identities
                        for i in s["identifiers"])}
    mandates = {canonical: (existing, why) for existing, canonical, why in directory.reconcile_existing}
    actions: list[StoreAction] = []
    target_of: dict[str, str] = {}
    touched: set[str] = set()

    for c in directory.stores:
        directory_key = (SOURCE_CSTOREPRO, TYPE_DIRECTORY_NAME, c.name)
        holder = taken.get(directory_key)
        if holder is not None:
            a = StoreAction(ACTION_PRESENT, c.name, holder, counts=dict(by_id[holder].get("counts") or {}))
            current = by_id[holder]
            drift = {k: current.get(k) for k in ("display_name", *ADDRESS_COLUMNS)
                     if current.get(k) != {"display_name": c.name, "address_line_1": c.address_line_1,
                                           "address_line_2": None, "city": c.city, "state": c.state,
                                           "postal_code": c.postal_code}[k]}
            if drift:
                a.reasons.append(f"fields differ from the directory (reported, not overwritten): {drift}")
            actions.append(a)
            target_of[c.name] = holder
            touched.add(holder)
            continue

        street, postal = street_key(c.address_line_1), postal_key(c.postal_code)
        if c.name in mandates:
            existing_name, why = mandates[c.name]
            matches = [s for s in stores if (s.get("display_name") or "") == existing_name]
            if len(matches) != 1:
                actions.append(StoreAction(ACTION_REFUSE, c.name, reasons=[
                    f"mandated existing record {existing_name!r} found {len(matches)} times; nothing written for "
                    f"{c.name!r}"]))
                continue
            record = matches[0]
            rid = str(record["id"])
            streets, postals = _streets_postals(record)
            if rid in protected:
                actions.append(StoreAction(ACTION_REFUSE, c.name, rid, reasons=[
                    "the mandated record carries a protected source identity; refused"]))
                continue
            if street not in streets or postal not in postals:
                actions.append(StoreAction(ACTION_REFUSE, c.name, rid, reasons=[
                    f"the record {existing_name!r} does not carry the canonical address ({c.address_line_1}, "
                    f"{c.postal_code}) in its own evidence; refused"]))
                continue
            updates = {"display_name": c.name, "address_line_1": c.address_line_1, "address_line_2": None,
                       "city": c.city, "state": c.state, "postal_code": c.postal_code}
            previous = {k: record.get(k) for k in updates if record.get(k) != updates[k]}
            stamp = (f"Reconciled {today}{' by ' + reconciled_by if reconciled_by else ''} to the {directory.source} "
                     f"store {c.name!r} (previously shown as {existing_name!r}"
                     + (f"; previous address {', '.join(str(previous[k]) for k in ADDRESS_COLUMNS if previous.get(k))}"
                        if any(k in previous for k in ADDRESS_COLUMNS) else "")
                     + f"). {why}. Same record and store id: no invoice, document, proposal or mapping moved. "
                       "Identity status unchanged by this reconciliation.")
            notes = f"{record['notes']}\n{stamp}" if record.get("notes") else stamp
            a = StoreAction(ACTION_ADOPT, c.name, rid, updates={**updates, "notes": notes}, previous=previous,
                            counts=dict(record.get("counts") or {}))
            a.identifiers.append(_ident(rid, *directory_key, {"verified": False, "source": directory.source,
                                                              "supplied_at": directory.supplied_at}))
            legacy = (SOURCE_STORE_MASTER, TYPE_STORE_ALIAS, existing_name)
            if legacy not in taken:
                a.identifiers.append(_ident(rid, *legacy, {
                    "verified": False, "alias_kind": "legacy_display_name",
                    "note": "the name this record was shown under before reconciliation; not a physical store name",
                    "previous": previous, "reconciled_at": today, "reconciled_by": reconciled_by}))
            a.reasons.append(f"operator mandate: {existing_name!r} is the {c.name!r} location; adopted in place")
            actions.append(a)
            target_of[c.name] = rid
            touched.add(rid)
            continue

        mandated_names = {existing for existing, _, _ in directory.reconcile_existing}
        risks = [s for s in stores if str(s["id"]) not in touched and not _is_source_identity(s)
                 and (s.get("display_name") or "") not in mandated_names and street in _streets_postals(s)[0]]
        if risks:
            actions.append(StoreAction(ACTION_DUPLICATE, c.name, reasons=[
                f"existing store(s) {[str(s['id']) for s in risks]} already carry the street {c.address_line_1!r}; "
                "nothing is created until a person decides which record is this location"]))
            continue
        new_id = str(uuid.uuid4())
        new_store = {"id": new_id, "display_name": c.name, "customer_name": None, "address_line_1": c.address_line_1,
                     "address_line_2": None, "city": c.city, "state": c.state, "postal_code": c.postal_code,
                     "status": STATUS_ACTIVE, "identity_status": IDENTITY_UNRESOLVED,
                     "notes": (f"Created {today}{' by ' + reconciled_by if reconciled_by else ''} from the "
                               f"{directory.source} (supplied {directory.supplied_at}). Name and address are the "
                               "directory's; identity is not confirmed until a person confirms it.")}
        a = StoreAction(ACTION_CREATE, c.name, new_id, new_store=new_store)
        a.identifiers.append(_ident(new_id, *directory_key, {"verified": False, "source": directory.source,
                                                            "supplied_at": directory.supplied_at}))
        actions.append(a)
        target_of[c.name] = new_id

    by_canonical = {a.canonical: a for a in actions if a.canonical}
    for alias, canonical in directory.aliases:
        key = (SOURCE_OPERATOR, TYPE_STORE_ALIAS, alias)
        target = target_of.get(canonical)
        owner = taken.get(key)
        home = by_canonical.get(canonical)
        if home is None or target is None:
            if home is not None:
                home.reasons.append(f"alias {alias!r} not recorded: {canonical!r} has no store yet")
            continue
        if owner == target:
            continue                                  # already recorded
        if owner is not None:
            home.reasons.append(f"alias {alias!r} already names store {owner}; not moved")
            continue
        contact = directory.alias_contacts.get(alias, {})
        evidence = {"verified": False, "alias_kind": "operator_alias", "supplied_at": contact.get("supplied_at")}
        if contact.get("contact_email"):
            evidence["contact_email"] = contact["contact_email"]          # contact metadata only
        home.identifiers.append(_ident(target, *key, evidence))

    for store in stores:
        sid = str(store["id"])
        if sid in touched or sid in target_of.values():
            continue
        if _is_source_identity(store):
            codes = [f"{i['source_system']}/{i['identifier_type']}/{i['identifier_value']}" for i in store["identifiers"]]
            reason = ("protected: must stay unresolved and unlinked (human/data-team decision)" if sid in protected
                      else "known only by a source-system code; not a physical store")
            actions.append(StoreAction(ACTION_KEEP_SOURCE, None, sid, reasons=[reason, *codes],
                                       counts=dict(store.get("counts") or {})))
        else:
            actions.append(StoreAction(ACTION_UNLISTED, None, sid, counts=dict(store.get("counts") or {}), reasons=[
                f"{store.get('display_name')!r} is not in the store directory and no mandate names it; left as is "
                "for a person to reconcile"]))
    return actions


def compare_snapshots(live: Sequence[Mapping[str, Any]], reference: Sequence[Mapping[str, Any]]) -> dict:
    """Differences between the live stores and a reference snapshot (e.g. the Store Infor.pdf transcription)."""
    def key(store):
        return store.get("display_name") or ",".join(sorted(f"{i['source_system']}/{i['identifier_value']}"
                                                            for i in store["identifiers"]))
    live_by, ref_by = {key(s): s for s in live}, {key(s): s for s in reference}
    out: dict[str, Any] = {"only_live": sorted(set(live_by) - set(ref_by)),
                           "only_reference": sorted(set(ref_by) - set(live_by)), "differences": {}}
    for name in sorted(set(live_by) & set(ref_by)):
        a, b = live_by[name], ref_by[name]
        diff: dict[str, Any] = {}
        for column in ("display_name", "customer_name", *ADDRESS_COLUMNS, "status", "identity_status"):
            if (a.get(column) or None) != (b.get(column) or None):
                diff[column] = {"live": a.get(column), "reference": b.get(column)}
        ids_a = {(i["source_system"], i["identifier_type"], i["identifier_value"]) for i in a["identifiers"]}
        ids_b = {(i["source_system"], i["identifier_type"], i["identifier_value"]) for i in b["identifiers"]}
        if ids_a != ids_b:
            diff["identifiers"] = {"only_live": sorted(ids_a - ids_b), "only_reference": sorted(ids_b - ids_a)}
        counts_a, counts_b = a.get("counts") or {}, b.get("counts") or {}
        shared = set(counts_a) & set(counts_b)
        if any(counts_a[k] != counts_b[k] for k in shared):
            diff["counts"] = {k: {"live": counts_a[k], "reference": counts_b[k]} for k in shared
                              if counts_a[k] != counts_b[k]}
        if (a.get("notes") or "").strip() != (b.get("notes") or "").strip():
            diff["notes"] = "differ (the reference transcription abbreviates notes)"
        if diff:
            out["differences"][name] = diff
    return out


def plan_findings(stores: Sequence[Mapping[str, Any]], actions: Sequence[StoreAction], directory: Directory) -> dict:
    """The read-only reconciliation report."""
    by_action: dict[str, list[dict]] = {}
    for a in actions:
        by_action.setdefault(a.action, []).append(asdict(a))
    adopted = [a for a in actions if a.action == ACTION_ADOPT]
    return {
        "current_stores": [{"id": str(s["id"]), "display_name": s.get("display_name"),
                            "customer_name": s.get("customer_name"),
                            "address": {k: s.get(k) for k in ADDRESS_COLUMNS}, "status": s.get("status"),
                            "identity_status": s.get("identity_status"), "notes": s.get("notes"),
                            "kind": "source_identity" if _is_source_identity(s) else "physical",
                            "identifiers": [f"{i['source_system']}/{i['identifier_type']}/{i['identifier_value']}"
                                            for i in s["identifiers"]],
                            "counts": s.get("counts")} for s in stores],
        "canonical_targets": [asdict(c) for c in directory.stores],
        "exact_matches": [a.canonical for a in actions if a.action == ACTION_PRESENT],
        "requiring_reconciliation": [{"canonical": a.canonical, "store_id": a.store_id, "previous": a.previous}
                                     for a in adopted],
        "to_create": [a.canonical for a in actions if a.action == ACTION_CREATE],
        "duplicate_risks": by_action.get(ACTION_DUPLICATE, []),
        "refused": by_action.get(ACTION_REFUSE, []),
        "evidence_preserved": {a.canonical: [f"{i['source_system']}/{i['identifier_type']}/{i['identifier_value']}"
                                             for s in stores if str(s["id"]) == a.store_id for i in s["identifiers"]]
                               for a in adopted},
        "new_identifiers": [f"{i['source_system']}/{i['identifier_type']}/{i['identifier_value']} -> {a.canonical}"
                            for a in actions for i in a.identifiers],
        "unresolved_source_identities": by_action.get(ACTION_KEEP_SOURCE, []),
        "named_stores_not_in_directory": by_action.get(ACTION_UNLISTED, []),
        "store_ids_reassigned": 0,
        "associations_on_adopted_records": {a.canonical: a.counts for a in adopted},
        "schema_migration_required": False,
    }
