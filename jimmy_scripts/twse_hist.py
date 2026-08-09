#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""台股歷史資料共用模組:TWSE(上市)為主、TPEx(上櫃)補;完全不使用 Yahoo。

供 update_close_price / trade_entry_server / fetch_612_close 共用。對外介面:
  norm_code(a)              A欄值/代號 -> 純台股代號;非台股(美股等)回 None
  month_closes(code, y, m)  某月每日收盤 {date: close}(上市優先,失敗試上櫃)
  daily_closes(code, s, e)  [s, e] 期間每日收盤 {date: close}
  close_on(a, date, gap=4)  指定日收盤;當日無交易則取 ±gap 最近交易日 -> (price, note)
  dividends(code, s, e)     期間現金股利 [(date, amount)] 升冪(除權息結果表「息」)

資料來源(皆官方、免金鑰):
  上市日成交  https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY
  上櫃日成交  https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock
  除權除息    https://www.twse.com.tw/rwd/zh/exRight/TWT49U
注意:TWSE/TPEx 不涵蓋美股,美股代號一律回 None / 空。
"""
import datetime
import json
import time
import urllib.request

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
_TIMEOUT = 20
_THROTTLE = 0.4          # 每次實際連線後稍歇,對官方站台客氣、避免限速


# ========================= 共用工具 =========================
def _get_json(url):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.load(resp)


def _f(x):
    """'2,355.00' -> 2355.0;'-'、''、'--'、'N/A'、None 一律回 None。"""
    try:
        s = str(x).replace(",", "").strip()
        if s in ("", "-", "--", "X", "N/A"):
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def _roc_date(s):
    """民國日期 -> date。支援 '115/06/01' 與 '115年06月01日'。失敗回 None。"""
    s = str(s).strip().replace("年", "/").replace("月", "/").replace("日", "")
    parts = [p for p in s.split("/") if p != ""]
    if len(parts) != 3:
        return None
    try:
        y, m, d = (int(p) for p in parts)
        return datetime.date(y + 1911, m, d)
    except ValueError:
        return None


def norm_code(a):
    """A欄值/代號 -> 純台股代號(去前導 ' 與 .TW/.TWO);非台股(美股等)回 None。"""
    v = str(a).strip().lstrip("'").upper()
    for suf in (".TWO", ".TW"):
        if v.endswith(suf):
            v = v[:-len(suf)]
            break
    # 台股代號:首字為數字、整體為英數(涵蓋 00631L、00981A 之類)
    if v and v[0].isdigit() and v.isalnum():
        return v
    return None


# ========================= 歷史日收盤價 =========================
_close_cache = {}        # (code, y, m) -> {date: close}(空 dict = 查過但無資料)


def _parse_stock_day(d):
    """STOCK_DAY 回應 -> {date: close};stat 非 OK 或無資料回 None。"""
    if d.get("stat") != "OK":
        return None
    out = {}
    for row in d.get("data", []):
        dt, px = _roc_date(row[0]), _f(row[6])
        if dt and px:                       # px 為 0 視同無效收盤
            out[dt] = px
    return out or None


def _twse_month(code, y, m):
    """上市某月每日收盤;非上市/查無回 None。

    主端點 /rwd/zh/afterTrading/STOCK_DAY 會間歇對個別代號回假錯誤
    (stat='查詢日期小於99年1月4日…',如 2308 整月空),故當主端點失敗/回空時,
    自動 fallback 到 /exchangeReport/STOCK_DAY(同參數同欄位、不會回該假錯誤)。
    """
    date = f"{y}{m:02d}01"
    urls = (
        f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
        f"?date={date}&stockNo={code}&response=json",
        f"https://www.twse.com.tw/exchangeReport/STOCK_DAY"
        f"?date={date}&stockNo={code}&response=json",
    )
    for url in urls:
        try:
            out = _parse_stock_day(_get_json(url))
        except Exception:
            out = None
        if out:
            return out
    return None


def _tpex_month(code, y, m):
    """上櫃某月每日收盤;查無回 None。"""
    url = (f"https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"
           f"?code={code}&date={y}/{m:02d}/01&response=json")
    try:
        d = _get_json(url)
    except Exception:
        return None
    tables = d.get("tables") or []
    rows = tables[0].get("data", []) if tables else []
    out = {}
    for row in rows:
        dt, px = _roc_date(row[0]), _f(row[6])
        if dt and px:
            out[dt] = px
    return out


def month_closes(code, y, m):
    """某月每日收盤 {date: close};上市優先,失敗試上櫃。結果(含空)入快取。"""
    key = (code, y, m)
    if key in _close_cache:
        return _close_cache[key]
    data = _twse_month(code, y, m)
    if not data:                            # 上市查無 -> 試上櫃
        data = _tpex_month(code, y, m)
    _close_cache[key] = data = data or {}
    time.sleep(_THROTTLE)
    return data


def _months_between(s, e):
    y, m = s.year, s.month
    while (y, m) <= (e.year, e.month):
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def daily_closes(code, start, end):
    """[start, end] 期間每日收盤 {date: close}。"""
    out = {}
    for y, m in _months_between(start, end):
        out.update(month_closes(code, y, m))
    return {d: v for d, v in out.items() if start <= d <= end}


def _adjacent(y, m, delta):
    m += delta
    if m < 1:
        return y - 1, 12
    if m > 12:
        return y + 1, 1
    return y, m


def close_on(a, date, gap=4):
    """指定日收盤;當日非交易日則取 ±gap 內最近交易日。回 (price, note)。
    price 為 None 時 note 帶失敗原因;成功時 note 為 'TWD'。"""
    code = norm_code(a)
    if not code:
        return None, "非台股(TWSE/TPEx 無資料)"
    if isinstance(date, str):
        date = datetime.datetime.strptime(date.replace("/", "-"), "%Y-%m-%d").date()

    closes = dict(month_closes(code, date.year, date.month))
    if date in closes:
        return round(closes[date], 2), "TWD"
    # 當日缺值/非交易日 -> 補鄰月後找 ±gap 最近交易日
    for delta in (-1, 1):
        ay, am = _adjacent(date.year, date.month, delta)
        closes.update(month_closes(code, ay, am))
    if not closes:
        return None, f"查無 {date} 報價"
    near = [d for d in closes if abs((d - date).days) <= gap]
    if near:
        best = min(near, key=lambda d: abs((d - date).days))
        return round(closes[best], 2), "TWD"
    last = max(closes)
    return None, f"無 {date} 報價,最近 {last}={round(closes[last], 2)}"


# ========================= 除權息(現金股利) =========================
_exright_cache = {}      # (startYMD, endYMD) -> [(date, code, kind, value)]


def _fetch_exright(start, end):
    """除權除息計算結果表(上市,全市場)一段日期 -> [(date, code, 權/息, 權值+息值)]。"""
    key = (start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    if key in _exright_cache:
        return _exright_cache[key]
    url = (f"https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
           f"?startDate={key[0]}&endDate={key[1]}&response=json")
    rows = []
    try:
        d = _get_json(url)
        if d.get("stat") == "OK":
            for row in d.get("data", []):
                dt, val = _roc_date(row[0]), _f(row[5])    # 資料日期 / 權值+息值
                if dt and val is not None:
                    rows.append((dt, str(row[1]).strip(), str(row[6]).strip(), val))
        time.sleep(_THROTTLE)
    except Exception:
        pass
    _exright_cache[key] = rows
    return rows


def dividends(code, start, end):
    """[start, end] 期間現金股利 [(date, amount)] 升冪。
    取 TWT49U 中該代號『權/息含息』者的權值+息值。為上市資料;
    上櫃個股配息此 API 未涵蓋(回空,殖利率欄請人工維護)。"""
    code = str(code).strip()
    out = set()
    seg = start
    while seg <= end:                       # 跨年分段查,避免單次回傳過大
        seg_end = min(datetime.date(seg.year, 12, 31), end)
        for dt, c, kind, val in _fetch_exright(seg, seg_end):
            if c == code and "息" in kind:
                out.add((dt, val))
        seg = datetime.date(seg.year + 1, 1, 1)
    return sorted(out)


# ========================= 自我測試 =========================
if __name__ == "__main__":
    print("close_on 2330 2026-06-12 ->", close_on("2330", "2026-06-12"))
    print("close_on 6488 2026-06-12 ->", close_on("6488", "2026-06-12"))   # 上櫃
    print("close_on TSM  2026-06-12 ->", close_on("TSM", "2026-06-12"))    # 美股 -> None
    today = datetime.date.today()
    print("dividends 0056 近2年 ->",
          dividends("0056", today - datetime.timedelta(days=730), today)[-4:])
