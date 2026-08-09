#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reauth_sheets.py — 乾淨地重新授權 Google Sheets,產生 token.json。

把所有 FutureWarning 關掉,並明確把授權網址印在最上方,
避免網址被警告訊息蓋掉、或瀏覽器沒自動跳出時不知道要點哪裡。

加 --drive 可同時授權 Google Drive(update_wealth_os.py 就地更新雲端 xlsm 需要);
scope 為超集,加了之後既有 Sheets 腳本照常可用。
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "token.json")
CREDENTIALS_FILE = os.path.join(HERE, "credentials.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
if "--drive" in sys.argv:
    # V4.4 xlsm 非本 app 建立,drive.file 不夠,需完整 drive 權限才能就地覆蓋
    SCOPES.append("https://www.googleapis.com/auth/drive")

from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)

print("=" * 70, flush=True)
print(" 即將開啟瀏覽器進行 Google 授權(請用 yaojing277@gmail.com 登入)", flush=True)
print(" 若瀏覽器沒有自動跳出,請手動複製下方印出的網址到瀏覽器開啟。", flush=True)
print("=" * 70, flush=True)

creds = flow.run_local_server(
    port=0,
    open_browser=True,
    authorization_prompt_message="請開啟此網址完成授權:\n{url}",
    success_message="授權完成,可以關閉這個分頁回到終端機了。",
)

with open(TOKEN_FILE, "w") as f:
    f.write(creds.to_json())

print("\n✅ 授權成功,已寫入:", TOKEN_FILE, flush=True)
print("   valid =", creds.valid, " has_refresh =", bool(creds.refresh_token), flush=True)
