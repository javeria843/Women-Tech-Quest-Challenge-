import os, sys, io, csv, json, html, hashlib, tempfile, subprocess

import pandas as pd
import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

st.set_page_config(page_title="Utility Bill Decoder", page_icon="⚡", layout="wide")

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@400;600&display=swap');
    .block-container {padding-top: 1.6rem; max-width: 1250px;}
    .hero {background: linear-gradient(120deg, #4f46e5 0%, #7c3aed 55%, #db2777 100%);
           padding: 1.4rem 1.8rem; border-radius: 16px; color: white; margin-bottom: 1.2rem;}
    .hero h1 {margin: 0; font-size: 2rem; color: white;}
    .hero p {margin: .3rem 0 0 0; opacity: .92; font-size: 1.02rem;}
    div[data-testid="stMetric"] {background: rgba(124,58,237,.08); border: 1px solid rgba(124,58,237,.25);
           padding: .8rem 1rem; border-radius: 12px;}
    .answer-card {border-left: 4px solid #7c3aed; background: rgba(124,58,237,.07);
           padding: .8rem 1rem; border-radius: 8px; margin-bottom: .8rem;
           font-family: 'Noto Naskh Arabic', 'Segoe UI', sans-serif; line-height: 1.9; font-size: 1.05rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

try:
    import solve
    from read_bill import extract
except Exception as e:  # missing file, missing package, bad .env
    st.error("Could not load the project code: %s" % e)
    st.stop()

QUESTIONS = [
    "Is this a net-metered (solar) connection or a standard connection? How did you tell?",
    "Which month in the last 12 months had the highest units consumed?",
    "How much fuel adjustment am I being charged (shown as FPA or FCA on the bill), and for which month?",
    "If I don't pay on time, what late payment surcharges will be added?",
    "Am I consuming more units than in the same month last year?",
    "What are my average monthly units consumed, and how does this month compare?",
    "How many more units do I consume in summer (June-August) than in winter (December-February)?",
    "Looking at the payments for the 12 months shown on the bill, have I been paying every bill?",
    "Have my units consumed gone up or down over the last 3 months?",
    "Based on the 12 months trend, how many units should I expect to consume next month?",
    "If I wanted to spread my electricity cost evenly across the year, how much should I set aside each month?",
    "If I consume the same units as last month, what would my bill be roughly?",
]
SAMPLE_DIRS = [os.path.join(HERE, "..", "test", "bills"), os.path.join(HERE, "..", "train", "bills")]


def pk(v):
    return "PKR {:,.2f}".format(v) if solve.num(v) else "—"


def show(v):
    return "—" if v in (None, "") else str(v)


LANG_RULES = {
    "English": "Answer in English.",
    "اردو (Urdu)": "Answer in Urdu (اردو script) using simple everyday words. Keep numbers, PKR amounts and month names exactly as in the facts.",
    "English + Urdu": "Answer in English first, then repeat the same answer in Urdu (اردو script) after a blank line. Start the English part with 'English:' and the Urdu part with 'اردو:'.",
}
EXTRA_RULES = """- The question may be written in English, Urdu or Roman Urdu (for example "tax kitna hai?"). Understand it and answer the same question.
- If the question is not covered by the rules above, answer it from the extracted details, the FACTS and the bill image.
- If the question is not about this bill, politely say you can only answer questions about this bill.
- Use plain words for line types (for example "sales tax" or "other tax"), never code names like other_tax."""


def answer_ui(path, d, f, qs, lang):
    """Same idea as solve.answer, but the answer language can be chosen."""
    old = "Answer in English, plain language, 2 to 4 sentences, specific and numeric."
    new = LANG_RULES[lang] + " Plain language, 2 to 4 sentences, specific and numeric."
    qp = solve.QP.replace(old, new) if old in solve.QP else solve.QP + "\n- " + new
    qp = qp.replace("Return ONLY a JSON object", EXTRA_RULES + "\nReturn ONLY a JSON object", 1)
    ans = {}
    for _ in range(2):
        todo = [r for r in qs if not str(ans.get(r["question_id"]) or "").strip()]
        if not todo:
            break
        qt = "\n".join("%s: %s" % (r["question_id"], r["question"]) for r in todo)
        prompt = (qp + "\n\nEXTRACTED BILL DETAILS:\n" + json.dumps(d, ensure_ascii=False)
                  + "\n\nFACTS:\n" + json.dumps(f, ensure_ascii=False) + "\n\nQUESTIONS:\n" + qt)
        res = solve.retry(lambda: solve.parse(solve.ask(path, prompt)))
        if isinstance(res, dict):
            for k, v in res.items():
                if v is not None and str(v).strip():
                    ans[str(k)] = str(v).strip()
    return ans


def answer_html(text):
    parts = [p.strip() for p in str(text).replace("\\n", "\n").split("\n") if p.strip()]
    return "".join('<div dir="auto">%s</div>' % html.escape(p) for p in parts)


def current_bill():
    """Return (key, path, name) for the uploaded or selected bill, or None."""
    up = st.session_state.get("uploaded")
    if up is not None:
        data = up.getvalue()
        key = hashlib.md5(data).hexdigest()
        path = os.path.join(tempfile.gettempdir(), "wtq_ui_%s%s" % (key, os.path.splitext(up.name)[1] or ".png"))
        if not os.path.exists(path):
            with open(path, "wb") as fh:
                fh.write(data)
        return key, path, up.name
    sample = st.session_state.get("sample")
    if sample and sample != "—":
        for folder in SAMPLE_DIRS:
            path = os.path.abspath(os.path.join(folder, sample))
            if os.path.exists(path):
                return path, path, sample
    return None


def store_for(key):
    return st.session_state.setdefault("store", {}).setdefault(key, {"d": None, "h": None, "f": None, "answers": {}})


def run_extract(path, st_):
    with st.spinner("Reading the bill (Level 1)..."):
        d = solve.retry(lambda: extract(path))
    if not isinstance(d, dict):
        st.error("Could not extract this bill. Check your API key and internet connection, then try again.")
        return
    st_["d"], st_["h"], st_["f"], st_["answers"] = d, None, None, {}


def run_history(path, st_):
    with st.spinner("Reading the bill history table (this takes about a minute)..."):
        h = solve.retry(lambda: solve.history(path)) or []
    st_["h"] = h
    st_["f"] = solve.facts(st_["d"], h)


# ---------------- sidebar ----------------
with st.sidebar:
    st.header("1. Choose a bill")
    st.file_uploader("Upload a bill image", type=["png", "jpg", "jpeg"], key="uploaded")
    names = []
    for folder in SAMPLE_DIRS:
        if os.path.isdir(folder):
            names += sorted(f for f in os.listdir(folder) if f.lower().endswith((".png", ".jpg", ".jpeg")))
    if names:
        st.selectbox("...or pick a sample bill", ["—"] + names, key="sample")
    st.divider()
    st.caption("Model: **%s** (%s)" % (os.getenv("MODEL_NAME", "not set"), os.getenv("MODEL_PROVIDER", "not set")))
    st.caption("All counts, averages and percentages are calculated in Python. The model only reads the bill and writes the wording.")

st.markdown(
    '<div class="hero"><h1>⚡ Utility Bill Decoder</h1>'
    "<p>Upload a K-Electric, LESCO or IESCO bill. Get its details and plain-English answers to your questions.</p></div>",
    unsafe_allow_html=True,
)

cur = current_bill()
tab1, tab2, tab3 = st.tabs(["📄 Level 1: Bill details", "💬 Level 2: Ask questions", "📦 Batch: generate CSV files"])

# ---------------- Level 1 ----------------
with tab1:
    if not cur:
        st.info("Upload a bill image or pick a sample in the sidebar to begin.")
    else:
        key, path, name = cur
        S = store_for(key)
        left, right = st.columns([1, 1.25], gap="large")
        with left:
            st.image(path, caption=name)
        with right:
            if st.button("🔍 Extract bill details", type="primary"):
                run_extract(path, S)
            d = S["d"]
            if not d:
                st.write("Press the button to read this bill.")
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("Provider", show(d.get("provider")))
                c2.metric("Tariff", show(d.get("tariff")))
                c3.metric("Bill month", show(d.get("bill_month")))
                c1, c2, c3 = st.columns(3)
                c1.metric("Units consumed", show(d.get("units_consumed")))
                c2.metric("Sanctioned load (kW)", show(d.get("sanctioned_load_kw")))
                c3.metric("Meter reading", "%s → %s" % (show(d.get("previous_reading")), show(d.get("current_reading"))))
                c1, c2, c3 = st.columns(3)
                c1.metric("Reading date", show(d.get("reading_date")))
                c2.metric("Issue date", show(d.get("issue_date")))
                c3.metric("Due date", show(d.get("due_date")))
                st.divider()
                c1, c2 = st.columns(2)
                c1.metric("Current bill", pk(d.get("current_bill")))
                c2.metric("Arrears", pk(d.get("arrears")))
                c1.metric("Payable by due date", pk(d.get("payable_within_due_date")))
                c2.metric("Payable after due date", pk(d.get("payable_after_due_date")))

        d = S["d"]
        if d:
            st.divider()
            a, b = st.columns(2, gap="large")
            with a:
                st.subheader("Charges")
                if d.get("charges"):
                    st.dataframe(pd.DataFrame(d["charges"]), hide_index=True)
                else:
                    st.caption("No charge lines.")
                st.metric("Total charges", pk(d.get("total_charges")))
            with b:
                st.subheader("Taxes")
                if d.get("taxes"):
                    st.dataframe(pd.DataFrame(d["taxes"]), hide_index=True)
                else:
                    st.caption("No separate tax lines are printed on this bill.")
                st.metric("Total taxes", pk(d.get("total_taxes")))
            with st.expander("Raw Level 1 JSON"):
                st.json(d)
            st.download_button("⬇ Download JSON", json.dumps(d, indent=2), file_name="%s.json" % os.path.splitext(name)[0],
                               mime="application/json")

# ---------------- Level 2 ----------------
with tab2:
    if not cur:
        st.info("Choose a bill in the sidebar first.")
    else:
        key, path, name = cur
        S = store_for(key)
        if not S["d"]:
            st.warning("First extract the bill on the Level 1 tab.")
            if st.button("Extract now", key="extract_l2"):
                run_extract(path, S)
                st.rerun()
        else:
            if st.button("📊 Read the history table", type="primary" if not S["f"] else "secondary"):
                run_history(path, S)
            f = S["f"]
            if not f:
                st.write("Read the bill history table first. The answers use it for averages, trends and comparisons.")
            else:
                hist = f["history_table_oldest_first"]
                if f.get("note"):
                    st.caption("Note: %s." % f["note"])
                if hist:
                    df = pd.DataFrame(hist)
                    left, right = st.columns([1.3, 1], gap="large")
                    with left:
                        st.subheader("Units by month")
                        st.bar_chart(df.set_index("month")["units"])
                    with right:
                        st.subheader("History table")
                        st.dataframe(df, hide_index=True, height=300)
                else:
                    st.warning("No history rows could be read from this bill image. Some answers will say the data is not available.")

                st.divider()
                st.subheader("Ask about this bill")
                lang = st.radio("Answer language", list(LANG_RULES), horizontal=True)
                picked = st.multiselect("Choose questions", QUESTIONS, default=QUESTIONS[:3])
                if st.checkbox("Select all 12 questions"):
                    picked = list(QUESTIONS)
                custom = st.text_area("...or ask anything about this bill (English, Urdu or Roman Urdu)",
                                      placeholder="e.g. isme tax kitna hai?  /  میرا بل اتنا زیادہ کیوں ہے؟", height=80)
                qlist = picked + ([custom.strip()] if custom.strip() else [])
                if st.button("💬 Get answers", disabled=not qlist):
                    qs = [{"question_id": "Q%d" % (i + 1), "question": q} for i, q in enumerate(qlist)]
                    with st.spinner("Writing answers..."):
                        ans = answer_ui(path, S["d"], f, qs, lang)
                        for r in qs:
                            a = str(ans.get(r["question_id"]) or "").strip()
                            S["answers"][r["question"] + "||" + lang] = solve.clean(path, a, r["question"]) if a else solve.FALLBACK
                for q in qlist:
                    a = S["answers"].get(q + "||" + lang)
                    if a:
                        st.markdown("**%s**" % q)
                        st.markdown('<div class="answer-card">%s</div>' % answer_html(a), unsafe_allow_html=True)
                with st.expander("Numbers calculated by code (facts given to the model)"):
                    st.json(f)

# ---------------- Batch ----------------
with tab3:
    st.write("Runs `solve.py` on a folder of bills and writes `level1.csv` and `level2.csv`. "
             "The default output folder is `output_demo`, so your real `output` folder is not touched.")
    c1, c2, c3 = st.columns(3)
    bills = c1.text_input("Bills folder", "../test/bills")
    csvd = c2.text_input("CSV templates folder", "../test/csv")
    outd = c3.text_input("Output folder", "../output_demo")
    reuse = st.checkbox("Reuse existing Level 1 results in the output folder (faster)")
    if st.button("▶ Generate CSV files", type="primary"):
        cmd = [sys.executable, "-u", "solve.py", bills, csvd, outd] + (["--reuse"] if reuse else [])
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        box, lines = st.empty(), []
        with st.spinner("Running... this takes several minutes."):
            proc = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", env=env)
            for line in proc.stdout:
                lines.append(line.rstrip())
                box.code("\n".join(lines[-18:]), language="text")
            proc.wait()
        if proc.returncode == 0:
            st.success("Finished.")
        else:
            st.error("The run stopped with an error. Read the log above.")
    for fname in ("level1.csv", "level2.csv"):
        fp = os.path.abspath(os.path.join(HERE, outd, fname))
        if os.path.exists(fp):
            with open(fp, encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            st.subheader("%s (%d rows)" % (fname, len(rows)))
            st.dataframe(pd.DataFrame(rows), hide_index=True, height=260)
            with open(fp, "rb") as fh:
                st.download_button("⬇ Download %s" % fname, fh.read(), file_name=fname, mime="text/csv", key="dl_" + fname)
            
