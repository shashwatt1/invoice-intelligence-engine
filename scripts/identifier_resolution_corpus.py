#!/usr/bin/env python
"""
Identifier resolution SHADOW CORPUS report — scripts/identifier_resolution_corpus.py

    python scripts/identifier_resolution_corpus.py

Runs the deterministic resolver (app/services/product_master/resolution.py)
over every stored invoice this environment can READ, and writes one report.
It changes nothing: no EDI, pipeline, Product Master, mapping, proposal or
invoice row is touched, nothing is persisted outside
analysis/identifier-shadow/corpus/ (gitignored), and no model or OCR call is
made.

Corpus sources, each read-only:
  * DATABASE — invoices, invoice_items, raw_extraction_json, stores and
    document_pages.raw_ocr_text through a READ ONLY, autocommit psycopg2
    session issuing SELECTs only, with the database settings already
    configured. Unreachable → recorded as pending; never worked around.
    Stored invoices carry no identifier observations: v11-only mode.
  * LOCAL ARTIFACTS — analysis/identifier-shadow/<invoice>/ (the 8 Phase 1
    gold invoices): ocr.json, v11.json and the frozen obs-v1.1
    observations.json. Resolved v11-only and v11 + obs-v1.1.

Product Master sources, each read-only (app/services/product_master/
resolution_evidence.py):
  * LIVE — when the database is reachable;
  * SEED PREVIEW — analysis/master-data/product_master_identifiers.csv,
    a seed PREVIEW, not the live Product Master, labelled as such throughout;
  * NONE — the empty lookup;
  * HYPOTHETICAL — the seed preview with AUTO_MATCHED read as confirmed, only
    to show what that business decision would change. Never a real mode.

The current-EDI comparison uses today's normalize_item_code() — imported and
called, not changed — on the same v11 product code the EDI writer would read,
and compares the historical Red Bull EDI file line by line.

Hard-stop checks (report["safety"]) must all be empty: a different code with
no KD explanation, an eligible line that is not VERIFIED/REVIEW_CONFIRMED on a
check-valid GTIN-12, a supplier id or letters in a would-be code, a DRAFT rule
behind an eligible line, a truncated GTIN-13/14, or an applied proposal.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.export_service import (  # noqa: E402  (read-only)
    NON_PDI_LINE_TYPES,
    normalize_item_code,
)
from app.services.identity_review_service import proposal_specs  # noqa: E402  (pure dry run)
from app.services.product_master.identifier_templates import (  # noqa: E402
    load_templates,
    select_template,
)
from app.services.product_master.identifiers import has_valid_upc_check  # noqa: E402
from app.services.product_master.reread import plan_rereads  # noqa: E402
from app.services.product_master.reread import summarize as summarize_rereads  # noqa: E402
from app.services.product_master.resolution import (  # noqa: E402
    RESOLVER_VERSION,
    PMMatch,
    ResolverLine,
    no_crosswalk,
    no_product_master,
    resolve_invoice,
)
from app.services.product_master.resolution_evidence import (  # noqa: E402
    LiveProductMaster,
    SeedPreviewProductMaster,
    classify_matches,
)
from scripts.identifier_shadow_report import align, gold_lines  # noqa: E402

SHADOW_DIR = ROOT / "analysis" / "identifier-shadow"
OUT = SHADOW_DIR / "corpus"
GOLD_DIR = ROOT / "tests" / "fixtures" / "identifier" / "invoices"
EDI_DIR = ROOT / "tests" / "fixtures" / "identifier" / "edi"
SEED_PREVIEW = ROOT / "analysis" / "master-data" / "product_master_identifiers.csv"
SEED_CANDIDATES = ROOT / "analysis" / "master-data" / "product_master_candidates.csv"

HAZARDS = {
    "KD1": "supplier id (or letter-bearing value) rendered as an item code",
    "KD2": "item-reference fragment zero-padded into an item code",
    "KD3": "supplier item number zero-padded into an item code",
    "KD4": "11-digit value passed through without a known convention",
    "KD5": "GTIN-13 truncated to 11 digits",
    "KD6": "GTIN-14 truncated to 11 digits",
    "KD7": "no check-digit gate: an invalid GTIN-12 emitted",
    "KD8": "multi-value cell concatenated and truncated",
    "KD9": "sign is per invoice: mixed SALES/RETURNS unrepresentable (invoice level, outside the resolver)",
    "KD10": "regression gate keyed on the extracted invoice number (invoice level, outside the resolver)",
}
EXPLAINED_PREFIXES = (*HAZARDS, "ASSOCIATION", "PRODUCT_MASTER", "POLICY", "UNRECOGNISED", "NOT_APPLICABLE",
                      "no product code")


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


@dataclass
class CorpusInvoice:
    key: str
    source: str
    vendor_name: str | None
    lines: list[ResolverLine]
    meta: list[dict]                   # per line: quantity, line_type, product_code as stored
    ocr_text: str
    observations: dict | None = None
    gold: dict | None = None
    store: str | None = None
    page_images_available: bool = False
    notes: list[str] = field(default_factory=list)


def local_artifacts() -> list[CorpusInvoice]:
    corpus = []
    for folder in sorted(p for p in SHADOW_DIR.iterdir() if (p / "v11.json").exists()):
        v11 = json.loads((folder / "v11.json").read_text())["parsed"]
        recorded = json.loads((folder / "observations.json").read_text())
        if recorded["prompt_version"] != "obs-v1.1":
            raise SystemExit(f"{folder.name}: expected frozen obs-v1.1 observations")
        items = v11["line_items"]
        gold_path = GOLD_DIR / f"{folder.name}.json"
        gold = json.loads(gold_path.read_text()) if gold_path.exists() else None
        corpus.append(CorpusInvoice(
            key=folder.name, source="local_artifact", vendor_name=(v11.get("vendor") or {}).get("name"),
            lines=[ResolverLine(i, it.get("description"), it.get("line_type"), it.get("product_code"),
                                it.get("supplier_item_id")) for i, it in enumerate(items, 1)],
            meta=[{"quantity": it.get("quantity"), "line_type": it.get("line_type"),
                   "product_code": it.get("product_code")} for it in items],
            ocr_text=json.loads((folder / "ocr.json").read_text())["full_text"],
            observations=recorded["parsed"], gold=gold,
            store=(gold or {}).get("receiving_store"),
            page_images_available=True,     # the hash-verified local photos exist for the 8 gold invoices
        ))
    return corpus


def _connect():
    import psycopg2

    from app.core.config import get_settings

    dsn = get_settings().database_url_sync.replace("postgresql+psycopg2://", "postgresql://")
    connection = psycopg2.connect(dsn, connect_timeout=5)
    connection.set_session(readonly=True, autocommit=True)
    return connection


def _v11_line_items(raw_extraction: Any) -> list[dict] | None:
    try:
        content = raw_extraction["choices"][0]["message"]["content"]
        return json.loads(content)["line_items"]
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def database_corpus(connection) -> list[CorpusInvoice]:
    """Every stored invoice, SELECTs only."""
    cursor = connection.cursor()
    cursor.execute("SELECT i.id, i.invoice_number, v.name, i.raw_extraction_json, i.document_id, s.name "
                   "FROM invoices i LEFT JOIN vendors v ON v.id = i.vendor_id "
                   "LEFT JOIN stores s ON s.id = i.store_id ORDER BY i.created_at")
    invoices = cursor.fetchall()
    cursor.execute("SELECT invoice_id, sort_order, description, line_type, product_sku, quantity, "
                   "corrected_fields FROM invoice_items ORDER BY invoice_id, sort_order")
    items: dict[Any, list] = defaultdict(list)
    for row in cursor.fetchall():
        items[row[0]].append(row)
    cursor.execute("SELECT document_id, page_number, raw_ocr_text FROM document_pages ORDER BY document_id, page_number")
    pages: dict[Any, list[str]] = defaultdict(list)
    for document_id, _page, text in cursor.fetchall():
        pages[document_id].append(text or "")
    corpus = []
    for row in invoices:
        invoice_id, number, vendor, raw, document_id = row[:5]
        store = row[5] if len(row) > 5 else None
        rows = items.get(invoice_id, [])
        extracted = _v11_line_items(raw)
        notes = []
        if extracted is None or len(extracted) != len(rows):
            notes.append("supplier_item_id unavailable: stored items and raw extraction differ in count")
            extracted = None
        lines, meta = [], []
        for i, (_, _sort, description, line_type, sku, quantity, corrected) in enumerate(rows, 1):
            supplier = extracted[i - 1].get("supplier_item_id") if extracted else None
            lines.append(ResolverLine(i, description, line_type, sku, supplier, tuple(corrected or ())))
            meta.append({"quantity": float(quantity) if quantity is not None else None,
                         "line_type": line_type, "product_code": sku})
        corpus.append(CorpusInvoice(key=f"db:{number}:{invoice_id}", source="database", vendor_name=vendor,
                                    lines=lines, meta=meta, ocr_text="\n".join(pages.get(document_id, [])),
                                    store=store, page_images_available=False, notes=notes))
    return corpus


def live_lookup(connection):
    """The live Product Master GTIN lookup (read-only)."""
    return LiveProductMaster(connection).gtin


# ---------------------------------------------------------------------------
# Comparison with today's EDI item code
# ---------------------------------------------------------------------------


def exported_today(meta: dict) -> bool:
    """export_service.pdi_items(): not a charge/duplicate/voided row, quantity above zero."""
    quantity = meta.get("quantity")
    return (meta.get("line_type") or "product") not in NON_PDI_LINE_TYPES and not (quantity is not None and quantity <= 0)


def hazard_for(line: dict, product_code: str | None) -> str:
    """Why today's code for this line is not one the resolver would emit."""
    if line["status"] == "NOT_APPLICABLE":
        return "NOT_APPLICABLE (service document or non-product line; D11)" + (
            " + KD1" if product_code and re.search(r"[A-Za-z]", product_code) else "")
    if not product_code:
        return "no product code today (already blocked by the identity gate)"
    mine = [c for c in line["candidates"] if c["source"] == "v11.product_code"]
    kinds = {c["kind"] for c in mine}
    if "," in product_code or any(c.get("piece") for c in mine):
        return "KD8"
    if re.search(r"[A-Za-z]", product_code) or "SUPPLIER_ITEM_ID" in kinds and not product_code.isdigit():
        return "KD1"
    if "SUPPLIER_ITEM_ID" in kinds:
        return "KD3" if len(product_code) >= 8 else "KD1"
    if "FRAGMENT" in kinds:
        return "KD2"
    if "ELEVEN_DIGIT" in kinds:
        return "KD4"
    if "GTIN_13" in kinds:
        return "KD5"
    if "GTIN_14" in kinds:
        return "KD6"
    if any(c["kind"] == "GTIN_12" and not c["check_valid"] for c in mine):
        return "KD7"
    if line["status"] in ("AMBIGUOUS", "CONFLICT") and "GTIN_12" in kinds:
        return "ASSOCIATION (valid GTIN-12 whose row association is anomalous or contradicted)"
    if line["status"] == "VALID_UNMATCHED":
        return "PRODUCT_MASTER (valid GTIN-12 absent from the Product Master in use; D6)"
    if line["status"] == "CORROBORATED":
        return "POLICY (Product Master match short of the VERIFIED rule)"
    if "UNASSIGNED" in kinds:
        return "UNRECOGNISED (value fits no template field)"
    return f"OTHER ({line['status']})"


def judge_against_gold(code: str | None, truth: str | None) -> str:
    """MATCHES_GOLD / CONTRADICTS_GOLD / NO_GOLD_GTIN12 (a code where gold has no GTIN-12) / NO_CODE."""
    if not code:
        return "NO_CODE"
    if truth is None:
        return "NO_GOLD_GTIN12"
    return "MATCHES_GOLD" if code == truth else "CONTRADICTS_GOLD"


def gold_code(g: dict) -> str | None:
    values = [re.sub(r"\D", "", o["raw_text"]) for o in g["identifiers"] if o["role_expected"] == "gtin_candidate"]
    return values[0][:11] if len(values) == 1 and len(values[0]) == 12 else None


def evaluate(inv: CorpusInvoice, templates, pm_lookup, *, use_observations: bool,
             crosswalk_lookup=no_crosswalk) -> dict[str, Any]:
    template, how = select_template(templates, inv.vendor_name, inv.ocr_text)
    result = resolve_invoice(inv.lines, inv.observations if use_observations else None, inv.ocr_text, template,
                             pm_lookup=pm_lookup, crosswalk_lookup=crosswalk_lookup, template_selection=how)
    gold_by_line: dict[int, dict] = {}
    if inv.gold:
        g_lines = gold_lines(inv.gold)
        pairs = align(g_lines, [{"description": ln.description} for ln in inv.lines])
        gold_by_line = {ai + 1: g_lines[gi] for gi, ai in pairs.items()}
    comparisons = []
    for line, meta in zip(result["lines"], inv.meta, strict=False):
        if not exported_today(meta):
            continue
        today = normalize_item_code(meta.get("product_code"))
        resolver = line["pdi_item_code"]
        if resolver and today == resolver:
            outcome = "SAME"
        elif resolver and today and today != resolver:
            outcome = "DIFFERENT_CODE"
        elif resolver and not today:
            outcome = "NEWLY_ALLOWED"
        elif today:
            outcome = "NEWLY_BLOCKED"
        else:
            outcome = "BLOCKED_TODAY_AND_BY_RESOLVER"
        g = gold_by_line.get(line["line_index"])
        truth = gold_code(g) if g else None
        comparisons.append({
            "line": line["line_index"], "description": line["description"],
            "product_code": meta.get("product_code"), "today": today, "resolver": resolver,
            "status": line["status"], "reasons": line["reasons"], "outcome": outcome,
            "hazard": hazard_for(line, meta.get("product_code")) if outcome in ("NEWLY_BLOCKED", "DIFFERENT_CODE") else None,
            "gold_code": truth, "gold_has_line": g is not None,
            "today_vs_gold": judge_against_gold(today, truth) if g is not None else None,
            "resolver_vs_gold": judge_against_gold(resolver, truth) if g is not None else None,
        })
    rereads = plan_rereads(inv.key, result, page_images_available=inv.page_images_available,
                           token_coordinates_available=False)
    pm_profile: Counter = Counter()
    for line in result["lines"]:
        for ev in line["pm_evidence"]:
            if ev["via"] == "gtin":
                pm_profile[classify_matches([PMMatch(**m) for m in ev["matches"]])] += 1
        looked = {ev["key"] for ev in line["pm_evidence"] if ev["via"] == "gtin"}
        for cand in line["candidates"]:
            if cand.get("check_valid") and cand.get("kind") == "GTIN_12" and cand["value"] not in looked:
                looked.add(cand["value"])
    return {"key": inv.key, "source": inv.source, "vendor": inv.vendor_name, "store": inv.store,
            "template": result["template"]["template_id"] if result["template"] else None,
            "template_selection": how, "inputs_hash": result["inputs_hash"],
            "resolver_version": result["resolver_version"],
            "status_counts": result["status_counts"], "anomalies": result["anomalies"],
            "association_proposals": result["association_proposals"],
            "lines": result["lines"], "edi_comparison": comparisons, "rereads": rereads,
            "pm_evidence_profile": dict(pm_profile),
            "crosswalk": {"lookups": sum(1 for ln in result["lines"] for ev in ln["pm_evidence"]
                                         if ev["via"] == "supplier_crosswalk"),
                          "trusted_matches": sum(1 for ln in result["lines"] for ev in ln["pm_evidence"]
                                                 if ev["via"] == "supplier_crosswalk"
                                                 and any(m["evidence_state"] == "CONFIRMED" for m in ev["matches"]))},
            "proposal_dry_run": Counter(s["source"] for s in proposal_specs(result)),
            "notes": inv.notes}


# ---------------------------------------------------------------------------
# Safety checks (hard stops) and aggregation
# ---------------------------------------------------------------------------


def safety_violations(runs: list[dict]) -> list[str]:
    """Every entry is a STOP condition."""
    out = []
    for run in runs:
        for c in run["edi_comparison"]:
            if c["outcome"] in ("DIFFERENT_CODE", "NEWLY_BLOCKED") and not (c["hazard"] or "").startswith(EXPLAINED_PREFIXES):
                out.append(f"{run['key']} line {c['line']}: {c['outcome']} without a known explanation ({c['hazard']})")
            if c["outcome"] == "DIFFERENT_CODE":
                out.append(f"{run['key']} line {c['line']}: resolver code {c['resolver']} differs from today {c['today']}")
        for ln in run["lines"]:
            if ln["would_be_eligible"]:
                gtin = ln["chosen_gtin"] or ""
                if ln["status"] not in ("VERIFIED", "REVIEW_CONFIRMED"):
                    out.append(f"{run['key']} line {ln['line_index']}: eligible with status {ln['status']}")
                if not (gtin.isdigit() and len(gtin) == 12 and has_valid_upc_check(gtin)):
                    out.append(f"{run['key']} line {ln['line_index']}: eligible code not from a check-valid GTIN-12")
                if any(c.get("rule_state") == "DRAFT" and c.get("value") == gtin for c in ln["candidates"]):
                    out.append(f"{run['key']} line {ln['line_index']}: a DRAFT rule behind an eligible line")
                if any(c.get("kind") in ("GTIN_13", "GTIN_14", "SUPPLIER_ITEM_ID") and c.get("value") == gtin
                       for c in ln["candidates"]):
                    out.append(f"{run['key']} line {ln['line_index']}: eligible code from a non-GTIN-12 read")
            if ln["pdi_item_code"] and not ln["pdi_item_code"].isdigit():
                out.append(f"{run['key']} line {ln['line_index']}: letters in a would-be item code")
        for prop in run["association_proposals"]:
            if prop.get("auto_applied") is not False:
                out.append(f"{run['key']}: an association proposal was applied")
    return out


def _bucket() -> dict[str, Any]:
    return {"invoices": 0, "lines": 0, "statuses": Counter(), "edi": Counter(), "review_lines": 0}


def aggregate(runs: list[dict]) -> dict[str, Any]:
    by_template: dict[str, dict] = defaultdict(_bucket)
    by_vendor: dict[str, dict] = defaultdict(_bucket)
    by_store: dict[str, dict] = defaultdict(_bucket)
    outcomes: Counter = Counter()
    hazards: Counter = Counter()
    rereads: list[dict] = []
    review_reasons: Counter = Counter()
    review_lines = anomalies = proposals = 0
    today_judged: Counter = Counter()
    resolver_judged: Counter = Counter()
    pm_profile: Counter = Counter()
    crosswalk: Counter = Counter()
    dry_run: Counter = Counter()
    gold_judged = eligible = 0
    affected: dict[str, set] = {"invoices": set(), "vendors": set(), "stores": set()}
    for run in runs:
        needs_review = [ln for ln in run["lines"] if ln["review_required"]]
        for bucket in (by_template[run["template"] or "(no template)"], by_vendor[run["vendor"] or "(unknown)"],
                       by_store[run["store"] or "(unknown)"]):
            bucket["invoices"] += 1
            bucket["lines"] += len(run["lines"])
            bucket["statuses"].update({k: v for k, v in run["status_counts"].items() if v})
            bucket["edi"].update(c["outcome"] for c in run["edi_comparison"])
            bucket["review_lines"] += len(needs_review)
        anomalies += len(run["anomalies"])
        proposals += len(run["association_proposals"])
        review_lines += len(needs_review)
        if needs_review:
            affected["invoices"].add(run["key"])
            affected["vendors"].add(run["vendor"])
            affected["stores"].add(run["store"])
        for ln in needs_review:
            review_reasons.update(ln["review_reasons"])
        eligible += sum(ln["would_be_eligible"] for ln in run["lines"])
        rereads += run["rereads"]
        pm_profile.update(run["pm_evidence_profile"])
        crosswalk.update(run["crosswalk"])
        dry_run.update(run["proposal_dry_run"])
        for c in run["edi_comparison"]:
            outcomes[c["outcome"]] += 1
            if c["hazard"]:
                hazards[c["hazard"].split(" ")[0]] += 1
            if c["gold_has_line"]:
                gold_judged += 1
                today_judged[c["today_vs_gold"]] += 1
                resolver_judged[c["resolver_vs_gold"]] += 1
    statuses: Counter = Counter()
    for b in by_template.values():
        statuses.update(b["statuses"])

    def plain(buckets):
        return {k: {**v, "statuses": dict(v["statuses"]), "edi": dict(v["edi"])} for k, v in sorted(buckets.items())}
    return {
        "invoices": len(runs), "lines": sum(len(r["lines"]) for r in runs),
        "status_totals": dict(statuses),
        "by_template": plain(by_template), "by_vendor": plain(by_vendor), "by_store": plain(by_store),
        "edi_outcomes": dict(outcomes), "newly_blocked_by_hazard": dict(hazards),
        "would_be_eligible_lines": eligible,
        "exported_lines_with_gold": gold_judged,
        "today_vs_gold": dict(today_judged), "resolver_vs_gold": dict(resolver_judged),
        "wrong_codes_resolver": resolver_judged.get("CONTRADICTS_GOLD", 0) + resolver_judged.get("NO_GOLD_GTIN12", 0),
        "product_master_evidence_per_gtin_lookup": dict(pm_profile),
        "supplier_crosswalk": dict(crosswalk),
        "association_anomalies": anomalies, "association_proposals": proposals,
        "association_proposals_applied": 0,
        "rereads": summarize_rereads(rereads),
        "review_queue": {"lines": review_lines, "invoices": len(affected["invoices"]),
                         "vendors": len(affected["vendors"]), "stores": len(affected["stores"]),
                         "reasons": dict(review_reasons)},
        "identity_proposal_dry_run": dict(dry_run),
        "unresolved_or_ambiguous_lines": statuses.get("UNRESOLVED", 0) + statuses.get("AMBIGUOUS", 0),
    }


def digest(runs: list[dict]) -> str:
    return hashlib.sha256(json.dumps(runs, sort_keys=True, default=str).encode()).hexdigest()


def regression_vs_phase3a(runs: list[dict]) -> dict[str, Any]:
    """Line statuses against the frozen Phase 3A outputs (same inputs, v11 + obs-v1.1, no Product Master)."""
    changed, compared = [], 0
    for run in runs:
        frozen = SHADOW_DIR / run["key"] / "resolution.phase3a.json"
        if not frozen.exists():
            continue
        before = json.loads(frozen.read_text())["lines"]
        for old, new in zip(before, run["lines"], strict=True):
            compared += 1
            if (old["status"], old["chosen_gtin"], old["would_be_eligible"]) != \
                    (new["status"], new["chosen_gtin"], new["would_be_eligible"]):
                changed.append({"invoice": run["key"], "line": new["line_index"],
                                "before": old["status"], "after": new["status"]})
    return {"lines_compared": compared, "changed": changed}


def historical_edi_comparison(runs: list[dict]) -> dict[str, Any]:
    """Each project EDI fixture: its B-record item codes against the resolver, where an extraction exists offline."""
    out = {}
    by_number = {re.sub(r"\D", "", run["key"].split("_")[-1]): run for run in runs}
    for path in sorted(EDI_DIR.glob("*.txt")):
        raw = path.read_bytes()
        records = [r.decode("latin-1") for r in raw.split(b"\r\n") if r.startswith(b"B")]
        number = re.sub(r"\D", "", path.stem)
        run = by_number.get(number)
        entry: dict[str, Any] = {"sha256": hashlib.sha256(raw).hexdigest(), "b_records": len(records)}
        if run is None:
            entry["resolver"] = "no stored extraction available offline (database pending)"
        else:
            lines = []
            for record, line in zip(records, run["lines"], strict=False):
                code = record[1:12]
                lines.append({"edi_item_code": code, "edi_description": record[12:37].strip(),
                              "resolver_status": line["status"], "resolver_code": line["pdi_item_code"],
                              "explanation": "KD1 (supplier id zero-padded)" if code.startswith("00000")
                              and any(c["kind"] == "SUPPLIER_ITEM_ID" and re.sub(r"\D", "", c["value"]) in code
                                      for c in line["candidates"]) else None})
            entry["lines"] = lines
        out[path.name] = entry
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    templates = load_templates()
    report: dict[str, Any] = {"resolver_version": RESOLVER_VERSION, "mode": "shadow corpus, read-only",
                              "hazards": HAZARDS, "sources": {}, "runs": {}}

    connection = None
    try:
        connection = _connect()
        report["sources"]["database"] = "reachable (read-only session)"
    except Exception as exc:  # noqa: BLE001 — unreachable is a reported state, not an error
        report["sources"]["database"] = f"UNREACHABLE ({type(exc).__name__}) — stored corpus pending; not worked around"

    local = local_artifacts()
    report["sources"]["local_artifacts"] = f"{len(local)} invoices (Phase 1 gold; cached v11, OCR, obs-v1.1)"
    seed = SeedPreviewProductMaster(SEED_PREVIEW, SEED_CANDIDATES)
    preview = seed.gtin
    report["sources"]["product_master_seed_preview"] = (
        f"{seed.rows} identifier rows over {seed.canonical_upcs} canonical UPCs; {seed.scoped_supplier_ids} "
        f"supplier ids carry a supplier scope — a seed PREVIEW, not the live Product Master")

    def auto_matched_as_confirmed(gtin: str):
        """HYPOTHETICAL sensitivity only: what the VERIFIED rule would do if AUTO_MATCHED counted as confirmed."""
        return tuple(PMMatch(m.product_id, "CONFIRMED" if m.evidence_state == "AUTO_MATCHED" else m.evidence_state,
                             m.source_system, m.canonical_upc, m.descriptions) for m in preview(gtin))

    plans = [("local_v11_only__no_pm", local, no_product_master, False, no_crosswalk),
             ("local_v11_plus_obs__no_pm", local, no_product_master, True, no_crosswalk),
             ("local_v11_only__seed_preview_pm", local, preview, False, seed.crosswalk),
             ("local_v11_plus_obs__seed_preview_pm", local, preview, True, seed.crosswalk),
             ("HYPOTHETICAL_local_v11_plus_obs__seed_preview_auto_matched_as_confirmed", local,
              auto_matched_as_confirmed, True, seed.crosswalk)]
    if connection is not None:
        stored = database_corpus(connection)
        live = LiveProductMaster(connection)
        report["sources"]["database"] += f"; {len(stored)} invoices"
        plans += [("database_v11_only__no_pm", stored, no_product_master, False, no_crosswalk),
                  ("database_v11_only__live_pm", stored, live.gtin, False, live.crosswalk)]

    for name, corpus, lookup, with_obs, crosswalk in plans:
        runs = [evaluate(inv, templates, lookup, use_observations=with_obs, crosswalk_lookup=crosswalk)
                for inv in corpus]
        again = [evaluate(inv, templates, lookup, use_observations=with_obs, crosswalk_lookup=crosswalk)
                 for inv in corpus]
        report["runs"][name] = {
            "summary": aggregate(runs),
            "determinism": {"first": digest(runs), "second": digest(again), "identical": digest(runs) == digest(again),
                            "inputs_hashes_identical": [r["inputs_hash"] for r in runs] == [r["inputs_hash"] for r in again]},
            "safety": safety_violations(runs),
            "regression_vs_phase3a": regression_vs_phase3a(runs) if name == "local_v11_plus_obs__no_pm" else None,
            "historical_edi": historical_edi_comparison(runs) if name == "local_v11_plus_obs__no_pm" else None,
            "invoices": runs,
        }
    if connection is not None:
        connection.close()

    (OUT / "corpus_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(json.dumps({"sources": report["sources"],
                      "runs": {k: {"summary": {s: v["summary"][s] for s in ("status_totals", "edi_outcomes",
                                                                             "would_be_eligible_lines",
                                                                             "wrong_codes_resolver")},
                                   "determinism": v["determinism"]["identical"], "safety": v["safety"]}
                               for k, v in report["runs"].items()}}, indent=2, default=str))


if __name__ == "__main__":
    main()
