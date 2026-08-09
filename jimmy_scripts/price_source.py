#!/usr/bin/env python3
"""股價取得共用模組：TWSE 官方為主、Yahoo 備援。

供 stock_notify / stock_notify_gmail / stock_alert / stock_alert_v2 共用。

取價策略：
  1. 主：證交所 TWSE 即時 API（mis.twse.com.tw），官方、支援一次批次查多檔，
     最不易被限速。
  2. 備援：Yahoo Finance（yfinance），僅補抓 TWSE 連成交價都取不到的標的。

對外主要介面：
  get_quotes(symbols) -> {symbol: {"price", "prev_close", "open"}}
  缺漏的標的不會出現在回傳 dict；個別欄位可能為 None，呼叫端需自行判斷。
"""

import requests
import yfinance as yf

# mis.twse 需帶瀏覽器 UA + Referer，否則易被擋
_TWSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://mis.twse.com.tw/stock/index.jsp",
}


def _to_float(v):
    """把 TWSE 回傳的字串轉 float；'-'、空字串、None 一律回 None。"""
    try:
        if v is None:
            return None
        v = str(v).strip()
        if v in ("", "-"):
            return None
        return float(v)
    except (ValueError, TypeError):
        return None


def _yahoo_to_twse_ch(symbol: str) -> str:
    """Yahoo 代號 -> TWSE ex_ch。2330.TW -> tse_2330.tw；6488.TWO -> otc_6488.tw。"""
    s = symbol.upper().strip()
    if s.endswith(".TWO"):
        return f"otc_{s[:-4]}.tw"
    if s.endswith(".TW"):
        return f"tse_{s[:-3]}.tw"
    # 無後綴的純代號預設視為上市
    return f"tse_{s}.tw"


def fetch_twse_batch(symbols: list) -> dict:
    """主來源：TWSE 即時 API 一次批次查詢。
    回傳 {symbol: {price, prev_close, open}}，取不到成交價的 symbol 不會出現。"""
    result = {}
    if not symbols:
        return result

    ch_map = {_yahoo_to_twse_ch(s): s for s in symbols}  # ex_ch -> 原始 symbol
    url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
    params = {"ex_ch": "|".join(ch_map.keys()), "json": "1", "delay": "0"}
    try:
        res = requests.get(url, params=params, headers=_TWSE_HEADERS, timeout=10)
        res.raise_for_status()
        for item in res.json().get("msgArray", []):
            ch = f"{item.get('ex')}_{item.get('c')}.tw"
            sym = ch_map.get(ch)
            if not sym:
                continue
            # z=最新成交價；盤後/無成交時為 '-'，退用最佳買價 b 第一檔
            price = _to_float(item.get("z"))
            if price is None:
                price = _to_float((item.get("b") or "").split("_")[0])
            if price is None:
                continue  # 連成交價都沒有 → 留給 Yahoo 備援
            result[sym] = {
                "price": price,
                "prev_close": _to_float(item.get("y")),  # y=昨收
                "open": _to_float(item.get("o")),         # o=開盤
            }
    except Exception as e:
        print(f"[TWSE ERROR] {e}")
    return result


def _yahoo_raw(symbol: str):
    """備援來源：Yahoo Finance（yfinance）。回傳 {price, prev_close, open} 或 None。"""
    try:
        info = yf.Ticker(symbol).fast_info
        return {
            "price": info.last_price,
            "prev_close": info.previous_close,
            "open": info.open,
        }
    except Exception as e:
        print(f"[Yahoo ERROR] {symbol}: {e}")
        return None


def get_quotes(symbols: list) -> dict:
    """整合：TWSE 為主，缺漏者以 Yahoo 備援。
    回傳 {symbol: {price, prev_close, open}}。"""
    result = fetch_twse_batch(symbols)
    missing = [s for s in symbols if s not in result]
    if missing:
        print(f"[備援] TWSE 未取得 {len(missing)} 檔，改用 Yahoo：{missing}")
        for sym in missing:
            data = _yahoo_raw(sym)
            if data:
                result[sym] = data
    return result
