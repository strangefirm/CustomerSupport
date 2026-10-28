"""Shared look and feel: raised "3D" cards, soft layered shadows, gradient buttons and a dark sidebar.

Colours come from .streamlit/config.toml; this adds only the depth effects the theme can't express.
"""

import streamlit as st

_CSS = """
<style>
:root {
  --raise-1: 0 1px 2px rgba(15,23,42,.06), 0 2px 4px rgba(15,23,42,.06), 0 6px 14px rgba(15,23,42,.07);
  --raise-2: 0 2px 4px rgba(15,23,42,.08), 0 8px 18px rgba(15,23,42,.12), 0 18px 36px rgba(15,23,42,.10);
  --edge: inset 0 1px 0 rgba(255,255,255,.9);
  --accent: #4f46e5;
  --accent-2: #7c3aed;
}

/* ---- page ---- */
[data-testid="stAppViewContainer"] { background: radial-gradient(1200px 500px at 15% -10%, #ffffff 0%, #eef1f7 55%, #e6ebf5 100%); }
[data-testid="stHeader"] { background: transparent; }
h1 {
  font-weight: 700; letter-spacing: -.02em;
  background: linear-gradient(90deg, #0f172a 0%, #4f46e5 100%); -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent; padding-bottom: .5rem; border-bottom: 1px solid #d5dcea;
}
h2, h3 { letter-spacing: -.01em; font-weight: 600; }

/* ---- raised cards: bordered containers, forms, metrics ---- */
[data-testid="stVerticalBlockBorderWrapper"] {
  background: linear-gradient(180deg, #ffffff 0%, #f8faff 100%);
  border: 1px solid #dde4f0 !important; border-radius: 14px;
  box-shadow: var(--edge), var(--raise-1);
  transition: transform .18s ease, box-shadow .18s ease;
}
[data-testid="stVerticalBlockBorderWrapper"]:hover { transform: translateY(-2px); box-shadow: var(--edge), var(--raise-2); }
/* don't lift layout wrappers that merely contain other cards */
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stVerticalBlockBorderWrapper"]:hover { transform: none; }

[data-testid="stForm"] {
  background: linear-gradient(180deg, #ffffff 0%, #f6f8fd 100%); border: 1px solid #dde4f0; border-radius: 16px;
  box-shadow: var(--edge), var(--raise-2); padding: 1.4rem;
}

[data-testid="stMetric"] {
  background: linear-gradient(145deg, #ffffff 0%, #eef2ff 100%); border: 1px solid #dde4f0; border-left: 4px solid var(--accent);
  border-radius: 12px; padding: .8rem 1rem; box-shadow: var(--edge), var(--raise-1);
  transition: transform .18s ease, box-shadow .18s ease;
}
[data-testid="stMetric"]:hover { transform: translateY(-3px) perspective(600px) rotateX(2deg); box-shadow: var(--edge), var(--raise-2); }
[data-testid="stMetricValue"] { font-weight: 700; letter-spacing: -.02em; }
[data-testid="stMetricLabel"] { text-transform: uppercase; font-size: .72rem; letter-spacing: .06em; opacity: .75; }

/* ---- buttons: gradient with a pressed state ---- */
.stButton > button, [data-testid="stFormSubmitButton"] > button, [data-testid="stBaseButton-secondary"] {
  border-radius: 10px; font-weight: 600;
  box-shadow: var(--edge), 0 1px 2px rgba(15,23,42,.10), 0 4px 8px rgba(15,23,42,.08);
  transition: transform .12s ease, box-shadow .12s ease;
}
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {
  background: linear-gradient(180deg, #6366f1 0%, #4f46e5 55%, #4338ca 100%); border: 1px solid #4338ca; color: #fff;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.35), 0 2px 4px rgba(67,56,202,.35), 0 8px 16px rgba(79,70,229,.28);
}
.stButton > button:hover, [data-testid="stFormSubmitButton"] > button:hover { transform: translateY(-2px); box-shadow: var(--edge), 0 6px 14px rgba(15,23,42,.16); }
.stButton > button:active, [data-testid="stFormSubmitButton"] > button:active {
  transform: translateY(1px); box-shadow: inset 0 2px 4px rgba(15,23,42,.25);
}

/* ---- inputs: slightly sunken ---- */
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {
  border-radius: 10px !important; box-shadow: inset 0 1px 3px rgba(15,23,42,.10);
  transition: box-shadow .15s ease;
}
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within, [data-baseweb="select"] > div:focus-within {
  box-shadow: inset 0 1px 3px rgba(15,23,42,.10), 0 0 0 3px rgba(79,70,229,.25);
}

/* ---- tabs: raised active tab ---- */
[data-baseweb="tab-list"] { gap: .4rem; border-bottom: 1px solid #d5dcea; }
[data-baseweb="tab"] { border-radius: 10px 10px 0 0; font-weight: 600; }
[data-baseweb="tab"][aria-selected="true"] { background: #fff; box-shadow: 0 -2px 8px rgba(15,23,42,.08); }

/* ---- chat and tables ---- */
[data-testid="stChatMessage"] {
  background: linear-gradient(180deg, #ffffff 0%, #f6f8fd 100%); border: 1px solid #dde4f0; border-radius: 14px;
  box-shadow: var(--edge), var(--raise-1); margin-bottom: .6rem;
}
[data-testid="stChatInput"] { border-radius: 14px; box-shadow: var(--edge), var(--raise-2); }
[data-testid="stDataFrame"] { border-radius: 12px; box-shadow: var(--raise-1); overflow: hidden; border: 1px solid #dde4f0; }
[data-testid="stAlert"] { border-radius: 12px; box-shadow: var(--raise-1); }
[data-testid="stExpander"] { border-radius: 12px; box-shadow: var(--raise-1); background: #fff; }

/* ---- sidebar: dark with an inset edge ---- */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #0f172a 0%, #172036 100%);
  box-shadow: 6px 0 24px rgba(15,23,42,.25);
}
[data-testid="stSidebarNav"] a, [data-testid="stSidebar"] [data-testid="stSidebarNavLink"] {
  border-radius: 10px; margin: 2px 0; transition: background .15s ease, transform .15s ease;
}
[data-testid="stSidebarNavLink"]:hover { background: rgba(129,140,248,.16); transform: translateX(3px); }
[data-testid="stSidebarNavLink"][aria-current="page"] {
  background: linear-gradient(90deg, rgba(99,102,241,.45), rgba(99,102,241,.18));
  box-shadow: inset 3px 0 0 #818cf8, 0 4px 10px rgba(0,0,0,.25);
}
[data-testid="stSidebar"] .stButton > button { background: #1e293b; border: 1px solid #334155; color: #e2e8f0; }

@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
"""


def apply() -> None:
    st.html(_CSS)
