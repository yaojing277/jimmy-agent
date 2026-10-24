#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""etf_ex_dividend_calendar.py — 把「股價試算」持股（ETF＋個股）已公布的除息日＋發放日同步進 Google 日曆。

ETF：TWSE 官方「ETF 收益分配彙整表」
  https://www.twse.com.tw/rwd/zh/ETF/etfDiv?response=json
這張表本身就是「已公告」的收益分配時程（除息交易日／收益分配基準日／發放日／金額），
投信一公告就會出現在這張表，不必等除息日真的發生 —— 所以除了補過去的紀錄，
也會自動抓到「已公告但還沒發生」的未來除息日／發放日；只是投信還沒公告時，
這張表當然也查不到，那種情況本腳本就不會生也不會猜。

個股（上市）：除息日＋現金股利一律取 TWSE 官方
  已發生：TWT49U 除權除息計算結果表（twse_hist.dividends）
  未發生：TWT48U 除權除息預告表
TWSE 沒有提供個股發放日，發放日改取 Yahoo 股市股利頁的 cashPayDate（以除息日對應）；
Yahoo 抓不到時只建除息事件、不猜發放日。A 欄是中文名稱的列用 update_stock_price.ALIAS 轉代號。

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
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import twse_hist
import update_stock_price as usp   # 有 get_service()（含 SA fallback）+ resolve_latest_sheet() + ALIAS

STATE_FILE = os.path.join(HERE, "etf_dividend_calendar_state.json")
CALENDAR_ID = "yaojing277@gmail.com"
ETF_DIV_URL = "https://www.twse.com.tw/rwd/zh/ETF/etfDiv?response=json"
TWT48U_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT48U?response=json"
YAHOO_DIV_URL = "https://tw.stock.yahoo.com/quote/{code}.TW/dividend"
_ETF_RE = re.compile(r"^00\d{2,3}[A-Z]?$")
_STOCK_RE = re.compile(r"^\d{4}$")
KIND_LABEL = {"ex": "除息", "pay": "發放"}


# ========================= 共用：取持股清單 =========================
def holdings():
    """回傳 (etf_codes, stock_codes)，讀「股價試算」最新 Jimmy_YYMMDD 分頁 A 欄。"""
    service = usp.get_service()
    sheet = usp.resolve_latest_sheet(service)
    resp = service.spreadsheets().values().get(
        spreadsheetId=usp.SPREADSHEET_ID, range=f"{sheet}!A2:A200").execute()
    etfs, stocks = [], []
    for r in resp.get("values", []):
        if not r:
            continue
        v = str(r[0]).strip()
        if _ETF_RE.match(v):
            etfs.append(v)
            continue
        code = usp.ALIAS.get(v, v)
        if _STOCK_RE.match(code):
            stocks.append(code)
        elif v:
            print(f"# A 欄「{v}」對不到台股代號,略過(請補 update_stock_price.ALIAS)", file=sys.stderr)
    return etfs, stocks


def etf_codes():
    return holdings()[0]


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


def _fmt_amount(x):
    """1.0 -> "1"、2.50020000 -> "2.5002"；None/空字串 -> None。"""
    if x in (None, "", "null"):
        return None
    try:
        return f"{float(x):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return None


_YAHOO_BUDGET_SEC = 180    # 整輪 Yahoo 查詢的時間上限；排程整個 job 只有 15 分鐘
_yahoo_deadline = None


def _yahoo_dividends(code, tries=3):
    """{除息日: {"pay": 現金股利發放日, "cash": 每股現金股利}}，取自 Yahoo 股市股利頁；抓不到回 {}。
    Yahoo 常斷線或逾時，所以重試幾次；超過整輪時間上限就直接放棄，缺的發放日隔天排程再補。"""
    global _yahoo_deadline
    if _yahoo_deadline is None:
        _yahoo_deadline = time.time() + _YAHOO_BUDGET_SEC
    req = urllib.request.Request(YAHOO_DIV_URL.format(code=code),
                                 headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    html = None
    for i in range(tries):
        if time.time() > _yahoo_deadline:
            print(f"# {code} Yahoo 查詢已超過時間上限,本輪略過", file=sys.stderr)
            return {}
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                html = r.read().decode("utf-8", "replace")
            break
        except Exception as e:
            if i == tries - 1:
                print(f"# {code} Yahoo 股利頁讀取失敗: {e}", file=sys.stderr)
                return {}
            time.sleep(3)
    out = {}
    for m in re.finditer(r'\{[^{}]*"cashPayDate"[^{}]*\}', html):
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            continue
        ex = (obj.get("date") or "")[:10]
        if not ex:
            continue
        rec = out.setdefault(ex, {})
        if obj.get("cashPayDate"):
            rec["pay"] = obj["cashPayDate"][:10]
        if obj.get("cash") not in (None, ""):
            rec["cash"] = obj["cash"]
    return out


def stock_distributions(codes, need_yahoo=lambda code, ex, kind: True):
    """個股今年以來已發生＋已預告的除息：[{"code","amount","ex_date","pay_date"}, ...]。
    pay_date 可能為 None（Yahoo 尚無資料）。

    金額：純「息」用 TWSE 官方值（即實際每股現金，含股本變動調整）；「權息」的官方值是
    參考價扣減值、混有股票股利，改用 Yahoo 的現金股利，取不到就標待公告，不拿混算值充數。"""
    codeset = set(codes)
    today = datetime.date.today()
    recs = {}   # (code, ex_date) -> (kind, amount)
    for dt, c, kind, val in twse_hist._fetch_exright(datetime.date(today.year, 1, 1), today):
        if c in codeset and "息" in kind:
            recs[(c, dt.isoformat())] = (kind, val)
    try:
        with urllib.request.urlopen(TWT48U_URL, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8"))
        for row in d.get("data", []):
            code = str(row[1]).strip()
            ex = _roc_date(row[0])
            if code in codeset and ex and "息" in str(row[3]):
                # 預告表的「現金股利」欄本來就只含現金
                recs.setdefault((code, ex.isoformat()), ("息", row[7] if len(row) > 7 else None))
    except Exception as e:
        print(f"# TWT48U 除權除息預告表讀取失敗: {e}", file=sys.stderr)
    yahoo = {}
    out = []
    for (code, ex), (kind, val) in sorted(recs.items()):
        y = {}
        if need_yahoo(code, ex, kind):
            if code not in yahoo:
                yahoo[code] = _yahoo_dividends(code)
            y = yahoo[code].get(ex, {})
        amount = _fmt_amount(val) if kind == "息" else _fmt_amount(y.get("cash"))
        out.append({"code": code, "amount": amount, "ex_date": ex, "pay_date": y.get("pay")})
    return out


def all_distributions():
    etfs, stocks = holdings()
    return distributions(etfs) + stock_distributions(stocks)


def _events_for(dists):
    """把 distributions() 結果拆成 (code, kind, date, amount) 逐筆事件；沒有發放日就不產生發放事件。"""
    for rec in dists:
        amt = rec["amount"]
        amt_str = f"{amt}元" if amt not in (None, "", "null") else "待公告"
        yield (rec["code"], "ex", rec["ex_date"], amt_str)
        if rec.get("pay_date"):
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


def _load_events(cal, dates):
    """一次讀進涵蓋所有日期的日曆事件，回傳 {日期: [事件,...]}；避免每筆各打一次 API。"""
    if not dates:
        return {}
    lo = min(dates)
    hi = (datetime.date.fromisoformat(max(dates)) + datetime.timedelta(days=1)).isoformat()
    by_day, token = {}, None
    while True:
        resp = cal.events().list(calendarId=CALENDAR_ID, timeMin=f"{lo}T00:00:00Z",
                                  timeMax=f"{hi}T00:00:00Z", singleEvents=True,
                                  maxResults=2500, pageToken=token).execute()
        for ev in resp.get("items", []):
            day = ev.get("start", {}).get("date") or ev.get("start", {}).get("dateTime", "")[:10]
            by_day.setdefault(day, []).append(ev)
        token = resp.get("nextPageToken")
        if not token:
            return by_day


def cmd_sync():
    cal = get_calendar_service()
    created = 0
    updated = 0
    etfs, stocks = holdings()
    etf_d = distributions(etfs)
    today = datetime.date.today()
    span = [f"{today.year}-01-01", (today + datetime.timedelta(days=180)).isoformat()]
    span += [d[k] for d in etf_d for k in ("ex_date", "pay_date")]
    by_day = _load_events(cal, span)

    def _titles(code, label, lo, hi):
        d0 = datetime.date.fromisoformat(lo)
        d1 = datetime.date.fromisoformat(hi)
        prefix = f"{code} {label}"
        return [e.get("summary", "") for day, evs in by_day.items()
                if d0.isoformat() <= day <= d1.isoformat() for e in evs
                if e.get("summary", "").startswith(prefix)]

    def need_yahoo(code, ex, kind):
        # 發放事件已在除息後 90 天內、且（純息不靠 Yahoo 金額，或除息標題已有金額）→ 不必再查 Yahoo
        pay_end = (datetime.date.fromisoformat(ex) + datetime.timedelta(days=90)).isoformat()
        pay_ok = bool(_titles(code, KIND_LABEL["pay"], ex, pay_end))
        ex_ok = any(not t.endswith("待公告") for t in _titles(code, KIND_LABEL["ex"], ex, ex))
        return not (pay_ok and (kind == "息" or ex_ok))

    events = list(_events_for(etf_d + stock_distributions(stocks, need_yahoo)))
    for code, kind, date_str, amt_str in events:
        new_summary = f"{code} {KIND_LABEL[kind]} {amt_str}"
        prefix = f"{code} {KIND_LABEL[kind]}"
        if min(span) <= date_str <= max(span):
            ev = next((e for e in by_day.get(date_str, []) if e.get("summary", "").startswith(prefix)), None)
        else:   # 不在一次讀進來的範圍內（例如很晚的發放日），改單筆查，避免重複建立
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
        for code, kind, date_str, amt_str in _events_for(all_distributions())
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
