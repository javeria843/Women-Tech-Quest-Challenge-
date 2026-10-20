import os, re, sys, csv, json, time, tempfile
from PIL import Image, ImageOps, ImageEnhance
from read_bill import extract, ask, parse

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
REUSE = "--reuse" in sys.argv
BILLS = ARGS[0] if len(ARGS) > 0 else "../test/bills"
CSVD = ARGS[1] if len(ARGS) > 1 else "../test/csv"
OUT = ARGS[2] if len(ARGS) > 2 else "../output"
PROV = {"KESC": "KE", "LESCO": "LESCO", "IESCO": "IESCO"}
FALLBACK = "The bill does not show enough information to answer this question."
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MON = {m.lower(): i + 1 for i, m in enumerate(MONTHS)}

EMPTY = {"provider": None, "tariff": None, "sanctioned_load_kw": None, "bill_month": None, "reading_date": None, "issue_date": None, "due_date": None, "previous_reading": None, "current_reading": None, "units_consumed": None, "charges": [], "total_charges": None, "taxes": [], "total_taxes": None, "current_bill": None, "arrears": None, "payable_within_due_date": None, "payable_after_due_date": None}

HP = """Find the BILL HISTORY table on this electricity bill (monthly units and bill amounts of previous months).
Write out EVERY row of that table as plain text, one row per line, exactly in this format:
YYYY-MM | UNITS | BILL | PAYMENT
Rules:
- Month as YYYY-MM (AUG 25, Aug-2025 and Aug 2025 all mean 2025-08). Copy the year exactly as printed next to each month.
- The table normally has 12 or 13 monthly rows. It may be split into a left block and a right block: read the left block top to bottom, then the right block top to bottom. Do not skip or merge any row.
- UNITS is only the units column, never the bill amount. BILL is the bill amount column. PAYMENT is the payment column.
- Read each number digit by digit. Keep a minus sign or CR as printed (CR means negative). Write NA for a blank or unreadable cell.
- Output only the rows, then one last line: TOTAL ROWS: n"""

QP = """You answer customer questions about one electricity bill. The bill image is attached, together with the extracted bill details and FACTS. Every number in the FACTS was calculated by code and is correct.
General rules:
- Use only information printed on the bill. Never use outside tariff or rate information. If the bill does not show a needed value, say so plainly instead of guessing.
- For every count, sum, average, difference, percentage, comparison, trend and estimate, copy the numbers from the FACTS exactly. Do not recount or recalculate.
- Write the final answer directly. Never write "Wait", "Actually" or correct yourself inside an answer.
- Write like a helpful person reading the bill. Never mention JSON, null, fields, code or facts.
- Use month names exactly as given in the FACTS. "History table" means the bill's table of previous months. The current bill month is not part of the previous months; mention what the current month used when it helps.
- If fewer than 12 previous months were read, say the answer is based on the months visible in the history table. If no history rows were read, say the history table is not available on this bill image and answer only what does not need it.
- Label every estimate as an estimate and explain how it was calculated.
- If a question has more than one reasonable meaning, state the meaning you used or answer both.
- Amounts are in PKR. Credits (CR) are negative. In net metering bills, negative units or bill amounts are credits or exports.
- Never include names, addresses, CNIC, account, reference, consumer or meter numbers.
- Answer in English, plain language, 2 to 4 sentences, specific and numeric.
Question-specific rules:
- Net metered or standard: check the image for import/export readings, net metering wording, negative units or credits, and net_metering_hints. State your decision and the evidence.
- Highest or lowest month: use highest_previous_month and lowest_previous_month; if the current month is higher than every previous month, say so too.
- FPA or FCA: give the amount from fpa_amounts and the month printed beside the FPA/FCA label on the bill (for example FCA Jul-26). If no month is printed, say so.
- Late payment: give the amount payable by the due date, the amount payable after it, and late_payment_extra_amount. If the bill shows several late amounts, mention each.
- Same month last year: use same_month_last_year exactly, including whether the current month is higher or lower. If that month is not in the table, say so.
- Average: give average_units_previous_months and current_units_minus_average.
- Summer vs winter: use summer_jun_to_aug, winter_dec_to_feb and summer_minus_winter_units. If calendar_months_missing is not empty, say the comparison uses only the months shown.
- Payments: use the payment facts. Say how many months have a payment printed, which were paid in full and which were paid less than billed.
- Last 3 months: use last_3_previous_months, last_3_months_steps and last_3_months_overall, and mention the current month too.
- Next month units: use next_month_units_estimates; give one estimate or a range, labelled as an estimate with the method.
- Spreading cost over the year: use history_average_monthly_bill and history_bills_read_for_months, and say how many months of bills were available. Never treat the current bill as an annual total. If no history bills were read, use the current bill as a one-month figure and say so.
- Same units as last month: "last month" is most_recent_previous_month. Use estimated_bill_if_same_units_as_most_recent_previous_month, explain it as units times the current bill per unit (which includes fixed charges and taxes), and mention the current bill.
- Taxes share: give the tax amount, the current bill amount and the percentage, and list each tax line from the extracted details with its amount. Only say tax lines are not itemized when the extracted taxes list is empty.
- Months above or below some units: name every matching month with its units and state the count.
Return ONLY a JSON object mapping each question id to its answer string."""

BAD = re.compile(r"\b(wait|actually|hold on|let me|hmm|oops)\b|\.\.\.", re.I)


def num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def tonum(s):
    s = str(s).strip().replace(",", "")
    neg = s.upper().endswith("CR") or s.startswith("-")
    m = re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        return None
    v = float(m.group())
    v = -v if neg else v
    return int(v) if v == int(v) else v


def mkey(s):
    m = re.match(r"^(\d{4})-(\d{1,2})$", str(s or "").strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        return None
    return int(m.group(1)) * 12 + int(m.group(2)) - 1


def label(k):
    return "%s %d" % (MONTHS[k % 12], k // 12)


def norm_month(s):
    s = str(s).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})$", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return "%s-%02d" % (m.group(1), int(m.group(2)))
    m = re.match(r"^([A-Za-z]{3})[A-Za-z]*[\s\-/']*(\d{2}|\d{4})$", s)
    if m and m.group(1).lower() in MON:
        y = int(m.group(2))
        if y < 100:
            y += 2000
        return "%d-%02d" % (y, MON[m.group(1).lower()])
    return None


def find(bill_id):
    for f in os.listdir(BILLS):
        if os.path.splitext(f)[0] == bill_id:
            return os.path.join(BILLS, f)
    return None


def retry(fn):
    for i in range(4):
        try:
            return fn()
        except Exception as e:
            print("retry", i + 1, e)
            time.sleep(15)
    return None


# ---------- history table reading ----------

def prep(p, tag, box=None):
    im = Image.open(p).convert("RGB")
    if box:
        w, h = im.size
        im = im.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))
    w, h = im.size
    s = max(1, min(3, round(3000 / max(w, h))))
    if s > 1:
        im = im.resize((w * s, h * s), Image.LANCZOS)
    im = ImageEnhance.Contrast(ImageOps.autocontrast(im)).enhance(1.3)
    q = os.path.join(tempfile.gettempdir(), "wtq_%s.png" % tag)
    im.save(q)
    return q


def parse_history(t):
    rows = []
    for line in str(t).splitlines():
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) >= 3:
            mo = norm_month(c[0])
            if mo:
                rows.append({"month": mo, "units": tonum(c[1]), "bill": tonum(c[2]),
                             "payment": tonum(c[3]) if len(c) > 3 else None})
    return rows


HPLAN = [("full", None, True), ("orig", None, False), ("top", (0, 0, 1, 0.6), True),
         ("bottom", (0, 0.4, 1, 1), True), ("full2", None, True)]


def history(p):
    best = []
    for i, (tag, box, big) in enumerate(HPLAN):
        try:
            src = prep(p, tag, box) if big else p
        except Exception as e:
            print("image prep failed:", e)
            src = p
        hint = ""
        if box:
            hint += "\nThis image is only a part of the bill. Read the history table if it is visible here."
        if i > 0:
            hint += "\nAttempt %d: earlier readings missed or misread rows. List all months of the table." % (i + 1)
        try:
            t = ask(src, HP + hint, js=False)
        except Exception as e:
            print("history call failed:", e)
            time.sleep(10)
            continue
        rows = parse_history(t)
        if len({r["month"] for r in rows}) > len({r["month"] for r in best}):
            best = rows
        if len({r["month"] for r in best}) >= 12:
            break
        time.sleep(3)
    return best


# ---------- facts computed in Python ----------

def split_history(h, d):
    """Sort rows by month, detect whether the last row is the current month
    (K-Electric tables include it) and fix a whole-year label shift."""
    rows, seen = [], set()
    for x in h:
        k = mkey(x.get("month"))
        if k is None or k in seen:
            continue
        seen.add(k)
        r = dict(x)
        r["k"] = k
        rows.append(r)
    rows.sort(key=lambda r: r["k"])
    bm = mkey(d.get("bill_month"))
    cu = d.get("units_consumed")
    cur, note = None, None
    if rows:
        last = rows[-1]
        consecutive = last["k"] - rows[0]["k"] == len(rows) - 1
        same_units = num(cu) and last.get("units") == cu
        if bm is not None and last["k"] == bm:
            cur = rows.pop()
        elif (bm is not None and (same_units or len(rows) >= 13) and consecutive
              and bm > last["k"] and (bm - last["k"]) % 12 == 0):
            gap = bm - last["k"]
            for r in rows:
                r["k"] += gap
            cur = rows.pop()
            note = "year labels shifted by %d year(s) so the last row matches the current bill month" % (gap // 12)
    return rows, cur, bm, note


def facts(d, h):
    rows, cur, bm, note = split_history(h, d)
    cu, cb = d.get("units_consumed"), d.get("current_bill")
    tt, tc = d.get("total_taxes"), d.get("total_charges")
    f = {"current_bill_month": label(bm) if bm is not None else None, "current_month_units": cu}
    if note:
        f["note"] = note
    f["history_table_oldest_first"] = [
        {"month": label(r["k"]), "units": r.get("units"), "bill": r.get("bill"), "payment": r.get("payment")}
        for r in rows]
    f["history_previous_months_read"] = len(rows)
    f["history_last_row_was_the_current_month"] = cur is not None

    ur = [r for r in rows if num(r.get("units"))]
    avg = None
    if ur:
        u = [r["units"] for r in ur]
        avg = round(sum(u) / len(u), 2)
        f["average_units_previous_months"] = avg
        hi = max(ur, key=lambda r: r["units"])
        lo = min(ur, key=lambda r: r["units"])
        f["highest_previous_month"] = {"month": label(hi["k"]), "units": hi["units"]}
        f["lowest_previous_month"] = {"month": label(lo["k"]), "units": lo["units"]}
        if num(cu) and cu > hi["units"]:
            f["current_month_is_higher_than_every_previous_month"] = True
        for t in (100, 200, 300, 500, 1000):
            m = ["%s (%s)" % (label(r["k"]), r["units"]) for r in ur if r["units"] > t]
            f["previous_months_above_%d_units" % t] = {"count": len(m), "months": m}
        if num(cu):
            diff = round(cu - avg, 2)
            f["current_units_minus_average"] = {
                "difference": diff, "current_is": "higher" if diff > 0 else "lower" if diff < 0 else "equal"}

        def season(ms):
            got = [r for r in ur if r["k"] % 12 + 1 in ms]
            missing = [MONTHS[m - 1] for m in ms if not any(r["k"] % 12 + 1 == m for r in got)]
            return {"months": ["%s (%s)" % (label(r["k"]), r["units"]) for r in got],
                    "total_units": sum(r["units"] for r in got), "calendar_months_missing": missing}

        s, w = season((6, 7, 8)), season((12, 1, 2))
        f["summer_jun_to_aug"], f["winter_dec_to_feb"] = s, w
        f["summer_minus_winter_units"] = s["total_units"] - w["total_units"]
        f["season_comparison_is_complete"] = not s["calendar_months_missing"] and not w["calendar_months_missing"]

        l3 = ur[-3:]
        steps = []
        for a, b in zip(l3, l3[1:]):
            dd = b["units"] - a["units"]
            steps.append("%s to %s: %s by %s" % (label(a["k"]), label(b["k"]),
                                                 "up" if dd > 0 else "down" if dd < 0 else "unchanged", abs(dd)))
        f["last_3_previous_months"] = ["%s (%s)" % (label(r["k"]), r["units"]) for r in l3]
        f["last_3_months_steps"] = steps
        if len(l3) == 3:
            a, b, c = (r["units"] for r in l3)
            f["last_3_months_overall"] = ("rising" if a < b < c else "falling" if a > b > c
                                          else "up then down" if b > a and b > c
                                          else "down then up" if b < a and b < c else "mostly flat")
        if num(cu):
            last = ur[-1]
            f["current_month_vs_most_recent_previous_month"] = {
                "current_units": cu, "previous_units": last["units"],
                "current_is": "higher" if cu > last["units"] else "lower" if cu < last["units"] else "equal"}

    if bm is not None:
        t = bm - 12
        m = next((r for r in rows if r["k"] == t), None)
        if m and num(m.get("units")) and num(cu):
            diff = cu - m["units"]
            f["same_month_last_year"] = {"month": label(t), "units_then": m["units"], "units_now": cu,
                                         "difference": diff,
                                         "now_is": "higher" if diff > 0 else "lower" if diff < 0 else "equal"}
        else:
            f["same_month_last_year"] = "%s is not in the history table, so it cannot be compared" % label(t)

    est = {}
    if avg is not None:
        est["average_of_previous_months"] = avg
    if len(ur) >= 3:
        est["average_of_last_3_previous_months"] = round(sum(r["units"] for r in ur[-3:]) / 3, 2)
    if bm is not None:
        m = next((r for r in ur if r["k"] == bm - 11), None)
        if m:
            est["same_month_last_year_%s" % label(bm - 11)] = m["units"]
    f["next_month_units_estimates"] = est

    pr = [r for r in rows if num(r.get("payment"))]
    f["payments_printed_for_months"] = len(pr)
    f["months_without_payment_value"] = len(rows) - len(pr)
    full, short = [], []
    for r in pr:
        b, pm = r.get("bill"), r["payment"]
        if num(b) and b > 0:
            if pm >= b - 1:
                full.append("%s (bill %s, paid %s)" % (label(r["k"]), b, pm))
            else:
                short.append("%s (bill %s, paid %s)" % (label(r["k"]), b, pm))
    f["months_paid_in_full"] = full
    f["months_paid_less_than_bill"] = short

    br = [r for r in rows if num(r.get("bill"))]
    f["history_bills_read_for_months"] = len(br)
    if br:
        tot = round(sum(r["bill"] for r in br), 2)
        f["history_bills_total"] = tot
        f["history_average_monthly_bill"] = round(tot / len(br), 2)
        if num(cb):
            f["average_monthly_bill_including_current_bill"] = round((tot + cb) / (len(br) + 1), 2)

    per_unit = round(cb / cu, 2) if num(cb) and num(cu) and cu else None
    if per_unit:
        f["current_bill_per_unit"] = per_unit
    if ur and per_unit:
        last = ur[-1]
        f["most_recent_previous_month"] = {"month": label(last["k"]), "units": last["units"], "bill": last.get("bill")}
        if last["units"] > 0:
            f["estimated_bill_if_same_units_as_most_recent_previous_month"] = round(last["units"] * per_unit)

    pw, pa = d.get("payable_within_due_date"), d.get("payable_after_due_date")
    if num(pw) and num(pa):
        f["late_payment_extra_amount"] = round(pa - pw, 2)
        if pw:
            f["late_payment_extra_percent"] = round((pa - pw) / pw * 100, 2)

    fp = [x["amount"] for x in d.get("charges") or []
          if isinstance(x, dict) and x.get("type") == "fpa" and num(x.get("amount"))]
    if fp:
        f["fpa_amounts"] = fp

    f["net_metering_hints"] = {
        "history_months_with_negative_units": [label(r["k"]) for r in rows if num(r.get("units")) and r["units"] < 0],
        "history_months_with_negative_bill": [label(r["k"]) for r in rows if num(r.get("bill")) and r["bill"] < 0]}

    if num(cb) and cb:
        if num(tt):
            f["taxes_percent_of_current_bill"] = round(tt / cb * 100, 2)
        if num(tc):
            f["charges_percent_of_current_bill"] = round(tc / cb * 100, 2)
        lines = {}
        for k in ("charges", "taxes"):
            for x in d.get(k) or []:
                if isinstance(x, dict) and num(x.get("amount")):
                    lines.setdefault("%s:%s" % (k, x.get("type")), []).append(round(x["amount"] / cb * 100, 2))
        f["line_percent_of_current_bill"] = lines
    return f


# ---------- answers ----------

def clean(p, a, q):
    if not BAD.search(a):
        return a
    try:
        t = str(ask(p, "Rewrite the answer below as a clean final answer to the question. Keep the correct conclusion "
                       "and the numbers, remove any self-correction or hesitation, 2 to 4 sentences. "
                       "Return only the rewritten answer, no JSON.\nQuestion: %s\nAnswer: %s" % (q, a),
                    js=False)).strip()
        if t and not BAD.search(t):
            return t
    except Exception as e:
        print("clean failed:", e)
    ms = list(re.finditer(r"(?i)\b(wait|actually)\b[,.:!]*\s*", a))
    if ms:
        a = a[ms[-1].end():].strip()
        a = a[:1].upper() + a[1:]
    return a.replace("...", ".")


def answer(p, d, f, qs):
    ans = {}
    for _ in range(2):
        todo = [r for r in qs if not str(ans.get(r["question_id"]) or "").strip()]
        if not todo:
            break
        qt = "\n".join("%s: %s" % (r["question_id"], r["question"]) for r in todo)
        prompt = (QP + "\n\nEXTRACTED BILL DETAILS:\n" + json.dumps(d)
                  + "\n\nFACTS:\n" + json.dumps(f) + "\n\nQUESTIONS:\n" + qt)
        res = retry(lambda: parse(ask(p, prompt)))
        if isinstance(res, dict):
            for k, v in res.items():
                if v is not None and str(v).strip():
                    ans[str(k)] = str(v).strip()
    return ans


# ---------- csv ----------

def read_rows(name):
    with open(os.path.join(CSVD, name), encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        return rd.fieldnames, list(rd)


def write_rows(name, fields, rows):
    with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def load_level1():
    path = os.path.join(OUT, "level1.csv")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        data = {}
        for r in rows:
            d = json.loads(r.get("json") or "")
            if not isinstance(d, dict) or not d:
                return None
            data[r["bill_id"]] = d
        return data or None
    except Exception:
        return None


def level1():
    fields, rows = read_rows("level1.csv")
    old = load_level1() if REUSE else None
    if old and all(r["bill_id"] in old for r in rows):
        for r in rows:
            r["json"] = json.dumps(old[r["bill_id"]], ensure_ascii=False, separators=(",", ":"))
        write_rows("level1.csv", fields, rows)
        print("level1 reused from", OUT)
        return old
    data = {}
    for r in rows:
        p = find(r["bill_id"])
        d = retry(lambda: extract(p)) if p else None
        if not isinstance(d, dict):
            d = dict(EMPTY)
            d["provider"] = PROV.get(r["bill_id"].split("_")[0].upper())
        data[r["bill_id"]] = d
        r["json"] = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
        print(r["bill_id"], "level1 done")
        time.sleep(3)
    write_rows("level1.csv", fields, rows)
    return data


def level2(data):
    fields, rows = read_rows("level2.csv")
    for bid in dict.fromkeys(r["bill_id"] for r in rows):
        qs = [r for r in rows if r["bill_id"] == bid]
        p = find(bid)
        ans = {}
        try:
            if p:
                d = data.get(bid) or {}
                h = retry(lambda: history(p)) or []
                f = facts(d, h)
                print(bid, "history rows read:", len(h), "| previous months used:", f["history_previous_months_read"],
                      "| last row is current month:", f["history_last_row_was_the_current_month"])
                if f.get("note"):
                    print("   note:", f["note"])
                for x in f["history_table_oldest_first"]:
                    print("   ", x["month"], x["units"], x["bill"], x["payment"])
                time.sleep(3)
                ans = answer(p, d, f, qs)
        except Exception as e:
            print(bid, "level2 error:", e)
        for r in qs:
            a = str(ans.get(r["question_id"]) or "").strip()
            r["answer"] = clean(p, a, r["question"]) if a and p else FALLBACK
        print(bid, "level2 done")
        time.sleep(3)
    write_rows("level2.csv", fields, rows)


def main():
    os.makedirs(OUT, exist_ok=True)
    data = level1()
    level2(data)
    print("finished")


if __name__ == "__main__":
    main()