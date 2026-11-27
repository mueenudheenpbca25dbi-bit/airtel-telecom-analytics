"""Airtel Telecom Analytics - Customer Retention dashboard (Class A, Group 1, Domain A).

Run:  streamlit run app.py
Every metric is computed in SQL and fetched through q(); Pandas only hands rows to Plotly.
"""
import base64
import os
import sqlite3
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Airtel retention", page_icon="📶", layout="wide",
                   initial_sidebar_state="collapsed")

# ------------------------------------------------------------------ configuration
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if HERE.name == "dashboard" else HERE
# Database path (fixed). Set AIRTEL_DB to override it.
DB_PATH = Path(os.environ.get("AIRTEL_DB") or
               r"C:\Users\MUEENUDHEEN\OneDrive\Desktop\airtel-telecom-analytics\data\airtel_dashboard_slim.db")
if not DB_PATH.is_file():
    DB_PATH = None

# ---- Mapping to the Airtel database (edit here if your validated definitions differ) ----
CUSTOMERS = ("customers c JOIN circles ci ON ci.circle_id = c.circle_id "
             "LEFT JOIN v_complaint_customer v ON v.customer_id = c.customer_id")
CIRCLE_COL, REGION_COL, CONN_COL = "ci.circle_name", "ci.region", "c.connection_type"
LEAVER_SQL = "c.is_churned = 1"                  # leaver definition
LEAVE_DATE_COL = "c.churn_date"                  # month of leaving
OPEN_SQL = "v.unresolved_count > 0"              # open-ticket subscriber (has an unresolved complaint)
AS_OF = "(SELECT MAX(churn_date) FROM customers)"  # end of observation window for active customers
TENURE_MONTHS = f"((julianday(COALESCE(c.churn_date, {AS_OF})) - julianday(c.activation_date)) / 30.4375)"
SCOPE_SQL = ""                                   # e.g. "c.ab_group = 'B'" if the validated population is narrower
TOP_N = 8                                        # circles shown in the ranking chart

NATIONAL_REF = 23.75          # supplied national reference (a reference, not a target)
T4_P_VALUE = 0.032            # validated T4 result, one-tailed (fixed, not live)
MIN_GROUP = 100               # groups smaller than this show counts, not rates

# Explicit chart heights (px): (supporting chart, hero chart = two supporting charts stacked + panel header)
SCREENS = {"Large monitor (1080p)": (335, 2 * 335 + 27), "Laptop (900p)": (235, 2 * 235 + 27),
           "Small laptop (768p)": (160, 2 * 160 + 27)}
RED, DEEP, SOFT, GREY, GREEN = "#E40000", "#a8000f", "#f2a0a0", "#8b94a7", "#2e9e5b"
INK, MUTED = "#2a1217", "#6f5a5e"
REDS = [[0, "#fff1f1"], [.5, "#ff8a8a"], [1, "#b00010"]]
BUBBLE = [[0, "#f5a3a3"], [1, "#8f000e"]]
RAMP = ["#7a000d", "#9a0010", "#b80013", "#d4001a", "#e0262b", "#e84a4a", "#ee6464", "#f27c7c"]
CONN_COLORS = {"Prepaid": DEEP, "Postpaid": SOFT}
TENURE_ORDER = ["0-6m", "6-12m", "1-2y", "2-3y", "3y+"]

# chart styles the user can pick, per slot (first option is the default)
STYLES = {
    "tenure": ["Area", "Radar", "Waterfall", "Lollipop", "Heat strip"],
    "circle": ["Bubble", "Treemap", "Heatmap", "Funnel", "Lollipop"],
    "conn": ["Heatmap", "Radar", "Bullet", "Lollipop"],
    "d_month": ["Area", "Line", "Step", "Lollipop"],
    "d_compare": ["Waterfall", "Dumbbell", "Bullet"],
    "d_tenure": ["Lollipop", "Area", "Radar", "Waterfall", "Heat strip"],
    "d_conn": ["Bullet", "Heatmap", "Radar", "Lollipop"],
}
STYLE_LABELS = {"tenure": "Tenure chart", "circle": "Circle chart", "conn": "Connection chart",
                "d_month": "Monthly leavers chart", "d_compare": "Circle vs national chart",
                "d_tenure": "Tenure chart", "d_conn": "Connection chart"}
OVERVIEW_SLOTS, DETAIL_SLOTS = ["tenure", "circle", "conn"], ["d_month", "d_compare", "d_tenure", "d_conn"]
DEFAULTS = {"f_conn": "All", "f_region": "All", "s_screen": "Large monitor (1080p)", **{f"s_{k}": v[0] for k, v in STYLES.items()}}

# ------------------------------------------------------------------ SQL fragments
LEFT = f"SUM(CASE WHEN {LEAVER_SQL} THEN 1 ELSE 0 END)"
RATE = f"CASE WHEN COUNT(*) >= {MIN_GROUP} THEN ROUND(100.0 * {LEFT} / COUNT(*), 1) END"
TENURE_GRP = (f"CASE WHEN {TENURE_MONTHS} < 6 THEN '0-6m' WHEN {TENURE_MONTHS} < 12 THEN '6-12m' "
              f"WHEN {TENURE_MONTHS} < 24 THEN '1-2y' WHEN {TENURE_MONTHS} < 36 THEN '2-3y' ELSE '3y+' END")
STATS = f"COUNT(*) AS n, {LEFT} AS leavers, {RATE} AS rate"
FROM = f" FROM {CUSTOMERS}"


@st.cache_resource
def connect():
    return sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, check_same_thread=False)


@st.cache_data(show_spinner=False)
def q(sql, params=()):
    """Run a read-only SQL query and return a DataFrame."""
    return pd.read_sql_query(sql, connect(), params=params)


def where(conn="All", region="All", circle=None, extra=()):
    conds, params = ([SCOPE_SQL] if SCOPE_SQL else []) + list(extra), []
    if conn != "All":
        conds.append(f"{CONN_COL} = ?")
        params.append(conn)
    if circle:
        conds.append(f"{CIRCLE_COL} = ?")
        params.append(circle)
    elif region != "All":
        conds.append(f"{REGION_COL} = ?")
        params.append(region)
    return (" WHERE " + " AND ".join(conds) if conds else ""), tuple(params)


def whole(v):
    return int(v) if pd.notna(v) else 0


def top(sql, params, label="lab", highest=True):
    """Highest (or lowest) rate row of a grouped query; drives the insight titles."""
    d = q(f"SELECT {label} AS x, rate FROM ({sql}) WHERE rate IS NOT NULL "
          f"ORDER BY rate {'DESC' if highest else 'ASC'} LIMIT 1", params)
    return (d.x[0], d.rate[0]) if len(d) else None


# ------------------------------------------------------------------ styling
ACTIVE_NAV = "circle" if st.session_state.get("page") == "Circle Analysis" else "overview"
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap');
html, body, .stApp, [class*="st-"] {{font-family:'Poppins','Segoe UI',sans-serif;}}
#MainMenu, footer, [data-testid="stToolbar"] {{display:none;}}
header[data-testid="stHeader"] {{background:transparent; height:0; pointer-events:none;}}
header[data-testid="stHeader"] button, header[data-testid="stHeader"] a {{pointer-events:auto;}}
.stApp {{background:linear-gradient(160deg,#fff 0%,#fff6f6 60%,#ffeaea 100%); color-scheme:light; color:{INK};}}
.block-container {{padding:.4rem 1rem 0 1rem; max-width:100%;}}
div[data-testid="stVerticalBlock"] {{gap:.4rem;}}
div[data-testid="stHorizontalBlock"] {{gap:.6rem;}}
div[data-testid="stElementContainer"] {{margin:0;}}

/* banner */
.st-key-banner {{background:linear-gradient(100deg,#7d000c 0%,#c40016 45%,#ef2b2b 100%); border-radius:14px;
  padding:6px 18px; position:relative; overflow:hidden; box-shadow:0 4px 14px rgba(160,0,20,.25);}}
.st-key-banner:before {{content:""; position:absolute; pointer-events:none; z-index:0; right:-80px; top:-60px; width:340px; height:340px; border-radius:50%;
  background:radial-gradient(circle,rgba(255,255,255,.22),rgba(255,255,255,0) 65%); animation:glow 7s ease-in-out infinite;}}
@keyframes glow {{50% {{transform:translate(-40px,18px) scale(1.15);}}}}
.hdr {{display:flex; align-items:center; gap:14px; position:relative; z-index:1; padding-bottom:1rem;}}
.st-key-banner [data-testid="stHorizontalBlock"] {{position:relative; z-index:2;}}
.hdr .div {{width:2px; height:38px; background:rgba(255,255,255,.55);}}
.logo {{display:flex; align-items:center;}}
.logo img {{height:48px; width:auto; display:block;}}
.logo.chip {{background:#fff; border-radius:10px; padding:4px 12px;}} .logo.chip img {{height:34px;}}
.ttl {{font-size:1.2rem; font-weight:700; color:#fff; line-height:1.2;}}
.rdr {{font-size:.72rem; color:#ffe3e3;}}
.st-key-banner .stButton button {{background:rgba(255,255,255,.15); color:#fff; border:1px solid rgba(255,255,255,.55);
  border-radius:10px; font-weight:600; min-height:36px; width:100%;}}
.st-key-banner .stButton button p {{color:inherit; font-size:.86rem; margin:0;}}
.st-key-banner .stButton button:hover {{background:rgba(255,255,255,.3); border-color:#fff; color:#fff;}}
.st-key-nav_{ACTIVE_NAV} button {{background:#fff !important; color:{RED} !important; border-color:#fff !important;}}

/* dark filter sidebar */
section[data-testid="stSidebar"] {{background:linear-gradient(180deg,#1b1b1f 0%,#2a2a2f 62%,#6b0712 100%);
  min-width:250px !important; max-width:250px !important; border-right:3px solid {RED};}}
section[data-testid="stSidebar"] * {{color:#f4f4f6;}}
section[data-testid="stSidebar"] .block-container, section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {{padding-top:.6rem;}}
section[data-testid="stSidebar"] label p {{color:#e9e9ee !important; font-size:.76rem !important; margin:0 !important;}}
div[data-baseweb="select"] > div {{background:#fff !important; border:none; border-radius:8px; min-height:34px;}}
div[data-baseweb="select"] * {{color:{INK} !important;}}
div[data-baseweb="popover"] * {{background:#fff; color:{INK};}}
.sb-h {{font-size:1.05rem; font-weight:700; margin:.1rem 0 .2rem; display:flex; align-items:center; gap:8px;}}
.sb-h svg {{width:20px; height:20px; fill:none; stroke:#fff; stroke-width:2; stroke-linecap:round; stroke-linejoin:round;}}
.sb-sub {{font-size:.66rem; color:#b9b9c2; margin:-.1rem 0 .2rem;}}
section[data-testid="stSidebar"] hr {{border-color:#46464d; margin:.5rem 0;}}
section[data-testid="stSidebar"] .stButton button {{background:{RED}; color:#fff; border:none; font-weight:600; width:100%;}}

/* cards */
.card {{display:flex; align-items:center; gap:12px; box-sizing:border-box; height:100px; margin-bottom:1rem; padding:8px 14px; border-radius:12px;
  border:1px solid #f1c3c3; background:linear-gradient(135deg,#fff 45%,#ffe6e6); box-shadow:0 2px 8px rgba(160,0,20,.08);
  animation:rise .75s cubic-bezier(.2,.8,.2,1) both; animation-delay:var(--d,0s);}}
@keyframes rise {{from {{opacity:0; transform:translateY(18px) scale(.97);}} to {{opacity:1; transform:none;}}}}
.ico {{flex:none; width:46px; height:46px; border-radius:50%; background:#fff; display:grid; place-items:center; border:1.5px solid #f1a9a9;}}
.ico svg {{width:26px; height:26px; fill:none; stroke:{RED}; stroke-width:1.9; stroke-linecap:round; stroke-linejoin:round;}}
.ct {{font-size:.82rem; font-weight:600; color:{INK};}}
.cv {{font-size:1.55rem; font-weight:700; color:{RED}; line-height:1.15;}}
.cs {{font-size:.66rem; color:{MUTED}; line-height:1.25;}}
.pill {{font-size:.58rem; font-weight:600; background:#fff; color:{MUTED}; border:1px solid #f1c3c3; border-radius:99px;
  padding:0 7px; margin-left:6px; vertical-align:middle;}}
.st-key-sel {{box-sizing:border-box; height:100px; padding:6px 14px; border-radius:12px; border:1px solid #f1c3c3;
  background:linear-gradient(135deg,#fff 45%,#ffe6e6); box-shadow:0 2px 8px rgba(160,0,20,.08); animation:rise .75s both;}}
.st-key-sel label p {{color:{INK} !important; font-size:.78rem !important; font-weight:600;}}

/* chart panels */
.ph {{background:linear-gradient(90deg,#8f000e,#d4001a); color:#fff; padding:4px 12px; border-radius:10px 10px 0 0;
  margin-bottom:-.45rem; position:relative; z-index:2; min-height:34px;}}
.pt {{font-weight:600; font-size:.84rem; line-height:1.25;}} .ps {{font-size:.64rem; color:#ffd9d9; line-height:1.2;}}
[data-testid="stPlotlyChart"], .tk {{background:#fff; border:1.5px solid #f1c3c3; border-top:none; border-radius:0 0 10px 10px;}}
[data-testid="stPlotlyChart"] {{animation:wipe 1.1s cubic-bezier(.3,.7,.2,1) both; animation-delay:.3s;}}
@keyframes wipe {{from {{clip-path:inset(0 100% 0 0);}} to {{clip-path:inset(0 0 0 0);}}}}
.tk {{box-sizing:border-box; padding:14px 12px 6px; overflow:hidden; animation:fade 1s both .5s;}}
@keyframes fade {{from {{opacity:0;}} to {{opacity:1;}}}}
.tk div {{font-size:.72rem; color:{INK}; line-height:1.3; margin:3px 0; display:flex; gap:8px;}}
.tk b {{flex:none; width:7px; height:7px; border-radius:50%; background:{RED}; margin-top:5px;}}
@media (prefers-reduced-motion: reduce) {{* {{animation:none !important;}}}}
</style>
""", unsafe_allow_html=True)


LOGO_TYPES = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def find_logo():
    """Looks for airtel_logo.png/.svg/.jpg/.webp (or airtel.*, logo.*) next to app.py, in assets/, or the project root."""
    for folder in (HERE, HERE / "assets", ROOT, ROOT / "assets"):
        for name in ("airtel_logo", "airtel", "logo"):
            for ext in LOGO_TYPES:
                p = folder / f"{name}{ext}"
                if p.is_file():
                    return p
    return None


# Airtel logo (white, transparent background) embedded so no extra file is needed.
EMBEDDED_LOGO = "iVBORw0KGgoAAAANSUhEUgAAAJ8AAACgCAYAAAASN76YAAAfGElEQVR42u1deZhcVZX/nVfV2QghCSRsAdmXsMoi4LALIjIojjIoiwzoDMyM+8cI6qiInyMOKgqKgAuLsgyiDCIKYkAZISyCrAYChn0TQhJISHdXvfebP9659umb+6peV1d3V3Xu+b73VXct793ld892zzlXEKk0kRQAAkBEJPU+SwDMAbAJgFkAtgWwKYAZAKYDSPS31GspgMUAFgD4C4AXADwiIsu85yUAMhHheBtPiZAqBbqkAHBbA3gzgF0BzAWwBYB1AUwFUBnCI+oAXgXwFID7APwOwHwRecK2QUSyCL7Vi8vBTTrJCoCdAewP4BAAOwGYHfq5eRUAmfeZmKtoLp4GcAuAywD8XkT6XZvGCwgj+MKgSyyXI7mlgu3dAHYHsJb5SaqASgLjKgEwNhr7TL9TMZ/XAdwM4BwRud4tAp8LR+py0Clns+/tR/JCks9yMNVJ9pNMSWbmNfO+l5lX/3P7Xhr4TmqeQf37EpIbOwDqQok0XkBHchLJ95D8JcleA5ZUQZcVAI0l3g99xhIATc17j5B8p2l7BGAXGxLu7wkkjyR5s8exak1AM1qUKfhJ8g2Sn3TAiwDsPm6XmL8PI3mDx+VqZrI7iWqGE/43yWoEYPcAz4rYt5C8yoDMilar32UdBL7MtJMkz1X9L4kA7GARa8TUBiTPJLnMU+7txKae0dBp5LgzSX7FVyMidaZBcTTJBd4k+sp+1sGg89uZ6nVitwFQxju3Mw7izQB8CcCx+nE/gJ5xMAbOd/gKgENE5E/dshsi4x14yvX+BcBpADbWyaoDqI6j/qfIHdO/AXAEgF4A6PT94GQcgk4M8OYC+CmA8xR4df1aFQMb/OOBKsrJDwZwrIKu4xeWjDPgWTF7LICvAdhAOYPdT03NpI2LruvC6gHwAIADRORVktLJ3C8ZR8CrKLdbk+RZAC5R4PVrPxMM3jutYHxRRfu3A4Aju4G5JOMEeImIpBoA8FMAp2AgmsTqdpVxquc6ju7E7dEkJ+tilAi+kTcs9gVwPfLoEytmVxeyANwdwC6dPsdJlwPPidpjAVwNYEvldokxKFYXx6tTKTIAkwEc6LliOo6qXQ68lORxAM4HMEWV7ooZdAn8PV7JLTRnTO2rBkfH+vuSLgfeIQDOVeClxqhwuQ/S6au/zXMpZk63BvAmHS+J4GuvcbEzgIuQRxU7J2tSYMWuDqLXj55eD8Dmndz/pAuBl5FcX10p6xvgRRoAH5H7/NaKrpb2AM9FpFQBfBPAjgBqEXiF+h8ArNHJjewmg0OU630KwPs946IbwcEG7pJ20eadrPN2BfiMuN0VwGfQ+fuyRW6eUHaaT76PUgIcrSxAp0TON3xxS5I9AM4AMFMnqBPbnnnAcSB0vkcHxpUAntXrrwrGDZD7KWcZECYF9xwXbqNu4HxO3H4QwKGeuO3UScgMl3JWeC+AuwFcC+B25GUyVopIny6wSWpAHQbgX5GX20gNYNmCWF4W1d9hiFsNkZpB8iGTQEMTwdtp1K+XoydInk1yH5KTS/Z7DsmrC3J/y4bYk+Qxer9K5HytsT03iNsp10s8l0In6XmZujiAPLTpBwCuFZGnDbAqTQyORESe1bD4mQAOMKK3rMh13+mNLGx4rpXpJO/3VnQ6hvkTfhZZZrgxta3/THK6x8FLZ5eZdMi9NckpGwKnz0zW3ds7mfN1sp/Pte0gANt71mMyBlwtNdwq8wyMKoDHAXwCeSDn90VkqUtpFJFMr7KcOlWg3oa8TouYZ2VNfHuOFgN4ppNdLZ0MPjfI7zJW31iSMxxs0Z8qgNcAnAXgYBH5tkYQV3RTP21lY19BmujrPE+USgNRSzNOTwB4LPr5WhC5quvNBvDWDrFsM8MBJ+h7NwA4Q0TmG/GWtamClAPMY8ijsScYXVEa+BYdLRSReieH0ncq53Pt2hV5tc+xHjzHVSoKgicAnAzgcBGZ73Q65XTtbutC5FVMy3IwB8z5nS7dOt3anQ1goufvGk09z9bIqyr3uxjAV0RkkTMmRihmjsZi7TfAaiZ2EwArADzYySK3k8HnBmzDAjCM9LPd81NjUCwAcLqIXOWJ2JHWRTPT70Y7OxZkCyL4hk/rlVjxIz0+NeThW2eIyFOmNO1IVwZ1nGy6cv9mem9qxOxvRWRZp1cu6HTwLRmDZzoRnwB4BMCXRORKx+0UdKPJTbYAsGYJzu+imPvVPdMJRlpXgs8N2qIxAJ6rZnARgC+KyDOjyO1CYzCnpN7rpMMDAO4wIrtjqVMtIRrwrcSqUSJoQZ8hBm/42/dTY80+BuBoETlRgVcREY6B+HLtnDvEvl4nIq+ryF0dclfaPOoDW2uTSf7eCyRIC7a7ypaVteXQbB2+Pi3+vYlrw1iVG/P6f59XkTQrCCTISL6k9Wlirb5hToArW3uU2aukB8SsxT3ZugfimzUTDsaSHdO+K/j3J7myREFxt7d8iQVvpGFyAJJVkpca7lTzNvbLUj1Q3PuPJI8nOWmsuV3BwjsvEEZWVCxyJcm9I9drv/iZSfJ6r25yWoLbpQXRIHeQ/DDJaZ3C7QLA29ic/5F64jUUu3d15HojB8C1SH5DuZ9/RoUFZL2givyLJP9Hz9iYZEHXSRNmwPdFT91oxPVWRK43wgDUvw8k+ROSzzXhfL0k/0zySpIfIblNSK/qND1XRf8cko83AJ09lcjqel0FPOk2ADr3gZ74uC2AzZCnCM5AHsP2EgaOEH1IRF7x7tGxR4iaLL0zAZyK4oT4TK8K8lrMB4rIQ+PxZEp0IncYotFS6XSuYMTtjiQXN7Hm7TEIp0dxOzYgrHjna0jo/SHeM3iNglXv2n2VZ+EWWe4keacaY/EMti4HclICIDJCz6/o67HGcGrE9erqWjmgm7ledTUH3aDDk0luhPwA5x2R1znpR56bcaeIPG71sjbreSnJNwH4MgZq7Ln4QT9rrR95ju9ZInJL1PO6lNuZv3cg+R2SiwqsyxdJXmDOupV2gV9FbdWI295AVpzvr7yJ5NR43lp3As+lJs4geYYq+L5zuuad8Eh13ezRLgAacXuKcZ3UC9wq7v2nSW4VjYzuBJ6b8N1I/sGrNJAW5ObWDBd6mOR6wwWgacc71Ulc8w4brAcc6cvNQc8ReF0KvHeQfMYDHQMH6vlRI2535bThAMC0Y1uST3qczQKv7iWnn2x/H6n7gPc2kq+UcGeEjpvv09drWuV8xp83S/eYLfDqHsermzZ+1VjmUc/rNuNCQ5ReMhEyQy2T4UBybSvgM+2YSvLaQJxeveBY+wtJToj+vO4F3ly1Zn0OU/Zkb5qQrKuGCj7TjgkkL/IWQBYAudu3/ZGrbhWB12V+PL3WIDnPuDKGEgVtdS4HiI8NRefzgHe+B+TQAc6O411GckoEXndzvS94HK+VKldO9/oTyVllAWHaMFF9hfTCvnwAuvd/YIAXLdsuBd6u6sez3KUZ+FLPAnbAW0pyn7KA8IB3YUFxyyxQAu0CI2oj8LpU5FZMhc+yte2KnLzLSb6vBeCtQfLiAOe1eRk189yvaf3pCLwu53oHG/dINkT9zvr1Xid5ZFkfm3HrrGPAX/PyT3wLup/kKXbxxJnsbkPjshL+vKLQdOcCeY3kB8oAz3Fb/Xsbs4OSelZz5hk/L+vJmYjulPHB9XYmuaRAlJY1Ll4g+Y4hAM/6ExcazpYGEpocV11I8kDX9gi88QG+zzXI+Ar58PxIkkV6gHQZ4NkImQ+aQIVaAPw2eGAeyc3LivNIHS5u9bVHw46yQGRKI+A5P95jJHcrCTwnZieR/Lq5Rz2wRWd3Vc4luVYE3vjjepurHsVAojgLzvGoG+Dt3AwUXvj+liR/VWAxO5A7UL5C8qQQ14zU3eBzXOioBq6TEDmO9CTJXRoBzxoV+v97zbZdLeDEtsEBt5PcMxoW4xt8nwo4dJvlRCxrdm6FB7o5ulVWK+CwNsNsJcmz3NkcnZaoHqm94PuCB4isgc7nvvPxIuB5IraH5AkkHw34BlMjYh3w7iP57ihmVx/wnV4yesWJ5F8UpGFWvGoJB5O8MRBkakt21IwoP1tPRY9ulNWY86UNRG4vycNMRaxV0ihJ7qFlN3oLdkJ83XK+E+HRmi2m8Zo66Wo5JyguCeKODVgG4BEtn1E3gOkBsD+AfwJwOPK6yECeuljRy5WtSPT/xQC+CeAcEVmuIOYol9ONNMauln1IvtEkoCAz3PHTakBspr/9BMnbAs7h1DMm7HbZtc5FE7nd6gk+p6+tqYp+1iBo09f9niT5lBGtfhRKGogyJsl7Sf6jEflRt4vcb1Au7FCDR/s9S9kPe3I+wVNJzoyWbCQYK1U0O+wR42crc3ZuKLjT355bpIUbN7EiNnK7SD73O0xj8UIcMCsIpSoKtf+Tcro5If9fpEghAP6DSRAP1WputPvxlGaavdcFARjQRREbqRQAtyX5fbMHW+RwXqwGxIVarmyOd78oXttIsjoA0JRA2xj5Gb7bID9Qz51o9CqAR5GftbZYRBZ7FnTHltKN4OsODsiy4NHvi/4m1r6L4GufGG72tcjhIkWKFClSpEiRIkWKFClSpEiRIkWKFClSpEiRIkWKFClSpEiRIkWKFCnSKFPXxPOZ8HV3+DFi3F2kEQddg5JlMXsscr6RA57jbiQnAFgbA8fAvyYib/jfixSpbWJWM8ZOIPl/Wh3+ZZJ/JXmPVqOa5onlSJHaArxZWpqsEd1qKrpHAEYato4nWgH0YlNosR4oxOgK9txIcq1Or3PszuoYzwnnZv66r5+m2tPh5syK1DuB21YecFWlTrS/7xSQqdowLpPNQ/0cLyL3Uq90bVGNFVdN6joDXBnLVd/g8+laB3AnklO7VVVo1E+VWOvq8RD7kJxUdJ9qp3VKREiyCmBL97Z5lYCF7hK8NwMwFXml0bFxHQxY5jMBTAGwIfIKCesDmANgrr5OALA38ioJYvrYHS6SVfu5OYCdtL/bA9hU+/xXAH8HoDfkkejUsrgCYI0h/mYC8tK0Y7FoEhHJSG4F4Es6AetqH6YEfrJC3UXdxvFcP3cE8BkA22k/ZwDoCfxkKRq486odurJqJF8tyRFsvZWVY7hYoNz3/d5ndY9rOz9lN+qArs1bBfpJ5PWpof3rMf8HKekw0NFYSHcGJogF4BMAd4jISl2dYyXG+pAXDK/rlSo37tGF7lSECrq7VEm/9i3FQFF0V2A9Mf1LugZ8Hl2OvKp81XAP/6prR5cC+HGHcIaqXpUGIOv23Ri7gATFVf+zrgKf6hQiIvcDOEUBVjUdccBLTae/IiJ/1N/FqlJjB0gZCkfvSM7nxK+I/AjAyQCeMWLLXVXljB8Xka/H/d3uo5YNDi/EadBH7QCBcsBERH5I8mYARwDYC8BMBd2dAH4tIg+PpK8shnJ1APh0EtxliyaywXeHBUYDwCcAnA3gbFtpVJ9VRV41NGsT0CzYbDVSFnyvVXLbTwnJVfrdqitkKF8f60VULTEZiQGbnYBJyEvL9gCYpJ/1AlghIku871bQellZ6u8TZ1lZAIpI3bW11cF0k+b3UT+bhtxXV1Urb7mGcrHFSXe0Qp/XjkWToIUqqsOcl5EBnysLq2eGpfreLOQe+j0BbIHciz1HgbeGDuJyAItJ/gXAQgC/AXCfiKxw9x3KABlA/a0dBpBrInduros8tu/RoQLQAx1ITta+7YXcgbqe9nE6cid2L4AlJJ9D7r2/C8Dv9e/+oXA9AEeSfEnvm5n5WCQit5Rtv4hkpv0bAdgZwO4AZgOYbLj2CuS7Pw8DuFufU29lXkZMv7GrWI+ROpTk90g+bE7kLksrSd5F8t9N3F0yxDZNI7kpyV1IHk/yv0heoic7PqnBB6ebldxKP7ck+TmNGVw6xD4uIrmd3ucg7zy2oVBmjl9NPH2zoYjVo1h/QvLpks9dQvK3Op5Ty86L2Tt/d5NDtN1+/F+UaZXfwya5kZ7WfXdgY79uTuUpOrUndIjKfJL7lumoCS74kB6+8px3JppPXy4LPm/StiF5rgan+keh9pt+1r3zdPtILtc+3+PO6CD5tpLgqwcOie7Vv39uz/xt1AeSm5D8sTnk0D89vc/0o14AlrtIHuzu2wTwbQVf1bv5pgCOR37M55uMo7DuuTmKnKa+BZwZn9yeAH5O8qMickVJVr+7ihEYT7r19WWqc5atMl8RkVTB8il148zWj+tGJDpnsWDgSNOQI1kAfEdElg3ReZyYPkjBxUailuShAM5Fvqlv/Z4ZindR/HmhjvE1JE8Vke86/XHUdD43KQA+DOCz+pnTs5y33g6uD7bEvC/e3267pY48B+MCkr0ick0DAIrRUzIMbFNJ4P6lokL0WSnJNwP4FoB9TT+d35Ae2DJvgbnnufYsAHBtC8aOeECQADAaAe8IABepLppi8J5xElg0qfnbn7Oa6uznMO/IeaOlAyYemK5DvlXlGj4Bg7dOQgPjtrlSM3lZwNvttsnWBHAWyc2cK6VJ+xKEt3CK/m40ae8D8GsFXk3bW/E4tpg+ZF6f3FXT718pIq+26C9NvTELAT7Uh70BfF+BV8PgSJ5KYL5C97bj1aPzIgC+SvLAEvPSXvCpTL4TwDzr1jCcJzOd80FRNZ9lTVZ8qqLilCGKqpbdEDqYJwH4iVrHfUZshsYkNeLV7dHaPk4C8AqAy3XchtoHuzgrHhNYZZ/UPYPkbBW16yjwkgAnTc3iSDzuV7TP7ETtNABnqlrCkQ50rTqPvdGHfgjg782K6DEdWKGm+kIATyA/LmoJgFkAdgGwn+poWUBXcivTAfkokueKyIKRYvNur5fkCQC+Y4A1oegnGAgHqgG4H8ADAB5DHvhZAbA1gG0BPCQij7uV24oP3QNiaoAe8LdLRvLzOr5pYPHQLAIXW/eCur9mKaf0VSN4qktNdcAPiMj5I63/WXGRKdJvAXCHAmmC+q/uBPBL9WndLyK9gXtdSnI68iDDTzYRRdRtsiNUb8IIAc85qI/W9tRMu6QAEHUAlwG4GHmYVm87m6XP6ANwEoCnPbBVAbxoHOg0nPstaghmnnpgDRi3wP8I4Cx9XaljfTiA0wCsFQBg6nHf40heoiFqI7ZnXjVLy3W0l+SlAHYA8L/IQ5tuFZFawVab3a5ZCuBUklsCeE8B9xOzSndrUWwNdcL7PP2OATGYAHgSwMkicmODvv6NwwxjUlIAvxORZ0ru8IiCdaoBCgPjmQC4VTnX8+YeLwB4mORjyEPPJnnWtm/h76Ic8FaMYJh/UuAy+SmA/UXkQyIyTyOLXZaSiAjVs56aK9N9VqhulTVpuCjAN/KCSEeCpMBAoefCuV1EbnQlOgr6mul7w52QNfQZPSbDreL5IV1g7LbKuaxOGDLClgD4DxF5Xu+bmL5URORnAK725oWewZUpOA8fLYMDdiWLyOsi8qBJjRO3jdNk0J3oflLZvTTgRlBdZMZYburAi7h1E65AG0mOnLntS38RBxbJYTpWaQMDJgHwMxG5S4FWMwslNRz0EmOshAIkXJ/3IjnFxVeOBucbtP1kVj0NECuGCw66TIf6VNFt5t6ZqKtsrEgCmn1HBKPqgk9J9gB4uydiiwB4UyOg6GJapGI4ZHzY+dkKwMZlXFltBZ8DnU0K9sRPVnDVdfJeN+Z+aFUlxirrhERj32neCfm07vkbqXpi3SJ+2ysAXgJwn/tdgDG4bbmXVDJZtSOkk89Ango5sq6WRv4xNzEkZyCP+NhIX9c1OoJVyuva8GlNRJ0UrLyxAF0nBoe6cdlTRS4b+OkEwAsisrDEfd/QaBrfReO7barIc3HnjRr43IpXzjcJwIGqc+yloJs6RMD4in7mWWidMMlSIKI6gTZB43RL9976JM9VidNjfJa+pKsB2M3re9F8zhk1zufVwzsCwCeQZ5xXvVXCBtYsjdkeqUV9z4jDuSUWD5FXCPjIEN09gsbZdTNGBXzGKbs2gNORR3y4zfaa+b7dmE4CDY7UDnacz8VE5InozZR+t0MSEp9F1n1SQoJNGcm5rXrAW0+dkAdhcDRJNaSUN+hkrJPXvvmZ3sxAbPAZC1QMYlXndGj+ZpCcICL9I2GAJSZwcRKA7ynwUq+B1qFpLaQa8tDy1LByYCDKJdLwqILBofBlDCibdiABvc6P2PHVKXvNVh1/xFaW27T+CPK9VhemU7Sp7MBXVcW2JyDGq1EMt80VlpQEnWDVWL1mBmAj0GOk9faqAm8zAJ/GqvuFocY6M/w15Ht/DwB43nT+DbWKP4nciTzW7pTVhV5GHnXkQJOWFMVFczwRwLNoQ3ZdM4PjGPUl1TE4fNw2yrHpioLuPwH8IeSSIDlXLa+JERMjTm5r7Vuqr082alPLBrfOfb8yGWcAtRd8moL4Lgx2Nvps3IraG5BHTSz1s8CMqB6qLzBSMbDSEkABgOdLRsl0lDW1nYpJMbqajW4Vowe8DODTCryq5n3a5GnnnI6itj1Ux8AeuTTR4WaY/fV2icoRrWpQRe7EnOJ1RAIGRgXALzTaJXEJx5FGlPpU79rBE7Eh2tot/G6pJZMgzweY0EARtbrfrW2qUxKpkTk6ENibAnjQAK+Ry2UD81vpFvBVSviNXGdWxgpNo4dBff0zVg0iDdFGWu5jNNs2LEbkNpobPaR0imKktpJb5Hcjz5RLmgBhcwA7meJOrT10FA/SSZAXuUk9y9a+WgV2nVHIuehmspUImgGLzXFAQZ45d5cRvaEtsxR5PvTeKpnYIugqJVMEVhjMNPuuNBqsB5wvB4PLKNjIFQe+g6LYbUg1T5IUjdVkANuUiDpONHHrV00m0713HMmZanhUWgBdSnJj3XQIBdTSgK+vySIENO22qJ8J8nzU+7Fq5rzlhBV9fRvJt2rnbIKKY9XuCKTVrS6ym5QXkSfx2KoHIQOuCuAAB7DAOMIzMq5BHn0sBWPr4v12BPB5BW9qUh7EOxOt4iVIpSQnahjdPOSVCxqJ7ueQV7ZoNibrAthL+1m1bYGurJXIUyRtxrvvcnGdmwbg2yTX9xJU3FVTC20qGu8LNi2I06XgW6SX62NS0HcCOJrk3i71wI5jwOp9AXniT6P9djd3Hyd5JslpfrZdIBOPJDckeTzyWorXIPf5Hgpgj0BWoXvOM3qhYDE4CToBwGkk5/h4gQHIlQA+ijx2zAUWSMDPlyKPgr1Bo2bnqR8KyAspboM85/P9GAg4kBKTNh5cI6IppvORB+A2EpGZcoUrSH4XeTL+QyrKMs+H6nS/8wEcpWMcqpxlr1NVSl0O4F4Aj6tuvybyop6zkVch2x/58VxbmPmo6fdOJnmnnSPTz4zkbQD2aCB23c7MWwBcR/ICNZ4Waj/rttbbSd5Bev4Jj+7V1ut7Vuvn3auFGnu9GnF+8UO/jl9Gci99/qD0RX0927SJDerAnaHfr1hdxt2L5PUFbUq9Pl0+DCvR1a7bT+vlpQXttv13tJTkgyT/TPIcl/9s+6CvxzSojWjnxz73dZ2bB0g+qodl9wfGsc+MQ6Zt2jUwN7affd6JoAzUJ7T9fFmLjD5E8oOJrqxE2frPMFBWwo/5snXf3PsbIq8d8mZdSRPNZ84Ks85RW5kpAfAjAA+OEwva5bfejnz/O8HA4TX+WIoRoRnyEhbbI08O39wXZaZq1OXIYy4tZ4F3Xxf86545VedmB+TpkOtg4GiqzJtXB7K6qlgf09RNBvp5G4CbTD9DPkDrLaE+ey7yLd3ZLiOeWpPkYzp4E8wPbXZ85jk8/fJhoYx6X8y6/eNvAPg3EVlurLuuFr35i9QAfE51okkYXD9PAhPkQFjXMexroqKcpkyiB8WRyG7ukoI5skdV2SvFwKlOrqLEdLtrYvpZB3AG8jrPExEO44eHIxqGVEs8xfZ51dduNhZuP4pDdASrlkzzLbVM75HogD0E4CgROcWFZ48X9405umEBgBNVz6pi8DllzcZRGoAbWgn/Q8oFHUjqDXyHoTny9+5rGKh85bjmFcijl17258j08y7kcZuuAFO/WURo1s8kcMNnALwXeUmxGgaOEq0HnKShy4YBuUiZCar0fhbAfiLyc1v3pZEoMxPX6GoG3nQIVzsAWBGR3wI4EnmlqKon1lKsWnAy89SUIsMm0TK8x6lbxZ1PJ+YeRfnIRfPkDibsQ148810AjhGRR4ukksHLRQBOQF5xa6JpSxrgtgy0cZDibJXLt5O8VqvKt0JLSN6k1eg3CD2jQHl3Cvb5JZ9zZgODo0ry1pL3+UXbfC8DfZipxdXvDSj6IfpNsxPT7fskdyd5hY51q/Q0yR+Q3F91vFLz5PVzW5LnafH2MnRqw84ZP88e6vvZB3kSszsYBUZfWYE8hP4lAPcg3xD/A4AF9rwHlIgRM9l0h6jbYqVRA3zOOAXATSJya6jIpE7kCeqiqCNcUNEp3PeIyJXtBKA5I2Oauql2UaNiU+R5sU50vo68hsqvAFzWrGaMiy4y998ReU2X/bWvM3RsJnrz1If8bOIXlCvPR16d60k7ZuqvbaWfmyEvO7w9Bg6DnqYccbn281kA50jZm+r/U5Gn8q2tIHQyfIl64PsBvC4irwUAwNXxREgfJOb9yQqMv1mY/rgNdeL1/x71462r/ru1jbHo5uk55AfnLPPbiRYDSEOMRTMi19B+Ot20V5/N/wfC+YstI5fjPgAAAABJRU5ErkJggg=="


def logo_html():
    """Embedded logo by default; if airtel_logo.png/.svg/... exists it is used instead (shown on a white chip)."""
    p = find_logo()
    if p:
        data = base64.b64encode(p.read_bytes()).decode()
        return f'<div class="logo chip"><img src="data:{LOGO_TYPES[p.suffix.lower()]};base64,{data}" alt="Airtel"></div>'
    return f'<div class="logo"><img src="data:image/png;base64,{EMBEDDED_LOGO}" alt="Airtel"></div>'


ICONS = {
    "people": '<circle cx="8" cy="8" r="3"/><circle cx="16.5" cy="8.5" r="2.6"/><path d="M2.5 20c0-4 2.5-6 5.5-6s5.5 2 5.5 6M13.5 14.6c3.5-.8 7.5.8 8 5.4"/>',
    "flask": '<path d="M9 3h6M10 3v6L4.8 18a2 2 0 0 0 1.8 3h10.8a2 2 0 0 0 1.8-3L14 9V3M7.5 15h9"/>',
    "trend": '<path d="M3 17l6-6 4 4 8-8M15 7h6v6"/>',
    "ticket": '<path d="M3 7h18v3a2 2 0 0 0 0 4v3H3v-3a2 2 0 0 0 0-4zM14 7v10" stroke-dasharray="2 2"/>',
}
FUNNEL_ICON = '<svg viewBox="0 0 24 24"><path d="M3 4h18l-7 8v6l-4 2v-8z"/></svg>'


def card(icon, title, value, sub, delay, fixed=False):
    tag = '<span class="pill">fixed · ignores filters</span>' if fixed else ""
    return (f'<div class="card" style="--d:{delay}s"><div class="ico"><svg viewBox="0 0 24 24">{ICONS[icon]}</svg>'
            f'</div><div><div class="ct">{title}{tag}</div><div class="cv">{value}</div>'
            f'<div class="cs">{sub}</div></div></div>')


def panel(title, sub=""):
    st.markdown(f'<div class="ph"><div class="pt">{title}</div><div class="ps">{sub}</div></div>', unsafe_allow_html=True)


# ------------------------------------------------------------------ chart helpers
def rgba(hex_color, a):
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


def lab_text(n, rate, sep="<br>"):
    return f"n={n:,}{sep}rate hidden (&lt;{MIN_GROUP})" if pd.isna(rate) else f"{rate:.1f}%{sep}n={n:,}"


def style(fig, h, right=12, top_m=8, legend=False):
    axis = dict(tickfont=dict(color=MUTED, size=10), title_font=dict(color=MUTED, size=10),
                linecolor="#e3b9b9", zeroline=False, automargin=True)
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    fig.update_layout(height=h, margin=dict(l=8, r=right, t=top_m, b=6), showlegend=legend,
                      legend=dict(orientation="h", y=-0.02, x=.5, xanchor="center", font=dict(size=10)),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(size=10, color=INK), transition=dict(duration=600, easing="cubic-in-out"))
    return fig


def empty_chart(h, msg="No subscribers match these filters"):
    return style(go.Figure(), h).update_layout(
        annotations=[dict(text=msg, showarrow=False, font=dict(size=12, color=MUTED))],
        xaxis=dict(visible=False), yaxis=dict(visible=False))


def ref_line(fig, horizontal_axis="y"):
    note = dict(line_dash="dash", line_color=RED, line_width=1.5, layer="above", annotation_text=f"{NATIONAL_REF:g}%",
                annotation_font=dict(color=RED, size=10), annotation_bgcolor="#fff", annotation_bordercolor=RED)
    if horizontal_axis == "y":
        fig.add_hline(y=NATIONAL_REF, annotation_position="right", **note)
    else:
        fig.add_vline(x=NATIONAL_REF, annotation_position="top", **note)


def rate_colors(df):
    return [GREY if pd.isna(r) else (DEEP if r > NATIONAL_REF else SOFT) for r in df["rate"]]


def lollipop(df, h, horizontal=False):
    if df.empty:
        return empty_chart(h)
    rates, nmax = df["rate"].fillna(0).tolist(), max(df["n"].max(), 1)
    sizes = [12 + 18 * n / nmax for n in df["n"]]
    sep = " · " if horizontal else "<br>"
    text = [lab_text(n, r, sep) for n, r in zip(df["n"], df["rate"])]
    sa, sb = [], []
    for c, v in zip(df["lab"], rates):
        sa += [c, c, None]
        sb += [0, v, None]
    marker = dict(size=sizes, color=rate_colors(df), line=dict(color="#fff", width=1.5))
    top_rate = max(rates + [NATIONAL_REF])
    fig = go.Figure()
    if horizontal:
        fig.add_scatter(x=sb, y=sa, mode="lines", line=dict(color="#efc6c6", width=3), hoverinfo="skip")
        fig.add_scatter(x=rates, y=df["lab"], mode="markers+text", text=text, textposition="middle right", marker=marker,
                        hovertemplate="%{y}: %{x:.1f}%<extra></extra>")
        ref_line(fig, "x")
        fig.update_xaxes(range=[0, top_rate * 1.7], ticksuffix="%", gridcolor="#fbe3e3")
        fig.update_yaxes(autorange="reversed")
        return style(fig, h, top_m=24)
    fig.add_scatter(x=sa, y=sb, mode="lines", line=dict(color="#efc6c6", width=3), hoverinfo="skip")
    fig.add_scatter(x=df["lab"], y=rates, mode="markers+text", text=text, textposition="top center", marker=marker,
                    hovertemplate="%{x}: %{y:.1f}%<extra></extra>")
    ref_line(fig)
    fig.update_yaxes(range=[0, top_rate * 1.5], ticksuffix="%", gridcolor="#fbe3e3")
    return style(fig, h, right=44)


def area(df, h):
    if df.empty:
        return empty_chart(h)
    rates = df["rate"].fillna(0).tolist()
    fig = go.Figure(go.Scatter(
        x=df["lab"], y=rates, mode="lines+markers+text", fill="tozeroy", textposition="top center",
        text=[lab_text(n, r) for n, r in zip(df["n"], df["rate"])], line=dict(color=RED, width=3, shape="spline", smoothing=.6),
        fillcolor=rgba(RED, .13), marker=dict(size=9, color="#fff", line=dict(color=RED, width=2.5)),
        hovertemplate="%{x}: %{y:.1f}%<extra></extra>"))
    ref_line(fig)
    fig.update_yaxes(range=[0, max(rates + [NATIONAL_REF]) * 1.55], ticksuffix="%", gridcolor="#fbe3e3")
    fig.update_xaxes(range=[-.4, len(df) - .6])
    return style(fig, h, right=44)


def radar_fig(cats, series, h):
    """series = [(name, values, colour, hover_texts)]; dashed ring = national reference."""
    fig = go.Figure()
    for name, vals, color, texts in series:
        fig.add_scatterpolar(r=vals + vals[:1], theta=cats + cats[:1], name=name, fill="toself", fillcolor=rgba(color, .18),
                             line=dict(color=color, width=2.5), hovertext=texts + texts[:1], hoverinfo="text+name")
    fig.add_scatterpolar(r=[NATIONAL_REF] * (len(cats) + 1), theta=cats + cats[:1], name=f"Reference {NATIONAL_REF:g}%",
                         mode="lines", line=dict(color=GREY, dash="dash", width=1.5), hoverinfo="name")
    rmax = max([v for _, vals, _, _ in series for v in vals] + [NATIONAL_REF]) * 1.2
    fig.update_layout(polar=dict(bgcolor="rgba(0,0,0,0)", radialaxis=dict(range=[0, rmax], ticksuffix="%", tickfont=dict(size=9),
                                                                          gridcolor="#f3d3d3", linecolor="#f3d3d3"),
                                 angularaxis=dict(tickfont=dict(size=11), gridcolor="#f3d3d3")))
    return style(fig, h, right=20, top_m=18, legend=True)


def radar(df, h):
    if df.empty:
        return empty_chart(h)
    texts = [lab_text(n, r, " · ") for n, r in zip(df["n"], df["rate"])]
    return radar_fig(df["lab"].tolist(), [("Churn rate", df["rate"].fillna(0).tolist(), RED, texts)], h)


def radar_by_group(df, h):
    """Churn rate across tenure groups, one polygon per connection type."""
    if df.empty:
        return empty_chart(h)
    cats = [c for c in TENURE_ORDER if c in set(df["rw"])]
    series = []
    for i, name in enumerate(sorted(df["cl"].unique())):
        d = df[df["cl"] == name].set_index("rw").reindex(cats)
        series.append((name, d["rate"].fillna(0).tolist(), CONN_COLORS.get(name, RAMP[i * 3]),
                       [lab_text(0 if pd.isna(n) else int(n), r, " · ") for n, r in zip(d["n"], d["rate"])]))
    return radar_fig(cats, series, h)


def waterfall_leavers(df, h, total):
    if df.empty:
        return empty_chart(h)
    fig = go.Figure(go.Waterfall(
        x=df["lab"].tolist() + ["Total leavers"], measure=["relative"] * len(df) + ["total"],
        y=[whole(v) for v in df["leavers"]] + [None],
        text=[f"{whole(v):,}<br>{'–' if pd.isna(r) else format(r, '.1f') + '%'}" for v, r in zip(df["leavers"], df["rate"])]
             + [f"{total:,}"],
        textposition="outside", cliponaxis=False, increasing=dict(marker=dict(color=RED)),
        totals=dict(marker=dict(color=DEEP)), connector=dict(line=dict(color="#e3b9b9", width=1)),
        hovertemplate="%{x}: %{y:,} leavers<extra></extra>"))
    fig.update_yaxes(title="Leavers (count)", gridcolor="#fbe3e3", rangemode="tozero")
    fig.update_xaxes(range=[-.6, len(df) + .6])
    return style(fig, h, top_m=22)


def heat_strip(df, h):
    if df.empty:
        return empty_chart(h)
    fig = go.Figure(go.Heatmap(
        z=[df["rate"].tolist()], x=df["lab"], y=["Churn"], showscale=False, xgap=4, colorscale=REDS, zmin=0, zmax=45,
        text=[[lab_text(n, r) for n, r in zip(df["n"], df["rate"])]], texttemplate="%{text}", textfont=dict(size=13),
        hovertemplate="%{x}: %{z:.1f}%<extra></extra>"))
    fig.update_yaxes(visible=False)
    return style(fig, h)


def heatmap(df, h, row_order=None, col_order=None, two_line=True):
    """Generic rate heat map; df has rw (row), cl (column), n, rate."""
    if df.empty:
        return empty_chart(h)
    rows = row_order or df["rw"].drop_duplicates().tolist()
    cols = [c for c in (col_order or df["cl"].drop_duplicates().tolist()) if c in set(df["cl"])]
    rate = df.pivot(index="rw", columns="cl", values="rate").reindex(index=rows, columns=cols)
    n = df.pivot(index="rw", columns="cl", values="n").reindex(index=rows, columns=cols)
    sep = "<br>" if two_line else " "
    text = [[("–" if pd.isna(n.iloc[i, j]) else
              f"n={int(n.iloc[i, j]):,}" if pd.isna(rate.iloc[i, j]) else
              f"{rate.iloc[i, j]:.1f}%{sep}n={int(n.iloc[i, j]):,}" if two_line else f"{rate.iloc[i, j]:.1f}%")
             for j in range(len(cols))] for i in range(len(rows))]
    fig = go.Figure(go.Heatmap(
        z=rate.values, x=cols, y=rows, text=text, texttemplate="%{text}", colorscale=REDS, zmin=0, zmax=45,
        showscale=False, xgap=3, ygap=3, customdata=n.values, textfont=dict(size=10 if two_line else 9),
        hovertemplate="%{y} · %{x}<br>%{z:.1f}% churn · n=%{customdata:,}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    return style(fig, h, top_m=4)


def bubble(df, h):
    if df.empty:
        return empty_chart(h)
    rates, lmax = df["rate"].fillna(0).tolist(), max(df["leavers"].max(), 1)
    fig = go.Figure(go.Scatter(
        x=df["n"], y=rates, mode="markers+text", text=df["lab"], textposition=["top center" if i % 2 == 0 else "bottom center" for i in range(len(df))],
        textfont=dict(size=10), cliponaxis=False,
        marker=dict(size=[12 + 34 * v / lmax for v in df["leavers"]], color=rates, colorscale=BUBBLE, cmin=0, cmax=45,
                    line=dict(color="#fff", width=1.5), opacity=.92),
        customdata=df["leavers"], hovertemplate="%{text}<br>%{y:.1f}% churn · n=%{x:,} · %{customdata:,} leavers<extra></extra>"))
    ref_line(fig)
    fig.update_yaxes(title="Churn rate", ticksuffix="%", gridcolor="#fbe3e3", range=[0, max(rates + [NATIONAL_REF]) * 1.35])
    fig.update_xaxes(title="Subscribers (count) · bubble size = leavers", gridcolor="#fbe3e3",
                     range=[df["n"].min() * .9, df["n"].max() * 1.08])
    return style(fig, h, right=44)


def treemap(df, h):
    if df.empty:
        return empty_chart(h)
    rates = df["rate"].fillna(0).tolist()
    fig = go.Figure(go.Treemap(
        labels=df["lab"], parents=[""] * len(df), values=df["n"], text=[lab_text(n, r, " · ") for n, r in zip(df["n"], df["rate"])],
        textinfo="label+text", marker=dict(colors=rates, colorscale=BUBBLE, cmin=0, cmax=45, showscale=False,
                                           line=dict(color="#fff", width=2)),
        hovertemplate="%{label}<br>%{text}<extra></extra>"))
    return style(fig, h, right=6, top_m=2)


def funnel(df, h):
    if df.empty:
        return empty_chart(h)
    d = df.sort_values("leavers", ascending=False)
    fig = go.Figure(go.Funnel(
        y=d["lab"], x=d["leavers"], textinfo="text", textposition="inside", textfont=dict(color="#fff", size=10),
        text=[f"{whole(v):,} leavers · " + ("rate hidden" if pd.isna(r) else f"{r:.1f}%") + f" · n={n:,}"
              for v, r, n in zip(d["leavers"], d["rate"], d["n"])],
        marker=dict(color=RAMP[:len(d)]), hovertemplate="%{y}: %{x:,} leavers<extra></extra>"))
    return style(fig, h, right=6, top_m=2)


def bullet(df, h):
    if df.empty:
        return empty_chart(h)
    rates = df["rate"].fillna(0).tolist()
    colors = [CONN_COLORS.get(c, DEEP) if pd.notna(r) else GREY for c, r in zip(df["lab"], df["rate"])]
    text = [f"{lab_text(n, r, ' · ')} · {whole(lv):,} leavers" if pd.notna(r) else lab_text(n, r, " · ")
            for n, r, lv in zip(df["n"], df["rate"], df["leavers"])]
    axis_max = max(40, max(rates + [NATIONAL_REF]) * 1.5)
    fig = go.Figure()
    fig.add_bar(y=df["lab"], x=[axis_max] * len(df), orientation="h", width=.7, marker_color="#fbeaea", hoverinfo="skip")
    fig.add_bar(y=df["lab"], x=rates, orientation="h", width=.32, marker_color=colors, text=text, textposition="outside",
                cliponaxis=False, hovertemplate="%{y}: %{x:.1f}%<extra></extra>")
    ref_line(fig, "x")
    fig.update_layout(barmode="overlay")
    fig.update_xaxes(range=[0, axis_max], ticksuffix="%", showgrid=False)
    fig.update_yaxes(autorange="reversed")
    return style(fig, h, top_m=24)


def dumbbell(circle, rate, n, h):
    if pd.isna(rate):
        return empty_chart(h, "Too few subscribers to show a rate")
    fig = go.Figure()
    fig.add_scatter(x=[NATIONAL_REF, rate], y=[0, 0], mode="lines", line=dict(color="#efc6c6", width=7), hoverinfo="skip")
    fig.add_scatter(x=[NATIONAL_REF], y=[0], mode="markers+text", text=[f"National reference<br>{NATIONAL_REF:g}%"],
                    textposition="bottom center", marker=dict(size=22, color=GREY, line=dict(color="#fff", width=2)), hoverinfo="skip")
    fig.add_scatter(x=[rate], y=[0], mode="markers+text", text=[f"{circle}<br>{rate:.1f}% · n={n:,}"],
                    textposition="top center", marker=dict(size=26, color=RED, line=dict(color="#fff", width=2)), hoverinfo="skip")
    fig.update_xaxes(range=[0, max(rate, NATIONAL_REF) * 1.5], ticksuffix="%", gridcolor="#fbe3e3")
    fig.update_yaxes(visible=False, range=[-1, 1])
    return style(fig, h)


def waterfall_compare(circle, rate, n, gap, h):
    if pd.isna(rate) or pd.isna(gap):
        return empty_chart(h, "Too few subscribers to show a rate")
    fig = go.Figure(go.Waterfall(
        x=["National reference", "Difference", circle], measure=["absolute", "relative", "total"], y=[NATIONAL_REF, gap, None],
        text=[f"{NATIONAL_REF:g}%", f"{gap:+.1f} pp", f"{rate:.1f}% · n={n:,}"], textposition="outside", cliponaxis=False,
        increasing=dict(marker=dict(color=RED)), decreasing=dict(marker=dict(color=GREEN)),
        totals=dict(marker=dict(color=DEEP)), connector=dict(line=dict(color="#e3b9b9", width=1)), hoverinfo="x+text"))
    fig.update_yaxes(range=[0, max(rate, NATIONAL_REF) * 1.45], ticksuffix="%", gridcolor="#fbe3e3")
    return style(fig, h, top_m=20)


def month_chart(df, kind, h):
    if df.empty:
        return empty_chart(h, "No leave dates available for these filters")
    x, y = df["month"], df["leavers"]
    text = y.map("{:,}".format)
    fig = go.Figure()
    if kind == "Lollipop":
        sa, sb = [], []
        for c, v in zip(x, y):
            sa += [c, c, None]
            sb += [0, v, None]
        fig.add_scatter(x=sa, y=sb, mode="lines", line=dict(color="#efc6c6", width=3), hoverinfo="skip")
        fig.add_scatter(x=x, y=y, mode="markers+text", text=text, textposition="top center",
                        marker=dict(size=11, color=DEEP, line=dict(color="#fff", width=1.5)))
    else:
        fig.add_scatter(x=x, y=y, mode="lines+markers+text", text=text, textposition="top center", textfont=dict(size=9),
                        fill="tozeroy" if kind == "Area" else None, fillcolor=rgba(RED, .12),
                        line=dict(color=RED, width=2.8, shape="hv" if kind == "Step" else "spline", smoothing=.5),
                        marker=dict(size=7, color="#fff", line=dict(color=RED, width=2)),
                        hovertemplate="%{x}: %{y:,} leavers<extra></extra>")
    fig.update_xaxes(type="category", tickangle=-45)
    fig.update_yaxes(title="Leavers (count)", gridcolor="#fbe3e3", rangemode="tozero", range=[0, y.max() * 1.2])
    return style(fig, h, top_m=14)


def show(fig):
    st.plotly_chart(fig, width="stretch", theme=None, config={"displayModeBar": False})


# ------------------------------------------------------------------ state, banner, sidebar
if DB_PATH is None:
    st.error(r"Could not find the Airtel database at C:\Users\MUEENUDHEEN\OneDrive\Desktop\airtel-telecom-analytics\data\airtel_dashboard_slim.db. "
             "Check that the file exists, or set the AIRTEL_DB environment variable.")
    st.stop()
try:
    circles = q("SELECT circle_name AS v FROM circles ORDER BY v")["v"].tolist()
    regions = q("SELECT DISTINCT region AS v FROM circles ORDER BY v")["v"].tolist()
    conns = q("SELECT DISTINCT connection_type AS v FROM customers ORDER BY v")["v"].tolist()
    as_of = q(f"SELECT {AS_OF} AS d")["d"][0]
except Exception as e:
    st.error(f"The database does not match the mapping at the top of this file: {e}")
    st.stop()
st.session_state.setdefault("circle", circles[0])
st.session_state.setdefault("page", "Overview")
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)


def go_page(name):
    st.session_state["page"] = name


def reset_all():
    st.session_state.update(DEFAULTS)
    st.session_state["circle"] = circles[0]


with st.container(key="banner"):
    c_head, c_nav = st.columns([7, 2.4])
    with c_nav:
        n1, n2 = st.columns(2)
        n1.button("Overview", key="nav_overview", on_click=go_page, args=("Overview",))
        n2.button("Circle Analysis", key="nav_circle", on_click=go_page, args=("Circle Analysis",))
    page = st.session_state["page"]
    circle = st.session_state["circle"]
    heading = ("Airtel Telecom Analytics – Customer Retention" if page == "Overview"
               else f"Airtel Telecom Analytics – Circle Analysis: {circle}")
    with c_head:
        st.markdown(f'<div class="hdr">{logo_html()}<div class="div"></div><div>'
                    f'<div class="ttl">{heading}</div><div class="rdr">This dashboard helps Head of Retention '
                    'prioritise retention efforts and understand churn patterns.</div></div></div>', unsafe_allow_html=True)

with st.sidebar:
    st.markdown(f'<div class="sb-h">{FUNNEL_ICON}Filters</div><div class="sb-sub">Apply to every card and chart</div>',
                unsafe_allow_html=True)
    conn = st.selectbox("Connection type", ["All"] + conns, key="f_conn")
    region = st.selectbox("Region", ["All"] + regions, key="f_region", disabled=(page != "Overview"),
                          help="On Circle Analysis, the circle selector replaces this filter.")
    st.markdown("---")
    st.markdown('<div class="sb-h">Chart styles</div><div class="sb-sub">Pick how each chart is drawn</div>', unsafe_allow_html=True)
    for slot in (OVERVIEW_SLOTS if page == "Overview" else DETAIL_SLOTS):
        st.selectbox(STYLE_LABELS[slot], STYLES[slot], key=f"s_{slot}")
    st.selectbox("Screen fit", list(SCREENS), key="s_screen", help="Sets chart heights so the page fills your screen without scrolling.")
    st.markdown("---")
    st.button("↺  Reset filters and styles", on_click=reset_all)
    st.markdown(f'<div class="sb-sub">Data through {as_of}</div>', unsafe_allow_html=True)


H_SMALL, H_HERO = SCREENS[st.session_state["s_screen"]]


def pick(slot):
    return st.session_state[f"s_{slot}"]


def tenure_chart(kind, df, total, h):
    return {"Area": lambda: area(df, h), "Radar": lambda: radar(df, h), "Lollipop": lambda: lollipop(df, h),
            "Waterfall": lambda: waterfall_leavers(df, h, total), "Heat strip": lambda: heat_strip(df, h)}[kind]()


def conn_chart(kind, df, heat, h):
    return {"Heatmap": lambda: heatmap(heat, h, col_order=sorted(heat["cl"].unique()) if len(heat) else None,
                                       row_order=[r for r in TENURE_ORDER if r in set(heat["rw"])]),
            "Radar": lambda: radar_by_group(heat, h), "Bullet": lambda: bullet(df, h),
            "Lollipop": lambda: lollipop(df, h)}[kind]()


# ================================================================== OVERVIEW
if page == "Overview":
    w, p = where(conn, region)
    base_n = whole(q(f"SELECT COUNT(*) AS n{FROM}{where()[0]}")["n"][0])
    kpi = q(f"SELECT {STATS}{FROM}{w}", p).iloc[0]
    k_n, k_left = whole(kpi.n), whole(kpi.leavers)

    k1, k2, k3 = st.columns(3)
    k1.markdown(card("people", "Subscriber base", f"{base_n:,}", "All subscribers in scope, every circle and connection type.",
                     0, fixed=True), unsafe_allow_html=True)
    k2.markdown(card("flask", "T4 test result", f"p = {T4_P_VALUE} <small style='font-size:.7rem'>(one-tailed)</small>",
                     "H1: churn is higher. Significant at 5%: association, not proof of cause.", .12, fixed=True),
                unsafe_allow_html=True)
    k3.markdown(card("trend", "Filtered churn", f"{100 * k_left / k_n:.2f}%" if k_n else "n/a",
                     f"{k_left:,} of {k_n:,} subscribers in the filters left (leaver = churned; full period).",
                     .24), unsafe_allow_html=True)

    t_sql = f"SELECT {TENURE_GRP} AS lab, {STATS}{FROM}{w} GROUP BY lab ORDER BY MIN({TENURE_MONTHS})"
    c_sql = f"SELECT {CIRCLE_COL} AS lab, {STATS}{FROM}{w} GROUP BY lab ORDER BY rate IS NULL, rate DESC LIMIT {TOP_N}"
    p_sql = f"SELECT {CONN_COL} AS lab, {STATS}{FROM}{w} GROUP BY lab ORDER BY rate IS NULL, rate DESC"
    hz_sql = f"SELECT {TENURE_GRP} AS rw, {CONN_COL} AS cl, {STATS}{FROM}{w} GROUP BY rw, cl"
    w_in = f"{w}{' AND ' if w else ' WHERE '}{CIRCLE_COL} IN (SELECT lab FROM ({c_sql}))"
    hc_sql = f"SELECT {CIRCLE_COL} AS rw, {TENURE_GRP} AS cl, {STATS}{FROM}{w_in} GROUP BY rw, cl"
    tenure, circ, pay, heat_z = q(t_sql, p), q(c_sql, p), q(p_sql, p), q(hz_sql, p)
    tt, tc, tp, tl = top(t_sql, p), top(c_sql, p), top(p_sql, p), top(p_sql, p, highest=False)

    left, right = st.columns([5, 7])
    with left:
        panel(f"Churn by tenure – highest for {tt[0]} subscribers ({tt[1]:.1f}%)" if tt else "Churn by tenure",
              f"Churn rate by tenure group; dashed line = {NATIONAL_REF:g}% national reference (not a target)")
        show(tenure_chart(pick("tenure"), tenure, k_left, H_HERO))
    with right:
        panel(f"Churn by circle – {tc[0]} is highest ({tc[1]:.1f}%)" if tc else "Churn by circle",
              f"Top {TOP_N} circles by churn rate; groups under {MIN_GROUP} show counts only")
        kind = pick("circle")
        if kind == "Heatmap":
            fig = heatmap(q(hc_sql, p + p), H_SMALL, row_order=circ["lab"].tolist(), col_order=TENURE_ORDER, two_line=False)
        else:
            fig = {"Bubble": bubble, "Treemap": treemap, "Funnel": funnel,
                   "Lollipop": lambda d, h: lollipop(d, h, horizontal=True)}[kind](circ, H_SMALL)
        show(fig)
        r1, r2 = st.columns(2)
        with r1:
            panel(f"Plan type – {tp[0]} churns more ({tp[1]:.1f}%)" if tp else "Prepaid vs postpaid churn",
                  "Churn rate and subscriber counts by connection type")
            show(conn_chart(pick("conn"), pay, heat_z, H_SMALL))
        with r2:
            gap = q(f"SELECT ROUND(MAX(rate) - MIN(rate), 1) AS g FROM ({p_sql}) WHERE rate IS NOT NULL", p)["g"][0]
            ab = q(f"SELECT SUM(CASE WHEN rate > {NATIONAL_REF} THEN 1 ELSE 0 END) AS a, COUNT(rate) AS t FROM ({c_sql})", p).iloc[0]
            lines = []
            if tc:
                lines.append(f"<b></b><span><strong>{tc[0]}</strong> has the highest circle churn at {tc[1]:.1f}%.</span>")
            if tt:
                lines.append(f"<b></b><span>Churn is highest for <strong>{tt[0]}</strong> tenure ({tt[1]:.1f}%).</span>")
            if tp and tl and tp[0] != tl[0] and pd.notna(gap):
                lines.append(f"<b></b><span><strong>{tp[0]}</strong> churns {gap:.1f} pp more than {tl[0]}.</span>")
            if ab.t:
                lines.append(f"<b></b><span>{whole(ab.a)} of the top {whole(ab.t)} circles are above the {NATIONAL_REF:g}% reference. "
                             "Association, not cause.</span>")
            panel("Key takeaways", "Generated from the current filters")
            st.markdown(f'<div class="tk" style="height:{H_SMALL}px">' + "".join(f"<div>{x}</div>" for x in lines) + "</div>", unsafe_allow_html=True)

# ================================================================== CIRCLE ANALYSIS
else:
    w, p = where(conn, circle=circle)
    s = q(f"SELECT {STATS}, ROUND(100.0 * {LEFT} / COUNT(*) - ?, 2) AS gap{FROM}{w}", (NATIONAL_REF,) + p).iloc[0]
    s_n, s_left = whole(s.n), whole(s.leavers)
    open_case = f"CASE WHEN {OPEN_SQL} THEN 1 ELSE 0 END"
    tk = q(f"SELECT SUM({open_case}) AS open_n, ROUND(100.0 * SUM({open_case}) / COUNT(*), 1) AS share{FROM}{w}", p).iloc[0]

    k1, k2, k3 = st.columns(3)
    with k1:
        with st.container(key="sel"):
            st.selectbox("Select circle", circles, key="circle")
    note = "" if conn == "All" else f" ({conn} only)"
    side = "above" if pd.notna(s.gap) and s.gap > 0 else "below"
    k2.markdown(card("trend", f"{circle}: subscribers and leavers{note}", f"{s_n:,} · {s_left:,}",
                     (f"Churn {100 * s_left / s_n:.1f}%, {abs(s.gap):.1f} pp {side} the {NATIONAL_REF:g}% national reference "
                      "(a reference, not a target).") if s_n and pd.notna(s.gap) else "Too few subscribers to show a rate.", 0),
                unsafe_allow_html=True)
    k3.markdown(card("ticket", f"{circle}: open tickets{note}", f"{whole(tk.open_n):,} · {tk.share if pd.notna(tk.share) else 0}%",
                     f"Subscribers with at least one unresolved complaint, as a share of {s_n:,}.", .12), unsafe_allow_html=True)

    m_where = where(conn, circle=circle, extra=(LEAVER_SQL, f"{LEAVE_DATE_COL} IS NOT NULL"))[0]
    m_sql = (f"SELECT strftime('%Y-%m', {LEAVE_DATE_COL}) AS month, COUNT(*) AS leavers{FROM}{m_where} "
             f"GROUP BY month ORDER BY month")
    t_sql = f"SELECT {TENURE_GRP} AS lab, {STATS}{FROM}{w} GROUP BY lab ORDER BY MIN({TENURE_MONTHS})"
    p_sql = f"SELECT {CONN_COL} AS lab, {STATS}{FROM}{w} GROUP BY lab ORDER BY rate IS NULL, rate DESC"
    hz_sql = f"SELECT {TENURE_GRP} AS rw, {CONN_COL} AS cl, {STATS}{FROM}{w} GROUP BY rw, cl"
    month, tenure, pay, heat_z = q(m_sql, p), q(t_sql, p), q(p_sql, p), q(hz_sql, p)
    peak = q(f"SELECT month, leavers FROM ({m_sql}) ORDER BY leavers DESC LIMIT 1", p)
    tt, tp = top(t_sql, p), top(p_sql, p)

    left, right = st.columns([5, 7])
    with left:
        if month.empty:
            panel(f"{circle}: monthly leavers", "No leave dates available for these filters")
        else:
            panel(f"{circle}: most leavers left in {peak['month'][0]} ({peak['leavers'][0]:,})",
                  f"Monthly leaver counts, {month['month'].iloc[0]} to {month['month'].iloc[-1]}")
        show(month_chart(month, pick("d_month"), H_HERO))
    with right:
        panel(f"{circle} is {abs(s.gap):.1f} pp {side} the national reference" if s_n and pd.notna(s.gap)
              else f"{circle}: too few subscribers to rate", f"Reference {NATIONAL_REF:g}% is supplied; it is not a target")
        ck = pick("d_compare")
        if ck == "Waterfall":
            fig = waterfall_compare(circle, s.rate, s_n, s.gap, H_SMALL)
        elif ck == "Dumbbell":
            fig = dumbbell(circle, s.rate, s_n, H_SMALL)
        else:
            fig = bullet(pd.DataFrame({"lab": [circle], "n": [s_n], "leavers": [s_left], "rate": [s.rate]}), H_SMALL)
        show(fig)
        r1, r2 = st.columns(2)
        with r1:
            panel(f"Tenure in {circle} – highest at {tt[0]} ({tt[1]:.1f}%)" if tt else f"Tenure in {circle}",
                  "Churn rate by tenure group, with counts")
            show(tenure_chart(pick("d_tenure"), tenure, s_left, H_SMALL))
        with r2:
            panel(f"{circle}: {tp[0]} churns more ({tp[1]:.1f}%)" if tp else f"{circle}: prepaid vs postpaid",
                  "Churn rate and counts by connection type")
            show(conn_chart(pick("d_conn"), pay, heat_z, H_SMALL))