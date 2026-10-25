"""
Identifier templates — app/services/product_master/identifier_templates.py

A template describes where a vendor's invoice PRINTS identifiers and what
each printed field is: its label, role, grammar, row relation, section
scope and barcode convention. It is document structure, nothing more. A
template never holds a UPC value, a product mapping, a prompt or a business
decision; named derivation rules it lists carry their own approval state,
and a DRAFT rule can never make a result EDI-eligible.

Templates are versioned JSON files in identifier_template_data/, reviewed
like code and exercised against the Phase 1 gold fixtures. The resolver
(app/services/product_master/resolution.py) uses a template to:

  * assign each printed value to a field by grammar, then label — never
    by any role an LLM suggested;
  * choose the SourceProfile convention for a barcode field;
  * run association checks (per-row counts, OCR order relative to the
    row's description);
  * gate derivation rules by their state;
  * mark a service document NOT_APPLICABLE.

Pure: reads its own data files, opens no database connection, and is not
reachable from invoice processing or the EDI writer.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

TEMPLATE_DIR = Path(__file__).with_name("identifier_template_data")

LIFECYCLE_DRAFT = "DRAFT"
LIFECYCLE_SHADOW = "SHADOW"
LIFECYCLE_APPROVED = "APPROVED"
LIFECYCLE_RETIRED = "RETIRED"
VALID_LIFECYCLES = frozenset({LIFECYCLE_DRAFT, LIFECYCLE_SHADOW, LIFECYCLE_APPROVED, LIFECYCLE_RETIRED})
SELECTABLE_LIFECYCLES = frozenset({LIFECYCLE_SHADOW, LIFECYCLE_APPROVED})

RULE_DRAFT = "DRAFT"
RULE_APPROVED = "APPROVED"
RULE_RETIRED = "RETIRED"
VALID_RULE_STATES = frozenset({RULE_DRAFT, RULE_APPROVED, RULE_RETIRED})

FIELD_BARCODE = "BARCODE"
FIELD_SUPPLIER_ID = "SUPPLIER_ID"
FIELD_FRAGMENT = "FRAGMENT"
FIELD_MANUFACTURER = "MANUFACTURER"
VALID_FIELD_ROLES = frozenset({FIELD_BARCODE, FIELD_SUPPLIER_ID, FIELD_FRAGMENT, FIELD_MANUFACTURER})

DOCUMENT_MERCHANDISE = "merchandise"
DOCUMENT_SERVICE = "service"
VALID_DOCUMENT_CLASSES = frozenset({DOCUMENT_MERCHANDISE, DOCUMENT_SERVICE})

RELATION_BEFORE = "before_description"
RELATION_AFTER = "after_description"
RELATION_UNKNOWN = "unknown"
VALID_RELATIONS = frozenset({RELATION_BEFORE, RELATION_AFTER, RELATION_UNKNOWN})

PER_ROW_EXACTLY_ONE = "exactly_one"
PER_ROW_AT_MOST_ONE = "at_most_one"
PER_ROW_ANY = "any"
VALID_PER_ROW = frozenset({PER_ROW_EXACTLY_ONE, PER_ROW_AT_MOST_ONE, PER_ROW_ANY})

VALID_SCOPES = frozenset({"line", "section", "document"})
VALID_POSITIONS = frozenset({"main_line", "sub_row"})
VALID_BARCODE_CONVENTIONS = frozenset({"FULL_BARCODE", "CHECK_DIGIT_OMITTED", "UNKNOWN"})

RULE_MANUFACTURER_FRAGMENT_UPC_A = "MANUFACTURER_FRAGMENT_UPC_A"
VALID_RULE_KINDS = frozenset({RULE_MANUFACTURER_FRAGMENT_UPC_A})


def label_key(label: str | None) -> str:
    """'ITEM#' ~ 'Item', 'UPC:' ~ 'UPC', 'Stock #' ~ 'STOCK' — for label matching only."""
    return re.sub(r"[^0-9A-Z]", "", (label or "").upper())


def name_key(text: str | None) -> str:
    """Vendor-name matching: upper case, '&' kept, every other symbol a single space."""
    return " ".join(re.sub(r"[^0-9A-Z&]", " ", (text or "").upper()).split())


@dataclass(frozen=True)
class TemplateField:
    """One identifier field the document prints."""

    name: str
    role: str
    grammar: str
    labels: tuple[str, ...] = ()
    scope: str = "line"
    position: str | None = None
    per_row: str = PER_ROW_ANY
    ocr_relation: str = RELATION_UNKNOWN
    barcode_convention: str | None = None
    separators: str = ""
    multi_value_separator: str | None = None
    capture_group: int | None = None
    allowed_lengths: tuple[int, ...] = ()
    letters_allowed: bool = False

    def matches(self, raw: str) -> bool:
        """
        Grammar, then the declared constraints: no letters unless allowed, and
        (when declared) each value's length after removing this field's own
        separators and multi-value separator must be an allowed length.
        """
        text = raw.strip()
        if re.fullmatch(self.grammar, text) is None:
            return False
        if not self.letters_allowed and re.search(r"[A-Za-z]", text) and self.capture_group is None:
            return False
        if self.allowed_lengths:
            pieces = text.split(self.multi_value_separator) if self.multi_value_separator else [text]
            for piece in pieces:
                core = re.sub("[" + re.escape(self.separators) + "]", "", piece) if self.separators else piece
                if len(core) not in self.allowed_lengths:
                    return False
        return True

    def captured(self, raw: str) -> str | None:
        """The digits a manufacturer field declares (e.g. '28400' of '28400 FRITOLAY')."""
        match = re.fullmatch(self.grammar, raw.strip())
        if match is None:
            return None
        return match.group(self.capture_group) if self.capture_group else match.group(0)

    def has_label(self, label: str | None) -> bool:
        key = label_key(label)
        return bool(key) and key in {label_key(x) for x in self.labels}


@dataclass(frozen=True)
class DerivationRule:
    """A named derivation. Its state, not its output, decides whether it may be trusted."""

    name: str
    kind: str
    state: str
    manufacturer_field: str
    fragment_field: str
    number_system: str
    manufacturer_digits: int
    fragment_digits: int
    manufacturer_scope: str = "section"
    decisions: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdentifierTemplate:
    template_id: str
    version: int
    lifecycle: str
    document_class: str
    fields: tuple[TemplateField, ...]
    vendor_aliases: tuple[str, ...] = ()
    ocr_vendor_markers: tuple[str, ...] = ()
    layout_signature: tuple[str, ...] = ()
    derivation_rules: tuple[DerivationRule, ...] = ()
    noise_patterns: tuple[str, ...] = ()
    supplier_scope: str | None = None
    pending_decisions: tuple[str, ...] = ()
    fixtures: tuple[str, ...] = ()
    notes: str = ""
    content_sha256: str = field(default="", compare=False)

    def is_noise(self, raw: str) -> bool:
        """A printed value the template declares is never an identifier (e.g. a route number)."""
        return any(re.fullmatch(pattern, raw.strip()) for pattern in self.noise_patterns)

    @property
    def crosswalk_scope(self) -> str:
        """The supplier scope approved crosswalk rows are recorded under (source_distributor)."""
        return self.supplier_scope or self.template_id

    def field_named(self, name: str) -> TemplateField:
        return next(f for f in self.fields if f.name == name)

    def line_fields(self) -> tuple[TemplateField, ...]:
        return tuple(f for f in self.fields if f.scope == "line")

    def context_fields(self) -> tuple[TemplateField, ...]:
        return tuple(f for f in self.fields if f.scope != "line")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def parse_template(data: dict, *, source: str = "<dict>") -> IdentifierTemplate:
    """Build and validate one template. Anything unrecognised is an error, not a default."""
    where = f"{source}: "
    fields = []
    for raw in data["fields"]:
        f = TemplateField(
            name=raw["name"], role=raw["role"], grammar=raw["grammar"],
            labels=tuple(raw.get("labels", ())), scope=raw.get("scope", "line"),
            position=raw.get("position"), per_row=raw.get("per_row", PER_ROW_ANY),
            ocr_relation=raw.get("ocr_relation", RELATION_UNKNOWN),
            barcode_convention=raw.get("barcode_convention"),
            separators=raw.get("separators", ""),
            multi_value_separator=raw.get("multi_value_separator"),
            capture_group=raw.get("capture_group"),
            allowed_lengths=tuple(raw.get("allowed_lengths", ())),
            letters_allowed=bool(raw.get("letters_allowed", False)),
        )
        _require(f.role in VALID_FIELD_ROLES, f"{where}field {f.name}: role {f.role!r}")
        _require(f.scope in VALID_SCOPES, f"{where}field {f.name}: scope {f.scope!r}")
        _require(f.position is None or f.position in VALID_POSITIONS, f"{where}field {f.name}: position")
        _require(f.per_row in VALID_PER_ROW, f"{where}field {f.name}: per_row {f.per_row!r}")
        _require(f.ocr_relation in VALID_RELATIONS, f"{where}field {f.name}: ocr_relation")
        _require((f.role == FIELD_BARCODE) == (f.barcode_convention is not None),
                 f"{where}field {f.name}: a barcode_convention is required exactly for BARCODE fields")
        _require(f.barcode_convention is None or f.barcode_convention in VALID_BARCODE_CONVENTIONS,
                 f"{where}field {f.name}: barcode_convention {f.barcode_convention!r}")
        re.compile(f.grammar)
        _require(all(isinstance(n, int) and n > 0 for n in f.allowed_lengths),
                 f"{where}field {f.name}: allowed_lengths must be positive integers")
        _require(f.role != FIELD_BARCODE or not f.letters_allowed,
                 f"{where}field {f.name}: a barcode field never allows letters")
        fields.append(f)
    names = [f.name for f in fields]
    _require(len(names) == len(set(names)), f"{where}duplicate field names")

    rules = []
    for raw in data.get("derivation_rules", ()):
        r = DerivationRule(
            name=raw["name"], kind=raw["kind"], state=raw["state"],
            manufacturer_field=raw["manufacturer_field"], fragment_field=raw["fragment_field"],
            number_system=raw["number_system"], manufacturer_digits=raw["manufacturer_digits"],
            fragment_digits=raw["fragment_digits"], manufacturer_scope=raw.get("manufacturer_scope", "section"),
            decisions=tuple(raw.get("decisions", ())),
        )
        _require(r.kind in VALID_RULE_KINDS, f"{where}rule {r.name}: kind {r.kind!r}")
        _require(r.state in VALID_RULE_STATES, f"{where}rule {r.name}: state {r.state!r}")
        _require(r.manufacturer_field in names and r.fragment_field in names,
                 f"{where}rule {r.name}: references an unknown field")
        rules.append(r)

    template = IdentifierTemplate(
        template_id=data["template_id"], version=data["version"], lifecycle=data["lifecycle"],
        document_class=data["document_class"], fields=tuple(fields),
        vendor_aliases=tuple(data.get("vendor_aliases", ())),
        ocr_vendor_markers=tuple(data.get("ocr_vendor_markers", ())),
        layout_signature=tuple(data.get("layout_signature", ())),
        derivation_rules=tuple(rules), pending_decisions=tuple(data.get("pending_decisions", ())),
        noise_patterns=tuple(data.get("noise_patterns", ())), supplier_scope=data.get("supplier_scope"),
        fixtures=tuple(data.get("fixtures", ())), notes=data.get("notes", ""),
        content_sha256=hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
    )
    for pattern in template.noise_patterns:
        re.compile(pattern)
    _require(template.lifecycle in VALID_LIFECYCLES, f"{where}lifecycle {template.lifecycle!r}")
    _require(template.document_class in VALID_DOCUMENT_CLASSES, f"{where}document_class")
    return template


def load_templates(directory: Path = TEMPLATE_DIR) -> list[IdentifierTemplate]:
    templates = [parse_template(json.loads(path.read_text()), source=path.name)
                 for path in sorted(directory.glob("*.json"))]
    ids = [t.template_id for t in templates]
    _require(len(ids) == len(set(ids)), "duplicate template_id")
    return templates


def _printed(token: str, ocr_text: str) -> bool:
    return re.search(rf"(?<![A-Za-z]){re.escape(token)}(?![A-Za-z])", ocr_text, re.IGNORECASE) is not None


def select_template(
    templates: list[IdentifierTemplate], vendor_name: str | None, ocr_text: str,
) -> tuple[IdentifierTemplate | None, str]:
    """
    The one template for this document, or None with the reason.

    A template qualifies when its vendor is recognised — an alias inside the
    extracted vendor name, or a vendor marker printed in the OCR text (the
    extracted vendor name can be wrong: v11 named the receiving store as the
    vendor of the Frito-Lay gold invoice) — AND every layout-signature token
    is printed. Zero or several qualifying templates select nothing.
    """
    vendor = name_key(vendor_name)
    ocr_names = name_key(ocr_text)
    hits: list[tuple[IdentifierTemplate, str]] = []
    for t in templates:
        if t.lifecycle not in SELECTABLE_LIFECYCLES:
            continue
        if any(name_key(a) and name_key(a) in vendor for a in t.vendor_aliases):
            how = "vendor_name"
        elif any(name_key(m) and name_key(m) in ocr_names for m in t.ocr_vendor_markers):
            how = "ocr_vendor_marker"
        else:
            continue
        if all(_printed(token, ocr_text) for token in t.layout_signature):
            hits.append((t, how))
    if len(hits) == 1:
        return hits[0][0], f"selected_by_{hits[0][1]}"
    return None, "no_template" if not hits else "several_templates_qualify"
