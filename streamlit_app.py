from __future__ import annotations

import base64
import io
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from parser_engine import OUTPUT_COLUMNS, parse_pdf
from snowflake_backend import existing_hashes, file_hash, is_snowflake, recent_runs, save_run

st.set_page_config(page_title="Debit Note Intelligence", page_icon="◈", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
:root { --ink:#eef2ff; --muted:#93a4c3; --lime:#b7f34a; --cyan:#47d7ff; --panel:rgba(15,25,48,.78); }
.stApp { background: radial-gradient(circle at 82% 2%, #17395e 0, transparent 28%), radial-gradient(circle at 7% 28%, #24325c 0, transparent 25%), #070d1c; color:var(--ink); font-family:'DM Sans',sans-serif; }
[data-testid="stSidebar"] { background:#091225; border-right:1px solid #20304e; }
h1,h2,h3 { font-family:'Space Grotesk',sans-serif!important; letter-spacing:-.03em; }
.hero { padding:2.3rem 2.5rem; border:1px solid #28405f; border-radius:26px; background:linear-gradient(125deg,rgba(20,35,68,.96),rgba(10,19,39,.78)); box-shadow:0 30px 80px rgba(0,0,0,.35); overflow:hidden; position:relative; }
.hero:after { content:'◈'; position:absolute; right:3rem; top:-2.5rem; font-size:11rem; color:rgba(71,215,255,.08); transform:rotate(17deg); }
.eyebrow { color:var(--lime); font-size:.74rem; font-weight:700; letter-spacing:.18em; text-transform:uppercase; }
.hero h1 { font-size:clamp(2.3rem,5vw,4.8rem); line-height:.95; margin:.7rem 0 1rem; max-width:900px; }
.hero p { color:#aebbd3; max-width:720px; font-size:1.06rem; }
.metric-card { min-height:142px; padding:1.35rem; border:1px solid #263a58; border-radius:20px; background:var(--panel); }
.metric-label { color:var(--muted); text-transform:uppercase; font-size:.7rem; letter-spacing:.12em; font-weight:700; }
.metric-value { font:700 2rem 'Space Grotesk'; margin-top:.65rem; }
.metric-foot { color:#7890b5; font-size:.76rem; margin-top:.2rem; }
.status-ok,.status-warn { display:inline-flex; border-radius:30px; padding:.35rem .75rem; font-size:.76rem; font-weight:700; }
.status-ok { color:#b7f34a; background:rgba(183,243,74,.1); border:1px solid rgba(183,243,74,.25); }
.status-warn { color:#ffca68; background:rgba(255,202,104,.1); border:1px solid rgba(255,202,104,.25); }
[data-testid="stFileUploader"] { border:1px dashed #3d5e87; border-radius:22px; padding:.8rem; background:rgba(14,27,51,.66); }
.stButton>button { border-radius:12px; border:1px solid #b7f34a; background:#b7f34a; color:#09101d; font-weight:800; }
.stDownloadButton>button { border-radius:12px; border:1px solid #365071; background:#11213b; color:#e9f0ff; }
div[data-testid="stDataFrame"] { border:1px solid #263b5d; border-radius:18px; overflow:hidden; }
.tiny { color:#7488aa; font-size:.78rem; }
</style>
""", unsafe_allow_html=True)


def money(value: float) -> str:
    return f"₹{value:,.0f}"


def excel_bytes(records: pd.DataFrame, issues: pd.DataFrame) -> bytes:
    book = Workbook()
    for index, (name, frame) in enumerate((("Extracted Data", records), ("Exceptions", issues))):
        sheet = book.active if index == 0 else book.create_sheet()
        sheet.title = name
        sheet.append(list(frame.columns))
        for row in frame.itertuples(index=False, name=None): sheet.append(list(row))
        for cell in sheet[1]:
            cell.fill = PatternFill("solid", fgColor="172B4D"); cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(vertical="center")
        sheet.freeze_panes = "A2"; sheet.auto_filter.ref = sheet.dimensions; sheet.sheet_view.showGridLines = False
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = min(42, max(12, max(len(str(c.value or "")) for c in column) + 2))
    output = io.BytesIO(); book.save(output); return output.getvalue()


with st.sidebar:
    st.markdown("### ◈ DN Intelligence")
    st.caption("Finance extraction cockpit")
    st.markdown("---")
    page = st.radio("Workspace", ["Command center", "Extraction studio", "Audit lab"], label_visibility="collapsed")
    st.markdown("---")
    st.markdown("**Parser coverage**")
    for vendor in ("Reliance Retail", "More Retail", "Vishal / Airplaza", "Tesco / Trent"):
        st.markdown(f"<div class='tiny'>● &nbsp; {vendor}</div>", unsafe_allow_html=True)
    st.markdown("<br><span class='status-ok'>4 rules online</span>", unsafe_allow_html=True)
    st.markdown("---")
    runtime_label = "Snowflake persistence enabled" if is_snowflake() else "Local development mode"
    st.caption(runtime_label)

if "records" not in st.session_state: st.session_state.records = []
if "issues" not in st.session_state: st.session_state.issues = []
if "evidence" not in st.session_state: st.session_state.evidence = {}
if "pdfs" not in st.session_state: st.session_state.pdfs = {}

if page == "Command center":
    st.markdown("<section class='hero'><div class='eyebrow'>Finance operations • live control plane</div><h1>From debit notes<br>to decision-ready data.</h1><p>Drop a chaotic batch of PDFs. The cockpit identifies each customer format, extracts claim-level detail, runs finance checks, and exposes every exception before export.</p></section>", unsafe_allow_html=True)
    st.write("")
    records = pd.DataFrame(st.session_state.records)
    issues = pd.DataFrame(st.session_state.issues)
    total = float(pd.to_numeric(records.get("Total Amount", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    values = [("Documents", len(st.session_state.pdfs), "in current workspace"), ("Claims extracted", len(records), "structured rows"), ("Claim value", money(total), "gross amount"), ("Exceptions", len(issues), "need attention")]
    cols = st.columns(4)
    for col, (label, value, foot) in zip(cols, values):
        col.markdown(f"<div class='metric-card'><div class='metric-label'>{label}</div><div class='metric-value'>{value}</div><div class='metric-foot'>{foot}</div></div>", unsafe_allow_html=True)
    st.write("")
    if records.empty:
        st.info("Your command center is ready. Open **Extraction studio** to process the first batch.")
    else:
        left, right = st.columns([1.25, 1])
        vendor = records.groupby("Vendor Name", dropna=False)["Total Amount"].sum().reset_index()
        fig = px.bar(vendor, x="Total Amount", y="Vendor Name", orientation="h", title="Claim value by customer", color="Total Amount", color_continuous_scale=["#213456", "#47d7ff", "#b7f34a"])
        fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", coloraxis_showscale=False, margin=dict(l=10,r=10,t=55,b=10))
        left.plotly_chart(fig, use_container_width=True)
        health = pd.DataFrame({"Status": ["Ready", "Exception"], "Count": [max(len(records)-len(issues), 0), len(issues)]})
        donut = px.pie(health, names="Status", values="Count", hole=.7, title="Batch health", color="Status", color_discrete_map={"Ready":"#b7f34a", "Exception":"#ff6b7d"})
        donut.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=10,r=10,t=55,b=10), legend_orientation="h")
        right.plotly_chart(donut, use_container_width=True)

elif page == "Extraction studio":
    st.markdown("## Extraction studio")
    st.caption("Upload one PDF or an entire debit-note batch. Nothing is archived or committed automatically.")
    files = st.file_uploader("Drop debit-note PDFs", type=["pdf"], accept_multiple_files=True)
    if files:
        st.markdown(f"<span class='status-ok'>{len(files)} document{'s' if len(files)!=1 else ''} staged</span>", unsafe_allow_html=True)
        if st.button("⚡ Run intelligent extraction", type="primary", use_container_width=True):
            records, issues, evidence, pdfs = [], [], {}, {}
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            staged = {uploaded.name: uploaded.getvalue() for uploaded in files}
            duplicates = existing_hashes([file_hash(data) for data in staged.values()])
            progress = st.progress(0, text="Reading documents…")
            for index, uploaded in enumerate(files, 1):
                data = staged[uploaded.name]
                if file_hash(data) in duplicates:
                    issues.append({"PDF Name": uploaded.name, "Issue": "Duplicate PDF already processed"})
                    progress.progress(index / len(files), text=f"Skipped duplicate {uploaded.name}")
                    continue
                pdfs[uploaded.name] = data
                found, errors, proof = parse_pdf(uploaded.name, data)
                records.extend(found); issues.extend(errors); evidence[uploaded.name] = proof
                progress.progress(index / len(files), text=f"Parsed {uploaded.name}")
            st.session_state.records, st.session_state.issues = records, issues
            st.session_state.evidence, st.session_state.pdfs = evidence, pdfs
            if is_snowflake():
                report = excel_bytes(pd.DataFrame(records).reindex(columns=OUTPUT_COLUMNS), pd.DataFrame(issues))
                save_run(run_id, pdfs, records, issues, report)
            progress.empty(); st.success(f"Extraction complete — {len(records)} claim rows found across {len(files)} PDFs.")
    if st.session_state.records or st.session_state.issues:
        tab1, tab2, tab3 = st.tabs(["Extracted claims", "Exceptions", "Evidence trail"])
        records = pd.DataFrame(st.session_state.records).reindex(columns=OUTPUT_COLUMNS)
        issues = pd.DataFrame(st.session_state.issues)
        with tab1:
            edited = st.data_editor(records, use_container_width=True, hide_index=True, num_rows="dynamic", key="claim_editor")
            st.caption("Review or correct extracted values before export. Changes here are included in the downloaded workbook.")
        with tab2:
            if issues.empty: st.success("No validation exceptions detected.")
            else: st.dataframe(issues, use_container_width=True, hide_index=True)
        with tab3:
            for name, proof in st.session_state.evidence.items():
                with st.expander(f"{name}  ·  {proof['vendor']}"):
                    a,b,c = st.columns(3); a.metric("Pages", proof["pages"]); b.metric("Text characters", f"{proof['characters']:,}"); c.metric("Rule", proof["vendor"])
                    st.code(proof.get("preview") or "No text preview available", language=None)
        payload = excel_bytes(edited, issues)
        st.download_button("↓ Download finance-ready workbook", payload, f"Debit_Note_Extraction_{datetime.now():%Y%m%d_%H%M%S}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

else:
    st.markdown("## Audit lab")
    st.caption("Put the source document beside its extracted evidence. No black boxes.")
    if not st.session_state.pdfs:
        st.info("Process a batch in **Extraction studio** to unlock document-level audit.")
    else:
        selected = st.selectbox("Document", list(st.session_state.pdfs))
        proof = st.session_state.evidence[selected]
        rows = pd.DataFrame([r for r in st.session_state.records if r["PDF Name"] == selected])
        left, right = st.columns([1, 1], gap="large")
        with left:
            st.markdown(f"### Source · {selected}")
            encoded = base64.b64encode(st.session_state.pdfs[selected]).decode()
            st.markdown(f'<iframe src="data:application/pdf;base64,{encoded}" width="100%" height="720" style="border:1px solid #263b5d;border-radius:18px"></iframe>', unsafe_allow_html=True)
        with right:
            st.markdown("### Extraction evidence")
            badge = "status-ok" if not rows.empty else "status-warn"
            st.markdown(f"<span class='{badge}'>{proof['vendor']}</span>", unsafe_allow_html=True)
            st.write("")
            if rows.empty: st.warning("No claim rows were extracted from this document.")
            else:
                for _, row in rows.iterrows():
                    with st.container(border=True):
                        st.markdown(f"**{row['InvoiceRefNo.'] or 'Reference not found'}** · {money(float(row['Total Amount'] or 0))}")
                        st.caption(f"{row['Claim Type']}  •  {row['Month']}  •  {row['Invoice Date']}")
                        st.write(row["Description"] or "No description extracted")
            with st.expander("Raw text evidence"):
                st.code(proof.get("preview") or "No text extracted", language=None)

    history = recent_runs()
    if history:
        st.markdown("### Recent Snowflake runs")
        st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)
