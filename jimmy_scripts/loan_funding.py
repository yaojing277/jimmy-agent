#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""loan_funding.py — 資金來源追蹤：信貸投入股市的報酬是否覆蓋利息／月付

觸發語：「資金來源」「信貸報酬」

【為什麼需要這支】
帳本（16_資產歷史／股票買賣紀錄）只追蹤「股票帳戶內的進出」，
**錢從哪個來源進到股票帳戶（薪水／存款／信貸）完全沒有被記錄**。
2026 年買進 1,250 萬裡，513 萬是賣股再投入、427 萬是穎崴員工認股，
真正的一般新資金約 310 萬——而信貸餘額 489 萬，顯然是主要資金來源之一。
這支把「借錢投資」這件事的成本與報酬攤開，回答兩個問題：
  1. 划不划算：歸屬損益 vs **累計利息**（利息才是真實成本，本金攤還只是把負債變現金）
  2. 撐不撐得住：歸屬損益 vs **累計月付**（現金流觀點，每月真的要掏這麼多錢出去）

【口徑（2026-10-11 Jimmy 決定）】
  - 只追蹤**信貸**，房貸是自住房的，與投資無關，納入只會混淆
    （「06_阿良資產負債表」的本金槓桿本來也只計信貸）
  - 投入股市金額由 Jimmy **直接指定**（撥款金額未必全數進股市）
  - 以**實際損益金額**比較，不用年化百分比——直接看賺的錢夠不夠付利息

【報酬歸屬】
  歸屬損益 = 投入金額 × 期間 TWR。
  用 TWR 不用簡單報酬率：TWR 是「每一塊錢的報酬」，乘上投入金額才是這筆錢賺到的數字；
  簡單報酬率受資金進出時點干擾，乘出來沒有意義。TWR 取自 update_wealth_os._monthly_pnl，
  與「02_每日漲跌」月損益表同一套（含配息）。

【設定檔】
  jimmy_scripts/loan_config.json（**不進版控**，內含個人財務數字，比照 cathay_report_notes.json）
  {
    "撥款日":   "2026/03/05",
    "原始金額": 5000000,
    "年利率":   0.0361,
    "總期數":   84,
    "月付":     66000,
    "投入股市": 4500000,
    "備註":     "7 年期信用貸款"
  }
  缺檔時印出範本並結束，不會寫任何東西。

用法：
  python3 loan_funding.py              # 終端列印報表（不寫試算表）
  python3 loan_funding.py --sheet      # 另外寫入「股價試算」的「資金來源」分頁
  python3 loan_funding.py --sheet --yes  # 不詢問直接寫
"""
import argparse
import json
import os
import sys
import warnings
import datetime as dt

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import update_wealth_os as u                      # noqa: E402
from googleapiclient.discovery import build       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "loan_config.json")
SHEET_NAME = "資金來源"
TEMPLATE = {
    "撥款日": "2026/03/05", "原始金額": 5000000, "年利率": 0.0361,
    "總期數": 84, "月付": 66000, "投入股市": 4500000, "備註": "7 年期信用貸款",
}


def load_config(skip_if_missing=False):
    """設定來源優先序:環境變數 LOAN_CONFIG_JSON(GitHub Actions 用) → 本機 loan_config.json。
    雲端跑不到本機檔案(含個人財務數字、不進版控),所以比照 GOOGLE_SA_JSON 走 Secret。"""
    env = os.environ.get("LOAN_CONFIG_JSON", "").strip()
    if env:
        try:
            c = json.loads(env)
        except json.JSONDecodeError as e:
            sys.exit(f"✗ 環境變數 LOAN_CONFIG_JSON 不是合法 JSON：{e}")
        miss = [k for k in ("撥款日", "原始金額", "年利率", "總期數", "月付", "投入股市") if k not in c]
        if miss:
            sys.exit(f"✗ LOAN_CONFIG_JSON 缺少欄位：{', '.join(miss)}")
        return c
    if not os.path.exists(CONFIG) and skip_if_missing:
        print("— 無 LOAN_CONFIG_JSON 也無本機設定檔，跳過資金來源更新（不算失敗）")
        return None
    if not os.path.exists(CONFIG):
        print(f"✗ 找不到設定檔 {CONFIG}\n請依下列範本建立（數字換成實際值）：\n")
        print(json.dumps(TEMPLATE, ensure_ascii=False, indent=2))
        print("\n⚠ 這個檔案含個人財務數字，已列入 .gitignore，不要提交版控。")
        sys.exit(1)
    c = json.load(open(CONFIG, encoding="utf-8"))
    miss = [k for k in ("撥款日", "原始金額", "年利率", "總期數", "月付", "投入股市") if k not in c]
    if miss:
        sys.exit(f"✗ 設定檔缺少欄位：{', '.join(miss)}")
    return c


def amortize(principal, annual_rate, n_periods, pmt, start):
    """等額本息攤還表。回傳 [(期數, 當期日期, 月付, 利息, 本金, 期末餘額), ...]。

    ⚠ 以設定檔的「月付」為準，不自己用公式算——實際銀行的月付可能有進位差異。
    最後一期若餘額不足，月付自動縮為「餘額+當期利息」，避免攤出負餘額。"""
    r = annual_rate / 12
    bal = float(principal)
    rows = []
    y, m, d = (int(x) for x in str(start).replace("-", "/").split("/"))
    for i in range(1, int(n_periods) + 1):
        mm = m + i - 1
        pay_date = dt.date(y + mm // 12, mm % 12 + 1, min(d, 28))
        interest = bal * r
        pay = min(pmt, bal + interest)
        princ = pay - interest
        bal = max(0.0, bal - princ)
        rows.append((i, pay_date, pay, interest, princ, bal))
        if bal <= 0:
            break
    return rows


def overseas_twd(creds):
    """複委託帳戶的台幣市值合計（M 欄「台幣合計」那列）。

    ⚠ 複委託尚未併入 16_資產歷史／04_ETF分析,所以必須另外讀這張分頁,
      否則負債比率的分母會漏掉一整個帳戶、比率被低估。
    讀不到就回 (0.0, 原因)——寧可標示「口徑不完整」,也不要讓整步失敗。
    """
    try:
        svc = build("sheets", "v4", credentials=creds)
        rows = svc.spreadsheets().values().get(
            spreadsheetId=u.SPREADSHEET_ID, range="複委託!A1:N40"
        ).execute().get("values", [])
    except Exception as e:                      # 分頁不存在/API 抖動
        return 0.0, f"讀取失敗（{type(e).__name__}）"
    for r in rows:
        if r and str(r[0]).strip() == "台幣合計":
            try:
                return float(str(r[12]).replace(",", "")), ""   # M 欄
            except (IndexError, ValueError):
                return 0.0, "「台幣合計」列找不到 M 欄數值"
    return 0.0, "找不到「台幣合計」列"


def debt_ratio(bal, mv_main, cash, mv_os, cfg):
    """信貸負債比率＝信貸餘額 ÷ 投資淨資產。

    淨資產＝(主帳戶股票市值 ＋ 現金 ＋ 複委託台幣市值) − 信貸餘額。
    口徑依 A8 阿吧 EP132：**自住房與房貸不計入**(與投資槓桿無關),
    他的例子是「淨資產 1000 萬借 300 萬＝1.3 倍槓桿」,即借款疊在自有本金之上。

    ⚠ 分子用**信貸餘額**而非「投入股市的金額」:沒進股市的那部分仍在付利息,
      而它對應的現金也已計入分母,用投入金額會兩頭都偏樂觀。
    ⚠ 借錢**不改變淨資產**(現金增、負債同步增),所以「再借」只會單向推高比率;
      額度只能靠攤還本金或淨資產成長來擴張。
    """
    assets = mv_main + cash + mv_os
    equity = assets - bal
    if equity <= 0:
        return None
    return {
        "assets": assets, "equity": equity, "ratio": bal / equity,
        "cap_max": equity * cfg["debt_max"], "cap_warn": equity * cfg["debt_warn"],
        "room_max": equity * cfg["debt_max"] - bal,
        "room_warn": equity * cfg["debt_warn"] - bal,
        "max": cfg["debt_max"], "warn": cfg["debt_warn"],
    }


def main():
    ap = argparse.ArgumentParser(description="資金來源：信貸投入與報酬覆蓋檢驗")
    ap.add_argument("--sheet", action="store_true", help="寫入「股價試算」的「資金來源」分頁")
    ap.add_argument("--yes", action="store_true", help="不詢問直接寫入")
    ap.add_argument("--asof", help="結算日（預設今天），例 2026/10/08")
    ap.add_argument("--skip-if-no-config", action="store_true",
                    help="沒有設定檔時正常結束（exit 0）而非中止，排程專用")
    ap.add_argument("--line-file", help="輸出一行負債比率摘要到此檔（給 workflow 併進每日 LINE）")
    args = ap.parse_args()

    c = load_config(skip_if_missing=args.skip_if_no_config)
    if c is None:
        return
    asof = (u._parse_rec_date(args.asof) if args.asof else dt.date.today())

    # ── 攤還表：算到 asof 為止已繳幾期、累計利息與月付 ──────────
    sch = amortize(c["原始金額"], c["年利率"], c["總期數"], c["月付"], c["撥款日"])
    done = [x for x in sch if x[1] <= asof]
    paid = sum(x[2] for x in done)
    interest = sum(x[3] for x in done)
    principal = sum(x[4] for x in done)
    bal = done[-1][5] if done else float(c["原始金額"])
    future_interest = sum(x[3] for x in sch if x[1] > asof)
    # 設定檔的「月付×期數」若與「本金＋利息」對不起來,攤完會留殘額——
    # 代表利率/期數/月付三者不自洽(多半是利率填得不夠精確)。出聲提醒,不默默吞掉。
    residual = sch[-1][5] if sch else 0.0
    if residual > 1:
        print(f"⚠ 攤還 {len(sch)} 期後仍有殘額 {residual:,.0f} 元——"
              f"設定檔的年利率／期數／月付三者不自洽,請核對銀行的攤還表。")

    # ── 投資報酬：以 TWR 乘上投入金額（與月損益表同一套口徑，含配息）──
    creds = u.get_creds()
    import tempfile
    wd = tempfile.mkdtemp(prefix="loan_")
    _drive, _meta, path, _bk = u.download_xlsm(creds, wd)
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    hws = wb[u.HIST_TAB]
    hist = []
    for r in range(2, hws.max_row + 1):
        dte = hws.cell(r, 1).value
        if dte is None:
            continue
        fl, dv = hws.cell(r, 9).value, hws.cell(r, 10).value
        hist.append((str(dte)[:10], float(hws.cell(r, 2).value), float(hws.cell(r, 3).value),
                     float(hws.cell(r, 4).value),
                     float(fl) if isinstance(fl, (int, float)) else None,
                     float(dv) if isinstance(dv, (int, float)) else None))
    hist.sort(key=lambda x: u._hist_sort_key(x[0]))
    months = u._monthly_pnl(hist)            # 新到舊：(y,m,pnl,flow,mv_end,ret,twr,div,mwr)

    # ── 負債比率監控（EP132 的 30%／20% 門檻）────────────────────
    scfg = u.read_settings(wb)
    mv_main = hist[-1][1] if hist else 0.0          # hist 已排成舊→新，取最新總市值
    mv_date = hist[-1][0] if hist else "—"
    mv_os, os_note = overseas_twd(creds)
    DR = debt_ratio(bal, mv_main, scfg["cash"], mv_os, scfg)

    draw = u._parse_rec_date(str(c["撥款日"]).replace("-", "/"))
    invested = float(c["投入股市"])
    # ⚠ 兩個都會嚴重失真的做法,都不要用:
    #   (a) 用「撥款月的整月 TWR」——7/03 撥款卻把 7/01~7/02 的漲跌也算進來
    #   (b) 假設全額在撥款日一次進場——實測 2026/07 的 283 萬是整個月分批投入的
    #       (7/17 才累積到 243 萬、7/29 才滿額),而跌勢集中在下旬,
    #       一次進場的假設會把 7 月虧損誇大數十萬。
    # 正確做法:**依撥款後的實際淨投入逐筆建批**,每批各自從自己的投入日起算逐日報酬。
    rate = {}              # {日期: 當日報酬率}
    for j in range(1, len(hist)):
        d = u._parse_rec_date(u._hist_sort_key(hist[j][0]))
        prev_mv = hist[j - 1][1]
        pnl = hist[j][1] - prev_mv - (hist[j][4] or 0.0) + (hist[j][5] or 0.0)
        rate[d] = (pnl / prev_mv) if prev_mv else 0.0
    days = sorted(rate)
    flows_after = {d: (hist[j][4] or 0.0) for j, d in
                   ((j, u._parse_rec_date(u._hist_sort_key(hist[j][0])))
                    for j in range(1, len(hist))) if d > draw}
    # 投入截止日:預設**撥款月最後一天**。
    # ⚠ 2026-10-12 Jimmy 確認:300 萬在 7 月就打完了,若本帳戶的淨投入吸收不完,
    #   差額多半是拿去**複委託**(海外券商,本系統完全沒追蹤),不該硬對應到 8、9 月的買進
    #   ——那會讓信貸的錢「蹭到」後面幾個月的漲幅,把報酬灌水。
    #   吸收不完的部分列為「未對應」,**不計入報酬歸屬的分母**。
    cut = c.get("投入截止日")
    if cut:
        cutoff = u._parse_rec_date(str(cut).replace("-", "/"))
    else:
        nm = dt.date(draw.year + draw.month // 12, draw.month % 12 + 1, 1)
        cutoff = nm - dt.timedelta(days=1)
    remain, tranches = invested, []          # tranches: [(投入日, 金額)]
    for d in sorted(flows_after):
        if d > cutoff:
            break
        f = flows_after[d]
        if f <= 0 or remain <= 0:
            continue
        take = min(f, remain)
        tranches.append((d, take))
        remain -= take
    unmatched = remain if remain > 1 else 0.0
    invested_tracked = invested - unmatched
    # 每批各自從投入日起累乘之後的日報酬
    detail_m, total_now = {}, 0.0
    for (td, amt) in tranches:
        val = amt
        for d in days:
            if d <= td:
                continue
            gain = val * rate[d]
            k = (d.year, d.month)
            detail_m[k] = detail_m.get(k, 0.0) + gain
            val += gain
        total_now += val
    attributed = total_now - invested_tracked
    cum = (total_now / invested_tracked) if invested_tracked else 1.0
    detail = [(k[0], k[1], None, detail_m[k]) for k in sorted(detail_m)]
    deployed_note = (f"分 {len(tranches)} 批投入，{tranches[0][0]} ~ {tranches[-1][0]}"
                     if tranches else "無對應批次")
    unmatched_note = (f"未對應 {unmatched:,.0f}（截止 {cutoff} 前本帳戶淨投入吸收不完；"
                      f"推測為複委託等本系統未追蹤的去向，不計入報酬歸屬）" if unmatched else "")

    print(f"\n{'='*64}\n  資金來源追蹤｜信貸（結算日 {asof}）\n{'='*64}")
    print(f"  撥款日 {c['撥款日']}　原始金額 {c['原始金額']:>12,.0f}　年利率 {c['年利率']:.2%}")
    print(f"  總期數 {c['總期數']} 期　月付 {c['月付']:,.0f}　備註：{c.get('備註','')}")
    print(f"\n  已繳 {len(done)} / {c['總期數']} 期")
    print(f"    累計已付      {paid:>12,.0f}")
    print(f"    ├ 累計利息    {interest:>12,.0f}   ← 真實成本")
    print(f"    └ 累計本金    {principal:>12,.0f}   （負債轉成資產，非成本）")
    print(f"    目前餘額      {bal:>12,.0f}")
    print(f"    未來剩餘利息  {future_interest:>12,.0f}")
    print(f"\n  信貸撥款中指定投入股市 {invested:,.0f}")
    print(f"    本帳戶可對應 {invested_tracked:,.0f}（{deployed_note}；加權累計報酬 {cum-1:+.2%}）")
    if unmatched:
        print(f"    ⚠ {unmatched_note}")
    print(f"    歸屬損益      {attributed:>12,.0f}")
    if DR:
        over = DR["ratio"] > DR["max"]
        print(f"\n{'─'*64}\n  負債比率（信貸餘額 ÷ 投資淨資產）\n{'─'*64}")
        print(f"    主帳戶股票市值  {mv_main:>12,.0f}   （{mv_date} 收盤）")
        print(f"    ＋ 現金         {scfg['cash']:>12,.0f}")
        print(f"    ＋ 複委託       {mv_os:>12,.0f}" + (f"   ⚠ {os_note}" if os_note else ""))
        print(f"    ＝ 投資資產     {DR['assets']:>12,.0f}")
        print(f"    − 信貸餘額      {bal:>12,.0f}")
        print(f"    ＝ 投資淨資產   {DR['equity']:>12,.0f}   ← 門檻的分母")
        print(f"\n    負債比率 {DR['ratio']:.2%}"
              f"   {'✗ 已超過' if over else '✓ 未超過'} {DR['max']:.0%} 上限")
        for label, cap, room in (("上限", DR["cap_max"], DR["room_max"]),
                                 ("保守線", DR["cap_warn"], DR["room_warn"])):
            pct = DR["max"] if label == "上限" else DR["warn"]
            state = f"尚可借 {room:,.0f}" if room > 0 else f"已超出 {-room:,.0f}"
            print(f"      {pct:.0%} {label}：可借上限 {cap:>12,.0f}   {state}")
        print(f"\n    ⚠ 借錢不改變淨資產（現金增、負債同步增），「再借」只會推高比率；")
        print(f"       額度只能靠攤還本金或淨資產成長擴張。")
        print(f"    ⚠ 自住房與房貸不計入（與投資槓桿無關），口徑依 A8 阿吧 EP132。")
    else:
        print("\n⚠ 投資淨資產 ≤ 0 或資料不足，略過負債比率。")

    print(f"\n{'─'*64}\n  覆蓋檢驗\n{'─'*64}")
    for label, cost in (("利息（划不划算）", interest), ("月付（現金流）", paid)):
        net = attributed - cost
        cov = (attributed / cost) if cost else float("inf")
        mark = "✓ 覆蓋" if net >= 0 else "✗ 未覆蓋"
        print(f"  vs 累計{label:<16}成本 {cost:>11,.0f}  淨效益 {net:>+12,.0f}  ({cov:.2f} 倍) {mark}")
    # ── 利息門檻:這筆錢要賺多少才不白借 ──────────────────────
    total_int = interest + future_interest
    years = float(c["總期數"]) / 12
    hurdle = (total_int / invested_tracked) if invested_tracked else None
    hurdle_ann = ((1 + hurdle) ** (1 / years) - 1) if (hurdle is not None and years) else None
    int_per_year = total_int / years if years else 0
    worst = min((amt for (_y, _m, _t, amt) in detail), default=0)
    worst_ym = next((f"{y}/{m:02d}" for (y, m, _t, a) in detail if a == worst), "—")
    print(f"\n{'─'*64}\n  利息門檻：這筆錢要賺多少才不白借\n{'─'*64}")
    print(f"  全期總利息（{c['總期數']} 期）        {total_int:>12,.0f}")
    print(f"  ÷ 可對應投入 {invested_tracked:,.0f}")
    print(f"  = 門檻報酬率（{years:.0f} 年內需達成） {hurdle:>11.1%}"
          f"   ＝年化 {hurdle_ann:.2%}" if hurdle is not None else "")
    print(f"  目前累計報酬                  {cum-1:>11.2%}"
          f"   {'✓ 已超過門檻' if (hurdle is not None and cum-1 >= hurdle) else '（仍在累積中）'}")
    print(f"\n  風險對照：平均每年利息 {int_per_year:,.0f}")
    print(f"            最差單月（{worst_ym}）歸屬損益 {worst:+,.0f}"
          f"  ← 約等於 {abs(worst)/int_per_year:.1f} 年的利息" if int_per_year else "")
    print(f"\n  ⚠ 此比較偏嚴格：以**整筆貸款**的利息，對**只有進股市那部分**的獲利。")
    print(f"     未對應的 {unmatched:,.0f} 若另有報酬，並未計入。" if unmatched else "")

    print(f"\n  逐月明細（撥款月起）")
    print(f"    {'月份':<9}{'當月歸屬損益':>14}")
    for (y, m, _t, amt) in detail:
        print(f"    {y}/{m:02d}   {amt:>14,.0f}")

    # ── LINE 一行摘要（排程用；放在 --sheet 判斷之前，不寫表也會產出）──
    if args.line_file and DR:
        room = DR["room_max"]
        tail = (f"尚可借 {room/10000:,.0f} 萬" if room > 0
                else f"⚠ 已超 {-room/10000:,.1f} 萬")
        line = (f"信貸負債比率 {DR['ratio']:.1%}"
                f"（上限 {DR['max']:.0%}，{tail}）")
        with open(args.line_file, "w", encoding="utf-8") as f:
            f.write(line)
        print(f"\n[line-file] {line}")

    if not args.sheet:
        print("\n（未寫入試算表；要寫請加 --sheet）")
        return

    rows = [["資金來源追蹤｜信貸", f"結算日 {asof}"], [],
            ["撥款日", str(c["撥款日"])], ["原始金額", c["原始金額"]],
            ["年利率", c["年利率"]], ["總期數", c["總期數"]], ["月付", c["月付"]],
            ["備註", c.get("備註", "")], [],
            ["已繳期數", f"{len(done)} / {c['總期數']}"],
            ["累計已付", round(paid)], ["累計利息（真實成本）", round(interest)],
            ["累計本金（非成本）", round(principal)], ["目前餘額", round(bal)],
            ["未來剩餘利息", round(future_interest)], [],
            ["指定投入股市", invested], ["本帳戶可對應", round(invested_tracked)],
            ["未對應（推測複委託）", round(unmatched)], ["投入方式", deployed_note],
            ["加權累計報酬", cum - 1],
            ["歸屬損益", round(attributed)], []]
    if DR:
        rows += [["─ 負債比率：信貸餘額 ÷ 投資淨資產 ─", "金額", "比率"],
                 [f"主帳戶股票市值（{mv_date}）", round(mv_main)],
                 ["＋ 現金（12_設定 B6）", round(scfg["cash"])],
                 ["＋ 複委託台幣市值" + (f"　⚠ {os_note}" if os_note else ""), round(mv_os)],
                 ["＝ 投資資產", round(DR["assets"])],
                 ["− 信貸餘額", round(bal)],
                 ["＝ 投資淨資產（門檻分母）", round(DR["equity"])],
                 ["負債比率", "", DR["ratio"]],
                 [f"{DR['max']:.0%} 上限：可借上限", round(DR["cap_max"]),
                  f"{'尚可借' if DR['room_max'] > 0 else '已超出'} "
                  f"{abs(DR['room_max']):,.0f}"],
                 [f"{DR['warn']:.0%} 保守線：可借上限", round(DR["cap_warn"]),
                  f"{'尚可借' if DR['room_warn'] > 0 else '已超出'} "
                  f"{abs(DR['room_warn']):,.0f}"],
                 ["判定", "超過上限" if DR["ratio"] > DR["max"] else
                  ("超過保守線" if DR["ratio"] > DR["warn"] else "保守線內")], []]
    rows += [["─ 覆蓋檢驗 ─", "成本", "淨效益", "倍數"],
            ["vs 累計利息（划不划算）", round(interest), round(attributed - interest),
             round(attributed / interest, 2) if interest else ""],
            ["vs 累計月付（現金流）", round(paid), round(attributed - paid),
             round(attributed / paid, 2) if paid else ""], [],
            ["─ 利息門檻：這筆錢要賺多少才不白借 ─"],
            [f"全期總利息（{c['總期數']} 期）", round(total_int)],
            ["÷ 可對應投入", round(invested_tracked)],
            [f"→ 門檻報酬率（{years:.0f} 年內需達成）", hurdle if hurdle is not None else ""],
            ["　換算年化門檻", hurdle_ann if hurdle_ann is not None else ""],
            ["目前累計報酬", cum - 1],
            ["是否已超過門檻", "是" if (hurdle is not None and cum - 1 >= hurdle) else "尚未（仍在累積）"],
            ["平均每年利息", round(int_per_year)],
            [f"最差單月（{worst_ym}）歸屬損益", round(worst),
             f"約 {abs(worst)/int_per_year:.1f} 年的利息" if int_per_year else ""], [],
            ["月份", "當月歸屬損益"]]
    rows += [[f"{y}/{m:02d}", round(amt)] for (y, m, _t, amt) in detail]
    rows += [[], ["口徑說明", "歸屬損益：依撥款後的實際淨投入逐筆建批，每批各自從投入日起算逐日報酬（含配息）"],
             ["", "⚠ 假設「信貸的錢先被花掉」——實際是哪筆錢買的股票無從得知，此假設影響結果甚大："],
             ["", "　 若改假設撥款日全額一次進場，同一期間歸屬損益會從 +34 萬變成 −6 萬（未覆蓋利息）"],
             ["", "利息才是真實成本；本金攤還只是把負債轉成資產，不是費用"],
             ["", "⚠ 門檻與覆蓋檢驗皆偏嚴格：以「整筆貸款」的利息，對「只有進股市那部分」的獲利；"
                  "未對應金額若另有報酬並未計入"],
             ["", "⚠ 真正的風險不是賺不贏利息（門檻低），而是波動期的現金流——"
                  "月薪 7 萬 − 生活 4 萬 − 房貸 3.4 萬 − 信貸 6.6 萬，月現金流本為負，靠現金部位填"],
             ["", "只追蹤信貸，房貸為自住房、與投資無關"],
             ["", "負債比率門檻 30%／20% 出自 A8 阿吧 EP132（「30% 以內還算合理、保守 20%、40% 以上效果就爛」）；"
                  "要調門檻改「12_設定」B33／B34，空著就用預設"],
             ["", "⚠ 比率分子用**信貸餘額**而非投入股市金額——沒進股市的那部分仍在付利息，"
                  "且對應現金已計入分母，用投入金額會兩頭偏樂觀"],
             ["", "⚠ 借錢不改變淨資產（現金增、負債同步增），「再借」只會推高比率；"
                  "額度只能靠攤還本金或淨資產成長擴張"],
             ["", f"投入截止 {cutoff}（預設撥款月底）；之後的買進不再對應信貸，"
                  "差額推測為複委託等本系統未追蹤的去向"]]

    svc = build("sheets", "v4", credentials=creds)
    meta = svc.spreadsheets().get(spreadsheetId=u.SPREADSHEET_ID).execute()
    sid = next((s["properties"]["sheetId"] for s in meta["sheets"]
                if s["properties"]["title"] == SHEET_NAME), None)
    if not args.yes:
        print(f"\n即將{'覆寫' if sid is not None else '新建'}分頁「{SHEET_NAME}」（{len(rows)} 列）")
        if input("確認寫入？(yes/no) ").strip().lower() not in ("y", "yes"):
            print("已取消。")
            return
    if sid is None:
        svc.spreadsheets().batchUpdate(spreadsheetId=u.SPREADSHEET_ID, body={"requests": [
            {"addSheet": {"properties": {"title": SHEET_NAME}}}]}).execute()
    else:
        svc.spreadsheets().values().clear(
            spreadsheetId=u.SPREADSHEET_ID, range=f"{SHEET_NAME}!A1:Z400").execute()
    # ⚠ valueInputOption=USER_ENTERED 會把 = + - @ 開頭的「字串」當公式解析,造成 #ERROR!
    #   (實際踩過:標籤「= 門檻報酬率…」)。寫入前一律在前面補半形單引號跳脫。
    def esc_cell(v):
        return f"'{v}" if isinstance(v, str) and v[:1] in "=+-@" else v
    rows = [[esc_cell(c) for c in r] for r in rows]
    svc.spreadsheets().values().update(
        spreadsheetId=u.SPREADSHEET_ID, range=f"{SHEET_NAME}!A1",
        valueInputOption="USER_ENTERED", body={"values": rows}).execute()
    print(f"✅ 已寫入「{SHEET_NAME}」分頁（{len(rows)} 列）")


if __name__ == "__main__":
    main()
