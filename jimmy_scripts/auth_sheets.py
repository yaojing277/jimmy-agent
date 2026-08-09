#!/usr/bin/env python3
# 執行一次以取得 Google Sheets 寫入權限的 token
# python3 jimmy_scripts/auth_sheets.py

import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

CLIENT_SECRET = os.path.join(os.path.dirname(__file__), "..", "client_secret_613914242956-6fgpdmuqi8bgv5veccnm0iub5i3p2fdq.apps.googleusercontent.com.json")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "..", "token_sheets.pickle")

flow = InstalledAppFlow.from_client_secrets_file(os.path.abspath(CLIENT_SECRET), SCOPES)
creds = flow.run_local_server(port=0)

with open(TOKEN_PATH, "wb") as f:
    pickle.dump(creds, f)

print(f"授權完成，token 已儲存至 {os.path.abspath(TOKEN_PATH)}")
