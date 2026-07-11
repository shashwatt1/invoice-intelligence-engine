"""
frontend/views/process.py — Upload + live processing timeline.

POSTs to /invoices/process (202), then polls /documents/{id} and
re-renders the timeline as each pipeline stage completes. The timeline
reflects real persisted stage transitions — never a fake spinner.
"""

from __future__ import annotations

import time

import api_client
import streamlit as st
from components import badge, section, timeline

_MIME_BY_EXTENSION = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}

POLL_INTERVAL_SECONDS = 0.7
POLL_TIMEOUT_SECONDS = 180


def _poll_until_terminal(document_id: str, slot) -> dict | None:
    """Poll document status, re-rendering the timeline each tick."""
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = api_client.get_document_status(document_id)
        slot.markdown(timeline(status), unsafe_allow_html=True)
        if status["is_terminal"]:
            return status
        time.sleep(POLL_INTERVAL_SECONDS)
    return None


def render() -> None:
    st.title("Process Invoice")
    st.caption("Upload a PDF, PNG, or JPEG invoice and watch every pipeline stage live.")

    left, right = st.columns([1, 1], gap="large")

    with left:
        section("Document", "Drag & drop or browse. Max 25 MB.")
        uploaded = st.file_uploader(
            "Invoice file", type=["pdf", "png", "jpg", "jpeg"],
            label_visibility="collapsed",
        )
        start = st.button(
            "Process invoice", type="primary", disabled=uploaded is None,
            width="stretch",
        )

    with right:
        section("Processing timeline", "Each stage updates as the pipeline commits it.")
        timeline_slot = st.empty()
        result_slot = st.container()

        last = st.session_state.get("last_processing_status")
        if last and not start:
            timeline_slot.markdown(timeline(last), unsafe_allow_html=True)

    if not start or uploaded is None:
        if last and last.get("invoice_id"):
            with result_slot:
                if st.button("View invoice details →", key="view_last"):
                    st.session_state["selected_invoice_id"] = last["invoice_id"]
                    st.switch_page(st.session_state["_pages"]["details"])
        return

    extension = (uploaded.name.rsplit(".", 1)[-1] or "").lower()
    mime_type = uploaded.type or _MIME_BY_EXTENSION.get(extension, "application/octet-stream")

    try:
        accepted = api_client.process_invoice(uploaded.name, uploaded.getvalue(), mime_type)
    except api_client.APIError as exc:
        if exc.error_code == "ERR_DUPLICATE_DOCUMENT":
            st.toast("Duplicate detected", icon="⚠️")
            with result_slot:
                st.warning(
                    "**This document was already processed.** The platform blocks "
                    "duplicate content by SHA-256 hash "
                    f"(existing document `{(exc.detail or {}).get('existing_document_id', '?')}`)."
                )
        else:
            st.toast("Upload rejected", icon="❌")
            with result_slot:
                st.error(f"**{exc.error_code}** — {exc.message}")
        return

    st.toast(f"Processing {uploaded.name}", icon="📄")
    try:
        status = _poll_until_terminal(accepted["document_id"], timeline_slot)
    except api_client.APIError as exc:
        with result_slot:
            st.error(f"Lost track of processing: {exc.message}")
        return

    if status is None:
        with result_slot:
            st.warning("Still processing — check **Invoice History** in a moment.")
        return

    st.session_state["last_processing_status"] = status

    with result_slot:
        if status["status"] == "FAILED":
            error = status.get("error") or {}
            st.toast("Processing failed", icon="❌")
            st.error(
                f"**Failed at {error.get('stage', 'unknown stage')}** — "
                f"{error.get('message', 'no detail recorded')}"
            )
            return

        st.toast("Invoice processed", icon="✅")
        st.markdown(
            f'<div class="iip-card" style="display:flex;justify-content:space-between;'
            f'align-items:center;"><div><div style="font-weight:600;">{status["filename"]}</div>'
            f'<div class="iip-kpi-hint">Extraction: {status.get("source_type") or "—"}</div></div>'
            f"{badge(status['status'])}</div>",
            unsafe_allow_html=True,
        )
        if status["status"] == "REVIEW_REQUIRED":
            st.info("Validation routed this invoice to **manual review** — details explain why.")
        st.write("")
        if st.button("View invoice details →", type="primary", key="view_new"):
            st.session_state["selected_invoice_id"] = status["invoice_id"]
            st.switch_page(st.session_state["_pages"]["details"])
