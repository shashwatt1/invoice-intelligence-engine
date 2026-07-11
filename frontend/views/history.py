"""frontend/views/history.py — Browse, search, filter, and sort processed invoices."""

from __future__ import annotations

import api_client
import pandas as pd
import streamlit as st
from components import section, when

_STATUS_FILTERS = ["All", "COMPLETED", "REVIEW_REQUIRED", "FAILED"]
_SORT_OPTIONS = {
    "Newest first": ("created_at", True),
    "Oldest first": ("created_at", False),
    "Highest total": ("grand_total", True),
    "Lowest confidence": ("confidence", False),
    "Vendor A–Z": ("vendor", False),
}
PAGE_SIZE = 12


def render() -> None:
    st.title("Invoice History")
    st.caption("Every processed document — search, filter, and open details.")

    filter_cols = st.columns([3, 1.4, 1.6])
    with filter_cols[0]:
        search = st.text_input(
            "Search", placeholder="Search by vendor, invoice number, or filename…",
            label_visibility="collapsed",
        )
    with filter_cols[1]:
        status = st.selectbox("Status", _STATUS_FILTERS, label_visibility="collapsed")
    with filter_cols[2]:
        sort_label = st.selectbox("Sort", list(_SORT_OPTIONS), label_visibility="collapsed")

    if st.session_state.get("_history_filters") != (search, status, sort_label):
        st.session_state["_history_filters"] = (search, status, sort_label)
        st.session_state["history_page"] = 1
    page = st.session_state.get("history_page", 1)
    sort_by, descending = _SORT_OPTIONS[sort_label]

    try:
        result = api_client.list_invoices(
            search=search or None,
            status=None if status == "All" else status,
            sort_by=sort_by,
            descending=descending,
            page=page,
            page_size=PAGE_SIZE,
        )
    except Exception:
        st.error("Backend API is unreachable. Start it with `uvicorn app.main:app`.")
        return

    rows, total = result["items"], result["total"]
    section("Results", f"{total} document{'s' if total != 1 else ''} found")

    if not rows:
        st.info("Nothing here yet. Process an invoice to populate history.")
        return

    frame = pd.DataFrame([
        {
            "File": row["filename"],
            "Vendor": row["vendor_name"] or "—",
            "Invoice #": row["invoice_number"] or "—",
            "Date": row["invoice_date"] or "—",
            "Total": row["grand_total"],
            "Currency": row["currency"] or "—",
            "Confidence": row["composite_confidence"],
            "Status": row["status"],
            "Processed": when(row["created_at"]),
        }
        for row in rows
    ])
    selection = st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Total": st.column_config.NumberColumn(format="%.2f"),
            "Confidence": st.column_config.ProgressColumn(
                format="percent", min_value=0, max_value=1
            ),
        },
    )
    picked_rows = selection.selection.rows if selection and selection.selection else []
    if picked_rows:
        picked = rows[picked_rows[0]]
        st.session_state["selected_invoice_id"] = picked["invoice_id"]
        st.session_state["selected_document_id"] = picked["document_id"]
        st.switch_page(st.session_state["_pages"]["details"])

    total_pages = max(1, -(-total // PAGE_SIZE))
    nav = st.columns([1, 2, 1])
    with nav[0]:
        if st.button("← Previous", disabled=page <= 1, width="stretch"):
            st.session_state["history_page"] = page - 1
            st.rerun()
    with nav[1]:
        st.markdown(
            f'<div style="text-align:center;color:#64748b;font-size:0.84rem;padding-top:0.5rem;">'
            f"Page {page} of {total_pages}</div>",
            unsafe_allow_html=True,
        )
    with nav[2]:
        if st.button("Next →", disabled=page >= total_pages, width="stretch"):
            st.session_state["history_page"] = page + 1
            st.rerun()
