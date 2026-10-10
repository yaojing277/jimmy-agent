"""
「16_資產歷史」補登／更正指定日期的列，然後跑全分頁刷新（重建 02_每日漲跌）並上傳雲端 xlsm。

為什麼要另寫：update_wealth_os.py 只會寫「今天」那一列；補過去缺漏的交易日、或更正舊列，
要能指定日期與數字。寫入邏輯完全沿用 update_wealth_os 的 surgical_write／_update_history
（同日覆寫、新日期依日期插入正確位置）與 full_refresh，不另造一套。

寫完指定列後，一併依買賣紀錄重寫 I 欄「淨投入」（每日同步也會做同一件事）。

數字口徑（與每日同步一致）：總市值＝Σ 收盤持股 × 當日官方收盤（停牌取停牌前最後收盤）、
成本＝Σ 股數 × 快照 B 欄買進價。房屋／負債／現金：既有列沿用該列原值（現金由 H−B−F+G 回推），
新增列沿用前一個較早日期的列。

用法：
  python3 patch_asset_history.py rows.json            # 預覽（下載、套用、驗證，不上傳）
  python3 patch_asset_history.py rows.json --yes      # 確認後上傳
rows.json：{"2026/07/03": [總市值, 成本], ...}
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore")
import openpyxl
import update_wealth_os as w


def existing_rows(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    out = {}
    for r in wb[w.HIST_TAB].iter_rows(min_row=2, values_only=True):
        if r[0] and isinstance(r[1], (int, float)):
            out[str(r[0]).replace("-", "/")[:10]] = r
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rows_json")
    ap.add_argument("--yes", action="store_true", help="驗證通過後上傳雲端")
    a = ap.parse_args()
    patch = json.load(open(a.rows_json))

    creds = w.get_creds()
    workdir = tempfile.mkdtemp(prefix="hist_patch_")
    drive, meta, path, backup = w.download_xlsm(creds, workdir)
    print(f"已下載 {meta['name']}（雲端時間 {meta['modifiedTime']}）；備份 {backup}")
    before = existing_rows(path)
    latest = max(before)

    cur = path
    for i, (date_str, (mv, cost)) in enumerate(sorted(patch.items())):
        if date_str in before:
            r = before[date_str]
        else:
            older = [d for d in before if d < date_str]
            r = before[max(older)] if older else before[min(before)]
        house, debt, net, mv0 = r[5], r[6], r[7], r[1]
        cash = net - mv0 - house + debt
        metrics = {"mv": mv, "cost": cost, "house": house, "debt": debt, "cash": cash}
        out = os.path.join(workdir, f"step{i}.xlsm")
        _, _, note, _ = w.surgical_write(cur, out, {}, {}, hist=(date_str, metrics))
        old = f"{r[1]:,.0f}／{r[2]:,.1f}" if date_str in before else "（新增）"
        print(f"[歷史] {date_str}：{old} → {mv:,.0f}／{cost:,.1f}；{note}")
        cur = out

    # 淨投入欄(I)＋配息欄(J):依買賣紀錄／除權息資料整欄重寫,
    # 02_每日漲跌 的單日損益＝市值變化−淨投入+配息(與每日同步同一套)
    recs = w.read_trade_records(creds)
    flows = w.compute_daily_flows(recs)
    divs = w.compute_daily_dividends(creds, recs)
    out = os.path.join(workdir, "flows.xlsm")
    w.surgical_write(cur, out, {}, {}, flows=flows, divs=divs)
    cur = out
    print(f"[淨投入] 依買賣紀錄寫入 {len(flows)} 個交易日的淨投入"
          + (f";[配息] 寫入 {len(divs)} 個除息日" if divs else ";[配息] 跳過"))

    final = os.path.join(workdir, "final.xlsm")
    fchanged, fsum = w.full_refresh(cur, final, latest)
    print(f"[full] 以 {latest} 重建全分頁快取：{len(fchanged)} 個元件；總市值 {fsum['F27']:,.0f}")

    # 驗證：每個指定日期都在、數字正確、日期由新到舊、02_每日漲跌 列數跟著增加
    after = existing_rows(final)
    for d, (mv, cost) in patch.items():
        r = after.get(d)
        assert r and abs(r[1] - mv) < 1 and abs(r[2] - cost) < 1, f"{d} 寫入後不符：{r}"
    wb = openpyxl.load_workbook(final, data_only=True)
    dates = [str(r[0]) for r in wb[w.HIST_TAB].iter_rows(min_row=2, values_only=True) if r[0]]
    assert dates == sorted(dates, reverse=True), "16_資產歷史 日期順序錯亂"
    daily = [r[0] for r in wb[w.DAILY_TAB].iter_rows(values_only=True)
             if r[0] and str(r[0])[:4].isdigit() and "/" in str(r[0]) and len(str(r[0])) == 10]
    print(f"[驗證] 16_資產歷史 {len(before)} → {len(after)} 列，日期由新到舊；02_每日漲跌 明細 {len(daily)} 天")
    shutil.copy2(final, os.path.join(os.path.dirname(os.path.abspath(a.rows_json)), "patched_preview.xlsm"))

    if not a.yes:
        print("（預覽：未上傳。確認無誤後加 --yes）")
        return
    info = w.upload_xlsm(drive, final)
    print(f"✅ 已上傳 {info['name']}（版本 {info.get('version')}，雲端時間 {info['modifiedTime']}）")


if __name__ == "__main__":
    main()
