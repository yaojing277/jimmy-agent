import pickle
import sys
import warnings
warnings.filterwarnings('ignore')

from google.auth.transport.requests import Request
from googleapiclient.discovery import build

TOKEN_FILE = '/Users/jimmy/Downloads/jimmy-agent/token.pickle'
SPREADSHEET_ID = '1UiqAHT2GUhKiviSz5NaLNclttlLVP3ujQMxJUn7Jyr8'
SHEET_NAME = sys.argv[1] if len(sys.argv) > 1 else 'Jimmy_260410'

def main():
    with open(TOKEN_FILE, 'rb') as f:
        creds = pickle.load(f)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    service = build('sheets', 'v4', credentials=creds)
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=SHEET_NAME
    ).execute()

    rows = result.get('values', [])
    print(f'分頁：{SHEET_NAME}，共 {len(rows)} 列\n')
    for i, row in enumerate(rows):
        print(f'[{i+1:>3}] {row}')

if __name__ == '__main__':
    main()
