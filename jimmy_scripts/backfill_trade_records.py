"""
股票買賣紀錄補登（2026-10-04）：把 ~/Downloads/股票買賣紀錄_補登列.xlsx 兩個分頁插回「股價試算」的「股票買賣紀錄」。

規則：
  - 每筆依日期插回原位（第一個「日期 <= 自己」的原有列上方），整表維持由新到舊；第 2~10 列空白緩衝不動
  - 只寫 A、F~Q、Z；B~E 依買／賣型態套公式（買：=TODAY()-F 與三個均價 FILTER；賣：=TODAY()-K，C~E 空），
    R~Y 若錨點列（原本的下一列）有公式則以相對位移複製，否則留空
  - 備註含【待確認】整列黃底
  - write 前先複製備份分頁「股票買賣紀錄_備份_YYYYMMDD」

用法：
  python3 backfill_trade_records.py dry-run
  python3 backfill_trade_records.py write      # 會再要求輸入 yes
  python3 backfill_trade_records.py verify     # 抽查
"""
import datetime as dt
import re
import sys
import warnings

warnings.filterwarnings("ignore")
import openpyxl

from sheets_writer import get_service, SPREADSHEET_ID, SHEET_NAME
from fix_split_avg import new_formulas

XLSX = "/Users/jimmy/Downloads/股票買賣紀錄_補登列.xlsx"
TODAY = dt.date.today()
BACKUP_NAME = f"{SHEET_NAME}_備份_{TODAY:%Y%m%d}"
BASE = dt.date(1899, 12, 30)
YELLOW = {"red": 1.0, "green": 0.949, "blue": 0.8}   # #FFF2CC，與補登檔一致


def serial_to_date(x):
    return BASE + dt.timedelta(days=x) if isinstance(x, (int, float)) and x > 0 else None


def load_new_rows():
    wb = openpyxl.load_workbook(XLSX)
    rows = []
    for ws in wb:
        for r in list(ws.iter_rows(values_only=True))[1:]:
            if not r[0]:
                continue
            is_buy = r[5] is not None
            d = (r[5] if is_buy else r[10]).date()
            rows.append({"src": ws.title, "r": list(r), "date": d, "buy": is_buy,
                         "pending": "【待確認】" in str(r[25] or "")})
    return rows


def load_existing(svc):
    v = svc.spreadsheets().values()
    rng = f"{SHEET_NAME}!A1:Z2000"
    unf = v.get(spreadsheetId=SPREADSHEET_ID, range=rng,
                valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    fml = v.get(spreadsheetId=SPREADSHEET_ID, range=rng,
                valueRenderOption="FORMULA").execute().get("values", [])
    return unf, fml


def plan(svc):
    """回傳 [(anchor_row, [new rows 由新到舊])]，anchor_row 為插入前的原列號。"""
    unf, fml = load_existing(svc)
    dated = []
    for i, r in enumerate(unf[1:], 2):
        r = r + [""] * 26
        d = serial_to_date(r[5]) or serial_to_date(r[10])
        if d:
            dated.append((i, d))
    new = load_new_rows()
    # 同日期：補登檔內的原順序保留；整體依日期新到舊（穩定排序）
    new.sort(key=lambda x: x["date"], reverse=True)
    groups = {}
    for n in new:
        anchor = next(i for i, d in dated if d <= n["date"])
        groups.setdefault(anchor, []).append(n)
    return sorted(groups.items()), unf, fml


def shift_formula(f, src_row, dst_row):
    return re.sub(r"(?<![A-Z])([A-Z]{1,2})" + str(src_row) + r"(?!\d)",
                  lambda m: m.group(1) + str(dst_row), f)


def build_row(n, row, anchor_fml):
    r = n["r"]
    a = str(r[0])
    out = [""] * 26
    out[0] = "'" + a if a.isdigit() else a
    if n["buy"]:
        out[1] = f"=TODAY()-F{row}"
        out[2:5] = new_formulas(row)   # 只算分割後（見 fix_split_avg.py）
    else:
        out[1] = f"=TODAY()-K{row}"
    for c in range(5, 17):   # F~Q
        v = r[c]
        if isinstance(v, dt.datetime):
            v = f"{v:%Y/%m/%d}"
        out[c] = "" if v is None else v
    if not n["buy"]:         # 賣出列 N、Q 與原表一致用公式
        out[13] = f"=L{row}*M{row}"
        out[16] = f"=(N{row}-O{row}-P{row})"
    for c in range(17, 25):  # R~Y：錨點列是公式才複製
        src = anchor_fml[1][c] if len(anchor_fml[1]) > c else ""
        if isinstance(src, str) and src.startswith("="):
            out[c] = shift_formula(src, anchor_fml[0], row)
    out[25] = r[25] or ""
    return out


def layout(groups):
    """計算插入後每筆新列的最終列號。"""
    result, offset = [], 0
    for anchor, items in groups:
        start = anchor + offset
        for k, n in enumerate(items):
            result.append((start + k, anchor, n))
        offset += len(items)
    return result


def dry_run(svc):
    groups, unf, fml = plan(svc)
    lay = layout(groups)
    print(f"共 {len(lay)} 列，分 {len(groups)} 個插入點；備份分頁：{BACKUP_NAME}\n")
    for anchor, items in groups:
        ex = (unf[anchor - 1] + [""] * 26)
        ed = serial_to_date(ex[5]) or serial_to_date(ex[10])
        print(f"▼ 插在原第 {anchor} 列（{ex[0]} {ed}）上方，{len(items)} 列")
    print()
    print(f"{'新列':>4} {'來源':<8} {'代號':<7} {'日期':<10} 型 {'價格':>8} {'股數':>6} {'手續費':>5} {'稅':>4} {'成本/應收':>9}  註")
    for row, anchor, n in lay:
        r = n["r"]
        if n["buy"]:
            p, q, fee, tax, amt = r[6], r[7], r[8], "", r[9]
        else:
            p, q, fee, tax, amt = r[11], r[12], r[14], r[15], r[16]
        mark = "★黃底" if n["pending"] else ""
        print(f"{row:>4} {n['src'][:6]:<8} {r[0]:<7} {n['date']} {'買' if n['buy'] else '賣'} "
              f"{p:>8} {q:>6} {fee:>5} {tax!s:>4} {amt:>9,}  {mark}")
    # 範例公式
    sample_buy = next(x for x in lay if x[2]["buy"])
    sample_sell = next(x for x in lay if not x[2]["buy"])
    for row, anchor, n in (sample_buy, sample_sell):
        b = build_row(n, row, (anchor, fml[anchor - 1] + [""] * 26))
        print(f"\n第 {row} 列 B~E：", b[1:5], " R~Y：", b[17:25])


def write(svc):
    groups, unf, fml = plan(svc)
    lay = layout(groups)
    ss = svc.spreadsheets()
    meta = ss.get(spreadsheetId=SPREADSHEET_ID).execute()
    sheets = {s["properties"]["title"]: s["properties"] for s in meta["sheets"]}
    if BACKUP_NAME in sheets:
        sys.exit(f"備份分頁「{BACKUP_NAME}」已存在，中止（避免重複插入）。")
    sid = sheets[SHEET_NAME]["sheetId"]
    old_rows = len(unf)

    if input(f"將插入 {len(lay)} 列並先建立備份「{BACKUP_NAME}」，輸入 yes 繼續：").strip() != "yes":
        sys.exit("已取消")

    # 1) 備份
    ss.batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": [{"duplicateSheet": {
        "sourceSheetId": sid, "insertSheetIndex": sheets[SHEET_NAME]["index"] + 1,
        "newSheetName": BACKUP_NAME}}]}).execute()
    print(f"✓ 已建立備份分頁 {BACKUP_NAME}")

    # 2) 插入空列（由下往上，列號不互相干擾；格式承接下方原列）
    reqs = [{"insertDimension": {"range": {"sheetId": sid, "dimension": "ROWS",
             "startIndex": anchor - 1, "endIndex": anchor - 1 + len(items)},
             "inheritFromBefore": False}} for anchor, items in reversed(groups)]
    ss.batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": reqs}).execute()
    print(f"✓ 已插入 {len(lay)} 列")

    # 3) 寫值與公式；只有【待確認】列改底色，其餘沿用插入時承接的格式
    data, fmt = [], []
    for row, anchor, n in lay:
        data.append({"range": f"{SHEET_NAME}!A{row}:Z{row}",
                     "values": [build_row(n, row, (anchor, fml[anchor - 1] + [""] * 26))]})
        if not n["pending"]:
            continue
        fmt.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": row - 1, "endRowIndex": row},
                    "cell": {"userEnteredFormat": {"backgroundColor": YELLOW}},
                    "fields": "userEnteredFormat.backgroundColor"}})
    ss.values().batchUpdate(spreadsheetId=SPREADSHEET_ID, body={
        "valueInputOption": "USER_ENTERED", "data": data}).execute()
    ss.batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": fmt}).execute()
    print(f"✓ 已寫入 {len(data)} 列，黃底 {sum(n['pending'] for _, _, n in lay)} 列")

    # 4) 原有列未被改動檢查（以計算值比對，排除 =TODAY() 相依的 B~E）
    new_unf, _ = load_existing(svc)
    inserted = {row for row, _, _ in lay}
    remain = [r for i, r in enumerate(new_unf, 1) if i not in inserted]
    norm = lambda r: [(r + [""] * 26)[c] for c in [0] + list(range(5, 26))]
    diffs = [i for i, (a, b) in enumerate(zip(unf, remain), 1) if norm(a) != norm(b)]
    print(f"✓ 原有 {old_rows} 列比對：{'全部一致' if not diffs else f'有差異 {diffs[:20]}'}")


def verify(svc, rows):
    v = svc.spreadsheets().values()
    for row in rows:
        f = v.get(spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}!A{row}:Z{row}",
                  valueRenderOption="FORMULA").execute()["values"][0]
        c = v.get(spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}!A{row}:Z{row}",
                  valueRenderOption="FORMATTED_VALUE").execute()["values"][0]
        print(f"--- 第 {row} 列")
        for i, (a, b) in enumerate(zip(f, c)):
            if a != "" and i < 25:
                print(f"  {chr(65 + i)}: {a!s:<70} → {b}")


if __name__ == "__main__":
    svc = get_service()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dry-run"
    if cmd == "dry-run":
        dry_run(svc)
    elif cmd == "write":
        write(svc)
    elif cmd == "verify":
        verify(svc, [int(x) for x in sys.argv[2:]])
