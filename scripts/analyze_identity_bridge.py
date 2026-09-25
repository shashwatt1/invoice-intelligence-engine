#!/usr/bin/env python
"""
11-digit to 12-digit product identity bridge — READ-ONLY EVIDENCE ONLY.

    python scripts/analyze_product_master.py      # must be run first
    python scripts/analyze_identity_bridge.py

An 11-digit identifier in this corpus is one of two different things, and
the correct repair is the opposite in each case:

    a distributor value that reached Excel as a float   -> lost a LEADING ZERO
    a Scan code from the sales system                   -> lost its CHECK DIGIT

Both hypotheses are evaluated for every 11-digit candidate and scored
against independent evidence — whether the reconstructed UPC actually
exists in the distributor sheets or in the application's own identifier
tables, and whether the descriptions on both sides agree. Nothing is
rewritten, no rule is applied, and no value is promoted from one identity
namespace to another. The output is a classification a person can review.

Read-only in the strict sense: the database session is opened READ ONLY,
only SELECTs are issued, and the script refuses any flag that implies a
write.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.analyze_product_master import (  # noqa: E402
    OUTPUT_DIR,
    has_valid_upc_check,
    refuse_database_arguments,
    upc_a_check_digit,
)

CANDIDATES_CSV = OUTPUT_DIR / "product_master_candidates.csv"
PROVENANCE_CSV = OUTPUT_DIR / "product_source_provenance.csv"
BRIDGE_CSV = OUTPUT_DIR / "identity_bridge_candidates.csv"
RECONCILIATION_CSV = OUTPUT_DIR / "case_mapping_reconciliation.csv"
BRIDGE_JSON = OUTPUT_DIR / "identity_bridge_report.json"
REPORT_MD = ROOT / "docs" / "product-identity-bridge-report.md"

# Packaging words carry no brand information, so two rows sharing only
# "CAN" is not corroboration of anything.
STOPWORDS = {
    "CAN", "CANS", "BTL", "BTLS", "BOTTLE", "BOTTLES", "NR", "NRLN", "DFT", "BBL",
    "PACK", "PK", "PKG", "LOOSE", "CASE", "THE", "AND", "WITH", "SINGLE", "SINGLES",
    "ALUMINUM", "TWIST", "OFF", "DEPOSIT", "ITEM", "GENERIC",
}
_SIZE_TOKEN = re.compile(r"^\d+(?:\.\d+)?(?:OZ|ML|L|LB|PK|P|CT|G)$")
_SPLIT = re.compile(r"[^A-Z0-9]+")


def meaningful_tokens(description: str) -> set[str]:
    """Brand-bearing words only — sizes, pack words and digits are dropped."""
    tokens = set()
    for token in _SPLIT.split((description or "").upper()):
        if len(token) < 3 or token in STOPWORDS:
            continue
        if token.isdigit() or _SIZE_TOKEN.match(token):
            continue
        if not any(c.isalpha() for c in token):
            continue
        tokens.add(token)
    return tokens


def describe_corroboration(left: str, right: str) -> tuple[str, str]:
    """
    How much the two descriptions agree, and on what.

    Exact shared words are the strong signal. A prefix relation (BUD inside
    BUDWEISER) is reported separately because it is suggestive but weaker.
    """
    left_tokens, right_tokens = meaningful_tokens(left), meaningful_tokens(right)
    if not left_tokens or not right_tokens:
        return "no_description", ""
    shared = left_tokens & right_tokens
    if shared:
        return "exact_token", "|".join(sorted(shared)[:4])
    partial = {
        f"{a}~{b}"
        for a in left_tokens
        for b in right_tokens
        if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a))
    }
    if partial:
        return "partial_token", "|".join(sorted(partial)[:3])
    return "no_overlap", ""


def load_corpus() -> tuple[dict, dict, dict]:
    """Canonical candidates, split by namespace, plus the id11 populations."""
    rows = list(csv.DictReader(CANDIDATES_CSV.open()))
    upc12 = {r["identity_value"]: r for r in rows if r["identity_namespace"] == "upc12"}
    id11 = {r["identity_value"]: r for r in rows if r["identity_namespace"] == "id11"}

    # Which of the two populations each 11-digit value came from. Determined
    # by how the value was written, not by guessing from the digits.
    population: dict[str, set[str]] = defaultdict(set)
    for row in csv.DictReader(PROVENANCE_CSV.open()):
        if not row["identity_key"].startswith("id11:"):
            continue
        value = row["identity_key"].split(":", 1)[1]
        if "scientific_notation_expanded" in row["identifier_flags"]:
            population[value].add("scientific_notation")
        elif row["source_type"] == "item_sales_summary":
            population[value].add("sales_scan_code")
        else:
            population[value].add("other")
    return upc12, id11, population


def load_existing_master() -> dict:
    """Read-only snapshot of what the application already holds."""
    try:
        import psycopg2
    except ImportError:
        return {"available": False, "reason": "psycopg2 not installed"}
    try:
        from app.core.config import get_settings

        settings = get_settings()
        dsn = settings.database_url_sync.replace("postgresql+psycopg2://", "postgresql://")
        connection = psycopg2.connect(dsn, connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 — absence of a database is not an error here
        return {"available": False, "reason": type(exc).__name__}

    connection.set_session(readonly=True, autocommit=True)
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT item_code, units_per_case, description FROM product_case_mappings"
        )
        case_mappings = [
            {"item_code": c, "units_per_case": u, "description": d}
            for c, u, d in cursor.fetchall()
        ]
        cursor.execute("SELECT DISTINCT item_code FROM product_identity")
        identity_codes = {row[0] for row in cursor.fetchall()}
        cursor.execute("SELECT kind, value FROM product_identifier")
        identifiers = cursor.fetchall()
    finally:
        connection.close()

    barcode_kinds = {"retail_upc_raw", "unit_upc"}
    return {
        "available": True,
        "case_mappings": case_mappings,
        "identity_codes": identity_codes,
        "identifier_values": {value for _, value in identifiers},
        "barcode_values": {value for kind, value in identifiers if kind in barcode_kinds},
        "identifier_kinds": dict(Counter(kind for kind, _ in identifiers)),
    }


def classify_bridge(
    code: str,
    corpus_upc12: dict,
    app_barcodes: set[str],
    app_identifiers: set[str],
    descriptions: str,
) -> dict:
    """
    Score both reconstructions of one 11-digit code against evidence that
    does not come from the code itself.
    """
    prepend = "0" + code
    append = code + upc_a_check_digit(code)

    prepend_check = has_valid_upc_check(prepend)
    prepend_corpus = prepend in corpus_upc12
    prepend_app = prepend in app_barcodes or prepend in app_identifiers
    append_corpus = append in corpus_upc12
    append_app = append in app_barcodes or append in app_identifiers

    prepend_lands = prepend_corpus or prepend_app
    append_lands = append_corpus or append_app

    corroboration, tokens, target, hypothesis = "not_applicable", "", "", "none"
    if prepend_lands and append_lands:
        hypothesis = "ambiguous"
    elif prepend_lands:
        hypothesis = "prepend_zero"
        target = prepend
    elif append_lands:
        hypothesis = "append_check_digit"
        target = append
    elif prepend_check:
        # Nothing to compare against, but the padded form is at least a
        # structurally valid UPC — weak, and recorded as such.
        hypothesis = "prepend_zero_unconfirmed"

    if target and target in corpus_upc12:
        corroboration, tokens = describe_corroboration(
            descriptions, corpus_upc12[target]["descriptions"]
        )

    if hypothesis == "ambiguous":
        strength = "AMBIGUOUS"
    elif not target and hypothesis == "prepend_zero_unconfirmed":
        strength = "STRUCTURAL_ONLY"
    elif not target:
        strength = "NO_BRIDGE_EVIDENCE"
    elif corroboration == "exact_token":
        strength = "STRONG"
    elif corroboration in {"partial_token"}:
        strength = "MODERATE"
    elif corroboration in {"no_description", "not_applicable"}:
        # Landed on a real UPC held by the application but with nothing to
        # compare descriptions against.
        strength = "MODERATE"
    else:
        strength = "WEAK_CONFLICTING_DESCRIPTION"

    return {
        "identifier11": code,
        "prepend_zero_candidate": prepend,
        "prepend_zero_check_valid": prepend_check,
        "prepend_zero_in_corpus": prepend_corpus,
        "prepend_zero_in_app": prepend_app,
        "append_check_candidate": append,
        "append_check_in_corpus": append_corpus,
        "append_check_in_app": append_app,
        "evidence_suggested_hypothesis": hypothesis,
        "bridge_target": target,
        "description_corroboration": corroboration,
        "shared_tokens": tokens,
        "evidence_strength": strength,
    }


def main() -> None:
    refuse_database_arguments(sys.argv[1:])
    if not CANDIDATES_CSV.exists():
        sys.exit(f"Run scripts/analyze_product_master.py first — {CANDIDATES_CSV} is missing.")

    print("11-to-12 digit identity bridge — READ-ONLY evidence, no rule applied\n")
    corpus_upc12, corpus_id11, populations = load_corpus()
    existing = load_existing_master()
    app_barcodes = existing.get("barcode_values", set())
    app_identifiers = existing.get("identifier_values", set())

    rows = []
    for code, candidate in sorted(corpus_id11.items()):
        result = classify_bridge(
            code, corpus_upc12, app_barcodes, app_identifiers, candidate["descriptions"]
        )
        sources = populations.get(code, set())
        result["source_population"] = (
            "mixed" if len(sources) > 1 else (next(iter(sources), "unknown"))
        )
        result["source_record_count"] = candidate["source_record_count"]
        result["descriptions"] = candidate["descriptions"][:160]
        result["in_app_as_item_code"] = code in existing.get("identity_codes", set())
        rows.append(result)

    columns = list(rows[0].keys())
    BRIDGE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with BRIDGE_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    reconciliation = reconcile_case_mappings(existing, corpus_upc12, corpus_id11)
    if reconciliation:
        with RECONCILIATION_CSV.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(reconciliation[0].keys()))
            writer.writeheader()
            writer.writerows(reconciliation)

    summary = summarize(rows, reconciliation, existing, corpus_upc12, corpus_id11)
    BRIDGE_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_report(summary, rows, reconciliation)

    print(f"  11-digit candidates examined ... {len(rows)}")
    for strength, count in sorted(summary["evidence_strength"].items(), key=lambda kv: -kv[1]):
        print(f"     {strength:<30} {count}")
    mappings = summary["case_mappings"]
    print(f"  case mappings reconciled ....... {mappings['rows']} rows "
          f"({mappings['distinct_item_codes']} distinct item codes)")
    print(f"     corroborated by corpus ...... {mappings['corroborated_rows']} rows "
          f"({mappings['corroborated_distinct']} distinct)")
    print(f"     contradicted by corpus ...... {mappings['disagreement_rows']}")
    print(f"\nWrote {BRIDGE_CSV.relative_to(ROOT)}, {RECONCILIATION_CSV.relative_to(ROOT)},")
    print(f"      {BRIDGE_JSON.relative_to(ROOT)} and {REPORT_MD.relative_to(ROOT)}")


def reconcile_case_mappings(existing: dict, corpus_upc12: dict, corpus_id11: dict) -> list[dict]:
    """
    Every governed case mapping, against whatever the corpus can say about
    it. The mappings themselves are untouched — this only reports whether
    independent evidence agrees with each one.
    """
    if not existing.get("available"):
        return []
    rows = []
    for mapping in sorted(existing["case_mappings"], key=lambda m: m["item_code"]):
        code = mapping["item_code"]
        governed = mapping["units_per_case"]
        row = {
            "item_code": code,
            "item_code_length": len(code),
            "governed_units_per_case": governed,
            "governed_description": (mapping["description"] or "")[:70],
            "found_in_corpus_as_id11": code in corpus_id11,
            "bridge_target": "",
            "bridge_evidence": "",
            "corpus_units_per_case": "",
            "units_agreement": "no_corpus_evidence",
            "corpus_descriptions": "",
            "shared_tokens": "",
        }
        if len(code) == 11:
            target = code + upc_a_check_digit(code)
            if target in corpus_upc12:
                counterpart = corpus_upc12[target]
                observed = [v for v in counterpart["units_per_case_values"].split(" || ") if v]
                corroboration, tokens = describe_corroboration(
                    mapping["description"] or "", counterpart["descriptions"]
                )
                row.update({
                    "bridge_target": target,
                    "bridge_evidence": "append_check_digit_lands_on_corpus_upc12",
                    "corpus_units_per_case": " || ".join(observed),
                    "corpus_descriptions": counterpart["descriptions"][:70],
                    "shared_tokens": tokens,
                })
                if observed:
                    row["units_agreement"] = (
                        "agrees" if [str(governed)] == observed
                        else "agrees_among_multiple" if str(governed) in observed
                        else "DISAGREES"
                    )
                else:
                    row["units_agreement"] = "bridged_but_no_pack_evidence"
                row["description_corroboration"] = corroboration
        rows.append(row)
    return rows


def summarize(rows, reconciliation, existing, corpus_upc12, corpus_id11) -> dict:
    strengths = Counter(r["evidence_strength"] for r in rows)
    hypotheses = Counter(r["evidence_suggested_hypothesis"] for r in rows)
    by_population: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        by_population[row["source_population"]][row["evidence_suggested_hypothesis"]] += 1

    agreement = Counter(r["units_agreement"] for r in reconciliation)
    corroborated = [
        r for r in reconciliation if r["units_agreement"] in {"agrees", "agrees_among_multiple"}
    ]

    # A mapping exists per store, so rows and distinct item codes are
    # different numbers and both matter.
    def distinct(rows_):
        return len({r["item_code"] for r in rows_})

    governed_by_code: dict[str, set] = defaultdict(set)
    for row in reconciliation:
        governed_by_code[row["item_code"]].add(row["governed_units_per_case"])
    cross_store = {
        code: sorted(values) for code, values in governed_by_code.items() if len(values) > 1
    }
    return {
        "read_only": True,
        "rule_applied": "NONE — this report is evidence for review, not a normalization rule.",
        "populations": {
            "corpus_id11_distinct": len(corpus_id11),
            "corpus_upc12_distinct": len(corpus_upc12),
            "app_available": existing.get("available", False),
            "app_identifier_kinds": existing.get("identifier_kinds", {}),
        },
        "evidence_strength": dict(strengths),
        "evidence_suggested_hypothesis": dict(hypotheses),
        "hypothesis_by_source_population": {
            name: dict(counts) for name, counts in by_population.items()
        },
        "case_mappings": {
            "rows": len(reconciliation),
            "distinct_item_codes": distinct(reconciliation),
            "eleven_digit_rows": sum(1 for r in reconciliation if r["item_code_length"] == 11),
            "eleven_digit_distinct": distinct(
                [r for r in reconciliation if r["item_code_length"] == 11]
            ),
            "bridged_rows": sum(1 for r in reconciliation if r["bridge_target"]),
            "bridged_distinct": distinct([r for r in reconciliation if r["bridge_target"]]),
            "corroborated_rows": len(corroborated),
            "corroborated_distinct": distinct(corroborated),
            "disagreement_rows": sum(
                1 for r in reconciliation if r["units_agreement"] == "DISAGREES"
            ),
            "units_agreement": dict(agreement),
            "governed_pack_size_inconsistent_across_stores": cross_store,
        },
        "interpretation": {
            "scientific_notation": (
                "Distributor sheets store the UPC as a number, and Excel's float form "
                "cannot carry a leading zero. The digits are intact but one is missing "
                "from the front, so prepending '0' restores a check-digit-valid UPC. The "
                "check digit is still present in these values."
            ),
            "sales_scan_code": (
                "The sales system stores a PDI-style item code, which is the 12-digit UPC "
                "with its check digit removed. The leading zero is present; the trailing "
                "check digit is what is missing, so the repair is to recompute and append "
                "it. Prepending a zero here performs no better than chance."
            ),
            "why_they_must_not_share_one_rule": (
                "Both are 11 digits and neither can be told from the other by length. "
                "Applying prepend-zero to a scan code, or append-check to a float-derived "
                "value, produces a plausible-looking identifier for a different product."
            ),
        },
    }


def write_report(summary, rows, reconciliation) -> None:
    lines: list[str] = []
    add = lines.append
    add("# Product Identity Bridge — 11-digit to 12-digit Analysis")
    add("")
    add("**Status: READ-ONLY EVIDENCE. No normalization rule is implemented.** Nothing "
        "in the database, the case mappings, the EDI path, extraction or the existing "
        "master data was modified. Every 11-digit identifier below keeps its own "
        "identity; the reconstructions are candidates for a person to accept or reject.")
    add("")
    add("## 1. The two 11-digit populations")
    add("")
    add("An 11-digit identifier is not one kind of thing. The corpus contains two, they "
        "are indistinguishable by length, and **the correct repair is the opposite in "
        "each case**.")
    add("")
    add("| | Distributor (scientific notation) | Item Sales (scan code) |")
    add("| --- | --- | --- |")
    add("| How it is written | Excel float, e.g. `1.8200250002E10` | Text, e.g. `01820000018` |")
    add("| What is missing | the **leading zero** | the **check digit** |")
    add("| Still present | the check digit | the leading zero |")
    add("| Repair | prepend `0` | recompute and append the check digit |")
    add("| Wrong repair produces | a valid-looking code for another product | a valid-looking code for another product |")
    add("")
    for key, text in summary["interpretation"].items():
        add(f"- **{key}** — {text}")
    add("")
    add("### Evidence-suggested hypothesis by source population")
    add("")
    add("| Source population | " + " | ".join(sorted(summary["evidence_suggested_hypothesis"])) + " |")
    add("| --- | " + " | ".join("---" for _ in summary["evidence_suggested_hypothesis"]) + " |")
    for name, counts in sorted(summary["hypothesis_by_source_population"].items()):
        cells = [str(counts.get(h, 0)) for h in sorted(summary["evidence_suggested_hypothesis"])]
        add(f"| {name} | " + " | ".join(cells) + " |")
    add("")
    add("## 2. Evidence strength of every bridge candidate")
    add("")
    add("Strength is decided by evidence outside the identifier itself: whether the "
        "reconstruction lands on a UPC that actually exists in the distributor sheets or "
        "in the application's identifier tables, and whether the descriptions agree.")
    add("")
    add("| Class | Meaning | Count |")
    add("| --- | --- | --- |")
    meanings = {
        "STRONG": "reconstruction lands on an existing UPC **and** descriptions share brand words",
        "MODERATE": "lands on an existing UPC, but descriptions do not corroborate it",
        "WEAK_CONFLICTING_DESCRIPTION": "lands on an existing UPC whose description disagrees — review first",
        "STRUCTURAL_ONLY": "padded form is a valid UPC, but no counterpart exists anywhere",
        "AMBIGUOUS": "**both** reconstructions land on existing but different UPCs",
        "NO_BRIDGE_EVIDENCE": "neither reconstruction matches anything known",
    }
    for strength, count in sorted(summary["evidence_strength"].items(), key=lambda kv: -kv[1]):
        add(f"| `{strength}` | {meanings.get(strength, '')} | {count} |")
    add("")
    strong = [r for r in rows if r["evidence_strength"] == "STRONG"][:10]
    if strong:
        add("### Strongest examples")
        add("")
        add("| 11-digit | Reconstruction | Target UPC-12 | Shared words | Source population |")
        add("| --- | --- | --- | --- | --- |")
        for row in strong:
            add(f"| `{row['identifier11']}` | {row['evidence_suggested_hypothesis']} | "
                f"`{row['bridge_target']}` | {row['shared_tokens']} | {row['source_population']} |")
        add("")
    ambiguous = [r for r in rows if r["evidence_strength"] == "AMBIGUOUS"][:10]
    add(f"### Ambiguous cases — {summary['evidence_strength'].get('AMBIGUOUS', 0)}")
    add("")
    if ambiguous:
        add("Both repairs land on a real UPC. These cannot be resolved automatically by "
            "any rule and must be decided by a person.")
        add("")
        add("| 11-digit | Prepend `0` | Append check | Descriptions |")
        add("| --- | --- | --- | --- |")
        for row in ambiguous:
            add(f"| `{row['identifier11']}` | `{row['prepend_zero_candidate']}` | "
                f"`{row['append_check_candidate']}` | {row['descriptions'][:44]} |")
    else:
        add("None — no 11-digit value in this corpus has both repairs landing on a known UPC.")
    add("")
    add("## 3. Reconciliation of the existing case mappings")
    add("")
    mappings = summary["case_mappings"]
    if not mappings["rows"]:
        add("The application database was not reachable, so this section is empty. "
            "The bridge analysis above is unaffected — it does not require a database.")
    else:
        add("A case mapping exists per store, so rows and distinct item codes differ; "
            "both are given.")
        add("")
        add("| | Rows | Distinct item codes |")
        add("| --- | --- | --- |")
        add(f"| governed case mappings inspected (read-only) | {mappings['rows']} | "
            f"{mappings['distinct_item_codes']} |")
        add(f"| with an 11-digit item code | {mappings['eleven_digit_rows']} | "
            f"{mappings['eleven_digit_distinct']} |")
        add(f"| reconstruction lands on a corpus UPC-12 | {mappings['bridged_rows']} | "
            f"{mappings['bridged_distinct']} |")
        add(f"| **corroborated** (corpus pack size agrees) | {mappings['corroborated_rows']} | "
            f"**{mappings['corroborated_distinct']}** |")
        add(f"| contradicted by the corpus | {mappings['disagreement_rows']} | — |")
        add("")
        inconsistent = mappings["governed_pack_size_inconsistent_across_stores"]
        if inconsistent:
            add("#### Pre-existing inconsistency found in the governed mappings")
            add("")
            add("These item codes already carry **different pack sizes in different "
                "stores**. That disagreement exists in the application today and is not "
                "caused by the bridge — the bridge only made it visible. Nothing here "
                "was changed.")
            add("")
            add("| Item code | Governed units-per-case across stores |")
            add("| --- | --- |")
            for code, values in sorted(inconsistent.items()):
                add(f"| `{code}` | {', '.join(str(v) for v in values)} |")
            add("")
        add("| Outcome | Count |")
        add("| --- | --- |")
        for outcome, count in sorted(mappings["units_agreement"].items(), key=lambda kv: -kv[1]):
            add(f"| `{outcome}` | {count} |")
        add("")
        corroborated = [r for r in reconciliation
                        if r["units_agreement"] in {"agrees", "agrees_among_multiple"}]
        if corroborated:
            add(f"### The corroborated mappings — {len(corroborated)} rows, "
                f"{mappings['corroborated_distinct']} distinct item codes")
            add("")
            add("Independent agreement between a governed mapping and the distributor "
                "sheets. This is **not** authority to change anything — the mappings "
                "already hold these values and remain untouched.")
            add("")
            add("| Item code | Reconstructed UPC-12 | Governed units | Corpus units | Shared words | Governed description |")
            add("| --- | --- | --- | --- | --- | --- |")
            for row in corroborated:
                add(f"| `{row['item_code']}` | `{row['bridge_target']}` | "
                    f"{row['governed_units_per_case']} | {row['corpus_units_per_case']} | "
                    f"{row['shared_tokens'] or '—'} | {row['governed_description'][:38]} |")
            add("")
        disagreeing = [r for r in reconciliation if r["units_agreement"] == "DISAGREES"]
        if disagreeing:
            add("### Contradictions — review before any rule is written")
            add("")
            add("| Item code | Reconstructed UPC-12 | Governed units | Corpus units |")
            add("| --- | --- | --- | --- |")
            for row in disagreeing:
                add(f"| `{row['item_code']}` | `{row['bridge_target']}` | "
                    f"{row['governed_units_per_case']} | {row['corpus_units_per_case']} |")
            add("")
    add("## 4. What this does and does not establish")
    add("")
    add("**Establishes**: the two 11-digit populations are real, separable by how the "
        "value was written rather than by its digits, and each has a reconstruction "
        "supported by independent evidence.")
    add("")
    add("**Does not establish**: that any individual bridge is correct. A reconstruction "
        "landing on a real UPC is evidence, not proof — two products can differ in ways "
        "no barcode arithmetic will reveal.")
    add("")
    add("**Not done here**: no normalization rule, no identity merge, no change to "
        "`product_case_mappings`, `product_identity`, `product_identifier`, extraction "
        "or EDI. `AMBIGUOUS` and `WEAK_CONFLICTING_DESCRIPTION` rows must be resolved by "
        "a person before any rule is considered.")
    add("")
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
