"""
「7/3 起加碼損益」每日結算（2026-10-04 起；排程：wealth_sync.yml 每個交易日 14:30）。

算法（與 2026-10-04 對話中手算版本逐格比對一致）：
  1. 讀「股票買賣紀錄」全部買賣，依「股票分割」分頁把分割前股數換算成分割後，逐代號 FIFO 配對。
  2. 起算日（含）之後的買進批次：
       - 已被賣出的部分 → 已實現損益＝分攤後賣出實收 − 分攤成本（實收已扣費）
       - 仍持有的部分   → 以結算日收盤價市值，扣「假設今天全賣」的手續費（0.1425%×6折，捨去，最低 20）
                          與證交稅（00 開頭 ETF 0.1%、個股 0.3%，捨去）
  3. 配息：起算日～結算日間除息、且買進日 < 除息日 <= 賣出日（或仍持有）的批次才計入。
     上市取 TWT49U（權值+息值）、上櫃取櫃買 exDailyQ（息值）。
  4. 結果整張重寫到分頁「加碼損益_0703」，右側 M:P 保留每日走勢（日期由新到舊，同日重跑覆寫）。
  5. --line-file 指定時輸出一行 LINE 摘要（給 workflow 併進每日通知）。

用法：
  python3 since_date_pnl.py --dry-run                   # 只印結果，不寫入
  python3 since_date_pnl.py --date 2026-10-02           # 指定結算日（預設今天；非交易日取最近收盤）
  python3 since_date_pnl.py --date ... --line-file since_pnl_line.txt
"""
import argparse
import collections
import datetime as dt
import json
import math
import re
import sys
import urllib.request
import warnings

warnings.filterwarnings("ignore")
import twse_hist as th
from update_stock_price import get_service, ALIAS, SPREADSHEET_ID

SINCE = dt.date(2026, 7, 3)
OUT_SHEET = "加碼損益_0703"
TRADE_SHEET = "股票買賣紀錄"
SPLIT_SHEET = "股票分割"
FEE_RATE, FEE_DISCOUNT, FEE_MIN = 0.001425, 0.6, 20
EXTRA_ALIAS = {"穎崴": "6515", "台積電": "2330"}
BASE = dt.date(1899, 12, 30)
HIST_COL = "M"   # 每日走勢 M:P


# ───────────────────────── 讀資料 ─────────────────────────
def to_code(a):
    a = str(a).strip().lstrip("'")
    return th.norm_code(a) or ALIAS.get(a) or EXTRA_ALIAS.get(a) or a


def as_text(x):
    """純數字字串（0050、00662…）加 ' 前綴，避免被 USER_ENTERED 吃掉前導零。"""
    x = str(x)
    return "'" + x if x.isdigit() else x


def serial(x):
    return BASE + dt.timedelta(days=int(x)) if isinstance(x, (int, float)) and x > 0 else None


def read_trades(svc):
    rows = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{TRADE_SHEET}!A1:Z2000",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    num = lambda x: isinstance(x, (int, float))
    tx = []
    for i, r in enumerate(rows[1:], 2):
        r = (r + [""] * 26)[:26]
        name = str(r[0]).strip()
        if not name or name.endswith("_刪"):
            continue
        if serial(r[5]) and num(r[6]) and num(r[7]):
            tx.append(dict(row=i, name=name, code=to_code(name), d=serial(r[5]), side="B",
                           q=r[7], amt=r[9] if num(r[9]) else r[6] * r[7]))
        if serial(r[10]) and num(r[11]) and num(r[12]):
            tx.append(dict(row=i, name=name, code=to_code(name), d=serial(r[10]), side="S",
                           q=r[12], amt=r[16] if num(r[16]) else r[11] * r[12]))
    # 同日：買先於賣；同類依表上由下往上（表格由新到舊，越下面越早）
    tx.sort(key=lambda t: (t["d"], 0 if t["side"] == "B" else 1, -t["row"]))
    return tx


def read_splits(svc):
    rows = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{SPLIT_SHEET}!A2:C50",
        valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    out = collections.defaultdict(list)
    for r in rows:
        if len(r) < 3:
            continue
        m = re.search(r"1\s*拆\s*(\d+)", r[2])
        d = dt.datetime.strptime(r[1].replace("-", "/"), "%Y/%m/%d").date()
        if m:
            out[to_code(r[0])].append((d, int(m.group(1))))
    return out


# ───────────────────────── 計算 ─────────────────────────
def fifo(tx, splits):
    """回傳 since 之後買進的批次清單：每批 {code,name,d,q,cost,sold:[(d,q,cost,proceeds)]}，剩餘以 q/cost 表示。"""
    lots = collections.defaultdict(list)
    tracked = []
    for t in tx:
        q = t["q"]
        for sd, ratio in splits.get(t["code"], []):
            if t["d"] < sd:
                q *= ratio
        if t["side"] == "B":
            lot = dict(code=t["code"], name=t["name"], d=t["d"], q=q, cost=t["amt"],
                       q0=q, cost0=t["amt"], sold=[])
            lots[t["code"]].append(lot)
            if t["d"] >= SINCE:
                tracked.append(lot)
            continue
        need, per = q, t["amt"] / q
        while need > 1e-9 and lots[t["code"]]:
            lot = lots[t["code"]][0]
            take = min(need, lot["q"])
            cost = lot["cost"] * take / lot["q"]
            lot["sold"].append((t["d"], take, cost, per * take))
            lot["q"] -= take
            lot["cost"] -= cost
            need -= take
            if lot["q"] <= 1e-9:
                lots[t["code"]].pop(0)
    return tracked


def _json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def cash_dividends(start, end):
    """{code: [(ex_date, 每股現金股利)]}，上市＋上櫃。"""
    out = collections.defaultdict(set)
    for d, code, kind, val in th._fetch_exright(start, end):
        if "息" in kind:
            out[code].add((d, val))
    try:
        j = _json("https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?response=json"
                  f"&startDate={start:%Y/%m/%d}&endDate={end:%Y/%m/%d}")
        for t in j.get("tables", []):
            for r in t.get("data", []):
                y, m, d = (int(x) for x in str(r[0]).split("/"))
                cash = float(str(r[6]).replace(",", "") or 0)
                if cash > 0:
                    out[str(r[1]).strip()].add((dt.date(y + 1911, m, d), cash))
    except Exception as e:
        print(f"⚠ 櫃買除息資料取得失敗（上櫃配息未計入）：{e}")
    return {k: sorted(v) for k, v in out.items()}


def sell_cost(code, mv):
    fee = max(FEE_MIN, math.floor(mv * FEE_RATE * FEE_DISCOUNT))
    tax = math.floor(mv * (0.001 if code.startswith("00") else 0.003))
    return fee, tax


def compute(tracked, asof):
    divs = cash_dividends(SINCE, asof)
    per = collections.OrderedDict()
    div_rows = []
    for lot in tracked:
        p = per.setdefault(lot["code"], dict(code=lot["code"], name=lot["name"], q=0, cost=0.0,
                                             hold_q=0, hold_cost=0.0, sold_q=0, sold_cost=0.0,
                                             proceeds=0.0, div=0.0))
        p["name"] = lot["name"]
        p["q"] += lot["q0"]
        p["cost"] += lot["cost0"]
        p["hold_q"] += lot["q"]
        p["hold_cost"] += lot["cost"]
        for _, q, c, pr in lot["sold"]:
            p["sold_q"] += q
            p["sold_cost"] += c
            p["proceeds"] += pr
        # 配息：除息日當天仍持有的股數（買進 < 除息日，且賣出日 >= 除息日 的部分 + 仍持有）
        for exd, cash in divs.get(lot["code"], []):
            if not (lot["d"] < exd <= asof):
                continue
            q_on = lot["q"] + sum(q for sd, q, _, _ in lot["sold"] if sd >= exd)
            if q_on > 0:
                amt = round(q_on * cash)
                p["div"] += amt
                div_rows.append((lot["name"], lot["code"], exd, cash, q_on, amt))
    for p in per.values():
        p["close"] = None
        p["mv"] = p["fee"] = p["tax"] = 0
        if p["hold_q"] > 1e-9:
            px, note = th.close_on(p["code"], asof)
            if px is None:
                sys.exit(f"✗ 取不到 {p['name']}（{p['code']}）{asof} 收盤：{note}")
            p["close"] = px
            p["mv"] = round(p["hold_q"] * px)
            p["fee"], p["tax"] = sell_cost(p["code"], p["mv"])
        p["unreal"] = p["mv"] - p["fee"] - p["tax"] - p["hold_cost"] if p["hold_q"] > 1e-9 else 0
        p["real"] = p["proceeds"] - p["sold_cost"]
        p["net"] = p["unreal"] + p["real"] + p["div"]
    # 合併同代號配息列
    agg = collections.OrderedDict()
    for name, code, exd, cash, q, amt in div_rows:
        k = (code, exd)
        if k in agg:
            agg[k][4] += q
            agg[k][5] += amt
        else:
            agg[k] = [name, code, exd, cash, q, amt]
    return sorted(per.values(), key=lambda p: -p["cost"]), list(agg.values())


# ───────────────────────── 輸出 ─────────────────────────
def summary(per):
    cost = sum(p["cost"] for p in per)
    unreal = sum(p["unreal"] for p in per)
    real = sum(p["real"] for p in per)
    div = sum(p["div"] for p in per)
    net = unreal + real + div
    return dict(cost=round(cost), unreal=round(unreal), real=round(real), div=round(div),
                net=round(net), pct=net / cost * 100 if cost else 0,
                mv=sum(p["mv"] for p in per), n=len(per))


def read_history(svc):
    try:
        rows = svc.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range=f"{OUT_SHEET}!M2:P400",
            valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    except Exception:
        return []
    out = []
    for r in rows:
        if len(r) >= 3 and isinstance(r[0], (int, float)):
            out.append((serial(r[0]), r[1], r[2]))
    return out


def ensure_sheet(svc):
    meta = svc.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == OUT_SHEET:
            return s["properties"]["sheetId"], False
    r = svc.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": [
        {"addSheet": {"properties": {"title": OUT_SHEET, "gridProperties": {"frozenRowCount": 0}}}}]}).execute()
    return r["replies"][0]["addSheet"]["properties"]["sheetId"], True


def write_sheet(svc, per, divs, s, asof, hist):
    sid, created = ensure_sheet(svc)
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    prev = next(((d, n) for d, n, _ in hist if d and d < asof), None)
    delta = s["net"] - prev[1] if prev else None

    A = [[f"{SINCE:%-m/%-d} 起加碼損益", "", "", "", "", "", "", "", "", "", ""],
         [f"結算日 {asof:%Y/%m/%d} 收盤｜更新 {now:%Y/%m/%d %H:%M}｜由 since_date_pnl.py 自動產生，手動修改會被覆蓋"],
         [],
         ["項目", "金額"],
         ["買進總成本（含買進手續費）", s["cost"]],
         ["仍持有部分 淨損益（已扣賣出成本）", s["unreal"]],
         ["已賣出部分 已實現損益", s["real"]],
         ["現金配息", s["div"]],
         ["淨總損益", s["net"]],
         ["報酬率", s["pct"] / 100],
         ["較前一交易日", delta if delta is not None else ""],
         [],
         # 明細 A~K；報酬率另寫在 L 欄同列
         ["股票", "代號", "買進股數", "成本", "收盤價", "仍持有股數", "市值",
          "賣出手續費＋證交稅", "已實現損益", "配息", "淨損益"]]
    detail_start = len(A) + 1
    for p in per:
        A.append([as_text(p["name"]), as_text(p["code"]), p["q"],
                  round(p["cost"]), p["close"] if p["close"] is not None else "已賣出",
                  p["hold_q"], p["mv"], p["fee"] + p["tax"], round(p["real"]), round(p["div"]),
                  round(p["net"])])
    detail_end = len(A)
    A.append(["合計", "", "", s["cost"], "", "", s["mv"],
              sum(p["fee"] + p["tax"] for p in per), s["real"], s["div"], s["net"]])
    A += [[], ["期間配息（買進日早於除息日、除息日當天仍持有才計入）"],
          ["股票", "代號", "除息日", "每股現金股利", "符合股數", "配息金額"]]
    div_start = len(A) + 1
    for name, code, exd, cash, q, amt in divs:
        A.append([as_text(name), as_text(code), f"{exd:%Y/%m/%d}", cash, q, amt])
    if not divs:
        A.append(["（無）"])
    A += [[], ["計算依據"],
          ["・成本取「股票買賣紀錄」J 欄（含買進手續費）；賣出部位以 FIFO 配對，只計起算日後買進、後來賣出的那段"],
          ["・仍持有部位假設結算日全部賣出：手續費 0.1425%×6 折（捨去、最低 20 元）、證交稅 ETF 0.1%／個股 0.3%"],
          ["・配息：上市取 TWSE 除權息計算結果表、上櫃取櫃買中心；未扣匯費與二代健保補充保費"],
          ["・分割前股數依「股票分割」分頁換算；【待確認】補登列（如國巨 7/07）照表上數字計算"]]

    # 報酬率欄（L）：與明細同列
    L = [["報酬率"]] + [[(p["net"] / p["cost"]) if p["cost"] else ""] for p in per] + \
        [[s["net"] / s["cost"] if s["cost"] else ""]]

    # 走勢（M:P，日期由新到舊，同日覆寫）
    hist = [h for h in hist if h[0] != asof]
    hist.append((asof, s["net"], s["pct"] / 100))
    hist.sort(key=lambda h: h[0], reverse=True)
    H = [["日期", "淨總損益", "報酬率", "較前一筆"]]
    for k, (d, n, pc) in enumerate(hist):
        nxt = hist[k + 1][1] if k + 1 < len(hist) else ""
        H.append([f"{d:%Y/%m/%d}", n, pc, (n - nxt) if nxt != "" else ""])

    v = svc.spreadsheets().values()
    v.clear(spreadsheetId=SPREADSHEET_ID, range=f"{OUT_SHEET}!A1:P1000").execute()
    v.batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"valueInputOption": "USER_ENTERED", "data": [
        {"range": f"{OUT_SHEET}!A1", "values": A},
        {"range": f"{OUT_SHEET}!L{detail_start - 1}", "values": L},
        {"range": f"{OUT_SHEET}!M1", "values": H},
    ]}).execute()

    # 格式：每次重設（列數會變）
    def rng(r0, r1, c0, c1):
        return {"sheetId": sid, "startRowIndex": r0 - 1, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}

    def fmt(r, pattern):
        return {"repeatCell": {"range": r, "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": pattern}}},
                               "fields": "userEnteredFormat.numberFormat"}}

    def bold(r):
        return {"repeatCell": {"range": r, "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                               "fields": "userEnteredFormat.textFormat.bold"}}
    signed = '+#,##0;-#,##0;0'
    reqs = [
        {"repeatCell": {"range": rng(1, 1000, 0, 16), "cell": {"userEnteredFormat": {}}, "fields": "userEnteredFormat"}},
        {"repeatCell": {"range": rng(1, 1, 0, 1), "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 14}}},
                        "fields": "userEnteredFormat.textFormat"}},
        bold(rng(4, 4, 0, 2)), bold(rng(9, 10, 0, 2)), bold(rng(detail_start - 1, detail_start - 1, 0, 12)),
        bold(rng(detail_end + 1, detail_end + 1, 0, 12)), bold(rng(1, 1, 12, 16)),
        fmt(rng(5, 5, 1, 2), "#,##0"), fmt(rng(6, 9, 1, 2), signed), fmt(rng(10, 10, 1, 2), "+0.00%;-0.00%"),
        fmt(rng(11, 11, 1, 2), signed),
        fmt(rng(detail_start, detail_end + 1, 2, 4), "#,##0"), fmt(rng(detail_start, detail_end + 1, 4, 5), "#,##0.00"),
        fmt(rng(detail_start, detail_end + 1, 5, 8), "#,##0"), fmt(rng(detail_start, detail_end + 1, 8, 11), signed),
        fmt(rng(detail_start, detail_end + 1, 11, 12), "+0.0%;-0.0%"),
        fmt(rng(div_start, div_start + max(len(divs), 1), 3, 4), "0.00####"),
        fmt(rng(div_start, div_start + max(len(divs), 1), 4, 6), "#,##0"),
        fmt(rng(div_start, div_start + max(len(divs), 1), 2, 3), "yyyy/mm/dd"),
        fmt(rng(2, 400, 12, 13), "yyyy/mm/dd"),
        fmt(rng(2, 400, 13, 14), signed), fmt(rng(2, 400, 14, 15), "+0.00%;-0.00%"), fmt(rng(2, 400, 15, 16), signed),
        {"autoResizeDimensions": {"dimensions": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 16}}},
    ]
    # 台股慣例：正數紅、負數綠（條件式格式每次先清再加）
    meta = svc.spreadsheets().get(spreadsheetId=SPREADSHEET_ID, fields="sheets(properties.sheetId,conditionalFormats)").execute()
    n_cf = next((len(s_.get("conditionalFormats", [])) for s_ in meta["sheets"] if s_["properties"]["sheetId"] == sid), 0)
    reqs += [{"deleteConditionalFormatRule": {"sheetId": sid, "index": 0}} for _ in range(n_cf)]
    areas = [rng(6, 11, 1, 2), rng(detail_start, detail_end + 1, 8, 12), rng(2, 400, 13, 16)]
    for cond, color in (("NUMBER_GREATER", {"red": 0.78, "green": 0.06, "blue": 0.18}),
                        ("NUMBER_LESS", {"red": 0.07, "green": 0.5, "blue": 0.29})):
        reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {"ranges": areas, "booleanRule": {
            "condition": {"type": cond, "values": [{"userEnteredValue": "0"}]},
            "format": {"textFormat": {"foregroundColor": color}}}}}})
    svc.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": reqs}).execute()
    return delta


def print_report(per, divs, s, asof):
    print(f"結算日 {asof}｜{SINCE} 起 {s['n']} 檔")
    for p in per:
        tag = "已賣出" if p["close"] is None else f"{p['close']:>9,.2f}"
        print(f"  {p['name']:<7}{p['q']:>8,.0f} 股 成本{p['cost']:>11,.0f} {tag:>9} "
              f"未實現{p['unreal']:>+10,.0f} 已實現{p['real']:>+9,.0f} 配息{p['div']:>7,.0f} 淨{p['net']:>+10,.0f}")
    for d in divs:
        print(f"  配息 {d[0]} {d[2]} {d[3]} × {d[4]:,.0f} = {d[5]:,}")
    print(f"成本 {s['cost']:,}｜未實現 {s['unreal']:+,}｜已實現 {s['real']:+,}｜配息 {s['div']:+,}"
          f"｜淨總損益 {s['net']:+,}（{s['pct']:+.2f}%）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="結算日 YYYY-MM-DD 或 YYYY/MM/DD（預設今天）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--line-file", help="輸出 LINE 摘要一行到此檔")
    a = ap.parse_args()
    asof = dt.date.fromisoformat(a.date.replace("/", "-")) if a.date else dt.date.today()

    svc = get_service()
    tx = read_trades(svc)
    tracked = fifo(tx, read_splits(svc))
    per, divs = compute(tracked, asof)
    s = summary(per)
    print_report(per, divs, s, asof)
    if a.dry_run:
        print("（dry-run：未寫入）")
        return
    delta = write_sheet(svc, per, divs, s, asof, read_history(svc))
    print(f"✓ 已寫入分頁「{OUT_SHEET}」" + (f"，較前一交易日 {delta:+,}" if delta is not None else ""))
    if a.line_file:
        line = f"{SINCE:%-m/%-d}起加碼損益 {s['net']:+,}（{s['pct']:+.2f}%）"
        if delta is not None:
            line += f"，較前日 {delta:+,}"
        with open(a.line_file, "w", encoding="utf-8") as f:
            f.write(line)


if __name__ == "__main__":
    main()
