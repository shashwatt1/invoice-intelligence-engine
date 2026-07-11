"""
frontend/theme.py — Design system for the Invoice Intelligence dashboard.

One neutral, enterprise palette (Stripe/Linear-inspired) expressed as
CSS custom properties, plus overrides that remove Streamlit's default
chrome. Injected once per page render by app.py.
"""

from __future__ import annotations

import streamlit as st

# Design tokens — referenced by components.py to keep every custom
# element on the same palette.
COLORS = {
    "bg": "#f8f9fb",
    "surface": "#ffffff",
    "border": "#e6e8ee",
    "text": "#0f172a",
    "text_secondary": "#64748b",
    "accent": "#4f46e5",
    "accent_soft": "#eef2ff",
    "success": "#047857",
    "success_soft": "#ecfdf5",
    "warning": "#b45309",
    "warning_soft": "#fffbeb",
    "danger": "#b91c1c",
    "danger_soft": "#fef2f2",
    "info": "#1d4ed8",
    "info_soft": "#eff6ff",
    "neutral_soft": "#f1f5f9",
}

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

:root {{
    --bg: {COLORS["bg"]};
    --surface: {COLORS["surface"]};
    --border: {COLORS["border"]};
    --text: {COLORS["text"]};
    --text-secondary: {COLORS["text_secondary"]};
    --accent: {COLORS["accent"]};
    --accent-soft: {COLORS["accent_soft"]};
}}

/* ---- Base -------------------------------------------------------- */
html, body, [class*="css"], [data-testid="stAppViewContainer"] * {{
    font-family: 'Inter', -apple-system, 'Segoe UI', 'Helvetica Neue', sans-serif;
}}
[data-testid="stAppViewContainer"] {{ background: var(--bg); }}
[data-testid="stMain"] .block-container {{
    padding: 2.2rem 3rem 4rem 3rem; max-width: 1250px;
}}
h1, h2, h3 {{ color: var(--text); letter-spacing: -0.02em; }}

/* ---- Hide Streamlit chrome --------------------------------------- */
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {{
    visibility: hidden; height: 0;
}}
[data-testid="stHeader"] {{ background: transparent; }}

/* ---- Sidebar ------------------------------------------------------ */
[data-testid="stSidebar"] {{
    background: var(--surface); border-right: 1px solid var(--border);
    min-width: 264px;
}}
[data-testid="stSidebarNav"] a span,
[data-testid="stSidebar"] [data-testid="stNavSectionHeader"] {{
    font-size: 0.92rem; font-weight: 500; color: var(--text);
}}
[data-testid="stSidebarNav"] a[aria-current="page"] {{
    background: var(--accent-soft); border-radius: 8px;
}}
[data-testid="stSidebarNav"] a[aria-current="page"] span {{ color: var(--accent); }}

/* ---- Buttons ------------------------------------------------------ */
.stButton > button, .stDownloadButton > button {{
    border-radius: 8px; border: 1px solid var(--border);
    font-weight: 500; padding: 0.45rem 1.05rem;
    transition: all 0.15s ease;
}}
.stButton > button[kind="primary"] {{
    background: var(--accent); border-color: var(--accent); color: #fff;
}}
.stButton > button[kind="primary"]:hover {{
    background: #4338ca; border-color: #4338ca;
    box-shadow: 0 4px 12px rgba(79, 70, 229, 0.25);
}}

/* ---- Inputs ------------------------------------------------------- */
[data-testid="stTextInput"] input, [data-testid="stSelectbox"] > div > div {{
    border-radius: 8px;
}}
[data-testid="stFileUploaderDropzone"] {{
    background: var(--surface);
    border: 1.5px dashed #c7cdd9; border-radius: 12px;
    padding: 1.4rem; transition: border-color 0.15s ease;
}}
[data-testid="stFileUploaderDropzone"]:hover {{ border-color: var(--accent); }}

/* ---- Tables / dataframes ------------------------------------------ */
[data-testid="stDataFrame"] {{
    border: 1px solid var(--border); border-radius: 10px;
    background: var(--surface);
}}

/* ---- Expanders ----------------------------------------------------- */
[data-testid="stExpander"] {{
    border: 1px solid var(--border); border-radius: 10px;
    background: var(--surface);
}}
[data-testid="stExpander"] summary {{ font-weight: 500; }}

/* ---- Custom components -------------------------------------------- */
.iip-card {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 1.15rem 1.3rem;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}}
.iip-kpi-label {{
    font-size: 0.74rem; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--text-secondary);
    margin-bottom: 0.35rem;
}}
.iip-kpi-value {{
    font-size: 1.72rem; font-weight: 700; color: var(--text);
    line-height: 1.15; font-variant-numeric: tabular-nums;
}}
.iip-kpi-hint {{ font-size: 0.78rem; color: var(--text-secondary); margin-top: 0.3rem; }}

.iip-badge {{
    display: inline-flex; align-items: center; gap: 0.32rem;
    padding: 0.14rem 0.62rem; border-radius: 999px;
    font-size: 0.74rem; font-weight: 600; letter-spacing: 0.02em;
    white-space: nowrap;
}}
.iip-dot {{ width: 6px; height: 6px; border-radius: 50%; display: inline-block; }}

.iip-section-title {{
    font-size: 1.02rem; font-weight: 600; color: var(--text);
    margin: 0 0 0.15rem 0;
}}
.iip-section-desc {{ font-size: 0.82rem; color: var(--text-secondary); margin: 0 0 0.8rem 0; }}

/* ---- Timeline ------------------------------------------------------ */
.iip-timeline {{ margin: 0.4rem 0 0 0.25rem; }}
.iip-step {{ display: flex; gap: 0.85rem; }}
.iip-step-rail {{ display: flex; flex-direction: column; align-items: center; }}
.iip-step-icon {{
    width: 26px; height: 26px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.72rem; font-weight: 700; flex-shrink: 0;
    border: 2px solid var(--border); background: var(--surface);
    color: var(--text-secondary);
}}
.iip-step-line {{ width: 2px; flex: 1; min-height: 22px; background: var(--border); }}
.iip-step.done .iip-step-icon {{
    background: {COLORS["success"]}; border-color: {COLORS["success"]}; color: #fff;
}}
.iip-step.done .iip-step-line {{ background: {COLORS["success"]}; }}
.iip-step.active .iip-step-icon {{
    border-color: var(--accent); color: var(--accent);
    animation: iip-pulse 1.2s ease-in-out infinite;
}}
.iip-step.failed .iip-step-icon {{
    background: {COLORS["danger"]}; border-color: {COLORS["danger"]}; color: #fff;
}}
.iip-step-body {{ padding: 0.15rem 0 1.05rem 0; }}
.iip-step-title {{ font-size: 0.9rem; font-weight: 600; color: var(--text); }}
.iip-step.pending .iip-step-title {{ color: var(--text-secondary); font-weight: 500; }}
.iip-step-sub {{ font-size: 0.76rem; color: var(--text-secondary); margin-top: 0.1rem; }}
@keyframes iip-pulse {{
    0%, 100% {{ box-shadow: 0 0 0 0 rgba(79, 70, 229, 0.35); }}
    50% {{ box-shadow: 0 0 0 7px rgba(79, 70, 229, 0); }}
}}

/* ---- Skeletons ----------------------------------------------------- */
.iip-skeleton {{
    border-radius: 10px; background: linear-gradient(
        90deg, #eef0f4 25%, #f7f8fa 50%, #eef0f4 75%);
    background-size: 200% 100%;
    animation: iip-shimmer 1.4s ease-in-out infinite;
}}
@keyframes iip-shimmer {{
    0% {{ background-position: 200% 0; }}
    100% {{ background-position: -200% 0; }}
}}

/* ---- Confidence bar ------------------------------------------------ */
.iip-conf-track {{
    height: 8px; border-radius: 999px; background: var(--border);
    overflow: hidden; margin-top: 0.45rem;
}}
.iip-conf-fill {{ height: 100%; border-radius: 999px; transition: width 0.5s ease; }}

/* ---- Check chips ---------------------------------------------------- */
.iip-check {{
    display: flex; align-items: flex-start; gap: 0.6rem;
    padding: 0.5rem 0.7rem; border-radius: 8px;
    font-size: 0.84rem; margin-bottom: 0.4rem;
}}
.iip-check .name {{ font-weight: 600; }}
.iip-check .msg {{ color: var(--text-secondary); font-size: 0.79rem; }}

.iip-brand {{
    display: flex; align-items: center; gap: 0.6rem;
    padding: 0.35rem 0.25rem 1.0rem 0.25rem;
}}
.iip-brand-mark {{
    width: 34px; height: 34px; border-radius: 9px;
    background: var(--accent); color: #fff;
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 0.95rem;
}}
.iip-brand-name {{ font-weight: 700; font-size: 0.98rem; color: var(--text); line-height: 1.1; }}
.iip-brand-sub {{ font-size: 0.7rem; color: var(--text-secondary); }}
</style>
"""


def inject_theme() -> None:
    """Inject the design system CSS. Call once at the top of every render."""
    st.markdown(_CSS, unsafe_allow_html=True)
