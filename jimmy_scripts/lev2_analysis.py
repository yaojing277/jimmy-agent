#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lev2_analysis.py — 台股兩檔 2 倍槓桿 ETF 每日漲跌分析

  00631L 元大台灣50正2   追蹤「台灣50指數」報酬兩倍
  00663L 國泰臺灣加權正2  追蹤「加權股價指數」報酬兩倍

槓桿倍數相同、追蹤標的不同,故兩者每日漲跌的差異即反映
兩檔指數的分歧與各自的槓桿再平衡耗損。

資料源:twse_hist.daily_closes()(TWSE 官方日收盤,無 Yahoo)。
槓桿型 ETF 依規定不配息,兩檔皆無配息紀錄,故直接用原始收盤價
計算日漲跌,不需還原股價。

對外介面:
  fetch(days=60)        -> [(date, p631, p663), ...] 升冪,長度 days+1(算報酬需前一日)
  analyse(rows)         -> dict:逐日明細 daily[] + 統計摘要 stats{}
  build_html(data)      -> 單一自足 HTML 字串

用法:
  python3 lev2_analysis.py                  # 終端印近 60 交易日分析
  python3 lev2_analysis.py --days 120       # 改天數
  python3 lev2_analysis.py --html           # 產出 lev2_site/index.html
  python3 lev2_analysis.py --json           # 輸出 JSON(供其他腳本取用)
"""
import argparse
import datetime
import json
import math
import os
import sys

import twse_hist as th

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = os.path.join(HERE, "lev2_site")

CODE_A = "00631L"        # 基準比較的「主角」,差異一律以 A - B 計
CODE_B = "00663L"
NAME_A = "元大台灣50正2"
NAME_B = "國泰臺灣加權正2"
IDX_A = "台灣50指數"
IDX_B = "加權股價指數"

TRADING_DAYS_PER_YEAR = 252


# ========================= 取價 =========================
def fetch(days=60):
    """取兩檔近 days 個交易日的共同收盤價;回 [(date, p631, p663), ...] 升冪。

    長度為 days+1:第一筆只當「前一日」用來算第二筆的報酬。
    以日曆天回抓 days*2.2 天(含週末與國定假日餘裕),不足時再往前追一次。
    """
    today = datetime.date.today()
    span = max(int(days * 2.2), 30)
    for attempt in range(3):
        start = today - datetime.timedelta(days=span)
        a = th.daily_closes(CODE_A, start, today)
        b = th.daily_closes(CODE_B, start, today)
        common = sorted(set(a) & set(b))          # 只取兩檔都有交易的日期
        if len(common) >= days + 1:
            common = common[-(days + 1):]
            return [(d, a[d], b[d]) for d in common]
        span = int(span * 1.8)                    # 資料不足 -> 拉長回抓區間重試
    if not common:
        sys.exit(f"查無 {CODE_A}/{CODE_B} 共同交易日資料,請確認網路或 TWSE 服務狀態。")
    print(f"⚠ 僅取得 {len(common)} 個共同交易日(要求 {days + 1}),以現有資料分析。",
          file=sys.stderr)
    return [(d, a[d], b[d]) for d in common]


# ========================= 統計工具 =========================
def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs):
    """樣本標準差(n-1);樣本數不足回 0。"""
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _cov(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = _mean(xs), _mean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n - 1)


def _corr(xs, ys):
    """Pearson 相關係數;任一方無波動(標準差 0)時回 0。"""
    sx, sy = _stdev(xs), _stdev(ys)
    if sx == 0 or sy == 0:
        return 0.0
    return _cov(xs, ys) / (sx * sy)


# ========================= 分析 =========================
def analyse(rows):
    """rows: [(date, pA, pB), ...] 升冪,長度 n+1 -> 產出 n 筆逐日報酬與統計。"""
    daily = []
    for (_, pa0, pb0), (d, pa, pb) in zip(rows, rows[1:]):
        ra = (pa / pa0 - 1) * 100          # 當日漲跌 %
        rb = (pb / pb0 - 1) * 100
        daily.append({
            "date": d.isoformat(),
            "pa": round(pa, 2), "pb": round(pb, 2),
            "ra": round(ra, 3), "rb": round(rb, 3),
            "diff": round(ra - rb, 3),     # 正值＝當日 00631L 較強
        })

    ras = [x["ra"] for x in daily]
    rbs = [x["rb"] for x in daily]
    diffs = [x["diff"] for x in daily]

    # 累積報酬:用期間首末收盤價直接算(等同連乘日報酬,但無浮點累積誤差)
    cum_a = (rows[-1][1] / rows[0][1] - 1) * 100
    cum_b = (rows[-1][2] / rows[0][2] - 1) * 100

    # 同向天數:兩檔當日同漲或同跌(其中一檔平盤不計入同向)
    same_dir = sum(1 for a, b in zip(ras, rbs) if (a > 0 and b > 0) or (a < 0 and b < 0))

    # 以 00663L(追蹤大盤)當日方向分組,看多頭日/空頭日各自誰較強
    up_days = [x for x in daily if x["rb"] > 0]
    down_days = [x for x in daily if x["rb"] < 0]

    mx = max(daily, key=lambda x: abs(x["diff"]))          # 單日最大偏離
    var_b = _stdev(rbs) ** 2
    ann = math.sqrt(TRADING_DAYS_PER_YEAR)

    stats = {
        "n": len(daily),
        "start": daily[0]["date"], "end": daily[-1]["date"],
        "corr": round(_corr(ras, rbs), 4),
        "beta": round(_cov(ras, rbs) / var_b, 4) if var_b else 0.0,
        "cum_a": round(cum_a, 2), "cum_b": round(cum_b, 2),
        "cum_gap": round(cum_a - cum_b, 2),
        "mean_a": round(_mean(ras), 4), "mean_b": round(_mean(rbs), 4),
        "sd_a": round(_stdev(ras), 3), "sd_b": round(_stdev(rbs), 3),
        "vol_a": round(_stdev(ras) * ann, 2),              # 年化波動 %
        "vol_b": round(_stdev(rbs) * ann, 2),
        "mad": round(_mean([abs(x) for x in diffs]), 4),   # 平均絕對追蹤差(百分點)
        "sd_diff": round(_stdev(diffs), 4),
        "max_dev": {"date": mx["date"], "diff": mx["diff"],
                    "ra": mx["ra"], "rb": mx["rb"]},
        "same_dir": same_dir,
        "same_dir_pct": round(same_dir / len(daily) * 100, 1),
        "a_wins": sum(1 for x in diffs if x > 0),          # 00631L 較強的天數
        "b_wins": sum(1 for x in diffs if x < 0),
        "up_n": len(up_days),
        "up_a": round(_mean([x["ra"] for x in up_days]), 3) if up_days else 0.0,
        "up_b": round(_mean([x["rb"] for x in up_days]), 3) if up_days else 0.0,
        "down_n": len(down_days),
        "down_a": round(_mean([x["ra"] for x in down_days]), 3) if down_days else 0.0,
        "down_b": round(_mean([x["rb"] for x in down_days]), 3) if down_days else 0.0,
        "last": daily[-1],
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    return {"daily": daily, "stats": stats}


# ========================= 終端輸出 =========================
def _c(v, width=7, dp=2, sign=True):
    """數值上色:正綠負紅(ANSI)。"""
    s = f"{v:+.{dp}f}" if sign else f"{v:.{dp}f}"
    color = "\033[32m" if v > 0 else ("\033[31m" if v < 0 else "\033[90m")
    return f"{color}{s:>{width}}\033[0m"


def print_report(data, tail=20):
    d, s = data["daily"], data["stats"]
    print(f"\n\033[1m{CODE_A} {NAME_A}　vs　{CODE_B} {NAME_B}\033[0m")
    print(f"期間 {s['start']} ~ {s['end']}（{s['n']} 個交易日）　產生於 {s['generated']}")

    print(f"\n\033[1m── 最近 {min(tail, len(d))} 個交易日 ──\033[0m")
    print(f"{'日期':<12}{CODE_A:>9}{'漲跌%':>10}{CODE_B:>10}{'漲跌%':>10}{'差異':>10}")
    for x in d[-tail:]:
        print(f"{x['date']:<12}{x['pa']:>9.2f}{_c(x['ra'], 10)}"
              f"{x['pb']:>10.2f}{_c(x['rb'], 10)}{_c(x['diff'], 10)}")

    print(f"\n\033[1m── 連動與追蹤統計 ──\033[0m")
    print(f"  日報酬相關係數      {s['corr']:.4f}"
          f"        Beta(631L對663L)  {s['beta']:.4f}")
    print(f"  平均絕對追蹤差      {s['mad']:.4f} pp"
          f"     追蹤差標準差      {s['sd_diff']:.4f} pp")
    print(f"  同向天數            {s['same_dir']}/{s['n']}（{s['same_dir_pct']}%）"
          f"     631L較強 {s['a_wins']} 天／663L較強 {s['b_wins']} 天")
    print(f"  單日最大偏離        {s['max_dev']['date']}　"
          f"631L {s['max_dev']['ra']:+.2f}%　663L {s['max_dev']['rb']:+.2f}%　"
          f"差 {s['max_dev']['diff']:+.2f} pp")

    print(f"\n\033[1m── 期間績效 ──\033[0m")
    print(f"  {CODE_A}  累積{_c(s['cum_a'], 8)}%   日均{_c(s['mean_a'], 8, 4)}%   "
          f"年化波動 {s['vol_a']:.2f}%")
    print(f"  {CODE_B}  累積{_c(s['cum_b'], 8)}%   日均{_c(s['mean_b'], 8, 4)}%   "
          f"年化波動 {s['vol_b']:.2f}%")
    print(f"  累積差距（631L−663L）{_c(s['cum_gap'], 8)} pp")

    print(f"\n\033[1m── 多空分組（以 663L 當日方向）──\033[0m")
    print(f"  上漲日 {s['up_n']:>3} 天：631L 日均{_c(s['up_a'], 8, 3)}%   "
          f"663L 日均{_c(s['up_b'], 8, 3)}%")
    print(f"  下跌日 {s['down_n']:>3} 天：631L 日均{_c(s['down_a'], 8, 3)}%   "
          f"663L 日均{_c(s['down_b'], 8, 3)}%")
    print()


# ========================= HTML 圖卡 =========================
def build_html(data):
    """單一自足 HTML(資料內嵌為 JSON,Chart.js 走 CDN)。"""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    s = data["stats"]
    tpl = _HTML_TEMPLATE
    for k, v in {
        "__DATA__": payload,
        "__CODE_A__": CODE_A, "__CODE_B__": CODE_B,
        "__NAME_A__": NAME_A, "__NAME_B__": NAME_B,
        "__IDX_A__": IDX_A, "__IDX_B__": IDX_B,
        "__START__": s["start"], "__END__": s["end"],
        "__N__": str(s["n"]), "__GEN__": s["generated"],
    }.items():
        tpl = tpl.replace(k, v)
    return tpl


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>正二 ETF 每日漲跌分析｜__CODE_A__ vs __CODE_B__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root{
    --bg:#0f1115; --card:#171a21; --line:#262b35;
    --txt:#e6e8ec; --sub:#9aa3b2;
    --ca:#ff6b4a;   /* 00631L 橘 */
    --cb:#4aa3ff;   /* 00663L 藍 */
    --up:#ff5d5d; --down:#2ec27e;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
    font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;
    padding:28px 14px;line-height:1.5}
  .wrap{max-width:1080px;margin:0 auto}
  h1{font-size:23px;margin:0 0 4px}
  .meta{color:var(--sub);font-size:13px;margin-bottom:22px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;
    padding:20px;margin-bottom:18px}
  .sec-title{font-size:16px;font-weight:700;margin:0 0 14px}
  .legend{display:flex;gap:16px;align-items:center;margin-bottom:12px;
    font-size:14px;flex-wrap:wrap}
  .dot{display:inline-block;width:12px;height:12px;border-radius:3px;
    margin-right:6px;vertical-align:middle}
  .chart-box{position:relative;height:340px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
  .kpi{background:#1d2129;border:1px solid var(--line);border-radius:11px;padding:14px}
  .kpi .k{font-size:12px;color:var(--sub);margin-bottom:6px}
  .kpi .v{font-size:24px;font-weight:700;letter-spacing:-.4px}
  .kpi .s{font-size:12px;color:var(--sub);margin-top:4px}
  .tbl-scroll{overflow-x:auto;max-height:460px;overflow-y:auto}
  table{width:100%;border-collapse:collapse;font-size:13.5px;min-width:560px}
  th,td{padding:7px 8px;text-align:right;border-bottom:1px solid var(--line);
    white-space:nowrap}
  th:first-child,td:first-child{text-align:left}
  thead th{color:var(--sub);font-weight:600;background:var(--card);
    position:sticky;top:0;border-bottom:2px solid var(--line)}
  .pos{color:var(--up)} .neg{color:var(--down)} .zero{color:var(--sub)}
  .ca{color:var(--ca)} .cb{color:var(--cb)}
  .note{font-size:12px;color:var(--sub);margin-top:14px}
  .note b{color:var(--txt)}
  @media (max-width:600px){
    h1{font-size:19px} .chart-box{height:260px} .kpi .v{font-size:20px}
  }
</style>
</head>
<body>
<div class="wrap">
  <h1>正二 ETF 每日漲跌分析</h1>
  <div class="meta">
    <span class="ca">__CODE_A__ __NAME_A__</span>（追蹤 __IDX_A__ ×2）　vs
    <span class="cb">__CODE_B__ __NAME_B__</span>（追蹤 __IDX_B__ ×2）<br>
    期間 __START__ ~ __END__（__N__ 個交易日）｜資料：TWSE 官方收盤價｜更新於 __GEN__
  </div>

  <div class="card">
    <div class="sec-title">關鍵指標</div>
    <div class="grid" id="kpis"></div>
  </div>

  <div class="card">
    <div class="sec-title">累積報酬走勢（期初＝0%）</div>
    <div class="legend">
      <span><span class="dot" style="background:var(--ca)"></span>__CODE_A__</span>
      <span><span class="dot" style="background:var(--cb)"></span>__CODE_B__</span>
    </div>
    <div class="chart-box"><canvas id="cum"></canvas></div>
  </div>

  <div class="card">
    <div class="sec-title">每日漲跌差異（__CODE_A__ − __CODE_B__，百分點）</div>
    <div class="chart-box"><canvas id="diff"></canvas></div>
    <div class="note">正值（綠）＝當日 <b>__CODE_A__</b> 較強；負值（紅）＝<b>__CODE_B__</b> 較強。
      兩檔槓桿倍數相同，差異主要來自 __IDX_A__ 與 __IDX_B__ 的成分股分歧。</div>
  </div>

  <div class="card">
    <div class="sec-title">每日明細</div>
    <div class="tbl-scroll"><table id="tbl">
      <thead><tr>
        <th>日期</th><th>__CODE_A__ 收盤</th><th>漲跌%</th>
        <th>__CODE_B__ 收盤</th><th>漲跌%</th><th>差異 (pp)</th>
      </tr></thead><tbody></tbody>
    </table></div>
    <div class="note">最新交易日在最上方。</div>
  </div>
</div>

<script>
const DATA = __DATA__;
const D = DATA.daily, S = DATA.stats;
const CA = getComputedStyle(document.documentElement).getPropertyValue('--ca').trim();
const CB = getComputedStyle(document.documentElement).getPropertyValue('--cb').trim();
const UP = getComputedStyle(document.documentElement).getPropertyValue('--up').trim();
const DOWN = getComputedStyle(document.documentElement).getPropertyValue('--down').trim();
const SUB = getComputedStyle(document.documentElement).getPropertyValue('--sub').trim();
const LINE = getComputedStyle(document.documentElement).getPropertyValue('--line').trim();

const sgn = (v, dp = 2) => (v > 0 ? '+' : '') + v.toFixed(dp);
const cls = v => v > 0 ? 'pos' : (v < 0 ? 'neg' : 'zero');

/* ---- KPI ---- */
const kpis = [
  { k: '日報酬相關係數', v: S.corr.toFixed(4),
    s: S.corr >= 0.95 ? '高度連動' : '連動偏低，留意成分差異' },
  { k: '平均絕對追蹤差', v: S.mad.toFixed(3) + ' pp', s: '每日漲跌差距的平均幅度' },
  { k: '單日最大偏離', v: sgn(S.max_dev.diff) + ' pp', s: S.max_dev.date },
  { k: '同向天數', v: S.same_dir_pct.toFixed(1) + '%', s: S.same_dir + ' / ' + S.n + ' 天同漲同跌' },
  { k: '期間累積報酬 · __CODE_A__', v: sgn(S.cum_a) + '%', s: '年化波動 ' + S.vol_a.toFixed(1) + '%', c: S.cum_a },
  { k: '期間累積報酬 · __CODE_B__', v: sgn(S.cum_b) + '%', s: '年化波動 ' + S.vol_b.toFixed(1) + '%', c: S.cum_b },
  { k: '累積差距', v: sgn(S.cum_gap) + ' pp',
    s: S.cum_gap > 0 ? '__CODE_A__ 領先' : '__CODE_B__ 領先', c: S.cum_gap },
  { k: '較強天數', v: S.a_wins + ' : ' + S.b_wins, s: '__CODE_A__ : __CODE_B__' },
];
document.getElementById('kpis').innerHTML = kpis.map(x =>
  `<div class="kpi"><div class="k">${x.k}</div>
   <div class="v ${x.c === undefined ? '' : cls(x.c)}">${x.v}</div>
   <div class="s">${x.s}</div></div>`).join('');

/* ---- 累積報酬走勢：以期間第一天為基準 0% ---- */
const labels = D.map(x => x.date.slice(5));
let ca = 1, cb = 1;
const cumA = [], cumB = [];
D.forEach(x => {
  ca *= (1 + x.ra / 100); cb *= (1 + x.rb / 100);
  cumA.push((ca - 1) * 100); cumB.push((cb - 1) * 100);
});

const gridCfg = { color: LINE, drawTicks: false };
const tickCfg = { color: SUB, font: { size: 11 } };

new Chart(document.getElementById('cum'), {
  type: 'line',
  data: {
    labels,
    datasets: [
      { label: '__CODE_A__', data: cumA, borderColor: CA, backgroundColor: CA + '22',
        borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: .15, fill: true },
      { label: '__CODE_B__', data: cumB, borderColor: CB, backgroundColor: CB + '22',
        borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: .15, fill: true },
    ],
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { callbacks: { label: c => ` ${c.dataset.label}  ${sgn(c.parsed.y)}%` } },
    },
    scales: {
      x: { grid: { display: false }, ticks: { ...tickCfg, maxTicksLimit: 12 } },
      y: { grid: gridCfg, ticks: { ...tickCfg, callback: v => v + '%' } },
    },
  },
});

/* ---- 每日差異柱狀 ---- */
new Chart(document.getElementById('diff'), {
  type: 'bar',
  data: {
    labels,
    datasets: [{
      data: D.map(x => x.diff),
      backgroundColor: D.map(x => x.diff >= 0 ? UP + 'cc' : DOWN + 'cc'),
      borderRadius: 2,
    }],
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: c => {
            const x = D[c.dataIndex];
            return [` 差異 ${sgn(x.diff)} pp`,
                    ` __CODE_A__ ${sgn(x.ra)}%`,
                    ` __CODE_B__ ${sgn(x.rb)}%`];
          },
        },
      },
    },
    scales: {
      x: { grid: { display: false }, ticks: { ...tickCfg, maxTicksLimit: 12 } },
      y: { grid: gridCfg, ticks: { ...tickCfg, callback: v => v + 'pp' } },
    },
  },
});

/* ---- 明細表（最新在上）---- */
document.querySelector('#tbl tbody').innerHTML = D.slice().reverse().map(x => `
  <tr>
    <td>${x.date}</td>
    <td>${x.pa.toFixed(2)}</td><td class="${cls(x.ra)}">${sgn(x.ra)}%</td>
    <td>${x.pb.toFixed(2)}</td><td class="${cls(x.rb)}">${sgn(x.rb)}%</td>
    <td class="${cls(x.diff)}">${sgn(x.diff)}</td>
  </tr>`).join('');
</script>
</body>
</html>
"""


# ========================= CLI =========================
def main():
    ap = argparse.ArgumentParser(description="00631L / 00663L 每日漲跌分析")
    ap.add_argument("--days", type=int, default=60, help="分析的交易日數(預設 60)")
    ap.add_argument("--tail", type=int, default=20, help="終端顯示最近幾筆(預設 20)")
    ap.add_argument("--html", action="store_true", help="產出 lev2_site/index.html")
    ap.add_argument("--json", action="store_true", help="輸出 JSON 到 stdout")
    args = ap.parse_args()

    data = analyse(fetch(args.days))

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    print_report(data, tail=args.tail)

    if args.html:
        os.makedirs(SITE_DIR, exist_ok=True)
        out = os.path.join(SITE_DIR, "index.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(build_html(data))
        print(f"✓ 已產出 {out}")
        print(f"  部署:bash {os.path.join(HERE, 'deploy_lev2.sh')}")


if __name__ == "__main__":
    main()
