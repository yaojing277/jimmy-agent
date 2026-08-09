#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""讀取「股價試算」中 Jimmy_260612 分頁,印出 A 欄(代號)與 K 欄現況。"""
import os, sys
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "token.json")
CREDENTIALS_FILE = os.path.join(HERE, "credentials.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = "1UiqAHT2GUhKiviSz5NaLNclttlLVP3ujQMxJUn7Jyr8"
SHEET_NAME = "Jimmy_260612"

def get_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("sheets", "v4", credentials=creds)

def main():
    service = get_service()
    # 先確認分頁存在
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    titles = [sh["properties"]["title"] for sh in meta.get("sheets", [])]
    if SHEET_NAME not in titles:
        print("找不到分頁,現有分頁:")
        for t in titles:
            print("  -", t)
        sys.exit(1)
    rng = f"{SHEET_NAME}!A1:K60"
    resp = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=rng).execute()
    rows = resp.get("values", [])
    print(f"分頁「{SHEET_NAME}」A欄/K欄現況(列: A=代號, K=收盤價):\n")
    for i, row in enumerate(rows, start=1):
        a = row[0] if len(row) > 0 else ""
        k = row[10] if len(row) > 10 else ""
        if str(a).strip() == "" and str(k).strip() == "":
            continue
        print(f"  列{i:>3}: A={a!r:>12}   K={k!r}")

if __name__ == "__main__":
    main()
