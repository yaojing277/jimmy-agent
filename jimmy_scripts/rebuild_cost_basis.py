#!/usr/bin/env python3
"""依「股票買賣紀錄」重算持股的加權平均成本,並校正 Jimmy_YYMMDD 快照的 B 欄「買進價」。

為什麼需要這支(2026-10-20 建立):
  快照的 B 欄長期以「滾動均價」維護——記帳時用
      新均價 = (舊均價 × 舊股數 + 應收付) ÷ 新股數
  算出後**只存 2 位小數**。股數動輒數萬到十萬股,每次四捨五入可能差數百元,
  多次累積就成千元級誤差。實測 00631L 在 103,000 股時存 30.02、真實值 30.0315,
  差 0.0115 × 103,000 = 1,182 元;00919 差 3,123 元。
  券商 App 的成本才是對的——以買賣紀錄重建的結果與 App 只差 3.96 元
  (賣出時以加權平均扣成本的進位差)。

口徑:
  * 成本 = Σ 買進應收付(J 欄,含手續費);賣出時以**加權平均**扣除
    (與 Jimmy 2026-09 的決定一致,非 FIFO)。
  * 分割前的股數依「股票分割」分頁換算成分割後(成本不變)。

⚠ 安全護欄:**只校正「重建股數 == 快照股數」的標的**。
  買賣紀錄只涵蓋 2025-06 之後,早於此的持股(0050/0056/00878/台積電/聯發科/
  國巨/凱基金/6515/台達電…)重建出來的股數會少一截,成本自然也少——
  那不是誤差而是資料不全,**硬套會把成本改到大錯**(如 00878 會從 204 萬掉到 100 萬)。
  對不上的一律跳過並列出原因。

用法:
  python3 rebuild_cost_basis.py check              # 只比對,不寫入(預設最新快照)
  python3 rebuild_cost_basis.py check --sheet Jimmy_261020
  python3 rebuild_cost_basis.py apply --yes        # 寫回 B 欄(僅對得上的標的)
"""
import argparse
import datetime
import re
import sys

from sheets_writer import get_service, SPREADSHEET_ID, SHEET_NAME
from update_stock_price import ALIAS

# 同一檔股票在「股票買賣紀錄」裡可能有多個名稱(代號/中文/舊名),快照又用第三種寫法。
# 不統一就會把同一檔拆成兩堆、算出「股數不符」而誤判為紀錄不完整
# (實測:台達電 50 + 2308 70 = 快照的 120;國巨 150 + 2327 650 = 快照的 800)。
# ALIAS 來自 update_stock_price(名稱→代號),這裡補它沒有的幾個。
EXTRA_ALIAS = {"台積電": "2330", "穎崴": "6515", "開發金": "2883", "新光金": "2887"}
_A = {**ALIAS, **EXTRA_ALIAS}

# ⚠ 「_刪」字尾是人工標記的**作廢重複列**,不可計入
# (實測:00878 排除後剛好等於快照的 100,000 股,計入就多 2,000 股)。
VOID_SUFFIX = "_刪"


def canon(name):
    """把買賣紀錄/快照的寫法統一成同一個鍵。"""
    n = str(name).strip()
    return _A.get(n, n)

SPLIT_SHEET = "股票分割"
EPOCH = datetime.date(1899, 12, 30)
TOL = 1.0                      # 成本差小於此金額視為相符(純顯示用)


def _d(serial):
    return EPOCH + datetime.timedelta(days=int(serial))


def latest_snapshot(svc):
    meta = svc.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    names = [s['properties']['title'] for s in meta['sheets']
             if re.fullmatch(r'Jimmy_\d{6}', s['properties']['title'])]
    if not names:
        sys.exit("找不到任何 Jimmy_YYMMDD 快照分頁。")
    return sorted(names)[-1]


def read_splits(svc):
    """{代號: (恢復交易日, 倍數)};只認『1拆N』。"""
    rows = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{SPLIT_SHEET}!A2:C50",
        valueRenderOption="UNFORMATTED_VALUE").execute().get('values', [])
    out = {}
    for r in rows:
        if len(r) >= 3 and r[0]:
            m = re.search(r'1拆(\d+)', str(r[2]))
            if m:
                out[str(r[0]).strip()] = (_d(r[1]), int(m.group(1)))
    return out


BAD = []          # 日期欄空白而無法採計的列(代號, 原因)


def read_events(svc):
    """{代號: [(日期, 'B'/'S', 股數, 金額), ...]}(未排序)。"""
    rows = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}!A2:Q1000",
        valueRenderOption="UNFORMATTED_VALUE").execute().get('values', [])
    ev = {}
    for r in rows:
        r = list(r) + [""] * (17 - len(r))
        raw = str(r[0]).strip()
        if not raw or VOID_SUFFIX in raw:          # 空白列與作廢列直接略過
            continue
        code = canon(raw)
        # ⚠ 少數列只填了股數、日期欄空白(或反之)。沒有日期就無法排序,
        #   硬轉會 ValueError;這種列一律跳過並記錄,不默默當成 0 吃掉。
        if r[7] not in ("", None) and isinstance(r[5], (int, float)):
            ev.setdefault(code, []).append((_d(r[5]), "B", float(r[7]), float(r[9] or 0)))
        elif r[7] not in ("", None):
            BAD.append((code, "買進列缺買進日"))
            ev.setdefault(code, []).append((datetime.date.min, "B", float(r[7]), float(r[9] or 0)))
        if r[12] not in ("", None) and isinstance(r[10], (int, float)):
            ev.setdefault(code, []).append((_d(r[10]), "S", float(r[12]), float(r[16] or 0)))
        elif r[12] not in ("", None):
            BAD.append((code, "賣出列缺賣出日"))
            ev.setdefault(code, []).append((datetime.date.min, "S", float(r[12]), float(r[16] or 0)))
    return ev


def rebuild(events, split):
    """時序走一遍,回 (股數, 成本)。分割前股數換算成分割後;賣出以加權平均扣成本。"""
    sd, sr = split if split else (None, 1)
    sh = cost = 0.0
    for d, kind, qty, amt in sorted(events, key=lambda e: e[0]):
        q = qty * sr if (sd and d < sd) else qty
        if kind == "B":
            sh += q
            cost += amt
        else:
            avg = cost / sh if sh else 0.0
            cost -= avg * q
            sh -= q
    return sh, cost


def analyse(svc, sheet):
    splits, events = read_splits(svc), read_events(svc)
    snap = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{sheet}!A2:C60",
        valueRenderOption="UNFORMATTED_VALUE").execute().get('values', [])
    fixes, skips = [], []
    for i, row in enumerate(snap, start=2):
        row = list(row) + [""] * (3 - len(row))
        disp = str(row[0]).strip()
        code = canon(disp)
        if not disp or row[1] in ("", None) or row[2] in ("", None):
            continue
        avg_s, sh_s = float(row[1]), float(row[2])
        if code not in events:
            skips.append((disp, "買賣紀錄查無此標的"))
            continue
        has_sell = any(k == "S" for _, k, _, _ in events[code])
        if any(c == code for c, _ in BAD) and (has_sell or code in splits):
            skips.append((disp, "有列的日期欄空白，且該檔有賣出或分割，順序會影響結果"))
            continue
        sh_r, cost_r = rebuild(events[code], splits.get(code))
        if abs(sh_r - sh_s) > 0.5:
            skips.append((disp, f"股數不符(重建 {sh_r:,.0f} vs 快照 {sh_s:,.0f})，紀錄可能不完整"))
            continue
        if abs(avg_s * sh_s - cost_r) > max(1000.0, 0.05 * avg_s * sh_s):
            skips.append((disp, f"成本差異過大({avg_s*sh_s - cost_r:+,.0f})，"
                                "可能是成本口徑不同(如員工認股以到手收盤價計入)，需人工確認"))
            continue
        fixes.append(dict(row=i, code=disp, sh=sh_s, old=avg_s, new=cost_r / sh_s,
                          old_cost=avg_s * sh_s, new_cost=cost_r))
    return fixes, skips


def main():
    ap = argparse.ArgumentParser(description="依買賣紀錄重算快照 B 欄買進價")
    ap.add_argument("mode", choices=["check", "apply"])
    ap.add_argument("--sheet", help="快照分頁名(預設最新)")
    ap.add_argument("--yes", action="store_true", help="apply 時略過確認")
    args = ap.parse_args()

    svc = get_service()
    sheet = args.sheet or latest_snapshot(svc)
    print(f"[快照] {sheet}\n")
    fixes, skips = analyse(svc, sheet)

    print(f"  {'代號':<9}{'快照成本':>13}{'重建成本':>13}{'差額':>10}   {'舊均價':>11} → {'新均價':<11}")
    todo = []
    for f in fixes:
        diff = f['old_cost'] - f['new_cost']
        mark = "✅" if abs(diff) < TOL else "⚠"
        if abs(diff) >= TOL:
            todo.append(f)
        print(f"  {f['code']:<9}{f['old_cost']:>13,.0f}{f['new_cost']:>13,.0f}"
              f"{diff:>10,.0f}   {f['old']:>11.5f} → {f['new']:<11.5f} {mark}")
    if skips:
        print("\n  跳過(不校正):")
        for code, why in skips:
            print(f"    {code:<9}{why}")
    net = sum(f['old_cost'] - f['new_cost'] for f in fixes)
    print(f"\n  可校正 {len(fixes)} 檔，其中需異動 {len(todo)} 檔；累計差額 {net:,.0f}")

    if args.mode == "check" or not todo:
        if args.mode == "apply":
            print("  無需異動。")
        return
    if not args.yes:
        if input("\n確認寫回 B 欄?(yes/no): ").strip().lower() != "yes":
            print("已取消。")
            return
    data = [{"range": f"{sheet}!B{f['row']}", "values": [[round(f['new'], 6)]]} for f in todo]
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data}).execute()
    print(f"\n✅ 已更新 {len(todo)} 檔的買進價。請接著跑「更新所有分頁」讓 Wealth OS 同步。")


if __name__ == "__main__":
    main()
