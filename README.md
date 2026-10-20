# ⚡ Utility Bill Decoder

An AI assistant that reads a Pakistani electricity bill (K-Electric, LESCO or IESCO) from a photo and explains it in plain language.

I built it for the **Build with AI** track of **Women Tech Quest 2026** (hosted by 10Pearls). The brief was simple to say and hard to do: electricity bills are confusing, every provider lays them out differently, and the photos are often tilted, small, or partly in Urdu. Can an AI read one and answer a customer's questions correctly?

This repo contains the full solution: the batch pipeline that produced my competition results, and a small Streamlit app to try it on any bill.

---

## What it does

**Level 1: Extraction.** Give it a bill image and it returns one JSON object with the bill's details: provider, tariff, dates, meter readings, units, every charge line, every tax line, the totals, arrears, and the amounts payable before and after the due date. Values that are not printed, or are covered by a grey box, come back as `null` instead of a guess.

**Level 2: Questions.** Ask things like:

- *How much of my bill is taxes?*
- *Which month had the highest units?*
- *Am I using more than in the same month last year?*
- *If I use the same units as last month, what will my bill roughly be?*

You get a short answer grounded in the bill, with the numbers shown. In the app you can ask your own question too, in English, Urdu or Roman Urdu, and choose whether the answer comes back in English, Urdu, or both.

A real example from a sample K-Electric bill:

> **How much of my bill is taxes?**
> Total taxes amount to PKR 569.62, which is 16.61% of the current bill amount of PKR 3430.24. The individual tax lines are electricity duty of PKR 29.41, GST of PKR 520.21, and municipal tax of PKR 20.0.

> **How many months in my history went above 200 units?**
> There are 2 months in the history table that went above 200 units: 2025-07 with 239 units and 2025-08 with 203 units.

---

## The idea that made it work

My first version asked the model everything in one go, including the counting and the percentages. It got things wrong in small, annoying ways: it miscounted months, mixed up which month came last, and once corrected itself in the middle of an answer.

So I split the job:

| Step | Who does it |
|------|-------------|
| Reading the bill image and the history table | The model (vision) |
| Counts, sums, averages, percentages, trends, estimates | **Python** |
| Writing the final answer in plain words | The model, using the numbers Python calculated |

The model never does arithmetic. It reads and explains. That one decision removed most of the wrong answers.

## How it works

```
bill image
   │
   ├─► Level 1: model extracts the bill fields ──► JSON
   │
   └─► Level 2
         1. Enlarge the image and read the history table as plain text rows
            (retry with the original image and zoomed crops if rows are missing)
         2. Clean the rows in Python: sort by month, find the current month,
            fix year labels that were read wrongly
         3. Calculate the facts in Python: averages, highest month, same month
            last year, summer vs winter, last 3 months, payments, yearly average,
            late payment amount, tax percentage, bill estimates
         4. Ask the model to answer all questions for that bill in ONE call,
            using only the bill and those facts
         5. Remove any self-corrections ("Wait...") and fill gaps with a safe fallback
```

Some details that turned out to matter:

- **K-Electric tables include the current month** as the last row, LESCO and IESCO tables don't. The code detects this so "previous months" is counted correctly.
- **Tables are read as plain text first**, then parsed by code. This was much more reliable than asking the model for JSON straight from the image.
- **Net metering bills** have negative units (exports). The code keeps the sign, so they are never counted as "above 200 units".
- **One call per bill for all 12 questions**, which keeps the run fast and well inside free-tier rate limits.
- **Retries and fallbacks.** API errors like `503 high demand` are retried. If something still fails, the answer cell gets a clear fallback line instead of being left empty, and one bad bill never stops the whole run.

---

## Project structure

```
source/
├── solve.py          # batch pipeline: fills level1.csv and level2.csv
├── read_bill.py      # model calls and JSON parsing
├── app.py            # Streamlit app
├── run_train.py      # runs extraction on the training bills
├── requirements.txt
├── .env.example
└── README.md
```

The scripts expect this layout next to `source/`:

```
test/bills/   bill images (.png / .jpg)
test/csv/     level1.csv and level2.csv templates
output/       completed CSV files are written here
```

---

## Setup

**Requirements:** Python 3.11 or later (built and tested on Python 3.11), and an API key for one of the allowed models. I used **Gemini 3.5 Flash-Lite**, which has a free tier on Google AI Studio.

```powershell
cd source
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in `source/` (copy `.env.example`) and add your key:

```env
MODEL_PROVIDER=google
MODEL_NAME=gemini-3.5-flash-lite
API_KEY=your-api-key-here
```

Never commit your real `.env`.

## Run it

**Generate both CSV files for a folder of bills:**

```powershell
python solve.py
```

With no arguments it uses `../test/bills`, `../test/csv` and writes to `../output`. To use other folders:

```powershell
python solve.py <bills_folder> <csv_templates_folder> <output_folder>
```

Add `--reuse` to skip Level 1 and reuse the results already saved in the output folder (handy when you are only tuning the Level 2 answers):

```powershell
python solve.py --reuse
```

A full run on 10 bills takes roughly 10 to 15 minutes. Progress is printed for each bill, including the history rows that were read, so you can sanity check them against the image.

**Check the output:**

```powershell
python -c "import csv;print(len(list(csv.DictReader(open('../output/level1.csv',encoding='utf-8')))),len(list(csv.DictReader(open('../output/level2.csv',encoding='utf-8')))))"
```

For 10 bills and 12 questions this prints `10 120`.

**Run the app:**

```powershell
streamlit run app.py
```

The app has three tabs:

1. **Level 1** shows the extracted details, charges, taxes and the raw JSON.
2. **Level 2** shows the history table and a units chart, lets you pick the preset questions or ask your own, and shows the numbers Python calculated so you can check every answer.
3. **Batch** runs `solve.py` on a folder and shows the resulting CSVs. It writes to `output_demo` by default so real results are never overwritten.

---

## Deploying the app (optional)

The app can be hosted free on [Streamlit Community Cloud](https://share.streamlit.io):

1. Push `app.py`, `solve.py`, `read_bill.py`, `requirements.txt` and this README to a GitHub repo. Do **not** push `.env`, `venv`, or any bill images.
2. Create a new app on Streamlit Cloud, choose `app.py` as the main file, and Python 3.11.
3. Under **Advanced settings → Secrets**, add:

```toml
MODEL_PROVIDER = "google"
MODEL_NAME = "gemini-3.5-flash-lite"
API_KEY = "AQ.Ab8RN6K8y7zN9bacZPEaK7stocfUXJqO4iNgC6eodEGhlsOLFQ"
```

Notes: use a separate key for the public app, since anyone who opens it spends your quota. The Batch tab only makes sense locally.

---

## Limitations (being honest)

- **I have not measured accuracy formally.** I checked results by hand on the sample bills and by reading the printed history rows against the images. There is no benchmark number here.
- **Small, tilted history tables are the weak spot.** The code retries with enlarged and cropped images, but a row can still be missed or misread. The app shows the table it read so you can spot this.
- **A different model run can word an answer differently.** The numbers come from Python and stay the same, the phrasing can vary.
- **Electricity bills only.** Other utilities (a gas bill, for example) will give odd answers.
- **Estimates are simple.** "Next month" and "same units as last month" use averages and the current bill per unit, and are labelled as estimates in the answer.
- **Built in one competition day.** It works end to end, but it is a prototype, not a production system.

## Privacy

Bills are sent to the model provider's API for reading. The prompts tell the model never to repeat names, addresses, CNIC, account, reference or meter numbers, and the bills in this project already have those covered with grey boxes. If you run it on your own bill, be aware it leaves your machine.

---

## AI usage

- **Models used by the solution:** Gemini 3.5 Flash-Lite (`gemini-3.5-flash-lite`) through the Google AI Studio (Gemini) API.
- **OCR or extraction services:** none. No invoice extraction agents or prebuilt document services are used.
- **Libraries:** Pillow for image enlarging and cropping, python-dotenv for configuration, Streamlit and pandas for the app (exact versions in `requirements.txt`).
- **AI tools used to help write the code:** Claude (Anthropic) as a coding assistant and for debugging and prompt iteration. I ran, tested and checked the results myself.

## Credits

Built by **Javeria Iqbal** for Women Tech Quest 2026, Build with AI track. Thank you to Women Tech Quest and 10Pearls for the challenge.
