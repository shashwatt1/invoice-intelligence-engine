"""
Targeted OCR reread requests — app/services/product_master/reread.py

Phase 3C, SHADOW ONLY. Turns the resolver's reread triggers into explicit
requests, decides whether each one can be executed safely, and caps them per
invoice. It performs no OCR itself.

Why requests are blocked today: a targeted reread must crop the page image
around the printed value. The Vision provider produces word tokens with
bounding boxes (app/services/ocr/google_vision.py), but the pipeline stores
only each page's text (document_pages.raw_ocr_text); no token coordinates
are persisted. Without them there is no safe way to locate the crop, so a
request is recorded as BLOCKED_MISSING_TOKEN_COORDINATES. It never falls back
to a guessed crop or an untested full-page re-OCR, and barcode decoding is
never used as identity proof.

A completed reread (once coordinates exist and an executor is approved) is
NOT an overwrite: it re-enters the resolver as an appended read with source
"reread" (resolve_invoice(..., rereads=...)), and persists as a new
observation with source "reread" — the original observation stays.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass

MAX_REREADS_PER_INVOICE = 10

STATUS_EXECUTABLE = "EXECUTABLE"
STATUS_BLOCKED_NO_TOKENS = "BLOCKED_MISSING_TOKEN_COORDINATES"
STATUS_BLOCKED_NO_IMAGE = "BLOCKED_MISSING_PAGE_IMAGE"
STATUS_CAPPED = "NOT_REQUESTED_CAP_REACHED"

# Most useful first when the cap bites: a failed check digit is the clearest
# evidence of a misread, a v11/observation disagreement next.
_PRIORITY = {"check_digit_failed": 0, "reads_disagree": 1, "leading_zero_reading_fails_check_digit": 2,
             "grammar_violation": 3, "observation_text_not_in_ocr": 4, "barcode_length_or_format_violation": 5}


@dataclass(frozen=True)
class RereadRequest:
    invoice_key: str
    line_index: int | None
    reason: str
    raw: str | None
    source: str | None
    status: str
    executable: bool
    provenance: dict


def plan_rereads(
    invoice_key: str,
    resolution: dict,
    *,
    page_images_available: bool,
    token_coordinates_available: bool,
    cap: int = MAX_REREADS_PER_INVOICE,
) -> list[dict]:
    """One request per distinct (line, reason, raw) trigger, in priority order, capped."""
    triggers = []
    seen: set[tuple] = set()
    for line in resolution["lines"]:
        for trigger in line.get("reread_triggers", []):
            raw = trigger.get("raw") or ",".join(trigger.get("values", []))
            key = (line["line_index"], trigger["reason"], raw)
            if key in seen:
                continue
            seen.add(key)
            triggers.append((line["line_index"], trigger, raw))
    triggers.sort(key=lambda t: (_PRIORITY.get(t[1]["reason"], 9), t[0] if t[0] is not None else 10**6, t[2] or ""))

    if not page_images_available:
        blocked = STATUS_BLOCKED_NO_IMAGE
    elif not token_coordinates_available:
        blocked = STATUS_BLOCKED_NO_TOKENS
    else:
        blocked = None

    requests: list[RereadRequest] = []
    for position, (line_index, trigger, raw) in enumerate(triggers):
        status = STATUS_CAPPED if position >= cap else (blocked or STATUS_EXECUTABLE)
        requests.append(RereadRequest(
            invoice_key=invoice_key, line_index=line_index, reason=trigger["reason"], raw=raw or None,
            source=trigger.get("source"), status=status, executable=status == STATUS_EXECUTABLE,
            provenance={"resolver_version": resolution.get("resolver_version"),
                        "inputs_hash": resolution.get("inputs_hash")},
        ))
    return [asdict(r) for r in requests]


def summarize(requests: Sequence[dict]) -> dict:
    by_status: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for r in requests:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
    return {"requested": len(requests), "by_status": by_status, "by_reason": by_reason,
            "executable": by_status.get(STATUS_EXECUTABLE, 0), "completed": 0,
            "changed_candidate": 0, "unresolved_after_reread": 0}
