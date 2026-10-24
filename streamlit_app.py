import hashlib
import json
import os
import re
from datetime import date
from html import escape

import requests
import streamlit as st

def configured_backend_url():
    if os.getenv("BACKEND_URL"):
        return os.environ["BACKEND_URL"].rstrip("/")
    try:
        return str(st.secrets["BACKEND_URL"]).rstrip("/")
    except Exception:
        return "http://127.0.0.1:8000"


BACKEND_URL = configured_backend_url()
TIMEOUT = 60
FILE_TYPES = ["jpg", "jpeg", "png", "pdf", "mp3", "mp4", "txt"]
NAV = {
    "citizen": ["Dashboard", "Report incident", "My complaints", "Notifications", "Appeals", "Legal help"],
    "official": ["Dashboard", "Assigned cases", "Notifications"],
    "supervisor": ["Dashboard", "Cases", "Oversight", "Appeals", "Notifications"],
    "legal_aid": ["Shared cases", "Legal directory"],
    "admin": ["Dashboard", "Administration", "Oversight", "All cases", "Notifications"],
}

st.set_page_config(page_title="CivicShield", page_icon="🛡️", layout="wide")


class APIError(Exception):
    pass


def api(method, path, *, body=None, files=None, authenticated=True):
    headers = {}
    if authenticated and st.session_state.get("token"):
        headers["Authorization"] = f"Bearer {st.session_state.token}"
    try:
        response = requests.request(method, f"{BACKEND_URL}/api{path}", json=body if files is None else None,
                                    files=files, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise APIError(f"Cannot reach the CivicShield API at {BACKEND_URL}: {exc}") from exc
    try:
        result = response.json()
    except ValueError:
        result = {"detail": response.text[:500]}
    if not response.ok:
        raise APIError(str(result.get("detail", result) if isinstance(result, dict) else result))
    return result


def download(evidence_id):
    headers = {"Authorization": f"Bearer {st.session_state.token}"}
    try:
        response = requests.get(f"{BACKEND_URL}/api/evidence/{evidence_id}/download", headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise APIError(f"Cannot reach the CivicShield API: {exc}") from exc
    if not response.ok:
        raise APIError(str(response.json().get("detail", "Download failed")))
    return response.content


def toast(message, kind="success"):
    st.session_state.flash = (message, kind)


def show_toast():
    message = st.session_state.pop("flash", None)
    if message:
        getattr(st, message[1], st.info)(message[0])


def render_table(rows, empty_message="No records to display."):
    """Render a styled HTML table instead of Streamlit's canvas dataframe grid."""
    if rows is None:
        rows = []
    if isinstance(rows, dict):
        rows = [rows]
    rows = list(rows)
    if not rows:
        st.caption(empty_message)
        return
    columns = []
    for row in rows:
        if isinstance(row, dict):
            for key in row:
                if key not in columns:
                    columns.append(key)
    if not columns:
        st.caption(empty_message)
        return

    def cell_value(value):
        if value is None or value == "":
            return "—"
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
        return str(value)

    header = "".join(f"<th>{escape(str(column).replace('_', ' '))}</th>" for column in columns)
    body = []
    for row in rows:
        cells = []
        for column in columns:
            value = cell_value(row.get(column) if isinstance(row, dict) else "")
            raw = escape(value)
            if column in {"status", "stage", "outcome", "channel"}:
                raw = f'<span class="civic-status civic-status-{escape(value.lower().replace(" ", "-").replace("_", "-"))}">{raw}</span>'
            cells.append(f"<td>{raw}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    st.markdown(
        f'<div class="civic-table-wrap"><table class="civic-table"><thead><tr>{header}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>',
        unsafe_allow_html=True,
    )


def set_page(page):
    st.session_state.page = page
    if st.session_state.get("user") and page in NAV.get(st.session_state.user["role"], []):
        st.session_state.nav_sync = True


def set_theme():
    theme = st.query_params.get("theme", "light").lower()
    if theme not in ("light", "dark"):
        theme = "light"
    choice = st.sidebar.radio("Appearance", ["Light", "Dark"], index=0 if theme == "light" else 1,
                              horizontal=True, key="theme_picker")
    if choice.lower() != theme:
        st.query_params["theme"] = choice.lower()
        st.rerun()
    dark = theme == "dark"
    colors = {
        "canvas": "#0b1220" if dark else "#f4f7fb",
        "surface": "#121d2f" if dark else "#ffffff",
        "surface_alt": "#18263a" if dark else "#eef4f8",
        "ink": "#eef5f7" if dark else "#102a43",
        "muted": "#a9bdc8" if dark else "#617487",
        "line": "#2b4056" if dark else "#d9e5ec",
        "accent": "#26b6a5",
        "accent_dark": "#168d82",
        "sidebar": "#0e1c2b" if dark else "#102a43",
        "table": "#15253a" if dark else "#f8fbfd",
        "table_header": "#20354b" if dark else "#e7f1f5",
        "table_header_hover": "#2b4960" if dark else "#dcebf0",
        "table_text": "#eef5f7" if dark else "#18364d",
        "table_text_muted": "#b6cbd4" if dark else "#607789",
    }
    st.markdown(f"""<style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@700;800&display=swap');
    :root {{ --canvas:{colors['canvas']}; --surface:{colors['surface']}; --surface-alt:{colors['surface_alt']};
      --ink:{colors['ink']}; --muted:{colors['muted']}; --line:{colors['line']}; --accent:{colors['accent']};
      --accent-dark:{colors['accent_dark']}; --sidebar:{colors['sidebar']}; --table:{colors['table']}; }}
    .stApp {{ background:var(--canvas); color:var(--ink); font-family:'DM Sans',sans-serif; }}
    .stApp [data-testid="stHeader"] {{ background:transparent; }}
    .main .block-container {{ max-width:1320px; padding:3.2rem 3.5rem 4rem; }}
    h1,h2,h3,h4 {{ font-family:'Manrope',sans-serif !important; color:var(--ink) !important; letter-spacing:-.035em; }}
    h1 {{ font-size:2.35rem !important; margin-bottom:.25rem !important; }}
    h2 {{ font-size:1.55rem !important; margin-top:2rem !important; }}
    h3 {{ font-size:1.12rem !important; }}
    p,li,label,span,small,[data-testid="stMarkdownContainer"] {{ color:var(--ink); }}
    [data-testid="stCaptionContainer"], .stCaption {{ color:var(--muted) !important; }}
    [data-testid="stSidebar"] {{ background:var(--sidebar); border-right:1px solid rgba(255,255,255,.08); }}
    [data-testid="stSidebar"] > div:first-child {{ padding:1.5rem 1.1rem; }}
    [data-testid="stSidebar"] * {{ color:#e7f3f4 !important; }}
    [data-testid="stSidebar"] [data-testid="stRadio"] label {{ border-radius:10px; padding:.15rem .7rem; transition:all .2s ease; }}
    [data-testid="stSidebar"] [data-testid="stRadio"] label:hover {{ background:rgba(38,182,165,.14); }}
    [data-testid="stSidebar"] [data-testid="stRadio"] [aria-checked="true"] + div {{ color:#70e0d1 !important; font-weight:700; }}
    [data-testid="stSidebar"] [data-testid="stRadio"] [data-testid="stMarkdownContainer"] p {{ font-size:.9rem; }}
    [data-testid="stMetric"] {{ background:var(--surface); border:1px solid var(--line); border-radius:16px; padding:1.2rem 1.25rem; box-shadow:0 10px 24px rgba(16,42,67,.06); }}
    [data-testid="stMetricLabel"] {{ color:var(--muted) !important; font-size:.77rem; font-weight:700; text-transform:uppercase; letter-spacing:.08em; }}
    [data-testid="stMetricValue"] {{ color:var(--ink) !important; font-family:'Manrope',sans-serif; }}
    [data-testid="stVerticalBlockBorderWrapper"], [data-testid="stExpander"] details {{ background:var(--surface); border:1px solid var(--line); border-radius:16px; box-shadow:0 8px 24px rgba(16,42,67,.045); }}
    [data-testid="stExpander"] details {{ padding:.25rem .4rem; }}
    input, textarea, [data-baseweb="select"] > div, [role="combobox"] {{ background:var(--surface) !important; color:var(--ink) !important; border:1px solid var(--line) !important; border-radius:10px !important; }}
    input:focus, textarea:focus {{ border-color:var(--accent) !important; box-shadow:0 0 0 3px rgba(38,182,165,.16) !important; }}
    button {{ border-radius:10px !important; font-weight:700 !important; min-height:2.55rem; transition:transform .15s ease, box-shadow .15s ease !important; }}
    button:hover {{ transform:translateY(-1px); box-shadow:0 7px 16px rgba(16,42,67,.12); }}
    [data-testid="stBaseButton-primary"] {{ background:linear-gradient(135deg,var(--accent),var(--accent-dark)) !important; border:0 !important; color:#fff !important; }}
    [data-testid="stBaseButton-secondary"] {{ background:var(--surface) !important; border:1px solid var(--line) !important; color:var(--ink) !important; }}
    [data-testid="stAlert"] {{ border-radius:12px; border-width:1px; background:var(--surface-alt); }}
    [data-testid="stFileUploaderDropzone"] {{ background:var(--surface-alt); border:1.5px dashed var(--accent) !important; border-radius:14px; }}
    [data-testid="stFileUploaderDropzone"] * {{ color:var(--ink) !important; }}
    [data-testid="stDataFrame"] {{ border:1px solid var(--line); border-radius:14px; overflow:hidden; background:{colors['table']} !important;
      --gdg-bg-cell:{colors['table']} !important; --gdg-bg-cell-medium:{colors['table_header']} !important;
      --gdg-bg-cell-light:{colors['table']} !important; --gdg-bg-header:{colors['table_header']} !important;
      --gdg-bg-header-hover:{colors['table_header_hover']} !important; --gdg-text-dark:{colors['table_text']} !important;
      --gdg-text-medium:{colors['table_text_muted']} !important; --gdg-text-light:{colors['table_text_muted']} !important;
      --gdg-text-header:{colors['table_text']} !important; --gdg-border-color:{colors['line']} !important;
      --gdg-accent-color:{colors['accent']} !important; }}
    [data-baseweb="tab-list"] {{ gap:.35rem; border-bottom:1px solid var(--line); }}
    [data-baseweb="tab"] {{ color:var(--muted); font-weight:700; }}
    [aria-selected="true"][data-baseweb="tab"] {{ color:var(--accent-dark) !important; }}
    code, pre {{ background:var(--surface-alt) !important; color:var(--ink) !important; border-radius:8px; }}
    .civic-brand {{ display:flex; align-items:center; gap:.65rem; margin:.25rem 0 1.8rem; }}
    .civic-mark {{ width:38px; height:38px; display:grid; place-items:center; border-radius:12px; background:linear-gradient(135deg,var(--accent),#138d86); color:white; font-size:1.25rem; box-shadow:0 8px 18px rgba(38,182,165,.25); }}
    .civic-brand strong {{ color:white; font-family:'Manrope',sans-serif; font-size:1.18rem; letter-spacing:-.03em; }}
    .civic-brand small {{ display:block; color:#9ab4bd !important; font-size:.68rem; margin-top:1px; }}
    .eyebrow {{ color:var(--accent-dark) !important; font-size:.72rem; text-transform:uppercase; letter-spacing:.14em; font-weight:800; margin-bottom:.4rem; }}
    .login-hero {{ background:linear-gradient(135deg,#102a43 0%,#164e63 55%,#138d86 100%); border-radius:24px; padding:2.4rem; margin-bottom:1.25rem; color:white; box-shadow:0 18px 40px rgba(16,42,67,.18); }}
    .login-hero h1, .login-hero p, .login-hero span {{ color:white !important; }}
    .login-hero h1 {{ font-size:2.75rem !important; margin:.2rem 0 .45rem !important; }}
    .login-hero p {{ opacity:.84; max-width:560px; margin:0; }}
    /* Full component pass: controls, tables, navigation, and responsive rhythm. */
    [data-testid="stWidgetLabel"] p {{ color:var(--ink) !important; font-size:.82rem; font-weight:700; letter-spacing:.01em; }}
    [data-testid="stForm"] {{ background:var(--surface); border:1px solid var(--line); border-radius:18px; padding:1.25rem 1.35rem .45rem; box-shadow:0 8px 22px rgba(16,42,67,.045); }}
    [data-testid="stForm"] [data-testid="stWidgetLabel"] p {{ font-size:.78rem; color:var(--muted) !important; text-transform:uppercase; letter-spacing:.08em; }}
    [data-testid="stCheckbox"] label {{ gap:.6rem; }}
    [data-testid="stCheckbox"] [role="checkbox"] {{ border:1px solid var(--line); border-radius:5px; background:var(--surface); }}
    [data-testid="stCheckbox"] [role="checkbox"][aria-checked="true"] {{ background:var(--accent); border-color:var(--accent); }}
    [data-testid="stSelectbox"] [data-baseweb="select"] > div, [data-testid="stDateInput"] input {{ min-height:2.7rem; }}
    [data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea {{ font-size:.93rem; line-height:1.5; }}
    [data-testid="stTextArea"] textarea {{ min-height:7rem; }}
    [data-testid="stButton"] > button, [data-testid="stFormSubmitButton"] > button {{ width:100%; letter-spacing:.01em; }}
    [data-testid="stButton"] > button[kind="secondary"], [data-testid="stFormSubmitButton"] > button[kind="secondary"] {{ box-shadow:none; }}
    [data-testid="stButton"] > button:disabled, [data-testid="stFormSubmitButton"] > button:disabled {{ opacity:.45; cursor:not-allowed; transform:none; box-shadow:none; }}
    [data-testid="stDownloadButton"] > button {{ width:100%; background:var(--surface) !important; color:var(--accent-dark) !important; border:1px solid var(--accent) !important; }}
    [data-testid="stAlert"] {{ padding:.85rem 1rem; margin:.75rem 0; box-shadow:0 5px 14px rgba(16,42,67,.035); }}
    [data-testid="stAlert"] p {{ font-size:.9rem; line-height:1.5; }}
    [data-testid="stDataFrame"] {{ margin:.65rem 0 1.25rem; box-shadow:0 10px 24px rgba(16,42,67,.06); background:{colors['table']} !important; }}
    [data-testid="stDataFrame"] > div, [data-testid="stDataFrame"] canvas {{ border-radius:14px; background:{colors['table']} !important; }}
    [data-testid="stDataFrame"] [role="columnheader"] {{ font-size:.72rem !important; font-weight:800 !important; text-transform:uppercase; letter-spacing:.06em; }}
    [data-testid="stDataFrame"] [role="gridcell"] {{ font-size:.84rem !important; }}
    [data-testid="stDataFrame"] [role="row"]:hover [role="gridcell"] {{ background:rgba(38,182,165,.08) !important; }}
    .civic-table-wrap {{ width:100%; overflow-x:auto; margin:.7rem 0 1.35rem; border:1px solid var(--line); border-radius:16px; background:{colors['table']} !important; box-shadow:0 10px 24px rgba(16,42,67,.06); }}
    .civic-table {{ width:100%; min-width:640px; border-collapse:separate; border-spacing:0; color:{colors['table_text']} !important; font-family:'DM Sans',sans-serif; font-size:.86rem; }}
    .civic-table th {{ background:{colors['table_header']} !important; color:{colors['table_text']} !important; padding:.85rem 1rem; text-align:left; font-size:.7rem; line-height:1.2; font-weight:800; text-transform:uppercase; letter-spacing:.075em; border-bottom:1px solid {colors['line']}; white-space:nowrap; }}
    .civic-table td {{ background:{colors['table']} !important; color:{colors['table_text']} !important; padding:.85rem 1rem; vertical-align:top; line-height:1.45; border-bottom:1px solid {colors['line']}; }}
    .civic-table tbody tr:last-child td {{ border-bottom:0; }}
    .civic-table tbody tr:hover td {{ background:{colors['table_header_hover']} !important; }}
    .civic-status {{ display:inline-flex; align-items:center; padding:.26rem .58rem; border-radius:999px; font-size:.7rem; font-weight:800; white-space:nowrap; background:{colors['table_header']}; color:{colors['table_text']} !important; }}
    .civic-status-resolved, .civic-status-upheld, .civic-status-overturned {{ background:#d8f4e9 !important; color:#14724e !important; }}
    .civic-status-rejected, .civic-status-failed, .civic-status-overdue {{ background:#ffe3e1 !important; color:#a23c39 !important; }}
    .civic-status-pending, .civic-status-under-review, .civic-status-info-requested {{ background:#fff0c9 !important; color:#806215 !important; }}
    .civic-status-assigned, .civic-status-submitted, .civic-status-in-progress {{ background:#dcecff !important; color:#1b5a91 !important; }}
    [data-testid="stExpander"] summary {{ padding:.8rem .75rem !important; font-weight:800; color:var(--ink) !important; }}
    [data-testid="stExpander"] summary:hover {{ color:var(--accent-dark) !important; }}
    [data-testid="stFileUploader"] section {{ padding:.35rem; }}
    [data-testid="stFileUploaderDropzoneInstructions"] {{ padding:.75rem 0; }}
    [data-testid="stFileUploaderDropzoneInstructions"] small {{ color:var(--muted) !important; }}
    [data-testid="stFileUploaderDropzone"] button {{ min-height:2.25rem; background:var(--surface) !important; color:var(--accent-dark) !important; border:1px solid var(--accent) !important; }}
    [data-testid="stTabs"] {{ margin-top:.8rem; }}
    [data-baseweb="tab-list"] {{ padding-left:.25rem; }}
    [data-baseweb="tab"] {{ padding:.75rem 1.1rem; border-radius:9px 9px 0 0; }}
    [data-baseweb="tab"]:hover {{ background:var(--surface-alt); }}
    [data-baseweb="tab-highlight"] {{ background:var(--accent) !important; height:3px !important; }}
    [data-testid="stSidebar"] [data-testid="stButton"] > button {{ background:transparent !important; color:#c9dde1 !important; border:1px solid rgba(255,255,255,.14) !important; box-shadow:none; }}
    [data-testid="stSidebar"] [data-testid="stButton"] > button:hover {{ background:rgba(255,255,255,.08) !important; color:#fff !important; border-color:rgba(112,224,209,.7) !important; }}
    [data-testid="stSidebar"] [data-testid="stRadio"] {{ margin:.2rem 0 1.2rem; }}
    [data-testid="stSidebar"] [data-testid="stRadio"] > label {{ color:#91aeb8 !important; font-size:.66rem; font-weight:800; text-transform:uppercase; letter-spacing:.12em; }}
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] {{ gap:.25rem; }}
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radio"] {{ min-height:2.5rem; padding:.55rem .7rem; border-radius:10px; }}
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radio"]:hover {{ background:rgba(38,182,165,.13); }}
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radio"][aria-checked="true"] {{ background:rgba(38,182,165,.18); box-shadow:inset 3px 0 0 var(--accent); }}
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radio"] > div:first-child {{ display:none; }}
    [data-testid="stSidebar"] [data-testid="stRadio"] [role="radio"] p {{ margin:0; color:#eefafa !important; font-weight:600; }}
    @media (max-width: 900px) {{
      .main .block-container {{ padding:2rem 1.2rem 3rem; }}
      h1 {{ font-size:1.9rem !important; }}
      [data-testid="stMetric"] {{ padding:.9rem; }}
      .login-hero {{ padding:1.6rem; }}
      .login-hero h1 {{ font-size:2.1rem !important; }}
    }}
    </style>""", unsafe_allow_html=True)


def file_signature(name, data):
    return f"{name}:{hashlib.sha256(data).hexdigest()}"


def remember_uploads(uploaded, state_key, removed_key):
    saved = st.session_state.setdefault(state_key, [])
    removed = st.session_state.setdefault(removed_key, set())
    known = {item["key"] for item in saved}
    for file in uploaded or []:
        data = file.getvalue()
        key = file_signature(file.name, data)
        if key not in known and key not in removed:
            saved.append({"key": key, "name": file.name, "mime": file.type or "application/octet-stream",
                          "data": data, "size": len(data), "status": "Selected"})
            known.add(key)
    return saved


def remove_upload(state_key, removed_key, key, picker_version_key):
    st.session_state[state_key] = [f for f in st.session_state.get(state_key, []) if f["key"] != key]
    st.session_state.setdefault(removed_key, set()).add(key)
    st.session_state[picker_version_key] = st.session_state.get(picker_version_key, 0) + 1
    st.rerun()


def upload_list(items, state_key, removed_key, key_prefix, picker_version_key):
    if not items:
        st.caption("No files selected.")
        return
    for index, item in enumerate(list(items)):
        left, right = st.columns([8, 2])
        status = item.get("status", "Selected")
        icon = "✓" if status == "Registered" else "!" if status == "Failed" else "•"
        with left:
            st.markdown(f"**{icon} {item['name']}**  \n{status} · {item['size'] / 1024:.1f} KB")
            if item.get("sha256"):
                st.caption(f"Evidence ID `{item['evidence_id']}` · SHA-256 `{item['sha256']}`")
            if item.get("error"):
                st.error(item["error"])
        if status != "Registered" and right.button("Remove from upload", key=f"{key_prefix}_{index}_{item['key']}"):
                remove_upload(state_key, removed_key, item["key"], picker_version_key)


def login():
    st.markdown("""<div class="login-hero">
        <div class="eyebrow" style="color:#9ce9df !important;">PUBLIC ACCOUNTABILITY · ACCESS TO JUSTICE</div>
        <h1>🛡️ CivicShield</h1>
        <p>Make every complaint traceable, evidence-backed, and visible from intake to resolution.</p>
    </div>""", unsafe_allow_html=True)
    st.caption(f"Connected service · {BACKEND_URL}")
    signin, register = st.tabs(["Sign in", "Create citizen account"])
    with signin, st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Sign in", type="primary", use_container_width=True)
    if submit:
        try:
            result = api("POST", "/auth/login", body={"email": email, "password": password}, authenticated=False)
            st.session_state.token = result["token"]
            st.session_state.user = api("GET", "/me")
            st.rerun()
        except (APIError, KeyError) as exc:
            st.error(str(exc))
    with register, st.form("register_form"):
        name = st.text_input("Full name")
        email = st.text_input("Email address", key="register_email")
        password = st.text_input("Password", type="password", key="register_password")
        submit = st.form_submit_button("Create account", type="primary", use_container_width=True)
    if submit:
        try:
            result = api("POST", "/auth/register", body={"full_name": name, "email": email, "password": password}, authenticated=False)
            st.session_state.token = result["token"]
            st.session_state.user = api("GET", "/me")
            st.rerun()
        except (APIError, KeyError) as exc:
            st.error(str(exc))
    st.caption("Demo accounts use password `Demo@12345`; use only for demonstration.")


def dashboard(user):
    st.title(f"Welcome, {user['full_name']}")
    try:
        cases = api("GET", "/complaints")
        public = api("GET", "/public/stats", authenticated=False)
    except APIError as exc:
        st.error(str(exc))
        return
    metrics = st.columns(4)
    metrics[0].metric("Your cases", len(cases))
    metrics[1].metric("Open", sum(c["status"] not in ("resolved", "decided") for c in cases))
    metrics[2].metric("Total complaints", public.get("complaint_volume", 0))
    metrics[3].metric("Overdue", public.get("overdue_cases", 0))
    st.info(public.get("caution", "Complaint volume is not evidence of misconduct."))
    if cases:
        st.subheader("Recent cases")
        render_table([{k: c.get(k) for k in ("code", "title", "status", "stage", "authority")} for c in cases[:5]])


def case_list():
    st.title("My complaints" if st.session_state.user["role"] == "citizen" else "Cases")
    show_toast()
    try:
        cases = api("GET", "/complaints")
    except APIError as exc:
        st.error(str(exc))
        return
    if not cases:
        st.info("No cases to display.")
        return
    render_table([{k: c.get(k) for k in ("id", "code", "title", "status", "stage", "authority", "ack_deadline")} for c in cases])
    options = {f"{c.get('code') or 'Draft'} · {c['title']} · {c['status']}": c["id"] for c in cases}
    label = st.selectbox("Open a case", list(options), key="case_selection")
    if st.button("Open case", type="primary"):
        st.session_state.selected_case_id = options[label]
        set_page("Case details")
        st.rerun()


def evidence(case, can_add):
    cid = case["id"]
    if can_add:
        st.subheader("Add evidence")
        st.caption("JPG, PNG, PDF, MP3, MP4, or TXT up to 5 MB. ZIP/archive uploads are not accepted.")
        state_key, removed_key = f"case_upload_files_{cid}", f"case_removed_{cid}"
        picker_version_key = f"case_picker_version_{cid}"
        picker_version = st.session_state.setdefault(picker_version_key, 0)
        files = st.file_uploader("Add Evidence", type=FILE_TYPES, accept_multiple_files=True, key=f"case_picker_{cid}_{picker_version}")
        items = remember_uploads(files, state_key, removed_key)
        upload_list(items, state_key, removed_key, f"case_{cid}", picker_version_key)
        pending = [item for item in items if item.get("status") != "Registered"]
        if pending and st.button(f"Upload {len(pending)} file(s)", type="primary", key=f"case_upload_button_{cid}"):
            for item in list(pending):
                try:
                    result = api("POST", f"/complaints/{cid}/evidence",
                                 files={"file": (item["name"], item["data"], item["mime"])})
                    st.session_state[state_key] = [f for f in st.session_state[state_key] if f["key"] != item["key"]]
                    st.session_state[removed_key].add(item["key"])
                    st.session_state.setdefault(f"case_success_{cid}", []).append(result)
                except APIError as exc:
                    for saved in st.session_state[state_key]:
                        if saved["key"] == item["key"]:
                            saved["status"], saved["error"] = "Failed", str(exc)
            st.session_state[picker_version_key] += 1
            st.rerun()
        for item in st.session_state.get(f"case_success_{cid}", []):
            st.success(f"Registered {item.get('filename')} · Evidence ID {item.get('evidence_id')} · SHA-256 {item.get('sha256')}")
    st.subheader("Evidence")
    if not case.get("evidence"):
        st.caption("No evidence attached.")
    for item in case.get("evidence", []):
        name, details, digest, action = st.columns([4, 2, 4, 2])
        name.markdown(f"**{item['original_filename']}**")
        details.caption(f"{item['size']} B · {item['mime']}")
        digest.caption(f"SHA-256: {item['sha256']}")
        if case.get("can_download_evidence"):
            download_key = f"evidence_bytes_{item['id']}"
            if st.session_state.get(download_key) is None and action.button("Prepare download", key=f"prepare_{item['id']}"):
                try:
                    st.session_state[download_key] = download(item["id"])
                    st.rerun()
                except APIError as exc:
                    action.error(str(exc))
            if st.session_state.get(download_key) is not None:
                action.download_button("Download", st.session_state[download_key], file_name=item["original_filename"], key=f"ev_{item['id']}")


def show_legal(legal):
    if not legal:
        st.info("No legal assistance has been generated for this case.")
        return
    st.warning(legal.get("disclaimer", "General procedural information, not personalized legal advice."))
    if legal.get("summary"):
        st.subheader("Case summary")
        st.write(legal["summary"])
    if legal.get("options"):
        st.subheader("Procedural options")
        for option in legal["options"]:
            if isinstance(option, dict):
                st.markdown(f"**{option.get('option', '')}** — {option.get('what', '')} ({option.get('typical_forum', '')})")
            else:
                st.write(option)
    providers = legal.get("providers") or legal.get("matches")
    if providers:
        st.subheader("Legal-aid providers")
        render_table(providers)
    if legal.get("checklist"):
        st.subheader("Checklist")
        for item in legal["checklist"]:
            st.markdown(f"- {item}")


def friendly_incident_text(text):
    text = str(text or "").strip()
    if "CIVICSHIELD" in text.upper() and "INCIDENT DESCRIPTION" in text.upper():
        marker = re.search(r"(?:^|\n)\s*\d+\.\s*Incident Description\s*(?:\n|$)", text, re.IGNORECASE)
        if marker:
            text = text[marker.end():]
            next_section = re.search(r"^\s*\d+\.\s+", text, re.MULTILINE)
            if next_section:
                text = text[:next_section.start()]
            text = " ".join(line.strip() for line in text.splitlines() if line.strip())
    return text or "No incident description is available."


def friendly_case_incident(case):
    report = case.get("citizen_report") or (case.get("ai_analysis") or {}).get("report") or {}
    incident = str(report.get("incident_summary") or "").strip()
    if not incident or "CIVICSHIELD" in incident.upper() or "SIMULATED DEMO DATA" in incident.upper():
        incident = case.get("original_description", "")
    return friendly_incident_text(incident)[:800]


def render_case_summary(summary, heading="Case summary"):
    st.subheader(heading)
    st.markdown(f"### {summary.get('title') or 'Complaint summary'}")
    if summary.get("incident_summary"):
        st.write(friendly_incident_text(summary["incident_summary"]))
    status = str(summary.get("current_status", "unknown")).replace("_", " ").title()
    stage = str(summary.get("stage", "unknown")).replace("_", " ").title()
    st.caption(f"Current status: {status} · Review stage: {stage}")
    if summary.get("note"):
        st.info(summary["note"])

    timeline = summary.get("timeline") or []
    if timeline:
        st.markdown("**Incident timeline**")
        for item in timeline:
            if isinstance(item, dict):
                when = item.get("when") or "Date not provided"
                event = item.get("event") or item.get("description") or "Event recorded"
                st.markdown(f"- **{when}:** {event}")
            else:
                st.markdown(f"- {item}")

    authorities = summary.get("authorities_involved") or []
    if authorities:
        names = [f"{item.get('name', 'Authority')} ({item.get('level', 'level not specified')})"
                 for item in authorities if isinstance(item, dict)]
        if names:
            st.markdown("**Authorities involved**")
            st.write(" · ".join(names))

    for label, key in (("Evidence received", "evidence_inventory"),):
        entries = summary.get(key) or []
        if entries:
            st.markdown(f"**{label}**")
            for item in entries:
                if isinstance(item, dict):
                    st.markdown(f"- {item.get('filename') or 'Evidence file'}")

    for label, key in (("Responses", "responses"), ("Decisions", "decisions"), ("Appeals", "appeals")):
        entries = summary.get(key) or []
        if entries:
            st.markdown(f"**{label}**")
            for item in entries:
                if not isinstance(item, dict):
                    continue
                heading = item.get("kind") or item.get("outcome") or item.get("status") or label[:-1]
                detail = item.get("text") or item.get("reasoning") or item.get("grounds")
                st.markdown(f"- **{str(heading).replace('_', ' ').title()}**")
                if detail:
                    st.write(detail)

    for label, key in (("Transfers", "transfers"), ("Escalations", "escalations")):
        entries = summary.get(key) or []
        if entries:
            st.markdown(f"**{label}**")
            for item in entries:
                if not isinstance(item, dict):
                    continue
                origin, destination = item.get("from") or "Authority", item.get("to") or "Unassigned"
                st.markdown(f"- {origin} → {destination}")
                if item.get("reason"):
                    st.write(item["reason"])

    options = summary.get("next_procedural_options") or []
    if options:
        st.markdown("**Possible next steps**")
        for option in options:
            if isinstance(option, dict):
                st.markdown(f"- **{option.get('option', 'Option')}** — {option.get('what', '')}")
                if option.get("typical_forum"):
                    st.caption(f"Typical forum: {option['typical_forum']}")
            else:
                st.markdown(f"- {option}")


def render_local_case_overview(case, error):
    st.warning(f"The n8n summary could not be generated ({error}). This local overview uses the case details already loaded; it is not an n8n summary.")
    timeline = [{"when": event.get("created_at"),
                 "event": f"{str(event.get('to_status') or 'Update').replace('_', ' ').title()} — {event.get('reason') or 'Case status updated.'}"}
                for event in case.get("status_history", [])]
    summary = {
        "title": case.get("title"),
        "incident_summary": friendly_case_incident(case),
        "current_status": case.get("status"),
        "stage": case.get("stage"),
        "timeline": timeline,
        "authorities_involved": ([{"name": case["current_authority"], "level": "Current authority"}]
                                 if case.get("current_authority") else []),
        "evidence_inventory": [{"filename": item.get("original_filename")} for item in case.get("evidence", [])],
        "responses": [{"kind": item.get("kind"), "text": item.get("body")} for item in case.get("responses", [])],
        "decisions": [{"outcome": item.get("outcome"), "reasoning": item.get("reasoning")} for item in case.get("decisions", [])],
        "appeals": [{"status": item.get("status"), "outcome": item.get("outcome"), "grounds": item.get("grounds")} for item in case.get("appeals", [])],
        "note": "Allegations are unverified unless the stage is a verified finding.",
    }
    render_case_summary(summary, "Local case overview")


def action(path, body):
    try:
        api("POST", path, body=body)
        toast("Action completed.")
        st.rerun()
    except APIError as exc:
        st.error(str(exc))


def official_actions(case, user):
    cid = case["id"]
    st.subheader("Authority actions")
    if st.button("Acknowledge", disabled=case["status"] != "assigned", key=f"ack_{cid}"):
        action(f"/complaints/{cid}/acknowledge", {"note": "Complaint acknowledged."})
    with st.form(f"response_{cid}"):
        text = st.text_area("Response / correction text")
        response, request = st.columns(2)
        send = response.form_submit_button("Send response")
        ask = request.form_submit_button("Request information")
    if send:
        action(f"/complaints/{cid}/respond", {"body": text})
    if ask:
        action(f"/complaints/{cid}/request-info", {"body": text})
    with st.form(f"decision_{cid}"):
        outcome = st.selectbox("Decision", ["upheld", "partially_upheld", "rejected", "resolved"])
        reason = st.text_area("Reasoning")
        save = st.form_submit_button("Record decision")
    if save:
        action(f"/complaints/{cid}/decision", {"outcome": outcome, "reasoning": reason})
    try:
        peers = api("GET", "/authorities/peers")
    except APIError:
        peers = []
    if peers:
        with st.form(f"transfer_{cid}"):
            choices = {f"{p['name']} (#{p['id']})": p["id"] for p in peers}
            target = st.selectbox("Transfer to peer authority", list(choices))
            reason = st.text_input("Transfer reason")
            transfer = st.form_submit_button("Transfer case")
        if transfer:
            action(f"/complaints/{cid}/transfer", {"to_authority_id": choices[target], "reason": reason})


def case_details(cid):
    try:
        case = api("GET", f"/complaints/{cid}")
    except APIError as exc:
        st.error(str(exc))
        return
    user = st.session_state.user
    st.title(case.get("title") or "Case details")
    if case.get("code"):
        st.caption(f"Complaint reference: {case['code']}")
    st.write(f"Status: **{case['status'].replace('_', ' ')}** · Stage: **{case['stage'].replace('_', ' ')}**")
    st.info("Stages are procedural labels. Allegations are unverified unless the stage is a verified finding. Overdue deadlines never imply guilt.")
    st.write(f"Current authority: {case.get('current_authority') or 'not yet assigned'} · Citizen: {case.get('citizen', {}).get('name', 'Masked')}")
    st.write(f"Acknowledge by: {case.get('ack_deadline') or '—'} · Resolve by: {case.get('resolution_deadline') or '—'}")
    with st.expander("Original complaint and integrity details", expanded=False):
        st.caption(f"Original text SHA-256: {case.get('original_hash', '')}")
        st.write(case.get("original_description", ""))
    report = case.get("citizen_report") or (case.get("ai_analysis") or {}).get("report") or {}
    if case.get("ai_analysis"):
        with st.expander(case["ai_analysis"].get("label", "AI-assisted analysis")):
            st.caption(case["ai_analysis"].get("disclaimer", "Requires human review."))
            st.write(report.get("incident_summary", ""))
    if case.get("duplicate_flags"):
        st.warning("Possible duplicates are flagged for human review; they do not reject this complaint.")
        st.write(case["duplicate_flags"])

    owns = user["role"] == "citizen" and case.get("citizen_id") == user["id"]
    if owns and case["status"] == "draft":
        if st.button("Edit Draft", key=f"edit_draft_{cid}", type="primary"):
            category = next((item for item in st.session_state.meta["categories"] if item["id"] == case["category_id"]), None)
            st.session_state.report_id = cid
            st.session_state.report_step = 1
            st.session_state.report_title = case["title"]
            st.session_state.report_category = f"{category['department']}: {category['name']}" if category else ""
            st.session_state.report_jurisdiction = case["jurisdiction"]
            st.session_state.report_date = date.fromisoformat(case["incident_date"]) if case.get("incident_date") else None
            st.session_state.report_location = case.get("location_text") or ""
            st.session_state.report_description = case["original_description"]
            st.session_state.report_values = {
                "title": st.session_state.report_title, "category": st.session_state.report_category,
                "jurisdiction": st.session_state.report_jurisdiction, "date": st.session_state.report_date,
                "location": st.session_state.report_location, "description": st.session_state.report_description,
            }
            set_page("Report incident")
            st.rerun()
        with st.expander("Delete draft"):
            st.warning("This removes the draft from your list; its audit history is retained.")
            confirmed = st.checkbox("I understand this draft will be removed", key=f"confirm_delete_{cid}")
            if st.button("Delete Draft", disabled=not confirmed, key=f"delete_{cid}", type="primary"):
                try:
                    api("DELETE", f"/complaints/{cid}")
                    toast("Draft deleted successfully.")
                    st.session_state.selected_case_id = None
                    set_page("My complaints")
                    st.rerun()
                except APIError as exc:
                    st.error(str(exc))
    evidence(case, owns and case["status"] != "draft")

    with st.expander("Case timeline"):
        rows = [{"when": e.get("created_at"), "status": e.get("to_status"), "reason": e.get("reason"), "actor": e.get("actor")}
                for e in case.get("status_history", [])]
        render_table(rows, "No timeline entries.")
    with st.expander("Official responses and decisions"):
        for label in ("responses", "decisions", "appeals"):
            st.subheader(label.title())
            render_table(case.get(label, []), f"No {label} recorded.")

    chain = case.get("audit_chain", {})
    (st.success if chain.get("valid") else st.error)(f"Audit chain {'intact' if chain.get('valid') else 'BROKEN'} · {chain.get('events', 0)} events")
    left, right = st.columns(2)
    if left.button("Show audit events", key=f"audit_button_{cid}"):
        try:
            st.session_state[f"audit_{cid}"] = api("GET", f"/complaints/{cid}/audit")["events"]
        except APIError as exc:
            st.error(str(exc))
    if right.button("Case summary (n8n)", key=f"summary_button_{cid}"):
        try:
            st.session_state[f"summary_{cid}"] = api("GET", f"/complaints/{cid}/summary")
            st.session_state.pop(f"summary_error_{cid}", None)
        except APIError as exc:
            st.session_state[f"summary_error_{cid}"] = str(exc)
    if st.session_state.get(f"audit_{cid}"):
        render_table(st.session_state[f"audit_{cid}"], "No audit events available.")
    if st.session_state.get(f"summary_{cid}"):
        render_case_summary(st.session_state[f"summary_{cid}"])
    elif st.session_state.get(f"summary_error_{cid}"):
        render_local_case_overview(case, st.session_state[f"summary_error_{cid}"])

    if owns:
        st.subheader("Citizen actions")
        if case["status"] == "info_requested":
            with st.form(f"reply_{cid}"):
                text = st.text_area("Reply to information request")
                if st.form_submit_button("Send reply"):
                    action(f"/complaints/{cid}/reply", {"body": text})
        decisions = case.get("decisions", [])
        last = decisions[-1] if decisions else None
        has_appeal = last and any(a.get("decision_id") == last["id"] for a in case.get("appeals", []))
        if case["status"] == "decided" and last and last.get("outcome") in ("rejected", "partially_upheld") and not has_appeal:
            with st.form(f"appeal_{cid}"):
                grounds = st.text_area("Appeal grounds")
                if st.form_submit_button("Submit appeal"):
                    action(f"/complaints/{cid}/appeal", {"grounds": grounds})
        shared = st.checkbox("Share a masked case summary with legal-aid providers", value=bool(case.get("legal_share")), key=f"share_{cid}")
        if st.button("Save legal-aid sharing preference", key=f"share_save_{cid}"):
            action(f"/complaints/{cid}/legal-share", {"share": shared})
        if case.get("legal_assistance"):
            show_legal(case["legal_assistance"])
        elif st.button("Show legal-assistance options", key=f"legal_{cid}"):
            try:
                api("POST", f"/complaints/{cid}/legal-help/request", body={})
                st.rerun()
            except APIError as exc:
                st.error(str(exc))
    if user["role"] in ("official", "supervisor") and case.get("current_authority_id") == user.get("authority_id"):
        official_actions(case, user)
    if user["role"] == "legal_aid" and case.get("legal_assistance"):
        show_legal(case["legal_assistance"])
    if st.button("Back to cases", key=f"case_back_{cid}"):
        role = user["role"]
        destination = {"citizen": "My complaints", "official": "Assigned cases", "supervisor": "Cases",
                       "legal_aid": "Shared cases", "admin": "All cases"}.get(role, "Dashboard")
        set_page(destination)
        st.rerun()


def report_wizard(meta):
    st.title("Report an incident")
    if st.session_state.pop("report_reset", False):
        for key in ("report_title", "report_category", "report_jurisdiction", "report_date", "report_location",
                    "report_description", "wizard_uploads", "wizard_removed", "wizard_registered", "report_ai",
                    "review_summary", "report_review_summary", "report_approved", "report_values"):
            st.session_state.pop(key, None)
        st.session_state.report_step = 1
    step = st.session_state.setdefault("report_step", 1)
    st.caption(f"Step {step} of 3 · Describe, attach evidence, then review before submitting.")
    if step == 1:
        saved = st.session_state.get("report_values", {})
        for key, value in (("report_title", "title"), ("report_category", "category"),
                           ("report_jurisdiction", "jurisdiction"), ("report_date", "date"),
                           ("report_location", "location"), ("report_description", "description")):
            if key not in st.session_state and value in saved:
                st.session_state[key] = saved[value]
        with st.form("report_details"):
            title = st.text_input("Title", key="report_title")
            categories = {f"{c['department']}: {c['name']}": c["id"] for c in meta["categories"]}
            category = st.selectbox("Category", list(categories), key="report_category")
            jurisdiction = st.selectbox("Jurisdiction / office area", meta["jurisdictions"], key="report_jurisdiction")
            incident_date = st.date_input("Incident date (optional)", value=None, key="report_date")
            location = st.text_input("Location", key="report_location")
            description = st.text_area("What happened? Describe facts: who, what, where, when", key="report_description", height=140)
            proceed = st.form_submit_button("Save draft and continue", type="primary")
        if proceed:
            payload = {"title": title, "category_id": categories[category], "jurisdiction": jurisdiction,
                       "incident_date": incident_date.isoformat() if isinstance(incident_date, date) else "",
                       "location_text": location, "description": description}
            st.session_state.report_values = {"title": title, "category": category, "jurisdiction": jurisdiction,
                                              "date": incident_date, "location": location, "description": description}
            try:
                if st.session_state.get("report_id"):
                    api("PUT", f"/complaints/{st.session_state.report_id}", body=payload)
                else:
                    st.session_state.report_id = api("POST", "/complaints", body=payload)["id"]
                st.session_state.report_step = 2
                st.rerun()
            except APIError as exc:
                st.error(str(exc))
        return

    cid = st.session_state.get("report_id")
    if not cid:
        st.session_state.report_step = 1
        st.rerun()
    if step == 2:
        st.subheader("Evidence")
        st.caption("JPG, PNG, PDF, MP3, MP4, or TXT up to 5 MB. n8n validates and hashes uploads. ZIP/archive files are not accepted.")
        picker_version_key = "wizard_picker_version"
        picker_version = st.session_state.setdefault(picker_version_key, 0)
        files = st.file_uploader("Select evidence files", type=FILE_TYPES, accept_multiple_files=True, key=f"wizard_file_picker_{picker_version}")
        items = remember_uploads(files, "wizard_uploads", "wizard_removed")
        upload_list(items, "wizard_uploads", "wizard_removed", "wizard", picker_version_key)
        pending = [item for item in items if item.get("status") != "Registered"]
        if pending and st.button(f"Upload {len(pending)} file(s)", type="primary", key="wizard_upload_button"):
            for item in list(pending):
                try:
                    result = api("POST", f"/complaints/{cid}/evidence",
                                 files={"file": (item["name"], item["data"], item["mime"])})
                    st.session_state.wizard_uploads = [f for f in st.session_state.wizard_uploads if f["key"] != item["key"]]
                    st.session_state.wizard_removed.add(item["key"])
                    st.session_state.setdefault("wizard_registered", []).append(result)
                except APIError as exc:
                    for saved in st.session_state.wizard_uploads:
                        if saved["key"] == item["key"]:
                            saved["status"], saved["error"] = "Failed", str(exc)
            st.session_state[picker_version_key] += 1
            st.rerun()
        for item in st.session_state.get("wizard_registered", []):
            st.success(f"Registered {item.get('filename')} · SHA-256 {item.get('sha256')}")
        back, analyze = st.columns(2)
        if back.button("Back", key="report_back_one"):
            st.session_state.report_step = 1
            st.rerun()
        if analyze.button("Run AI-assisted analysis", type="primary", disabled=bool(pending), key="report_analyze"):
            try:
                st.session_state.report_ai = api("POST", f"/complaints/{cid}/analyze")
                st.session_state.report_step = 3
                st.rerun()
            except APIError as exc:
                st.error(str(exc))
        if pending:
            st.caption("Upload or remove selected files before continuing to review.")
        return

    analysis = st.session_state.get("report_ai")
    if not analysis:
        st.session_state.report_step = 2
        st.rerun()
    report = analysis.get("report", {})
    if "review_summary" not in st.session_state:
        st.session_state.review_summary = st.session_state.get("report_review_summary", report.get("incident_summary", ""))
    st.subheader(analysis.get("label", "AI-assisted report"))
    st.caption(analysis.get("disclaimer", "Review the AI-assisted report before submission."))
    summary = st.text_area("Incident summary (edit freely)", key="review_summary")
    st.write(f"Location: {report.get('location') or '—'} · Dates: {', '.join(report.get('date_time', {}).get('dates', [])) or '—'}")
    st.write("Timeline")
    for event in report.get("timeline", []):
        st.markdown(f"- **{event.get('when', '')}:** {event.get('event', '')}")
    for item in report.get("missing_information", []):
        st.warning(f"Missing information: {item}")
    if report.get("duplicate_warning"):
        st.info(report["duplicate_warning"])
    approved = st.checkbox("I reviewed and approve this report. Allegations are not proven facts.", key="report_approved")
    back, submit = st.columns(2)
    if back.button("Back", key="report_back_two"):
        st.session_state.report_review_summary = summary
        st.session_state.report_step = 2
        st.rerun()
    if submit.button("Submit complaint", type="primary", disabled=not approved, key="report_submit"):
        try:
            reviewed = {**report, "incident_summary": summary}
            result = api("POST", f"/complaints/{cid}/submit", body={"approved": True, "citizen_report": reviewed})
            st.session_state.selected_case_id = cid
            set_page("My complaints")
            st.session_state.report_id = None
            st.session_state.report_reset = True
            toast(f"Submitted {result['code']} · n8n intake: {result['workflow']}.")
            st.rerun()
        except APIError as exc:
            st.error(str(exc))


def notifications_page():
    st.title("Notifications")
    try:
        notes = api("GET", "/notifications")
    except APIError as exc:
        st.error(str(exc))
        return
    for note in notes:
        with st.container(border=True):
            st.markdown(f"**{note['title']}** · {note['channel'].replace('_', ' ')} · {note['created_at']}")
            st.write(note["message"])
            if not note["is_read"] and st.button("Mark read", key=f"note_{note['id']}"):
                try:
                    api("POST", f"/notifications/{note['id']}/read", body={})
                    st.rerun()
                except APIError as exc:
                    st.error(str(exc))
            if note.get("complaint_id") and st.button("Open case", key=f"note_case_{note['id']}"):
                st.session_state.selected_case_id = note["complaint_id"]
                set_page("Case details")
                st.rerun()


def appeals_page(user):
    st.title("Appeals")
    try:
        appeals = api("GET", "/appeals")
    except APIError as exc:
        st.error(str(exc))
        return
    if not appeals:
        st.info("No appeals to display.")
    for item in appeals:
        with st.container(border=True):
            st.markdown(f"**{item.get('code', 'Appeal')}** · {item.get('title', '')} · {item['status']}")
            st.write(item.get("grounds", ""))
            if user["role"] == "supervisor" and item["status"] == "under_review":
                with st.form(f"decide_appeal_{item['id']}"):
                    outcome = st.selectbox("Outcome", ["overturned", "modified", "upheld"], key=f"appeal_outcome_{item['id']}")
                    reason = st.text_area("Reasoning", key=f"appeal_reason_{item['id']}")
                    submit = st.form_submit_button("Decide appeal")
                if submit:
                    action(f"/appeals/{item['id']}/decide", {"outcome": outcome, "reasoning": reason})
            if st.button("Open case", key=f"appeal_case_{item['id']}"):
                st.session_state.selected_case_id = item["complaint_id"]
                set_page("Case details")
                st.rerun()


def oversight_page(user):
    st.title("Oversight")
    try:
        overdue = api("GET", "/supervisor/overdue")
        escalated = api("GET", "/supervisor/escalated")
        analytics = api("GET", "/supervisor/analytics")
    except APIError as exc:
        st.error(str(exc))
        return
    st.subheader("Overdue cases")
    st.caption("Overdue means a procedural deadline passed; it is not a finding of misconduct.")
    render_table(overdue, "No overdue cases.")
    if st.button("Run n8n deadline monitor", type="primary"):
        try:
            st.json(api("POST", "/admin/run-deadline-monitor", body={}))
        except APIError as exc:
            st.error(str(exc))
    st.subheader("Escalations")
    render_table(escalated, "No escalated cases.")
    st.subheader("Department performance")
    st.info(analytics["overall"].get("caution", "Metrics describe process, not guilt."))
    render_table(analytics.get("departments", []), "No department performance data.")


def legal_directory():
    st.title("Legal-aid directory")
    try:
        render_table(api("GET", "/legal-aid"), "No legal-aid providers found.")
    except APIError as exc:
        st.error(str(exc))


def administration():
    st.title("Administration")
    section = st.radio("Section", ["Users", "Configuration", "Audit", "Workflows"], horizontal=True)
    try:
        if section == "Users":
            render_table(api("GET", "/admin/users"), "No users found.")
        elif section == "Configuration":
            config = api("GET", "/admin/config")
            for key in ("rules", "authorities", "categories", "legal_aid"):
                st.subheader(key.replace("_", " ").title())
                render_table(config.get(key, []), f"No {key.replace('_', ' ')} configured.")
        elif section == "Audit":
            audit = api("GET", "/admin/audit")
            st.subheader("Hash-chain verification")
            render_table(audit.get("chains", []), "No audit chains found.")
            st.subheader("Recent events")
            render_table(audit.get("recent", []), "No recent events found.")
        else:
            runs = api("GET", "/admin/workflow-runs")
            render_table(runs, "No workflow runs found.")
            for run in runs:
                if run["status"] == "pending" and st.button(f"Retry {run['workflow']} run #{run['id']}", key=f"retry_{run['id']}"):
                    api("POST", f"/admin/workflow-runs/{run['id']}/retry", body={})
                    st.rerun()
    except APIError as exc:
        st.error(str(exc))


def main():
    for key, value in (("token", ""), ("user", None), ("page", "Dashboard"), ("selected_case_id", None)):
        st.session_state.setdefault(key, value)
    set_theme()
    if not st.session_state.token:
        login()
        return
    try:
        user = api("GET", "/me")
        st.session_state.user = user
        meta = api("GET", "/meta", authenticated=False)
        st.session_state.meta = meta
    except APIError as exc:
        st.error(str(exc))
        if st.button("Sign out"):
            st.session_state.token, st.session_state.user = "", None
            st.rerun()
        return
    pages = NAV.get(user["role"], ["Dashboard"])
    if st.session_state.page not in pages and st.session_state.page != "Case details":
        st.session_state.page = pages[0]
    if st.session_state.pop("nav_sync", False) and st.session_state.page in pages:
        st.session_state.nav_selection = st.session_state.page
    def navigate():
        st.session_state.page = st.session_state.nav_selection

    with st.sidebar:
        st.markdown("""<div class="civic-brand">
            <div class="civic-mark">✦</div>
            <div><strong>CivicShield</strong><small>ACCOUNTABILITY PLATFORM</small></div>
        </div>""", unsafe_allow_html=True)
        st.caption(f"Signed in as {user['full_name']} · {user['role'].replace('_', ' ')}")
        if "nav_selection" not in st.session_state or st.session_state.nav_selection not in pages:
            st.session_state.nav_selection = pages[0]
        st.radio("Navigation", pages, key="nav_selection", on_change=navigate)
        if st.button("Sign out", use_container_width=True):
            for key in list(st.session_state):
                del st.session_state[key]
            st.rerun()
    show_toast()
    page = st.session_state.page
    if page == "Dashboard":
        dashboard(user)
    elif page == "Report incident":
        report_wizard(meta)
    elif page in ("My complaints", "Cases", "Assigned cases", "All cases", "Shared cases", "Legal help"):
        case_list()
    elif page == "Case details" and st.session_state.selected_case_id:
        case_details(st.session_state.selected_case_id)
    elif page == "Notifications":
        notifications_page()
    elif page == "Appeals":
        appeals_page(user)
    elif page == "Oversight":
        oversight_page(user)
    elif page == "Legal directory":
        legal_directory()
    elif page == "Administration":
        administration()


if __name__ == "__main__":
    main()
