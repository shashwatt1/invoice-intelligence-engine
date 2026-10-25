"""
Resolution record building — app/services/product_master/resolution_records.py

Pure functions that turn resolver output and observations into the rows of
the two append-only tables (app/models/identifier_resolution.py). No
database access here: the repository (app/repositories/
identifier_resolution_repository.py) inserts what these build, so the
mapping is testable without a database and cannot drift from it.

Rows are always NEW rows. A REVIEW_CONFIRMED decision is recorded as a new
resolution row whose provenance names the approved proposal; the resolver's
earlier row is left exactly as it was.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from app.models.identifier_resolution import (
    OBS_SOURCE_LLM,
    OBS_SOURCE_V11,
    VALID_OBSERVATION_SOURCES,
)

OBSERVATION_FIELDS = ("raw_text", "printed_label", "role_hint", "scope", "section_label", "position", "page")


def observation_rows(
    *, invoice_id: uuid.UUID, run_id: uuid.UUID, observation_result: dict,
    item_ids: Mapping[int, uuid.UUID], source: str = OBS_SOURCE_LLM,
    prompt_version: str | None = None, prompt_sha256: str | None = None, model: str | None = None,
    provenance: dict | None = None,
) -> list[dict[str, Any]]:
    """Every observation of one result, line and context, exactly as observed."""
    if source not in VALID_OBSERVATION_SOURCES:
        raise ValueError(f"source must be one of {sorted(VALID_OBSERVATION_SOURCES)}")
    common = {"invoice_id": invoice_id, "run_id": run_id, "source": source, "prompt_version": prompt_version,
              "prompt_sha256": prompt_sha256, "model": model, "provenance": dict(provenance or {})}
    rows = []
    for line in observation_result.get("lines", []):
        index = line.get("line_index")
        for obs in line.get("observations", []):
            rows.append({**common, "line_index": index, "invoice_item_id": item_ids.get(index) if index else None,
                         **{f: obs.get(f) for f in OBSERVATION_FIELDS}})
    for obs in observation_result.get("context_observations", []):
        rows.append({**common, "line_index": None, "invoice_item_id": None,
                     **{f: obs.get(f) for f in OBSERVATION_FIELDS}})
    return rows


def v11_field_rows(
    *, invoice_id: uuid.UUID, run_id: uuid.UUID, lines: Sequence[Any], item_ids: Mapping[int, uuid.UUID],
    model: str | None = None,
) -> list[dict[str, Any]]:
    """v11's product_code / supplier_item_id as observations (source v11_field), raw as stored."""
    rows = []
    for line in lines:
        for field_name in ("product_code", "supplier_item_id"):
            value = getattr(line, field_name, None)
            if value:
                rows.append({"invoice_id": invoice_id, "run_id": run_id, "source": OBS_SOURCE_V11,
                             "line_index": line.line_index, "invoice_item_id": item_ids.get(line.line_index),
                             "raw_text": value, "printed_label": None, "role_hint": None, "scope": "line",
                             "section_label": None, "position": None, "page": None, "prompt_version": "v11",
                             "prompt_sha256": None, "model": model, "provenance": {"v11_field": field_name}})
    return rows


def resolution_rows(
    *, invoice_id: uuid.UUID, run_id: uuid.UUID, result: dict, item_ids: Mapping[int, uuid.UUID],
    provenance: dict | None = None,
) -> list[dict[str, Any]]:
    """One row per resolved line (unlisted printed rows included, with no item id)."""
    template = result.get("template") or {}
    rows = []
    for line in result["lines"]:
        index = line["line_index"]
        rows.append({
            "invoice_id": invoice_id, "invoice_item_id": item_ids.get(index) if index else None,
            "run_id": run_id, "line_index": index, "status": line["status"], "chosen_gtin": line["chosen_gtin"],
            "would_be_eligible": bool(line["would_be_eligible"]), "pdi_item_code": line["pdi_item_code"],
            "resolver_version": result["resolver_version"], "template_id": template.get("template_id"),
            "template_version": template.get("version"), "template_sha256": template.get("content_sha256"),
            "inputs_hash": result["inputs_hash"], "resolution": line,
            "provenance": {"producer": "resolver", "mode": result.get("mode"), **(provenance or {})},
        })
    return rows


def review_confirmed_row(
    *, previous: Mapping[str, Any], gtin: str, proposal_id: uuid.UUID, reviewed_by: str,
    run_id: uuid.UUID,
) -> dict[str, Any]:
    """
    The NEW row an approved line-identity proposal appends. REVIEW_CONFIRMED on
    a GTIN-12 is shadow-eligible; the earlier resolver row is not touched.
    """
    if not (gtin.isdigit() and len(gtin) == 12):
        raise ValueError("a reviewed line identity must be a complete GTIN-12")
    from app.services.product_master.identifiers import has_valid_upc_check

    if not has_valid_upc_check(gtin):
        raise ValueError("a reviewed GTIN-12 must carry a valid check digit; nothing is repaired")
    resolution = dict(previous.get("resolution") or {})
    resolution.update({"status": "REVIEW_CONFIRMED", "chosen_gtin": gtin, "chosen_kind": "GTIN_12",
                       "reasons": ["approved_line_identity_proposal"], "would_be_eligible": True,
                       "pdi_item_code": gtin[:11]})
    return {
        "invoice_id": previous["invoice_id"], "invoice_item_id": previous.get("invoice_item_id"),
        "run_id": run_id, "line_index": previous.get("line_index"), "status": "REVIEW_CONFIRMED",
        "chosen_gtin": gtin, "would_be_eligible": True, "pdi_item_code": gtin[:11],
        "resolver_version": previous["resolver_version"], "template_id": previous.get("template_id"),
        "template_version": previous.get("template_version"), "template_sha256": previous.get("template_sha256"),
        "inputs_hash": previous["inputs_hash"], "resolution": resolution,
        "provenance": {"producer": "review", "proposal_id": str(proposal_id), "reviewed_by": reviewed_by},
    }
