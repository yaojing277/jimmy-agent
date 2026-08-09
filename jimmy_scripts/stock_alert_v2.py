#!/usr/bin/env python3
# 股價跌幅警示 - 與今日開盤相比，每增加 1% 跌幅通知一次
# 每 10 分鐘由 cron 執行一次（週一至週五 09:00~14:00）

import csv
import io
import json
import os
import smtplib
import requests
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from price_source import get_quotes

# ===== 設定區 =====
GMAIL_ADDRESS = "yaojing277@gmail.com"
GMAIL_APP_PWD = os.environ.get("GMAIL_APP_PASSWORD", "")

STOCKS_SHEET_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vS-PlFAVvZbWQXI3WUhUowmT8xeuUU_oZwxcnFDZAlD9sKtBhvBuvcKUDo-lI2yNf8A-FpkRNiIb7m_/pub?gid=325315358&single=true&output=csv"
ALERT_START = 2   # 從跌 2% 開始，每增加 1% 通知一次
STATE_PATH  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_alert_state.json")
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


def load_state() -> dict:
    tz_tw = timezone(timedelta(hours=8))
    today = datetime.now(tz_tw).strftime("%Y-%m-%d")
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
        if state.get("date") == today and "thresholds" in state:
            return state
    return {"date": today, "thresholds": {}}


def save_state(state: dict):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


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
    tz_tw = timezone(timedelta(hours=8))
    now = datetime.now(tz_tw).strftime("%Y/%m/%d %H:%M")

    stocks = load_stocks()
    if not stocks:
        print("[WARN] 無法讀取股票清單")
        return

    state = load_state()
    alerts = []
    state_changed = False

    quotes = get_quotes(list(stocks.values()))

    for name, symbol in stocks.items():
        q = quotes.get(symbol)
        if not q or not q.get("open"):
            continue

        price = q["price"]
        open_price = q["open"]
        pct = (price - open_price) / open_price
        print(f"{symbol}  現價 {price:.2f}  開盤 {open_price:.2f}  {pct*100:.2f}%")

        # 計算當前已跌過的整數門檻（跌 2.5% → level=2，跌 3.1% → level=3）
        current_level = int(abs(pct * 100)) if pct < 0 else 0
        last_level = state["thresholds"].get(symbol, 0)

        if current_level >= ALERT_START and current_level > last_level:
            alerts.append(
                f"⚠️ {name}（{symbol}）\n"
                f"  現價：{price:.2f}\n"
                f"  開盤：{open_price:.2f}\n"
                f"  跌幅：{pct*100:.2f}%（觸發 -{current_level}% 警示）"
            )
            state["thresholds"][symbol] = current_level
            state_changed = True

    if alerts:
        subject = f"📉 跌幅警示 {now}"
        body = f"📉 跌幅警示 {now}\n\n" + "\n\n".join(alerts)
        send_gmail(subject, body)
        print(body)

    if state_changed:
        save_state(state)

    if not alerts:
        print(f"[{now}] 無觸發警示")


if __name__ == "__main__":
    main()
