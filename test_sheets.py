import os
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv()

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]

# CHANGE THIS to match whatever you set in main.py's range=... line
SHEET_RANGE = "shared expenses!A1"

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

def test_sheet_write():
    spreadsheet_id = os.getenv('SPREADSHEET_ID')
    _, sheets_service = get_google_services()

    # Dummy row matching the same column order as main.py's ReceiptData
    dummy_row = [
        "2026-08-08",     # date
        "TEST STORE",     # source
        "Offline",        # online_offline
        "Test item",      # description
        "Test Brand",     # brand
        1,                # total_unit
        "ea",             # unit
        9.99,             # amount_paid
        "Test Category",  # category_a
        "Test Sub",       # category_b
        "Cash",           # payment_method
        "Self",           # who_paid
        "Saturday",       # day_of_the_week
        "August",         # month
        2026,             # year
    ]

    print(f"Attempting to append to range: {SHEET_RANGE}")
    result = sheets_service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=SHEET_RANGE,
        valueInputOption="USER_ENTERED",
        body={"values": [dummy_row]}
    ).execute()

    print("Success. Updated range:", result.get('updates', {}).get('updatedRange'))

def test_drive_folders():
    input_folder_id = os.getenv('INPUT_FOLDER_ID')
    processed_folder_id = os.getenv('PROCESSED_FOLDER_ID')
    drive_service, _ = get_google_services()

    for label, folder_id in [("INPUT_FOLDER_ID", input_folder_id), ("PROCESSED_FOLDER_ID", processed_folder_id)]:
        try:
            meta = drive_service.files().get(fileId=folder_id, fields="id, name").execute()
            print(f"{label} OK -> folder name: {meta.get('name')}")
        except Exception as e:
            print(f"{label} FAILED -> {e}")

if __name__ == '__main__':
    test_drive_folders()
    test_sheet_write()