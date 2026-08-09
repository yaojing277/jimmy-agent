#!/usr/bin/env python3
# 每日到期提醒 — 由 GitHub Actions 每天執行
# 讀取 reminders.json，對今天到期的項目推播 LINE，並移除已推播的紀錄

import json
import os
import requests
from datetime import datetime, timezone, timedelta

LINE_TOKEN   = os.environ.get("LINE_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")
if not (LINE_TOKEN and LINE_USER_ID):
    raise SystemExit("缺少環境變數 LINE_TOKEN / LINE_USER_ID(請設 GitHub Secrets 或本機 export)。")
REMINDERS_FILE = "reminders.json"


def send_line(message: str):
    res = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_TOKEN}",
        },
        data=json.dumps({
            "to": LINE_USER_ID,
            "messages": [{"type": "text", "text": message}],
        }),
    )
    if res.status_code != 200:
        print(f"[LINE ERROR] {res.status_code} {res.text}")
    else:
        print("[LINE] 推播成功")


def main():
    tz_tw = timezone(timedelta(hours=8))
    today = datetime.now(tz_tw).strftime("%Y-%m-%d")
    print(f"[INFO] 今天日期（台灣）：{today}")

    if not os.path.exists(REMINDERS_FILE):
        print("[INFO] reminders.json 不存在，跳過")
        return

    with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
        reminders = json.load(f)

    due       = [r for r in reminders if r.get("remind_on") == today]
    remaining = [r for r in reminders if r.get("remind_on") != today]

    if not due:
        print("[INFO] 今天沒有到期提醒")
    else:
        print(f"[INFO] 發現 {len(due)} 筆到期提醒")

    for r in due:
        note    = r.get("note", "（無備註）")
        created = r.get("created_date", "不明")
        days    = r.get("days", "?")
        msg = (
            f"⏰ 到期提醒\n"
            f"備註：{note}\n"
            f"設定日期：{created}\n"
            f"提醒天數：{days} 天\n"
            f"提醒日：{today}"
        )
        print(f"[INFO] 傳送：{note}")
        send_line(msg)

    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(remaining, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 已移除 {len(due)} 筆，剩餘 {len(remaining)} 筆")


if __name__ == "__main__":
    main()
