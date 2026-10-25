#!/usr/bin/env python
"""
Identifier observation SHADOW report — scripts/identifier_shadow_report.py

    python scripts/identifier_shadow_report.py

Evaluates, per gold invoice, the OBSERVATION-ONLY call (obs-v1) against:

    GOLD     tests/fixtures/identifier/invoices/*.json   (Phase 1 transcriptions)
    ANCHORS  the cached v11 line list                    (analysis/identifier-shadow/*/v11.json)
    RESULT   the observation result                      (analysis/identifier-shadow/*/observations.json)
    OCR      the cached OCR text both calls saw          (analysis/identifier-shadow/*/ocr.json)

Observations reach a gold line through line_index -> v11 line -> gold line
(v11 and gold lines paired by order when their counts agree, otherwise by
description — never by identifier, which would hide wrong-line errors).
Rows returned with line_index null are compared with gold lines that no v11
line represents.

There is no operational-drift metric: the observation schema has no
operational field. Pure analysis — it reads files, writes
analysis/identifier-shadow/report.json and decides nothing. Matching here is
for MEASUREMENT only; it is not identifier resolution and never feeds the
pipeline.
"""

from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
GOLD_DIR = ROOT / "tests" / "fixtures" / "identifier" / "invoices"
SHADOW_DIR = ROOT / "analysis" / "identifier-shadow"

# Gold roles (Phase 1 vocabulary) -> role_hint vocabulary.
ROLE_MAP = {
    "gtin_candidate": "gtin_candidate",
    "gtin_candidate_list": "gtin_candidate",
    "gtin_fragment_item_reference": "item_reference_fragment",
    "supplier_item_id": "supplier_item_id",
    "manufacturer_number": "manufacturer_number",
}

JOIN_MAX_PIECES = 4      # a printed value broken by OCR into at most this many pieces
JOIN_MAX_SPAN = 400      # ... all found within this many characters of OCR text


def loose(value: str | None) -> str:
    """Whitespace removed — for MATCHING only; raw values are compared verbatim too."""
    return re.sub(r"\s+", "", value or "")


def alnum(value: str | None) -> str:
    """Separators removed — only for the v11 cross-check diagnostic."""
    return re.sub(r"[^0-9A-Za-z]", "", value or "")


def gold_lines(gold: dict) -> list[dict]:
    return [{**line, "_section": section["type"]} for section in gold["sections"] for line in section["lines"]]


def gold_values(line: dict) -> set[str]:
    values = set()
    for obs in line["identifiers"]:
        values.add(loose(obs["raw_text"]))
        values.update(loose(part) for part in obs["raw_text"].split(",") if part.strip())
    return values


def description_of(line: dict) -> str:
    return (line.get("description_as_printed") or line.get("product_as_printed")
            or line.get("description") or "").upper()


def align(gold: list[dict], anchors: list[dict]) -> dict[int, int]:
    """gold index -> v11 index, by order when counts agree, else description. NEVER by identifier."""
    if len(gold) == len(anchors):
        return {i: i for i in range(len(gold))}
    pairs: dict[int, int] = {}
    used: set[int] = set()
    for gi, g in enumerate(gold):
        scored = sorted(((SequenceMatcher(None, description_of(g), description_of(a)).ratio(), ai)
                         for ai, a in enumerate(anchors) if ai not in used), reverse=True)
        if scored and scored[0][0] >= 0.6:
            pairs[gi] = scored[0][1]
            used.add(scored[0][1])
    return pairs


def ocr_provenance(raw: str, ocr_text: str) -> str:
    """
    'exact'     — raw_text occurs verbatim in the OCR text;
    'joined'    — raw_text is the in-order join of 2..JOIN_MAX_PIECES pieces of
                  OCR text within JOIN_MAX_SPAN characters (a wrapped cell);
    'not_found' — neither: the value was built, repaired or re-typed.
    """
    if not raw:
        return "not_found"
    if raw in ocr_text:
        return "exact"
    for match in re.finditer(re.escape(raw[:2]), ocr_text):
        start = match.start()
        pos, rest, pieces = start, raw, 0
        while rest and pieces < JOIN_MAX_PIECES:
            size = len(rest)
            while size and ocr_text.find(rest[:size], pos, start + JOIN_MAX_SPAN) < 0:
                size -= 1
            if size < min(2, len(rest)):
                break
            pos = ocr_text.find(rest[:size], pos, start + JOIN_MAX_SPAN) + size
            rest, pieces = rest[size:], pieces + 1
        if not rest and pieces > 1:
            return "joined"
    return "not_found"


def heading_printed(label: str, ocr_text: str) -> bool:
    """A section heading printed as a whole word ('SALES' is not in 'SALESMAN')."""
    return re.search(rf"(?<![A-Za-z]){re.escape(label)}(?![A-Za-z])", ocr_text, re.IGNORECASE) is not None


def row_names(result: dict) -> list[str]:
    """'line k' for a numbered row, 'null row j' (1-based among null rows) otherwise."""
    names, nulls = [], 0
    for row in result["lines"]:
        if row["line_index"] is None:
            nulls += 1
            names.append(f"null row {nulls}")
        else:
            names.append(f"line {row['line_index']}")
    return names


def evaluate(gold: dict, current: dict, result: dict, ocr_text: str) -> dict[str, Any]:
    """All observation-call metrics for one invoice. Pure."""
    g_lines = gold_lines(gold)
    anchors = current["line_items"]
    n = len(anchors)
    partial_gold = gold.get("transcription_status", "").startswith("PARTIAL")
    pairs = align(g_lines, anchors)
    anchor_to_gold = {ai: gi for gi, ai in pairs.items()}

    numbered = [row for row in result["lines"] if row["line_index"] is not None]
    unlisted = [row for row in result["lines"] if row["line_index"] is None]
    by_index: dict[int, list[dict]] = {}
    for row in numbered:
        by_index.setdefault(row["line_index"], []).extend(row["observations"])

    owners: dict[str, set[int]] = {}
    for gi, g in enumerate(g_lines):
        for v in gold_values(g):
            owners.setdefault(v, set()).add(gi)
    value_owner = {v: next(iter(gis)) for v, gis in owners.items() if len(gis) == 1}

    m: dict[str, Any] = {
        "gold_lines": len(g_lines), "v11_anchors": n, "gold_paired_to_anchor": len(pairs),
        "gold_observations": 0, "matched": 0, "verbatim": 0, "not_verbatim": [],
        "missing": [], "wrong_role": [], "wrong_position": [], "wrong_line": [], "extra": [],
        "outside_partial_gold": 0,
    }

    # -- coverage: every v11 index 1..N exactly once; null rows after them, never in their place
    indices = [row["line_index"] for row in numbered]
    order_ok, seen_null, last = True, False, 0
    for row in result["lines"]:
        if row["line_index"] is None:
            seen_null = True
        elif seen_null or row["line_index"] <= last:
            order_ok = False
        else:
            last = row["line_index"]
    m["coverage"] = {
        "missing_indices": [i for i in range(1, n + 1) if i not in indices],
        "duplicate_indices": sorted({i for i in indices if indices.count(i) > 1}),
        "out_of_range_indices": sorted({i for i in indices if not 1 <= i <= n}),
        "numbered_rows_in_order_before_null_rows": order_ok,
        "null_rows": len(unlisted),
    }

    # -- null rows vs gold lines that no v11 line represents (by value: they have no other key)
    unpaired_gold = [gi for gi in range(len(g_lines)) if gi not in pairs]
    null_to_gold: dict[int, int] = {}
    for ri, row in enumerate(unlisted):
        values = {loose(o["raw_text"]) for o in row["observations"]}
        match = next((gi for gi in unpaired_gold if gi not in null_to_gold.values()
                      and values & gold_values(g_lines[gi])), None)
        if match is not None:
            null_to_gold[ri] = match
    m["unlisted_rows"] = {
        "gold_lines_without_v11_line": [description_of(g_lines[gi]) for gi in unpaired_gold],
        "found_as_null_row": [description_of(g_lines[gi]) for gi in null_to_gold.values()],
        "null_rows_not_in_gold": [[o["raw_text"] for o in unlisted[ri]["observations"]]
                                  for ri in range(len(unlisted)) if ri not in null_to_gold],
    }

    # -- per gold line: the observations of the row that represents it
    rows_for_gold: dict[int, list[dict]] = {gi: by_index.get(ai + 1, []) for gi, ai in pairs.items()}
    for ri, gi in null_to_gold.items():
        rows_for_gold[gi] = unlisted[ri]["observations"]

    multi_section = len({s["type"] for s in gold["sections"]}) > 1
    m["section_context"] = {"checked": 0, "ok": 0, "mismatches": []}
    m["multi_value_cells"] = []
    for gi, g in enumerate(g_lines):
        observed = rows_for_gold.get(gi)
        if observed is None:
            continue
        for gold_obs in g["identifiers"]:
            m["gold_observations"] += 1
            key = loose(gold_obs["raw_text"])
            if "," in gold_obs["raw_text"] or gold_obs["role_expected"] == "gtin_candidate_list":
                m["multi_value_cells"].append(multi_value_status(gold_obs["raw_text"], observed, description_of(g)))
            hit = next((o for o in observed if loose(o["raw_text"]) == key), None)
            if hit is None:
                m["missing"].append(f"{description_of(g)}: {gold_obs['raw_text']} ({gold_obs['role_expected']})")
                continue
            m["matched"] += 1
            if hit["raw_text"] == gold_obs["raw_text"]:
                m["verbatim"] += 1
            else:
                m["not_verbatim"].append(f"gold {gold_obs['raw_text']!r} vs observed {hit['raw_text']!r}")
            expected_role = ROLE_MAP.get(gold_obs["role_expected"])
            if expected_role and hit["role_hint"] != expected_role:
                m["wrong_role"].append(f"{gold_obs['raw_text']}: gold {expected_role}, observed {hit['role_hint']}")
            if gold_obs.get("position") and hit.get("position") and hit["position"] != gold_obs["position"]:
                m["wrong_position"].append(f"{gold_obs['raw_text']}: gold {gold_obs['position']}, "
                                           f"observed {hit['position']}")
            if multi_section:
                # Expected only when the heading is actually printed; otherwise null.
                expected = g["_section"] if heading_printed(g["_section"], ocr_text) else None
                got = (hit.get("section_label") or "").upper() or None
                m["section_context"]["checked"] += 1
                if got == expected:
                    m["section_context"]["ok"] += 1
                else:
                    m["section_context"]["mismatches"].append(
                        f"{gold_obs['raw_text']}: expected {expected}, got {got}")

    m["column_coverage"] = column_coverage(g_lines, rows_for_gold, result)

    # -- wrong line / extra: every observation on every row
    def judge(observations: list[dict], gi: int | None, where: str) -> None:
        for o in observations:
            key = loose(o["raw_text"])
            if gi is not None and key in owners and gi in owners[key]:
                continue
            owner = value_owner.get(key)
            if owner is not None and owner != gi:
                m["wrong_line"].append(f"{o['raw_text']} observed on {where} but printed on "
                                       f"'{description_of(g_lines[owner])}'")
            elif key in owners:
                continue  # printed on several gold lines: judged by the duplicates metric
            elif gi is None and partial_gold:
                m["outside_partial_gold"] += 1
            elif gi is not None and multi_value_piece(key, g_lines[gi]):
                continue  # a piece of a split multi-value cell: judged by multi_value_cells
            else:
                m["extra"].append(f"{where}: {o['raw_text']} ({o['role_hint']})")

    for index, observations in sorted(by_index.items()):
        row_gi = anchor_to_gold.get(index - 1)
        judge(observations, row_gi,
              f"'{description_of(g_lines[row_gi])}'" if row_gi is not None else f"v11 line {index}")
    for ri, row in enumerate(unlisted):
        row_gi = null_to_gold.get(ri)
        judge(row["observations"], row_gi,
              f"null row {ri + 1}" + (f" ('{description_of(g_lines[row_gi])}')" if row_gi is not None else ""))

    # -- context observations: section/document scope (Frito-Lay's MANUFACTURER lines)
    context = result["context_observations"]
    expected_scoped = [(obs["raw_text"], s["type"]) for s in gold["sections"] for obs in s["identifiers"]]
    expected_scoped += [(obs["raw_text"], None) for obs in gold.get("footer_identifiers", [])]
    m["section_scope"] = []
    for raw, section in expected_scoped:
        digits = re.sub(r"\D", "", raw)
        hit = next((o for o in context if re.sub(r"\D", "", o["raw_text"]) == digits and
                    ((section is None and o["scope"] == "document") or
                     (section is not None and o["scope"] == "section"
                      and (o.get("section_label") or "").upper() == section))), None)
        m["section_scope"].append({"expected": raw, "section": section, "found": hit is not None})
    line_values = {v for g in g_lines for v in gold_values(g)}
    m["line_value_in_context"] = [o["raw_text"] for o in context if loose(o["raw_text"]) in line_values]

    m["association"] = association(result, anchors)
    m["duplicates"] = duplicates(result, owners)
    m["section_inheritance"] = section_inheritance(result, ocr_text)
    all_obs = [o for row in result["lines"] for o in row["observations"]] + context
    provenance = [(o["raw_text"], ocr_provenance(o["raw_text"], ocr_text)) for o in all_obs]
    m["raw_fidelity"] = {
        "observations": len(all_obs),
        "exact_in_ocr": sum(p == "exact" for _, p in provenance),
        "joined_from_ocr_pieces": [r for r, p in provenance if p == "joined"],
        "not_found_in_ocr": [r for r, p in provenance if p == "not_found"],
        "verbatim_vs_gold": f"{m['verbatim']}/{m['matched']}",
    }

    if gold.get("document_class") == "service":
        # Counted only. The observation layer does not decide NOT_APPLICABLE.
        m["service_document_counts"] = {
            "observations": len(all_obs),
            "gtin_candidate_hints": sum(o["role_hint"] == "gtin_candidate" for o in all_obs),
        }

    # v11 baseline characterization (the recorded v11 output; v11 is not re-run).
    supplier_values = {loose(o["raw_text"]) for g in g_lines for o in g["identifiers"]
                       if o["role_expected"] == "supplier_item_id"}
    codes = [loose(line.get("product_code")) for line in anchors]
    m["v11_baseline"] = {
        "supplier_id_as_upc": [c for c in codes if c and c in supplier_values],
        "gtin13_14_truncation_risk": [c for c in codes if c and len(re.sub(r"\D", "", c)) in (13, 14)],
    }
    return m


def label_key(label: str | None) -> str:
    """'ITEM#' ~ 'ITEM', 'UPC:' ~ 'UPC', 'Stock #' ~ 'STOCK' — for column matching only."""
    return re.sub(r"[^0-9A-Z]", "", (label or "").upper())


def column_coverage(g_lines: list[dict], rows_for_gold: dict[int, list[dict]], result: dict) -> dict[str, Any]:
    """
    Per gold column (the printed_label the gold records), over the gold rows
    some returned row represents:

      value_on_row          the gold value observed on its row (verbatim up to whitespace);
      value_anywhere        the gold value observed on any row;
      column_seen_on_row    the row has the gold value OR an observation carrying that
                            column's label — the column was read even if the OCR
                            garbled the value (separates OCR misreads from omission).

    A column is 'mostly_omitted' when column_seen_on_row covers under half its rows.
    """
    everywhere = {loose(o["raw_text"]) for row in result["lines"] for o in row["observations"]}
    columns: dict[str, dict[str, int]] = {}
    for gi, g in enumerate(g_lines):
        if gi not in rows_for_gold:
            continue
        observed = rows_for_gold[gi]
        for gold_obs in g["identifiers"]:
            column = gold_obs.get("printed_label") or f"(unlabelled {gold_obs['role_expected']})"
            c = columns.setdefault(column, {"gold_rows": 0, "value_on_row": 0, "value_anywhere": 0,
                                            "column_seen_on_row": 0})
            key = loose(gold_obs["raw_text"])
            on_row = any(loose(o["raw_text"]) == key for o in observed)
            labelled = bool(gold_obs.get("printed_label")) and any(
                label_key(o.get("printed_label")) == label_key(gold_obs["printed_label"]) for o in observed)
            c["gold_rows"] += 1
            c["value_on_row"] += on_row
            c["value_anywhere"] += key in everywhere
            c["column_seen_on_row"] += on_row or labelled
    return {"columns": columns,
            "mostly_omitted": sorted(k for k, c in columns.items() if c["column_seen_on_row"] * 2 < c["gold_rows"])}


def multi_value_piece(key: str, gold_line: dict) -> bool:
    return bool(key) and any("," in o["raw_text"] and key in loose(o["raw_text"]) for o in gold_line["identifiers"])


def multi_value_status(cell: str, observed: list[dict], where: str) -> dict[str, Any]:
    """preserved = one verbatim observation; split = the cell spread over several; else truncated/missing."""
    key = loose(cell)
    raws = [o["raw_text"] for o in observed]
    inside = [r for r in raws if loose(r) and loose(r) in key]
    if cell in raws:
        status = "preserved"
    elif key in {loose(r) for r in raws}:
        status = "preserved_whitespace_differs"
    elif len(inside) > 1 and key in "".join(loose(r) for r in inside):
        status = "split"
    elif inside:
        status = "truncated"
    else:
        status = "missing"
    return {"line": where, "cell": cell, "status": status, "observed": inside}


def association(result: dict, anchors: list[dict]) -> dict[str, Any]:
    """Observation-to-line association consistency. Diagnostics: reported, never corrected."""
    rows = {row["line_index"]: row["observations"] for row in result["lines"] if row["line_index"] is not None}
    gtin_counts = [sum(o["role_hint"] == "gtin_candidate" for o in rows.get(i, []))
                   for i in range(1, len(anchors) + 1)]
    adjacent = [f"line {i + 1} has 0 gtin hints, line {j + 1} has {gtin_counts[j]}"
                for i in range(len(gtin_counts)) for j in (i - 1, i + 1)
                if 0 <= j < len(gtin_counts) and gtin_counts[i] == 0 and gtin_counts[j] >= 2]
    same = other = absent = 0
    elsewhere = []
    for i, line in enumerate(anchors, 1):
        for field in ("product_code", "supplier_item_id"):
            value = alnum(line.get(field))
            if not value:
                continue
            found = sorted(j for j, obs in rows.items() if j != i and value in {alnum(o["raw_text"]) for o in obs})
            if value in {alnum(o["raw_text"]) for o in rows.get(i, [])}:
                same += 1
            elif found:
                other += 1
                elsewhere.append(f"v11 line {i} {field}={line.get(field)!r} observed on line(s) {found}")
            else:
                absent += 1
    return {"zero_gtin_next_to_multi_gtin": adjacent,
            "v11_value_on_same_row": same, "v11_value_on_other_row": other, "v11_value_not_observed": absent,
            "v11_value_elsewhere": elsewhere}


def duplicates(result: dict, owners: dict[str, set[int]]) -> dict[str, Any]:
    seen: dict[str, list[str]] = {}
    within_row = []
    for where, row in zip(row_names(result), result["lines"], strict=True):
        keys = [loose(o["raw_text"]) for o in row["observations"]]
        within_row += [f"{where}: {k}" for k in sorted({k for k in keys if keys.count(k) > 1})]
        for k in set(keys):
            seen.setdefault(k, []).append(where)
    across = {k: w for k, w in seen.items() if len(w) > 1}
    context_keys = {loose(o["raw_text"]) for o in result["context_observations"]}
    return {
        "across_rows_printed_on_several_gold_lines": {k: w for k, w in across.items() if len(owners.get(k, ())) > 1},
        "across_rows_unexpected": {k: w for k, w in across.items() if len(owners.get(k, ())) <= 1},
        "within_one_row": within_row,
        "in_line_and_context": sorted(k for k in context_keys if k in seen),
    }


def section_inheritance(result: dict, ocr_text: str) -> dict[str, Any]:
    line_labels = {o.get("section_label") for row in result["lines"] for o in row["observations"]}
    labels = line_labels | {o.get("section_label") for o in result["context_observations"]}
    row_labels = [(row["line_index"], {o.get("section_label") for o in row["observations"]})
                  for row in result["lines"]]
    sequence = [next(iter(s)) for index, s in row_labels if index is not None and len(s) == 1 and next(iter(s))]
    scoped = {o["section_label"] for o in result["context_observations"]
              if o["scope"] == "section" and o.get("section_label")}
    return {
        "labels_not_printed": sorted({x for x in labels if x and not heading_printed(x, ocr_text)}),
        "rows_with_mixed_labels": [index for index, s in row_labels if len(s) > 1],
        "label_blocks_not_contiguous": sorted({x for i, x in enumerate(sequence)
                                               if x in sequence[:i] and sequence[i - 1] != x}),
        # A section-scope value was recorded for a heading no row observation carries.
        "section_scope_labels_without_line_labels": sorted(scoped - line_labels),
    }


def summary(metrics: dict[str, Any]) -> dict[str, Any]:
    """Counts only."""
    sc, cov = metrics["section_context"], metrics["coverage"]
    return {
        "matched": metrics["matched"], "verbatim": metrics["verbatim"],
        **{k: len(metrics[k]) for k in ("missing", "wrong_role", "wrong_line", "wrong_position", "extra")},
        "section_context": f"{sc['ok']}/{sc['checked']}",
        "section_scope_found": f"{sum(s['found'] for s in metrics['section_scope'])}/{len(metrics['section_scope'])}",
        "coverage_ok": not (cov["missing_indices"] or cov["duplicate_indices"] or cov["out_of_range_indices"])
                       and cov["numbered_rows_in_order_before_null_rows"],
        "null_rows": cov["null_rows"],
        "not_found_in_ocr": len(metrics["raw_fidelity"]["not_found_in_ocr"]),
        "columns_mostly_omitted": metrics["column_coverage"]["mostly_omitted"],
        "null_rows_recovered": f"{len(metrics['unlisted_rows']['found_as_null_row'])}/"
                               f"{len(metrics['unlisted_rows']['gold_lines_without_v11_line'])}",
        "multi_value": [c["status"] for c in metrics["multi_value_cells"]],
    }


def main() -> None:
    report: dict[str, Any] = {}
    for gold_path in sorted(GOLD_DIR.glob("*.json")):
        name = gold_path.stem
        folder = SHADOW_DIR / name
        needed = [folder / f for f in ("ocr.json", "v11.json", "observations.json")]
        if not all(p.exists() for p in needed):
            report[name] = {"status": "not run"}
            continue
        ocr_text = json.loads(needed[0].read_text())["full_text"]
        current = json.loads(needed[1].read_text())
        recorded = json.loads(needed[2].read_text())
        gold = json.loads(gold_path.read_text())
        metrics = evaluate(gold, current["parsed"], recorded["parsed"], ocr_text)
        v11_now = hashlib.sha256(needed[1].read_bytes()).hexdigest()
        metrics["v11_unchanged"] = recorded["v11_sha256_before"] == recorded["v11_sha256_after"] == v11_now
        metrics["tokens"] = {k: recorded[k] for k in ("input_tokens", "output_tokens", "total_tokens")}
        metrics["model"] = recorded["model"]
        metrics["prompt_version"] = recorded["prompt_version"]
        metrics["previous_runs"] = {
            path.stem: summary(evaluate(gold, current["parsed"], json.loads(path.read_text())["parsed"], ocr_text))
            for path in sorted(folder.glob("observations.run*.json"))
        }
        metrics["summary"] = summary(metrics)
        report[name] = metrics
    SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    (SHADOW_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: v.get("summary", v) for k, v in report.items()}, indent=2, default=str))


if __name__ == "__main__":
    main()
