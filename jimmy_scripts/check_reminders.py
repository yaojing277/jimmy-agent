#!/usr/bin/env python3
# 每日到期提醒 — 由 GitHub Actions 每天執行
# 讀取 reminders.json，對今天到期的項目推播 LINE，並移除已推播的紀錄

import json
import os
import re
import requests
from datetime import date, datetime, timezone, timedelta

LINE_TOKEN   = os.environ.get("LINE_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")
if not (LINE_TOKEN and LINE_USER_ID):
    raise SystemExit("缺少環境變數 LINE_TOKEN / LINE_USER_ID(請設 GitHub Secrets 或本機 export)。")
REMINDERS_FILE = "reminders.json"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def send_line(message: str) -> bool:
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
        return False
    print("[LINE] 推播成功")
    return True


def main():
    tz_tw = timezone(timedelta(hours=8))
    today = datetime.now(tz_tw).strftime("%Y-%m-%d")
    print(f"[INFO] 今天日期（台灣）：{today}")

    if not os.path.exists(REMINDERS_FILE):
        print("[INFO] reminders.json 不存在，跳過")
        return

    with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
        reminders = json.load(f)

    # ⚠ 到期判斷用 `<=` 而不是 `==`:GitHub cron 遲到 5~8h 是常態,只要哪天遲到超過
    #   約 9h40m 跨過午夜,`==` 就再也不會符合——那筆提醒會留在檔案裡、但日期已過,
    #   從此永遠不推、也不留任何痕跡(判斷鐵律 5:靜默失效比報錯危險)。
    #   改成「到期或逾期都推」,失敗方向從「靜默漏掉」變成「晚一天推到」(判斷鐵律 9)。
    # ⚠ remind_on 缺值或格式不對的不參與比較(字串比大小會 TypeError),
    #   留在檔案裡並印 ::warning::——不要安靜吃掉。
    due, remaining, bad = [], [], []
    for r in reminders:
        d = r.get("remind_on")
        if not (isinstance(d, str) and DATE_RE.match(d)):
            bad.append(r)
            remaining.append(r)
            continue
        (due if d <= today else remaining).append(r)
    due.sort(key=lambda r: r["remind_on"])    # 逾期的先推
    for r in bad:
        print(f"::warning::remind_on 格式不對,未處理:{r.get('remind_on')!r} "
              f"note={str(r.get('note'))[:40]}")

    if not due:
        print("[INFO] 今天沒有到期提醒")
    else:
        print(f"[INFO] 發現 {len(due)} 筆到期提醒")

    failed = []
    for r in due:
        note    = r.get("note", "（無備註）")
        created = r.get("created_date", "不明")
        days    = r.get("days", "?")
        on      = r["remind_on"]
        # ⚠ 這行原本印 today,逾期推送時會顯示成「今天才是提醒日」而看不出遲到。
        late    = (date.fromisoformat(today) - date.fromisoformat(on)).days
        msg = (
            f"⏰ 到期提醒\n"
            f"備註：{note}\n"
            f"設定日期：{created}\n"
            f"提醒天數：{days} 天\n"
            f"提醒日：{on}" + (f"（逾期 {late} 天，今天才推）" if late > 0 else "")
        )
        print(f"[INFO] 傳送：{note}（提醒日 {on}，逾期 {late} 天）")
        if not send_line(msg):
            failed.append(r)

    # ⚠ 推播失敗的留在檔案裡,下一班再試。原本不論成敗都移除:LINE 掛掉那次的提醒
    #   會直接消失、而且沒有任何人知道——與上面那個 `==` 是同一種靜默失效。
    # ⚠ 刻意不 exit 非零:這一步沒有 continue-on-error,一旦失敗會中斷整個每日排程,
    #   讓 LINE 的暫時故障連坐掉股價同步。改以 ::warning:: 現形,提醒本身下一班自動重試。
    remaining += failed
    if failed:
        print(f"::warning::{len(failed)} 筆提醒推播失敗,保留在 reminders.json 下一班重試")

    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(remaining, f, ensure_ascii=False, indent=2)

    print(f"[INFO] 已推播並移除 {len(due) - len(failed)} 筆，"
          f"剩餘 {len(remaining)} 筆（含失敗保留 {len(failed)} 筆）")


if __name__ == "__main__":
    main()
