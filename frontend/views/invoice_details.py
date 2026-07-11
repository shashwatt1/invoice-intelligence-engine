"""
frontend/views/invoice_details.py — Full invoice detail view.

Header + vendor + totals, interactive line-item table, visual validation
report, database persistence confirmation, and the collapsed developer
panel (hidden by default).
"""

from __future__ import annotations

import json

import api_client
import pandas as pd
import streamlit as st
from components import badge, check_row, confidence_bar, money, section, timeline, when


def _field(label: str, value: str) -> str:
    return (
        f'<div style="padding:0.32rem 0;"><div class="iip-kpi-label" style="margin:0;">{label}'
        f'</div><div style="font-size:0.92rem;font-weight:500;">{value}</div></div>'
    )


def _render_failed_document(document_id: str) -> None:
    """Detail view for documents that never produced an invoice."""
    status = api_client.get_document_status(document_id)
    st.markdown(
        f'<div class="iip-card" style="display:flex;justify-content:space-between;'
        f'align-items:center;"><div style="font-weight:600;">{status["filename"]}</div>'
        f'{badge(status["status"])}</div>',
        unsafe_allow_html=True,
    )
    error = status.get("error") or {}
    if error:
        st.error(
            f"**Failed at {error.get('stage', '?')}** — "
            f"{error.get('message', 'no detail recorded')}"
        )
    st.write("")
    section("Processing timeline")
    st.markdown(timeline(status), unsafe_allow_html=True)


def render() -> None:
    st.title("Invoice Details")

    invoice_id = st.session_state.get("selected_invoice_id")
    document_id = st.session_state.get("selected_document_id")

    if invoice_id is None and document_id is None:
        st.info("Select an invoice from **Invoice History** or the **Dashboard**.")
        return

    try:
        if invoice_id is None:
            _render_failed_document(document_id)
            return
        detail = api_client.get_invoice(invoice_id)
    except api_client.APIError as exc:
        st.error(f"Could not load invoice: {exc.message}")
        return

    # ---- Header ----------------------------------------------------------
    header_left, header_right = st.columns([3, 1])
    with header_left:
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:0.7rem;flex-wrap:wrap;">'
            f'<span style="font-size:1.25rem;font-weight:700;">'
            f'{detail["invoice_number"] or "(no invoice number)"}</span>'
            f'{badge(detail["status"])}{badge(detail["document_status"])}</div>'
            f'<div class="iip-kpi-hint">{detail["filename"]} · '
            f'{detail["source_type"] or "—"} · processed {when(detail["created_at"])}</div>',
            unsafe_allow_html=True,
        )
    with header_right:
        st.markdown(
            f'<div class="iip-card">{confidence_bar(detail["composite_confidence"])}</div>',
            unsafe_allow_html=True,
        )

    st.write("")

    # ---- Vendor / invoice meta / totals -----------------------------------
    vendor = detail.get("vendor") or {}
    columns = st.columns(3, gap="medium")
    with columns[0]:
        section("Vendor")
        st.markdown(
            '<div class="iip-card">'
            + _field("Name", vendor.get("name") or detail.get("vendor_name") or "—")
            + _field("Tax ID", vendor.get("tax_id") or "—")
            + _field("Address", vendor.get("address") or "—")
            + _field("Email", vendor.get("email") or "—")
            + "</div>",
            unsafe_allow_html=True,
        )
    with columns[1]:
        section("Invoice")
        st.markdown(
            '<div class="iip-card">'
            + _field("Invoice date", detail["invoice_date"] or "—")
            + _field("Due date", detail["due_date"] or "—")
            + _field("Currency", detail["currency"])
            + _field("Extraction model", detail["extraction_model"] or "—")
            + "</div>",
            unsafe_allow_html=True,
        )
    with columns[2]:
        section("Totals")
        st.markdown(
            '<div class="iip-card">'
            + _field("Subtotal", money(detail["subtotal"]))
            + _field("Tax", money(detail["tax_amount"]))
            + _field("Discount", money(detail["discount_amount"]))
            + _field("Grand total", f'<span style="font-size:1.15rem;font-weight:700;">'
                     f'{money(detail["grand_total"], detail["currency"])}</span>')
            + "</div>",
            unsafe_allow_html=True,
        )

    # ---- Line items --------------------------------------------------------
    st.write("")
    section("Line items", f"{len(detail['line_items'])} row(s) extracted in document order")
    if detail["line_items"]:
        frame = pd.DataFrame([
            {
                "Product": item["description"],
                "Quantity": item["quantity"],
                "Unit price": item["unit_price"],
                "Line total": item["line_total"],
                "Tax %": item["tax_rate"],
            }
            for item in detail["line_items"]
        ])
        st.dataframe(
            frame, hide_index=True, width="stretch",
            column_config={
                "Quantity": st.column_config.NumberColumn(format="%.2f"),
                "Unit price": st.column_config.NumberColumn(format="%.4f"),
                "Line total": st.column_config.NumberColumn(format="%.2f"),
                "Tax %": st.column_config.NumberColumn(format="%.1f"),
            },
        )
    else:
        st.warning("No line items were extracted — see the validation report.")

    # ---- Validation report ---------------------------------------------------
    st.write("")
    report = detail.get("validation_report") or {}
    summary = report.get("summary", {})
    section(
        "Validation report",
        f"{summary.get('passed', 0)} passed · {summary.get('warnings', 0)} warnings · "
        f"{summary.get('failed', 0)} failed · decision {report.get('decision', '—')}",
    )
    validation_left, validation_right = st.columns([1, 1], gap="medium")
    checks = report.get("checks", [])
    ordered = {"FAILED": [], "WARNING": [], "PASSED": [], "SKIPPED": []}
    for check in checks:
        ordered.setdefault(check.get("status", "SKIPPED"), []).append(check)

    with validation_left:
        blockers = ordered["FAILED"] + ordered["WARNING"]
        if blockers:
            st.markdown("".join(check_row(c) for c in blockers), unsafe_allow_html=True)
        else:
            st.markdown(check_row({"name": "ALL_CHECKS_PASSED", "status": "PASSED",
                                   "message": "No failures or warnings."}),
                        unsafe_allow_html=True)
        if report.get("review_reasons"):
            st.caption("Review reasons: " + " · ".join(report["review_reasons"]))
    with (
        validation_right,
        st.expander(
            f"Passed & skipped checks ({len(ordered['PASSED']) + len(ordered['SKIPPED'])})"
        ),
    ):
        st.markdown(
            "".join(check_row(c) for c in ordered["PASSED"] + ordered["SKIPPED"]),
            unsafe_allow_html=True,
        )

    # ---- Database confirmation -----------------------------------------------
    st.write("")
    db = detail["database"]
    section("Database persistence", "What this run wrote to PostgreSQL.")
    flags = [
        ("Vendor saved", db["vendor_saved"]),
        ("Invoice saved", db["invoice_saved"]),
        (f"Line items saved ({db['items_saved']})", db["items_saved"] > 0),
        (f"Processing logs ({db['logs_saved']})", db["logs_saved"] > 0),
        ("Duplicate check passed", db["duplicate_check_passed"]),
    ]
    cells = "".join(
        f'<div style="display:flex;align-items:center;gap:0.5rem;padding:0.3rem 0;">'
        f'<span style="color:{"#047857" if ok else "#b91c1c"};font-weight:700;">'
        f'{"✓" if ok else "✕"}</span><span style="font-size:0.88rem;">{label}</span></div>'
        for label, ok in flags
    )
    st.markdown(
        f'<div class="iip-card"><div style="display:grid;'
        f'grid-template-columns:repeat(auto-fit, minmax(210px, 1fr));gap:0 1rem;">{cells}</div>'
        f'<div class="iip-kpi-hint" style="margin-top:0.5rem;">Total pipeline duration: '
        f'{db["processing_duration_ms"]:,} ms</div></div>',
        unsafe_allow_html=True,
    )

    # ---- Developer panel (hidden by default) -----------------------------------
    st.write("")
    llm = detail.get("llm_metadata") or {}
    with st.expander("Developer panel", expanded=False):
        st.markdown(
            '<div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(150px, 1fr));'
            'gap:0.6rem;">'
            + "".join(
                f'<div class="iip-card" style="padding:0.7rem 0.9rem;">'
                f'<div class="iip-kpi-label">{label}</div>'
                f'<div style="font-weight:600;font-size:0.95rem;">{value}</div></div>'
                for label, value in [
                    ("Model", llm.get("model", "—")),
                    ("Prompt version", llm.get("prompt_version", "—")),
                    ("Latency", f"{llm.get('latency_ms', 0):,} ms"),
                    ("Input tokens", f"{llm.get('input_tokens', 0):,}"),
                    ("Output tokens", f"{llm.get('output_tokens', 0):,}"),
                    ("Est. cost", f"${llm.get('estimated_cost_usd') or 0:.6f}"),
                ]
            )
            + "</div>",
            unsafe_allow_html=True,
        )
        st.write("")
        tabs = st.tabs(
            ["OCR text", "Raw structured output", "LLM metadata",
             "Validation metadata", "Database IDs"]
        )
        with tabs[0]:
            st.code(detail.get("ocr_text") or "(no text stored)", language=None)
        with tabs[1]:
            st.json(detail.get("raw_extraction") or {})
        with tabs[2]:
            st.json(llm)
        with tabs[3]:
            st.json(report)
        with tabs[4]:
            st.code(json.dumps({
                "invoice_id": detail["invoice_id"],
                "document_id": detail["document_id"],
                "vendor_id": vendor.get("id"),
            }, indent=2), language="json")
