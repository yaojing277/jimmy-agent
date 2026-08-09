#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_close_price.py — 依 A 欄代號抓收盤價,寫回指定列的價格欄(預設 K)。

適用:「股價試算」→ 每月 Jimmy_YYMMDD 月結快照分頁。
A 欄放股票代號/名稱,K 欄放某日收盤價(K1 是日期序號,例如 6/26)。
新增了幾列股票後,用這支一次把那幾列的 K 欄收盤價補上。

分頁預設 --sheet latest:自動挑 Jimmy_YYMMDD 結尾日期最新的分頁,
換月不必改參數;要指定別張再用 --sheet Jimmy_YYMMDD 覆寫。

資料來源:TWSE(上市)/ TPEx(上櫃)官方 API(免金鑰),不使用 Yahoo。
美股無 TWSE/TPEx 資料,一律略過。
TWSE 上市改抓:/rwd/ 失敗或回假錯誤時自動 fallback /exchangeReport/(見 twse_hist.py)。

授權:走 token.json(OAuth)。token 約 7 天會過期失效,失效時執行
  python3 reauth_sheets.py
依畫面網址用 yaojing277@gmail.com 重新授權即可。

──────────────────────────────────────────────────────────────
用法範例(不帶 --sheet 即自動用最新的 Jimmy_YYMMDD 分頁)
  # 看分頁現況(A 欄與目標欄,標出哪幾列缺值)
  python3 update_close_price.py inspect

  # 指定某張分頁(覆寫自動挑最新)
  python3 update_close_price.py --sheet Jimmy_260626 inspect

  # 補第 23、24 列(日期沿用 K1 標頭,先預覽再確認)
  python3 update_close_price.py fill --rows 23,24

  # 補第 23~26 列;免確認直接寫
  python3 update_close_price.py fill --rows 23-26 --yes

  # 自動找出「A 有值、K 空白」的列全部補上
  python3 update_close_price.py fill --auto

  # 指定其他價格欄/日期(例如新開了 L 欄 = 6/13)
  python3 update_close_price.py fill --rows 23 --col L --date 2026/6/13

  # 只試算不寫入
  python3 update_close_price.py fill --rows 23 --dry-run

  # 【補整列】新增一列只填好 A/B/C 後,一鍵補齊其餘所有欄位:
  #   各日期欄收盤 + D~N/S~W 公式(現今價自動取最後日期欄) + O/P/Q/R 配息
  python3 update_close_price.py fill-row --rows 25
  python3 update_close_price.py fill-row --rows 25 --code 2330   # 代號覆寫
  python3 update_close_price.py fill-row --rows 25,26 --yes      # 多列、免確認
  python3 update_close_price.py fill-row --rows 25 --freq 4      # 指定一年配 4 次

  # 【健檢】比對公式樣式 / 日期價缺值 / 錯誤值 / K 價對照 TWSE
  python3 update_close_price.py audit
  python3 update_close_price.py audit --no-price                 # 跳過抓價,只查公式/缺值
──────────────────────────────────────────────────────────────

新代號對不上時:把對照加到下方 ALIAS(名稱/英文 -> 台股代號)即可。
"""
import os
import sys
import re
import json
import argparse
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twse_hist          # 收盤價/配息查詢:TWSE/TPEx 官方,不使用 Yahoo

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
except ImportError:
    sys.exit("缺少套件,請先執行:\n  pip3 install --upgrade "
             "google-api-python-client google-auth-httplib2 google-auth-oauthlib")

# ========================= 基本設定 =========================
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "token.json")
CREDENTIALS_FILE = os.path.join(HERE, "credentials.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SPREADSHEET_ID = "1UiqAHT2GUhKiviSz5NaLNclttlLVP3ujQMxJUn7Jyr8"
SHEET_NAME = "Jimmy_260615"   # 預設分頁(可用 --sheet 覆寫)
DEFAULT_COL = "K"      # 預設價格欄
HEADER_ROW = 1         # 標頭列(該欄標頭=日期)
DATA_START_ROW = 2     # 資料起始列

# A 欄非純代號(中文名/英文)時的對照:顯示值 -> 台股代號
ALIAS = {
    "聯電":   "2303",
    "台達電": "2308",
    "鴻海":   "2317",
    "國巨":   "2327",
    "TSMC":  "2330",   # 台積電(台股本國)
    "聯發科": "2454",
    "凱基金": "2883",
    "台新金": "2887",
    "台新新光金": "2887",   # 台新金併新光金後更名(代號仍 2887)
    "中信金": "2891",
    "WW":    "6515",   # 穎崴(台股;美股 WW 無 TWSE 資料)
    "禾伸堂": "3026",
    "緯創":   "3231",
}

# ========================= 認證 =========================
def get_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif os.path.exists(CREDENTIALS_FILE):
            from google_auth_oauthlib.flow import InstalledAppFlow
            creds = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES).run_local_server(port=0)
        else:
            sys.exit(f"找不到有效憑證({TOKEN_FILE} / {CREDENTIALS_FILE})。")
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("sheets", "v4", credentials=creds)


_SHEET_RE = re.compile(r"^Jimmy_(\d{6})$")   # Jimmy_YYMMDD


def resolve_latest_sheet(service):
    """列出所有分頁,挑 Jimmy_YYMMDD 結尾日期最新的那張,回傳分頁名。
    找不到任何 Jimmy_YYMMDD 時報錯結束。"""
    meta = service.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID,
        fields="sheets.properties.title").execute()
    cands = []
    for s in meta.get("sheets", []):
        title = s["properties"]["title"]
        m = _SHEET_RE.match(title)
        if m:
            cands.append((m.group(1), title))   # 字串 YYMMDD 可直接比大小
    if not cands:
        sys.exit("找不到任何 Jimmy_YYMMDD 分頁。")
    cands.sort()
    return cands[-1][1]

# ========================= 工具 =========================
def serial_to_date(serial):
    """Google Sheets 日期序號 -> date(基準 1899-12-30)。"""
    try:
        return datetime.date(1899, 12, 30) + datetime.timedelta(days=int(float(serial)))
    except (ValueError, TypeError):
        return None

def parse_date(s):
    s = str(s).strip().replace("-", "/")
    for fmt in ("%Y/%m/%d", "%Y/%m/%d "):
        try:
            return datetime.datetime.strptime(s.strip(), fmt.strip()).date()
        except ValueError:
            pass
    return None

def parse_rows(spec):
    """'23,24' 或 '23-26' 或混合 -> 排序後不重複的列號 list。"""
    out = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(out)

def resolve_symbol(a_value):
    """A 欄值 -> 台股代號(單一 list);非台股(美股等)回 []。"""
    v = str(a_value).strip()
    code = twse_hist.norm_code(ALIAS.get(v, v))   # 先套名稱對照,再正規化代號
    return [code] if code else []

def fetch_close(symbol, target_date):
    """回傳 (收盤價, 'TWD') 或 (None, 訊息)。資料來源 TWSE/TPEx 官方。"""
    return twse_hist.close_on(symbol, target_date)

def col_header_date(service, col):
    rng = f"{SHEET_NAME}!{col}{HEADER_ROW}"
    v = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=rng,
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [[None]])
    return v[0][0] if v and v[0] else None

def read_col(service, col, top=80):
    """讀 A 欄與目標欄,回傳 {row: (a_value, target_value)}。"""
    rng = f"{SHEET_NAME}!A{DATA_START_ROW}:{col}{top}"
    rows = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=rng,
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    col_idx = ord(col.upper()) - ord("A")
    out = {}
    for i, row in enumerate(rows, start=DATA_START_ROW):
        a = row[0] if len(row) > 0 else ""
        t = row[col_idx] if len(row) > col_idx else ""
        out[i] = (a, t)
    return out

# ========================= 補整列:版面/抓取輔助 =========================
def col_letter(idx):
    """0-based 欄索引 -> A1 欄字母。"""
    s, n = "", idx + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s

def letter_to_idx(letter):
    """A1 欄字母 -> 0-based 欄索引(支援多字母)。"""
    n = 0
    for ch in str(letter).strip().upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1

def expected_formulas(C, LAST, r):
    """某列應有的公式(以標頭定位的欄字母 + 最後日期欄 LAST 組出)。
    fill-row 寫入與 audit 比對共用同一份,避免兩邊走鐘。
    優雅降級:目標欄或所依賴欄位被刪(不在 C)時,該公式自動略過。"""
    specs = {
        "cost":       (("buy", "shares"),         lambda: f"={C['buy']}{r}*{C['shares']}{r}"),
        "mktval":     (("shares",),               lambda: f"={C['shares']}{r}*{LAST}{r}"),
        "pl":         (("mktval", "cost"),        lambda: f"={C['mktval']}{r}-{C['cost']}{r}"),
        "plpct":      (("pl", "cost"),            lambda: f"=({C['pl']}{r}/{C['cost']}{r})*100"),
        "boughtcost": (("boughtqty",),            lambda: f"={LAST}{r}*{C['boughtqty']}{r}"),
        "avg":        (("cost", "boughtcost", "shares", "boughtqty"),
                       lambda: f"=({C['cost']}{r}+{C['boughtcost']}{r})/({C['shares']}{r}+{C['boughtqty']}{r})"),
        "divincome":  (("shares", "div", "freq"), lambda: f"={C['shares']}{r}*{C['div']}{r}*{C['freq']}{r}"),
        "cur_avg":    (("avg",),                  lambda: f"={LAST}{r}-{C['avg']}{r}"),
        "cur_orig":   (("buy",),                  lambda: f"={LAST}{r}-{C['buy']}{r}"),
        "shrink":     (("cur_avg", "cur_orig"),   lambda: f"={C['cur_avg']}{r}-{C['cur_orig']}{r}"),
        "yield":      (("divincome", "cost"),     lambda: f"=({C['divincome']}{r}/{C['cost']}{r})*100"),
        "yield2":     (("yield", "freq"),         lambda: f"={C['yield']}{r}*{C['freq']}{r}"),
    }
    out = {}
    for key, (deps, fn) in specs.items():
        if key in C and all(d in C for d in deps):
            out[C[key]] = fn()
    return out

# 以「標頭關鍵字」定位欄位,對欄位位移(未來插入日期欄)也不會錯。
HEADER_KEYS = {
    "buy": "買進價", "shares": "持有股數", "cost": "持有成本",
    "mktval": "現今市值", "pl": "賺賠", "plpct": "現賠",
    "boughtqty": "購入數", "boughtcost": "購入成本", "avg": "平均股價",
    "freq": "配息數", "exmonth": "除息日",
    "div": ("平均股息", "最近股利"),          # 新標頭「平均股息」/ 舊「最近股利」
    "divincome": ("股息所得", "最近股利所得"),  # 新標頭「股息所得」/ 舊「最近股利所得」
    "cur_avg": "現今價-平均",
    "cur_orig": ("現今價-原持有", "現今價-成本價"), "shrink": "縮短差價", "yield": "殖利率",
    "yield2": "換算殖利率",
}

def get_layout(service):
    """讀標頭列 -> (cols, date_cols, last_date_col)。
    cols: HEADER_KEYS 的 key -> 欄字母;date_cols: [(欄字母, date)](日期序號欄)。"""
    row = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}!1:1",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [[]])
    hdr = row[0] if row else []
    cols, date_cols = {"stock": "A"}, []
    for j, v in enumerate(hdr):
        letter = col_letter(j)
        if isinstance(v, (int, float)) and 40000 < v < 60000:
            date_cols.append((letter, serial_to_date(v)))
            continue
        text = str(v).strip()
        for key, kw in HEADER_KEYS.items():
            kws = kw if isinstance(kw, tuple) else (kw,)
            if any(k in text for k in kws):
                cols.setdefault(key, letter)
    last_date_col = date_cols[-1][0] if date_cols else DEFAULT_COL
    return cols, date_cols, last_date_col

def detect_freq(divs):
    """從配息間隔推一年配幾次:無=0、單筆=1、月配=12、季配=4、半年=2、年配=1。"""
    if not divs:
        return 0
    if len(divs) == 1:
        return 1
    gaps = [(divs[i][0] - divs[i - 1][0]).days for i in range(1, len(divs))]
    recent = gaps[-6:]
    med = sorted(recent)[len(recent) // 2]
    if med <= 45:
        return 12
    if med <= 135:
        return 4
    if med <= 270:
        return 2
    return 1

# ========================= 指令:fill-row(補整列) =========================
def cmd_fill_row(service, args):
    rows = parse_rows(args.rows)
    if args.code and len(rows) != 1:
        sys.exit("--code 只能搭配單一列使用(多列請各自從 A 欄解析)。")
    cols, date_cols, LAST = get_layout(service)
    if not date_cols:
        sys.exit("找不到任何日期欄,無法補整列。")
    # 必要欄位(缺就無法補);配息/殖利率為選用欄,刪掉時優雅降級只跳過相關欄
    REQUIRED = ["buy", "shares", "cost", "mktval", "pl", "plpct",
                "boughtqty", "boughtcost", "avg", "cur_avg", "cur_orig", "shrink"]
    OPTIONAL = ["freq", "exmonth", "div", "divincome", "yield", "yield2"]
    missing = [k for k in REQUIRED if k not in cols]
    if missing:
        sys.exit(f"標頭找不到必要欄位:{missing},請確認分頁標頭名稱。")
    C = cols  # 簡稱
    skipped_opt = [k for k in OPTIONAL if k not in cols]
    if skipped_opt:
        print(f"提醒:找不到選用欄位 {skipped_opt},該分頁已刪除,相關配息/殖利率欄將略過不寫。\n")

    lo, hi = min(rows), max(rows)
    end_col = col_letter(max(letter_to_idx(x) for x in
                             list(C.values()) + [d[0] for d in date_cols]))
    grid = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A{lo}:{end_col}{hi}",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    def cell(r, letter):
        i, j = r - lo, ord(letter) - ord("A")
        if 0 <= i < len(grid) and j < len(grid[i]):
            return grid[i][j]
        return ""

    print(f"分頁「{SHEET_NAME}」補整列;現今價欄=最後日期欄 {LAST}"
          f"({dict(date_cols).get(LAST)})\n")
    plans = []   # (row, a, sym, cur, prices{letter:val}, freq, exmonth, q, expR, note)
    for r in rows:
        a_val = str(args.code).strip() if args.code else str(cell(r, "A")).strip()
        if a_val == "":
            plans.append((r, "", "", "", {}, "", "", "", "", "A欄空白,略過"))
            continue
        codes = resolve_symbol(a_val)
        if not codes:
            plans.append((r, a_val, "", "", {}, "", "", "", "", "非台股(無 TWSE 資料),略過"))
            continue
        used = codes[0]
        want_dates = [d for _, d in date_cols if d]
        today = datetime.date.today()
        closes = (twse_hist.daily_closes(used, min(want_dates), max(want_dates))
                  if want_dates else {})
        divs = twse_hist.dividends(used, today - datetime.timedelta(days=730), today)
        cur = "TWD"
        if not closes and not divs:
            plans.append((r, a_val, "", "", {}, "", "", "", "", "查無報價,略過"))
            continue
        prices = {letter: round(closes[d], 2) for letter, d in date_cols if d in closes}
        missing_dates = [str(d) for letter, d in date_cols if d not in closes]
        # 配息數:--freq > 既有 O > 自動偵測
        existing_o = cell(r, C["freq"]) if "freq" in C else ""
        if args.freq is not None:
            freq = args.freq
        elif str(existing_o).strip() != "":
            freq = int(existing_o)
        else:
            freq = detect_freq(divs)
        # Q = 最近 freq 次股息「平均」(O=1→最近一次, O=4→最近四次平均);無配息→0
        if divs and freq:
            recent_divs = divs[-freq:]
            q = round(sum(a for _, a in recent_divs) / len(recent_divs), 2)
            months = sorted(set(d.month for d, _ in recent_divs))
            exmonth = ",".join(map(str, months))
        else:
            q = 0
            exmonth = ""
        shares = cell(r, C["shares"]) or 0
        try:
            expR = round(float(shares) * float(q) * float(freq), 2)
        except (TypeError, ValueError):
            expR = ""
        note = ("缺日期收盤:" + ",".join(missing_dates)) if missing_dates else ""
        plans.append((r, a_val, used, cur, prices, freq, exmonth, q, expR, note))

    # 預覽
    for r, a, sym, cur, prices, freq, exmonth, q, expR, note in plans:
        if not sym:
            print(f"  列{r}: {a!r} → {note}")
            continue
        pstr = " ".join(f"{lt}={v}" for lt, v in prices.items())
        print(f"  列{r}: {a!r} [{sym}/{cur}]  {pstr}  | O={freq} P={exmonth} "
              f"Q={q} R≈{expR}" + (f"  ⚠ {note}" if note else ""))

    valid = [p for p in plans if p[2]]
    if not valid:
        print("\n沒有可寫入的列。")
        return
    if args.dry_run:
        print("\n[dry-run] 未寫入。")
        return
    if not args.yes:
        ans = input(f"\n確認補齊 {len(valid)} 列(D~N/S~W 公式+價格+配息)?(yes/no): ").strip().lower()
        if ans != "yes":
            print("已取消。")
            return

    data = []
    for r, a, sym, cur, prices, freq, exmonth, q, expR, note in valid:
        # 1) 各日期欄收盤(靜態值)
        for letter, val in prices.items():
            data.append({"range": f"{SHEET_NAME}!{letter}{r}", "values": [[val]]})
        # 2) 購入數 L:空白才補 0,不覆寫既有
        if str(cell(r, C["boughtqty"])).strip() == "":
            data.append({"range": f"{SHEET_NAME}!{C['boughtqty']}{r}", "values": [[0]]})
        # 3) 配息靜態欄 O/P/Q(欄位被刪則略過)
        if "freq" in C:
            data.append({"range": f"{SHEET_NAME}!{C['freq']}{r}", "values": [[freq]]})
        if "exmonth" in C:
            data.append({"range": f"{SHEET_NAME}!{C['exmonth']}{r}", "values": [[exmonth]]})
        if "div" in C:
            data.append({"range": f"{SHEET_NAME}!{C['div']}{r}", "values": [[q]]})
        # 4) 公式欄(現今價一律取最後日期欄 LAST;與 audit 共用 expected_formulas)
        for letter, f in expected_formulas(C, LAST, r).items():
            data.append({"range": f"{SHEET_NAME}!{letter}{r}", "values": [[f]]})

    resp = service.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data}).execute()
    print(f"\n完成,更新 {resp.get('totalUpdatedCells')} 格({len(valid)} 列)。")
    nodiv = [str(p[0]) for p in valid if not p[5]]
    if nodiv:
        print(f"提醒:列 {','.join(nodiv)} 查無配息(不配息/槓桿型),O=0、Q=0、R=0。")

# ========================= 指令:audit(健檢) =========================
def cmd_audit(service, args):
    cols, date_cols, LAST = get_layout(service)
    if not date_cols:
        sys.exit("找不到任何日期欄,無法健檢。")
    REQUIRED = ["buy", "shares", "cost", "mktval", "pl", "plpct",
                "boughtqty", "boughtcost", "avg", "cur_avg", "cur_orig", "shrink"]
    OPTIONAL = ["freq", "exmonth", "div", "divincome", "yield", "yield2"]
    miss = [k for k in REQUIRED if k not in cols]
    if miss:
        sys.exit(f"標頭找不到必要欄位:{miss},請確認分頁標頭名稱。")
    C = cols
    skipped_opt = [k for k in OPTIONAL if k not in cols]
    if skipped_opt:
        print(f"提醒:找不到選用欄位 {skipped_opt}(該分頁已刪除),相關檢查略過。\n")
    allcols = list(cols.values()) + [d[0] for d in date_cols]
    end = col_letter(max(letter_to_idx(x) for x in allcols))
    last_row = args.max_row
    FO = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}!A1:{end}{last_row}",
        valueRenderOption="FORMULA").execute().get("values", [])
    VA = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}!A1:{end}{last_row}",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    def gf(i, letter):
        j = letter_to_idx(letter)
        return FO[i][j] if i < len(FO) and j < len(FO[i]) else ""
    def gv(i, letter):
        j = letter_to_idx(letter)
        return VA[i][j] if i < len(VA) and j < len(VA[i]) else ""

    date_letters = [d[0] for d in date_cols]
    last_date = date_cols[-1][1] if date_cols else None
    # 會出錯誤值(#DIV/0! 等)的計算欄
    comp_cols = [C[k] for k in ("cost", "mktval", "pl", "plpct", "avg",
                                "divincome", "cur_avg", "cur_orig", "shrink",
                                "yield", "yield2") if k in C]
    title = "公式樣式 / 日期價缺值 / 錯誤值"
    if not args.no_price and last_date:
        title += f" / {LAST}價對照TWSE({last_date})"
    print(f"分頁「{SHEET_NAME}」健檢:{title}  (現今價欄={LAST})\n")

    total = bad = nitems = 0
    for i in range(1, len(FO)):
        r = i + 1
        a = gf(i, "A")
        if str(a).strip() == "":
            continue
        total += 1
        iss = []
        # 1) 公式樣式
        for letter, f in expected_formulas(C, LAST, r).items():
            got = str(gf(i, letter)).strip()
            if got != f:
                iss.append(f"{letter}{r} 公式異常(實={got!r} 期={f!r})")
        # 2) 日期價缺值
        for letter in date_letters:
            if str(gv(i, letter)).strip() == "":
                iss.append(f"{letter}{r} 日期收盤空白")
        # 3) 錯誤值
        for letter in comp_cols:
            v = gv(i, letter)
            if isinstance(v, str) and v.startswith("#"):
                iss.append(f"{letter}{r} 錯誤值 {v}")
        # 4) 現今價欄對照實際收盤
        if not args.no_price and last_date:
            kv = gv(i, LAST)
            actual = None
            for sym in resolve_symbol(str(a)):
                actual, _ = fetch_close(sym, last_date)
                if actual is not None:
                    break
            if (actual is not None and isinstance(kv, (int, float))
                    and abs(kv - actual) > max(0.02, actual * 0.001)):
                iss.append(f"{LAST}{r} 收盤 {kv} ≠ 實際 {actual}")
        if iss:
            bad += 1
            nitems += len(iss)
            print(f"  ⚠ 列{r} {str(a)!r}:")
            for x in iss:
                print(f"      - {x}")
    if bad == 0:
        print(f"檢查 {total} 列 → 全部乾淨 ✅ 無錯誤")
    else:
        print(f"\n檢查 {total} 列 → {bad} 列有問題,共 {nitems} 項。"
              f"\n可用 fill-row 補整列,或手動修正後再 audit。")

# ========================= 指令:inspect =========================
def cmd_inspect(service, args):
    col = args.col.upper()
    hdr = col_header_date(service, col)
    hdr_date = serial_to_date(hdr) if isinstance(hdr, (int, float)) else hdr
    data = read_col(service, col)
    print(f"分頁「{SHEET_NAME}」 A欄 / {col}欄(標頭 {col}1 = {hdr} → {hdr_date}):\n")
    empties = []
    for r in sorted(data):
        a, t = data[r]
        if str(a).strip() == "" and str(t).strip() == "":
            continue
        flag = ""
        if str(a).strip() != "" and str(t).strip() == "":
            flag = "  ← 缺值"
            empties.append(r)
        print(f"  列{r:>3}: A={str(a)!r:>12}   {col}={t!r}{flag}")
    if empties:
        print(f"\n缺值列:{','.join(map(str, empties))}"
              f"\n→ 可執行:python3 {os.path.basename(__file__)} fill --rows {','.join(map(str, empties))}")
    else:
        print("\n(目標欄無缺值。)")

# ========================= 指令:fill =========================
def cmd_fill(service, args):
    col = args.col.upper()
    # 決定日期:--date 優先,否則用該欄標頭
    if args.date:
        target_date = parse_date(args.date)
        if not target_date:
            sys.exit(f"無法解析日期:{args.date}(請用 2026/6/12 格式)")
    else:
        hdr = col_header_date(service, col)
        target_date = serial_to_date(hdr) if isinstance(hdr, (int, float)) else parse_date(hdr)
        if not target_date:
            sys.exit(f"無法從 {col}1 標頭({hdr})判斷日期,請改用 --date 指定。")

    data = read_col(service, col)
    if args.auto:
        rows = [r for r in sorted(data)
                if str(data[r][0]).strip() != "" and str(data[r][1]).strip() == ""]
        if not rows:
            print("沒有『A 有值、目標欄空白』的列,無需處理。")
            return
    else:
        if not args.rows:
            sys.exit("請用 --rows 指定列(例 --rows 23,24 或 23-26),或用 --auto。")
        rows = parse_rows(args.rows)

    print(f"目標:分頁「{SHEET_NAME}」{col} 欄,日期 {target_date}\n")
    plan = []   # (row, a, symbol, price, cur, note)
    for r in rows:
        a = data.get(r, ("", ""))[0]
        if str(a).strip() == "":
            plan.append((r, a, "", None, "", "A欄空白,略過"))
            continue
        price, cur, used = None, "", ""
        for sym in resolve_symbol(a):
            price, cur = fetch_close(sym, target_date)
            used = sym
            if price is not None:
                break
        note = "" if price is not None else f"{cur}"   # 失敗時 cur 放訊息
        plan.append((r, a, used, price, cur if price is not None else "", note))

    print("預覽:")
    for r, a, sym, price, cur, note in plan:
        line = f"  {col}{r}  A={str(a)!r:>10}  [{sym}]  = "
        line += (f"{price} {cur}" if price is not None else f"(略) {note}")
        print(line)

    writable = [(r, price) for (r, a, sym, price, cur, note) in plan if price is not None]
    if not writable:
        print("\n沒有可寫入的值。")
        return
    if args.dry_run:
        print("\n[dry-run] 未寫入。")
        return
    if not args.yes:
        ans = input(f"\n確認寫入 {len(writable)} 格到 {col} 欄?(yes/no): ").strip().lower()
        if ans != "yes":
            print("已取消。")
            return

    body = {"valueInputOption": "USER_ENTERED",
            "data": [{"range": f"{SHEET_NAME}!{col}{r}", "values": [[v]]} for r, v in writable]}
    resp = service.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID, body=body).execute()
    print(f"\n完成,更新 {resp.get('totalUpdatedCells')} 格。")
    skipped = [str(r) for (r, a, sym, price, cur, note) in plan if price is None]
    if skipped:
        print(f"未寫入(需手動處理或補 ALIAS)的列:{','.join(skipped)}")

# ========================= main =========================
def main():
    global SHEET_NAME
    ap = argparse.ArgumentParser(description="依 A 欄代號抓收盤價寫回 K 欄")
    ap.add_argument("--sheet", default="latest",
                    help="目標分頁名稱;預設 'latest' = 自動挑 Jimmy_YYMMDD 結尾日期最新的分頁")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ins = sub.add_parser("inspect", help="看 A 欄與目標欄現況、標出缺值")
    p_ins.add_argument("--col", default=DEFAULT_COL, help=f"價格欄(預設 {DEFAULT_COL})")

    p_fill = sub.add_parser("fill", help="抓收盤價寫回")
    p_fill.add_argument("--rows", help="列號,例 23,24 或 23-26")
    p_fill.add_argument("--auto", action="store_true", help="自動補所有缺值列")
    p_fill.add_argument("--col", default=DEFAULT_COL, help=f"價格欄(預設 {DEFAULT_COL})")
    p_fill.add_argument("--date", help="收盤日期,例 2026/6/12(預設取該欄標頭)")
    p_fill.add_argument("--yes", action="store_true", help="免確認直接寫")
    p_fill.add_argument("--dry-run", action="store_true", help="只預覽不寫入")

    p_fr = sub.add_parser("fill-row", help="補整列:價格+公式+配息一鍵到位")
    p_fr.add_argument("--rows", required=True, help="列號,例 25 或 25,26")
    p_fr.add_argument("--code", help="股票代號覆寫(僅單列),例 2330;預設讀 A 欄")
    p_fr.add_argument("--freq", type=int, help="配息數覆寫(一年幾次);預設沿用既有或自動偵測")
    p_fr.add_argument("--yes", action="store_true", help="免確認直接寫")
    p_fr.add_argument("--dry-run", action="store_true", help="只預覽不寫入")

    p_au = sub.add_parser("audit", help="健檢:公式樣式/日期價缺值/錯誤值/K價對照")
    p_au.add_argument("--no-price", action="store_true", help="跳過抓 TWSE 對照收盤(較快)")
    p_au.add_argument("--max-row", type=int, default=80, help="掃描到第幾列(預設 80)")

    args = ap.parse_args()
    service = get_service()
    if args.sheet == "latest":
        SHEET_NAME = resolve_latest_sheet(service)
        print(f"[latest] 自動選用最新分頁:{SHEET_NAME}")
    else:
        SHEET_NAME = args.sheet
    if args.cmd == "inspect":
        cmd_inspect(service, args)
    elif args.cmd == "fill":
        cmd_fill(service, args)
    elif args.cmd == "fill-row":
        cmd_fill_row(service, args)
    else:
        cmd_audit(service, args)

if __name__ == "__main__":
    main()
