#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""update_wealth_os.py — 同步「股價試算」最新 Jimmy_YYMMDD 分頁 → 雲端 Wealth OS xlsm(就地更新)

觸發語:「更新 Wealth OS」

流程:
  1. Sheets API 讀「股價試算」最新 Jimmy_YYMMDD 分頁(自動挑日期最大者)
  2. Drive API 下載 Jimmy_Wealth_OS_Master_V4.4_Google.xlsm(先留本機備份)
  3. zip 手術式更新「02_持股總表」:只重寫該分頁 XML、sharedStrings、workbook.xml(calcPr),
     其餘元件(drawings/巨集按鈕、VBA、metadata、圖表)位元組原封不動
       C買進價/D股數/I~K歷史價/L現價/M購入數/N購入成本/O平均股價 + 標頭日期 + A1 標題
       E/F/G/H/P/Q/R/S 為公式,不動;設 fullCalcOnLoad 讓 Excel 開檔重算
  4. Drive API files().update 就地覆蓋(檔案 ID 不變,Drive 保留版本歷史)

⚠ 寫入絕不可走 openpyxl 的 wb.save():它重存會遺失 drawings 等 19 個元件,
  Excel 會判定檔案損毀打不開(2026-07-11 實際發生過)。openpyxl 只拿來「讀」。

用法:
  python3 update_wealth_os.py update            # 預覽差異後輸入 yes 寫入
  python3 update_wealth_os.py update --yes      # 不詢問直接寫入
  python3 update_wealth_os.py update --dry-run  # 只預覽
  python3 update_wealth_os.py update --yes --full
      # 同步後加做「全分頁快取刷新」:以 02 現值重算 01/03/04/13/14/16/17/安全指數
      # 的公式快取(讓 Drive 預覽也正確),並更新 Dashboard 日期、AI 摘要、07 路線圖
      # 股數、09 建議文字、14 現價快照等模板內容;各分頁動刀前檢查格位,版面變了即中止

前置:token.json 需含 Drive 權限;不足時先跑:
  python3 reauth_sheets.py --drive

清單差異原則:以股價試算為準。若兩邊清單出現增減,本腳本列出差異但不自動增刪列
(公式列增刪牽動 SUM 範圍,需人工處理),其餘一致的股票照常更新。
"""
import os
import sys
import io
import re
import shutil
import tempfile
import zipfile
import warnings
from datetime import datetime
from xml.sax.saxutils import escape

warnings.filterwarnings("ignore")

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
    import openpyxl
except ImportError:
    sys.exit("缺少套件,請先執行:\n  pip3 install --upgrade "
             "google-api-python-client google-auth-httplib2 google-auth-oauthlib openpyxl")

# 共用「股價試算」ID、最新分頁挑選與名稱→代號對照
from update_stock_price import ALIAS, resolve_latest_sheet, SPREADSHEET_ID

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "token.json")
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"

XLSM_FILE_ID = "1WCx7vav1olLNs8WJ7EQ4nr6zqG9csCGj"   # Jimmy_Wealth_OS_Master_V4.4_Google.xlsm
XLSM_MIME = "application/vnd.ms-excel.sheet.macroenabled.12"
TARGET_TAB = "03_持股總表"
HEADER_ROW = 2          # A2=股票
DATA_START_ROW = 3

# 11_設定:全檔唯一事實來源(2026-07-24 起)。房屋/房貸/信貸/現金/門檻/目標股數都以此為準,
# 各分頁公式已改為跨表引用它;本腳本亦一律讀這裡,不再從 Dashboard 讀(Dashboard 現在是引用公式)。
SETTINGS_TAB = "12_設定"
SETTINGS_MAP = {          # 鍵名 -> 11_設定 的列號(B 欄)
    "house": 3, "mortgage": 4, "loan": 5, "cash": 6, "cash_target": 7,
    "salary": 8, "living": 9, "mortgage_pay": 10, "loan_pay": 11, "dividend": 12,
    "lev_max": 13, "hi_max": 14, "mkt_min": 15, "single_max": 16,
    "t0050": 17, "t00662": 18, "ttsmc": 19,
}
# 設定表沒有對應項目、維持字面值的門檻
LEV_LOWER = 0.20          # 正二下緣(低於此顯示「可分批增加」)
STOCK_MAX = 0.40          # 個股合計上限

# 02_每日漲跌:每次 --full 依 16_資產歷史 重建;公式參照歷史,快取一併刷新(Drive 預覽也正確)
DAILY_TAB = "02_每日漲跌"

# 10_投資日誌:同步時偵測股數變動,自動補登買賣紀錄(均價由成本差回推,含手續費)
LOG_TAB = "11_投資日誌"

# 15_資產歷史:每次同步附加一列凍結快照(同日期重跑則覆寫該列)
HIST_TAB = "16_資產歷史"
HIST_HOUSE = 9_000_000               # 房屋市值(沿用原 F 欄公式常數)
HIST_DEBT = 8_400_000 + 5_000_000    # 負債備援值;實際以 01_Dashboard!E5(房貸)+H5(信貸) 動態讀取為準
HIST_CASH = 4_598_541                # 現金池備援值;實際以 01_Dashboard!B6 動態讀取為準(2026-07-15 Jimmy 確認)

# xlsm 用中文全名,股價試算部分用英文;統一經代號比對
ALIAS_XLSM = dict(ALIAS)
ALIAS_XLSM["台積電"] = "2330"


def to_code(label):
    return ALIAS_XLSM.get(str(label).strip(), str(label).strip())


def read_settings(wb):
    """讀 11_設定 B3~B19,回傳參數字典。缺格或非數值即中止(版面被改過)。"""
    if SETTINGS_TAB not in wb.sheetnames:
        sys.exit(f"找不到分頁「{SETTINGS_TAB}」。")
    ws = wb[SETTINGS_TAB]
    out = {}
    for key, row in SETTINGS_MAP.items():
        v = ws.cell(row, 2).value
        if isinstance(v, str):
            v = v.lstrip("=")
        try:
            out[key] = float(v)
        except (TypeError, ValueError):
            sys.exit(f"「{SETTINGS_TAB}」B{row}({ws.cell(row, 1).value})不是數值:{v!r},停止。")
    return out


def num(s):
    """'1,234.5' -> 1234.5;空字串 -> None;整數值回傳 int。"""
    s = str(s).replace(",", "").strip()
    if s == "":
        return None
    v = float(s)
    return int(v) if v == int(v) else v


def get_creds():
    # 無人環境(GitHub Actions):Service Account 優先,永不過期
    from update_stock_price import get_sa_creds
    sa = get_sa_creds()
    if sa:
        return sa
    if not os.path.exists(TOKEN_FILE):
        sys.exit(f"找不到 {TOKEN_FILE},請先執行:python3 reauth_sheets.py --drive")
    creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    if DRIVE_SCOPE not in (creds.scopes or []):
        sys.exit("token.json 沒有 Drive 權限,請先執行:python3 reauth_sheets.py --drive")
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            sys.exit("憑證已失效,請執行:python3 reauth_sheets.py --drive")
    return creds


# ========================= 來源:股價試算 =========================
def read_source(creds):
    """回傳 (分頁名, 日期標頭[4], {代號: {...}}, 代號順序)"""
    svc = build("sheets", "v4", credentials=creds)
    sheet_name = resolve_latest_sheet(svc)
    rows = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{sheet_name}!A1:Q60").execute().get("values", [])
    if not rows:
        sys.exit(f"{sheet_name} 讀不到資料。")
    header = rows[0] + [""] * (17 - len(rows[0]))
    date_headers = header[7:11]            # H..K:如 7/1, 7/2, 7/6, 7/9
    data = {}
    order = []
    for r in rows[1:]:
        r = list(r) + [""] * (17 - len(r))
        label = str(r[0]).strip()
        if not label:                      # 合計列 A 欄為空,之後不再有資料
            break
        code = to_code(label)
        data[code] = {
            "label": label,
            "buy":    num(r[1]),           # B 買進價
            "shares": num(r[2]),           # C 持有股數
            "hist":   [num(r[7]), num(r[8]), num(r[9])],   # H,I,J 歷史價
            "price":  num(r[10]),          # K 現今價
            "add_qty":  num(r[11]),        # L 購入數
            "add_cost": num(r[12]),        # M 購入成本
            "avg":      num(r[13]),        # N 平均股價
        }
        order.append(code)
    return sheet_name, date_headers, data, order


# ========================= 目標:xlsm =========================
def download_xlsm(creds, workdir):
    drive = build("drive", "v3", credentials=creds)
    meta = drive.files().get(fileId=XLSM_FILE_ID, fields="name,mimeType,modifiedTime").execute()
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, drive.files().get_media(fileId=XLSM_FILE_ID))
    done = False
    while not done:
        _, done = dl.next_chunk()
    path = os.path.join(workdir, meta["name"])
    with open(path, "wb") as f:
        f.write(buf.getvalue())
    # 就地覆蓋前先留一份時間戳備份(Drive 本身另有版本歷史)
    backup = os.path.join(tempfile.gettempdir(),
                          f"wealth_os_backup_{datetime.now():%y%m%d_%H%M%S}.xlsm")
    shutil.copy2(path, backup)
    return drive, meta, path, backup


def upload_xlsm(drive, path):
    media = MediaFileUpload(path, mimetype=XLSM_MIME, resumable=True)
    return drive.files().update(fileId=XLSM_FILE_ID, media_body=media,
                                fields="id,name,size,version,modifiedTime").execute()


# ========================= zip 手術式寫入 =========================
def _locate_sheet_part(zin, tab=TARGET_TAB):
    """由 workbook.xml + rels 找指定分頁對應的 worksheet XML 路徑。"""
    wbxml = zin.read("xl/workbook.xml").decode("utf-8")
    m = re.search(rf'<sheet [^>]*name="{tab}"[^>]*r:id="(rId\d+)"', wbxml)
    if not m:
        m = re.search(rf'<sheet [^>]*r:id="(rId\d+)"[^>]*name="{tab}"', wbxml)
    if not m:
        sys.exit(f"workbook.xml 找不到分頁「{tab}」。")
    rid = m.group(1)
    rels = zin.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    m = re.search(rf'Id="{rid}"[^>]*Target="(worksheets/[^"]+)"', rels)
    if not m:
        m = re.search(rf'Target="(worksheets/[^"]+)"[^>]*Id="{rid}"', rels)
    return "xl/" + m.group(1)


def _set_cell(xml, ref, val=None, string_idx=None):
    """改一格:保留樣式屬性,val=None 且無 string_idx 時清空。"""
    pat = re.compile(rf'<c r="{ref}"([^>]*?)(?:/>|>.*?</c>)', re.S)
    m = pat.search(xml)
    if not m:
        sys.exit(f"worksheet XML 找不到儲存格 {ref},版面可能已改,停止以免寫壞。")
    attrs = re.sub(r'\s*t="s"', "", m.group(1).rstrip("/").rstrip())
    if string_idx is not None:
        new = f'<c r="{ref}"{attrs} t="s"><v>{string_idx}</v></c>'
    elif val is None:
        new = f'<c r="{ref}"{attrs}/>'
    else:
        v = f"{val:g}" if isinstance(val, float) else str(val)
        new = f'<c r="{ref}"{attrs}><v>{v}</v></c>'
    return pat.sub(new.replace("\\", "\\\\"), xml, count=1)


def _hist_row_xml(n, styles, date_str, m):
    """組 15_資產歷史 一列:日期用 inlineStr(不動 sharedStrings)。"""
    def c(col, inner):
        return f'<c r="{col}{n}" s="{styles.get(col, "0")}"' + \
               (f'>{inner}</c>' if inner else '/>')
    ret = 0 if m["cost"] == 0 else (m["mv"] - m["cost"]) / m["cost"]
    debt = m.get("debt", HIST_DEBT)
    house = m.get("house", HIST_HOUSE)
    net = m["mv"] + house - debt + m.get("cash", HIST_CASH)
    return (f'<row r="{n}">'
            + f'<c r="A{n}" s="{styles.get("A", "0")}" t="inlineStr">'
              f'<is><t>{escape(date_str)}</t></is></c>'
            + c("B", f"<v>{m['mv']:.0f}</v>")
            + c("C", f"<v>{m['cost']:.1f}</v>")
            + c("D", f"<v>{m['mv'] - m['cost']:.1f}</v>")
            + c("E", f"<v>{ret:.10f}</v>")
            + c("F", f"<v>{house:.0f}</v>")
            + c("G", f"<v>{debt:.0f}</v>")
            + c("H", f"<v>{net:.0f}</v>")
            + "</row>")


def _update_history(hxml, sis, date_str, metrics):
    """凍結舊活公式列、同日覆寫、否則附加一列。回傳 (新xml, 動作說明)。"""
    data_rows = []          # (列號, 日期文字, 是否含公式, row原文)
    for m in re.finditer(r'<row r="(\d+)"[^>]*>(.*?)</row>', hxml, re.S):
        rn, content = int(m.group(1)), m.group(2)
        if rn < 2:
            continue
        a = re.search(rf'<c r="A{rn}"[^>]*?(?:t="s"[^>]*>.*?<v>(\d+)</v>|t="inlineStr"[^>]*>.*?<t>(.*?)</t>)',
                      content, re.S)
        if not a:
            continue
        date_txt = sis[int(a.group(1))] if a.group(1) else a.group(2)
        data_rows.append((rn, date_txt, "<f" in content, m.group(0)))
    if not data_rows:
        sys.exit(f"「{HIST_TAB}」找不到資料列,版面可能已改。")

    styles = {col: s for col, _r, s in
              re.findall(r'<c r="([A-H])(\d+)" s="(\d+)"', data_rows[0][3])}

    legacy = next((r for r in data_rows if r[2]), None)          # 含公式的舊活列
    same_day = next((r for r in data_rows if r[1] == date_str), None)
    if legacy:
        new_row = _hist_row_xml(legacy[0], styles, date_str, metrics)
        return hxml.replace(legacy[3], new_row, 1), f"凍結原活公式列(列{legacy[0]})為 {date_str} 靜態快照"
    if same_day:
        new_row = _hist_row_xml(same_day[0], styles, date_str, metrics)
        return hxml.replace(same_day[3], new_row, 1), f"同日重跑,覆寫列{same_day[0]}({date_str})"
    n = max(r[0] for r in data_rows) + 1
    new_row = _hist_row_xml(n, styles, date_str, metrics)
    m = re.search(rf'<row r="{n}"[^>]*>.*?</row>|<row r="{n}"[^>]*/>', hxml, re.S)
    if m:
        return hxml.replace(m.group(0), new_row, 1), f"附加 {date_str} 至列{n}(置換空列)"
    last = next(r for r in data_rows if r[0] == n - 1)
    return hxml.replace(last[3], last[3] + new_row, 1), f"附加 {date_str} 至列{n}"


def _append_trade_log(lxml, date_str, trades):
    """在 10_投資日誌 最後一筆之後附加自動偵測的買賣紀錄。"""
    import datetime as _dt
    data_rows = [int(m.group(1)) for m in
                 re.finditer(r'<row r="(\d+)"[^>]*>(?=.*?<c r="A\1"[^>]*><v>)', lxml)]
    data_rows = [r for r in data_rows if r >= 4]
    if not data_rows:
        sys.exit(f"「{LOG_TAB}」找不到資料列,版面可能已改。")
    last = max(data_rows)
    row_last = re.search(rf'<row r="{last}"[^>]*>.*?</row>', lxml, re.S).group(0)
    st = {c: s for c, _r, s in re.findall(r'<c r="([A-H])(\d+)" s="(\d+)"', row_last)}
    y, mo, d = (int(x) for x in date_str.split("/"))
    ser = (_dt.date(y, mo, d) - _dt.date(1899, 12, 30)).days
    inl = lambda t: f'<is><t>{escape(t)}</t></is>'
    prev = row_last
    for i, t in enumerate(trades):
        n = last + 1 + i
        rowx = (f'<row r="{n}">'
                f'<c r="A{n}" s="{st.get("A", "0")}"><v>{ser}</v></c>'
                f'<c r="B{n}" s="{st.get("B", "0")}" t="inlineStr">{inl(t["label"])}</c>'
                f'<c r="C{n}" s="{st.get("C", "0")}" t="inlineStr">{inl(t["act"])}</c>'
                f'<c r="D{n}" s="{st.get("D", "0")}"><v>{t["qty"]:g}</v></c>'
                f'<c r="E{n}" s="{st.get("E", "0")}"><v>{t["px"]:g}</v></c>'
                f'<c r="F{n}" s="{st.get("F", "0")}"><v>{t["amt"]:g}</v></c>'
                f'<c r="G{n}" s="{st.get("G", "0")}" t="inlineStr">{inl("同步自動記錄(含費均價)")}</c>'
                f'<c r="H{n}" s="{st.get("H", "0")}" t="inlineStr">{inl("✅")}</c>'
                f'</row>')
        m = re.search(rf'<row r="{n}"[^>]*>.*?</row>|<row r="{n}"[^>]*/>', lxml, re.S)
        if m:
            lxml = lxml.replace(m.group(0), rowx, 1)
        else:
            lxml = lxml.replace(prev, prev + rowx, 1)
        prev = rowx
    return lxml


def surgical_write(orig_path, out_path, num_cells, str_cells, hist=None, trades=None):
    """只重寫 worksheet XML / sharedStrings / workbook.xml(/資產歷史),其餘元件原樣複製。
    num_cells: {ref: 數值或 None}; str_cells: {ref: 文字}; hist: (日期字串, metrics)。"""
    zin = zipfile.ZipFile(orig_path)
    sheet_part = _locate_sheet_part(zin)
    sheet = zin.read(sheet_part).decode("utf-8")
    sst = zin.read("xl/sharedStrings.xml").decode("utf-8")
    wbxml = zin.read("xl/workbook.xml").decode("utf-8")

    for ref, val in num_cells.items():
        sheet = _set_cell(sheet, ref, val)

    # 字串:先在 sharedStrings 找同字,找到沿用索引,否則追加
    sis = [re.sub(r"<[^>]+>", "", s) for s in re.findall(r"<si>(.*?)</si>", sst, re.S)]
    appended = []
    for ref, text in str_cells.items():
        if text in sis:
            idx = sis.index(text)
        else:
            idx = len(sis)
            sis.append(text)
            appended.append(text)
        sheet = _set_cell(sheet, ref, string_idx=idx)
    if appended:
        add = "".join(f"<si><t>{escape(t)}</t></si>" for t in appended)
        sst = sst.replace("</sst>", add + "</sst>")
        m = re.search(r'uniqueCount="(\d+)"', sst)
        if m:
            sst = sst.replace(m.group(0), f'uniqueCount="{int(m.group(1)) + len(appended)}"')

    # 15_資產歷史:凍結/覆寫/附加一列
    hist_part, hist_note = None, None
    if hist is not None:
        date_str, metrics = hist
        hist_part = _locate_sheet_part(zin, HIST_TAB)
        hxml = zin.read(hist_part).decode("utf-8")
        hxml, hist_note = _update_history(hxml, sis, date_str, metrics)

    # 10_投資日誌:自動補登偵測到的買賣
    log_part = None
    if trades:
        log_part = _locate_sheet_part(zin, LOG_TAB)
        lxml = zin.read(log_part).decode("utf-8")
        lxml = _append_trade_log(lxml, hist[0], trades)

    # 公式快取值已過期 → 開檔強制重算
    if "fullCalcOnLoad" not in wbxml:
        if "<calcPr" in wbxml:
            wbxml = re.sub(r"<calcPr([^>/]*)/>", r'<calcPr\1 fullCalcOnLoad="1"/>', wbxml, count=1)
        else:
            wbxml = wbxml.replace("</workbook>", '<calcPr fullCalcOnLoad="1"/></workbook>')

    repl = {sheet_part: sheet.encode("utf-8"),
            "xl/sharedStrings.xml": sst.encode("utf-8"),
            "xl/workbook.xml": wbxml.encode("utf-8")}
    if hist_part:
        repl[hist_part] = hxml.encode("utf-8")
    if log_part:
        repl[log_part] = lxml.encode("utf-8")
    with zipfile.ZipFile(out_path, "w") as zout:
        for info in zin.infolist():
            data = repl.get(info.filename)
            if data is None:
                data = zin.read(info.filename)
            zout.writestr(info, data, compress_type=info.compress_type)
    zin.close()
    return sheet_part, hist_part, hist_note, log_part


def verify_output(orig_path, out_path, sheet_part, spot_checks, hist_part=None, log_part=None):
    """自檢:元件清單一致、僅預期元件變動、XML 合法、抽查值正確。"""
    from xml.dom import minidom
    za, zb = zipfile.ZipFile(orig_path), zipfile.ZipFile(out_path)
    assert za.namelist() == zb.namelist(), "元件清單與原檔不一致,中止!"
    changed = [n for n in za.namelist() if za.read(n) != zb.read(n)]
    allowed = {sheet_part, "xl/sharedStrings.xml", "xl/workbook.xml"}
    if hist_part:
        allowed.add(hist_part)
    if log_part:
        allowed.add(log_part)
    assert set(changed) <= allowed, f"意外變動元件 {set(changed) - allowed},中止!"
    for n in changed:
        minidom.parseString(zb.read(n))
    za.close(); zb.close()

    wb = openpyxl.load_workbook(out_path, keep_vba=True)   # 只讀,驗證可開
    ws = wb[TARGET_TAB]
    for (row, col), want in spot_checks.items():
        got = ws.cell(row, col).value
        assert got == want, f"抽查失敗 ({row},{col}):{got} != {want}"
    return changed


# 02_每日漲跌 欄寬(A日期 / B~E 金額 / F 金額 / G 報酬率)
DAILY_COLS = ('<cols><col customWidth="1" min="1" max="1" width="11.5"/>'
              '<col customWidth="1" min="2" max="4" width="13.5"/>'
              '<col customWidth="1" min="5" max="6" width="15.0"/>'
              '<col customWidth="1" min="7" max="7" width="11.5"/></cols>')


def _build_daily_sheetdata(hist, pct_style="11"):
    """組 02_每日漲跌 的 <cols>+<sheetData>:公式參照 16_資產歷史,快取一併寫入。
    hist: [(日期, 市值, 成本, 損益), ...](順序＝資產歷史列序,對應歷史列 2,3,4...)。
    欄:A日期 B總市值 C未實現損益 D單日損益漲跌(真實) E買進金額(成本變化) F單日市值漲跌 G單日報酬率。
    pct_style: 百分比(0.00%)樣式索引,由呼叫端動態取自 16_資產歷史 報酬率欄(索引會隨 styles 漂移,勿寫死)。"""
    H = f"'{HIST_TAB}'!"
    ST_TITLE, ST_TXT, ST_NUM, ST_PCT = "58", "5", "9", str(pct_style)

    def esc(f):
        return (f.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    def cell(ref, s, *, f=None, v=None, is_str=False):
        if f is not None:
            t = ' t="str"' if is_str else ''
            vv = (f'<v>{esc(str(v))}</v>' if (is_str and v is not None)
                  else (f'<v>{v}</v>' if v is not None else ''))
            return f'<c r="{ref}" s="{s}"{t}><f>{esc(f)}</f>{vv}</c>'
        if v is None:
            return f'<c r="{ref}" s="{s}"/>'
        if is_str:
            return f'<c r="{ref}" s="{s}" t="inlineStr"><is><t>{esc(str(v))}</t></is></c>'
        return f'<c r="{ref}" s="{s}"><v>{v}</v></c>'

    rows = ['<row r="1">' + cell("A1", ST_TITLE,
            v="02_每日漲跌｜每日總市值與損益變化", is_str=True) + '</row>']
    hdr = ['日期', '總市值', '未實現損益', '單日損益漲跌(真實)', '買進金額', '單日市值漲跌', '單日報酬率']
    rows.append('<row r="2">' + ''.join(
        cell(f"{c}2", ST_TXT, v=h, is_str=True) for c, h in zip("ABCDEFG", hdr)) + '</row>')

    for i, (dt, mv, cost, pl) in enumerate(hist):
        dr = 3 + i          # 每日漲跌列號
        hr = 2 + i          # 對應歷史列號(A日期 B市值 C成本 D損益)
        hrp = hr - 1
        A = f'IF({H}A{hr}="","",{H}A{hr})'
        B = f'IF({H}A{hr}="","",{H}B{hr})'
        C = f'IF({H}A{hr}="","",{H}D{hr})'
        parts = [cell(f"A{dr}", ST_TXT, f=A, v=dt, is_str=True),
                 cell(f"B{dr}", ST_NUM, f=B, v=f"{mv:.0f}"),
                 cell(f"C{dr}", ST_NUM, f=C, v=f"{pl:.0f}")]
        if i == 0:          # 首列無前值
            parts += [cell(f"D{dr}", ST_NUM), cell(f"E{dr}", ST_NUM),
                      cell(f"F{dr}", ST_NUM), cell(f"G{dr}", ST_PCT)]
        else:
            pmv, pcost, ppl = hist[i - 1][1], hist[i - 1][2], hist[i - 1][3]
            buy, dmv, dpl = cost - pcost, mv - pmv, pl - ppl
            D = f'IF({H}A{hr}="","",{H}D{hr}-{H}D{hrp})'          # 單日損益漲跌(真實)
            E = f'IF({H}A{hr}="","",{H}C{hr}-{H}C{hrp})'          # 買進金額=成本變化
            F = f'IF({H}A{hr}="","",{H}B{hr}-{H}B{hrp})'          # 單日市值漲跌
            G = f'IF(OR({H}A{hr}="",{H}B{hrp}=0),"",({H}D{hr}-{H}D{hrp})/{H}B{hrp})'
            parts += [cell(f"D{dr}", ST_NUM, f=D, v=f"{dpl:.0f}"),
                      cell(f"E{dr}", ST_NUM, f=E, v=f"{buy:.0f}"),
                      cell(f"F{dr}", ST_NUM, f=F, v=f"{dmv:.0f}"),
                      cell(f"G{dr}", ST_PCT, f=G, v=(f"{dpl/pmv:.10g}" if pmv else None))]
        rows.append(f'<row r="{dr}">' + ''.join(parts) + '</row>')
    return DAILY_COLS + '<sheetData>' + ''.join(rows) + '</sheetData>'


# ========================= --full:全分頁快取刷新 =========================
def _xml_cache(xml, ref, value, is_str=False):
    """公式格:保留 <f>,重寫快取 <v>。找不到格或非公式格即中止。"""
    m = re.search(rf'<c r="{ref}"([^>]*?)>(.*?)</c>', xml, re.S)
    if not m:
        sys.exit(f"--full:找不到儲存格 {ref},版面可能已改,停止以免寫壞。")
    fm = re.search(r'<f[^>]*?/>|<f[^>]*?>.*?</f>', m.group(2), re.S)
    if not fm:
        sys.exit(f"--full:{ref} 不是公式格,版面可能已改,停止。")
    s = re.search(r's="(\d+)"', m.group(1)).group(1)
    t = ' t="str"' if is_str else ''
    v = value if is_str else f"{value:.10g}"
    return xml[:m.start()] + f'<c r="{ref}" s="{s}"{t}>{fm.group(0)}<v>{v}</v></c>' + xml[m.end():]


def _xml_setnum(xml, ref, val):
    m = re.search(rf'<c r="{ref}"([^>]*?)>.*?</c>', xml, re.S)
    if not m:
        sys.exit(f"--full:找不到儲存格 {ref}。")
    s = re.search(r's="(\d+)"', m.group(1)).group(1)
    return xml[:m.start()] + f'<c r="{ref}" s="{s}"><v>{val:.10g}</v></c>' + xml[m.end():]


def full_refresh(path_in, path_out, date_str):
    """以 02 現值重算各分頁公式快取與模板文字(zip 手術,openpyxl 只讀)。"""
    import openpyxl
    wb = openpyxl.load_workbook(path_in, keep_vba=True)
    ws = wb[TARGET_TAB]
    S = []
    for r in range(DATA_START_ROW, ws.max_row + 1):
        n = ws.cell(r, 1).value
        if not n or str(n).strip() in ("", "合計"):
            continue
        S.append(dict(name=str(n).strip(), cat=str(ws.cell(r, 2).value).strip(),
                      buy=float(ws.cell(r, 3).value), sh=float(ws.cell(r, 4).value),
                      px=float(ws.cell(r, 12).value), avg=float(ws.cell(r, 15).value), row=r))
    for d in S:
        d['E'] = d['buy'] * d['sh']; d['F'] = d['px'] * d['sh']; d['G'] = d['F'] - d['E']
        d['H'] = d['G'] / d['E'] if d['E'] else 0
        d['P'] = d['px'] - d['avg']; d['Q'] = d['px'] - d['buy']; d['R'] = d['P'] - d['Q']
    E27 = sum(d['E'] for d in S); F27 = sum(d['F'] for d in S)
    G27, H27 = F27 - E27, (F27 - E27) / E27
    for d in S:
        d['S'] = d['F'] / F27
    N = {d['name']: d for d in S}
    cat = {}
    for d in S:
        cat[d['cat']] = cat.get(d['cat'], 0) + d['F']
    hi = cat.get('高股息ETF', 0) / F27; lev = cat.get('正二ETF', 0) / F27
    mkt = cat.get('市值ETF', 0) / F27; oth = cat.get('其他ETF', 0) / F27
    stk = cat.get('個股', 0) / F27
    mx_d = max(S, key=lambda d: d['F']); mx = mx_d['F'] / F27
    # 全部參數與門檻讀 11_設定(唯一事實來源)
    cfg = read_settings(wb)
    cash, house = cfg["cash"], cfg["house"]
    mort, loan = cfg["mortgage"], cfg["loan"]
    e6 = F27 + house + cash - mort - loan
    h6 = (mort + loan) / (F27 + house)
    b5x = 20 if cash < cfg["cash_target"] else 0
    b6x = 15 if lev > cfg["lev_max"] else 0
    b7x = 10 if hi > cfg["hi_max"] else 0
    b8x = 10 if mx > cfg["single_max"] else 0
    b9x = 15 if h6 > 0.6 else 0
    safety = max(0, 100 - b5x - b6x - b7x - b8x - b9x - 5)
    stars = ("★★★★★" if safety >= 90 else "★★★★☆" if safety >= 80 else
             "★★★☆☆" if safety >= 70 else "★★☆☆☆" if safety >= 60 else "★☆☆☆☆")

    zin = zipfile.ZipFile(path_in)
    tabs = {t: _locate_sheet_part(zin, t) for t in (
        "01_Dashboard", TARGET_TAB, "04_ETF分析", "05_個股分析", "08_投資路線圖",
        "10_AI建議", "13_加碼分析", "14_買點排行榜", "15_安全指數", "17_重複曝險",
        "18_投資規則檢查")}
    X = {p: zin.read(p).decode("utf-8") for p in tabs.values()}
    sst = zin.read("xl/sharedStrings.xml").decode("utf-8")
    sis = [re.sub(r"<[^>]+>", "", x) for x in re.findall(r"<si>(.*?)</si>", sst, re.S)]
    appended = []

    def sidx(t):
        if t in sis:
            return sis.index(t)
        sis.append(t); appended.append(t)
        return len(sis) - 1

    def setstr(part, ref, text):
        xml = X[part]
        m = re.search(rf'<c r="{ref}"([^>]*?)>.*?</c>', xml, re.S)
        if not m:
            sys.exit(f"--full:找不到 {ref}。")
        s = re.search(r's="(\d+)"', m.group(1)).group(1)
        X[part] = xml[:m.start()] + f'<c r="{ref}" s="{s}" t="s"><v>{sidx(text)}</v></c>' + xml[m.end():]

    def C(part, ref, v, is_str=False):
        X[part] = _xml_cache(X[part], ref, v, is_str)

    def V(part, ref, v):
        X[part] = _xml_setnum(X[part], ref, v)

    # 02:全公式快取
    p = tabs[TARGET_TAB]
    for d in S:
        for col in "EFGHPQRS":
            C(p, f"{col}{d['row']}", d[col])
    total_row = None
    m = re.search(r'<c r="E(\d+)"[^>]*><f[^>]*>SUM\(E', X[p])
    total_row = int(m.group(1)) if m else 27
    for ref, v in ((f"E{total_row}", E27), (f"F{total_row}", F27),
                   (f"G{total_row}", G27), (f"H{total_row}", H27), (f"S{total_row}", 1)):
        C(p, ref, v)

    # 01_Dashboard
    p = tabs["01_Dashboard"]
    setstr(p, "B3", date_str)
    for ref, v in (("E3", F27), ("B4", E27), ("E4", G27), ("H4", H27),
                   ("B5", house), ("B6", cash), ("E5", mort), ("H5", loan),
                   ("E6", e6), ("H6", h6), ("H3", safety)):
        C(p, ref, v)
    for rn, (cn, ratio) in {"11": ('高股息ETF', hi), "12": ('市值ETF', mkt),
                            "13": ('正二ETF', lev), "14": ('個股', stk),
                            "15": ('其他ETF', oth)}.items():
        C(p, f"B{rn}", cat.get(cn, 0))
        C(p, f"C{rn}", ratio)
    C(p, "D11", "偏高，停止新增" if hi > cfg["hi_max"] else "合理", True)
    C(p, "D12", "偏低，優先增加" if mkt < cfg["mkt_min"] else "合理", True)
    C(p, "D13", "偏高，暫停加碼" if lev > cfg["lev_max"] else
      ("可分批增加" if lev < LEV_LOWER else "合理"), True)
    C(p, "D14", "略高，留意集中度" if stk > STOCK_MAX else "合理", True)
    C(p, "D15", "觀察", True)
    t50, t62 = cfg["t0050"], cfg["t00662"]
    gap50 = int(t50 - N['0050']['sh']); gap62 = int(t62 - N['00662']['sh'])
    ai = [
        (f"0050 已達 {N['0050']['sh']:,.0f} 股，距離 {t50:,.0f} 股目標尚差 {gap50:,} 股"
         if gap50 > 0 else f"0050 已達成 {t50:,.0f} 股目標，依路線圖停止買入"),
        (f"00662 已達 {N['00662']['sh']:,.0f} 股，距 {t62:,.0f} 股目標尚差 {gap62:,} 股"
         if gap62 > 0 else f"00662 已達成 {t62:,.0f} 股目標"),
        f"高股息 ETF 比例 {hi:.1%}" + (f"，仍高於 {cfg['hi_max']:.0%} 上限，維持停止新增本金"
                                    if hi > cfg["hi_max"] else f"，低於 {cfg['hi_max']:.0%} 上限"),
        f"正二比例 {lev:.1%}" + (f"，已超過 {cfg['lev_max']:.0%} 上限，暫停加碼" if lev > cfg["lev_max"] else
                                (f"，位於 {LEV_LOWER:.0%}~{cfg['lev_max']:.0%} 目標區間"
                                 if lev >= LEV_LOWER else f"，低於 {LEV_LOWER:.0%} 目標區間下緣")),
        f"最大單一持股 {mx_d['name']}({mx:.1%})" + (f"，超過 {cfg['single_max']:.0%} 上限，留意集中度"
                                                if mx > cfg["single_max"] else ""),
        f"安全指數 {safety} 分;現金池 {cash:,.0f}(預留目標 {cfg['cash_target']:,.0f})",
    ]
    for i, t in enumerate(ai):
        setstr(p, f"B{18 + i}", t)

    # 03_ETF分析
    p = tabs["04_ETF分析"]
    lev_ex = cat.get('正二ETF', 0) - N['00663L']['F']
    for rn, v in {"4": cat.get('高股息ETF', 0), "5": cat.get('市值ETF', 0), "6": lev_ex,
                  "7": N['00663L']['F'], "8": cat.get('其他ETF', 0),
                  "9": cat.get('個股', 0)}.items():
        C(p, f"B{rn}", v)
        C(p, f"C{rn}", v / F27)
    C(p, "F4", "偏高" if hi > cfg["hi_max"] else "合理", True)
    C(p, "F5", "偏低" if mkt < cfg["mkt_min"] else "合理", True)
    r6 = lev_ex / F27
    C(p, "F6", "偏高" if r6 > cfg["lev_max"] else
      ("可分批增加" if r6 < LEV_LOWER else "合理"), True)
    C(p, "F8", "略高" if oth > 0.05 else "合理", True)
    # 目標欄現為引用 11_設定 的公式,快取一併刷新
    C(p, "E4", cfg["hi_max"]); C(p, "D5", cfg["mkt_min"]); C(p, "E6", cfg["lev_max"])

    # 04_個股分析(列4起 ↔ 02 個股與其後各列;F 欄為 shared 公式,逐列刷快取)
    p = tabs["05_個股分析"]
    st04 = [d for d in S if d['row'] >= 14]
    for i, d in enumerate(st04):
        r = 4 + i
        C(p, f"B{r}", d['F']); C(p, f"C{r}", d['S'])
        C(p, f"D{r}", d['G']); C(p, f"E{r}", d['H'])
        C(p, f"F{r}", "偏高" if d['S'] > cfg["single_max"] else
          ("留意" if d['S'] > 0.1 else "合理"), True)

    # 07_投資路線圖(C 欄靜態現況股數;D 欄目標為引用 11_設定 的公式,E 欄達成率)
    p = tabs["08_投資路線圖"]
    V(p, "C4", N['0050']['sh']); V(p, "C5", N['00662']['sh']); V(p, "C7", N['00631L']['sh'])
    C(p, "D4", cfg["t0050"]); C(p, "D5", cfg["t00662"]); C(p, "D6", cfg["ttsmc"])
    C(p, "E4", N['0050']['sh'] / cfg["t0050"])
    C(p, "E5", N['00662']['sh'] / cfg["t00662"])
    C(p, "E6", N['台積電']['sh'] / cfg["ttsmc"])

    # 09_AI建議(理由文字)
    p = tabs["10_AI建議"]
    setstr(p, "D4", f"目前 {N['0050']['sh']:,.0f} 股，距離目標尚差 {max(gap50,0):,} 股"
           if gap50 > 0 else f"已達成 {t50:,.0f} 股目標")
    setstr(p, "D7", f"00631L 已增加至 {N['00631L']['sh']:,.0f} 股，需避免槓桿過度")

    # 13_加碼分析(列4起 ↔ 02 列3起)
    p = tabs["13_加碼分析"]
    for i, d in enumerate(S):
        r = 4 + i
        C(p, f"C{r}", d['px']); C(p, f"D{r}", d['buy'])
        C(p, f"E{r}", d['P']);  C(p, f"F{r}", d['S'])
        h = (5 if d['name'] in ("0050", "00662") else 4 if d['name'] == "台積電" else
             1 if d['cat'] == "高股息ETF" or d['name'] == "6515" else
             3 if d['cat'] == "正二ETF" else 2)
        C(p, f"H{r}", h)
        C(p, f"I{r}", "優先布局" if h >= 5 else "可布局" if h >= 4 else
          "分批觀察" if h >= 3 else "停止新增" if h == 1 else "觀察", True)

    # 14_買點排行榜(E 快取 + D 現價快照;寶雅已於 2026-07-28 出清移除)
    p = tabs["14_買點排行榜"]
    for ref, n in (("E4", "0050"), ("E5", "00662"), ("E6", "台積電"), ("E7", "00631L"),
                   ("E8", "聯發科"), ("E10", "6515")):
        C(p, ref, N[n]['S'])
    C(p, "E9", hi)
    for ref, n in (("D4", "0050"), ("D5", "00662"), ("D6", "台積電"), ("D7", "00631L"),
                   ("D8", "聯發科"), ("D10", "6515")):
        V(p, ref, N[n]['px'])

    # 08_安全指數
    p = tabs["15_安全指數"]
    for ref, v in (("B2", safety), ("B5", b5x), ("B6", b6x), ("B7", b7x),
                   ("B8", b8x), ("B9", b9x)):
        C(p, ref, v)
    C(p, "D2", stars, True)
    setstr(p, "D8", f"最大單一持股為 {mx_d['name']}(約 {mx:.1%})")

    # 16_重複曝險
    p = tabs["17_重複曝險"]
    for i, n in enumerate(("台積電", "鴻海", "聯發科", "台達電", "6515")):
        r = 4 + i
        C(p, f"B{r}", N[n]['F']); C(p, f"D{r}", N[n]['F']); C(p, f"E{r}", N[n]['S'])

    # 17_投資規則檢查
    p = tabs["18_投資規則檢查"]
    for ref, v in (("B4", N['0050']['sh']), ("B5", N['00662']['sh']),
                   ("B6", N['台積電']['sh']), ("B7", hi), ("B8", lev),
                   ("B9", mx), ("B10", cash),
                   ("C4", cfg["t0050"]), ("C5", cfg["t00662"]), ("C6", cfg["ttsmc"]),
                   ("C7", cfg["hi_max"]), ("C8", cfg["lev_max"]),
                   ("C9", cfg["single_max"]), ("C10", cfg["cash_target"])):
        C(p, ref, v)
    C(p, "D4", "達標" if N['0050']['sh'] >= cfg["t0050"] else "未達標", True)
    C(p, "D5", "達標" if N['00662']['sh'] >= cfg["t00662"] else "未達標", True)
    C(p, "D6", "達標" if N['台積電']['sh'] >= cfg["ttsmc"] else "未達標", True)
    C(p, "D7", "偏高" if hi > cfg["hi_max"] else "OK", True)
    C(p, "D8", "偏高" if lev > cfg["lev_max"] else "OK", True)
    C(p, "D9", "偏高" if mx > cfg["single_max"] else "OK", True)
    C(p, "D10", "OK" if cash >= cfg["cash_target"] else "不足", True)

    # sharedStrings
    if appended:
        sst = sst.replace("</sst>", "".join(f"<si><t>{escape(t)}</t></si>" for t in appended) + "</sst>")
        m = re.search(r'uniqueCount="(\d+)"', sst)
        if m:
            sst = sst.replace(m.group(0), f'uniqueCount="{int(m.group(1)) + len(appended)}"')

    # 02_每日漲跌:依 16_資產歷史 重建整張 sheetData(公式＋快取,列數隨歷史成長)
    hist_rows = []
    hws = wb[HIST_TAB]
    for r in range(2, hws.max_row + 1):
        dt = hws.cell(r, 1).value
        if dt is None:
            continue
        hist_rows.append((str(dt)[:10], float(hws.cell(r, 2).value),   # 市值 B
                          float(hws.cell(r, 3).value),                 # 成本 C
                          float(hws.cell(r, 4).value)))                # 損益 D
    # 百分比樣式索引動態取自 16_資產歷史 報酬率欄(E),避免寫死索引隨 styles 漂移而失準
    try:
        pct_style = wb[HIST_TAB]["E2"].style_id
    except Exception:
        pct_style = 11
    daily_part = _locate_sheet_part(zin, DAILY_TAB)
    dxml = zin.read(daily_part).decode("utf-8")
    dxml = re.sub(r"<cols>.*?</sheetData>", _build_daily_sheetdata(hist_rows, pct_style),
                  dxml, flags=re.S)

    repl = {p: x.encode("utf-8") for p, x in X.items()}
    repl["xl/sharedStrings.xml"] = sst.encode("utf-8")
    repl[daily_part] = dxml.encode("utf-8")
    with zipfile.ZipFile(path_out, "w") as zout:
        for info in zin.infolist():
            data = repl.get(info.filename)
            if data is None:
                data = zin.read(info.filename)
            zout.writestr(info, data, compress_type=info.compress_type)
    zin.close()

    # 自檢
    from xml.dom import minidom
    za, zb = zipfile.ZipFile(path_in), zipfile.ZipFile(path_out)
    assert za.namelist() == zb.namelist(), "--full:元件清單不一致!"
    changed = [n for n in za.namelist() if za.read(n) != zb.read(n)]
    assert set(changed) <= set(repl), f"--full:意外變動 {set(changed) - set(repl)}"
    for n in changed:
        minidom.parseString(zb.read(n))
    za.close(); zb.close()
    openpyxl.load_workbook(path_out, keep_vba=True)   # 可開檔驗證
    return changed, dict(F27=F27, hi=hi, lev=lev, mx=f"{mx_d['name']} {mx:.1%}", safety=safety)


# ========================= 同步 =========================
def fmt(v):
    if v is None:
        return "(空)"
    return f"{v:,}" if isinstance(v, int) else f"{v:,.2f}"


def sync(args):
    creds = get_creds()
    sheet_name, date_headers, src, src_order = read_source(creds)
    print(f"[來源] 股價試算/{sheet_name},共 {len(src)} 檔;"
          f"日期欄 {date_headers[0]}~{date_headers[3]}")

    workdir = tempfile.mkdtemp(prefix="wealth_os_")
    drive, meta, path, backup = download_xlsm(creds, workdir)
    print(f"[目標] {meta['name']}(雲端最後修改 {meta['modifiedTime']})")
    print(f"[備份] {backup}")

    wb = openpyxl.load_workbook(path, keep_vba=True)   # 只讀:預覽與列定位
    if TARGET_TAB not in wb.sheetnames:
        sys.exit(f"xlsm 內找不到分頁「{TARGET_TAB}」。")
    ws = wb[TARGET_TAB]
    if str(ws.cell(HEADER_ROW, 1).value).strip() != "股票":
        sys.exit(f"「{TARGET_TAB}」A{HEADER_ROW} 不是「股票」,版面可能已改,停止以免寫壞。")

    xl_rows = {}
    for r in range(DATA_START_ROW, ws.max_row + 1):
        label = ws.cell(r, 1).value
        if label is None or str(label).strip() in ("", "合計"):
            continue
        xl_rows[to_code(label)] = r

    only_src = [c for c in src_order if c not in xl_rows]
    only_xl = [c for c in xl_rows if c not in src]
    common = [c for c in src_order if c in xl_rows]

    # ---------- 預覽 ----------
    print(f"\n更新明細(共同 {len(common)} 檔;C買進價/D股數/L現價 舊→新):")
    for code in common:
        s, r = src[code], xl_rows[code]
        old = (ws.cell(r, 3).value, ws.cell(r, 4).value, ws.cell(r, 12).value)
        new = (s["buy"], s["shares"], s["price"])
        mark = "" if all(num(str(o)) == n for o, n in zip(old, new)) else "  *"
        print(f"  列{r:>2} {code:>7} {str(s['label']):　<5}"
              f" C {fmt(num(str(old[0])))}→{fmt(new[0])}"
              f" | D {fmt(num(str(old[1])))}→{fmt(new[1])}"
              f" | L {fmt(num(str(old[2])))}→{fmt(new[2])}{mark}")
    print(f"  另同步:I~K 歷史價與標頭({date_headers[0]}、{date_headers[1]}、{date_headers[2]})、"
          f"L 標頭({date_headers[3]}現價)、M購入數/N購入成本/O平均股價、A1 標題")
    if only_src:
        print(f"\n⚠ 只在股價試算、xlsm 缺列(不自動新增,請人工加列):{', '.join(only_src)}")
    if only_xl:
        print(f"⚠ 只在 xlsm、股價試算已無(不自動刪除,請人工處理):{', '.join(only_xl)}")

    if args.dry_run:
        print("\n[dry-run] 未寫入。")
        return
    if not args.yes:
        ans = input("\n確認就地更新雲端檔案?(yes/no) ").strip().lower()
        if ans not in ("y", "yes"):
            print("已取消,未寫入。")
            return

    # ---------- 組 cell 更新表,zip 手術寫入 ----------
    num_cells, str_cells = {}, {}
    for code in common:
        s, r = src[code], xl_rows[code]
        num_cells[f"C{r}"] = s["buy"]
        num_cells[f"D{r}"] = s["shares"]
        for j, hv in enumerate(s["hist"]):
            num_cells[f"{'IJK'[j]}{r}"] = hv
        num_cells[f"L{r}"] = s["price"]
        num_cells[f"M{r}"] = s["add_qty"]
        num_cells[f"N{r}"] = s["add_cost"]
        num_cells[f"O{r}"] = s["avg"]
    for i in range(3):
        str_cells[f"{'IJK'[i]}{HEADER_ROW}"] = date_headers[i]
    str_cells[f"L{HEADER_ROW}"] = f"{date_headers[3]}現價"
    str_cells["A1"] = f"03_持股總表｜最新庫存(同步自 股價試算 {sheet_name})"

    # 15_資產歷史快照:市值=Σ股數×現價、成本=Σ股數×買進價(與 02 的 F27/E27 同口徑)
    # 房屋/負債/現金一律讀 11_設定(唯一事實來源)
    cfg = read_settings(wb)
    metrics = {
        "mv":   sum(src[c]["shares"] * src[c]["price"] for c in common
                    if src[c]["shares"] and src[c]["price"]),
        "cost": sum(src[c]["shares"] * src[c]["buy"] for c in common
                    if src[c]["shares"] and src[c]["buy"]),
        "cash":  cfg["cash"],
        "debt":  cfg["mortgage"] + cfg["loan"],
        "house": cfg["house"],
    }
    date_str = f"{datetime.now():%Y/%m/%d}"

    # 偵測持股變動 → 自動補登投資日誌(均價=成本差/股數差,即含手續費均價)
    trades = []
    for code in common:
        s, r = src[code], xl_rows[code]
        old_sh = num(str(ws.cell(r, 4).value or 0)) or 0
        old_buy = num(str(ws.cell(r, 3).value or 0)) or 0
        dq = (s["shares"] or 0) - old_sh
        if abs(dq) < 1:
            continue
        dcost = (s["buy"] or 0) * (s["shares"] or 0) - old_buy * old_sh
        px = round(abs(dcost / dq), 2) if dq else 0
        trades.append({"label": s["label"], "act": "買進" if dq > 0 else "賣出",
                       "qty": abs(dq), "px": px, "amt": round(abs(dcost))})
    if trades:
        for t in trades:
            print(f"[日誌] 偵測 {t['label']} {t['act']} {t['qty']:,.0f} 股 @ {t['px']}(含費)")

    out = os.path.join(workdir, "updated.xlsm")
    sheet_part, hist_part, hist_note, log_part = surgical_write(
        path, out, num_cells, str_cells, hist=(date_str, metrics), trades=trades)
    print(f"[歷史] {hist_note};市值 {metrics['mv']:,.0f} 成本 {metrics['cost']:,.0f}")
    if log_part:
        print(f"[日誌] 已自動補登 {len(trades)} 筆至 {LOG_TAB}")

    first = common[0]
    spot = {(xl_rows[first], 12): src[first]["price"],
            (xl_rows[common[-1]], 4): src[common[-1]]["shares"]}
    changed = verify_output(path, out, sheet_part, spot, hist_part, log_part)
    print(f"[自檢] 元件清單一致,僅變動 {changed};XML 合法;抽查值 OK")

    if getattr(args, "full", False):
        out2 = os.path.join(workdir, "updated_full.xlsm")
        fchanged, fsum = full_refresh(out, out2, date_str)
        print(f"[full] 全分頁快取刷新:{len(fchanged)} 個元件;"
              f"總市值 {fsum['F27']:,.0f}|高股息 {fsum['hi']:.1%}|正二 {fsum['lev']:.1%}|"
              f"最大 {fsum['mx']}|安全指數 {fsum['safety']}")
        out = out2

    info = upload_xlsm(drive, out)
    print(f"\n✅ 已就地更新:{info['name']}(檔案 ID 不變,版本 {info.get('version')},"
          f"大小 {info.get('size')} bytes,雲端時間 {info['modifiedTime']})")
    print(f"   共更新 {len(common)} 檔;原檔備份:{backup}")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="同步股價試算最新分頁到 Wealth OS xlsm(02_持股總表)")
    sub = ap.add_subparsers(dest="cmd")
    up = sub.add_parser("update", help="預覽並更新")
    up.add_argument("--yes", action="store_true", help="不詢問直接寫入")
    up.add_argument("--dry-run", action="store_true", help="只預覽不寫入")
    up.add_argument("--full", action="store_true",
                    help="同步後加做全分頁快取刷新(Dashboard/ETF分析/個股分析/加碼分析/排行榜/安全指數/規則檢查)")
    args = ap.parse_args()
    if args.cmd != "update":
        ap.print_help()
        return
    sync(args)


if __name__ == "__main__":
    main()
