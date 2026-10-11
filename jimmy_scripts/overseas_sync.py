#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""overseas_sync.py — 複委託（海外）帳戶追蹤

觸發語：「複委託」「更新複委託」

【為什麼需要這支】
主帳戶（元大）與國泰帳戶都已自動化，但**複委託完全不在系統裡**。
2026-10-12 查信貸資金流向時發現：信貸 300 萬中約 20.9 萬沒被主帳戶吸收，
推測是進了複委託——也就是說「總資產」與「正二曝險比例」目前都是低估的。
這支先把複委託獨立追蹤起來（2026-10-12 Jimmy 決定：**先獨立、不動現有核心邏輯**），
確認報價與匯率穩定後，再決定要不要併進 04_ETF分析 的曝險計算。

【資料流】
  「股價試算／複委託持股」(人工維護) → yfinance 抓收盤＋匯率 → 「股價試算／複委託」(本支重寫)

【持股分頁格式】複委託持股（第 1 列為表頭，之後逐檔一列）
  代號 | 名稱 | 股數 | 買進均價 | 幣別 | 買進日 | 備註
  - 代號：yfinance 代號（美股直接寫 VOO／QQQM；港股 0700.HK；台股 0050.TW）
  - 買進均價：**以該檔幣別計價**（美股就填 USD 單價），含費均價較準
  - 幣別：USD／HKD／TWD；留空預設 USD
  - 買進日：可留空（只影響「持有天數」欄，不影響損益）

【⚠ 時差】美股收盤是台灣時間凌晨，台北 14:30 排程抓到的是**前一個美股交易日**的收盤。
  這不是錯誤，產出會標明報價日期，不要拿它跟台股同一天的數字做「當日」比較。

【⚠ 匯率】用 yfinance 的 TWD=X（USD→TWD 即期），與券商實際換匯價會有差異，
  只作估算。損益同時以**原幣**與**台幣**兩種口徑呈現：原幣損益是真實投資績效，
  台幣損益含匯率變動，兩者不可混談。

用法：
  python3 overseas_sync.py                 # 終端列印（不寫試算表）
  python3 overseas_sync.py --sheet         # 另外寫入「複委託」分頁
  python3 overseas_sync.py --sheet --yes   # 不詢問直接寫
  python3 overseas_sync.py --init          # 建立「複委託持股」空白範本分頁
"""
import argparse
import os
import sys
import warnings
import datetime as dt

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import update_wealth_os as u                      # noqa: E402
from googleapiclient.discovery import build       # noqa: E402

HOLD_SHEET = "複委託持股"      # 人工維護
OUT_SHEET = "複委託"           # 本支重寫
HEADERS = ["代號", "名稱", "股數", "買進均價", "幣別", "買進日", "備註"]


def fx_rate(cur):
    """外幣 → TWD。TWD 回 1.0；抓不到回 None（呼叫端留白，不要用舊值硬湊）。"""
    if cur.upper() == "TWD":
        return 1.0
    import yfinance as yf
    sym = "TWD=X" if cur.upper() == "USD" else f"{cur.upper()}TWD=X"
    try:
        h = yf.Ticker(sym).history(period="7d")
        return float(h["Close"].iloc[-1]) if len(h) else None
    except Exception:
        return None


def quote(code):
    """回傳 (收盤價, 報價日)；抓不到回 (None, None)。"""
    import yfinance as yf
    try:
        h = yf.Ticker(code).history(period="7d")
        if not len(h):
            return None, None
        return float(h["Close"].iloc[-1]), h.index[-1].date()
    except Exception:
        return None, None


def read_holdings(svc):
    try:
        rows = svc.spreadsheets().values().get(
            spreadsheetId=u.SPREADSHEET_ID,
            range=f"{HOLD_SHEET}!A1:G100").execute().get("values", [])
    except Exception:
        return None
    out = []
    for r in rows[1:]:
        r = list(r) + [""] * (7 - len(r))
        code = str(r[0]).strip()
        if not code:
            continue
        q = u.num(str(r[2])) if str(r[2]).strip() else None
        px = u.num(str(r[3])) if str(r[3]).strip() else None
        if not q:
            continue
        out.append({"code": code, "name": str(r[1]).strip() or code, "qty": q,
                    "buy": px, "cur": (str(r[4]).strip() or "USD").upper(),
                    "date": str(r[5]).strip(), "note": str(r[6]).strip()})
    return out


def main():
    ap = argparse.ArgumentParser(description="複委託帳戶追蹤")
    ap.add_argument("--sheet", action="store_true", help="寫入「複委託」分頁")
    ap.add_argument("--yes", action="store_true", help="不詢問直接寫入")
    ap.add_argument("--init", action="store_true", help="建立「複委託持股」範本分頁")
    ap.add_argument("--skip-if-empty", action="store_true",
                    help="沒有持股資料時正常結束（exit 0），排程專用")
    args = ap.parse_args()

    creds = u.get_creds()
    svc = build("sheets", "v4", credentials=creds)

    if args.init:
        meta = svc.spreadsheets().get(spreadsheetId=u.SPREADSHEET_ID).execute()
        if any(s["properties"]["title"] == HOLD_SHEET for s in meta["sheets"]):
            sys.exit(f"分頁「{HOLD_SHEET}」已存在，不重建（避免蓋掉既有持股）。")
        svc.spreadsheets().batchUpdate(spreadsheetId=u.SPREADSHEET_ID, body={"requests": [
            {"addSheet": {"properties": {"title": HOLD_SHEET}}}]}).execute()
        svc.spreadsheets().values().update(
            spreadsheetId=u.SPREADSHEET_ID, range=f"{HOLD_SHEET}!A1",
            valueInputOption="USER_ENTERED", body={"values": [
                HEADERS,
                ["VOO", "Vanguard S&P500", 10, 620, "USD", "2026/07/15", "範例，請改成實際持股"],
            ]}).execute()
        print(f"✅ 已建立「{HOLD_SHEET}」分頁；請填入實際持股後再跑 overseas_sync.py")
        return

    hold = read_holdings(svc)
    if hold is None:
        msg = f"找不到分頁「{HOLD_SHEET}」，請先執行：python3 overseas_sync.py --init"
        if args.skip_if_empty:
            print(f"— {msg}（跳過，不算失敗）")
            return
        sys.exit(f"✗ {msg}")
    if not hold:
        msg = f"分頁「{HOLD_SHEET}」沒有任何持股資料"
        if args.skip_if_empty:
            print(f"— {msg}（跳過，不算失敗）")
            return
        sys.exit(f"✗ {msg}")

    rates = {}
    for h in hold:
        if h["cur"] not in rates:
            rates[h["cur"]] = fx_rate(h["cur"])

    rows, warn = [], []
    for h in hold:
        px, qd = quote(h["code"])
        fx = rates.get(h["cur"])
        if px is None:
            warn.append(f"{h['code']} 取不到報價")
        if fx is None:
            warn.append(f"{h['cur']} 取不到匯率")
        mv = (h["qty"] * px) if px else None                     # 原幣市值
        cost = (h["qty"] * h["buy"]) if h["buy"] else None       # 原幣成本
        pnl = (mv - cost) if (mv is not None and cost is not None) else None
        ret = (pnl / cost) if (pnl is not None and cost) else None
        mv_twd = (mv * fx) if (mv is not None and fx) else None
        pnl_twd = (pnl * fx) if (pnl is not None and fx) else None
        rows.append({**h, "px": px, "qdate": qd, "fx": fx, "mv": mv, "cost": cost,
                     "pnl": pnl, "ret": ret, "mv_twd": mv_twd, "pnl_twd": pnl_twd})

    f = lambda x, n=0: (f"{x:,.{n}f}" if isinstance(x, (int, float)) else "—")
    print(f"\n{'='*86}\n  複委託帳戶（報價日見各列；匯率 "
          + "、".join(f"{k} {v:.3f}" for k, v in rates.items() if v) + f"）\n{'='*86}")
    print(f"  {'代號':<9}{'股數':>8}{'均價':>10}{'現價':>10}{'原幣損益':>12}{'報酬':>8}"
          f"{'台幣市值':>13}{'台幣損益':>12}  報價日")
    for r in rows:
        print(f"  {r['code']:<9}{f(r['qty']):>8}{f(r['buy'],2):>10}{f(r['px'],2):>10}"
              f"{f(r['pnl'],0):>12}{(f'{r[chr(114)+chr(101)+chr(116)]:+.1%}' if r['ret'] is not None else '—'):>8}"
              f"{f(r['mv_twd']):>13}{f(r['pnl_twd']):>12}  {r['qdate'] or '—'}")
    tot_mv = sum(r["mv_twd"] for r in rows if r["mv_twd"] is not None)
    tot_cost = sum((r["cost"] * r["fx"]) for r in rows
                   if r["cost"] is not None and r["fx"])
    tot_pnl = sum(r["pnl_twd"] for r in rows if r["pnl_twd"] is not None)
    print(f"\n  台幣合計：市值 {tot_mv:,.0f}　成本 {tot_cost:,.0f}　損益 {tot_pnl:+,.0f}"
          f"（{tot_pnl/tot_cost:+.2%}）" if tot_cost else "")
    if warn:
        print("\n  ⚠ " + "；".join(sorted(set(warn))))
    print("\n  ⚠ 台幣損益含匯率變動，與原幣損益不可混談；匯率為 yfinance 即期，"
          "與券商實際換匯價有差異，僅供估算。")

    if not args.sheet:
        print("\n（未寫入試算表；要寫請加 --sheet）")
        return

    out = [[f"複委託帳戶", f"更新 {dt.date.today()}"], [],
           ["代號", "名稱", "股數", "買進均價", "幣別", "現價", "報價日",
            "原幣成本", "原幣市值", "原幣損益", "報酬率", "匯率", "台幣市值", "台幣損益"]]
    for r in rows:
        out.append([r["code"], r["name"], r["qty"], r["buy"], r["cur"],
                    r["px"], str(r["qdate"] or ""), r["cost"], r["mv"], r["pnl"],
                    r["ret"], r["fx"],
                    round(r["mv_twd"]) if r["mv_twd"] is not None else "",
                    round(r["pnl_twd"]) if r["pnl_twd"] is not None else ""])
    out += [[], ["台幣合計", "", "", "", "", "", "", round(tot_cost), "", "", "", "",
                 round(tot_mv), round(tot_pnl)], [],
            ["口徑說明", "原幣損益＝真實投資績效；台幣損益另含匯率變動，兩者不可混談"],
            ["", "匯率為 yfinance 即期（TWD=X），與券商實際換匯價有差異，僅供估算"],
            ["", "美股收盤為台灣凌晨，台北 14:30 排程抓到的是前一個美股交易日收盤"],
            ["", "⚠ 本帳戶目前**未**併入 16_資產歷史／04_ETF分析，總資產與曝險比例不含這裡"]]
    if warn:
        out.append(["⚠ 警告", "；".join(sorted(set(warn)))])

    meta = svc.spreadsheets().get(spreadsheetId=u.SPREADSHEET_ID).execute()
    exists = any(s["properties"]["title"] == OUT_SHEET for s in meta["sheets"])
    if not args.yes:
        print(f"\n即將{'覆寫' if exists else '新建'}分頁「{OUT_SHEET}」（{len(out)} 列）")
        if input("確認寫入？(yes/no) ").strip().lower() not in ("y", "yes"):
            print("已取消。")
            return
    if not exists:
        svc.spreadsheets().batchUpdate(spreadsheetId=u.SPREADSHEET_ID, body={"requests": [
            {"addSheet": {"properties": {"title": OUT_SHEET}}}]}).execute()
    else:
        svc.spreadsheets().values().clear(
            spreadsheetId=u.SPREADSHEET_ID, range=f"{OUT_SHEET}!A1:Z200").execute()
    # 同 loan_funding:USER_ENTERED 會把 = + - @ 開頭的字串當公式,寫入前跳脫
    def esc_cell(v):
        return f"'{v}" if isinstance(v, str) and v[:1] in "=+-@" else v
    out = [[esc_cell(c) for c in r] for r in out]
    svc.spreadsheets().values().update(
        spreadsheetId=u.SPREADSHEET_ID, range=f"{OUT_SHEET}!A1",
        valueInputOption="USER_ENTERED", body={"values": out}).execute()
    print(f"✅ 已寫入「{OUT_SHEET}」分頁（{len(out)} 列）")


if __name__ == "__main__":
    main()
