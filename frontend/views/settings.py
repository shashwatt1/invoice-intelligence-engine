"""frontend/views/settings.py — Settings (placeholder for post-MVP configuration)."""

from __future__ import annotations

import api_client
import streamlit as st
from components import section


def render() -> None:
    st.title("Settings")
    st.caption("Platform configuration. Editable settings arrive with the review workflow phase.")

    section("Connection")
    st.markdown(
        f'<div class="iip-card">'
        f'<div class="iip-kpi-label">API base URL</div>'
        f'<div style="font-weight:600;">{api_client.API_BASE_URL}</div>'
        f'<div class="iip-kpi-hint">Override with the API_BASE_URL environment variable.</div>'
        f"</div>",
        unsafe_allow_html=True,
    )

    st.write("")
    section("Processing configuration", "Managed via backend environment variables (.env).")
    rows = [
        ("OCR provider", "Google Vision (digital PDFs bypass OCR via pdfplumber)"),
        ("LLM provider", "OpenAI — structured outputs, versioned prompts"),
        ("Review threshold", "Composite confidence < 0.85 routes to manual review"),
        ("Rounding tolerance", "±0.02 on all mathematical validation checks"),
        ("Max upload size", "25 MB · PDF, PNG, JPEG"),
    ]
    body = "".join(
        f'<div style="display:flex;justify-content:space-between;gap:1rem;padding:0.45rem 0;'
        f'border-bottom:1px solid #eef0f4;"><span style="color:#64748b;font-size:0.86rem;">'
        f"{label}</span><span style='font-size:0.86rem;font-weight:500;text-align:right;'>"
        f"{value}</span></div>"
        for label, value in rows
    )
    st.markdown(f'<div class="iip-card">{body}</div>', unsafe_allow_html=True)
