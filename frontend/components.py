"""
frontend/components.py — Reusable UI building blocks.

HTML/CSS renderers (KPI cards, badges, timeline, skeletons, check rows)
sharing the design tokens in theme.py. Kept free of any API knowledge —
views fetch data, components render it.
"""

from __future__ import annotations

import html
from datetime import datetime

import streamlit as st
from theme import COLORS

# Status → (label, fg, bg) for badges. Covers document statuses and
# invoice decisions with one visual vocabulary.
_STATUS_STYLES = {
    "COMPLETED": ("Completed", COLORS["success"], COLORS["success_soft"]),
    "VALIDATED": ("Validated", COLORS["success"], COLORS["success_soft"]),
    "REVIEW_REQUIRED": ("Review required", COLORS["warning"], COLORS["warning_soft"]),
    "FAILED": ("Failed", COLORS["danger"], COLORS["danger_soft"]),
    "UPLOADED": ("Uploaded", COLORS["info"], COLORS["info_soft"]),
    "OCR_IN_PROGRESS": ("OCR running", COLORS["info"], COLORS["info_soft"]),
    "OCR_COMPLETED": ("OCR complete", COLORS["info"], COLORS["info_soft"]),
    "AI_PROCESSING": ("AI structuring", COLORS["info"], COLORS["info_soft"]),
}


def badge(status: str) -> str:
    """Return badge HTML for a document/invoice status."""
    label, fg, bg = _STATUS_STYLES.get(
        status, (status.replace("_", " ").title(), COLORS["text_secondary"], COLORS["neutral_soft"])
    )
    return (
        f'<span class="iip-badge" style="color:{fg};background:{bg};">'
        f'<span class="iip-dot" style="background:{fg};"></span>{html.escape(label)}</span>'
    )


def kpi_card(label: str, value: str, hint: str = "") -> str:
    hint_html = f'<div class="iip-kpi-hint">{html.escape(hint)}</div>' if hint else ""
    return (
        f'<div class="iip-card"><div class="iip-kpi-label">{html.escape(label)}</div>'
        f'<div class="iip-kpi-value">{html.escape(value)}</div>{hint_html}</div>'
    )


def kpi_row(cards: list[tuple[str, str, str]]) -> None:
    """Render KPI cards in equal columns. cards = [(label, value, hint)]."""
    for column, (label, value, hint) in zip(st.columns(len(cards)), cards, strict=True):
        with column:
            st.markdown(kpi_card(label, value, hint), unsafe_allow_html=True)


def section(title: str, description: str = "") -> None:
    st.markdown(
        f'<p class="iip-section-title">{html.escape(title)}</p>'
        + (f'<p class="iip-section-desc">{html.escape(description)}</p>' if description else ""),
        unsafe_allow_html=True,
    )


def skeleton_rows(count: int = 3, height: int = 46) -> None:
    """Loading placeholder while data is fetched."""
    blocks = "".join(
        f'<div class="iip-skeleton" style="height:{height}px;margin-bottom:0.55rem;"></div>'
        for _ in range(count)
    )
    st.markdown(blocks, unsafe_allow_html=True)


def confidence_bar(score: float | None) -> str:
    """Confidence score with a colored fill bar (green/amber/red)."""
    if score is None:
        return '<div class="iip-kpi-hint">No confidence recorded</div>'
    pct = max(0.0, min(1.0, score)) * 100
    color = (
        COLORS["success"] if score >= 0.85
        else COLORS["warning"] if score >= 0.6
        else COLORS["danger"]
    )
    return (
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;">'
        f'<span class="iip-kpi-label" style="margin:0;">Composite confidence</span>'
        f'<span style="font-weight:700;font-size:1.05rem;color:{color};">{pct:.1f}%</span></div>'
        f'<div class="iip-conf-track"><div class="iip-conf-fill" '
        f'style="width:{pct:.1f}%;background:{color};"></div></div>'
    )


# ---------------------------------------------------------------------------
# Processing timeline
# ---------------------------------------------------------------------------

PIPELINE_STEPS = [
    ("UPLOAD", "Uploading", "File validated, hashed, and stored"),
    ("TEXT_EXTRACTION", "Text extraction", "Digital PDF parsing or OCR"),
    ("AI_STRUCTURING", "AI structuring", "Structured extraction via OpenAI"),
    ("VALIDATION", "Validation", "Math checks and confidence scoring"),
    ("PERSISTENCE", "Database persistence", "Vendor, invoice, and line items saved"),
]

# Document status → index of the step currently running (None = all done)
_ACTIVE_STEP_BY_STATUS = {
    "UPLOADED": 1,
    "OCR_IN_PROGRESS": 1,
    "OCR_COMPLETED": 2,
    "AI_PROCESSING": 2,
    "VALIDATED": 4,
    "REVIEW_REQUIRED": 4,
}


def timeline(status: dict) -> str:
    """
    Render the live processing timeline from a DocumentStatusData payload.

    Steps with a SUCCESS log entry are done; the step implied by the
    current document status pulses as active; a FAILURE entry marks its
    step failed and the rest stay pending.
    """
    completed = {s["stage"] for s in status.get("stages", []) if s["status"] == "SUCCESS"}
    failed_stage = (status.get("error") or {}).get("stage")
    durations = {
        s["stage"]: s.get("duration_ms")
        for s in status.get("stages", [])
        if s.get("duration_ms") is not None
    }
    is_terminal = status.get("is_terminal", False)
    active_index = None if is_terminal else _ACTIVE_STEP_BY_STATUS.get(status.get("status"))

    rows = []
    for index, (stage_key, title, sub) in enumerate(PIPELINE_STEPS):
        if stage_key == failed_stage:
            state, icon = "failed", "✕"
        elif stage_key in completed:
            state, icon = "done", "✓"
        elif failed_stage is not None:
            state, icon = "pending", str(index + 1)
        elif active_index is not None and index == active_index:
            state, icon = "active", "•"
        else:
            state, icon = "pending", str(index + 1)

        duration = durations.get(stage_key)
        subtitle = f"{sub} · {duration} ms" if state == "done" and duration is not None else sub
        line = '<div class="iip-step-line"></div>' if index < len(PIPELINE_STEPS) - 1 else ""
        rows.append(
            f'<div class="iip-step {state}">'
            f'<div class="iip-step-rail"><div class="iip-step-icon">{icon}</div>{line}</div>'
            f'<div class="iip-step-body"><div class="iip-step-title">{html.escape(title)}</div>'
            f'<div class="iip-step-sub">{html.escape(subtitle)}</div></div></div>'
        )
    return f'<div class="iip-card"><div class="iip-timeline">{"".join(rows)}</div></div>'


# ---------------------------------------------------------------------------
# Validation check rows
# ---------------------------------------------------------------------------

_CHECK_STYLES = {
    "PASSED": ("✓", COLORS["success"], COLORS["success_soft"]),
    "FAILED": ("✕", COLORS["danger"], COLORS["danger_soft"]),
    "WARNING": ("!", COLORS["warning"], COLORS["warning_soft"]),
    "SKIPPED": ("—", COLORS["text_secondary"], COLORS["neutral_soft"]),
}


def check_row(check: dict) -> str:
    icon, fg, bg = _CHECK_STYLES.get(check.get("status", ""), _CHECK_STYLES["SKIPPED"])
    name = check.get("name", "").replace("_", " ").title()
    parts = []
    if check.get("message"):
        parts.append(html.escape(check["message"]))
    if check.get("expected") is not None:
        parts.append(
            f"expected {html.escape(str(check['expected']))}, "
            f"got {html.escape(str(check.get('actual')))}"
        )
    detail = f'<div class="msg">{" · ".join(parts)}</div>' if parts else ""
    return (
        f'<div class="iip-check" style="background:{bg};">'
        f'<span style="color:{fg};font-weight:700;">{icon}</span>'
        f'<div><span class="name" style="color:{fg};">{html.escape(name)}</span>{detail}</div>'
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def money(value: float | None, currency: str | None = None) -> str:
    if value is None:
        return "—"
    formatted = f"{value:,.2f}"
    return f"{formatted} {currency}" if currency else formatted


def when(iso_timestamp: str | None) -> str:
    if not iso_timestamp:
        return "—"
    try:
        return datetime.fromisoformat(iso_timestamp).strftime("%d %b %Y · %H:%M")
    except ValueError:
        return iso_timestamp


def brand_header() -> None:
    st.markdown(
        '<div class="iip-brand"><div class="iip-brand-mark">II</div>'
        '<div><div class="iip-brand-name">Invoice Intelligence</div>'
        '<div class="iip-brand-sub">Enterprise AP Platform</div></div></div>',
        unsafe_allow_html=True,
    )
