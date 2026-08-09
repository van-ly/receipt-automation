import os
import io
import re
import json
import base64
import time
from datetime import datetime, timezone
from typing import List, Optional
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import anthropic
from anthropic import Anthropic
from pydantic import BaseModel, Field, ValidationError
from PIL import Image

# Load variables from .env file
load_dotenv()

# Define OAuth Scopes
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]

# Tab name in the target spreadsheet. Must match exactly (case-sensitive).
SHEET_TAB_NAME = "shared expenses"

# Set DRY_RUN=true in .env to run extraction only -- no Sheets writes, no
# Drive moves. Useful for sanity-checking AI output before it touches real data.
DRY_RUN = os.getenv('DRY_RUN', 'false').strip().lower() in ('1', 'true', 'yes')

# Retry settings. Covers both transient API errors (rate limits, timeouts,
# 5xx) and malformed/invalid JSON responses -- since we no longer use
# schema-enforced structured outputs, a retry on a parse failure can succeed
# where the first attempt didn't.
MAX_RETRIES = 2
API_RETRY_BACKOFF_SECONDS = 5
PARSE_RETRY_BACKOFF_SECONDS = 2

# Receipts are photos, often much higher resolution than needed for OCR.
# Downscaling to this max dimension (matches Claude's vision "sweet spot")
# cuts input tokens/cost without hurting extraction quality.
MAX_IMAGE_DIMENSION = 1568

# Directory for raw AI response logs, one file per receipt, for audit/debugging.
LOG_DIR = "logs"

# Total mismatch tolerance in dollars before flagging a reconciliation warning.
TOTAL_TOLERANCE = 0.02

# Category A -> valid Category B values, sourced from expenses_test_-_keyCAT.csv
CATEGORY_MAP = {
    "Car": ["Gas", "Maintenance"],
    "Clothes": ["Bottoms", "Jackets/Coats", "Shoes", "Socks", "Tops", "Overalls"],
    "Entertainment": ["Books", "Board Games", "Card Games", "Card Game Accessories", "DLC",
                       "Garden", "Video Game Accessories", "Video Games", "Movies", "Sports",
                       "Streaming", "DVDs", "Subscription", "Crafts", "Software", "Music Equipment"],
    "Food": ["Drinks", "Fruits", "Meats", "Pantry Staples", "Ready made meals",
             "Sauces/Spices", "Snacks", "Take Out", "Vegetables"],
    "Garden": ["Plants", "Tools/Accessories", "Plant Food/Fertizilizer/Soil"],
    "Household": ["Bathroom", "Bedroom", "Disposable Products", "Furniture", "Kitchen",
                   "Living Room", "Maintenance", "Soaps/Cleaners"],
    "Vacation": ["Activities", "Air Travel", "Ground Transportation", "Hotel Costs"],
}

CATEGORY_A_VALUES = list(CATEGORY_MAP.keys())
CATEGORY_B_VALUES = sorted({b for values in CATEGORY_MAP.values() for b in values})

CATEGORY_MAP_TEXT = "\n".join(
    f"- {a}: {', '.join(bs)}" for a, bs in CATEGORY_MAP.items()
)

# Explicit JSON schema spelled out in the prompt. We no longer use Anthropic's
# structured outputs (output_format) here -- the nested items array combined
# with ~14 optional fields exceeded the schema compiler's complexity limits
# ("Schema is too complex" / grammar compilation timeout). Instead we describe
# the exact shape we want and parse + validate the JSON ourselves.
JSON_SCHEMA_TEXT = """{
  "date": string or null,        // YYYY-MM-DD, null if not shown -- do not guess
  "source": string or null,      // store/source name, null if not determinable
  "online_offline": string or null,   // "Online" or "Offline", null if unclear
  "payment_method": string or null,   // e.g. "Credit Card", "Cash", "Debit" -- infer from context, null if no indication
  "total_amount": number or null,     // the printed total on the receipt, if shown, null if not shown
  "items": [
    {
      "description": string,          // required -- description of this line item
      "brand": string or null,
      "amount_paid": number or null,  // price paid for this specific item
      "category_a": string or null,   // exact value from the Category A list below
      "category_b": string or null    // exact value from the Category B list below, valid for the chosen category_a
    }
  ]
}"""

SYSTEM_PROMPT_TEXT = (
    "You extract structured data from a photo of a receipt and respond with "
    "ONLY valid JSON matching this exact shape -- no markdown code fences, "
    "no explanation, no text before or after the JSON:\n\n"
    f"{JSON_SCHEMA_TEXT}\n\n"
    "Create one entry in 'items' for each distinct line item on the receipt "
    "-- do not summarize multiple items into one entry, and do not include "
    "the receipt's overall total as an item (use the separate 'total_amount' "
    "field for that instead).\n\n"
    "For each item's category_a and category_b, you must choose a valid pair "
    "from this list (category_a: [category_b options]):\n"
    f"{CATEGORY_MAP_TEXT}\n\n"
    "Pick the category_a that best fits that specific item, then pick a "
    "category_b listed under that category_a. Use the exact spelling shown "
    "above. Different items on the same receipt can have different "
    "categories.\n\n"
    "payment_method applies to the whole receipt (one value, not per item) "
    "-- infer Cash vs Card/Debit/etc. from context such as a printed card "
    "type, last-4-digits, 'CASH TENDERED' or change-due lines.\n\n"
    "IMPORTANT: Do not guess or assume values for any field. If a field is "
    "not clearly shown or determinable from the receipt image, use null for "
    "that field rather than filling in a plausible-sounding default. It is "
    "better to leave a field null than to invent a value.\n\n"
    "Respond with ONLY the JSON object. Your response must start with { and "
    "end with }."
)

def validate_category_pair(category_a: str, category_b: str) -> bool:
    """Return True if category_b is a valid sub-category of category_a."""
    return category_b in CATEGORY_MAP.get(category_a, [])

# One purchased item/line on the receipt.
class LineItem(BaseModel):
    description: str
    brand: Optional[str] = None
    amount_paid: Optional[float] = None
    category_a: Optional[str] = None
    category_b: Optional[str] = None

# Full receipt: shared header fields + a list of line items.
class ReceiptData(BaseModel):
    date: Optional[str] = None
    source: Optional[str] = None
    online_offline: Optional[str] = None
    payment_method: Optional[str] = None
    total_amount: Optional[float] = None
    items: List[LineItem] = Field(default_factory=list)

def get_google_services():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    drive_service = build('drive', 'v3', credentials=creds)
    sheets_service = build('sheets', 'v4', credentials=creds)
    return drive_service, sheets_service

def find_next_empty_row(sheets_service, spreadsheet_id, tab_name, header_row=1, max_row=2000):
    """
    Find the first row (below the header) where column A or column D is blank.
    Returns the 1-indexed row number to start writing at.
    """
    col_range = f"'{tab_name}'!A{header_row + 1}:D{max_row}"
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=col_range
    ).execute()

    values = result.get('values', [])

    for i, row in enumerate(values):
        row_num = header_row + 1 + i
        col_a = row[0] if len(row) > 0 else ""
        col_d = row[3] if len(row) > 3 else ""
        if col_a.strip() == "" or col_d.strip() == "":
            return row_num

    return header_row + 1 + len(values)

def blank_if_none(value):
    return "" if value is None else value

def downscale_image(file_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """
    Resize the image so its longer side is at most MAX_IMAGE_DIMENSION,
    preserving aspect ratio. Returns (new_bytes, new_mime_type). If the
    image is already small enough, or can't be processed by Pillow, returns
    the original bytes/mime_type unchanged.
    """
    try:
        img = Image.open(io.BytesIO(file_bytes))
        width, height = img.size
        longest_side = max(width, height)

        if longest_side <= MAX_IMAGE_DIMENSION:
            return file_bytes, mime_type

        scale = MAX_IMAGE_DIMENSION / longest_side
        new_size = (int(width * scale), int(height * scale))
        img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
        img = img.resize(new_size, Image.LANCZOS)

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue(), "image/jpeg"

    except Exception as e:
        print(f"  WARNING: image downscaling failed ({e}). Using original image.")
        return file_bytes, mime_type

def strip_json_fences(text: str) -> str:
    """Remove markdown code fences around JSON, if the model added them anyway."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return text.strip()

def log_raw_response(file_name: str, raw_text: str, success: bool):
    """Write the raw AI response to a log file for audit/debugging purposes."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_name = re.sub(r'[^A-Za-z0-9_.-]', '_', file_name)
        status = "ok" if success else "FAILED"
        log_path = os.path.join(LOG_DIR, f"{timestamp}_{status}_{safe_name}.json")
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(raw_text)
    except Exception as e:
        print(f"  WARNING: failed to write audit log for {file_name}: {e}")

def reconcile_total(receipt: ReceiptData) -> Optional[str]:
    """
    Compare the receipt's printed total_amount against the sum of item
    amount_paid values. Returns a warning string if they disagree beyond
    TOTAL_TOLERANCE, or None if they match / can't be checked (missing data).
    """
    if receipt.total_amount is None:
        return None

    item_amounts = [item.amount_paid for item in receipt.items if item.amount_paid is not None]
    if not item_amounts:
        return None

    items_sum = sum(item_amounts)
    diff = abs(items_sum - receipt.total_amount)

    if diff > TOTAL_TOLERANCE:
        return (
            f"Sum of item prices (${items_sum:.2f}) does not match the receipt's "
            f"printed total (${receipt.total_amount:.2f}), difference of ${diff:.2f}. "
            f"Possible missed/misread item -- double check this receipt."
        )
    return None

def extract_receipt_with_retry(ai_client, image_b64, mime_type, file_name):
    """
    Call the Anthropic API to extract receipt data as JSON (prompted, not
    schema-enforced -- see JSON_SCHEMA_TEXT), retrying on transient API
    errors and on JSON parse/validation failures.

    Returns the parsed ReceiptData on success. Raises the last exception if
    all retries are exhausted.
    """
    last_exception = None

    for attempt in range(1, MAX_RETRIES + 2):
        raw_text = None
        try:
            response = ai_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT_TEXT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime_type,
                                    "data": image_b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": "Extract the metadata from this receipt as JSON per the schema in the system prompt.",
                            },
                        ],
                    },
                    {
                        # Prefill forces the response to start as a JSON object,
                        # skipping any preamble/markdown the model might add.
                        "role": "assistant",
                        "content": "{",
                    },
                ],
            )

            completion = "".join(block.text for block in response.content if block.type == "text")
            raw_text = "{" + completion  # re-attach the prefilled opening brace
            raw_text = strip_json_fences(raw_text)

            if response.stop_reason not in ("end_turn", "stop_sequence"):
                raise RuntimeError(
                    f"Response did not finish normally (stop_reason={response.stop_reason}). "
                    f"Output may be incomplete."
                )

            data = json.loads(raw_text)
            receipt = ReceiptData(**data)

            log_raw_response(file_name, raw_text, success=True)
            return receipt

        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as e:
            last_exception = e
            if raw_text:
                log_raw_response(file_name, raw_text, success=False)
            if attempt <= MAX_RETRIES:
                wait = API_RETRY_BACKOFF_SECONDS * attempt
                print(f"  Transient API error on attempt {attempt} for {file_name} ({type(e).__name__}). Retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"  Giving up on {file_name} after {attempt} attempts (API error).")
                raise

        except (json.JSONDecodeError, ValidationError) as e:
            last_exception = e
            if raw_text:
                log_raw_response(file_name, raw_text, success=False)
            if attempt <= MAX_RETRIES:
                print(f"  Malformed response on attempt {attempt} for {file_name} ({type(e).__name__}). Retrying in {PARSE_RETRY_BACKOFF_SECONDS}s...")
                time.sleep(PARSE_RETRY_BACKOFF_SECONDS)
            else:
                print(f"  Giving up on {file_name} after {attempt} attempts (parse/validation error).")
                raise

        except Exception:
            if raw_text:
                log_raw_response(file_name, raw_text, success=False)
            raise

    raise last_exception

def process_receipts():
    spreadsheet_id = os.getenv('SPREADSHEET_ID', '1ZQ1ScEFAGU_qhuzpLqLSaEPu2ENxrHTO_B9Lwrm3Nhs')
    input_folder_id = os.getenv('INPUT_FOLDER_ID', '1-Rd0B5AcPZc9pBb2O2lmLscYUJkiXQu1')
    processed_folder_id = os.getenv('PROCESSED_FOLDER_ID', '1L9YXi6ZuKoYvIJeizEJ_3wng0DRNpVpD')
    anthropic_key = os.getenv('ANTHROPIC_API_KEY')

    if not anthropic_key:
        raise ValueError("ANTHROPIC_API_KEY not found. Please set it in your .env file.")

    if DRY_RUN:
        print("DRY RUN MODE: extraction only, no Sheets writes or Drive moves will happen.\n")

    drive_service, sheets_service = get_google_services()
    ai_client = Anthropic(api_key=anthropic_key)

    query = f"'{input_folder_id}' in parents and trashed = false"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    files = results.get('files', [])

    if not files:
        print("No new receipts found in the input folder.")
        return

    print(f"Found {len(files)} receipt(s) to process...\n")

    processed_count = 0
    skipped_count = 0
    failed_count = 0
    failed_files = []

    for file in files:
        file_id = file['id']
        file_name = file['name']
        mime_type = file['mimeType']

        print(f"Processing: {file_name}...")

        try:
            request = drive_service.files().get_media(fileId=file_id)
            file_stream = io.BytesIO()
            downloader = MediaIoBaseDownload(file_stream, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            file_bytes = file_stream.getvalue()

            file_bytes, mime_type = downscale_image(file_bytes, mime_type)
            image_b64 = base64.b64encode(file_bytes).decode('utf-8')

            receipt = extract_receipt_with_retry(ai_client, image_b64, mime_type, file_name)

            if not receipt.items:
                print(f"  WARNING: no line items extracted. Skipping sheet write.")
                skipped_count += 1
                continue

            total_warning = reconcile_total(receipt)
            if total_warning:
                print(f"  WARNING: {total_warning}")

            rows = []
            for item in receipt.items:
                if item.category_a is not None and item.category_b is not None:
                    if not validate_category_pair(item.category_a, item.category_b):
                        print(
                            f"  WARNING: '{item.category_b}' is not a valid sub-category of "
                            f"'{item.category_a}' for item '{item.description}'. "
                            f"Row will still be written -- double check this entry in the sheet."
                        )

                rows.append([
                    blank_if_none(receipt.date),
                    blank_if_none(receipt.source),
                    blank_if_none(receipt.online_offline),
                    blank_if_none(item.description),
                    blank_if_none(item.brand),
                    blank_if_none(item.amount_paid),
                    blank_if_none(item.category_a),
                    blank_if_none(item.category_b),
                    blank_if_none(receipt.payment_method),
                ])

            if DRY_RUN:
                print(f"  DRY RUN: would write {len(rows)} row(s):")
                for r in rows:
                    print(f"    {r}")
                processed_count += 1
                continue

            start_row = find_next_empty_row(sheets_service, spreadsheet_id, SHEET_TAB_NAME)
            end_row = start_row + len(rows) - 1
            write_range = f"'{SHEET_TAB_NAME}'!A{start_row}:I{end_row}"

            sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=write_range,
                valueInputOption="USER_ENTERED",
                body={"values": rows}
            ).execute()

            drive_service.files().update(
                fileId=file_id,
                addParents=processed_folder_id,
                removeParents=input_folder_id,
                fields='id, parents'
            ).execute()

            print(f"  Wrote {len(rows)} item row(s) to {write_range} and moved to Processed folder.")
            processed_count += 1

        except Exception as e:
            print(f"  FAILED: {file_name} -- {type(e).__name__}: {e}")
            print(f"  This receipt was NOT moved to Processed and can be retried on the next run.")
            failed_count += 1
            failed_files.append(file_name)
            continue

    print("\n" + "=" * 50)
    print("RUN SUMMARY")
    print("=" * 50)
    print(f"Processed: {processed_count}")
    print(f"Skipped (no items found): {skipped_count}")
    print(f"Failed: {failed_count}")
    if failed_files:
        print("Failed files:")
        for f in failed_files:
            print(f"  - {f}")
    print("=" * 50)

if __name__ == '__main__':
    process_receipts()
