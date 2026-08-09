# Receipt Automation

Drop a photo of a receipt into a Google Drive folder. This script reads it,
extracts each line item (description, price, category, etc.) using the
Claude API, writes one row per item into a Google Sheet, and moves the
receipt image to a "processed" folder.

## What it does

1. Scans a Google Drive input folder for new receipt images.
2. Sends each image to the Claude API (Haiku 4.5) with a prompt describing
   the exact JSON shape to extract, including a fixed category taxonomy.
3. Parses and validates the response (Pydantic).
4. Writes one row per line item to a Google Sheet -- shared receipt-level
   fields (date, store, payment method) repeated on every row, item-level
   fields (description, price, category) unique per row.
5. Moves the processed receipt image to a separate "processed" Drive folder.

## Why line items, not one row per receipt

A single grocery receipt often mixes categories (e.g. food + household
items). Itemizing means each purchase gets categorized individually instead
of the whole receipt being lumped under one category.

## Reliability features

- **Retry with backoff** on transient API errors (rate limits, timeouts,
  5xx) and on malformed/invalid JSON responses.
- **Per-receipt error isolation** -- one bad receipt doesn't stop the batch;
  it's logged as failed and skipped, remaining receipts still process.
- **Write-before-move ordering** -- a receipt is only moved to the
  "processed" folder after its data is successfully written to the sheet,
  so a failed write doesn't silently lose a receipt.
- **Total reconciliation** -- if the receipt shows a printed total, it's
  compared against the sum of extracted item prices; a mismatch is flagged
  as a warning (possible missed or misread item).
- **No-guessing extraction** -- every field is optional. If a value isn't
  clearly shown on the receipt, the model is instructed to leave it blank
  rather than invent a plausible-looking value.
- **Audit logging** -- every raw AI response is saved to `logs/` (one file
  per receipt, timestamped) for debugging and traceability.
- **Image downscaling** -- receipt photos are resized before sending to
  the API, cutting token cost without hurting extraction quality.
- **Run summary** -- prints counts of processed / skipped / failed receipts
  at the end of each run.
- **Dry-run mode** -- extract and preview output without writing to Sheets
  or moving files, so you can sanity-check before touching real data.

## Setup

### 1. Install dependencies

```
pip install -r requirements.txt
```

### 2. Google Cloud / OAuth setup

1. Create a project in [Google Cloud Console](https://console.cloud.google.com).
2. Enable the **Google Drive API** and **Google Sheets API** for that project.
3. Create an OAuth 2.0 Client ID (Application type: **Desktop app**).
4. Download the credentials JSON and save it as `credentials.json` in the
   project root.
5. On first run, the script opens a browser for you to log in and grant
   access. This creates `token.json`, which is reused on subsequent runs.

`credentials.json` and `token.json` contain secrets and are **not** committed
to this repo (see `.gitignore`).

### 3. Anthropic API key

Get a key from [console.anthropic.com](https://console.anthropic.com) and
add it to `.env` (see below).

### 4. Google Drive folders and Sheet

- Create two Drive folders: one for incoming receipts, one for processed
  receipts. Copy their folder IDs from the URL
  (`drive.google.com/drive/folders/<FOLDER_ID>`).
- Create a Google Sheet with a tab matching `SHEET_TAB_NAME` in `main.py`,
  with headers in row 1 matching the column order the script writes:
  Date, Source, Online/Offline, Description, Brand, Amount Paid,
  Category A, Category B, Payment Method.
- Copy the spreadsheet ID from its URL
  (`docs.google.com/spreadsheets/d/<SPREADSHEET_ID>`).

### 5. Environment variables

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=your_key_here
SPREADSHEET_ID=your_spreadsheet_id
INPUT_FOLDER_ID=your_input_folder_id
PROCESSED_FOLDER_ID=your_processed_folder_id
DRY_RUN=false
```

## Usage

```
python main.py
```

To test without writing to Sheets or moving files in Drive (still calls the
Claude API for extraction, but only prints the result):

**macOS/Linux:**
```
DRY_RUN=true python main.py
```

**Windows (cmd):**
```
set DRY_RUN=true && python main.py
```

Or just set `DRY_RUN=true` in `.env` and run `python main.py` normally --
works the same on any OS.

## Category taxonomy

The category list (`CATEGORY_MAP` in `main.py`) is currently hardcoded,
sourced from a CSV export of the target sheet's valid category pairs. If the
taxonomy changes, update `CATEGORY_MAP` directly.

## Tests

21 unit tests cover the pure logic -- category validation, JSON response
parsing, row-placement, total reconciliation, and image downscaling -- with
no live API calls or Google credentials required:

```
python -m pytest tests/
```
