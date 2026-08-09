#!/usr/bin/env python3
# 股價即時通知 - Gmail 版
# 每 30 分鐘由 cron 執行一次（週一至週五 09:00~14:00，共 11 次）

import csv
import io
import os
import pickle
import smtplib
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta

from price_source import get_quotes

# ===== 設定區 =====
GMAIL_ADDRESS = "yaojing277@gmail.com"
GMAIL_APP_PWD = os.environ.get("GMAIL_APP_PASSWORD", "")

STOCKS_SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vS-PlFAVvZbWQXI3WUhUowmT8xeuUU_oZwxcnFDZAlD9sKtBhvBuvcKUDo-lI2yNf8A-FpkRNiIb7m_/pub?gid=325315358&single=true&output=csv"
SHEET_ID   = "1iZ1cVWpY5HXWUNh8smwirFQQHWUAOO16Wh13hP0F5ME"
TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "token_sheets.pickle")
# ==================


def load_stocks() -> dict:
    try:
        res = requests.get(STOCKS_SHEET_URL, timeout=10)
        res.raise_for_status()
        res.encoding = "utf-8"
        reader = csv.DictReader(io.StringIO(res.text))
        return {row["名稱"]: row["代號"] for row in reader if row.get("代號")}
    except Exception as e:
        print(f"[ERROR] 無法讀取股票清單：{e}")
        return {}


def update_sheet_prices(symbol_price_map: dict, is_open: bool = False):
    if not os.path.exists(TOKEN_PATH):
        return
    try:
        import gspread
        from google.auth.transport.requests import Request

        with open(TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)
        if not creds.valid and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_PATH, "wb") as f:
                pickle.dump(creds, f)

        gc = gspread.authorize(creds)
        sheet = gc.open_by_key(SHEET_ID).sheet1
        rows = sheet.get_all_values()

        if not rows:
            return

        header = rows[0]
        if len(header) < 3:
            sheet.update_cell(1, 3, "現價")
        if len(header) < 4:
            sheet.update_cell(1, 4, "開盤價")

        updates = []
        for i, row in enumerate(rows[1:], start=2):
            if len(row) >= 2 and row[1] in symbol_price_map:
                price = round(symbol_price_map[row[1]], 2)
                updates.append({"range": f"C{i}", "values": [[price]]})
                if is_open:
                    updates.append({"range": f"D{i}", "values": [[price]]})

        if updates:
            sheet.spreadsheet.values_batch_update({"valueInputOption": "RAW", "data": updates})

        label = "現價 + 開盤價" if is_open else "現價"
        print(f"[Sheet] {label} 欄位更新完成")
    except Exception as e:
        print(f"[Sheet ERROR] {e}")


def send_gmail(subject: str, body: str):
    if not GMAIL_APP_PWD:
        print("[ERROR] 未設定 GMAIL_APP_PASSWORD 環境變數")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = GMAIL_ADDRESS
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PWD)
            server.sendmail(GMAIL_ADDRESS, GMAIL_ADDRESS, msg.as_string())
        print("[GMAIL] 寄送成功")
    except Exception as e:
        print(f"[GMAIL ERROR] {e}")


def main():
    stocks = load_stocks()
    if not stocks:
        send_gmail("⚠️ 股價通知異常", "無法讀取股票清單，請確認 Google Sheet 設定")
        return

    tz_tw = timezone(timedelta(hours=8))
    now_dt = datetime.now(tz_tw)
    is_open = (now_dt.hour == 9 and now_dt.minute < 30)
    now = now_dt.strftime("%Y/%m/%d %H:%M")

    lines = [f"📈 股價通知 {now}"]
    symbol_price_map = {}

    quotes = get_quotes(list(stocks.values()))

    for name, symbol in stocks.items():
        q = quotes.get(symbol)
        if q and q.get("prev_close"):
            price = q["price"]
            change = price - q["prev_close"]
            pct = change / q["prev_close"] * 100
            symbol_price_map[symbol] = price
            arrow = "▲" if change >= 0 else "▼"
            sign = "+" if change >= 0 else ""
            lines.append(
                f"\n{name}（{symbol}）\n"
                f"  現價：{price:.2f}\n"
                f"  {arrow} {sign}{change:.2f}（{sign}{pct:.2f}%）"
            )
        else:
            lines.append(f"\n{name}（{symbol}）：取得失敗")

    body = "\n".join(lines)
    subject = f"📈 股價通知 {now}"
    send_gmail(subject, body)
    print(body)
    update_sheet_prices(symbol_price_map, is_open=is_open)


if __name__ == "__main__":
    main()
