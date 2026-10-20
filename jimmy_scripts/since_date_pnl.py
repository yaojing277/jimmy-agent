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
  python3 since_date_pnl.py --date 2026-10-16 --dry-run --html investment_since_0703_report.html
  python3 since_date_pnl.py --until 2026-10-16 --dry-run --html investment_since_0703_1016_report.html
                                                        # 只算 7/3～10/16 買進的批次（結算日預設今天）
  python3 since_date_pnl.py --since 2026-10-20 --dry-run --html investment_since_1020_report.html
                                                        # 另一波加碼：10/20 起買進的批次
                                                        # 產出完整 HTML 報告（--dry-run 則只產 HTML、不寫分頁）
"""
import argparse
import collections
import datetime as dt
import json
import math
import re
import sys
import time
import urllib.request
import warnings

warnings.filterwarnings("ignore")
import twse_hist as th
from update_stock_price import get_service, ALIAS, SPREADSHEET_ID

SINCE = dt.date(2026, 7, 3)
UNTIL = None   # 買進結束日（含）；None＝不設上限（每日排程）。由 --until 設定，只影響「哪些買進算加碼」
OUT_SHEET = "加碼損益_0703"
TRADE_SHEET = "股票買賣紀錄"
SPLIT_SHEET = "股票分割"
FEE_RATE, FEE_DISCOUNT, FEE_MIN = 0.001425, 0.6, 20
EXTRA_ALIAS = {"穎崴": "6515", "台積電": "2330"}
BASE = dt.date(1899, 12, 30)
HIST_COL = "M"   # 每日走勢 M:P


# HTML 報告樣式（--html；cathay_since_pnl.py 會從產出的報告讀這段 <style> 沿用）
REPORT_CSS = """
/* 版面：單欄報表。摘要在上、損益分布圖居中、明細與計算依據在下；台股慣例紅漲綠跌 */
:root{
  --bg:#f6f5f2; --surface:#ffffff; --ink:#1d2129; --muted:#646b78; --line:#e2e0da;
  --accent:#1f4e79;            /* 深藍：標題與連結 */
  --up:#c8102e; --up-soft:#f7dde1;      /* 台股：賺＝紅 */
  --down:#13804b; --down-soft:#d9efe3;  /* 台股：賠＝綠 */
  --warn:#9a6700; --warn-soft:#fbefd0;
  --f-display:"Noto Serif TC","Songti TC",serif;
  --f-body:"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif;
  --f-num:"IBM Plex Mono",ui-monospace,"SF Mono",Menlo,monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#14171c; --surface:#1c2027; --ink:#e7e9ee; --muted:#9aa1ad; --line:#2d323b;
  --accent:#8fb8e3; --up:#ff6b7d; --up-soft:#3a1e24; --down:#4cc98a; --down-soft:#173326;
  --warn:#e3b34a; --warn-soft:#3a2f14; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#14171c; --surface:#1c2027; --ink:#e7e9ee; --muted:#9aa1ad; --line:#2d323b;
  --accent:#8fb8e3; --up:#ff6b7d; --up-soft:#3a1e24; --down:#4cc98a; --down-soft:#173326;
  --warn:#e3b34a; --warn-soft:#3a2f14; color-scheme:dark}

body{background:var(--bg);color:var(--ink);font:15px/1.65 var(--f-body)}
.wrap{max-width:960px;margin:0 auto;padding-inline:16px;padding-block:32px 56px;display:grid;gap:36px}
h1,h2{font-family:var(--f-display);text-wrap:balance;margin:0;color:var(--ink)}
h1{font-size:clamp(26px,4.5vw,36px);line-height:1.25}
h2{font-size:20px}
.eyebrow{font-size:12px;letter-spacing:.12em;color:var(--muted);text-transform:uppercase}
.lede{color:var(--muted);max-width:62ch;margin:8px 0 0}
.num{font-family:var(--f-num);font-variant-numeric:tabular-nums}
.up{color:var(--up)} .down{color:var(--down)}
section{display:grid;gap:14px;min-width:0}
.sec-head{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:6px 16px}
.sec-head p{margin:0;color:var(--muted);font-size:13px}

/* 摘要 */
.hero{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:16px;align-items:stretch}
.big{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:22px 24px;display:grid;gap:6px;align-content:start}
.big .label{font-size:13px;color:var(--muted)}
.big .value{font-family:var(--f-num);font-size:clamp(34px,7vw,48px);font-weight:600;line-height:1.1;color:var(--up)}
.big .pct{font-family:var(--f-num);font-size:18px;color:var(--up)}
.ledger{display:grid;gap:0;align-content:start;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:8px 20px}
.ledger div{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px dashed var(--line);font-size:14px}
.ledger div:last-child{border-bottom:0;font-weight:700}
.ledger span:last-child{font-family:var(--f-num);font-variant-numeric:tabular-nums;white-space:nowrap}

/* 損益分布 */
.bars{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 18px;display:grid;gap:7px}
.bar-row{display:grid;grid-template-columns:72px minmax(0,1fr) 96px;gap:10px;align-items:center;font-size:13px}
.bar-row .name{white-space:nowrap}
.bar-row .amt{text-align:right;font-family:var(--f-num);font-variant-numeric:tabular-nums}
.track{position:relative;height:14px}
.track::before{content:"";position:absolute;top:-4px;bottom:-4px;left:var(--zero);border-left:1px solid var(--muted);opacity:.5}
.track i{position:absolute;top:0;height:100%;border-radius:2px}
.track i.pos{background:var(--up)} .track i.neg{background:var(--down)}

/* 表格 */
.table-box{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:760px;font-size:13.5px}
th,td{padding:9px 12px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{font-weight:500;color:var(--muted);font-size:12px;letter-spacing:.04em;background:var(--bg)}
th:first-child,td:first-child{text-align:left}
td{font-family:var(--f-num);font-variant-numeric:tabular-nums}
td:first-child{font-family:var(--f-body)}
tbody tr:last-child td{border-bottom:0}
tfoot td{font-weight:600;border-top:2px solid var(--line);border-bottom:0}
.chip{display:inline-block;font-family:var(--f-body);font-size:11px;padding:1px 7px;border-radius:999px;margin-left:6px;vertical-align:1px}
.chip.sold{background:var(--bg);color:var(--muted);border:1px solid var(--line)}
.chip.warn{background:var(--warn-soft);color:var(--warn)}
.code{color:var(--muted);font-family:var(--f-num);font-size:12px;margin-left:4px}

/* 註記 */
.notes{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.note{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px 18px;min-width:0}
.note h3{margin:0 0 6px;font-size:14px}
.note p,.note ul{margin:0;color:var(--muted);font-size:13.5px}
.note ul{padding-left:18px;display:grid;gap:4px}
.note.warn{border-color:var(--warn);background:var(--warn-soft)}
.note.warn h3{color:var(--warn)}
footer{font-size:12px;color:var(--muted);border-top:1px solid var(--line);padding-top:14px}

@media (max-width:640px){
  .hero{grid-template-columns:1fr}
  .bar-row{grid-template-columns:58px minmax(0,1fr) 82px;font-size:12px}
}
/* 走勢 */
.trend{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.trend svg{display:block;width:100%;height:auto}
.trend text{font-family:var(--f-num);font-size:11px;fill:var(--muted)}
"""


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
                           q=r[7], amt=r[9] if num(r[9]) else r[6] * r[7],
                           warn=any("待確認" in str(c) for c in r)))
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
def period(sep="～"):
    """區間字樣：「7/3 起」或「7/3～10/16」。"""
    return f"{SINCE:%-m/%-d}{sep}{UNTIL:%-m/%-d}" if UNTIL else f"{SINCE:%-m/%-d} 起"


def fifo(tx, splits):
    """回傳 since（～until）之間買進的批次清單：每批 {code,name,d,q,cost,sold:[(d,q,cost,proceeds)]}，剩餘以 q/cost 表示。"""
    lots = collections.defaultdict(list)
    tracked = []
    for t in tx:
        q = t["q"]
        for sd, ratio in splits.get(t["code"], []):
            if t["d"] < sd:
                q *= ratio
        if t["side"] == "B":
            lot = dict(code=t["code"], name=t["name"], d=t["d"], q=q, cost=t["amt"],
                       q0=q, cost0=t["amt"], sold=[], warn=t.get("warn", False))
            lots[t["code"]].append(lot)
            if t["d"] >= SINCE and (UNTIL is None or t["d"] <= UNTIL):
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


def _json_retry(url, what, tries=3):
    """抓 JSON，失敗重試；全部失敗就中止。
    配息資料抓不到時寧可整步失敗、不寫入，也不要拿缺漏的資料照算——2026-10-08 雲端排程
    TWT49U 靜默失敗（twse_hist._fetch_exright 吞例外回空清單），0050 配息 1,800 被漏算卻照常寫入。"""
    last = None
    for i in range(tries):
        try:
            return _json(url)
        except Exception as e:
            last = e
            time.sleep(3 * (i + 1))
    sys.exit(f"✗ {what}取得失敗（重試 {tries} 次）：{last}；未寫入，避免漏算配息")


def cash_dividends(start, end):
    """{code: [(ex_date, 每股現金股利)]}，上市（TWT49U 權值+息值）＋上櫃（櫃買 exDailyQ 息值）。"""
    out = collections.defaultdict(set)
    j = _json_retry("https://www.twse.com.tw/rwd/zh/exRight/TWT49U?response=json"
                    f"&startDate={start:%Y%m%d}&endDate={end:%Y%m%d}", "TWSE 除權息資料")
    if j.get("stat") == "OK":
        for r in j.get("data", []):
            d, val = th._roc_date(r[0]), th._f(r[5])
            if d and val is not None and "息" in str(r[6]):
                out[str(r[1]).strip()].add((d, val))
    elif "沒有符合條件" not in str(j.get("stat")):     # 期間內確實沒資料是正常，其餘一律視為失敗
        sys.exit(f"✗ TWSE 除權息資料回應異常：{j.get('stat')}；未寫入")
    j = _json_retry("https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?response=json"
                    f"&startDate={start:%Y/%m/%d}&endDate={end:%Y/%m/%d}", "櫃買除息資料")
    for t in j.get("tables", []):
        for r in t.get("data", []):
            y, m, d = (int(x) for x in str(r[0]).split("/"))
            cash = float(str(r[6]).replace(",", "") or 0)
            if cash > 0:
                out[str(r[1]).strip()].add((dt.date(y + 1911, m, d), cash))
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
                                             proceeds=0.0, div=0.0, warn=False, n_buy=0))
        p["name"] = lot["name"]
        p["warn"] = p["warn"] or lot["warn"]
        p["n_buy"] += 1
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
          ["・分割前股數依「股票分割」分頁換算；買賣紀錄備註標【待確認】的列照表上數字計算"]]

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


def _esc(x):
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _sg(n, dec=0):
    """帶正負號的金額；負號用全形減號（與舊報告一致）。"""
    v = f"{abs(n):,.{dec}f}"
    return ("+" if n > 0 else "−" if n < 0 else "") + v


def _cls(n):
    return "up" if n > 0 else "down" if n < 0 else ""


def _trend_svg(hist):
    """每日淨總損益折線（hist：[(date, net, pct)]，任意順序）。少於 2 筆不畫。"""
    pts = sorted((h for h in hist if h[0]), key=lambda h: h[0])
    if len(pts) < 2:
        return ""
    W, H, PL, PR, PT, PB = 900, 240, 64, 12, 14, 26
    vals = [n for _, n, _ in pts] + [0]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * .08 or 1
    lo, hi = lo - pad, hi + pad
    n = len(pts)
    X = lambda i: PL + (W - PL - PR) * i / (n - 1)
    Y = lambda v: PT + (H - PT - PB) * (hi - v) / (hi - lo)
    step = 10 ** max(len(str(int(hi - lo))) - 1, 0)
    step = step * (5 if (hi - lo) / step > 8 else 2 if (hi - lo) / step > 4 else 1)
    g = []
    t = (lo // step + 1) * step
    while t < hi:
        st = 'stroke="var(--muted)" stroke-width="1.5"' if t == 0 else 'stroke="var(--line)"'
        g.append(f'<line x1="{PL}" x2="{W - PR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" {st}/>'
                 f'<text x="{PL - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{t / 10000:+.0f}萬</text>')
        t += step
    lastm = None
    for i, (d, _, _) in enumerate(pts):
        if d.month != lastm:
            g.append(f'<text x="{X(i):.1f}" y="{H - 6}" text-anchor="middle">{d.month}月</text>')
            lastm = d.month
    line = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, (_, v, _) in enumerate(pts))
    d1, v1, _ = pts[-1]
    color = "var(--up)" if v1 >= 0 else "var(--down)"
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="每日淨總損益走勢">{"".join(g)}'
            f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2"/>'
            f'<circle cx="{X(n - 1):.1f}" cy="{Y(v1):.1f}" r="4" fill="{color}"/></svg>')


def render_html(per, divs, s, asof, hist, delta):
    """完整自足 HTML（含 doctype 與 charset，直接以瀏覽器開檔也不會亂碼）。"""
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    since = period()
    n_buy = sum(p["n_buy"] for p in per)
    value = s["cost"] + s["net"]
    total_fee = sum(p["fee"] for p in per)
    total_tax = sum(p["tax"] for p in per)
    sold_proceeds = round(sum(p["proceeds"] for p in per))
    wan = lambda x: f"{x / 10000:,.0f} 萬" if abs(x) >= 10000 else f"{x:,.0f} 元"   # 未滿 1 萬顯示實數，免得「賠了 0 萬」
    verb = "賺了" if s["net"] >= 0 else "賠了"

    rows = []
    for p in per:
        is_etf = p["code"].startswith("00")
        name = _esc(p["name"]) + ("" if is_etf else f'<span class="code">{_esc(p["code"])}</span>')
        sold_all = p["close"] is None
        if sold_all:
            name += '<span class="chip sold">已賣出</span>'
        elif p["sold_q"] > 1e-9:
            name += '<span class="chip sold">部分賣出</span>'
        if p["warn"]:
            name += '<span class="chip warn">待確認</span>'
        mv = round(p["proceeds"]) if sold_all else p["mv"]
        close, proceeds, fee, tax, div = p["close"], round(p["proceeds"]), p["fee"], p["tax"], p["div"]
        pct = p["net"] / p["cost"] * 100 if p["cost"] else 0
        rows.append(
            f'<tr><td>{name}</td><td>{p["q"]:,.0f}</td><td>{p["cost"]:,.0f}</td>'
            f'<td>{"—" if sold_all else f"{close:,.2f}"}</td>'
            f'<td>{mv:,.0f}{"" if sold_all or p["sold_q"] < 1e-9 else f" ＋ {proceeds:,}"}</td>'
            f'<td>{"已含" if sold_all else f"{fee:,}"}</td>'
            f'<td>{"已含" if sold_all else f"{tax:,}"}</td>'
            f'<td>{f"{div:,.0f}" if p["div"] else "—"}</td>'
            f'<td class="{_cls(p["net"])}">{_sg(p["net"])}</td>'
            f'<td class="{_cls(p["net"])}">{pct:+.1f}%</td></tr>')

    # 各檔貢獻：發散長條，同一比例尺
    nets = [p["net"] for p in per] + [0]
    mx, mn = max(nets), min(nets)
    span = (mx - mn) or 1
    zero = -mn / span * 100
    bars = []
    for p in sorted(per, key=lambda p: -p["net"]):
        g = p["net"]
        w = abs(g) / span * 100
        left = zero if g >= 0 else zero - w
        bars.append(f'<div class="bar-row"><span class="name">{_esc(p["name"])}</span>'
                    f'<div class="track" style="--zero:{zero:.2f}%"><i class="{"pos" if g >= 0 else "neg"}" '
                    f'style="left:{left:.2f}%;width:{max(w, .4):.2f}%"></i></div>'
                    f'<span class="amt {_cls(g)}">{_sg(g)}</span></div>')

    div_rows = "".join(
        f'<tr><td>{_esc(n)}<span class="code">{_esc(c)}</span></td><td>{d:%Y/%m/%d}</td><td>{cash:,.4g}</td>'
        f'<td>{q:,.0f}</td><td class="up">{amt:,}</td></tr>' for n, c, d, cash, q, amt in divs
    ) or '<tr><td colspan="5" style="text-align:left;color:var(--muted)">期間無符合條件的配息</td></tr>'

    trend = _trend_svg([h for h in hist if h[0] != asof] + [(asof, s["net"], s["pct"] / 100)])
    trend_sec = f"""<section aria-labelledby="trend-h">
  <div class="sec-head"><h2 id="trend-h">每日走勢</h2><p>淨總損益（含息、扣完賣出成本），每個交易日排程結算</p></div>
  <div class="trend">{trend}</div>
</section>""" if trend else ""
    warn_names = "、".join(p["name"] for p in per if p["warn"])
    warn_note = f"""<div class="note warn"><h3>待確認的補登列</h3>
      <p>{_esc(warn_names)} 有標記【待確認】的買進列，股數或價格為推估；確認後改「股票買賣紀錄」即可，下次結算自動更正。</p></div>""" if warn_names else ""
    delta_txt = f"，較前一交易日 <span class=\"{_cls(delta)}\">{_sg(delta)}</span>" if delta is not None else ""

    return f"""<!doctype html>
<html lang="zh-Hant-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{since}{" " if UNTIL else ""}加碼損益</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&family=Noto+Serif+TC:wght@600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>{REPORT_CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">股價試算 · 股票買賣紀錄 · 結算日 {asof:%Y/%m/%d} 收盤</div>
  <h1>{since}{" " if UNTIL else ""}加碼的 {wan(s["cost"])}，{verb} {wan(abs(s["net"]))}</h1>
  <p class="lede">{SINCE.year} 年 {SINCE.month} 月 {SINCE.day} 日（含）{f"至 {UNTIL.month} 月 {UNTIL.day} 日（含）" if UNTIL else "起"}的 {n_buy} 筆買進，計入已賣出部分的實收、期間配息，並把仍持有的部位以 {asof:%m/%d} 收盤價扣除賣出手續費與證交稅後結算{delta_txt}。</p>
</header>
<section class="hero" aria-label="總結">
  <div class="big">
    <span class="label">淨總損益（含息、扣完賣出成本）</span>
    <span class="value" style="color:var(--{"up" if s["net"] >= 0 else "down"})">{_sg(s["net"])}</span>
    <span class="pct" style="color:var(--{"up" if s["net"] >= 0 else "down"})">{s["pct"]:+.2f}%</span>
    <span class="label" style="margin-top:8px">投入 {s["cost"]:,} 元，目前價值約 {value:,} 元</span>
  </div>
  <div class="ledger">
    <div><span>買進總成本（含買進手續費）</span><span>{s["cost"]:,}</span></div>
    <div><span>仍持有部分 淨損益</span><span class="{_cls(s["unreal"])}">{_sg(s["unreal"])}</span></div>
    <div><span>已賣出部分 已實現損益</span><span class="{_cls(s["real"])}">{_sg(s["real"])}</span></div>
    <div><span>現金配息</span><span class="{_cls(s["div"])}">{_sg(s["div"])}</span></div>
    <div><span>淨總損益</span><span class="{_cls(s["net"])}">{_sg(s["net"])}</span></div>
  </div>
</section>
{trend_sec}
<section aria-labelledby="dist-h">
  <div class="sec-head"><h2 id="dist-h">各檔貢獻</h2><p>含配息、已扣賣出成本；紅色賺、綠色賠</p></div>
  <div class="bars">{"".join(bars)}</div>
</section>
<section aria-labelledby="detail-h">
  <div class="sec-head"><h2 id="detail-h">逐檔明細</h2><p>依投入金額排序；表格可左右捲動</p></div>
  <div class="table-box">
    <table>
      <thead><tr>
        <th>股票</th><th>{since} 買進股數</th><th>成本</th><th>{asof:%m/%d} 收盤</th><th>市值／賣出實收</th>
        <th>賣出手續費</th><th>證交稅</th><th>配息</th><th>淨損益</th><th>報酬率</th>
      </tr></thead>
      <tbody>{"".join(rows)}</tbody>
      <tfoot><tr>
        <td>合計</td><td></td><td>{s["cost"]:,}</td><td></td><td>{s["mv"]:,} ＋ {sold_proceeds:,}</td>
        <td>{total_fee:,}</td><td>{total_tax:,}</td><td>{s["div"]:,}</td>
        <td class="{_cls(s["net"])}">{_sg(s["net"])}</td><td class="{_cls(s["net"])}">{s["pct"]:+.2f}%</td>
      </tr></tfoot>
    </table>
  </div>
</section>
<section aria-labelledby="div-h">
  <div class="sec-head"><h2 id="div-h">期間配息</h2><p>買進日早於除息日、且除息日當天仍持有才計入</p></div>
  <div class="table-box">
    <table style="min-width:520px">
      <thead><tr><th>股票</th><th>除息日</th><th>每股配息</th><th>符合股數</th><th>配息金額</th></tr></thead>
      <tbody>{div_rows}</tbody>
    </table>
  </div>
</section>
<section aria-labelledby="notes-h">
  <h2 id="notes-h">計算依據與限制</h2>
  <div class="notes">
    <div class="note"><h3>成本與已實現</h3><ul>
      <li>成本取「股票買賣紀錄」J 欄，已含買進手續費。</li>
      <li>已賣出部位以先進先出（FIFO）配對，只計 {since} 買進、後來賣出的那一段；實收已扣手續費與證交稅。</li>
      <li>分割前股數依「股票分割」分頁換算成分割後。</li></ul></div>
    <div class="note"><h3>賣出成本假設</h3><ul>
      <li>仍持有部位假設結算日一次全部賣出。</li>
      <li>手續費 0.1425% × 6 折，未滿 1 元捨去，最低 20 元。</li>
      <li>證交稅：ETF（含槓桿 ETF）0.1%，個股 0.3%。</li></ul></div>
    <div class="note"><h3>資料來源</h3><ul>
      <li>收盤價：TWSE／TPEx 官方收盤（{asof:%Y/%m/%d}）。</li>
      <li>配息：上市取 TWSE 除權息計算結果表（TWT49U）、上櫃取櫃買中心。</li>
      <li>配息未扣匯費與二代健保補充保費。</li></ul></div>
    {warn_note}
  </div>
</section>
<footer>產出時間 {now:%Y/%m/%d %H:%M}（結算日 {asof:%Y/%m/%d} 收盤）。由 since_date_pnl.py --html 產生，資料來源：Google 試算表「股價試算」。</footer>
</div>
</body>
</html>
"""


def print_report(per, divs, s, asof):
    print(f"結算日 {asof}｜{SINCE} {'～ ' + str(UNTIL) if UNTIL else '起'} {s['n']} 檔")
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
    ap.add_argument("--html", metavar="PATH", help="另產出完整 HTML 報告（例：investment_since_0703_report.html）")
    ap.add_argument("--since", help="起算日（含）YYYY-MM-DD，預設 2026-07-03；改了就是另一波加碼，須搭配 --dry-run")
    ap.add_argument("--until", help="買進結束日（含）YYYY-MM-DD：只算起算日～此日之間買進的批次；"
                                    "結算日（--date）可以更晚。分頁走勢是不設上限的那組，故須搭配 --dry-run")
    a = ap.parse_args()
    asof = dt.date.fromisoformat(a.date.replace("/", "-")) if a.date else dt.date.today()
    global SINCE, UNTIL
    if a.since:
        SINCE = dt.date.fromisoformat(a.since.replace("/", "-"))
    if a.until:
        UNTIL = dt.date.fromisoformat(a.until.replace("/", "-"))
    if (a.since or a.until) and not a.dry_run:
        sys.exit("✗ --since／--until 只能搭配 --dry-run：「加碼損益_0703」分頁與走勢是 7/3 起、不設結束日的那組，混寫會讓走勢失真")
    if UNTIL and UNTIL < SINCE:
        sys.exit(f"✗ 結束日 {UNTIL} 早於起算日 {SINCE}")

    svc = get_service()
    # 只計入結算日（含）以前的交易：補算過去某天時，不能把之後才發生的買賣算進去
    tx = [t for t in read_trades(svc) if t["d"] <= asof]
    tracked = fifo(tx, read_splits(svc))
    per, divs = compute(tracked, asof)
    s = summary(per)
    print_report(per, divs, s, asof)
    if a.html:
        # 分頁走勢是不設結束日的那組；設了 --until 就不引用（不畫走勢、不算較前一交易日）
        hist = [] if (UNTIL or a.since) else [h for h in read_history(svc) if h[0] and h[0] <= asof]
        prev = next((n for d, n, _ in sorted(hist, reverse=True) if d < asof), None)
        with open(a.html, "w", encoding="utf-8") as f:
            f.write(render_html(per, divs, s, asof, hist, s["net"] - prev if prev is not None else None))
        print(f"✓ 已產出 {a.html}")
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
