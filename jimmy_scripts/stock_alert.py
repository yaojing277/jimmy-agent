#!/usr/bin/env python3
# 股價跌幅警示 - 與今日開盤相比，每增加 1% 跌幅通知一次
# 每 10 分鐘由 cron 執行一次（週一至週五 09:00~14:00）

import csv
import io
import json
import os
import requests
from datetime import datetime, timezone, timedelta

from price_source import get_quotes

# ===== 設定區 =====
LINE_TOKEN   = os.environ.get("LINE_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")
if not (LINE_TOKEN and LINE_USER_ID):
    raise SystemExit("缺少環境變數 LINE_TOKEN / LINE_USER_ID(請設 GitHub Secrets 或本機 export)。")

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


def send_line(message: str):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_TOKEN}",
    }
    body = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": message}],
    }
    res = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers=headers,
        data=json.dumps(body),
    )
    if res.status_code != 200:
        print(f"[LINE ERROR] {res.status_code} {res.text}")
    else:
        print("[LINE] 推播成功")


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
        message = f"📉 跌幅警示 {now}\n\n" + "\n\n".join(alerts)
        send_line(message)
        print(message)

    if state_changed:
        save_state(state)

    if not alerts:
        print(f"[{now}] 無觸發警示")


if __name__ == "__main__":
    main()
