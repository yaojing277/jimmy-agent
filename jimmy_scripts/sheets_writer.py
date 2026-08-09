#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股價試算 - 股票買賣紀錄 寫回工具 (Google Sheets API / OAuth 2.0)

用途:
  1. inspect 模式:讀回「股票買賣紀錄」分頁,印出列號 + 各欄字母 + 內容,
     用來確認「要寫到哪個儲存格」(這分頁表格不是從 A 欄開始,務必先看清楚)。
  2. write 模式:把 ROWS 內的資料,寫到指定起點儲存格(A1 表示法)。

前置需求(只需做一次):
  pip3 install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib
  並把 GCP 下載的 OAuth 用戶端憑證放到本資料夾,命名為 credentials.json
  (取得方式見同資料夾的 SHEETS_API_SETUP.md)

用法:
  # 第一步:先看分頁結構,確認位址
  python3 sheets_writer.py inspect

  # 第二步:確認 START_CELL 與 ROWS 後寫入(會先顯示預覽,輸入 yes 才真的寫)
  python3 sheets_writer.py write
"""

import os
import sys
import argparse

# ---- 套件檢查 ----
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
except ImportError:
    sys.exit(
        "缺少套件,請先執行:\n"
        "  pip3 install --upgrade google-api-python-client "
        "google-auth-httplib2 google-auth-oauthlib"
    )

# =========================================================
# 基本設定
# =========================================================
HERE = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(HERE, "credentials.json")   # GCP 下載的 OAuth 憑證
TOKEN_FILE = os.path.join(HERE, "token.json")               # 首次授權後自動產生
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# 「股價試算」主檔
SPREADSHEET_ID = "1UiqAHT2GUhKiviSz5NaLNclttlLVP3ujQMxJUn7Jyr8"
SHEET_NAME = "股票買賣紀錄"

# =========================================================
# 要寫入的資料(來自交割明細擷圖)
# =========================================================
# 寫入方式:在第 INSERT_ROW 列「上方插入」len(ROWS) 個空列,再把值寫進去
# (用插入、不覆蓋,原本第 4~6 列會往下移,資料不會被蓋掉)
INSERT_ROW = 4   # 分頁資料從第 4 列起、最新在最上面,新交易插在第 4 列上方

# 欄位對齊分頁實際欄位(A~P 共 16 欄),只填「股票 / 日期 / 應收付(P)」,其餘留空。
# 賣出 → 日期填 J(賣出日,index 9);買進 → 日期填 F(買進日,index 5);淨額一律填 P(index 15)。
# 順序「由新到舊」(最新在最上),對齊分頁排序。
A, F, J, P = 0, 5, 9, 15

def _sell(stock, date, net):
    r = [""] * 16
    r[A], r[J], r[P] = stock, date, net
    return r

def _buy(stock, date, net):
    r = [""] * 16
    r[A], r[F], r[P] = stock, date, net
    return r

# 寫入前先修正既有單格(本批不需要,清空以免誤改)
PRE_FIX = []

# 第四批(交割明細擷圖,由新到舊,只記集買;證券信託款排除)
# 純數字代號前置 ' 以文字寫入,保留前導零。
ROWS = [
    _buy ("'00662", "2026/6/1",  24050),   # 富邦NASD
    _buy ("'0050",  "2026/6/1",  34379),   # 元大台灣
    _buy ("'0050",  "2026/6/1",  20177),   # 元大台灣
    _buy ("'2327",  "2026/5/27", 202072),  # 國巨*(待核對)
    _buy ("'2327",  "2026/5/26", 62053),   # 國巨*(待核對)
    _buy ("'00919", "2026/5/22", 234450),  # 群益臺灣(待核對)
    _buy ("'2308",  "2026/5/21", 20017),   # 台達電
    _buy ("00981A", "2026/5/21", 55227),   # 主動統一
    _buy ("'00662", "2026/5/21", 47060),   # 富邦NASD
    _buy ("'0050",  "2026/5/21", 30275),   # 元大台灣
    _buy ("'2454",  "2026/5/20", 64054),   # 聯發科
    _buy ("'2308",  "2026/5/20", 40334),   # 台達電
    _buy ("'00662", "2026/5/20", 11755),   # 富邦NASD
]


# =========================================================
# 認證
# =========================================================
def get_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                sys.exit(f"找不到 {CREDENTIALS_FILE},請先依 SHEETS_API_SETUP.md 取得 OAuth 憑證。")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("sheets", "v4", credentials=creds)


def col_letter(n):
    """0-based 欄索引 -> A1 欄字母"""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# =========================================================
# inspect:印出分頁結構,確認位址
# =========================================================
def inspect(service, max_rows=40, max_cols=26):
    rng = f"{SHEET_NAME}!A1:{col_letter(max_cols-1)}{max_rows}"
    resp = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=rng
    ).execute()
    values = resp.get("values", [])
    print(f"分頁「{SHEET_NAME}」前 {max_rows} 列(只顯示有值的欄):\n")
    for i, row in enumerate(values, start=1):
        cells = []
        for j, v in enumerate(row):
            if str(v).strip() != "":
                cells.append(f"{col_letter(j)}{i}={v}")
        if cells:
            print(f"列{i:>3}: " + " | ".join(cells))
    print("\n→ 找到『標的』那一欄的字母、以及最新一筆資料在第幾列,")
    print("  回頭把腳本上方的 START_CELL 改成正確位址(例如 G2)。")


# =========================================================
# write:在 INSERT_ROW 上方插入空列後寫入(先預覽,確認才寫)
# =========================================================
def get_sheet_id(service):
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    for sh in meta.get("sheets", []):
        if sh["properties"]["title"] == SHEET_NAME:
            return sh["properties"]["sheetId"]
    sys.exit(f"找不到分頁「{SHEET_NAME}」")


def write(service):
    n = len(ROWS)
    end_row = INSERT_ROW + n - 1
    rng = f"{SHEET_NAME}!A{INSERT_ROW}:P{end_row}"
    hdr = ["A股票", "F買進日", "J賣出日", "P應收付"]
    print(f"即將在分頁「{SHEET_NAME}」第 {INSERT_ROW} 列上方插入 {n} 個空列,")
    print(f"再把資料寫入 {rng}。預覽(只列有值的欄):\n")
    for i, r in enumerate(ROWS, start=INSERT_ROW):
        vals = []
        for idx, name in [(A, "A股票"), (F, "F買進日"), (J, "J賣出日"), (P, "P應收付")]:
            if r[idx] != "":
                vals.append(f"{name}={r[idx]}")
        print(f"  第{i}列: " + " | ".join(vals))
    ans = input("\n確認插入並寫入?(yes/no): ").strip().lower()
    if ans != "yes":
        print("已取消,未變更任何資料。")
        return

    sheet_id = get_sheet_id(service)
    # 0) 寫入前先修正既有單格(在插入列之前,趁位址未位移)
    for cell, val in PRE_FIX:
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{SHEET_NAME}!{cell}",
            valueInputOption="USER_ENTERED",
            body={"values": [[val]]},
        ).execute()
        print(f"已修正 {cell} = {val}")
    # 1) 插入空列
    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"requests": [{
            "insertDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": INSERT_ROW - 1,   # 0-based,inclusive
                    "endIndex": INSERT_ROW - 1 + n  # exclusive
                },
                "inheritFromBefore": False
            }
        }]}
    ).execute()
    # 2) 寫入值
    resp = service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=rng,
        valueInputOption="USER_ENTERED",   # 讓日期/數字依儲存格格式解析
        body={"values": ROWS},
    ).execute()
    print(f"完成。已插入 {n} 列並更新 {resp.get('updatedCells')} 個儲存格,"
          f"範圍 {resp.get('updatedRange')}")


def main():
    ap = argparse.ArgumentParser(description="股票買賣紀錄 寫回工具")
    ap.add_argument("mode", choices=["inspect", "write"], help="inspect=看結構 / write=寫入")
    ap.add_argument("--rows", type=int, default=40, help="inspect 讀取列數")
    args = ap.parse_args()

    service = get_service()
    if args.mode == "inspect":
        inspect(service, max_rows=args.rows)
    else:
        write(service)


if __name__ == "__main__":
    main()
