# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.6.0] - Simplified sheet layout
### Changed
- Sheet layout reduced from 15 columns to 9: Date, Source, Online/Offline,
  Description, Brand, Amount Paid, Category A, Category B, Payment Method.
- Removed `total_unit`, `unit`, `who_paid`, `day_of_the_week`, `month`,
  `year` from extraction entirely (schema, prompt, and row output) since
  they're no longer tracked in the target sheet.
- Write range changed from `A:O` to `A:I` to match.

## [0.5.0] - Reliability and quality hardening
### Changed
- Switched from Anthropic structured outputs (`output_format`) to prompted
  JSON extraction (`messages.create` + response prefill with `"{"`). The
  structured-outputs schema compiler rejected the nested `items` array
  combined with ~14 optional fields ("Schema is too complex" / grammar
  compilation timeout) even after earlier attempts to simplify it. Prompted
  JSON removes that ceiling; validity is now enforced by manual `json.loads`
  + Pydantic validation instead of the API guaranteeing it.

### Added
- **Prompt caching**: the static system prompt (category taxonomy +
  extraction instructions) is marked `cache_control: ephemeral` so repeated
  calls in a batch reuse the cached version instead of re-paying full input
  cost each time.
- **Retry with backoff**: `extract_receipt_with_retry()` retries on
  transient API errors (`RateLimitError`, `APIConnectionError`,
  `InternalServerError`) and on malformed/invalid JSON responses
  (`JSONDecodeError`, `ValidationError`), up to `MAX_RETRIES` (2), with
  backoff. Permanent errors (bad request, auth) fail immediately.
- **Per-receipt error isolation**: each receipt is wrapped in its own
  try/except. One failure is logged and the batch continues to the next
  file instead of crashing.
- **Write-before-move ordering** (hardened): a receipt is only moved to the
  Processed folder if the Sheets write succeeds, protected by the
  per-receipt exception handling above -- a failed write no longer risks
  silently losing a receipt or getting it stuck in a half-processed state.
- **Run summary**: processed/skipped/failed counts (plus failed filenames)
  printed at the end of each run.
- **Dry-run mode** (`DRY_RUN=true` in `.env`): runs extraction but skips
  Sheets writes and Drive moves, printing what would be written instead.
- **`stop_reason` check**: a response that didn't finish normally (e.g. cut
  off by `max_tokens`) is now treated as a failure instead of silently
  writing incomplete data.
- **Total reconciliation**: new `total_amount` field captures the receipt's
  printed total; `reconcile_total()` compares it against the sum of item
  prices and warns (doesn't block) on mismatch beyond a small tolerance.
- **Audit logging**: every raw AI response is saved to `logs/` (timestamped,
  one file per receipt) for debugging/traceability. Added to `.gitignore`
  since logs contain real receipt data.
- **Image downscaling**: `downscale_image()` resizes receipt photos to a max
  1568px dimension (Pillow) before sending to the API, cutting input tokens
  without hurting extraction quality. Small images pass through unchanged.
- **`requirements.txt`**: pinned dependency minimums for reproducibility.
- **`README.md`**: setup instructions, feature list, usage (including
  Windows-compatible dry-run syntax).
- **`tests/test_main.py`**: 21 unit tests covering category validation, JSON
  fence stripping, row-placement logic, total reconciliation, image
  downscaling, and schema parsing -- no live API/credentials required.

## [0.4.0] - Line-item extraction
### Changed
- `ReceiptData` split into shared receipt-level fields (date, source,
  payment method, etc.) and a nested `items: List[LineItem]`, where each
  item has its own description, price, and category. One receipt image can
  now produce multiple sheet rows -- one per distinct line item -- instead
  of one summarized row per receipt.
- `payment_method` stays a single value per receipt (not per item); the
  model infers Cash vs. Card/Debit from context (printed card type,
  last-4-digits, "CASH TENDERED"/change-due lines).
- Row writing switched from `values().append()` to `values().update()`
  targeting a specific computed range, sized to the number of items
  extracted from that receipt.

### Added
- `find_next_empty_row()`: scans columns A and D starting after the header
  row and returns the first row where either is blank, so new data fills
  gaps in the sheet instead of always writing at the physical end.

### Fixed
- All extracted fields (date, source, payment method, who paid, item
  quantity/unit/price, category, etc.) made optional (`Optional[...]`,
  default `None`). Previously required fields forced the model to guess a
  plausible-looking value (e.g. today's date) when a receipt didn't show
  one. Prompt now explicitly instructs the model to leave a field null
  rather than invent a value; row-building converts `None` to an empty
  cell.

## [0.3.0] - Category enforcement
### Added
- `CATEGORY_MAP` dict sourced from `expenses_test_-_keyCAT.csv`, mapping each
  Category A to its valid Category B values.
- Category mapping text injected into the extraction prompt so the model
  picks a `category_b` valid for whichever `category_a` it selects.
- `validate_category_pair()` -- a post-extraction check that warns (does not
  block) if the returned A/B pair doesn't match the CSV mapping.

### Changed
- `category_a`/`category_b` were briefly typed as `Literal[...]` enums for
  API-level enforcement, then reverted to plain strings in 0.5.0 once the
  enum (47 values for category_b) combined with optional/nullable fields
  proved too complex for the structured-outputs schema compiler.

### Notes
- Category list is hardcoded in `main.py`. If the category taxonomy changes,
  `CATEGORY_MAP` must be updated manually (not read live from the CSV/sheet).

## [0.2.0] - Switched extraction provider: Gemini -> Anthropic API
### Changed
- Replaced `google.genai` client with the Anthropic Python SDK (`anthropic`).
- Model: `gemini-2.0-flash` -> `claude-haiku-4-5-20251001`.
- Env var: `GEMINI_API_KEY` -> `ANTHROPIC_API_KEY`.
- Receipt image is now base64-encoded and sent as an `image` content block
  (Anthropic Messages API format) instead of `types.Part.from_bytes`.
- Structured extraction now uses `client.messages.parse(..., output_format=ReceiptData)`
  instead of Gemini's `response_schema` + manual `json.loads(response.text)`.
  Result is read from `response.parsed_output`.

### Reason
- Gemini API free tier on this Google account returned `limit: 0` on all
  quota metrics regardless of project (billing-policy issue tied to the
  Google account, not the Cloud project). Anthropic API chosen over other
  free-tier alternatives (OpenRouter `:free` models) for reliability and
  because the target role uses Claude/Claude Code directly.

## [0.1.0] - Initial version
### Added
- `main.py`: reads receipt images from a Google Drive input folder, extracts
  structured data via Gemini (`gemini-2.0-flash`) using a Pydantic schema
  (`ReceiptData`), appends a row to a Google Sheet, then moves the processed
  file to a separate Drive folder.
- OAuth 2.0 (Installed App flow) for Drive + Sheets access via
  `credentials.json` / `token.json`.
- `.env`-based config for `SPREADSHEET_ID`, `INPUT_FOLDER_ID`,
  `PROCESSED_FOLDER_ID`, and the model API key.
