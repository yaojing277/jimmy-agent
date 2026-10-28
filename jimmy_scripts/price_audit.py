#!/usr/bin/env python3
"""用官方當日最高／最低價，檢驗「股票買賣紀錄」的成交價是否可能成交。

為什麼需要這支(2026-10-28 建立):
  成交價打錯是**現有檢查全部抓不到**的一類錯誤——
    * 手續費公式驗不出來(價格錯,算出的手續費照樣「自洽」)
    * 「是否為合法跳動價位」也驗不出來(00878 列315 把 22.19 打成 21.19,
      21.19 同樣是合法跳動價位)
    * `rebuild_cost_basis.py` 的股數護欄更抓不到(股數沒錯)
  但它一筆就可能差幾百到上千元,比所有漏算的手續費加起來還多。
  **唯一能抓的辦法是跟當天的真實行情比對。**

  首次執行就抓到兩筆:
    00878 列315  2025/02/04  21.19 → 22.19(金額少 500)
    00919 列360  2024/07/22  23.25,當日區間 24.20~24.97
                 ——23.25 正是同一天 00878 的成交價(列357),複製貼上錯誤

用法:
  python3 price_audit.py                      # 全表
  python3 price_audit.py --code 00919         # 只驗單一標的
  python3 price_audit.py --since 2026-01-01   # 只驗這天之後
  python3 price_audit.py --tol 0.01           # 放寬容許偏離(預設 0.3%)

建議時機:**記帳流程的第 0 步**——寫完買賣紀錄、還沒併入快照之前跑一次。
  (完整流程:寫買賣紀錄 → **price_audit** → 併入快照股數 → rebuild_cost_basis → 更新所有分頁)
"""
import argparse
import datetime
import json
import sys
import urllib.request

from sheets_writer import get_service, SPREADSHEET_ID
from update_stock_price import ALIAS
import twse_hist as T

EPOCH = datetime.date(1899, 12, 30)
UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")}
VOID_SUFFIX = "_刪"

# ⚠ 容許偏離:盤中零股與整股是**不同的價格區間**,零股偶爾會小幅超出整股的高低點。
#   實測超出幅度都在 0.2% 以內,而真正的打錯都在 3% 以上——0.3% 把兩者乾淨地分開。
DEFAULT_TOL = 0.003

# 價格查詢用的代號。⚠ **與 rebuild_cost_basis 的 ALIAS 刻意分開**:
#   那邊是「把同一檔的不同寫法併成一堆算成本」(合併後新光金併入 2887 台新新光金),
#   這裡要的是「交易當時那檔股票的行情」——2024/08 的新光金是 **2888**,不是 2887。
#   用錯會讓 5 筆正常紀錄被誤報成偏離 33%(2026-10-28 第一版就是這樣)。
#   其餘幾筆是「買賣紀錄只寫中文名、而 ALIAS 沒收」——不是查不到行情,是代號根本沒送對。
#   以下每一個都用「紀錄價是否落在該代號當日區間」反向確認過(全數吻合)。
PRICE_CODE = {
    "新光金": "2888",     # ⚠ 合併前的新光金;合併後才併入 2887(見上方說明)
    "華碩": "2357", "上銀": "2049", "統一超": "2912", "寶成": "9904", "亞航": "2630",
    "940": "00940",      # 純數字代號被 Sheets 吃掉前導零的殘留
    "華邦金": "2344",     # ⚠ 買賣紀錄的名稱寫錯了,實際是**華邦電**(2344);
                          #   10 筆價格全部落在 2344 的當日區間,2880/2881/2891 則全不符
}
EXTRA_ALIAS = {"台積電": "2330", "穎崴": "6515", "開發金": "2883", "新光金": "2887"}

# 已知例外:不是打錯,不必每次都跳出來
KNOWN = {
    213: "員工認股贖回,J 欄是認購價的名目金額,與市價無關(見 CLAUDE.md)",
    467: "00878 期初整併列,價格是由倒推金額反除得到的均價,不是某一天的成交價",
}

_cache = {}


def _hl_month(code, y, m):
    """{日期: (最低, 最高)};上市走 TWSE、上櫃走 TPEx,都查不到回 {}。"""
    key = (code, y, m)
    if key in _cache:
        return _cache[key]
    out = {}
    # TWSE:主端點會間歇回假錯誤,故備用 /exchangeReport/(策略同 twse_hist._twse_month)
    for path in ("rwd/zh/afterTrading", "exchangeReport"):
        url = (f"https://www.twse.com.tw/{path}/STOCK_DAY"
               f"?date={y}{m:02d}01&stockNo={code}&response=json")
        try:
            d = json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=20))
        except Exception:
            continue
        if d.get("stat") != "OK":
            continue
        for row in d.get("data", []):
            dt, hi, lo = T._roc_date(row[0]), T._f(row[4]), T._f(row[5])
            if dt and lo and hi:
                out[dt] = (lo, hi)
        if out:
            break
    if not out:                                   # 上櫃:欄位順序與 TWSE 相同
        try:
            d = T._get_json("https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"
                            f"?code={code}&date={y}/{m:02d}/01&response=json")
            for row in ((d.get("tables") or [{}])[0].get("data") or []):
                dt, hi, lo = T._roc_date(row[0]), T._f(row[4]), T._f(row[5])
                if dt and lo and hi:
                    out[dt] = (lo, hi)
        except Exception:
            pass
    _cache[key] = out
    return out


def read_trades(svc, since=None, only=None):
    """回 [(列號, 原始名稱, 查價代號, 日期, 價格, 股數, '買'/'賣')]。"""
    rows = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range="股票買賣紀錄!A2:Q1000",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    A = {**ALIAS, **EXTRA_ALIAS}
    out = []
    for i, r in enumerate(rows, start=2):
        r = list(r) + [""] * 17
        name = str(r[0]).strip()
        if not name or VOID_SUFFIX in name:
            continue
        code = PRICE_CODE.get(name) or A.get(name, name)
        if only and only not in (name, code):
            continue
        # 買進 F/G/H、賣出 K/L/M
        for dcol, pcol, qcol, kind in ((5, 6, 7, "買"), (10, 11, 12, "賣")):
            if r[qcol] in ("", None) or not isinstance(r[dcol], (int, float)):
                continue
            px = float(r[pcol] or 0)
            if not px:
                continue
            d = EPOCH + datetime.timedelta(days=int(float(r[dcol])))
            if since and d < since:
                continue
            out.append((i, name, code, d, px, float(r[qcol]), kind))
    return out


def main():
    ap = argparse.ArgumentParser(description="以官方行情檢驗買賣紀錄的成交價")
    ap.add_argument("--code", help="只驗這個代號或名稱")
    ap.add_argument("--since", help="只驗這個日期(含)之後,格式 YYYY-MM-DD")
    ap.add_argument("--tol", type=float, default=DEFAULT_TOL,
                    help=f"容許偏離比例(預設 {DEFAULT_TOL:.3f};零股區間與整股略有差異)")
    ap.add_argument("--quiet", action="store_true", help="只印問題,不印進度與無法驗證清單")
    args = ap.parse_args()

    since = datetime.date.fromisoformat(args.since) if args.since else None
    svc = get_service()
    rows = read_trades(svc, since, args.code)
    months = {(c, d.year, d.month) for _, _, c, d, _, _, _ in rows}
    if not args.quiet:
        print(f"待驗 {len(rows)} 筆,涉及 {len(months)} 個(代號,月份)組合", flush=True)

    bad, known, nodata = [], [], []
    for n, (i, name, code, d, px, qty, kind) in enumerate(rows, 1):
        hl = _hl_month(code, d.year, d.month).get(d)
        if not hl:
            nodata.append((i, name, d, px))
        else:
            lo, hi = hl
            if px < lo * (1 - args.tol) or px > hi * (1 + args.tol):
                off = (px - hi) / hi if px > hi else (px - lo) / lo
                (known if i in KNOWN else bad).append((i, name, kind, d, px, lo, hi, off, qty))
        if not args.quiet and n % 100 == 0:
            print(f"  …{n}/{len(rows)}", flush=True)

    def table(title, lst):
        print(f"\n{'='*78}\n{title}（{len(lst)} 筆）\n{'='*78}")
        if not lst:
            print("  （無）")
            return
        print(f"{'列':>5}  {'標的':<8}{'':3}{'日期':<12}{'紀錄價':>10}"
              f"{'當日低':>10}{'當日高':>10}{'偏離':>8}  股數")
        for i, name, kind, d, px, lo, hi, off, qty in sorted(lst, key=lambda t: -abs(t[7])):
            odd = "" if qty % 1000 == 0 else "（零股）"
            print(f"{i:>5}  {name:<8}{kind:<3}{str(d):<12}{px:>10,g}"
                  f"{lo:>10,g}{hi:>10,g}{off:>7.1%}  {qty:>7,.0f}{odd}")
            if i in KNOWN:
                print(f"       ↳ {KNOWN[i]}")

    table("⚠ 成交價不在當日最高／最低之間", bad)
    if known:
        table("已知例外（不必處理）", known)

    if nodata and not args.quiet:
        print(f"\n{'='*78}\n無法驗證（{len(nodata)} 筆）\n{'='*78}")
        print("  ⚠ 多半是**代號沒送對**（買賣紀錄只寫中文名、而對照表沒收），不是行情查不到。")
        print("     請把名稱補進 PRICE_CODE——用「紀錄價是否落在該代號當日區間」就能確認猜得對不對。")
        print("     真正查不到的只有已下市代號（如合併前的 2888 新光金）。")
        for i, name, d, px in sorted(nodata, key=lambda t: t[2], reverse=True)[:15]:
            print(f"   列{i:>3}  {name:<8}{d}  @{px:g}")
        if len(nodata) > 15:
            print(f"   …另 {len(nodata) - 15} 筆")

    ok = len(rows) - len(bad) - len(known) - len(nodata)
    print(f"\n小結：驗過 {ok:,} 筆正常、{len(bad)} 筆可疑、"
          f"{len(known)} 筆已知例外、{len(nodata)} 筆無法驗證")
    if bad:
        print("⚠ 可疑的那幾筆請翻對帳單或券商 App 的成交明細確認；"
              "改完價格記得連 J 欄金額一起更正。")


if __name__ == "__main__":
    main()
