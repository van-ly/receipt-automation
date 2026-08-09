"""
Unit tests for the pure logic in main.py -- functions that don't require
live API calls or Google credentials. Run with: python -m pytest tests/
"""
import io
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from PIL import Image

import main


# --- validate_category_pair ---

def test_validate_category_pair_valid():
    assert main.validate_category_pair("Food", "Fruits") is True

def test_validate_category_pair_invalid_pairing():
    assert main.validate_category_pair("Food", "Gas") is False

def test_validate_category_pair_unknown_category_a():
    assert main.validate_category_pair("NotACategory", "Fruits") is False


# --- strip_json_fences ---

def test_strip_json_fences_removes_markdown():
    text = '```json\n{"a": 1}\n```'
    assert main.strip_json_fences(text) == '{"a": 1}'

def test_strip_json_fences_passthrough_plain_json():
    text = '{"a": 1}'
    assert main.strip_json_fences(text) == '{"a": 1}'

def test_strip_json_fences_no_language_tag():
    text = '```\n{"a": 1}\n```'
    assert main.strip_json_fences(text) == '{"a": 1}'


# --- find_next_empty_row ---

class FakeValues:
    def __init__(self, data):
        self.data = data
    def get(self, **kwargs):
        return self
    def execute(self):
        return {'values': self.data}

class FakeSpreadsheets:
    def __init__(self, data):
        self.data = data
    def values(self):
        return FakeValues(self.data)

class FakeSheetsService:
    def __init__(self, data):
        self.data = data
    def spreadsheets(self):
        return FakeSpreadsheets(self.data)

def test_find_next_empty_row_finds_gap():
    data = [
        ['2021-08-01', 'Target', 'offline', 'desc'],
        ['2021-08-05', 'foodmaxx', 'offline', 'desc2'],
        [],  # blank row -- this is where it should write
    ]
    svc = FakeSheetsService(data)
    row = main.find_next_empty_row(svc, 'fake_id', 'shared expenses')
    assert row == 4  # header is row 1, so data starts row 2; blank is row 4

def test_find_next_empty_row_no_gap_appends_after_last():
    data = [
        ['2021-08-01', 'Target', 'offline', 'desc'],
        ['2021-08-05', 'foodmaxx', 'offline', 'desc2'],
    ]
    svc = FakeSheetsService(data)
    row = main.find_next_empty_row(svc, 'fake_id', 'shared expenses')
    assert row == 4  # no gap found, write after the 2 existing rows

def test_find_next_empty_row_empty_sheet():
    svc = FakeSheetsService([])
    row = main.find_next_empty_row(svc, 'fake_id', 'shared expenses')
    assert row == 2  # right after the header


# --- blank_if_none ---

def test_blank_if_none_with_none():
    assert main.blank_if_none(None) == ""

def test_blank_if_none_with_value():
    assert main.blank_if_none("Target") == "Target"
    assert main.blank_if_none(9.99) == 9.99
    assert main.blank_if_none(0) == 0


# --- reconcile_total ---

def _receipt(total_amount, item_amounts):
    return main.ReceiptData(
        total_amount=total_amount,
        items=[main.LineItem(description=f"item{i}", amount_paid=a) for i, a in enumerate(item_amounts)]
    )

def test_reconcile_total_matches():
    r = _receipt(10.00, [4.00, 6.00])
    assert main.reconcile_total(r) is None

def test_reconcile_total_within_tolerance():
    r = _receipt(10.00, [4.00, 5.99])  # off by 0.01, under TOTAL_TOLERANCE
    assert main.reconcile_total(r) is None

def test_reconcile_total_mismatch_warns():
    r = _receipt(10.00, [4.00, 4.00])  # off by 2.00
    warning = main.reconcile_total(r)
    assert warning is not None
    assert "does not match" in warning

def test_reconcile_total_no_total_no_warning():
    r = _receipt(None, [4.00, 6.00])
    assert main.reconcile_total(r) is None

def test_reconcile_total_no_item_amounts_no_warning():
    r = _receipt(10.00, [])
    assert main.reconcile_total(r) is None


# --- downscale_image ---

def test_downscale_image_resizes_large_image():
    img = Image.new('RGB', (3000, 4000), color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    original_bytes = buf.getvalue()

    new_bytes, new_mime = main.downscale_image(original_bytes, 'image/png')
    result_img = Image.open(io.BytesIO(new_bytes))

    assert max(result_img.size) <= main.MAX_IMAGE_DIMENSION
    assert new_mime == 'image/jpeg'

def test_downscale_image_passthrough_small_image():
    img = Image.new('RGB', (500, 500), color='blue')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    original_bytes = buf.getvalue()

    new_bytes, new_mime = main.downscale_image(original_bytes, 'image/png')

    assert new_bytes == original_bytes
    assert new_mime == 'image/png'

def test_downscale_image_preserves_aspect_ratio():
    img = Image.new('RGB', (4000, 2000), color='white')  # 2:1 ratio
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    original_bytes = buf.getvalue()

    new_bytes, _ = main.downscale_image(original_bytes, 'image/png')
    result_img = Image.open(io.BytesIO(new_bytes))

    original_ratio = 4000 / 2000
    new_ratio = result_img.size[0] / result_img.size[1]
    assert abs(original_ratio - new_ratio) < 0.01


# --- ReceiptData / LineItem schema ---

def test_receipt_data_accepts_all_none():
    r = main.ReceiptData(
        date=None, source=None, online_offline=None, payment_method=None,
        total_amount=None,
        items=[main.LineItem(description="Apples")]
    )
    assert r.date is None
    assert r.items[0].amount_paid is None

def test_receipt_data_parses_realistic_json():
    sample = {
        "date": "2026-08-08",
        "source": "Target",
        "online_offline": "Offline",
        "payment_method": "Card",
        "total_amount": 12.50,
        "items": [
            {"description": "Apples", "amount_paid": 3.50, "category_a": "Food", "category_b": "Fruits"},
            {"description": "Bread", "amount_paid": 9.00, "category_a": "Food", "category_b": "Pantry Staples"},
        ]
    }
    r = main.ReceiptData(**sample)
    assert len(r.items) == 2
    assert r.items[0].description == "Apples"
