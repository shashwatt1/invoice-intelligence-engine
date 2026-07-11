"""
frontend/app.py — Invoice Intelligence dashboard entry point.

Run with:
    streamlit run frontend/app.py

Talks to the FastAPI backend only via frontend/api_client.py
(API_BASE_URL env var, default http://localhost:8000/api/v1).
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="Invoice Intelligence",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)

import api_client  # noqa: E402
from components import brand_header  # noqa: E402
from theme import inject_theme  # noqa: E402
from views import dashboard, history, invoice_details, process, settings  # noqa: E402

inject_theme()

pages = {
    "dashboard": st.Page(dashboard.render, title="Dashboard", icon=":material/monitoring:",
                         url_path="dashboard", default=True),
    "process": st.Page(process.render, title="Process Invoice", icon=":material/upload_file:",
                       url_path="process"),
    "history": st.Page(history.render, title="Invoice History", icon=":material/history:",
                       url_path="history"),
    "details": st.Page(invoice_details.render, title="Invoice Details",
                       icon=":material/receipt_long:", url_path="invoice"),
    "settings": st.Page(settings.render, title="Settings", icon=":material/settings:",
                        url_path="settings"),
}
# Views navigate programmatically (e.g. history row → details) via this map.
st.session_state["_pages"] = pages

with st.sidebar:
    brand_header()

navigation = st.navigation(list(pages.values()), position="sidebar")

with st.sidebar:
    st.markdown('<div style="flex:1;"></div>', unsafe_allow_html=True)
    if api_client.api_available():
        st.markdown(
            '<div style="font-size:0.76rem;color:#047857;padding:0.4rem 0.25rem;">'
            "● API connected</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="font-size:0.76rem;color:#b91c1c;padding:0.4rem 0.25rem;">'
            "● API unreachable — start the backend:<br>"
            "<code>uvicorn app.main:app</code></div>",
            unsafe_allow_html=True,
        )

navigation.run()
