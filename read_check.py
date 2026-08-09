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

    sheets_service = build('sheets', 'v4', credentials=creds)
    return sheets_service

def read_range(range_str):
    spreadsheet_id = os.getenv('SPREADSHEET_ID')
    sheets_service = get_google_services()

    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_str
    ).execute()

    values = result.get('values', [])
    print(f"Range requested: {range_str}")
    print(f"Rows returned: {len(values)}")
    for row in values:
        print(row)

    # Also print basic spreadsheet metadata to confirm we're hitting the right file/tab
    meta = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    print("\nSpreadsheet title:", meta.get('properties', {}).get('title'))
    print("Tabs found:")
    for sheet in meta.get('sheets', []):
        props = sheet.get('properties', {})
        print(f"  - {props.get('title')} (gridSize: {props.get('gridProperties')})")

if __name__ == '__main__':
    read_range("'shared expenses'!A910:O920")