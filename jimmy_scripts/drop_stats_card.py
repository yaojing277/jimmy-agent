#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""個股／ETF「每日跌幅分布」統計圖卡產生器。

仿「00878 當日跌到多少% 你會選擇進場？」圖卡：
  期間每日漲跌幅 → 依跌幅區間分桶（<1%、1~2%、2~4%、≥4%）→ 天數／占下跌天數比例
  ＋ 最大單日漲跌幅、今年以來漲幅（可對照比較標的）→ 單一自足 HTML（可選 PNG）。

資料源：twse_hist（TWSE 官方日收盤＋TWT49U 除息），除息日以「前收−股利」為參考價，
避免把除息缺口誤算成暴跌（與證交所／Goodinfo 漲跌幅口徑一致）。

用法：
  python3 drop_stats_card.py 00878                                  # 今年 1/1 ~ 今天，對照 0050
  python3 drop_stats_card.py 00878 --start 2026-01-01 --end 2026-08-31 --compare 0050
  python3 drop_stats_card.py 00631L --compare 0050 --png            # 另存 PNG（需 playwright）
  python3 drop_stats_card.py 00878 --no-html                        # 只印終端統計
  python3 drop_stats_card.py 00631L --edges 1,2,4,7                # 高波動標的多切一桶
  python3 drop_stats_card.py 00878 --raw                            # 不調整除息（與網路圖卡同口徑）
"""
import argparse
import collections
import datetime
import html
import os
import sys

import twse_hist

DEFAULT_EDGES = [1, 2, 4]          # 跌幅分桶界線（%）；正二等高波動標的建議 --edges 1,2,4,7
COLORS = ["green", "blue", "amber", "orange", "red"]
HINTS = ["日常最常見的微幅震盪整理", "大盤回檔時的溫和拉回", "較明顯的修正",
         "大幅修正", "極端急跌"]


def make_buckets(edges):
    """界線 [1,2,4] → [(標籤, 下界含, 上界不含, 顏色, 說明)]，以「跌幅絕對值 %」判斷。"""
    bounds = [0.0] + [float(e) for e in edges] + [999.0]
    n = len(bounds) - 1
    out = []
    for i in range(n):
        lo, hi = bounds[i], bounds[i + 1]
        if i == 0:
            label = f"跌幅不到 {hi:g}%"
        elif i == n - 1:
            label = f"跌幅 {lo:g}% 以上"
        else:
            label = f"跌幅 {lo:g}%~{hi:g}%"
        # 顏色/說明：頭尾固定，中間依序取用
        k = 0 if i == 0 else (len(COLORS) - 1 if i == n - 1 else min(i, len(COLORS) - 2))
        out.append((label, lo, hi, COLORS[k], HINTS[k]))
    return out


# ========================= 計算 =========================
SPLIT_PCT = 30.0   # 單日漲跌超過此值（正二上限約 ±20%）視為分割/反分割，改用官方漲跌價差


def official_ref(code, day):
    """查 STOCK_DAY 當月資料，以「收盤 − 漲跌價差」回推證交所官方參考價；查不到回 None。"""
    url = (f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
           f"?date={day:%Y%m}01&stockNo={code}&response=json")
    try:
        for row in twse_hist._get_json(url).get("data", []):
            if twse_hist._roc_date(row[0]) == day:
                diff = row[7].replace(",", "").lstrip("X")
                return float(row[6].replace(",", "")) - float(diff)
    except Exception:
        pass
    return None


def daily_changes(code, start, end, raw=False):
    """回傳 ([(date, close, pct)], closes, splits)；pct 以除息調整後參考價計算。
    分割/反分割日（漲跌超過 SPLIT_PCT）一律以官方參考價計算，splits 記 {date: 換股倍數}。
    多抓 start 前 20 天，讓第一個交易日也有前收。"""
    closes = twse_hist.daily_closes(code, start - datetime.timedelta(days=20), end)
    if not closes:
        raise SystemExit(f"查無 {code} 收盤資料（上市/上櫃皆無）")
    divs = {} if raw else dict(twse_hist.dividends(code, start, end))
    days = sorted(closes)
    out, splits = [], {}
    for prev, cur in zip(days, days[1:]):
        ref = closes[prev] - divs.get(cur, 0.0)
        pct = (closes[cur] - ref) / ref * 100
        if abs(pct) > SPLIT_PCT:
            oref = official_ref(code, cur)
            if oref:
                r = closes[prev] / oref       # 參考價四捨五入到分，倍數接近整數就取整
                splits[cur] = round(r) if abs(r - round(r)) < 0.02 else round(r, 4)
                ref = oref - divs.get(cur, 0.0) / splits[cur]
                pct = (closes[cur] - ref) / ref * 100
                print(f"  ※ {code} {cur} 偵測到分割（1 拆 {splits[cur]:g}），參考價 {oref:.2f}")
        if cur >= start:
            out.append((cur, closes[cur], round(pct, 2)))
    return out, closes, splits


def ytd_return(closes, start, end, splits=None):
    """start 前最後一個收盤 → end 前最後一個收盤 的價格漲幅 %（不含息，已還原分割）。"""
    before = [d for d in closes if d < start]
    upto = [d for d in closes if d <= end]
    if not before or not upto:
        return None
    b, e = closes[max(before)], closes[max(upto)]
    for d, ratio in (splits or {}).items():
        if max(before) < d <= max(upto):
            e *= ratio
    return (e - b) / b * 100


def analyze(code, start, end, compare=None, raw=False, edges=DEFAULT_EDGES):
    rows, closes, splits = daily_changes(code, start, end, raw)
    downs = [r for r in rows if r[2] < 0]
    stats = {
        "code": code, "start": start, "end": end,
        "trading_days": len(rows),
        "up_days": sum(1 for r in rows if r[2] > 0),
        "flat_days": sum(1 for r in rows if r[2] == 0),
        "down_days": len(downs),
        "last_date": rows[-1][0], "last_close": rows[-1][1],
        "max_up": max(rows, key=lambda r: r[2]),
        "max_down": min(rows, key=lambda r: r[2]),
        "ret": ytd_return(closes, start, end, splits),
        "buckets": [], "raw": raw,
    }
    for label, lo, hi, color, hint in make_buckets(edges):
        hit = [r for r in downs if lo <= abs(r[2]) < hi]
        months = collections.Counter(r[0].month for r in hit)
        stats["buckets"].append({
            "label": label, "color": color, "days": len(hit),
            "ratio": len(hit) / len(downs) * 100 if downs else 0,
            "note": bucket_note(hint, hit, months),
        })
    if compare:
        _, cc, csp = daily_changes(compare, start, end)
        stats["compare"] = (compare, ytd_return(cc, start, end, csp))
    return stats


def bucket_note(hint, hit, months):
    """自動產生「說明與走勢觀察」：天數少列出日期，否則列出集中月份。"""
    if not hit:
        return "期間內未出現"
    if len(hit) <= 3:
        ds = "、".join(r[0].strftime("%m/%d") for r in hit)
        return f"{hint}（僅 {ds} 出現 {len(hit)} 次）"
    top = [f"{m} 月" for m, _ in months.most_common(3)]
    return f"{hint}，集中於 {'、'.join(top)}"


# ========================= 輸出 =========================
def print_report(s):
    print(f"\n{s['code']}  {s['start']} ~ {s['end']}  交易日 {s['trading_days']} 天"
          f"（漲 {s['up_days']}／平 {s['flat_days']}／跌 {s['down_days']}）")
    print(f"{s['last_date']:%m/%d} 收盤 {s['last_close']}  期間漲幅 {s['ret']:+.2f}%", end="")
    if "compare" in s:
        print(f"（{s['compare'][0]} {s['compare'][1]:+.2f}%）", end="")
    print(f"\n最大單日漲幅 {s['max_up'][2]:+.2f}% ({s['max_up'][0]:%m/%d})"
          f"   最大單日跌幅 {s['max_down'][2]:+.2f}% ({s['max_down'][0]:%m/%d})\n")
    print(f"{'跌幅區間':<12}{'天數':>5}{'占比':>9}   說明")
    for b in s["buckets"]:
        print(f"{b['label']:<12}{b['days']:>5}{b['ratio']:>8.1f}%   {b['note']}")


def render_html(s):
    e = html.escape
    ret = s["ret"] or 0
    cmp_html = ""
    if "compare" in s and s["compare"][1] is not None:
        c, cr = s["compare"]
        cmp_html = f'<div class="sub">（{e(c)} 為 {cr:.2f}%）</div>'
        verdict = f"今年以來績效{'打敗' if ret > cr else '落後'} {e(c)}"
    else:
        verdict = "每日跌幅分布統計"
    period = f"{s['start']:%-m/%-d}~{s['end']:%-m/%-d}"
    rows = "".join(
        f'<tr class="{b["color"]}"><td class="lab">{e(b["label"])}</td>'
        f'<td class="num">{b["days"]}<small> 天</small></td>'
        f'<td class="pct">{b["ratio"]:.1f}%</td><td class="note">{e(b["note"])}</td></tr>'
        for b in s["buckets"])
    return f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<title>{e(s['code'])} 跌幅分布</title>
<style>
:root{{--navy:#1b3a6b;--red:#d62828;--green:#2a9d4b;--bg:#f4f7fb;}}
*{{box-sizing:border-box;margin:0}}
body{{min-width:1130px;background:var(--bg);font-family:"PingFang TC","Noto Sans TC",sans-serif;color:var(--navy);padding:24px}}
.card{{width:1080px;margin:auto;background:#fff;border-radius:24px;padding:36px;box-shadow:0 8px 30px #0002}}
h1{{font-size:64px;font-weight:900;line-height:1.15}} h1 b{{color:var(--red)}}
.banner{{margin:18px 0;background:#fff4c2;border-radius:40px;text-align:center;font-size:34px;font-weight:800;padding:12px}}
.period{{background:#f3c6cc;color:#7a1d2a;border-radius:12px;text-align:center;font-size:28px;font-weight:800;padding:10px}}
.kpis{{display:grid;grid-template-columns:1.1fr 1fr 1fr;gap:16px;margin:18px 0}}
.kpi{{border:2px solid #dde4ee;border-radius:16px;padding:16px;text-align:center}}
.kpi .t{{font-size:24px;font-weight:800}} .kpi .v{{font-size:64px;font-weight:900;color:var(--red)}}
.kpi .sub{{font-size:22px;font-weight:700}}
.ud{{display:flex;flex-direction:column;gap:12px}}
.ud div{{border-radius:14px;padding:10px 14px;font-weight:800;font-size:22px}}
.ud .up{{background:#fdecec;color:var(--red);border:2px solid var(--red)}}
.ud .dn{{background:#e9f7ee;color:var(--green);border:2px solid var(--green)}}
.ud span{{font-size:42px;font-weight:900;display:block}}
table{{width:100%;border-collapse:separate;border-spacing:0 8px}}
th{{background:var(--navy);color:#fff;font-size:26px;padding:12px}}
td{{padding:14px;font-size:24px;font-weight:700}}
.lab{{font-size:26px;font-weight:900;border-radius:12px 0 0 12px;white-space:nowrap;width:230px}}
.num{{font-size:48px;font-weight:900;text-align:center;white-space:nowrap;width:150px}} .num small{{font-size:22px}}
.pct{{font-size:44px;font-weight:900;color:var(--red);text-align:center}}
.note{{border-radius:0 12px 12px 0}}
tr.green td{{background:#eaf6ea}} tr.blue td{{background:#e8f0fb}}
tr.amber td{{background:#fdf1dc}} tr.orange td{{background:#fde4d4}} tr.red td{{background:#fbe6ea}}
.foot{{text-align:center;font-size:18px;margin-top:14px;color:#556}}
</style></head><body><div class="card">
<h1>{e(s['code'])} 當日跌到<b>多少%</b><br>你會選擇進場？</h1>
<div class="banner">{verdict}</div>
<div class="period">{period} 每日跌幅統計　總交易日 {s['trading_days']} 天（下跌 {s['down_days']} 天）</div>
<div class="kpis">
 <div class="kpi"><div class="t">{s['last_date']:%-m/%-d} 股價</div><div class="v">{s['last_close']:.2f}</div></div>
 <div class="kpi"><div class="t">期間漲幅</div><div class="v">{ret:.2f}%</div>{cmp_html}</div>
 <div class="ud">
  <div class="up">最大單日漲幅<span>{s['max_up'][2]:+.2f}%</span>({s['max_up'][0]:%m/%d})</div>
  <div class="dn">最大單日跌幅<span>{s['max_down'][2]:+.2f}%</span>({s['max_down'][0]:%m/%d})</div>
 </div>
</div>
<table><tr><th>跌幅區間</th><th>天數</th><th>占總下跌天數比例</th><th>說明與走勢觀察</th></tr>{rows}</table>
<div class="foot">資料來源：證交所（{'未調整除息' if s['raw'] else '除息日以前收−股利為參考價'}）　產出：drop_stats_card.py　{datetime.date.today()}</div>
</div></body></html>"""


def save_png(html_path, png_path):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("（未安裝 playwright，略過 PNG：pip3 install playwright && python3 -m playwright install chromium）")
        return
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1130, "height": 800}, device_scale_factor=2)
        pg.goto("file://" + os.path.abspath(html_path))
        pg.locator(".card").screenshot(path=png_path)
        b.close()
    print("PNG →", png_path)


def main():
    ap = argparse.ArgumentParser(description="每日跌幅分布統計圖卡")
    ap.add_argument("code")
    today = datetime.date.today()
    ap.add_argument("--start", default=f"{today.year}-01-01")
    ap.add_argument("--end", default=str(today))
    ap.add_argument("--compare", default="0050", help="對照標的，空字串＝不比較")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--png", action="store_true")
    ap.add_argument("--edges", default="1,2,4", help="跌幅分桶界線，逗號分隔（例：1,2,4,7）")
    ap.add_argument("--raw", action="store_true", help="不做除息調整（純收盤價相減，除息缺口會被算成下跌）")
    a = ap.parse_args()
    start, end = (datetime.date.fromisoformat(x) for x in (a.start, a.end))
    code = a.code.upper()
    s = analyze(code, start, end, a.compare.upper() if a.compare and a.compare.upper() != code else None, a.raw,
                 [float(x) for x in a.edges.split(',')])
    print_report(s)
    if a.no_html:
        return
    path = f"drop_stats_{code}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_html(s))
    print("\nHTML →", os.path.abspath(path))
    if a.png:
        save_png(path, path.replace(".html", ".png"))


if __name__ == "__main__":
    sys.exit(main())
