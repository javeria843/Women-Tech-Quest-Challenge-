import os, sys, base64, json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
P = os.getenv("MODEL_PROVIDER")
M = os.getenv("MODEL_NAME")
K = os.getenv("API_KEY")

PROMPT = """You are reading a Pakistani electricity bill (K-Electric, LESCO or IESCO) from a scanned image, screenshot or photo. The bill may be tilted, partly in Urdu, and names, addresses and ID numbers are covered with grey boxes.
Return ONLY one JSON object, no markdown and no explanation. Include every field below, even when its value is null.

{
"provider": "KE | LESCO | IESCO",
"tariff": string,
"sanctioned_load_kw": number,
"bill_month": "YYYY-MM",
"reading_date": "YYYY-MM-DD",
"issue_date": "YYYY-MM-DD",
"due_date": "YYYY-MM-DD",
"previous_reading": number,
"current_reading": number,
"units_consumed": number,
"charges": [{"type": "energy | fixed | fpa | quarterly_adjustment | surcharge | meter_rent | subsidy | other", "amount": number}],
"total_charges": number,
"taxes": [{"type": "gst | electricity_duty | income_tax | municipal_tax | other_tax", "amount": number}],
"total_taxes": number,
"current_bill": number,
"arrears": number,
"payable_within_due_date": number,
"payable_after_due_date": number
}

GENERAL RULES
- Extract values exactly as printed. Never recalculate, round or correct any amount.
- All amounts are numbers in PKR. Remove currency labels and commas: Rs. 19,964 becomes 19964.
- Credits and subsidies are negative: 38209CR becomes -38209. Subsidy lines are recorded as negative amounts.
- Dates: 22nd Sep 2026 becomes 2026-09-22. Month: MAR 2026 becomes 2026-03.
- Use null for any value that is not printed, blank, covered by a grey box or unreadable. Do not guess. Use [] for an empty list.
- Never return names, addresses, CNIC, account numbers, reference numbers, consumer numbers or meter numbers.
- Use numbers for numeric fields, never quoted strings.

FIELD RULES
- provider: KE for K-Electric (KESC), LESCO for Lahore, IESCO for Islamabad.
- tariff: the tariff code exactly as printed, such as A1-R or A-1a(01).
- sanctioned_load_kw: the sanctioned load in kW, labelled Sanc Load, S.Load or San Load.
- bill_month: the billing month in YYYY-MM.
- reading_date, issue_date, due_date: the printed dates. Look carefully for reading_date and issue_date; they are printed on the bill (often near the meter readings or the billing month), return them instead of null if visible. The due date is the last date to pay without late payment surcharge.
- previous_reading, current_reading: the printed meter readings.
- units_consumed: the units billed for the current month, as printed.
- charges: one entry per line in the charges section of the bill. Keep every printed charge line as a separate entry; never merge lines, even if two have the same type (for example two energy lines). Skip blank lines and lines printed as 0.
- total_charges: the printed charges subtotal, or null if no subtotal is printed.
- taxes: one entry per line in the tax or government section of the bill. The printed section decides whether a line belongs in charges or taxes, not its name. If only one combined tax amount is printed, return [] for taxes and put that amount in total_taxes.
- total_taxes: the printed tax subtotal, or null if none is printed.
- current_bill: the printed amount for this month's bill.
- arrears: previous dues carried forward. Keep the printed sign (credit is negative). Null if blank.
- payable_within_due_date: the amount payable by the due date.
- payable_after_due_date: the amount payable after the due date. If several late-payment amounts are printed, use the highest one. If the bill says "NOT TO BE PAID" or shows no amount, return null.

CHARGE TYPES (for charges)
- energy: cost of units used, such as Variable Upto ... Units, Cost of Electricity, Energy Charges.
- fixed: fixed charges.
- fpa: fuel price adjustment. Use fpa for both FPA and FCA.
- quarterly_adjustment: quarterly tariff adjustment such as QTA or Uniform Quarterly Adjustment.
- surcharge: surcharges such as Additional Surcharge (PHL) or F.C Surcharge.
- meter_rent: meter rent or service rent.
- subsidy: government subsidy or relief lines, amount negative.
- other: any other charge line.

TAX TYPES (for taxes)
- gst: sales tax or GST, including GST on FPA.
- electricity_duty: electricity duty, labelled ED, E.D, Elec Duty or Electricity Duty. The small tax line named "duty" or "ED" (usually the smallest amount, listed before sales tax) is electricity_duty, never other_tax.
- income_tax: income tax.
- municipal_tax: municipal or local charges printed under taxes, such as KE MUCT (KMC).
- other_tax: use only for a tax line that clearly fits none of the types above, such as extra tax, further tax or TV fee. Never use other_tax for duty, ED, sales tax, GST or MUCT lines.
"""

TRANSCRIBE = """Transcribe this electricity bill exactly as printed, as plain text, one row per line:
1. every line of the charges section with its amount (do not merge or skip any row)
2. every line of the taxes section with its amount
3. the charges subtotal, taxes subtotal, current bill, arrears, amount payable within due date, and EVERY amount payable after due date
4. every date printed anywhere on the bill with its label
5. previous reading, current reading, units, sanctioned load, tariff
Do not summarize. Do not include names, addresses or ID numbers."""

PROVIDERS = {"KESC": "KE", "LESCO": "LESCO", "IESCO": "IESCO"}

def ask(path, prompt=PROMPT, js=True):
    img = open(path, "rb").read()
    mime = "image/png" if path.lower().endswith("png") else "image/jpeg"
    b64 = base64.b64encode(img).decode()
    if P == "google":
        from google import genai
        from google.genai import types
        c = genai.Client(api_key=K)
        cfg = types.GenerateContentConfig(temperature=0, response_mime_type="application/json" if js else "text/plain")
        r = c.models.generate_content(model=M, contents=[types.Part.from_bytes(data=img, mime_type=mime), prompt], config=cfg)
        return r.text
    if P == "anthropic":
        import anthropic
        c = anthropic.Anthropic(api_key=K)
        r = c.messages.create(model=M, max_tokens=3000, temperature=0, messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
            {"type": "text", "text": prompt}]}])
        return r.content[0].text
    from openai import OpenAI
    c = OpenAI(api_key=K)
    r = c.chat.completions.create(model=M, messages=[{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}])
    return r.choices[0].message.content

def parse(text):
    t = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(t)

def fix_date(s):
    if not s:
        return None
    for f in ("%Y-%m-%d", "%d-%b-%y", "%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d %b %Y", "%d-%B-%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s.strip(), f).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s

def extract(path):
    notes = ask(path, TRANSCRIBE, js=False)
    d = parse(ask(path, PROMPT + "\n\nTRANSCRIPT OF THE BILL (use it together with the image; the printed values here are the truth):\n" + notes))
    for k in ("reading_date", "issue_date", "due_date"):
        d[k] = fix_date(d.get(k))
    prefix = os.path.basename(path).split("_")[0].upper()
    d["provider"] = PROVIDERS.get(prefix, d.get("provider"))
    return d

if __name__ == "__main__":
    print(json.dumps(extract(sys.argv[1]), indent=2))