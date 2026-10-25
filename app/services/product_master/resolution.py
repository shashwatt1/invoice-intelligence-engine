"""
Deterministic identifier resolution — app/services/product_master/resolution.py

Phase 3A, SHADOW ONLY. Decides, for each invoice line, what its printed
identifiers establish — and says so with a status, the evidence and every
candidate derivation. It changes nothing: no invoice, Product Master,
commercial or case-mapping row is written, and neither the pipeline nor the
EDI writer calls it. EDI eligibility here is a *would-be* answer for shadow
reports; the production gate (export_service.pdi_export_eligibility) is
untouched.

Inputs, all read-only:
  * the v11 line items (the rows; their product_code / supplier_item_id are
    reads like any other, not decisions);
  * the frozen obs-v1.1 observations (evidence only, never authority);
  * the cached OCR text (to check where values sit relative to rows);
  * the vendor's identifier template (identifier_templates.py);
  * a Product Master lookup and a supplier crosswalk lookup, injected as
    callables so this module never opens a session and has no write path.

Ladder: R0 applicability, R1 candidates (every read of the row, template
field by grammar then label), R2 grammar/check validation, R3 printed
complete GTIN, R4 APPROVED template derivation, R5 CONFIRMED supplier
crosswalk, R6 identifier agreement, R7 description-size veto (can block,
never create), R8 prior reviewed history (disagreement is a veto). R9, fuzzy
suggestion, is deliberately not implemented here: a fuzzy engine may not
create identity, and suggestions belong to the review UI.

Every value is classified by app/services/product_master/identifiers.py —
this is an extension of that engine, not a second one. Its rules hold:
length never picks a repair; nothing is padded; no check digit is computed
to make a value valid; an invalid value stays invalid.

Shadow policy, fixed until a later phase changes it deliberately:
  * VERIFIED needs ALL of: a complete GTIN-12 with a valid check digit, a
    consistent association, an exact Product Master match in state
    CONFIRMED, and v11 AND the observation reading that same GTIN on that
    same line. The two reads come from the same OCR text, so their agreement
    is a consistency check, not independent evidence; a Product Master match
    on one read is CORROBORATED at most;
  * only VERIFIED and REVIEW_CONFIRMED on a GTIN-12 would be EDI-eligible —
    CORROBORATED and VALID_UNMATCHED never are, whatever D5/D6 decide;
  * a DRAFT derivation rule never produces an eligible result;
  * D20 is NO: an OCR-order finding becomes a reviewer proposal and an
    anomaly; a value is never moved to another row.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.product_master.identifier_templates import (
    DOCUMENT_SERVICE,
    FIELD_BARCODE,
    FIELD_MANUFACTURER,
    FIELD_SUPPLIER_ID,
    PER_ROW_EXACTLY_ONE,
    RELATION_AFTER,
    RELATION_BEFORE,
    RULE_APPROVED,
    IdentifierTemplate,
    TemplateField,
)
from app.services.product_master.identifiers import (
    BARCODE_CHECK_DIGIT_OMITTED,
    BARCODE_CONVENTION_UNKNOWN,
    BARCODE_FULL,
    DERIVE_SPLIT_MULTI_VALUE,
    DERIVE_STRIP_SEPARATORS,
    GTIN_12,
    GTIN_14,
    GTIN_KIND_BY_LENGTH,
    SourceProfile,
    compose_manufacturer_fragment,
    derive_identifier,
    eleven_digit_readings,
    has_valid_gtin_check,
    split_multi_value,
)

RESOLVER_VERSION = "phase3c-shadow-3"

STATUS_VERIFIED = "VERIFIED"
STATUS_REVIEW_CONFIRMED = "REVIEW_CONFIRMED"
STATUS_CORROBORATED = "CORROBORATED"
STATUS_VALID_UNMATCHED = "VALID_UNMATCHED"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_CONFLICT = "CONFLICT"
STATUS_INVALID = "INVALID"
STATUS_UNRESOLVED = "UNRESOLVED"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
ALL_STATUSES = (STATUS_VERIFIED, STATUS_REVIEW_CONFIRMED, STATUS_CORROBORATED, STATUS_VALID_UNMATCHED,
                STATUS_AMBIGUOUS, STATUS_CONFLICT, STATUS_INVALID, STATUS_UNRESOLVED, STATUS_NOT_APPLICABLE)

# Shadow mode: the only statuses that would be EDI-eligible. Not configurable here.
SHADOW_ELIGIBLE_STATUSES = frozenset({STATUS_VERIFIED, STATUS_REVIEW_CONFIRMED})

# Mirrors export_service.NON_PDI_LINE_TYPES (a test pins them equal) without
# importing the EDI module.
NOT_APPLICABLE_LINE_TYPES = frozenset({"charge", "duplicate", "voided"})

# Product Master evidence states that can ground identity. CONFLICT,
# UNRESOLVED and REVIEW_REQUIRED never do. Only CONFIRMED can verify.
STATE_CONFIRMED = "CONFIRMED"
COUNTING_EVIDENCE_STATES = frozenset({STATE_CONFIRMED, "AUTO_MATCHED"})

HUMAN_IDENTITY_FIELDS = frozenset({"product_code", "product_sku"})

KIND_SUPPLIER_ITEM_ID = "SUPPLIER_ITEM_ID"
KIND_FRAGMENT = "FRAGMENT"
KIND_ELEVEN_DIGIT = "ELEVEN_DIGIT"
KIND_LENGTH_MISMATCH = "LENGTH_MISMATCH"
KIND_NON_NUMERIC = "NON_NUMERIC"
KIND_COMPOSED_HYPOTHESIS = "COMPOSED_GTIN_12_HYPOTHESIS"
KIND_CONVENTION_DERIVED = "CONVENTION_DERIVED_GTIN_12"
KIND_UNASSIGNED = "UNASSIGNED"

JOIN_MAX_PIECES = 4
JOIN_MAX_SPAN = 400


@dataclass(frozen=True)
class PMMatch:
    """One Product Master fact a lookup returned. Read-only."""

    product_id: str
    evidence_state: str
    source_system: str
    canonical_upc: str | None = None
    descriptions: tuple[str, ...] = ()


PMLookup = Callable[[str], Sequence[PMMatch]]                 # canonical GTIN -> matches
CrosswalkLookup = Callable[[str, str], Sequence[PMMatch]]     # (supplier scope, supplier id) -> matches
HistoryLookup = Callable[[str, str], Sequence[str]]           # (supplier scope, supplier id) -> reviewed GTINs


def no_product_master(_gtin: str) -> Sequence[PMMatch]:
    """The lookup when no Product Master is reachable: it knows nothing."""
    return ()


def no_crosswalk(_scope: str, _supplier_item_id: str) -> Sequence[PMMatch]:
    return ()


def no_history(_scope: str, _supplier_item_id: str) -> Sequence[str]:
    return ()


_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(FL\s*OZ|OZ|0Z|ML|LTR|LITER|L|GAL)(?![A-Z])", re.IGNORECASE)
_UNIT = {"FLOZ": "OZ", "OZ": "OZ", "0Z": "OZ", "ML": "ML", "LTR": "L", "LITER": "L", "L": "L", "GAL": "GAL"}


def size_tokens(text: str | None) -> set[str]:
    """Explicit sizes printed in a description ('8.4OZ', '20 oz', '1 LTR'), normalised. OCR '0Z' reads as OZ."""
    out = set()
    for number, unit in _SIZE.findall((text or "").upper()):
        value = number.rstrip("0").rstrip(".") if "." in number else number
        unit_key = re.sub(r"\s", "", unit)
        out.add(f"{value}{_UNIT[unit_key]}")
    return out


def size_veto(description: str | None, pm_descriptions: Sequence[str]) -> bool:
    """
    R7. True only when the invoice line and EVERY Product Master description
    print explicit sizes and none of them share one. A veto can block an
    identity; nothing here can create one.
    """
    mine = size_tokens(description)
    theirs = [size_tokens(d) for d in pm_descriptions]
    return bool(mine) and bool(theirs) and all(t and not (t & mine) for t in theirs)


@dataclass(frozen=True)
class ResolverLine:
    """One v11 row, reduced to what resolution reads."""

    line_index: int
    description: str | None
    line_type: str | None = "product"
    product_code: str | None = None
    supplier_item_id: str | None = None
    corrected_fields: tuple[str, ...] = ()


@dataclass
class Read:
    """One printed value, from one source."""

    source: str
    raw: str
    printed_label: str | None = None
    section_label: str | None = None
    position: str | None = None


@dataclass
class LineResolution:
    line_index: int | None
    status: str
    reasons: list[str]
    description: str | None = None
    unlisted_row: bool = False
    candidates: list[dict] = field(default_factory=list)
    chosen_gtin: str | None = None
    chosen_kind: str | None = None
    would_be_eligible: bool = False
    pdi_item_code: str | None = None
    association: dict = field(default_factory=lambda: {"state": "not_checked", "anomalies": [], "notes": []})
    pm_evidence: list[dict] = field(default_factory=list)
    reread_triggers: list[dict] = field(default_factory=list)
    review_required: bool = False
    review_reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reads, field assignment and candidates
# ---------------------------------------------------------------------------


def _norm(text: str | None) -> str:
    return " ".join((text or "").upper().split())


def ocr_provenance(raw: str, ocr_text: str) -> str:
    """'exact', 'joined' (in-order OCR pieces within a short span) or 'not_found'."""
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


def _line_reads(line: ResolverLine, observed: list[dict], rereads: Sequence[dict] = ()) -> list[Read]:
    reads: list[Read] = []
    if line.product_code and line.product_code.strip():
        reads.append(Read("v11.product_code", line.product_code.strip()))
    if line.supplier_item_id and line.supplier_item_id.strip():
        reads.append(Read("v11.supplier_item_id", line.supplier_item_id.strip()))
    for obs in observed:
        if obs.get("raw_text"):
            reads.append(Read("observation", obs["raw_text"], obs.get("printed_label"),
                              obs.get("section_label"), obs.get("position")))
    for obs in rereads:
        if obs.get("raw_text"):
            reads.append(Read("reread", obs["raw_text"], obs.get("printed_label"),
                              obs.get("section_label"), obs.get("position")))
    return reads


def _in_description(raw: str, description: str | None) -> bool:
    value = raw.strip()
    return bool(value) and re.search(rf"(?<![0-9A-Za-z]){re.escape(value)}(?![0-9A-Za-z])",
                                     description or "", re.IGNORECASE) is not None


def assign_field(read: Read, template: IdentifierTemplate | None,
                 description: str | None = None) -> tuple[TemplateField | None, str, list[str]]:
    """
    The template field a printed value belongs to: grammar first, label to
    break a tie or to place a value whose grammar fails. The LLM's role_hint
    is never consulted.

    A value that fits no field's grammar but is printed inside the row's own
    description (M&N's '(58842)') is left unassigned: whether such codes are
    identifiers is pending classification (D21), so they neither resolve
    anything nor trigger a reread.
    """
    if template is None:
        return None, "no_template", []
    if template.is_noise(read.raw):
        return None, "noise_pattern", []
    fields = template.line_fields()
    by_grammar = [f for f in fields if f.matches(read.raw)]
    by_label = [f for f in fields if f.has_label(read.printed_label)]
    if len(by_grammar) == 1:
        flags = ["label_disagrees_with_grammar"] if by_label and by_grammar[0] not in by_label else []
        return by_grammar[0], "grammar", flags
    if len(by_grammar) > 1:
        both = [f for f in by_grammar if f in by_label]
        if len(both) == 1:
            return both[0], "grammar+label", []
        return None, "several_fields_match", []
    if _in_description(read.raw, description):
        return None, "in_description_pending_D21", []
    if len(by_label) == 1:
        return by_label[0], "label", ["grammar_violation"]
    return None, "no_template_field", []


def _barcode_candidates(read: Read, fld: TemplateField, template_id: str) -> list[dict]:
    raw = read.raw.strip()
    sep = fld.multi_value_separator
    pieces = split_multi_value(raw, sep) if sep and sep in raw else [raw]
    out = []
    for piece in pieces:
        text = piece.strip()
        chain: list[str] = []
        detail: dict[str, Any] = {}
        if len(pieces) > 1:
            chain.append(DERIVE_SPLIT_MULTI_VALUE)
        if fld.separators and any(c in text for c in fld.separators):
            text = re.sub("[" + re.escape(fld.separators) + "]", "", text)
            chain.append(DERIVE_STRIP_SEPARATORS)
        base: dict[str, Any] = {"source": read.source, "raw": read.raw, "piece": piece if len(pieces) > 1 else None,
                "field": fld.name, "field_role": fld.role, "derivation_chain": chain}
        if not text.isdigit():
            out.append({**base, "kind": KIND_NON_NUMERIC, "value": None, "check_valid": False})
            continue
        convention = fld.barcode_convention or BARCODE_FULL
        profile = SourceProfile(name=f"template:{template_id}:{fld.name}",
                                barcode_convention=(BARCODE_CHECK_DIGIT_OMITTED
                                                    if convention == BARCODE_CHECK_DIGIT_OMITTED else BARCODE_FULL))
        derived = derive_identifier(text, profile)
        detail["identifiers_engine"] = {"identifier_type": derived.identifier_type,
                                        "derivation": derived.derivation,
                                        "reason": derived.derivation_detail.get("reason")}
        length = len(text)
        if length in GTIN_KIND_BY_LENGTH:
            valid = has_valid_gtin_check(text)
            out.append({**base, "kind": GTIN_KIND_BY_LENGTH[length], "value": text if valid else None,
                        "digits": text, "check_valid": valid, **detail})
        elif length == 11 and convention == BARCODE_CHECK_DIGIT_OMITTED:
            out.append({**base, "kind": KIND_CONVENTION_DERIVED, "value": derived.canonical_upc, "digits": text,
                        "check_valid": True, "check_digit_computed": True,
                        "derivation_chain": [*chain, derived.derivation], **detail})
        elif length == 11 and convention == BARCODE_CONVENTION_UNKNOWN:
            out.append({**base, "kind": KIND_ELEVEN_DIGIT, "value": None, "digits": text, "check_valid": False,
                        "readings": eleven_digit_readings(text), **detail})
        else:
            out.append({**base, "kind": KIND_LENGTH_MISMATCH, "value": None, "digits": text,
                        "check_valid": False, **detail})
    return out


def _candidates_for(read: Read, fld: TemplateField | None, how: str, flags: list[str],
                    template: IdentifierTemplate | None) -> list[dict]:
    if template is None:
        # Generic: only a printed complete GTIN counts; v11's supplier field is a supplier read.
        if read.source == "v11.supplier_item_id":
            return [{"source": read.source, "raw": read.raw, "field": None, "field_role": FIELD_SUPPLIER_ID,
                     "assigned_by": how, "flags": flags, "kind": KIND_SUPPLIER_ITEM_ID,
                     "value": read.raw.strip(), "derivation_chain": []}]
        generic = TemplateField(name="GENERIC", role=FIELD_BARCODE, grammar=r".*", separators="- ",
                                barcode_convention=BARCODE_FULL)
        cands = _barcode_candidates(read, generic, "generic")
        return [{**c, "assigned_by": how, "flags": flags, "field": None} for c in cands
                if c["kind"] in GTIN_KIND_BY_LENGTH.values()]
    if fld is None:
        return [{"source": read.source, "raw": read.raw, "field": None, "field_role": None, "assigned_by": how,
                 "flags": flags, "kind": KIND_UNASSIGNED, "value": None, "derivation_chain": []}]
    if fld.role == FIELD_BARCODE:
        return [{**c, "assigned_by": how, "flags": flags} for c in _barcode_candidates(read, fld, template.template_id)]
    kind = KIND_SUPPLIER_ITEM_ID if fld.role == FIELD_SUPPLIER_ID else KIND_FRAGMENT
    return [{"source": read.source, "raw": read.raw, "field": fld.name, "field_role": fld.role,
             "assigned_by": how, "flags": flags, "kind": kind, "value": read.raw.strip(),
             "section_label": read.section_label, "derivation_chain": []}]


# ---------------------------------------------------------------------------
# Association checks (never move a value)
# ---------------------------------------------------------------------------


def locate_anchors(descriptions: Sequence[str | None], ocr_text: str) -> list[int | None]:
    """
    Where each row's description starts in the OCR text, in row order.

    Greedy and sequential: row k is searched after row k-1's anchor, first
    by its whole description, then by shorter leading-word prefixes (never
    fewer than two words, or one word of five characters or more). A row
    not found gets None and is skipped by the order check.
    """
    text = _norm(ocr_text)
    anchors: list[int | None] = []
    cursor = 0
    for description in descriptions:
        words = _norm(description).split()
        found = None
        for size in range(len(words), 0, -1):
            if size < 2 and (size == 0 or len(words[0]) < 5):
                break
            position = text.find(" ".join(words[:size]), cursor)
            if position >= 0:
                found = position
                break
        anchors.append(found)
        if found is not None:
            cursor = found + 1
    return anchors


def _ocr_order_findings(lines: list[ResolverLine], per_line: dict[int, list[dict]], ocr_text: str,
                        template: IdentifierTemplate) -> tuple[list[dict], set[tuple[int, str]]]:
    """
    Rows whose identifier sits, in the OCR text, beside another row's description.

    Evidence counts only when it is one-to-one. A value is judged only if it
    was assigned by grammar, is printed exactly once, its own row was located,
    and the row on the far side of its expected position was located too.
    Values that land on the same expected row (Carlson prints some pairs of
    rows' descriptions before both UPCs) are indeterminate, not anomalies.
    Nothing is moved: a finding is an anomaly plus a reviewer proposal.
    """
    text = _norm(ocr_text)
    anchors = locate_anchors([ln.description for ln in lines], ocr_text)
    anchor_of = {ln.line_index: a for ln, a in zip(lines, anchors, strict=True)}
    placed = [(i, a) for i, a in anchor_of.items() if a is not None]
    judged: list[tuple[dict, int, int, TemplateField]] = []
    for line in lines:
        if anchor_of[line.line_index] is None:
            continue
        for cand in per_line.get(line.line_index, []):
            if not cand.get("field") or cand.get("piece") or "grammar" not in cand.get("assigned_by", ""):
                continue
            fld = template.field_named(cand["field"])
            if fld.ocr_relation not in (RELATION_BEFORE, RELATION_AFTER):
                continue
            raw = _norm(cand["raw"])
            if not raw or text.count(raw) != 1:
                continue  # absent or printed more than once: order says nothing
            offset = text.find(raw)
            if fld.ocr_relation == RELATION_AFTER:
                before = [idx for idx, a in placed if a < offset]
                expected = before[-1] if before else None
                far_side = expected + 1 if expected is not None else None
            else:
                after = [idx for idx, a in placed if a > offset]
                expected = after[0] if after else None
                far_side = expected - 1 if expected is not None else None
            if expected is None or (far_side in anchor_of and anchor_of[far_side] is None):
                continue  # an unlocated neighbour could own this value
            judged.append((cand, line.line_index, expected, fld))
    landing: dict[tuple[str, int], set[str]] = {}
    for cand, _assigned, expected, fld in judged:
        landing.setdefault((fld.name, expected), set()).add(_norm(cand["raw"]))
    findings = []
    confirmations: set[tuple[int, str]] = set()
    for cand, assigned, expected, fld in judged:
        if len(landing[(fld.name, expected)]) > 1:
            continue  # indeterminate
        if expected == assigned:
            confirmations.add((assigned, _norm(cand["raw"])))
            continue
        findings.append({"kind": "ocr_order", "field": fld.name, "raw": cand["raw"],
                         "source": cand["source"], "assigned_line": assigned,
                         "ocr_order_suggests_line": expected, "auto_applied": False})
    return findings, confirmations


def _count_findings(lines: list[ResolverLine], per_line: dict[int, list[dict]],
                    template: IdentifierTemplate) -> list[dict]:
    findings = []
    indices = [ln.line_index for ln in lines]
    for fld in template.line_fields():
        if fld.per_row != PER_ROW_EXACTLY_ONE:
            continue
        counts = {i: len({_norm(c["raw"]) for c in per_line.get(i, [])
                          if c.get("field") == fld.name and "grammar" in c.get("assigned_by", "")})
                  for i in indices}
        for pos, i in enumerate(indices):
            if counts[i] != 0:
                continue
            for j in (indices[pos - 1] if pos else None, indices[pos + 1] if pos + 1 < len(indices) else None):
                if j is not None and counts[j] >= 2:
                    findings.append({"kind": "count_pattern", "field": fld.name, "line_without": i,
                                     "neighbour_with_several": j, "count": counts[j], "auto_applied": False})
    return findings


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


def _counting(matches: Sequence[PMMatch]) -> list[PMMatch]:
    return [m for m in matches if m.evidence_state in COUNTING_EVIDENCE_STATES]


def _products(matches: Sequence[PMMatch]) -> list[str]:
    return sorted({m.product_id for m in matches})


def _evidence(gtin: str, matches: Sequence[PMMatch], via: str) -> dict:
    return {"via": via, "key": gtin, "matches": [asdict(m) for m in matches],
            "counting_products": _products(_counting(matches))}


def _veto(line: LineResolution, matches: Sequence[PMMatch]) -> str | None:
    descriptions = [d for m in _counting(matches) for d in m.descriptions]
    return "description_size_veto" if size_veto(line.description, descriptions) else None


def _decide(line: LineResolution, cands: list[dict], anomalies: list[dict], template: IdentifierTemplate | None,
            context: list[dict], pm_lookup: PMLookup, crosswalk: CrosswalkLookup, scope: str,
            corrected: bool, history: HistoryLookup = no_history) -> None:
    gtins = [c for c in cands if c["kind"] in GTIN_KIND_BY_LENGTH.values()]
    valid = [c for c in gtins if c["check_valid"]]
    invalid = [c for c in gtins if not c["check_valid"]]
    values_by_read: dict[tuple[str, str], set[str]] = {}
    for c in valid:
        values_by_read.setdefault((c["source"], c["raw"]), set()).add(c["value"])
    distinct = sorted({c["value"] for c in valid})
    suppliers = [c for c in cands if c["kind"] == KIND_SUPPLIER_ITEM_ID]
    fragments = [c for c in cands if c["kind"] == KIND_FRAGMENT]
    eleven = [c for c in cands if c["kind"] == KIND_ELEVEN_DIGIT]
    convention = [c for c in cands if c["kind"] == KIND_CONVENTION_DERIVED]
    mismatched = [c for c in cands if c["kind"] in (KIND_LENGTH_MISMATCH, KIND_NON_NUMERIC)]

    for c in invalid:
        line.reread_triggers.append({"reason": "check_digit_failed", "raw": c["raw"], "source": c["source"]})
    for c in mismatched:
        line.reread_triggers.append({"reason": "barcode_length_or_format_violation", "raw": c["raw"],
                                     "source": c["source"]})
    for c in cands:
        if "grammar_violation" in c.get("flags", []):
            line.reread_triggers.append({"reason": "grammar_violation", "raw": c["raw"], "source": c["source"]})

    def choose(status: str, gtin: str | None, kind: str | None, *reasons: str) -> None:
        line.status, line.chosen_gtin, line.chosen_kind = status, gtin, kind
        line.reasons.extend(reasons)

    # Crosswalk: (supplier scope, supplier id) -> products with a canonical UPC.
    # Only an APPROVED crosswalk counts: a CONFIRMED Product Master row.
    crosswalk_upcs: set[str] = set()
    crosswalk_matches: list[PMMatch] = []
    history_gtins: set[str] = set()
    for c in sorted({c["value"] for c in suppliers}):
        matches = crosswalk(scope, c)
        if matches:
            line.pm_evidence.append(_evidence(c, matches, "supplier_crosswalk"))
        trusted = [m for m in matches if m.evidence_state == STATE_CONFIRMED and m.canonical_upc]
        crosswalk_matches += trusted
        crosswalk_upcs |= {m.canonical_upc for m in trusted if m.canonical_upc}
        history_gtins |= set(history(scope, c))

    # Human authority already recorded on the row.
    human = [c for c in valid if c["source"] == "v11.product_code" and c["kind"] == GTIN_12]
    if corrected and human and len(distinct) == 1:
        choose(STATUS_REVIEW_CONFIRMED, human[0]["value"], GTIN_12, "product_code_corrected_by_a_person")
        return

    disjoint = [k for k, v in values_by_read.items() if any(not (v & w) for w in values_by_read.values())]
    if len(distinct) > 1 and len(values_by_read) > 1 and disjoint:
        line.reread_triggers.append({"reason": "reads_disagree", "values": distinct})
        choose(STATUS_CONFLICT, None, None, "reads_disagree")
        return
    if invalid and valid:
        choose(STATUS_CONFLICT, None, None, "invalid_read_vs_valid_read")
        return
    row_upcs = {c["value"] for c in valid if c["kind"] == GTIN_12}
    if crosswalk_upcs and row_upcs and not (crosswalk_upcs & row_upcs):
        choose(STATUS_CONFLICT, None, None, "crosswalk_disagrees_with_printed_gtin")
        return
    if anomalies:
        choose(STATUS_AMBIGUOUS, None, None, "association_anomaly")
        return

    if len(distinct) > 1:
        matched = []
        for value in distinct:
            pm = pm_lookup(value)
            line.pm_evidence.append(_evidence(value, pm, "gtin"))
            if len(_products(_counting(pm))) == 1:
                matched.append(value)
        if len(matched) == 1:
            kind = next(c["kind"] for c in valid if c["value"] == matched[0])
            choose(STATUS_CORROBORATED, matched[0], kind, "one_value_of_multi_value_cell_matches_product_master")
        else:
            choose(STATUS_AMBIGUOUS, None, None, "several_gtins_in_one_cell")
        return

    if len(distinct) == 1:
        value = distinct[0]
        kind = next(c["kind"] for c in valid if c["value"] == value)
        pm = pm_lookup(value)
        line.pm_evidence.append(_evidence(value, pm, "gtin"))
        products = _products(_counting(pm))
        if len({c["source"] for c in valid}) > 1:
            line.association["notes"].append("two_reads_agree (same OCR text: consistent, not independent)")
        if kind == GTIN_14:
            choose(STATUS_AMBIGUOUS, value, kind, "gtin_14_packaging_level_unclear")
        elif len(products) > 1:
            choose(STATUS_AMBIGUOUS, value, kind, "product_master_maps_value_to_several_products")
        elif history_gtins and value not in history_gtins:
            choose(STATUS_CONFLICT, None, None, "reviewed_history_disagrees")
        elif (veto := _veto(line, pm)) is not None:
            choose(STATUS_CONFLICT, None, None, veto)
        elif len(products) == 1:
            sources = {c["source"] for c in valid if c["value"] == value}
            supported = set(line.association.get("positively_supported", []))
            missing = []
            if kind != GTIN_12:
                missing.append("not_gtin_12")
            if not ("observation" in sources and any(src.startswith("v11.") for src in sources)):
                missing.append("v11_and_observation_do_not_both_read_it")
            if not any(m.evidence_state == STATE_CONFIRMED for m in _counting(pm)):
                missing.append("product_master_match_not_confirmed")
            if not any(_norm(c["raw"]) in supported for c in valid if c["value"] == value):
                missing.append("association_not_positively_supported")
            reasons = ["exact_product_master_match"]
            if crosswalk_upcs and value in crosswalk_upcs:
                reasons.append("supplier_crosswalk_agrees")
            if history_gtins and value in history_gtins:
                reasons.append("reviewed_history_agrees")
            if missing:
                choose(STATUS_CORROBORATED, value, kind, *reasons, *(f"not_verified:{m}" for m in missing))
            else:
                choose(STATUS_VERIFIED, value, kind, *reasons)
        else:
            reasons = ["valid_gtin_not_in_product_master"]
            if pm:
                reasons.append("product_master_match_without_counting_evidence")
            choose(STATUS_VALID_UNMATCHED, value, kind, *reasons)
        if kind != GTIN_12:
            line.reasons.append("no_pdi_representation_for_non_upc_a (D8)")
        return

    if invalid:
        choose(STATUS_INVALID, None, None, "check_digit_failed_never_repaired")
        return

    if convention:
        value = convention[0]["value"]
        pm = pm_lookup(value)
        line.pm_evidence.append(_evidence(value, pm, "template_convention"))
        if len(_products(_counting(pm))) == 1:
            choose(STATUS_CORROBORATED, value, GTIN_12, "template_convention_value_matches_product_master")
        else:
            choose(STATUS_UNRESOLVED, None, None, "template_convention_value_unmatched")
        return

    if eleven:
        readings = eleven[0]["readings"]
        matched = []
        for reading in readings:
            pm = pm_lookup(reading["gtin"])
            line.pm_evidence.append(_evidence(reading["gtin"], pm, f"eleven_digit:{reading['reading']}"))
            if len(_products(_counting(pm))) == 1 and (reading["check_valid"] or not reading["validating"]):
                matched.append(reading["gtin"])
        leading = next(r for r in readings if r["validating"])
        if not leading["check_valid"]:
            line.reasons.append("leading_zero_reading_fails_check_digit")
            line.reread_triggers.append({"reason": "leading_zero_reading_fails_check_digit", "raw": eleven[0]["raw"]})
        if len(matched) == 1:
            choose(STATUS_CORROBORATED, matched[0], GTIN_12, "exactly_one_eleven_digit_reading_matches_product_master")
        else:
            choose(STATUS_AMBIGUOUS, None, None, "eleven_digit_convention_unknown (D19)")
        return

    if mismatched:
        choose(STATUS_UNRESOLVED, None, None, "barcode_read_unusable")
        return

    if fragments:
        _fragments(line, fragments, template, context, pm_lookup, choose)
        return

    if suppliers:
        upcs = sorted(crosswalk_upcs)
        if len(upcs) == 1 and (veto := _veto(line, crosswalk_matches)) is not None:
            choose(STATUS_CONFLICT, None, None, veto)
        elif len(upcs) == 1:
            choose(STATUS_CORROBORATED, upcs[0], GTIN_12, "confirmed_supplier_crosswalk_to_product_with_upc")
        elif len(upcs) > 1:
            choose(STATUS_AMBIGUOUS, None, None, "supplier_crosswalk_to_several_upcs")
        else:
            choose(STATUS_UNRESOLVED, None, None, "supplier_item_id_without_crosswalk")
        return

    choose(STATUS_UNRESOLVED, None, None, "no_identifier")


def _fragments(line: LineResolution, fragments: list[dict], template: IdentifierTemplate | None,
               context: list[dict], pm_lookup: PMLookup, choose: Callable[..., None]) -> None:
    rules = [r for r in (template.derivation_rules if template else ()) if r.fragment_field == fragments[0]["field"]]
    if not rules:
        choose(STATUS_UNRESOLVED, None, None, "fragment_without_derivation_rule")
        return
    rule = rules[0]
    section = next((c.get("section_label") for c in fragments if c.get("section_label")), None)
    manufacturers = sorted({c["value"] for c in context if c["field"] == rule.manufacturer_field
                            and (rule.manufacturer_scope != "section" or c.get("section_label") == section)})
    if len(manufacturers) != 1:
        choose(STATUS_UNRESOLVED, None, None, "manufacturer_scope_unresolved (D1)")
        return
    recorded: set[str] = set()
    for frag in fragments:
        composed = compose_manufacturer_fragment(
            manufacturers[0], frag["value"], number_system=rule.number_system,
            manufacturer_digits=rule.manufacturer_digits, fragment_digits=rule.fragment_digits)
        if composed is None:
            frag["hypothesis"] = {"rule": rule.name, "rule_state": rule.state, "composed": None,
                                  "reason": "fragment_outside_rule_width_never_padded"}
            continue
        frag["hypothesis"] = {"rule": rule.name, "rule_state": rule.state, "decisions": list(rule.decisions),
                              "manufacturer": manufacturers[0], **composed}
        if composed["gtin"] in recorded:
            continue  # the same fragment read twice (v11 and observation): one hypothesis
        recorded.add(composed["gtin"])
        pm = pm_lookup(composed["gtin"])
        line.pm_evidence.append(_evidence(composed["gtin"], pm, f"rule:{rule.name}"))
        line.candidates.append({"source": "derived", "raw": frag["raw"], "field": frag["field"],
                                "kind": KIND_COMPOSED_HYPOTHESIS, "value": composed["gtin"],
                                "rule": rule.name, "rule_state": rule.state,
                                "derivation_chain": [composed["derivation"]], "check_digit_computed": True})
        if rule.state == RULE_APPROVED and len(_products(_counting(pm))) == 1:
            choose(STATUS_CORROBORATED, composed["gtin"], GTIN_12, "approved_rule_value_matches_product_master")
            return
    reason = ("fragment_rule_is_draft" if rule.state != RULE_APPROVED
              else "approved_rule_value_unmatched")
    if all(f.get("hypothesis", {}).get("composed", True) is None for f in fragments):
        reason = "fragment_outside_rule_width_never_padded"
    choose(STATUS_UNRESOLVED, None, None, reason)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _inputs_hash(lines: Sequence[ResolverLine], observations: dict | None, ocr_text: str,
                 template: IdentifierTemplate | None, scope: str, rereads: dict | None = None) -> str:
    payload = {"lines": [asdict(ln) for ln in lines], "observations": observations, "ocr_text": ocr_text,
               "rereads": rereads,
               "template": template.content_sha256 if template else None, "scope": scope,
               "resolver": RESOLVER_VERSION}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def resolve_invoice(
    lines: Sequence[ResolverLine],
    observations: dict | None,
    ocr_text: str,
    template: IdentifierTemplate | None,
    *,
    supplier_scope: str | None = None,
    pm_lookup: PMLookup = no_product_master,
    crosswalk_lookup: CrosswalkLookup = no_crosswalk,
    history_lookup: HistoryLookup = no_history,
    rereads: dict[int, list[dict]] | None = None,
    template_selection: str | None = None,
) -> dict[str, Any]:
    """Resolve every line of one invoice. Pure: same inputs, same output."""
    lines = list(lines)
    rereads = rereads or {}
    scope = supplier_scope or (template.crosswalk_scope if template else "unknown")
    obs_rows = (observations or {}).get("lines", [])
    by_index: dict[int, list[dict]] = {}
    unlisted: list[list[dict]] = []
    for row in obs_rows:
        if row.get("line_index") is None:
            unlisted.append(row.get("observations", []))
        else:
            by_index.setdefault(row["line_index"], []).extend(row.get("observations", []))

    context: list[dict] = []
    if template is not None:
        for obs in (observations or {}).get("context_observations", []):
            for ctx_field in template.context_fields():
                if ctx_field.role == FIELD_MANUFACTURER and (ctx_field.matches(obs["raw_text"])
                                                             or ctx_field.has_label(obs.get("printed_label"))):
                    digits = ctx_field.captured(obs["raw_text"])
                    if digits:
                        context.append({"field": ctx_field.name, "value": digits, "raw": obs["raw_text"],
                                        "section_label": obs.get("section_label"), "scope": obs.get("scope")})

    per_line: dict[int, list[dict]] = {}
    for line in lines:
        cands: list[dict] = []
        for read in _line_reads(line, by_index.get(line.line_index, []), rereads.get(line.line_index, ())):
            fld, how, flags = assign_field(read, template, line.description)
            cands.extend(_candidates_for(read, fld, how, flags, template))
        per_line[line.line_index] = cands

    anomalies: list[dict] = []
    confirmations: set[tuple[int, str]] = set()
    if template is not None and template.document_class != DOCUMENT_SERVICE:
        order_findings, confirmations = _ocr_order_findings(lines, per_line, ocr_text, template)
        anomalies = order_findings + _count_findings(lines, per_line, template)

    results = []
    for line in lines:
        res = LineResolution(line_index=line.line_index, status=STATUS_UNRESOLVED, reasons=[],
                             description=line.description, candidates=per_line[line.line_index])
        mine = [a for a in anomalies if line.line_index in (a.get("assigned_line"), a.get("ocr_order_suggests_line"),
                                                           a.get("line_without"), a.get("neighbour_with_several"))]
        if template is not None and template.document_class != DOCUMENT_SERVICE:
            supported = sorted(raw for index, raw in confirmations if index == line.line_index)
            state = "anomaly" if mine else ("positive" if supported else "indeterminate")
            res.association = {"state": state, "anomalies": mine, "positively_supported": supported, "notes": []}
        if (line.line_type or "product") in NOT_APPLICABLE_LINE_TYPES:
            res.status, res.reasons = STATUS_NOT_APPLICABLE, [f"line_type_{line.line_type}"]
        elif template is not None and template.document_class == DOCUMENT_SERVICE:
            res.status, res.reasons = STATUS_NOT_APPLICABLE, ["service_document_template (D11 decides export)"]
        else:
            for c in res.candidates:
                if c["source"] == "observation" and ocr_text and ocr_provenance(c["raw"], ocr_text) == "not_found":
                    res.reread_triggers.append({"reason": "observation_text_not_in_ocr", "raw": c["raw"]})
            corrected = bool(HUMAN_IDENTITY_FIELDS & set(line.corrected_fields))
            _decide(res, res.candidates, mine, template, context, pm_lookup, crosswalk_lookup, scope, corrected,
                    history_lookup)
        _finish(res, template)
        results.append(res)

    for position, observed in enumerate(unlisted, 1):
        res = LineResolution(line_index=None, status=STATUS_UNRESOLVED, reasons=["row_not_in_v11"],
                             unlisted_row=True)
        ghost = ResolverLine(line_index=-position, description=None)
        ghost_candidates: list[dict] = []
        for read in _line_reads(ghost, observed):
            fld, how, flags = assign_field(read, template, None)
            ghost_candidates.extend(_candidates_for(read, fld, how, flags, template))
        res.candidates = ghost_candidates
        _finish(res, template)
        results.append(res)

    counts = {s: sum(r.status == s for r in results) for s in ALL_STATUSES}
    return {
        "resolver_version": RESOLVER_VERSION,
        "mode": "shadow",
        "template": ({"template_id": template.template_id, "version": template.version,
                      "lifecycle": template.lifecycle, "content_sha256": template.content_sha256}
                     if template else None),
        "template_selection": template_selection,
        "supplier_scope": scope,
        "inputs_hash": _inputs_hash(lines, observations, ocr_text, template, scope, rereads),
        "status_counts": counts,
        "association_proposals": [a for a in anomalies if a["kind"] == "ocr_order"],
        "anomalies": anomalies,
        "manufacturer_context": context,
        "lines": [asdict(r) for r in results],
    }


def _finish(res: LineResolution, template: IdentifierTemplate | None) -> None:
    """Shadow eligibility, the would-be PDI code and review triggers. Nothing here is applied."""
    res.would_be_eligible = (res.status in SHADOW_ELIGIBLE_STATUSES and res.chosen_kind == GTIN_12
                             and bool(res.chosen_gtin) and not res.unlisted_row)
    res.pdi_item_code = res.chosen_gtin[:11] if res.would_be_eligible and res.chosen_gtin else None
    reasons = []
    if res.status not in (STATUS_VERIFIED, STATUS_REVIEW_CONFIRMED, STATUS_NOT_APPLICABLE):
        reasons.append(f"status_{res.status}")
    if res.association.get("anomalies"):
        reasons.append("association_anomaly")
    if res.reread_triggers:
        reasons.append("reread_requested")
    if template is None:
        reasons.append("no_template")
    if res.unlisted_row:
        reasons.append("row_not_in_v11")
    res.review_reasons = reasons
    res.review_required = bool(reasons)
