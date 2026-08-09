# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.3.0] - Category enforcement
### Added
- `CATEGORY_MAP` dict sourced from `expenses_test_-_keyCAT.csv`, mapping each
  Category A to its valid Category B values.
- `category_a` and `category_b` fields on `ReceiptData` changed from free-text
  `str` to `Literal[...]` enums, so the API rejects any value outside the
  fixed category list at the schema level.
- Category mapping text injected into the extraction prompt so the model
  picks a `category_b` valid for whichever `category_a` it selects.
- `validate_category_pair()` — a post-extraction check that warns (does not
  block) if the returned A/B pair doesn't match the CSV mapping. Schema
  enums enforce valid individual values; this catches invalid *pairings*,
  which JSON Schema can't express directly.

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
