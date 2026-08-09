#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sheet_value_guard.py — 改公式的安全網:改前拍快照,改後比對計算值。

用途:重構/批次改公式時,確保「只動寫法、不動結果」。
  1. snapshot:把某範圍的『計算值』存成快照檔。
  2. (你動手改公式)
  3. diff:再讀一次,逐格比對,列出任何被改動的值(含變成 #DIV/0! 之類)。

資料源:Google Sheets API(沿用同資料夾 token.json 的 OAuth)。

──────────────────────────────────────────────────────────────
用法
  # 改前拍快照(預設「股價試算」;名稱預設用 sheet+range)
  python3 sheet_value_guard.py snapshot --sheet '股票買賣紀錄' --range C1:E200

  # 改完比對
  python3 sheet_value_guard.py diff --sheet '股票買賣紀錄' --range C1:E200

  # 指定快照名稱 / 另一個試算表
  python3 sheet_value_guard.py snapshot --sheet 工作表1 --range A1:Z50 --name beforefix
  python3 sheet_value_guard.py diff     --sheet 工作表1 --range A1:Z50 --name beforefix
  python3 sheet_value_guard.py snapshot --ssid <試算表ID> --sheet ... --range ...
──────────────────────────────────────────────────────────────
"""
import os
import sys
import json
import argparse
import datetime

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
except ImportError:
    sys.exit("缺少套件,請先執行:\n  pip3 install --upgrade "
             "google-api-python-client google-auth-httplib2 google-auth-oauthlib")

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "token.json")
SNAP_DIR = os.path.join(HERE, ".value_snapshots")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
DEFAULT_SSID = "1UiqAHT2GUhKiviSz5NaLNclttlLVP3ujQMxJUn7Jyr8"   # 股價試算

def get_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE) \
        if os.path.exists(TOKEN_FILE) else None
    if not creds:
        sys.exit(f"找不到 {TOKEN_FILE}。")
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("sheets", "v4", credentials=creds)

def col_letter(idx):
    s, n = "", idx + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s

def letter_to_idx(letter):
    n = 0
    for ch in letter.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1

def parse_range_start(rng):
    """'C1:E200' -> (col_idx, row) 左上角;回傳 0-based 欄索引與 1-based 列號。"""
    first = rng.split("!")[-1].split(":")[0]
    col = "".join(c for c in first if c.isalpha())
    row = "".join(c for c in first if c.isdigit())
    return letter_to_idx(col) if col else 0, int(row) if row else 1

def snap_path(name):
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return os.path.join(SNAP_DIR, safe + ".json")

def read_values(service, ssid, sheet, rng):
    return service.spreadsheets().values().get(
        spreadsheetId=ssid, range=f"{sheet}!{rng}",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])

def cmd_snapshot(service, args):
    vals = read_values(service, args.ssid, args.sheet, args.range)
    os.makedirs(SNAP_DIR, exist_ok=True)
    name = args.name or f"{args.sheet}_{args.range}".replace(":", "-")
    payload = {"ssid": args.ssid, "sheet": args.sheet, "range": args.range,
               "ts": datetime.datetime.now().isoformat(timespec="seconds"),
               "values": vals}
    with open(snap_path(name), "w") as f:
        json.dump(payload, f, ensure_ascii=False)
    ncells = sum(len(r) for r in vals)
    print(f"已拍快照「{name}」:{args.sheet}!{args.range}  "
          f"{len(vals)} 列 / {ncells} 格(有值)\n→ 改完用 diff 同名比對。")

def cmd_diff(service, args):
    name = args.name or f"{args.sheet}_{args.range}".replace(":", "-")
    p = snap_path(name)
    if not os.path.exists(p):
        sys.exit(f"找不到快照「{name}」({p}),請先 snapshot。")
    snap = json.load(open(p))
    base_col, base_row = parse_range_start(snap["range"])
    before = snap["values"]
    after = read_values(service, snap["ssid"], snap["sheet"], snap["range"])
    def cell(grid, i, j):
        return grid[i][j] if i < len(grid) and j < len(grid[i]) else ""
    rows = max(len(before), len(after))
    diffs = []
    for i in range(rows):
        cols = max(len(before[i]) if i < len(before) else 0,
                   len(after[i]) if i < len(after) else 0)
        for j in range(cols):
            b, a = cell(before, i, j), cell(after, i, j)
            if isinstance(b, (int, float)) and isinstance(a, (int, float)):
                if abs(b - a) > 1e-9:
                    diffs.append((i, j, b, a))
            elif str(b) != str(a):
                diffs.append((i, j, b, a))
    addr = lambda i, j: f"{col_letter(base_col + j)}{base_row + i}"
    print(f"比對快照「{name}」({snap['ts']}) vs 現在:{snap['sheet']}!{snap['range']}")
    if not diffs:
        print("→ 計算值完全一致 ✅ 沒有任何值被改動。")
        return
    print(f"→ ⚠ 有 {len(diffs)} 格數值不同:")
    for i, j, b, a in diffs[:200]:
        print(f"    {addr(i, j)}: 前 {b!r}  →  後 {a!r}")
    if len(diffs) > 200:
        print(f"    ...(其餘 {len(diffs) - 200} 格略)")

def main():
    ap = argparse.ArgumentParser(description="改公式安全網:快照 / 比對計算值")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, helptext in (("snapshot", "拍快照"), ("diff", "比對")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--sheet", required=True, help="分頁名稱")
        p.add_argument("--range", required=True, help="範圍,例 C1:E200")
        p.add_argument("--name", help="快照名稱(預設 sheet_range)")
        p.add_argument("--ssid", default=DEFAULT_SSID, help="試算表 ID(預設股價試算)")
    args = ap.parse_args()
    service = get_service()
    (cmd_snapshot if args.cmd == "snapshot" else cmd_diff)(service, args)

if __name__ == "__main__":
    main()
