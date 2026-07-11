"""frontend/views/dashboard.py — Executive dashboard."""

from __future__ import annotations

import api_client
import pandas as pd
import streamlit as st
from components import badge, kpi_row, section, skeleton_rows


def _format_ms(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value / 1000:.1f}s" if value >= 1000 else f"{value:.0f} ms"


def render() -> None:
    st.title("Dashboard")
    st.caption("Processing overview across the invoice intelligence pipeline.")

    placeholder = st.container()
    with placeholder:
        try:
            with st.spinner(""):
                data = api_client.dashboard_summary()
        except api_client.APIError as exc:
            st.error(f"Could not load dashboard: {exc.message}")
            return
        except Exception:
            skeleton_rows(2, height=90)
            st.warning("Backend API is unreachable. Start it with `uvicorn app.main:app`.")
            return

    success = data["success_rate"]
    kpi_row([
        ("Total processed", str(data["total_documents"]),
         f"{data['in_progress']} in progress" if data["in_progress"] else "all settled"),
        ("Success rate", f"{success * 100:.0f}%" if success is not None else "—",
         f"{data['completed']} completed"),
        ("Review required", str(data["review_required"]), "awaiting manual review"),
        ("Avg confidence", f"{data['average_confidence'] * 100:.1f}%"
         if data["average_confidence"] is not None else "—", "composite score"),
        ("Avg processing", _format_ms(data["average_processing_ms"]), "upload → persisted"),
    ])

    st.write("")
    left, right = st.columns([2, 1], gap="medium")

    with left:
        section("Recent activity", "Latest documents through the pipeline.")
        if not data["recent"]:
            st.info("No documents processed yet — start with **Process Invoice**.")
        else:
            frame = pd.DataFrame([
                {
                    "File": row["filename"],
                    "Vendor": row["vendor_name"] or "—",
                    "Invoice #": row["invoice_number"] or "—",
                    "Total": row["grand_total"],
                    "Status": row["status"],
                    "Confidence": row["composite_confidence"],
                    "_invoice_id": row["invoice_id"],
                    "_document_id": row["document_id"],
                }
                for row in data["recent"]
            ])
            selection = st.dataframe(
                frame.drop(columns=["_invoice_id", "_document_id"]),
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
            rows = selection.selection.rows if selection and selection.selection else []
            if rows:
                picked = data["recent"][rows[0]]
                st.session_state["selected_invoice_id"] = picked["invoice_id"]
                st.session_state["selected_document_id"] = picked["document_id"]
                st.switch_page(st.session_state["_pages"]["details"])

    with right:
        section("Pipeline health", "Documents by lifecycle state.")
        breakdown = data["status_breakdown"]
        if not breakdown:
            st.info("Nothing processed yet.")
        else:
            chips = "".join(
                f'<div style="display:flex;justify-content:space-between;align-items:center;'
                f'padding:0.42rem 0;">{badge(status)}'
                f'<span style="font-weight:600;font-variant-numeric:tabular-nums;">{count}</span></div>'
                for status, count in sorted(breakdown.items(), key=lambda kv: -kv[1])
            )
            st.markdown(f'<div class="iip-card">{chips}</div>', unsafe_allow_html=True)

        st.write("")
        section("AI usage", "Cumulative LLM spend across all documents.")
        st.markdown(
            f'<div class="iip-card">'
            f'<div class="iip-kpi-label">Tokens consumed</div>'
            f'<div class="iip-kpi-value">{data["total_tokens"]:,}</div>'
            f'<div class="iip-kpi-hint">≈ ${data["total_estimated_cost_usd"]:.4f} estimated cost</div>'
            f"</div>",
            unsafe_allow_html=True,
        )
