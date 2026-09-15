#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""etf_ex_dividend_calendar.py — 把「股價試算」持股中 ETF 已公布的除息日＋發放日同步進 Google 日曆。

資料源改用 TWSE 官方「ETF 收益分配彙整表」：
  https://www.twse.com.tw/rwd/zh/ETF/etfDiv?response=json
這張表本身就是「已公告」的收益分配時程（除息交易日／收益分配基準日／發放日／金額），
投信一公告就會出現在這張表，不必等除息日真的發生 —— 所以除了補過去的紀錄，
也會自動抓到「已公告但還沒發生」的未來除息日／發放日；只是投信還沒公告時，
這張表當然也查不到，那種情況本腳本就不會生也不會猜。

兩種用法：
  sync                                        # 全自動：直接用 Service Account 寫入 Google 日曆
                                                #   （需要環境變數 GOOGLE_SA_JSON；日曆需先分享給該 SA）
                                                #   查重靠 Calendar API 當天事件比對，無狀態、可在
                                                #   GitHub Actions 這種每次全新環境的地方安全重跑。
  check                                        # 印出「本機 state 檔」還沒同步的紀錄(JSON)
  mark <code> <kind> <date>                    # 標記某筆已同步進 state 檔（kind: ex/pay）

sync 是給 wealth_sync.yml 排程用的全自動路徑；check/mark 是本機沒有 GOOGLE_SA_JSON、
用 Claude 對話裡已連線的 Calendar MCP 手動建事件時的輔助路徑，兩者的資料來源、
過濾規則完全相同，只是「誰負責實際建立事件、怎麼查重」不同。
"""
import os
import sys
import json
import re
import datetime
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import update_stock_price as usp   # 有 get_service()（含 SA fallback）+ resolve_latest_sheet()

STATE_FILE = os.path.join(HERE, "etf_dividend_calendar_state.json")
CALENDAR_ID = "yaojing277@gmail.com"
ETF_DIV_URL = "https://www.twse.com.tw/rwd/zh/ETF/etfDiv?response=json"
_ETF_RE = re.compile(r"^00\d{2,3}[A-Z]?$")
KIND_LABEL = {"ex": "除息", "pay": "發放"}


# ========================= 共用：取 ETF 清單 / 收益分配時程 =========================
def etf_codes():
    service = usp.get_service()
    sheet = usp.resolve_latest_sheet(service)
    resp = service.spreadsheets().values().get(
        spreadsheetId=usp.SPREADSHEET_ID, range=f"{sheet}!A2:A200").execute()
    rows = resp.get("values", [])
    return [str(r[0]).strip() for r in rows if r and _ETF_RE.match(str(r[0]).strip())]


def _roc_date(s):
    """"115年09月08日" -> date(2026, 9, 8)。"""
    if not s:
        return None
    s = str(s).strip()
    m = re.match(r"^(\d+)年(\d+)月(\d+)日$", s)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    return datetime.date(y + 1911, mo, d)


def distributions(codes):
    """回傳 [{"code","amount","ex_date","pay_date"}, ...]，TWSE 已公告的收益分配時程。"""
    with urllib.request.urlopen(ETF_DIV_URL, timeout=10) as r:
        d = json.loads(r.read().decode("utf-8"))
    codeset = set(codes)
    out = []
    for row in d.get("data", []):
        code = str(row[0]).strip()
        if code not in codeset:
            continue
        ex_date = _roc_date(row[2])
        pay_date = _roc_date(row[4])
        if not ex_date or not pay_date:
            continue
        amount = row[5] if len(row) > 5 else None
        out.append({"code": code, "amount": amount,
                     "ex_date": ex_date.isoformat(), "pay_date": pay_date.isoformat()})
    return out


def _events_for(dists):
    """把 distributions() 結果拆成 (code, kind, date, amount) 逐筆事件。"""
    for rec in dists:
        amt = rec["amount"]
        amt_str = f"{amt}元" if amt not in (None, "", "null") else "待公告"
        yield (rec["code"], "ex", rec["ex_date"], amt_str)
        yield (rec["code"], "pay", rec["pay_date"], amt_str)


# ========================= sync：Service Account 全自動路徑 =========================
def get_calendar_service():
    sa_json = os.environ.get("GOOGLE_SA_JSON")
    if not sa_json:
        sys.exit("找不到環境變數 GOOGLE_SA_JSON，sync 只能在有 Service Account 金鑰的環境執行"
                  "（例如 GitHub Actions）。本機請改用 check / mark。")
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(
        json.loads(sa_json), scopes=["https://www.googleapis.com/auth/calendar.events"])
    return build("calendar", "v3", credentials=creds)


def find_event(cal, code, kind, date_str):
    """回傳當天已存在、標題前綴符合「代號 除息/發放」的事件(找不到回 None)。"""
    day = datetime.date.fromisoformat(date_str)
    time_min = f"{day.isoformat()}T00:00:00Z"
    time_max = f"{(day + datetime.timedelta(days=1)).isoformat()}T00:00:00Z"
    resp = cal.events().list(calendarId=CALENDAR_ID, timeMin=time_min, timeMax=time_max,
                              singleEvents=True).execute()
    prefix = f"{code} {KIND_LABEL[kind]}"
    for ev in resp.get("items", []):
        if ev.get("summary", "").startswith(prefix):
            return ev
    return None


def create_dividend_event(cal, code, kind, date_str, amt_str):
    day = datetime.date.fromisoformat(date_str)
    next_day = day + datetime.timedelta(days=1)
    body = {
        "summary": f"{code} {KIND_LABEL[kind]} {amt_str}",
        "start": {"date": day.isoformat()},
        "end": {"date": next_day.isoformat()},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 540}]},
    }
    cal.events().insert(calendarId=CALENDAR_ID, body=body).execute()


def update_event_summary(cal, event_id, summary):
    cal.events().patch(calendarId=CALENDAR_ID, eventId=event_id, body={"summary": summary}).execute()


def cmd_sync():
    cal = get_calendar_service()
    created = 0
    updated = 0
    for code, kind, date_str, amt_str in _events_for(distributions(etf_codes())):
        new_summary = f"{code} {KIND_LABEL[kind]} {amt_str}"
        ev = find_event(cal, code, kind, date_str)
        if ev is None:
            create_dividend_event(cal, code, kind, date_str, amt_str)
            created += 1
            print(f"已建立:{new_summary}（{date_str}）")
        elif ev.get("summary") == f"{code} {KIND_LABEL[kind]} 待公告" and amt_str != "待公告":
            # 之前金額還沒公告時建的佔位事件，現在補上正式金額
            update_event_summary(cal, ev["id"], new_summary)
            updated += 1
            print(f"已更新:{ev.get('summary')} → {new_summary}（{date_str}）")
    print(f"共新增 {created} 筆、更新 {updated} 筆")


# ========================= check / mark：本機 state 檔輔助路徑 =========================
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"synced": []}   # ["00878|ex|2026-08-18", ...]


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def cmd_check():
    state = load_state()
    synced = set(state["synced"])
    new_records = [
        {"code": code, "kind": kind, "date": date_str, "amount": amt_str}
        for code, kind, date_str, amt_str in _events_for(distributions(etf_codes()))
        if f"{code}|{kind}|{date_str}" not in synced
    ]
    print(json.dumps(new_records, ensure_ascii=False, indent=2))


def cmd_mark(code, kind, date_str):
    state = load_state()
    key = f"{code}|{kind}|{date_str}"
    if key not in state["synced"]:
        state["synced"].append(key)
        save_state(state)
    print(f"已標記同步:{key}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "sync":
        cmd_sync()
    elif cmd == "check":
        cmd_check()
    elif cmd == "mark" and len(sys.argv) == 5:
        cmd_mark(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        sys.exit(__doc__)
