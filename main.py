import os
import io
import json
import base64
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from anthropic import Anthropic
from pydantic import BaseModel, Field

# Load variables from .env file
load_dotenv()

# Define OAuth Scopes
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]

# Pydantic schema for structured receipt extraction
class ReceiptData(BaseModel):
    date: str = Field(description="Date of transaction formatted as YYYY-MM-DD")
    source: str = Field(description="Source or store name")
    online_offline: str = Field(description="Either 'Online' or 'Offline'")
    description: str = Field(description="Summary item description")
    brand: str = Field(description="Brand or store brand name")
    total_unit: float = Field(description="Total number of items/units purchased")
    unit: str = Field(description="Unit type e.g., 'pcs', 'lbs', 'ea', or 'N/A'")
    amount_paid: float = Field(description="Total amount paid")
    category_a: str = Field(description="Broad expense category e.g., Groceries, Dining, Electronics, Utilities")
    category_b: str = Field(description="Sub-category e.g., Food, Coffee, Household, Hardware")
    payment_method: str = Field(description="Payment method used e.g., Credit Card, Cash, Debit, Apple Pay")
    who_paid: str = Field(description="Person who paid or 'Self'")
    day_of_the_week: str = Field(description="Day of week e.g., Monday, Tuesday")
    month: str = Field(description="Month name or number e.g., January")
    year: int = Field(description="Year as 4-digit integer e.g., 2026")

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

def process_receipts():
    spreadsheet_id = os.getenv('SPREADSHEET_ID', '1ZQ1ScEFAGU_qhuzpLqLSaEPu2ENxrHTO_B9Lwrm3Nhs')
    input_folder_id = os.getenv('INPUT_FOLDER_ID', '1-Rd0B5AcPZc9pBb2O2lmLscYUJkiXQu1')
    processed_folder_id = os.getenv('PROCESSED_FOLDER_ID', '1L9YXi6ZuKoYvIJeizEJ_3wng0DRNpVpD')
    anthropic_key = os.getenv('ANTHROPIC_API_KEY')

    if not anthropic_key:
        raise ValueError("ANTHROPIC_API_KEY not found. Please set it in your .env file.")

    drive_service, sheets_service = get_google_services()
    ai_client = Anthropic(api_key=anthropic_key)

    query = f"'{input_folder_id}' in parents and trashed = false"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    files = results.get('files', [])

    if not files:
        print("No new receipts found in the input folder.")
        return

    print(f"Found {len(files)} receipt(s) to process...")

    for file in files:
        file_id = file['id']
        file_name = file['name']
        mime_type = file['mimeType']

        print(f"Processing: {file_name}...")

        request = drive_service.files().get_media(fileId=file_id)
        file_stream = io.BytesIO()
        downloader = MediaIoBaseDownload(file_stream, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        file_bytes = file_stream.getvalue()

        image_b64 = base64.b64encode(file_bytes).decode('utf-8')

        response = ai_client.messages.parse(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
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
                            "text": "Extract the metadata from this receipt according to the provided schema.",
                        },
                    ],
                }
            ],
            output_format=ReceiptData,
        )

        receipt = response.parsed_output

        row = [
            receipt.date,
            receipt.source,
            receipt.online_offline,
            receipt.description,
            receipt.brand,
            receipt.total_unit,
            receipt.unit,
            receipt.amount_paid,
            receipt.category_a,
            receipt.category_b,
            receipt.payment_method,
            receipt.who_paid,
            receipt.day_of_the_week,
            receipt.month,
            receipt.year,
        ]

        sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range="shared expenses!A1",
            valueInputOption="USER_ENTERED",
            body={"values": [row]}
        ).execute()

        drive_service.files().update(
            fileId=file_id,
            addParents=processed_folder_id,
            removeParents=input_folder_id,
            fields='id, parents'
        ).execute()

        print(f"Successfully processed {file_name} and moved to Processed folder.")

if __name__ == '__main__':
    process_receipts()