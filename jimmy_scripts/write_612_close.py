#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 2026-06-12 收盤價寫入 Jimmy_260612 分頁 K 欄(逐列指定,保留空列)。"""
import os, sys
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "token.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = "1UiqAHT2GUhKiviSz5NaLNclttlLVP3ujQMxJUn7Jyr8"
SHEET = "Jimmy_260612"

# 列號 -> 6/12 收盤價(K欄)。K15 為 A 欄空列、K21(WW)待確認,皆不寫。
K_VALUES = {
    2: 101.95, 3: 59.6, 4: 50.6, 5: 34.83, 6: 120.05, 7: 101.45,
    8: 59.8, 9: 32.05, 10: 30.19, 11: 27.99, 12: 30.6,
    13: 2215, 14: 260.5, 16: 2310, 17: 4180,
    18: 27.45, 19: 30.2, 20: 67.6,
}

def get_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("sheets", "v4", credentials=creds)

def main():
    service = get_service()
    data = [{"range": f"{SHEET}!K{r}", "values": [[v]]} for r, v in sorted(K_VALUES.items())]
    resp = service.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
    print(f"完成,更新 {resp.get('totalUpdatedCells')} 格。")

if __name__ == "__main__":
    main()
