"""
bse_market_suite.py  —  Unified Indian Market Announcements Dashboard
═══════════════════════════════════════════════════════════════════════
(Formerly market_web_report.py, now with the Idea Board embedded under
the BSE Equity tab — see idea_board_streamlit_02.py, merged in below.)

Four source databases, one app:

  • BSE Equity   →  bse_equity.db   (table: announcements; also the source
                     for the embedded Idea Board — idea_groups / idea_types /
                     announcement_idea_scores, written by
                     announcement_ideas_pipeline.py)
  • BSE SME      →  bse_sme.db      (tables: announcements, corp_actions)
  • NSE Equity   →  nse_equity.db   (table: announcements)
  • NSE SME      →  nse_sme.db      (table: announcements)

Pages (sidebar):
  1. Announcements  — search / filter per-DB, clickable PDF links
                       · BSE Equity tab has two sub-tabs:
                         "Announcements" (as before) and "Idea Board"
                         (category-scored announcement ideas)
  2. Charts         — daily volume, category breakdown, timeline per DB
  3. Insights       — keyword freq · trigger flags · clusters · AI digest
  4. My Activity    — per-user view & search history

Install (one-time):
    pip install streamlit pandas plotly anthropic streamlit-option-menu streamlit-authenticator

Run:
    streamlit run bse_market_suite.py

Cron / unattended:
    The app reads the DBs read-only; run market_announcements.py and
    announcement_ideas_pipeline.py separately to populate them.
"""

# ─── IMPORTS ─────────────────────────────────────────────────────────────────

import json
import re
import sqlite3
from collections import Counter
from datetime import date, timedelta
from pathlib import Path


import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

try:
    from streamlit_option_menu import option_menu
except ImportError:
    st.error("Run:  pip install streamlit-option-menu")
    st.stop()

try:
    import streamlit_authenticator as stauth
except ImportError:
    st.error("Run:  pip install streamlit-authenticator")
    st.stop()

try:
    import anthropic
    _ANTHROPIC_OK = True
except ImportError:
    _ANTHROPIC_OK = False

# ─── DB PATHS  (edit if your files live elsewhere) ───────────────────────────

DB_PATHS = {
    "BSE Equity": "bse_equity.db",
    "BSE SME":    "bse_sme.db",
    "NSE Equity": "nse_equity.db",
    "NSE SME":    "nse_sme.db",
}
BSE_ATTACH_BASE = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"
MAX_AI_ROWS     = 50

# ─── TRIGGER TAXONOMY (shared across all sources) ────────────────────────────

TRIGGER_TAXONOMY = {
    "Acquisition / Merger":  ["acquisition","merger","amalgamation","takeover","scheme of arrangement","business transfer"],
    "Buyback":               ["buyback","buy-back","share repurchase","capital reduction"],
    "Dividend":              ["dividend","interim dividend","final dividend","special dividend"],
    "Fundraise / Capital":   ["rights issue","preferential allotment","qip","ncd","fpo","ipo","private placement","debenture"],
    "Board / Management":    ["resignation","appointment","ceo","managing director","key managerial","change in director","new appointment"],
    "Financial Results":     ["financial results","quarterly results","unaudited","audited","q1 ","q2 ","q3 ","q4 ","fy2","half year"],
    "Regulatory / Legal":    ["sebi","nclt","nclat","court order","penalty","show cause","adjudication","regulatory"],
    "Pledge / Encumbrance":  ["pledge","pledged","encumbrance","invocation","release of pledge"],
    "Insider / UPSI":        ["upsi","insider trading","trading window","price sensitive"],
    "Credit Rating":         ["rating","upgrade","downgrade","reaffirm","crisil","icra","care rating","india ratings"],
    "Capex / Expansion":     ["capex","expansion","capacity","new plant","greenfield","brownfield","capital expenditure"],
    "Subsidiary / JV":       ["subsidiary","joint venture","associate","stake","divestment","step-down subsidiary"],
}
TRIGGER_COLORS = {
    "Acquisition / Merger": "#e63946","Buyback": "#f4a261","Dividend": "#2a9d8f",
    "Fundraise / Capital":  "#457b9d","Board / Management": "#6d6875","Financial Results": "#264653",
    "Regulatory / Legal":   "#e9c46a","Pledge / Encumbrance": "#f77f00","Insider / UPSI": "#d62828",
    "Credit Rating":        "#4cc9f0","Capex / Expansion": "#80b918","Subsidiary / JV": "#b5838d",
}
STOP_WORDS = {
    "the","a","an","and","or","of","in","to","for","is","are","has","have","had","was","were",
    "be","been","being","with","on","at","by","from","as","that","this","its","it","we","our",
    "their","pursuant","under","sub","reg","sebi","bse","nse","ltd","limited","pvt","inc","per",
    "re","no","not","will","shall","herewith","enclosed","submission","submitted","intimation",
    "informed","please","find","attached","copy","regarding","ref","information","disclosure",
    "regulation","act","exchange","company","companies",
}

# ─── DESIGN TOKENS ───────────────────────────────────────────────────────────

INK       = "#0f1923"; INK_SOFT = "#5b6878"; INK_MUTED = "#8a96a3"
LINE      = "#e3e8ee"; LINE_SOFT = "#edf1f5"
SURFACE   = "#ffffff"; SURFACE_1 = "#f6f8fa"; SURFACE_2 = "#eef2f6"
ACCENT    = "#1d5fb0"; ACCENT_DK = "#154a8c"; ACCENT_BG = "#eaf1fb"
SUCCESS   = "#1d8a5e"; WARNING = "#b9740a"; DANGER = "#c0392b"
SHADOW    = "0 1px 2px rgba(15,25,35,0.04),0 2px 8px rgba(15,25,35,0.04)"
SHADOW_MD = "0 2px 4px rgba(15,25,35,0.05),0 6px 16px rgba(15,25,35,0.07)"

SOURCE_COLORS = {
    "BSE Equity": "#1d5fb0",
    "BSE SME":    "#e9c46a",
    "NSE Equity": "#2a9d8f",
    "NSE SME":    "#e63946",
}

# ─── PAGE CONFIG ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Market Announcements Dashboard",
    page_icon="📊",
    layout="wide",
)

# ─── SHARED CSS ──────────────────────────────────────────────────────────────

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');
:root{{--ink:{INK};--ink-soft:{INK_SOFT};--ink-muted:{INK_MUTED};--line:{LINE};--surface:{SURFACE};--surface-1:{SURFACE_1};--accent:{ACCENT};--accent-bg:{ACCENT_BG};}}
html,body,[class*="css"]{{font-family:'IBM Plex Sans',sans-serif;color:{INK};-webkit-font-smoothing:antialiased;}}
.stApp{{background:{SURFACE};}}
.block-container{{padding-top:1.25rem;padding-bottom:3rem;max-width:1400px;}}
#MainMenu,header[data-testid="stHeader"]{{background:transparent;}}
footer{{visibility:hidden;}}
::-webkit-scrollbar{{width:9px;height:9px;}}
::-webkit-scrollbar-track{{background:transparent;}}
::-webkit-scrollbar-thumb{{background:{LINE};border-radius:8px;}}
::-webkit-scrollbar-thumb:hover{{background:{INK_MUTED};}}
section[data-testid="stSidebar"]{{width:240px!important;min-width:240px!important;background:{SURFACE_2};border-right:1px solid {LINE};}}
section[data-testid="stSidebar"]>div{{padding:1.1rem 0.9rem;}}
.page-head{{margin-bottom:1.1rem;}}
.page-head h1{{font-size:1.35rem;font-weight:600;color:{INK};margin:0;display:flex;align-items:center;gap:8px;letter-spacing:-0.01em;}}
.page-head p{{font-size:0.85rem;color:{INK_MUTED};margin:3px 0 0;}}
.filter-bar{{background:{SURFACE};border:1px solid {LINE};border-radius:12px;padding:0.85rem 1rem 0.55rem;margin-bottom:1.1rem;box-shadow:{SHADOW};}}
.filter-bar-label{{font-size:0.68rem;font-weight:600;color:{INK_MUTED};text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.55rem;display:flex;align-items:center;gap:6px;}}
.filter-bar-label::before{{content:'';width:3px;height:11px;background:{ACCENT};border-radius:2px;}}
.field-spacer{{height:1.55rem;}}
.metric-card{{background:{SURFACE_1};border:1px solid {LINE};border-radius:10px;padding:0.9rem 1.05rem;text-align:left;transition:border-color 0.15s ease,box-shadow 0.15s ease;}}
.metric-card:hover{{border-color:{INK_MUTED};box-shadow:{SHADOW};}}
.metric-card .val{{font-size:1.55rem;font-weight:600;color:{INK};font-family:'IBM Plex Mono',monospace;line-height:1.1;letter-spacing:-0.01em;}}
.metric-card .lbl{{font-size:0.7rem;color:{INK_MUTED};text-transform:uppercase;letter-spacing:0.07em;margin-top:5px;font-weight:500;}}
.source-badge{{display:inline-block;padding:2px 8px;border-radius:20px;font-size:0.7rem;font-weight:600;letter-spacing:0.04em;margin-bottom:0.5rem;}}
.stTabs [data-baseweb="tab-list"]{{gap:4px;border-bottom:1px solid {LINE};}}
.stTabs [data-baseweb="tab"]{{font-family:'IBM Plex Sans',sans-serif;font-size:0.82rem;font-weight:500;letter-spacing:0.01em;color:{INK_SOFT};padding:0.55rem 1rem;transition:color 0.15s ease;}}
.stTabs [data-baseweb="tab"]:hover{{color:{ACCENT};}}
.stTabs [aria-selected="true"]{{color:{ACCENT}!important;font-weight:600;}}
.stTabs [data-baseweb="tab-highlight"]{{background-color:{ACCENT}!important;height:2.5px;}}

/* ── Form controls: one consistent, compact height + focus language ──────── */
div[data-testid="stTextInput"] input,
div[data-testid="stDateInput"] input{{
  font-size:0.82rem;height:2.35rem;padding:0 0.65rem;border-radius:8px!important;
  border:1px solid {LINE}!important;background:{SURFACE_1};
  transition:border-color 0.14s ease,box-shadow 0.14s ease,background 0.14s ease;
}}
div[data-testid="stTextInput"] input:focus,
div[data-testid="stDateInput"] input:focus{{
  border-color:{ACCENT}!important;background:{SURFACE};box-shadow:0 0 0 3px {ACCENT_BG}!important;
}}
div[data-testid="stDateInput"] div[data-baseweb="base-input"]{{border-radius:8px!important;}}
div[data-testid="stDateInput"] svg{{width:14px;height:14px;color:{INK_MUTED};}}
div[data-testid="stSelectbox"] div[data-baseweb="select"]>div,
div[data-testid="stMultiSelect"] div[data-baseweb="select"]>div{{
  min-height:2.35rem;font-size:0.82rem;border-radius:8px!important;
  border:1px solid {LINE}!important;background:{SURFACE_1};
  transition:border-color 0.14s ease,box-shadow 0.14s ease;
}}
div[data-testid="stSelectbox"] div[data-baseweb="select"]>div:hover,
div[data-testid="stMultiSelect"] div[data-baseweb="select"]>div:hover{{border-color:{INK_MUTED};}}
div[data-testid="stSelectbox"] div[data-baseweb="select"]:focus-within>div,
div[data-testid="stMultiSelect"] div[data-baseweb="select"]:focus-within>div{{
  border-color:{ACCENT}!important;box-shadow:0 0 0 3px {ACCENT_BG};
}}
.stMultiSelect span[data-baseweb="tag"]{{background:{ACCENT_BG}!important;color:{ACCENT_DK}!important;border-radius:6px!important;font-size:0.76rem!important;}}
label[data-testid="stWidgetLabel"]{{margin-bottom:0.3rem;}}
label[data-testid="stWidgetLabel"] p{{font-size:0.76rem;color:{INK_SOFT};font-weight:500;letter-spacing:0.01em;}}

/* ── Buttons: match input height so rows line up ─────────────────────────── */
.stButton button,.stDownloadButton button{{
  border-radius:8px;border:1px solid {LINE};font-weight:500;font-size:0.82rem;
  color:{INK_SOFT};background:{SURFACE};height:2.35rem;padding:0 0.9rem;
  transition:all 0.14s ease;box-shadow:none;
}}
.stButton button:hover,.stDownloadButton button:hover{{border-color:{ACCENT};color:{ACCENT};background:{ACCENT_BG};}}
.stButton button[kind="primary"],.stButton button[data-testid="baseButton-primary"]{{background:{ACCENT};border-color:{ACCENT};color:#fff;}}
.stButton button[kind="primary"]:hover{{background:{ACCENT_DK};border-color:{ACCENT_DK};color:#fff;}}
div[data-testid="stPopover"] button{{
  border-radius:8px;border:1px solid {LINE};font-size:0.78rem;font-weight:500;
  color:{INK_SOFT};background:{SURFACE};padding:0.3rem 0.7rem;
  transition:all 0.14s ease;box-shadow:none;
}}
div[data-testid="stPopover"] button:hover{{border-color:{ACCENT};color:{ACCENT};background:{ACCENT_BG};}}

div[data-testid="stDataFrame"],div[data-testid="stTable"]{{border:1px solid {LINE};border-radius:10px;overflow:hidden;box-shadow:{SHADOW};}}
div[data-testid="stDataFrame"] [role="columnheader"]{{background:{SURFACE_1}!important;color:{INK_SOFT}!important;font-weight:600!important;font-size:0.78rem!important;text-transform:uppercase;letter-spacing:0.04em;}}
div[data-testid="stDataFrame"] [role="row"]:hover{{background:{ACCENT_BG}!important;}}
div[data-testid="stAlert"]{{border-radius:10px;border:1px solid {LINE};font-size:0.85rem;}}
div[data-testid="stMetricValue"]{{font-family:'IBM Plex Mono',monospace;color:{INK};font-weight:600;}}
div[data-testid="stMetricLabel"]{{color:{INK_MUTED};font-size:0.75rem;text-transform:uppercase;letter-spacing:0.05em;}}
hr{{border-color:{LINE}!important;margin:1.2rem 0;}}
.stCaption,[data-testid="stCaptionContainer"]{{color:{INK_MUTED}!important;}}
a{{color:{ACCENT};text-decoration:none;}}
a:hover{{text-decoration:underline;}}
.main .block-container{{animation:fadeIn 0.25s ease;}}
@keyframes fadeIn{{from{{opacity:0.4;}}to{{opacity:1;}}}}
</style>
""", unsafe_allow_html=True)

# ─── IDEA BOARD CSS (embedded from idea_board_streamlit_02.py, namespaced) ───

st.markdown("""
<style>
.idb-card{background:var(--background-color,#fff);border:1px solid rgba(120,120,120,0.25);
  border-radius:8px;padding:14px;margin-bottom:12px;}
.idb-top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;}
.idb-company{font-weight:600;font-size:14px;}
.idb-symbol{font-family:monospace;font-size:11px;opacity:0.65;}
.idb-subject{font-size:13px;opacity:0.85;margin:6px 0;line-height:1.5;}
.idb-score{font-family:monospace;font-weight:700;font-size:13px;padding:2px 8px;border-radius:10px;}
.idb-score.high{background:#E1F5EE;color:#085041;}
.idb-score.mid{background:#FAEEDA;color:#633806;}
.idb-score.low{background:#FAECE7;color:#712B13;}
.idb-kw{display:inline-block;font-family:monospace;font-size:10.5px;background:#E1F5EE;
  color:#085041;padding:2px 6px;border-radius:3px;margin:2px 4px 0 0;}
.idb-kw.cat{background:#FAEEDA;color:#633806;}
.idb-ts{font-family:monospace;font-size:10.5px;opacity:0.55;margin-top:8px;}
</style>
""", unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
#  AUTH  — SQLite-backed user management
#  DB file : market_users.db  (auto-created on first run)
#
#  ┌─────────────────────────────────────────┐
#  │  DEFAULT CREDENTIALS (first run only)   │
#  │  Username : admin                        │
#  │  Password : admin@123                    │
#  │  Role     : admin                        │
#  └─────────────────────────────────────────┘
#  Use the "Create User" tab (admin only) to add more users.
#  Use the "Reset Password" tab to change any password.
# ═════════════════════════════════════════════════════════════════════════════

import hashlib
AUTH_DB = "market_users.db"

def _hash_pwd(pwd: str) -> str:
    return hashlib.sha256(pwd.strip().encode()).hexdigest()

def _auth_conn():
    c = sqlite3.connect(AUTH_DB)
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT    UNIQUE NOT NULL COLLATE NOCASE,
            full_name    TEXT    NOT NULL,
            pwd_hash     TEXT    NOT NULL,
            role         TEXT    NOT NULL DEFAULT 'user',
            created_at   TEXT    DEFAULT (datetime('now','localtime')),
            last_login   TEXT
        )
    """)
    c.commit()
    # Always ensure the default admin exists (idempotent)
    existing = c.execute("SELECT COUNT(*) FROM users WHERE username='admin' COLLATE NOCASE").fetchone()[0]
    if existing == 0:
        c.execute(
            "INSERT OR IGNORE INTO users (username, full_name, pwd_hash, role) VALUES (?,?,?,?)",
            ("admin", "Administrator", _hash_pwd("admin@123"), "admin"),
        )
        c.commit()
    return c

def _verify(username: str, password: str):
    """Return (full_name, role) on success, else None."""
    c = _auth_conn()
    row = c.execute(
        "SELECT full_name, pwd_hash, role FROM users WHERE username=? COLLATE NOCASE",
        (username.strip(),)
    ).fetchone()
    if row and row["pwd_hash"] == _hash_pwd(password):
        c.execute(
            "UPDATE users SET last_login=datetime('now','localtime') WHERE username=? COLLATE NOCASE",
            (username.strip(),)
        )
        c.commit()
        c.close()
        return row["full_name"], row["role"]
    c.close()
    return None

def _create_user(username, full_name, password, role="user"):
    c = _auth_conn()
    try:
        c.execute(
            "INSERT INTO users (username, full_name, pwd_hash, role) VALUES (?,?,?,?)",
            (username.strip().lower(), full_name.strip(), _hash_pwd(password), role),
        )
        c.commit(); c.close()
        return True, ""
    except sqlite3.IntegrityError:
        c.close()
        return False, f"Username '{username}' already exists."

def _reset_password(username, new_password):
    c = _auth_conn()
    n = c.execute(
        "UPDATE users SET pwd_hash=? WHERE username=? COLLATE NOCASE",
        (_hash_pwd(new_password), username.strip())
    ).rowcount
    c.commit(); c.close()
    return n > 0

def _list_users():
    c = _auth_conn()
    rows = c.execute(
        "SELECT username, full_name, role, created_at, last_login FROM users ORDER BY username"
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]

def _delete_user(username):
    c = _auth_conn()
    c.execute("DELETE FROM users WHERE username=? COLLATE NOCASE", (username.strip(),))
    c.commit(); c.close()


def _do_login():
    if st.session_state.get("auth_user"):
        return True

    st.markdown(f"""
    <div class="page-head" style="text-align:center;margin-top:2.5rem;">
      <h1 style="justify-content:center;font-size:1.6rem;">📊 Market Announcements Dashboard</h1>
      <p style="font-size:0.9rem;">BSE Equity · BSE SME · NSE Equity · NSE SME</p>
    </div>""", unsafe_allow_html=True)

    _, mid, _ = st.columns([1, 1.1, 1])
    with mid:
        tab_login, tab_reset, tab_create = st.tabs(["🔑 Log in", "🔒 Reset Password", "➕ Create User"])

        # ── LOGIN TAB ──────────────────────────────────────────────────────
        with tab_login:
            st.markdown("<br>", unsafe_allow_html=True)
            with st.form("login_form", clear_on_submit=False):
                uname     = st.text_input("Username", placeholder="Enter your username")
                pwd       = st.text_input("Password", type="password", placeholder="Enter your password")
                submitted = st.form_submit_button("🔑  Log in", use_container_width=True, type="primary")

            if submitted:
                if not uname or not pwd:
                    st.error("Please enter both username and password.")
                else:
                    result = _verify(uname, pwd)
                    if result:
                        full_name, role = result
                        st.session_state["auth_user"] = uname.strip().lower()
                        st.session_state["auth_name"] = full_name
                        st.session_state["auth_role"] = role
                        st.rerun()
                    else:
                        st.error("❌ Incorrect username or password. Please try again.")

            st.markdown(f"""
            <div style="margin-top:1.2rem;padding:0.85rem 1rem;background:{ACCENT_BG};
                 border:1px solid #bdd3f0;border-radius:10px;font-size:0.8rem;color:{INK_SOFT};">
              <div style="font-weight:600;color:{ACCENT_DK};margin-bottom:4px;">🔑 Default login credentials</div>
              Username &nbsp;→&nbsp; <code style="background:#fff;padding:1px 6px;border-radius:4px;">admin</code><br>
              Password &nbsp;→&nbsp; <code style="background:#fff;padding:1px 6px;border-radius:4px;">admin@123</code><br>
              <div style="margin-top:6px;color:{WARNING};font-size:0.75rem;">
                ⚠️ Please reset your password after first login.
              </div>
            </div>""", unsafe_allow_html=True)

        # ── RESET PASSWORD TAB ─────────────────────────────────────────────
        with tab_reset:
            st.markdown("<br>", unsafe_allow_html=True)
            st.caption("Verify with your current password, then set a new one.")
            with st.form("reset_form", clear_on_submit=True):
                r_user    = st.text_input("Username")
                r_cur_pwd = st.text_input("Current password", type="password")
                r_new_pwd = st.text_input("New password", type="password",
                                          help="At least 6 characters")
                r_confirm = st.text_input("Confirm new password", type="password")
                r_submit  = st.form_submit_button("🔒  Reset password", use_container_width=True, type="primary")

            if r_submit:
                if not all([r_user, r_cur_pwd, r_new_pwd, r_confirm]):
                    st.error("All four fields are required.")
                elif r_new_pwd != r_confirm:
                    st.error("New passwords do not match.")
                elif len(r_new_pwd) < 6:
                    st.error("New password must be at least 6 characters.")
                elif not _verify(r_user, r_cur_pwd):
                    st.error("❌ Current username or password is incorrect.")
                else:
                    _reset_password(r_user, r_new_pwd)
                    st.success("✅ Password reset successfully. Go to Log in tab to continue.")

        # ── CREATE USER TAB (admin PIN protected) ─────────────────────────
        with tab_create:
            st.markdown("<br>", unsafe_allow_html=True)
            st.caption("Create a new user account. Requires admin username + password to authorise.")
            with st.form("create_form", clear_on_submit=True):
                admin_uname   = st.text_input("Admin username",  placeholder="Your admin username")
                admin_pwd     = st.text_input("Admin password",  type="password", placeholder="Your admin password")
                st.markdown("---")
                new_uname     = st.text_input("New username",    placeholder="e.g. john_doe")
                new_fullname  = st.text_input("Full name",       placeholder="e.g. John Doe")
                new_pwd       = st.text_input("New password",    type="password", help="At least 6 characters")
                new_pwd_conf  = st.text_input("Confirm password", type="password")
                new_role      = st.selectbox("Role", ["user", "admin"])
                c_submit      = st.form_submit_button("➕  Create user", use_container_width=True, type="primary")

            if c_submit:
                admin_res = _verify(admin_uname, admin_pwd)
                if not admin_res or admin_res[1] != "admin":
                    st.error("❌ Admin credentials are incorrect or insufficient.")
                elif not all([new_uname, new_fullname, new_pwd, new_pwd_conf]):
                    st.error("All new-user fields are required.")
                elif new_pwd != new_pwd_conf:
                    st.error("New passwords do not match.")
                elif len(new_pwd) < 6:
                    st.error("Password must be at least 6 characters.")
                else:
                    ok, msg = _create_user(new_uname, new_fullname, new_pwd, new_role)
                    if ok:
                        st.success(f"✅ User **{new_uname}** created successfully. They can now log in.")
                    else:
                        st.error(f"❌ {msg}")

    return False

if not _do_login():
    st.stop()

current_user = st.session_state["auth_user"]
current_name = st.session_state.get("auth_name", current_user)

# ─── VIEW / SEARCH HISTORY (session-level, per user) ─────────────────────────

if "view_history" not in st.session_state:
    st.session_state["view_history"] = []
if "search_history" not in st.session_state:
    st.session_state["search_history"] = []

def _log_view(source, rec_dict):
    import datetime
    st.session_state["view_history"].append({
        "source": source,
        "logged_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **rec_dict,
    })

def _log_search(source, filters, result_count):
    import datetime
    st.session_state["search_history"].append({
        "source": source,
        "filters": filters,
        "result_count": result_count,
        "searched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

# ═════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ═════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown(f"""
    <div style="background:{SURFACE};border:1px solid {LINE};border-radius:10px;
         padding:0.6rem 0.75rem;margin-bottom:0.7rem;display:flex;align-items:center;gap:8px;">
      <div style="width:30px;height:30px;border-radius:50%;background:{ACCENT_BG};
           color:{ACCENT_DK};display:flex;align-items:center;justify-content:center;
           font-weight:600;font-size:0.78rem;flex-shrink:0;">
        {(current_name or current_user or "?")[:1].upper()}
      </div>
      <div style="overflow:hidden;">
        <div style="font-size:0.82rem;font-weight:600;color:{INK};white-space:nowrap;text-overflow:ellipsis;overflow:hidden;">
          {current_name or current_user}
        </div>
        <div style="font-size:0.68rem;color:{INK_MUTED};">@{current_user}</div>
      </div>
    </div>""", unsafe_allow_html=True)

    page = option_menu(
        menu_title="Dashboard",
        menu_icon="display",
        options=["Announcements", "Charts", "Insights", "My Activity"],
        icons=["file-earmark-text", "bar-chart-line", "lightbulb", "clock-history"],
        default_index=0,
        styles={
            "container":      {"padding":"0.9rem 0.8rem","background-color":SURFACE,"border-radius":"14px","box-shadow":SHADOW_MD},
            "menu-title":     {"font-size":"1.1rem","font-weight":"600","color":INK,"padding":"0 0 0.7rem 0.2rem"},
            "icon":           {"font-size":"0.95rem","color":INK_MUTED},
            "nav-link":       {"font-size":"0.88rem","font-weight":"500","color":INK_SOFT,"text-align":"left","margin":"2px 0","padding":"0.6rem 0.7rem","border-radius":"8px"},
            "nav-link-selected": {"background-color":ACCENT_BG,"color":ACCENT_DK,"font-weight":"600"},
        },
    )

    st.markdown("<div style='height:0.6rem'></div>", unsafe_allow_html=True)
    if st.button("Log out", use_container_width=True):
        st.session_state["auth_user"] = None
        st.rerun()

# ═════════════════════════════════════════════════════════════════════════════
#  HELPERS — DB ACCESS
# ═════════════════════════════════════════════════════════════════════════════

def _conn(source: str) -> sqlite3.Connection:
    path = DB_PATHS[source]
    if not Path(path).exists():
        return None
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c

def _df(source: str, sql: str, params=()) -> pd.DataFrame:
    c = _conn(source)
    if c is None:
        return pd.DataFrame()
    try:
        df = pd.read_sql_query(sql, c, params=params)
    except Exception:
        df = pd.DataFrame()
    finally:
        c.close()
    return df

# ─── Per-source normalised loaders ───────────────────────────────────────────

def load_bse_equity(from_dt, to_dt, symbol="", category="", subcategory="") -> pd.DataFrame:
    clauses, p = ["DATE(input_timestamp) BETWEEN ? AND ?"], [str(from_dt), str(to_dt)]
    if symbol:      clauses.append("(symbol LIKE ? OR company_name LIKE ?)"); p += [f"%{symbol}%",f"%{symbol}%"]
    if category:    clauses.append("category = ?");    p.append(category)
    if subcategory: clauses.append("subcategory = ?"); p.append(subcategory)
    sql = f"""
        SELECT scrip_code, symbol, company_name, category, subcategory,
               subject, file_name, input_timestamp
        FROM   v_announcements
        WHERE  {' AND '.join(clauses)}
        ORDER  BY input_timestamp DESC
    """
    df = _df("BSE Equity", sql, p)
    if not df.empty and "file_name" in df.columns:
        df["document_url"] = df["file_name"].apply(
            lambda fn: f"{BSE_ATTACH_BASE}{fn}" if fn else "")
    return df


def load_bse_sme_ann(from_dt, to_dt, scrip="", category="", grp="") -> pd.DataFrame:
    clauses, p = ["announce_date BETWEEN ? AND ?"], [str(from_dt), str(to_dt)]
    if scrip:    clauses.append("LOWER(scrip_name) LIKE ?"); p.append(f"%{scrip.lower()}%")
    if category: clauses.append("LOWER(category) LIKE ?");  p.append(f"%{category.lower()}%")
    if grp:      clauses.append("grp = ?");                 p.append(grp.upper())
    sql = f"""
        SELECT scrip_code, scrip_name, grp, category, announce_date,
               end_date, purpose, attachment_url
        FROM   announcements
        WHERE  {' AND '.join(clauses)}
        ORDER  BY announce_date DESC
    """
    return _df("BSE SME", sql, p)


def load_bse_sme_corp(from_dt, to_dt, scrip="", category="") -> pd.DataFrame:
    clauses, p = ["ex_date BETWEEN ? AND ?"], [str(from_dt), str(to_dt)]
    if scrip:    clauses.append("LOWER(scrip_name) LIKE ?"); p.append(f"%{scrip.lower()}%")
    if category: clauses.append("LOWER(category) LIKE ?");   p.append(f"%{category.lower()}%")
    sql = f"""
        SELECT scrip_code, scrip_name, grp, category,
               ex_date, record_date, end_date, purpose
        FROM   corp_actions
        WHERE  {' AND '.join(clauses)}
        ORDER  BY ex_date DESC
    """
    return _df("BSE SME", sql, p)


def load_nse(source: str, from_dt, to_dt, symbol="", subject="") -> pd.DataFrame:
    """Works for both nse_equity and nse_sme (same schema)."""
    # ann_date stored as "28-Jun-2026 23:26:37" — convert to ISO in SQL
    clauses = ["""
        (CASE
           WHEN length(ann_date) >= 11 THEN
             substr(ann_date,8,4)||'-'||
             CASE substr(ann_date,4,3)
               WHEN 'Jan' THEN '01' WHEN 'Feb' THEN '02' WHEN 'Mar' THEN '03'
               WHEN 'Apr' THEN '04' WHEN 'May' THEN '05' WHEN 'Jun' THEN '06'
               WHEN 'Jul' THEN '07' WHEN 'Aug' THEN '08' WHEN 'Sep' THEN '09'
               WHEN 'Oct' THEN '10' WHEN 'Nov' THEN '11' WHEN 'Dec' THEN '12'
               ELSE '00' END||'-'||
             substr(ann_date,1,2)
           ELSE ann_date
         END)
         BETWEEN ? AND ?
    """]
    p = [str(from_dt), str(to_dt)]
    if symbol:  clauses.append("(LOWER(symbol) LIKE ? OR LOWER(company_name) LIKE ?)"); p += [f"%{symbol.lower()}%",f"%{symbol.lower()}%"]
    if subject: clauses.append("LOWER(subject) LIKE ?"); p.append(f"%{subject.lower()}%")
    sql = f"""
        SELECT symbol, company_name, subject, description, ann_date, attachment_url
        FROM   announcements
        WHERE  {' AND '.join(clauses)}
        ORDER  BY ann_date DESC
    """
    return _df(source, sql, p)


# ─── Trigger / NLP helpers ───────────────────────────────────────────────────

def tokenize(text):
    if not text: return []
    return [w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in STOP_WORDS]

def bigrams(toks):
    return [f"{toks[i]} {toks[i+1]}" for i in range(len(toks)-1)]

def top_terms(series, n=30, include_bg=True):
    cnt = Counter()
    for t in series.dropna():
        toks = tokenize(t)
        cnt.update(toks)
        if include_bg: cnt.update(bigrams(toks))
    return cnt.most_common(n)

def flag_triggers(desc):
    if not desc: return []
    lo = desc.lower()
    return [lbl for lbl, terms in TRIGGER_TAXONOMY.items() if any(t in lo for t in terms)]

def assign_cluster(desc):
    flags = flag_triggers(desc)
    return flags[0] if flags else "General / Other"

def ai_digest(rows_text: str) -> str:
    if not _ANTHROPIC_OK:
        return "⚠️  anthropic package not installed. Run: pip install anthropic"
    client = anthropic.Anthropic()
    prompt = f"""You are an Indian equity market analyst. Below is a batch of corporate announcements.

Produce a structured digest:
1. **Executive Summary** (3–4 sentences): dominant themes.
2. **Key Corporate Actions** (bullets): M&A, fundraises, buybacks, capex.
3. **Results Season Signals**: companies announcing results, tone cues.
4. **Regulatory / Risk Flags**: SEBI, NCLT, pledging, insider trading.
5. **Analyst Watchlist** (top 3–5 companies, one-line rationale each).

Be concise, precise, investment-relevant. No filler.

Announcements:
{rows_text}"""
    msg = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text

def wildcard_like(pattern):
    if not pattern: return None
    esc = pattern.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")
    if "*" in pattern or "?" in pattern:
        return esc.replace("*","%").replace("?","_")
    return f"%{esc}%"

def _metric(col, val, lbl):
    col.markdown(f"""<div class="metric-card"><div class="val">{val}</div><div class="lbl">{lbl}</div></div>""",
                 unsafe_allow_html=True)

def _source_badge(source):
    col = SOURCE_COLORS.get(source, ACCENT)
    st.markdown(
        f'<span class="source-badge" style="background:{col}20;color:{col};border:1px solid {col}40;">'
        f'{source}</span>', unsafe_allow_html=True)

def _plotly_defaults(fig, height=380):
    fig.update_layout(
        font_family="IBM Plex Sans", plot_bgcolor="#f7f9fb", paper_bgcolor="white",
        margin=dict(l=10, r=10, t=30, b=40), height=height,
        xaxis=dict(gridcolor=LINE, linecolor=LINE),
        yaxis=dict(gridcolor=LINE, linecolor=LINE),
    )
    return fig


# ─── PER-TABLE SETTINGS (⚙️ gear popover) ────────────────────────────────────
# Gives every report list its own column-visibility / sort / row-height /
# density controls, persisted per-user in session_state so choices survive
# reruns (filter changes, tab switches, etc).

def _report_settings(key: str, columns: list, default_cols: list = None,
                      default_height: int = 500, label: str = None):
    """
    Renders a compact '⚙️ Settings' popover for a report table and returns
    the user's chosen (visible_columns, height, sort_col, sort_asc, compact).

    key             unique per-table session key, e.g. "be_table"
    columns         full list of displayable column names (already renamed
                    to their display labels)
    default_cols    columns shown by default (defaults to all)
    default_height  default table height in px
    label           optional caption shown to the left of the gear button
    """
    ss_key = f"_settings::{key}"
    defaults = {
        "columns": default_cols if default_cols is not None else list(columns),
        "height": default_height,
        "sort_col": "(none)",
        "sort_asc": False,
    }
    if ss_key not in st.session_state:
        st.session_state[ss_key] = dict(defaults)
    saved = st.session_state[ss_key]
    # Guard against stale columns (e.g. filters changed which fields exist)
    saved["columns"] = [c for c in saved["columns"] if c in columns] or list(columns)
    if saved["sort_col"] not in (["(none)"] + columns):
        saved["sort_col"] = "(none)"

    hdr_l, hdr_r = st.columns([0.82, 0.18])
    with hdr_l:
        if label:
            st.caption(label)
    with hdr_r:
        with st.popover("⚙️ Settings", use_container_width=True):
            st.markdown("**Table settings**")
            sel_cols = st.multiselect(
                "Visible columns", columns, default=saved["columns"],
                key=f"{key}__cols",
                help="Choose which columns appear in this table.",
            )
            sort_col = st.selectbox(
                "Sort by", ["(none)"] + columns,
                index=(["(none)"] + columns).index(saved["sort_col"]),
                key=f"{key}__sortcol",
            )
            sort_asc = st.checkbox("Ascending", value=saved["sort_asc"], key=f"{key}__sortasc")
            height = st.slider(
                "Table height (px)", 250, 900, saved["height"], step=50, key=f"{key}__height",
            )
            if st.button("↺ Reset to default", key=f"{key}__reset", use_container_width=True):
                st.session_state[ss_key] = dict(defaults)
                st.rerun()

    new_settings = {
        "columns": sel_cols or list(columns),
        "height": height,
        "sort_col": sort_col,
        "sort_asc": sort_asc,
    }
    st.session_state[ss_key] = new_settings
    return (new_settings["columns"], new_settings["height"],
            new_settings["sort_col"], new_settings["sort_asc"])


def _apply_report_settings(disp_df: pd.DataFrame, settings: tuple) -> pd.DataFrame:
    """Apply the (columns, height, sort_col, sort_asc) tuple from
    _report_settings to a display dataframe: reorders/filters columns and
    sorts rows. Height is used directly by the caller when invoking
    st.dataframe."""
    cols, _height, sort_col, sort_asc = settings
    out = disp_df.copy()
    if sort_col != "(none)" and sort_col in out.columns:
        out = out.sort_values(by=sort_col, ascending=sort_asc)
    keep = [c for c in cols if c in out.columns]
    return out[keep] if keep else out


# ═════════════════════════════════════════════════════════════════════════════
#  IDEA BOARD  (embedded from idea_board_streamlit_02.py, scoped to BSE Equity)
#
#  Reads the idea_groups / idea_types / announcement_idea_scores / announcements
#  tables that announcement_ideas_pipeline.py writes into the SAME bse_equity.db
#  used elsewhere in this app (DB_PATHS["BSE Equity"]). All keys/functions are
#  prefixed idb_ / _idb_ to avoid clashing with the rest of the dashboard.
# ═════════════════════════════════════════════════════════════════════════════

IDB_PAGE_SIZE = 15
IDB_CACHE_TTL = 60  # seconds; "Refresh data" button bypasses this instantly


def _idb_connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA query_only = ON")
    return conn


@st.cache_data(ttl=300, show_spinner=False)
def idb_get_meta(db_path: str, mtime: float):
    """Small, rarely-changing lookup tables — safe to cache longer."""
    conn = _idb_connect(db_path)
    try:
        groups = pd.read_sql_query("SELECT id, name, sort_order FROM idea_groups ORDER BY sort_order", conn)
        types = pd.read_sql_query(
            "SELECT id, group_id, name, description, sort_order FROM idea_types ORDER BY sort_order", conn
        )
    finally:
        conn.close()
    types = types.merge(
        groups.rename(columns={"name": "group_name"}), left_on="group_id", right_on="id", suffixes=("", "_g")
    )
    return groups, types


@st.cache_data(ttl=300, show_spinner=False)
def idb_get_date_bounds(db_path: str, mtime: float):
    conn = _idb_connect(db_path)
    try:
        row = conn.execute("SELECT MIN(input_timestamp), MAX(input_timestamp) FROM announcements").fetchone()
    finally:
        conn.close()
    if not row or not row[0] or not row[1]:
        return None, None
    try:
        return pd.to_datetime(row[0]).date(), pd.to_datetime(row[1]).date()
    except Exception:
        return None, None


@st.cache_data(ttl=IDB_CACHE_TTL, show_spinner=False)
def idb_get_type_counts(db_path: str, mtime: float, date_start, date_end):
    conn = _idb_connect(db_path)
    try:
        if date_start and date_end:
            q = """
                SELECT s.idea_type_id AS id, COUNT(*) AS count, AVG(s.score) AS avg_score
                FROM announcement_idea_scores s
                JOIN announcements a ON a.id = s.announcement_id
                WHERE a.input_timestamp >= ? AND a.input_timestamp <= ?
                GROUP BY s.idea_type_id
            """
            df = pd.read_sql_query(q, conn, params=[f"{date_start} 00:00:00", f"{date_end} 23:59:59"])
        else:
            q = "SELECT idea_type_id AS id, COUNT(*) AS count, AVG(score) AS avg_score FROM announcement_idea_scores GROUP BY idea_type_id"
            df = pd.read_sql_query(q, conn)
    finally:
        conn.close()
    return df


@st.cache_data(ttl=IDB_CACHE_TTL, show_spinner=False)
def idb_get_summary_metrics(db_path: str, mtime: float, date_start, date_end):
    conn = _idb_connect(db_path)
    try:
        if date_start and date_end:
            row = conn.execute(
                """SELECT COUNT(DISTINCT s.announcement_id), COUNT(*)
                   FROM announcement_idea_scores s JOIN announcements a ON a.id = s.announcement_id
                   WHERE a.input_timestamp >= ? AND a.input_timestamp <= ?""",
                [f"{date_start} 00:00:00", f"{date_end} 23:59:59"],
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(DISTINCT announcement_id), COUNT(*) FROM announcement_idea_scores"
            ).fetchone()
    finally:
        conn.close()
    return (row[0] or 0), (row[1] or 0)


def _idb_build_where(date_start, date_end, min_score, search, idea_type_ids):
    clauses = ["s.score >= ?"]
    params = [min_score]
    if date_start and date_end:
        clauses.append("a.input_timestamp >= ? AND a.input_timestamp <= ?")
        params += [f"{date_start} 00:00:00", f"{date_end} 23:59:59"]
    if idea_type_ids:
        placeholders = ",".join("?" for _ in idea_type_ids)
        clauses.append(f"s.idea_type_id IN ({placeholders})")
        params += list(idea_type_ids)
    if search:
        like = f"%{search}%"
        clauses.append("(a.company_name LIKE ? OR a.subject LIKE ? OR a.symbol LIKE ?)")
        params += [like, like, like]
    return " AND ".join(clauses), params


@st.cache_data(ttl=IDB_CACHE_TTL, show_spinner=False)
def idb_count_filtered(db_path, mtime, date_start, date_end, min_score, search, idea_type_ids):
    where_sql, params = _idb_build_where(date_start, date_end, min_score, search, idea_type_ids)
    conn = _idb_connect(db_path)
    try:
        n = conn.execute(
            f"SELECT COUNT(*) FROM announcement_idea_scores s JOIN announcements a ON a.id = s.announcement_id WHERE {where_sql}",
            params,
        ).fetchone()[0]
    finally:
        conn.close()
    return n


@st.cache_data(ttl=IDB_CACHE_TTL, show_spinner=False)
def idb_fetch_page(db_path, mtime, date_start, date_end, min_score, search, idea_type_ids, sort_col, sort_asc, limit, offset):
    where_sql, params = _idb_build_where(date_start, date_end, min_score, search, idea_type_ids)
    order_expr = "a.input_timestamp" if sort_col == "input_timestamp" else "s.score"
    order_dir = "ASC" if sort_asc else "DESC"
    query = f"""
        SELECT s.id AS score_id, s.announcement_id, s.idea_type_id, s.score, s.matched_keywords,
               a.company_name, a.symbol, a.scrip_code, a.subject, a.input_timestamp, a.attachment_url
        FROM announcement_idea_scores s
        JOIN announcements a ON a.id = s.announcement_id
        WHERE {where_sql}
        ORDER BY {order_expr} {order_dir}
        LIMIT ? OFFSET ?
    """
    conn = _idb_connect(db_path)
    try:
        df = pd.read_sql_query(query, conn, params=params + [limit, offset])
    finally:
        conn.close()
    return df


def idb_score_badge(score: float) -> str:
    band = "high" if score >= 70 else "mid" if score >= 40 else "low"
    return f'<span class="idb-score {band}">{score:.1f}</span>'


def idb_render_card(row) -> str:
    kws = json.loads(row.matched_keywords) if row.matched_keywords else []
    kw_html = "".join(
        f'<span class="idb-kw{" cat" if k.startswith("[category]") else ""}">'
        f'{k.replace("[category] ", "")}</span>'
        for k in kws
    )
    link = (
        f'<a href="{row.attachment_url}" target="_blank">View filing &rarr;</a>'
        if row.attachment_url else ""
    )
    return f"""
    <div class="idb-card">
      <div class="idb-top">
        <div>
          <div class="idb-company">{row.company_name or ""}</div>
          <div class="idb-symbol">{row.symbol or ""} &middot; {row.scrip_code or ""}</div>
        </div>
        {idb_score_badge(row.score)}
      </div>
      <div class="idb-subject">{row.subject or ""}</div>
      <div>{kw_html}</div>
      <div class="idb-ts">{(row.input_timestamp or "")[:16]} &nbsp; {link}</div>
    </div>
    """


def render_idea_board(db_path: str):
    """Idea board scoped to BSE Equity — reads idea_groups / idea_types /
    announcement_idea_scores from the same bse_equity.db used elsewhere in
    this app. Run announcement_ideas_pipeline.py separately to populate the
    idea tables (this view is read-only)."""

    st.caption("Corporate announcements categorized by key business events and developments · scored from `announcement_ideas_pipeline.py`")

    with st.expander("⚙️ Data source", expanded=False):
        dcol1, dcol2 = st.columns([5, 1])
        with dcol1:
            idb_db_path = st.text_input("SQLite database path", value=db_path, key="idb_db_path", label_visibility="collapsed")
        with dcol2:
            idb_refresh = st.button("Refresh data", use_container_width=True, key="idb_refresh")

    idb_db_file = Path(idb_db_path)
    if not idb_db_file.exists():
        st.error(
            f"Can't find `{idb_db_path}`. Run the pipeline first, e.g.\n\n"
            f"`python3 announcement_ideas_pipeline.py --db {idb_db_path} run`"
        )
        return

    idb_mtime = idb_db_file.stat().st_mtime

    try:
        groups, types = idb_get_meta(str(idb_db_file), idb_mtime)
    except sqlite3.OperationalError as e:
        st.error(
            f"`{idb_db_path}` doesn't have the idea-board tables yet. Run:\n\n"
            f"`python3 announcement_ideas_pipeline.py --db {idb_db_path} run`\n\n"
            f"Details: {e}"
        )
        return

    if idb_refresh:
        st.cache_data.clear()
        st.rerun()

    # ---------------- filters ----------------
    bounds_min, bounds_max = idb_get_date_bounds(str(idb_db_file), idb_mtime)

    fcol1, fcol2, fcol3, fcol4 = st.columns([2, 2, 1, 1.3])

    with fcol1:
        if bounds_min and bounds_max:
            default_start = max(bounds_min, bounds_max - pd.Timedelta(days=6))
            date_range = st.date_input(
                "Date range", value=(default_start, bounds_max), min_value=bounds_min, max_value=bounds_max,
                key="idb_date_range",
            )
            if isinstance(date_range, tuple) and len(date_range) == 2:
                date_start, date_end = date_range
            else:
                date_start, date_end = bounds_min, bounds_max
        else:
            date_start, date_end = None, None
            st.caption("No announcement dates found — date filter disabled.")

    type_counts = idb_get_type_counts(str(idb_db_file), idb_mtime, date_start, date_end)
    types = types.drop(columns=["count", "avg_score"], errors="ignore").merge(
        type_counts, on="id", how="left"
    )
    types["count"] = types["count"].fillna(0).astype(int)
    types["avg_score"] = types["avg_score"].fillna(0).round(1)

    with fcol2:
        search = st.text_input("Search company or subject", "", key="idb_search")
    with fcol3:
        min_score = st.slider("Minimum score", 0, 100, 0, step=5, key="idb_min_score")
    with fcol4:
        sort_by = st.selectbox("Sort by", ["Score (high to low)", "Date (newest first)"], key="idb_sort_by")

    if "idb_nav_group" not in st.session_state:
        st.session_state.idb_nav_group = None  # None = top level (all groups)
    if "idb_nav_type" not in st.session_state:
        st.session_state.idb_nav_type = None   # None = showing the group itself, not one sub-category

    group_counts = types.groupby("group_name")["count"].sum().to_dict()
    groups_sorted = groups.sort_values("sort_order")

    def _idb_nav_row(items, ncols=4):
        for i in range(0, len(items), ncols):
            row = items[i: i + ncols]
            cols = st.columns(len(row))
            for col, (label, key, on_click) in zip(cols, row):
                with col:
                    if st.button(label, use_container_width=True, key=key):
                        on_click()
                        st.rerun()

    if date_start is not None:
        st.caption(f"Scoped to announcements between **{date_start}** and **{date_end}**")

    st.divider()

    # ---------------- category navigation ----------------
    st.markdown("#### Browse by category")

    if st.session_state.idb_nav_group is None:
        total_all = int(types["count"].sum())
        st.markdown(f"**● All groups**  ({total_all})")
        items = []
        for _, g in groups_sorted.iterrows():
            gname = g["name"]
            cnt = int(group_counts.get(gname, 0))

            def _idb_select_group(gname=gname):
                st.session_state.idb_nav_group = gname
                st.session_state.idb_nav_type = None

            items.append((f"{gname}  ({cnt})", f"idb_grp_{g['id']}", _idb_select_group))
        _idb_nav_row(items)
        selected_group, selected_type = None, None

    else:
        sub_types = types[types["group_name"] == st.session_state.idb_nav_group].sort_values("sort_order")
        group_total = int(sub_types["count"].sum())

        bcol1, bcol2 = st.columns([1, 5])
        with bcol1:
            if st.button("← All groups", use_container_width=True, key="idb_back_to_groups"):
                st.session_state.idb_nav_group = None
                st.session_state.idb_nav_type = None
                st.rerun()
        with bcol2:
            crumb = f"**{st.session_state.idb_nav_group}**"
            if st.session_state.idb_nav_type:
                crumb += f"  →  **{st.session_state.idb_nav_type}**"
            st.markdown(crumb)

        items = []
        all_marker = "●" if st.session_state.idb_nav_type is None else "○"
        if st.session_state.idb_nav_type is not None:
            def _idb_select_all():
                st.session_state.idb_nav_type = None
            items.append((f"{all_marker} All in {st.session_state.idb_nav_group}  ({group_total})", "idb_type_all", _idb_select_all))
        else:
            st.markdown(f"{all_marker} **All in {st.session_state.idb_nav_group}**  ({group_total}) — showing below")

        for _, t in sub_types.iterrows():
            selected = st.session_state.idb_nav_type == t["name"]
            marker = "●" if selected else "○"
            tname = t["name"]

            def _idb_select_type(tname=tname):
                st.session_state.idb_nav_type = tname

            items.append((f"{marker} {tname}  ({int(t['count'])})", f"idb_type_{t['id']}", _idb_select_type))

        _idb_nav_row(items)

        selected_group = st.session_state.idb_nav_group
        selected_type = st.session_state.idb_nav_type

    if selected_type:
        idea_type_ids = tuple(types.loc[types["name"] == selected_type, "id"].tolist())
    elif selected_group:
        idea_type_ids = tuple(types.loc[types["group_name"] == selected_group, "id"].tolist())
    else:
        idea_type_ids = tuple()

    # ---------------- summary metrics ----------------
    total_announcements, total_matches_in_range = idb_get_summary_metrics(str(idb_db_file), idb_mtime, date_start, date_end)
    active_types = int((types["count"] > 0).sum())

    m1, m2, m3 = st.columns(3)
    _metric(m1, f"{total_announcements:,}", "Announcements with an idea")
    _metric(m2, f"{total_matches_in_range:,}", "Total idea matches")
    _metric(m3, f"{active_types} / {len(types)}", "Idea types with data")

    st.divider()

    # ---------------- pagination state ----------------
    filters_key = f"{search}|{min_score}|{sort_by}|{idea_type_ids}|{date_start}|{date_end}"
    if st.session_state.get("idb_filters_key") != filters_key:
        st.session_state.idb_filters_key = filters_key
        st.session_state.idb_page = 1

    sort_col, sort_asc = ("score", False) if sort_by.startswith("Score") else ("input_timestamp", False)

    total_items = idb_count_filtered(str(idb_db_file), idb_mtime, date_start, date_end, min_score, search, idea_type_ids)
    total_pages = max(1, -(-total_items // IDB_PAGE_SIZE))
    idb_page = min(max(st.session_state.get("idb_page", 1), 1), total_pages)
    st.session_state.idb_page = idb_page
    offset = (idb_page - 1) * IDB_PAGE_SIZE

    page_df = idb_fetch_page(
        str(idb_db_file), idb_mtime, date_start, date_end, min_score, search, idea_type_ids,
        sort_col, sort_asc, IDB_PAGE_SIZE, offset,
    )

    # ---------------- render, grouped by idea type ----------------
    if page_df.empty:
        st.info("No announcements match these filters. Try lowering the minimum score or clearing the search.")
    else:
        types_to_show = types[types["id"].isin(page_df["idea_type_id"].unique())].sort_values(
            ["group_name", "sort_order"]
        )
        for _, t in types_to_show.iterrows():
            sub = page_df[page_df["idea_type_id"] == t["id"]]
            if sub.empty:
                continue
            st.subheader(f"{t['name']}  ·  {t['group_name']}")
            st.caption(t["description"])
            cols = st.columns(3)
            for i, row in enumerate(sub.itertuples()):
                with cols[i % 3]:
                    st.markdown(idb_render_card(row), unsafe_allow_html=True)

        st.divider()
        pcol1, pcol2, pcol3 = st.columns([1, 2, 1])
        with pcol1:
            if st.button("← Previous", disabled=idb_page <= 1, use_container_width=True, key="idb_prev"):
                st.session_state.idb_page = idb_page - 1
                st.rerun()
        with pcol2:
            start_n = offset + 1
            end_n = min(offset + IDB_PAGE_SIZE, total_items)
            st.markdown(
                f"<div style='text-align:center;padding-top:6px;font-size:13px;color:var(--text-secondary,#666);'>"
                f"Showing {start_n}–{end_n} of {total_items} &middot; page {idb_page} of {total_pages}</div>",
                unsafe_allow_html=True,
            )
        with pcol3:
            if st.button("Next →", disabled=idb_page >= total_pages, use_container_width=True, key="idb_next"):
                st.session_state.idb_page = idb_page + 1
                st.rerun()

    _log_search("BSE Equity — Idea Board", {"search": search, "min_score": min_score, "group": selected_group, "type": selected_type}, total_items)


# ═════════════════════════════════════════════════════════════════════════════
#  PAGE: ANNOUNCEMENTS
# ═════════════════════════════════════════════════════════════════════════════

if page == "Announcements":

    st.markdown("""<div class="page-head">
      <h1>🗎 Announcements</h1>
      <p>Search and export corporate filings across all four market databases</p>
    </div>""", unsafe_allow_html=True)

    # ── Global filters ────────────────────────────────────────────────────────
    st.markdown('<div class="filter-bar">', unsafe_allow_html=True)
    st.markdown('<div class="filter-bar-label">🔍 Global Filters</div>', unsafe_allow_html=True)

    f_row1 = st.columns([1, 1, 1, 1])
    from_date = f_row1[0].date_input("From Date", value=date.today() - timedelta(days=7))
    to_date   = f_row1[1].date_input("To Date",   value=date.today())
    keyword   = f_row1[2].text_input("Keyword / Symbol / Company", "")
    f_row1[3].markdown("<div class='field-spacer'></div>", unsafe_allow_html=True)
    if f_row1[3].button("Clear filters", use_container_width=True):
        st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

    # ── Per-source tabs ───────────────────────────────────────────────────────
    t_be, t_bs, t_ne, t_ns = st.tabs([
        "🔵 BSE Equity", "🟡 BSE SME", "🟢 NSE Equity", "🔴 NSE SME"
    ])

    # ── BSE EQUITY ────────────────────────────────────────────────────────────
    with t_be:
        _source_badge("BSE Equity")

        be_sub_ann, be_sub_idb = st.tabs(["📋 Announcements", "💡 Idea Board"])

        with be_sub_ann:
            c = _conn("BSE Equity")
            cat_opts  = [""] + ([r["name"] for r in c.execute("SELECT name FROM categories ORDER BY name").fetchall()] if c else [])
            subcat_opts = [""] + ([r["name"] for r in c.execute("SELECT DISTINCT name FROM subcategories WHERE name IS NOT NULL AND name != '' ORDER BY name").fetchall()] if c else [])
            if c: c.close()

            sr1 = st.columns(2)
            be_cat    = sr1[0].selectbox("Category",    cat_opts,   key="be_cat")
            be_subcat = sr1[1].selectbox("Sub-Category", subcat_opts, key="be_subcat")

            df_be = load_bse_equity(from_date, to_date, keyword, be_cat, be_subcat)

            if df_be.empty:
                st.caption(f"{len(df_be):,} record(s)")
                st.info("No records found. Adjust filters above.")
            else:
                disp = df_be.rename(columns={
                    "scrip_code":"Code","symbol":"Symbol","company_name":"Company",
                    "category":"Category","subcategory":"Sub-Category",
                    "subject":"Subject","input_timestamp":"Timestamp","document_url":"Document",
                }).drop(columns=["file_name"], errors="ignore")

                be_settings = _report_settings(
                    "be_table_cfg", list(disp.columns), default_height=500,
                    label=f"{len(df_be):,} record(s)",
                )
                be_view = _apply_report_settings(disp, be_settings)
                _, be_height, _, _ = be_settings

                ev = st.dataframe(
                    be_view, use_container_width=True, height=be_height,
                    on_select="rerun", selection_mode="multi-row", key="be_table",
                    column_config={"Document": st.column_config.LinkColumn("Document", display_text="📄", width="small")},
                )
                for idx in (ev.selection.rows if ev else []):
                    _log_view("BSE Equity", be_view.iloc[idx].to_dict())

                st.download_button("⬇ Download CSV", df_be.to_csv(index=False).encode(), "bse_equity_results.csv", "text/csv")
                _log_search("BSE Equity", {"keyword": keyword, "category": be_cat, "subcategory": be_subcat, "from": str(from_date), "to": str(to_date)}, len(df_be))

        with be_sub_idb:
            render_idea_board(DB_PATHS["BSE Equity"])

    # ── BSE SME ───────────────────────────────────────────────────────────────
    with t_bs:
        _source_badge("BSE SME")
        sme_view = st.radio("Table", ["Announcements", "Corp Actions"], horizontal=True, key="bsesme_view")

        if sme_view == "Announcements":
            df_bs = load_bse_sme_ann(from_date, to_date, keyword)
            if df_bs.empty:
                st.caption(f"{len(df_bs):,} record(s)")
                st.info("No SME announcements found.")
            else:
                disp_bs = df_bs.rename(columns={"scrip_code":"Code","scrip_name":"Company","grp":"Group","category":"Category","announce_date":"Date","end_date":"End","purpose":"Purpose","attachment_url":"Document"})
                bs_settings = _report_settings(
                    "bs_ann_table_cfg", list(disp_bs.columns), default_height=500,
                    label=f"{len(df_bs):,} record(s)",
                )
                bs_view = _apply_report_settings(disp_bs, bs_settings)
                _, bs_height, _, _ = bs_settings

                ev2 = st.dataframe(
                    bs_view, use_container_width=True, height=bs_height,
                    on_select="rerun", selection_mode="multi-row", key="bs_ann_table",
                    column_config={"Document": st.column_config.LinkColumn("Document", display_text="📄", width="small")},
                )
                for idx in (ev2.selection.rows if ev2 else []):
                    _log_view("BSE SME", bs_view.iloc[idx].to_dict())
                st.download_button("⬇ Download CSV", df_bs.to_csv(index=False).encode(), "bse_sme_ann.csv", "text/csv")
        else:
            df_bc = load_bse_sme_corp(from_date, to_date, keyword)
            if df_bc.empty:
                st.caption(f"{len(df_bc):,} record(s)")
                st.info("No corp actions found.")
            else:
                disp_bc = df_bc.rename(columns={"scrip_code":"Code","scrip_name":"Company","grp":"Group","category":"Category","ex_date":"Ex-Date","record_date":"Record Date","end_date":"End","purpose":"Purpose"})
                bc_settings = _report_settings(
                    "bs_corp_table_cfg", list(disp_bc.columns), default_height=500,
                    label=f"{len(df_bc):,} record(s)",
                )
                bc_view = _apply_report_settings(disp_bc, bc_settings)
                _, bc_height, _, _ = bc_settings

                st.dataframe(
                    bc_view, use_container_width=True, height=bc_height, hide_index=True,
                )
                st.download_button("⬇ Download CSV", df_bc.to_csv(index=False).encode(), "bse_sme_corp.csv", "text/csv")

        _log_search("BSE SME", {"keyword": keyword, "from": str(from_date), "to": str(to_date)}, len(df_bs) if sme_view == "Announcements" else 0)

    # ── NSE EQUITY ────────────────────────────────────────────────────────────
    with t_ne:
        _source_badge("NSE Equity")
        ne_sub = st.text_input("Subject filter", "", key="ne_sub")
        df_ne = load_nse("NSE Equity", from_date, to_date, keyword, ne_sub)
        if df_ne.empty:
            st.caption(f"{len(df_ne):,} record(s)")
            st.info("No records found.")
        else:
            disp_ne = df_ne.rename(columns={"symbol":"Symbol","company_name":"Company","subject":"Subject","description":"Description","ann_date":"Date","attachment_url":"Document"})
            ne_settings = _report_settings(
                "ne_table_cfg", list(disp_ne.columns), default_height=500,
                label=f"{len(df_ne):,} record(s)",
            )
            ne_view = _apply_report_settings(disp_ne, ne_settings)
            _, ne_height, _, _ = ne_settings

            ev3 = st.dataframe(
                ne_view, use_container_width=True, height=ne_height,
                on_select="rerun", selection_mode="multi-row", key="ne_table",
                column_config={"Document": st.column_config.LinkColumn("Document", display_text="📄", width="small")},
            )
            for idx in (ev3.selection.rows if ev3 else []):
                _log_view("NSE Equity", ne_view.iloc[idx].to_dict())
            st.download_button("⬇ Download CSV", df_ne.to_csv(index=False).encode(), "nse_equity_results.csv", "text/csv")
        _log_search("NSE Equity", {"keyword": keyword, "subject": ne_sub, "from": str(from_date), "to": str(to_date)}, len(df_ne))

    # ── NSE SME ───────────────────────────────────────────────────────────────
    with t_ns:
        _source_badge("NSE SME")
        ns_sub = st.text_input("Subject filter", "", key="ns_sub")
        df_ns = load_nse("NSE SME", from_date, to_date, keyword, ns_sub)
        if df_ns.empty:
            st.caption(f"{len(df_ns):,} record(s)")
            st.info("No records found.")
        else:
            disp_ns = df_ns.rename(columns={"symbol":"Symbol","company_name":"Company","subject":"Subject","description":"Description","ann_date":"Date","attachment_url":"Document"})
            ns_settings = _report_settings(
                "ns_table_cfg", list(disp_ns.columns), default_height=500,
                label=f"{len(df_ns):,} record(s)",
            )
            ns_view = _apply_report_settings(disp_ns, ns_settings)
            _, ns_height, _, _ = ns_settings

            ev4 = st.dataframe(
                ns_view, use_container_width=True, height=ns_height,
                on_select="rerun", selection_mode="multi-row", key="ns_table",
                column_config={"Document": st.column_config.LinkColumn("Document", display_text="📄", width="small")},
            )
            for idx in (ev4.selection.rows if ev4 else []):
                _log_view("NSE SME", ns_view.iloc[idx].to_dict())
            st.download_button("⬇ Download CSV", df_ns.to_csv(index=False).encode(), "nse_sme_results.csv", "text/csv")
        _log_search("NSE SME", {"keyword": keyword, "subject": ns_sub, "from": str(from_date), "to": str(to_date)}, len(df_ns))

    st.stop()


# ═════════════════════════════════════════════════════════════════════════════
#  PAGE: CHARTS
# ═════════════════════════════════════════════════════════════════════════════

elif page == "Charts":

    st.markdown("""<div class="page-head">
      <h1>📊 Charts</h1>
      <p>Daily volume · category breakdown · source comparison · timeline</p>
    </div>""", unsafe_allow_html=True)

    st.markdown('<div class="filter-bar">', unsafe_allow_html=True)
    st.markdown('<div class="filter-bar-label">📅 Date Range</div>', unsafe_allow_html=True)
    cr = st.columns([1, 1, 2])
    ch_from = cr[0].date_input("From", value=date.today() - timedelta(days=30), key="ch_from")
    ch_to   = cr[1].date_input("To",   value=date.today(), key="ch_to")
    st.markdown('</div>', unsafe_allow_html=True)

    # ── Load counts per source ────────────────────────────────────────────────
    @st.cache_data(ttl=120)
    def chart_daily(src_key, db_key, date_col, from_dt, to_dt, extra_where=""):
        if not Path(DB_PATHS[src_key]).exists():
            return pd.DataFrame()
        sql = f"""
            SELECT DATE({date_col}) AS day, COUNT(*) AS n
            FROM   announcements
            WHERE  DATE({date_col}) BETWEEN ? AND ? {extra_where}
            GROUP  BY day ORDER BY day
        """
        return _df(src_key, sql, (str(from_dt), str(to_dt)))

    @st.cache_data(ttl=120)
    def chart_category(src_key, cat_col, from_dt, to_dt, date_col, extra_where=""):
        if not Path(DB_PATHS[src_key]).exists():
            return pd.DataFrame()
        tbl = "v_announcements" if src_key == "BSE Equity" else "announcements"
        sql = f"""
            SELECT {cat_col} AS category, COUNT(*) AS n
            FROM   {tbl}
            WHERE  DATE({date_col}) BETWEEN ? AND ? {extra_where}
            GROUP  BY {cat_col} ORDER BY n DESC LIMIT 15
        """
        return _df(src_key, sql, (str(from_dt), str(to_dt)))

    # BSE Equity daily
    df_be_d = chart_daily("BSE Equity", "bse_equity.db", "input_timestamp", ch_from, ch_to)
    # NSE Equity daily
    df_ne_d = chart_daily("NSE Equity", "nse_equity.db", "fetched_at", ch_from, ch_to)
    # NSE SME daily
    df_ns_d = chart_daily("NSE SME", "nse_sme.db", "fetched_at", ch_from, ch_to)

    # ── Chart 1: Cross-source daily comparison ────────────────────────────────
    st.markdown("#### Daily Announcement Volume — All Sources")

    frames = []
    for lbl, df_d in [("BSE Equity", df_be_d), ("NSE Equity", df_ne_d), ("NSE SME", df_ns_d)]:
        if not df_d.empty:
            tmp = df_d.copy(); tmp["source"] = lbl
            frames.append(tmp)

    if frames:
        combined = pd.concat(frames, ignore_index=True)
        fig_line = px.line(
            combined, x="day", y="n", color="source",
            color_discrete_map=SOURCE_COLORS,
            labels={"day": "Date", "n": "Announcements", "source": "Source"},
            markers=True,
        )
        _plotly_defaults(fig_line, 360)
        st.plotly_chart(fig_line, use_container_width=True)
    else:
        st.info("No data available for the selected range.")

    st.markdown("---")

    # ── Chart 2: Per-source tabs ──────────────────────────────────────────────
    ct1, ct2, ct3, ct4 = st.tabs(["🔵 BSE Equity", "🟡 BSE SME", "🟢 NSE Equity", "🔴 NSE SME"])

    # BSE Equity
    with ct1:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("##### Daily Volume")
            if df_be_d.empty:
                st.info("No data.")
            else:
                fig = px.bar(df_be_d, x="day", y="n", color_discrete_sequence=[SOURCE_COLORS["BSE Equity"]])
                _plotly_defaults(fig, 300)
                st.plotly_chart(fig, use_container_width=True)
        with col_b:
            st.markdown("##### By Category")
            df_be_c = chart_category("BSE Equity", "category", ch_from, ch_to, "input_timestamp")
            if df_be_c.empty:
                st.info("No data.")
            else:
                fig2 = px.pie(df_be_c, names="category", values="n", hole=0.45, height=300)
                fig2.update_layout(font_family="IBM Plex Sans", margin=dict(l=0,r=0,t=20,b=0))
                st.plotly_chart(fig2, use_container_width=True)

        # Summary metrics
        be_total = _df("BSE Equity","SELECT COUNT(*) n FROM announcements WHERE DATE(input_timestamp) BETWEEN ? AND ?",(str(ch_from),str(ch_to)))
        be_co    = _df("BSE Equity","SELECT COUNT(DISTINCT company_name) n FROM announcements WHERE DATE(input_timestamp) BETWEEN ? AND ?",(str(ch_from),str(ch_to)))
        m1,m2,m3 = st.columns(3)
        _metric(m1, int(be_total["n"].iloc[0]) if not be_total.empty else 0, "Announcements")
        _metric(m2, int(be_co["n"].iloc[0]) if not be_co.empty else 0, "Companies")
        _metric(m3, (ch_to - ch_from).days + 1, "Days in Range")

    # BSE SME
    with ct2:
        col_a, col_b = st.columns(2)
        c_sme = _conn("BSE SME")
        if c_sme:
            with col_a:
                st.markdown("##### Corp Actions by Category")
                df_corp_cat = pd.read_sql_query(
                    "SELECT category, COUNT(*) n FROM corp_actions WHERE category IS NOT NULL GROUP BY category ORDER BY n DESC LIMIT 10",
                    c_sme)
                if df_corp_cat.empty: st.info("No corp actions.")
                else:
                    fig3 = px.bar(df_corp_cat, x="n", y="category", orientation="h",
                                  color_discrete_sequence=[SOURCE_COLORS["BSE SME"]], height=300)
                    _plotly_defaults(fig3, 300)
                    st.plotly_chart(fig3, use_container_width=True)
            with col_b:
                st.markdown("##### Announcements by Category")
                df_ann_cat = pd.read_sql_query(
                    "SELECT category, COUNT(*) n FROM announcements WHERE category IS NOT NULL GROUP BY category ORDER BY n DESC LIMIT 10",
                    c_sme)
                if df_ann_cat.empty: st.info("No announcements.")
                else:
                    fig4 = px.pie(df_ann_cat, names="category", values="n", hole=0.45, height=300)
                    fig4.update_layout(font_family="IBM Plex Sans", margin=dict(l=0,r=0,t=20,b=0))
                    st.plotly_chart(fig4, use_container_width=True)

            ann_cnt  = c_sme.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
            corp_cnt = c_sme.execute("SELECT COUNT(*) FROM corp_actions").fetchone()[0]
            c_sme.close()
            m1,m2,m3 = st.columns(3)
            _metric(m1, f"{ann_cnt:,}", "Announcements (total)")
            _metric(m2, f"{corp_cnt:,}", "Corp Actions (total)")
            _metric(m3, "bse_sme.db", "Source DB")
        else:
            st.info("bse_sme.db not found.")

    # NSE Equity
    with ct3:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("##### Daily Volume")
            if df_ne_d.empty: st.info("No data.")
            else:
                fig5 = px.bar(df_ne_d, x="day", y="n", color_discrete_sequence=[SOURCE_COLORS["NSE Equity"]])
                _plotly_defaults(fig5, 300)
                st.plotly_chart(fig5, use_container_width=True)
        with col_b:
            st.markdown("##### Top 10 Companies by Announcement Count")
            df_ne_co = _df("NSE Equity",
                "SELECT company_name, COUNT(*) n FROM announcements WHERE DATE(fetched_at) BETWEEN ? AND ? GROUP BY company_name ORDER BY n DESC LIMIT 10",
                (str(ch_from), str(ch_to)))
            if df_ne_co.empty: st.info("No data.")
            else:
                fig6 = px.bar(df_ne_co, x="n", y="company_name", orientation="h",
                              color_discrete_sequence=[SOURCE_COLORS["NSE Equity"]], height=300)
                _plotly_defaults(fig6, 300)
                st.plotly_chart(fig6, use_container_width=True)

        ne_tot = _df("NSE Equity","SELECT COUNT(*) n FROM announcements")
        ne_co  = _df("NSE Equity","SELECT COUNT(DISTINCT company_name) n FROM announcements")
        ne_sub_cnt = _df("NSE Equity","SELECT COUNT(DISTINCT subject) n FROM announcements")
        m1,m2,m3 = st.columns(3)
        _metric(m1, int(ne_tot["n"].iloc[0]) if not ne_tot.empty else 0, "Total in DB")
        _metric(m2, int(ne_co["n"].iloc[0]) if not ne_co.empty else 0, "Companies")
        _metric(m3, int(ne_sub_cnt["n"].iloc[0]) if not ne_sub_cnt.empty else 0, "Unique Subjects")

    # NSE SME
    with ct4:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("##### Daily Volume")
            if df_ns_d.empty: st.info("No data.")
            else:
                fig7 = px.bar(df_ns_d, x="day", y="n", color_discrete_sequence=[SOURCE_COLORS["NSE SME"]])
                _plotly_defaults(fig7, 300)
                st.plotly_chart(fig7, use_container_width=True)
        with col_b:
            st.markdown("##### Top 10 Companies")
            df_ns_co = _df("NSE SME",
                "SELECT company_name, COUNT(*) n FROM announcements GROUP BY company_name ORDER BY n DESC LIMIT 10",())
            if df_ns_co.empty: st.info("No data.")
            else:
                fig8 = px.bar(df_ns_co, x="n", y="company_name", orientation="h",
                              color_discrete_sequence=[SOURCE_COLORS["NSE SME"]], height=300)
                _plotly_defaults(fig8, 300)
                st.plotly_chart(fig8, use_container_width=True)

        ns_tot = _df("NSE SME","SELECT COUNT(*) n FROM announcements")
        ns_co  = _df("NSE SME","SELECT COUNT(DISTINCT company_name) n FROM announcements")
        m1,m2 = st.columns(2)
        _metric(m1, int(ns_tot["n"].iloc[0]) if not ns_tot.empty else 0, "Total in DB")
        _metric(m2, int(ns_co["n"].iloc[0]) if not ns_co.empty else 0, "Companies")

    st.stop()


# ═════════════════════════════════════════════════════════════════════════════
#  PAGE: INSIGHTS
# ═════════════════════════════════════════════════════════════════════════════

elif page == "Insights":

    st.markdown("""<div class="page-head">
      <h1>💡 Insights</h1>
      <p>Keyword frequency · Trigger flags · Clusters · AI digest — per source</p>
    </div>""", unsafe_allow_html=True)

    # ── Filter bar ────────────────────────────────────────────────────────────
    st.markdown('<div class="filter-bar">', unsafe_allow_html=True)
    st.markdown('<div class="filter-bar-label">Filters</div>', unsafe_allow_html=True)

    fi1 = st.columns([1, 1, 1.5, 1.5, 1])
    ins_from  = fi1[0].date_input("From", value=date.today() - timedelta(days=30), key="ins_from")
    ins_to    = fi1[1].date_input("To",   value=date.today(), key="ins_to")
    ins_src   = fi1[2].selectbox("Source", list(DB_PATHS.keys()), key="ins_src")
    ins_kw    = fi1[3].text_input("Keyword / Symbol", "", key="ins_kw")
    n_kw      = fi1[4].slider("Top N keywords", 10, 50, 25)

    fi2 = st.columns([2, 1, 1])
    inc_bg = fi2[0].toggle("Include bigrams (2-word phrases)", value=True)
    fi2[1].markdown("<div class='field-spacer'></div>", unsafe_allow_html=True)
    run = fi2[1].button("▶  Run analysis", type="primary", use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    if not run:
        st.info("Set your filters above and click **Run analysis** to begin.", icon="👈")
        st.stop()

    # ── Load the text column depending on source ──────────────────────────────
    with st.spinner("Loading data…"):
        if ins_src == "BSE Equity":
            df = load_bse_equity(ins_from, ins_to, ins_kw)
            text_col = "subject"
            name_col = "company_name"
            cat_col  = "category"
            sub_col  = "subcategory"
        elif ins_src == "BSE SME":
            df = load_bse_sme_ann(ins_from, ins_to, ins_kw)
            text_col = "purpose"
            name_col = "scrip_name"
            cat_col  = "category"
            sub_col  = "grp"
        else:
            df = load_nse(ins_src, ins_from, ins_to, ins_kw)
            text_col = "description"
            name_col = "company_name"
            cat_col  = "subject"
            sub_col  = "subject"

    if df.empty:
        st.warning("No announcements found. Try broadening the date range or removing filters.")
        st.stop()

    # Enrich
    df["triggers"] = df[text_col].apply(flag_triggers)
    df["cluster"]  = df[text_col].apply(assign_cluster)

    flagged_count    = df["triggers"].apply(bool).sum()
    unique_companies = df[name_col].nunique() if name_col in df.columns else "—"
    cat_count        = df[cat_col].nunique() if cat_col in df.columns else "—"

    _log_search(ins_src, {"from": str(ins_from), "to": str(ins_to), "keyword": ins_kw}, len(df))

    # ── Summary metrics ───────────────────────────────────────────────────────
    m1,m2,m3,m4 = st.columns(4)
    _metric(m1, f"{len(df):,}", "Announcements")
    _metric(m2, str(unique_companies), "Companies")
    _metric(m3, str(cat_count), "Categories / Subjects")
    _metric(m4, f"{flagged_count:,}", "Trigger-Flagged")
    st.markdown("<br>", unsafe_allow_html=True)

    # ── Insight tabs ──────────────────────────────────────────────────────────
    itab1, itab2, itab3, itab4 = st.tabs([
        "📈 Keyword Frequency", "🚩 Trigger Flags", "🗂 Clusters", "🤖 AI Digest"
    ])

    # TAB 1 — Keywords
    with itab1:
        st.markdown(f"#### Top terms in `{text_col}` field")
        st.caption(f"{len(df):,} announcements · stop words removed · source: **{ins_src}**")
        terms = top_terms(df[text_col], n=n_kw, include_bg=inc_bg)
        if not terms:
            st.warning("No tokens extracted. The text field may be empty.")
        else:
            term_df = pd.DataFrame(terms, columns=["term","count"])
            fig_kw = px.bar(
                term_df.sort_values("count"), x="count", y="term", orientation="h",
                color="count", color_continuous_scale="Blues",
                labels={"count":"Frequency","term":""}, height=max(400, n_kw*22),
            )
            fig_kw.update_layout(coloraxis_showscale=False, font_family="IBM Plex Sans",
                                  plot_bgcolor="#f7f9fb", paper_bgcolor="white",
                                  margin=dict(l=10,r=20,t=20,b=20))
            st.plotly_chart(fig_kw, use_container_width=True)

            if cat_col in df.columns and st.checkbox("Break down by category"):
                cats_p = df[cat_col].dropna().unique().tolist()
                sel_c  = st.selectbox("Category", cats_p)
                sub_t  = top_terms(df[df[cat_col]==sel_c][text_col], n=n_kw, include_bg=inc_bg)
                if sub_t:
                    sub_df = pd.DataFrame(sub_t, columns=["term","count"])
                    fig_sub = px.bar(sub_df.sort_values("count"), x="count", y="term", orientation="h",
                                     color="count", color_continuous_scale="Teal",
                                     height=max(300, n_kw*22))
                    fig_sub.update_layout(coloraxis_showscale=False, font_family="IBM Plex Sans",
                                          plot_bgcolor="#f7f9fb", paper_bgcolor="white",
                                          margin=dict(l=10,r=20,t=10,b=10))
                    st.plotly_chart(fig_sub, use_container_width=True)

    # TAB 2 — Triggers
    with itab2:
        st.markdown("#### Announcements matched to corporate-action trigger categories")
        exploded = df[df["triggers"].apply(bool)].copy()
        exploded = exploded.explode("triggers").rename(columns={"triggers":"trigger"})

        if exploded.empty:
            st.info("No trigger keywords found in current filter set.")
        else:
            tc = exploded["trigger"].value_counts().reset_index()
            tc.columns = ["trigger","count"]
            tc["color"] = tc["trigger"].map(TRIGGER_COLORS)

            fig_donut = px.pie(
                tc, names="trigger", values="count", hole=0.52,
                color="trigger", color_discrete_map=TRIGGER_COLORS, height=360,
            )
            fig_donut.update_layout(font_family="IBM Plex Sans",
                                     legend=dict(font=dict(size=11)), margin=dict(l=0,r=0,t=20,b=0))
            fig_donut.update_traces(textposition="inside", textinfo="percent+label")

            cc1, cc2 = st.columns([1,1])
            with cc1:
                st.plotly_chart(fig_donut, use_container_width=True)
            with cc2:
                st.dataframe(tc.drop(columns="color").rename(columns={"trigger":"Trigger","count":"Matches"}),
                             hide_index=True, use_container_width=True)

            st.markdown("---")
            st.markdown("##### Filter by trigger")
            sel_trg = st.selectbox("Trigger", ["— all —"] + sorted(exploded["trigger"].unique().tolist()))
            view_e  = exploded if sel_trg == "— all —" else exploded[exploded["trigger"]==sel_trg]
            cols_show = [c for c in [name_col,"trigger",cat_col,text_col] if c in view_e.columns]
            st.dataframe(view_e[cols_show].rename(columns={name_col:"Company","trigger":"Trigger",cat_col:"Category",text_col:"Text"}),
                         hide_index=True, use_container_width=True, height=400)

    # TAB 3 — Clusters
    with itab3:
        st.markdown("#### Announcements grouped by dominant topic cluster")
        cc = df["cluster"].value_counts().reset_index()
        cc.columns = ["cluster","count"]

        fig_cl = px.bar(cc, x="cluster", y="count", color="cluster",
                        color_discrete_map={**TRIGGER_COLORS,"General / Other":"#adb5bd"},
                        labels={"cluster":"","count":"Announcements"}, height=360)
        fig_cl.update_layout(showlegend=False, font_family="IBM Plex Sans",
                              plot_bgcolor="#f7f9fb", paper_bgcolor="white",
                              xaxis_tickangle=-30, margin=dict(l=10,r=10,t=20,b=60))
        st.plotly_chart(fig_cl, use_container_width=True)

        st.markdown("---")
        sel_cl = st.selectbox("Browse cluster", cc["cluster"].tolist())
        cl_df  = df[df["cluster"]==sel_cl]
        cols_cl = [c for c in [name_col, cat_col, text_col] if c in cl_df.columns]
        st.caption(f"{len(cl_df):,} announcements in **{sel_cl}**")
        ev_cl = st.dataframe(
            cl_df[cols_cl].rename(columns={name_col:"Company",cat_col:"Category",text_col:"Text"}),
            hide_index=True, use_container_width=True, height=400,
            on_select="rerun", selection_mode="multi-row", key="cl_table",
        )
        for idx in (ev_cl.selection.rows if ev_cl else []):
            _log_view(ins_src, cl_df.iloc[idx].to_dict())

    # TAB 4 — AI Digest
    with itab4:
        st.markdown(f"#### AI-generated investment digest — **{ins_src}**")
        if len(df) > MAX_AI_ROWS:
            st.warning(f"⚠️  {len(df):,} items in scope — Claude will process the most recent **{MAX_AI_ROWS}**. Narrow filters for a more targeted digest.")
        else:
            st.caption(f"Will send {len(df)} announcements to Claude.")

        restrict_cl = st.selectbox("Restrict to cluster (optional)",
                                   ["— all clusters —"] + sorted(df["cluster"].unique().tolist()))
        ai_df = df if restrict_cl == "— all clusters —" else df[df["cluster"]==restrict_cl]

        if st.button("🤖  Generate AI Digest", type="primary"):
            if ai_df.empty:
                st.warning("No data to analyse.")
            else:
                with st.spinner(f"Claude is reading {min(len(ai_df), MAX_AI_ROWS)} announcements…"):
                    try:
                        sample = ai_df.head(MAX_AI_ROWS)
                        lines = [
                            f"- [{row.get(name_col,'') or row.get('symbol','')} | {row.get(cat_col,'')}] {row.get(text_col,'')}"
                            for _, row in sample.iterrows()
                        ]
                        digest = ai_digest("\n".join(lines))
                        st.markdown("---")
                        st.markdown(digest)
                        st.markdown("---")
                        st.download_button(
                            "⬇  Download digest as .txt", data=digest,
                            file_name=f"{ins_src.lower().replace(' ','_')}_digest_{ins_from}_{ins_to}.txt",
                            mime="text/plain",
                        )
                    except Exception as e:
                        st.error(f"Claude API error: {e}")

    st.stop()


# ═════════════════════════════════════════════════════════════════════════════
#  PAGE: MY ACTIVITY
# ═════════════════════════════════════════════════════════════════════════════

elif page == "My Activity":

    st.markdown(f"""<div class="page-head">
      <h1>🕘 My Activity</h1>
      <p>Every record you've viewed and every search you've run — visible only to you</p>
    </div>""", unsafe_allow_html=True)

    views   = st.session_state.get("view_history", [])
    searches = st.session_state.get("search_history", [])

    unique_co = len({r.get("company_name") or r.get("scrip_name") or r.get("symbol","")
                     for r in views if r.get("company_name") or r.get("scrip_name") or r.get("symbol")})
    m1,m2,m3,m4 = st.columns(4)
    _metric(m1, f"{len(views):,}",   "Records viewed")
    _metric(m2, f"{unique_co:,}",    "Unique companies")
    _metric(m3, f"{len(searches):,}","Searches run")
    _metric(m4, current_user,        "Logged-in user")

    st.markdown("<br>", unsafe_allow_html=True)

    act1, act2 = st.tabs(["📄  Viewed records", "🔍  Search history"])

    with act1:
        if not views:
            st.info("No records viewed yet. Select rows in Announcements or Insights to log them here.")
        else:
            v_df = pd.DataFrame(views)
            st.caption(f"{len(v_df):,} record(s) viewed this session")
            st.dataframe(v_df, hide_index=True, use_container_width=True, height=480)
            st.download_button("⬇ Download view history",
                               v_df.to_csv(index=False).encode(),
                               f"{current_user}_view_history.csv", "text/csv")

    with act2:
        if not searches:
            st.info("No searches logged yet — run a search on Announcements or Insights.")
        else:
            rows_out = []
            for s in searches:
                f = s.get("filters", {})
                bits = [f"{k}: {v}" for k, v in f.items() if v not in (None,"","[]",[], False)]
                rows_out.append({
                    "Source":      s.get("source",""),
                    "Filters":     "; ".join(bits) if bits else "(no filters)",
                    "Results":     s.get("result_count",""),
                    "Searched at": s.get("searched_at",""),
                })
            s_df = pd.DataFrame(rows_out)
            st.caption(f"{len(s_df):,} search(es) this session")
            st.dataframe(s_df, hide_index=True, use_container_width=True, height=480)
            st.download_button("⬇ Download search history",
                               s_df.to_csv(index=False).encode(),
                               f"{current_user}_search_history.csv", "text/csv")
