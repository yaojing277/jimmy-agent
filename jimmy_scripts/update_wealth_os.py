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
import calendar as _calendar_mod
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

# 00_正二分析:每次 --full 由 lev2_analysis 抓 TWSE 收盤重建(00631L vs 00663L)
# 資料源在 TWSE 而非表內,故全部寫死為數值,不寫公式。
# 抓取失敗只警告不中斷 —— wealth_sync.yml 每日排程會跑 --full,不能因外部 API 抖動整批失敗。
LEV2_TAB = "00_正二分析"
LEV2_DAYS = 60

# 06_阿良資產負債表:每次 --full 由 lev2_balance 以 03 現值 + 12_設定 參數整張重建(數值寫死)
# 12_設定 缺參數只警告不中斷,理由同上
LEV2BAL_TAB = "06_阿良資產負債表"
# 2026-09-16 起停用:Jimmy 改回手動維護範本版面(D9 改引用 03_持股總表 合計),重建會覆蓋手動公式
LEV2BAL_ENABLED = False

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


def _locate_chart_parts(zin, sheet_tab):
    """分頁 → sheetN.xml → sheetN.xml.rels 找 drawing 檔 → drawingM.xml.rels 找每個 rId 對應的
    chart 檔。回傳 dict(sheet_part, drawing_path, drawing_rels_path, chart_by_rid)。
    ⚠ chart 檔名(chartN.xml 的 N)會隨 Excel 重新存檔而被重新編號(已實測發生過,原本 chart5
    漂移成 chart2),不可假設固定檔名——凡是要覆寫既有圖表內容,一律先呼叫此函式动态定位,
    再用 _find_chart_by_marker() 靠內容指紋(而非檔名/rId 順序)辨識是哪張圖。"""
    sheet_part = _locate_sheet_part(zin, sheet_tab)
    sheet_file = sheet_part.split("/")[-1]
    rels_path = f"xl/worksheets/_rels/{sheet_file}.rels"
    if rels_path not in zin.namelist():
        sys.exit(f"「{sheet_tab}」({sheet_part}) 沒有 rels 檔,不存在圖表關聯。")
    srels = zin.read(rels_path).decode("utf-8")
    m = re.search(r'Type="[^"]*/drawing"[^>]*Target="\.\./(drawings/[^"]+)"', srels)
    if not m:
        m = re.search(r'Target="\.\./(drawings/[^"]+)"[^>]*Type="[^"]*/drawing"', srels)
    if not m:
        sys.exit(f"「{sheet_tab}」rels 找不到 drawing 關聯。")
    drawing_path = "xl/" + m.group(1)
    drawing_file = drawing_path.split("/")[-1]
    drawing_rels_path = f"xl/drawings/_rels/{drawing_file}.rels"
    chart_by_rid = {}
    if drawing_rels_path in zin.namelist():
        drels = zin.read(drawing_rels_path).decode("utf-8")
        for rm in re.finditer(r'<Relationship Id="(rId\d+)"[^>]*Target="\.\./(charts/[^"]+)"', drels):
            chart_by_rid[rm.group(1)] = "xl/" + rm.group(2)
    return dict(sheet_part=sheet_part, drawing_path=drawing_path,
                drawing_rels_path=drawing_rels_path, chart_by_rid=chart_by_rid)


def _find_chart_by_marker(zin, chart_by_rid, marker):
    """逐一讀取 chart_by_rid 的 chart XML,回傳第一個內容含 marker 字串的路徑;找不到回傳 None。
    用內容指紋而非檔名/rId 判斷,完全免疫於 Excel 重新編號 part 的問題。"""
    for path in chart_by_rid.values():
        if marker in zin.read(path).decode("utf-8"):
            return path
    return None


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
    欄:A日期 B總市值 C未實現損益 D單日損益漲跌(真實) E淨投入資金(成本變化) F單日市值漲跌 G單日報酬率。
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
    hdr = ['日期', '總市值', '未實現損益', '單日損益漲跌(真實)', '淨投入資金', '單日市值漲跌', '單日報酬率']
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
        if i == 0:          # 起點列(無前一日):D/E/F/G 改為整欄總計 SUM(x4:x末列)
            if len(hist) > 1:
                # 整欄範圍 x4:x1048576(從第4列到欄底最大列;列數成長自動涵蓋,不寫死末列,
                # 亦不含 D3 故不循環。用明確上界而非 D4:D 開放語法,Excel/Google 皆通用)
                END = 1048576
                sD = sum(hist[k][3] - hist[k - 1][3] for k in range(1, len(hist)))     # Σ損益Δ
                sE = sum(hist[k][2] - hist[k - 1][2] for k in range(1, len(hist)))     # Σ淨投入資金
                sF = sum(hist[k][1] - hist[k - 1][1] for k in range(1, len(hist)))     # Σ市值Δ
                sG = sD / hist[0][1] if hist[0][1] else 0    # 累計報酬率=Σ損益Δ/起始市值(D3/B3)
                parts += [cell(f"D{dr}", ST_NUM, f=f"SUM(D4:D{END})", v=f"{sD:.0f}"),
                          cell(f"E{dr}", ST_NUM, f=f"SUM(E4:E{END})", v=f"{sE:.0f}"),
                          cell(f"F{dr}", ST_NUM, f=f"SUM(F4:F{END})", v=f"{sF:.0f}"),
                          cell(f"G{dr}", ST_PCT, f=f"D{dr}/B{dr}", v=f"{sG:.10g}")]
            else:
                parts += [cell(f"D{dr}", ST_NUM), cell(f"E{dr}", ST_NUM),
                          cell(f"F{dr}", ST_NUM), cell(f"G{dr}", ST_PCT)]
        else:
            pmv, pcost, ppl = hist[i - 1][1], hist[i - 1][2], hist[i - 1][3]
            buy, dmv, dpl = cost - pcost, mv - pmv, pl - ppl
            D = f'IF({H}A{hr}="","",{H}D{hr}-{H}D{hrp})'          # 單日損益漲跌(真實)
            E = f'IF({H}A{hr}="","",{H}C{hr}-{H}C{hrp})'          # 淨投入資金=成本變化
            F = f'IF({H}A{hr}="","",{H}B{hr}-{H}B{hrp})'          # 單日市值漲跌
            G = f'IF(OR({H}A{hr}="",{H}B{hrp}=0),"",({H}D{hr}-{H}D{hrp})/{H}B{hrp})'
            parts += [cell(f"D{dr}", ST_NUM, f=D, v=f"{dpl:.0f}"),
                      cell(f"E{dr}", ST_NUM, f=E, v=f"{buy:.0f}"),
                      cell(f"F{dr}", ST_NUM, f=F, v=f"{dmv:.0f}"),
                      cell(f"G{dr}", ST_PCT, f=G, v=(f"{dpl/pmv:.10g}" if pmv else None))]
        rows.append(f'<row r="{dr}">' + ''.join(parts) + '</row>')
    return DAILY_COLS + '<sheetData>' + ''.join(rows) + '</sheetData>'


def _build_daily_chart_xml(hist):
    """組 02_每日漲跌 的雙軸折線圖 chart5.xml(自足、快取內嵌、範圍隨歷史成長)。
    hist 同 _build_daily_sheetdata:[(日期,市值,成本,損益),...]。圖只繪每日資料列(i>=1,
    即每日漲跌列4起,跳過起點列3——起點的 D/E/F 是整欄 SUM 總計非當日值,會讓折線爆衝)。
    主軸左:總市值(B)＋未實現損益(C);副軸右:單日損益漲跌(真實,D)。三者量級差百倍,需雙軸。"""
    from xml.sax.saxutils import escape as _esc
    T = TAB = DAILY_TAB
    r0, rlast = 4, len(hist) + 2          # 每日資料列 4..(len+2)
    dates = [hist[i][0] for i in range(1, len(hist))]
    tot   = [hist[i][1] for i in range(1, len(hist))]                 # B 總市值
    pnl   = [hist[i][3] for i in range(1, len(hist))]                 # C 未實現損益(損益)
    dpl   = [hist[i][3] - hist[i - 1][3] for i in range(1, len(hist))]  # D 單日損益漲跌(真實,排除買賣)

    def numc(vals, fmt="General"):
        pts = "".join(f'<c:pt idx="{i}"><c:v>{v:.0f}</c:v></c:pt>' for i, v in enumerate(vals))
        return (f'<c:numCache><c:formatCode>{fmt}</c:formatCode>'
                f'<c:ptCount val="{len(vals)}"/>{pts}</c:numCache>')

    def strc(vals):
        pts = "".join(f'<c:pt idx="{i}"><c:v>{_esc(str(v))}</c:v></c:pt>' for i, v in enumerate(vals))
        return f'<c:strCache><c:ptCount val="{len(vals)}"/>{pts}</c:strCache>'

    catf = f"'{T}'!$A${r0}:$A${rlast}"
    CAT = f'<c:cat><c:strRef><c:f>{_esc(catf)}</c:f>{strc(dates)}</c:strRef></c:cat>'

    def ser(idx, name_cell, name_txt, col, vals, rgb):
        txf = f"'{T}'!${name_cell}$2"
        valf = f"'{T}'!${col}${r0}:${col}${rlast}"
        return (f'<c:ser><c:idx val="{idx}"/><c:order val="{idx}"/>'
                f'<c:tx><c:strRef><c:f>{_esc(txf)}</c:f>'
                f'<c:strCache><c:ptCount val="1"/><c:pt idx="0"><c:v>{_esc(name_txt)}</c:v></c:pt></c:strCache>'
                f'</c:strRef></c:tx>'
                f'<c:spPr><a:ln w="28575"><a:solidFill><a:srgbClr val="{rgb}"/></a:solidFill></a:ln></c:spPr>'
                f'<c:marker><c:symbol val="circle"/><c:size val="5"/>'
                f'<c:spPr><a:solidFill><a:srgbClr val="{rgb}"/></a:solidFill></c:spPr></c:marker>'
                f'{CAT}<c:val><c:numRef><c:f>{_esc(valf)}</c:f>{numc(vals)}</c:numRef></c:val>'
                f'<c:smooth val="0"/></c:ser>')

    AXC1, AXV1, AXV2, AXC2 = 111111111, 222222222, 333333333, 444444444
    line1 = (f'<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>'
             + ser(0, "B", "總市值", "B", tot, "2E75B6")
             + ser(1, "C", "未實現損益", "C", pnl, "548235")
             + f'<c:marker val="1"/><c:axId val="{AXC1}"/><c:axId val="{AXV1}"/></c:lineChart>')
    line2 = (f'<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>'
             + ser(2, "D", "單日損益漲跌(真實)", "D", dpl, "ED7D31")
             + f'<c:marker val="1"/><c:axId val="{AXC2}"/><c:axId val="{AXV2}"/></c:lineChart>')
    catAx1 = (f'<c:catAx><c:axId val="{AXC1}"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
              f'<c:delete val="0"/><c:axPos val="b"/>'
              f'<c:txPr><a:bodyPr rot="-2700000" vert="horz"/><a:lstStyle/>'
              f'<a:p><a:pPr><a:defRPr sz="800"/></a:pPr><a:endParaRPr lang="zh-TW"/></a:p></c:txPr>'
              f'<c:crossAx val="{AXV1}"/><c:crosses val="autoZero"/><c:auto val="1"/>'
              f'<c:lblAlgn val="ctr"/><c:lblOffset val="100"/><c:noMultiLvlLbl val="0"/></c:catAx>')
    valAx1 = (f'<c:valAx><c:axId val="{AXV1}"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
              f'<c:delete val="0"/><c:axPos val="l"/><c:majorGridlines/>'
              f'<c:title><c:tx><c:rich><a:bodyPr rot="-5400000" vert="horz"/><a:lstStyle/>'
              f'<a:p><a:pPr><a:defRPr sz="900"/></a:pPr><a:r><a:rPr lang="zh-TW" sz="900"/>'
              f'<a:t>總市值 / 未實現損益</a:t></a:r></a:p></c:rich></c:tx><c:overlay val="0"/></c:title>'
              f'<c:numFmt formatCode="#,##0" sourceLinked="0"/><c:majorTickMark val="out"/>'
              f'<c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/>'
              f'<c:crossAx val="{AXC1}"/><c:crosses val="autoZero"/><c:crossBetween val="between"/></c:valAx>')
    valAx2 = (f'<c:valAx><c:axId val="{AXV2}"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
              f'<c:delete val="0"/><c:axPos val="r"/>'
              f'<c:title><c:tx><c:rich><a:bodyPr rot="-5400000" vert="horz"/><a:lstStyle/>'
              f'<a:p><a:pPr><a:defRPr sz="900"/></a:pPr><a:r><a:rPr lang="zh-TW" sz="900"/>'
              f'<a:t>單日損益漲跌(真實)</a:t></a:r></a:p></c:rich></c:tx><c:overlay val="0"/></c:title>'
              f'<c:numFmt formatCode="#,##0" sourceLinked="0"/><c:majorTickMark val="out"/>'
              f'<c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/>'
              f'<c:crossAx val="{AXC2}"/><c:crosses val="max"/><c:crossBetween val="between"/></c:valAx>')
    catAx2 = (f'<c:catAx><c:axId val="{AXC2}"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
              f'<c:delete val="1"/><c:axPos val="b"/>'
              f'<c:crossAx val="{AXV2}"/><c:crosses val="autoZero"/><c:auto val="1"/>'
              f'<c:lblAlgn val="ctr"/><c:lblOffset val="100"/><c:noMultiLvlLbl val="0"/></c:catAx>')
    title = ('<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/>'
             '<a:p><a:pPr><a:defRPr sz="1200" b="1"/></a:pPr>'
             '<a:r><a:rPr lang="zh-TW" sz="1200" b="1"/><a:t>每日總市值與損益走勢</a:t></a:r></a:p>'
             '</c:rich></c:tx><c:overlay val="0"/></c:title>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<c:roundedCorners val="0"/><c:chart>'
            + title + '<c:autoTitleDeleted val="0"/><c:plotArea><c:layout/>'
            + line1 + line2 + catAx1 + valAx1 + valAx2 + catAx2
            + '</c:plotArea><c:legend><c:legendPos val="b"/><c:overlay val="0"/></c:legend>'
            '<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart></c:chartSpace>')


def _build_daily_sheetdata_desc(hist, pct_style, title_row=1, txt_style="5"):
    """跟 _build_daily_sheetdata 欄位定義完全一樣,但日期由新到舊排序(最新在最上面)。
    「加總列」(最早一天,沒有前一天可比,整欄總計=累計報酬率)固定釘在表頭正下方(agg_row),
    逐日資料從 agg_row 下一列(data_start)開始,一樣新到舊排序,每天新資料插進 data_start,
    把既有逐日列往下推,加總列本身位置不變(2026-08-28 Jimmy 要求,加總列比較像「合計」,
    釘在最上面比放在表格最後一列直覺)。SUM 用固定範圍(data_start~data_end,不能用開放式
    上界)。title_row:標題列列號,由呼叫端決定要放第幾列(報酬日曆可能搬到這個表格上面)。
    txt_style:A欄日期/表頭文字用的樣式(預設 "5",呼叫端可傳明確白底黑字的安全版本,
    避免手機暗色主題下「無填色」+主題相對字色看不到文字)。
    最早一天(orig_idx=0)的原始 A/B/C 值另外獨立放在表格最後一列(base_row,緊接在最舊
    逐日列下面),D/E/F/G 留空(沒有前一天可比);agg_row 的 A 改成「累計」說明文字,
    B/C 改成參照最新一天(data_start)的總市值/損益(2026-08-29 Jimmy 要求)。
    回傳(sheetData 內容,不含 <row> 外的 <sheetData> 包裝, dict(title_row,header_row,
    agg_row,data_start,data_end,base_row))。data_start/data_end 只涵蓋逐日列(不含加總列
    跟 base_row),呼叫端要把加總列/base_row 也套用同一套字色/格式修正時,請額外處理。"""
    H = f"'{HIST_TAB}'!"
    ST_TITLE, ST_TXT, ST_NUM, ST_PCT = "58", str(txt_style), "9", str(pct_style)
    N = len(hist)
    header_row = title_row + 1
    agg_row = header_row + 1
    data_start = agg_row + 1
    data_end = data_start + N - 2   # N-1 筆逐日列;N==1 時 data_end < data_start(空範圍)

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

    rows = [f'<row r="{title_row}">' + cell(f"A{title_row}", ST_TITLE,
            v="02_每日漲跌｜每日總市值與損益變化", is_str=True) + '</row>']
    hdr = ['日期', '總市值', '未實現損益', '單日損益漲跌(真實)', '淨投入資金', '單日市值漲跌', '單日報酬率']
    rows.append(f'<row r="{header_row}">' + ''.join(
        cell(f"{c}{header_row}", ST_TXT, v=h, is_str=True) for c, h in zip("ABCDEFG", hdr)) + '</row>')

    # 加總列(agg_row,累計統計):固定釘在表頭正下方。A 欄「累計」說明文字。
    # B/C 改成「歷史新高」:逐日列範圍(data_start~data_end,即表格 B25:B67 這種樣式)裡
    # 總市值/未實現損益的最大值,而不是單純鏡射最新一天的數字(2026-09-02 Jimmy 要求)。
    # 範圍用動態算出的 data_start/data_end,不寫死列號,表格每天長一列時公式自動涵蓋新增列。
    # D/E/F 整欄加總逐日列(data_start~data_end,加總列不在這個範圍內,不用再排除自己)。
    # (2026-08-29 Jimmy 要求)
    # 2026-08-29 Jimmy 改成更精簡的「累計YY/MM至今」格式(手動示範過,例:2026/07/01→累計26/07至今)
    A0 = f"累計{hist[0][0][2:4]}/{hist[0][0][5:7]}至今"
    if N > 1:
        maxB = max(hist[j][1] for j in range(1, N))
        maxC = max(hist[j][3] for j in range(1, N))
        agg_parts = [cell(f"A{agg_row}", ST_TXT, v=A0, is_str=True),
                     cell(f"B{agg_row}", ST_NUM, f=f"MAX(B{data_start}:B{data_end})", v=f"{maxB:.0f}"),
                     cell(f"C{agg_row}", ST_NUM, f=f"MAX(C{data_start}:C{data_end})", v=f"{maxC:.0f}")]
    else:
        # 只有 base_row、還沒有任何逐日列時,MAX 範圍會反向(data_end<data_start),
        # 退回鏡射最新一天的舊行為,避免產生無效公式
        agg_parts = [cell(f"A{agg_row}", ST_TXT, v=A0, is_str=True),
                     cell(f"B{agg_row}", ST_NUM, f=f"B{data_start}", v=f"{hist[-1][1]:.0f}"),
                     cell(f"C{agg_row}", ST_NUM, f=f"C{data_start}", v=f"{hist[-1][3]:.0f}")]
    if N > 1:
        sD = sum(hist[j][3] - hist[j - 1][3] for j in range(1, N))
        sE = sum(hist[j][2] - hist[j - 1][2] for j in range(1, N))
        sF = sum(hist[j][1] - hist[j - 1][1] for j in range(1, N))
        sG = sD / hist[0][1] if hist[0][1] else 0
        agg_parts += [cell(f"D{agg_row}", ST_NUM, f=f"SUM(D{data_start}:D{data_end})", v=f"{sD:.0f}"),
                      cell(f"E{agg_row}", ST_NUM, f=f"SUM(E{data_start}:E{data_end})", v=f"{sE:.0f}"),
                      cell(f"F{agg_row}", ST_NUM, f=f"SUM(F{data_start}:F{data_end})", v=f"{sF:.0f}"),
                      cell(f"G{agg_row}", ST_PCT, f=f"D{agg_row}/B{agg_row}", v=f"{sG:.10g}")]
    else:
        agg_parts += [cell(f"D{agg_row}", ST_NUM), cell(f"E{agg_row}", ST_NUM),
                      cell(f"F{agg_row}", ST_NUM), cell(f"G{agg_row}", ST_PCT)]
    rows.append(f'<row r="{agg_row}">' + ''.join(agg_parts) + '</row>')

    # 逐日列(orig_idx 1..N-1,新到舊):data_start 是最新一天,往下遞減到最舊的逐日列
    for k in range(max(N - 1, 0)):
        dr = data_start + k
        orig_idx = (N - 1) - k
        hr = 2 + orig_idx          # 16_資產歷史 對應列號
        hrp = hr - 1
        dt, mv, cost, pl = hist[orig_idx]
        A = f'IF({H}A{hr}="","",{H}A{hr})'
        B = f'IF({H}A{hr}="","",{H}B{hr})'
        C = f'IF({H}A{hr}="","",{H}D{hr})'
        pmv, pcost, ppl = hist[orig_idx - 1][1], hist[orig_idx - 1][2], hist[orig_idx - 1][3]
        buy, dmv, dpl = cost - pcost, mv - pmv, pl - ppl
        D = f'IF({H}A{hr}="","",{H}D{hr}-{H}D{hrp})'
        E = f'IF({H}A{hr}="","",{H}C{hr}-{H}C{hrp})'
        F = f'IF({H}A{hr}="","",{H}B{hr}-{H}B{hrp})'
        G = f'IF(OR({H}A{hr}="",{H}B{hrp}=0),"",({H}D{hr}-{H}D{hrp})/{H}B{hrp})'
        parts = [cell(f"A{dr}", ST_TXT, f=A, v=dt, is_str=True),
                 cell(f"B{dr}", ST_NUM, f=B, v=f"{mv:.0f}"),
                 cell(f"C{dr}", ST_NUM, f=C, v=f"{pl:.0f}"),
                 cell(f"D{dr}", ST_NUM, f=D, v=f"{dpl:.0f}"),
                 cell(f"E{dr}", ST_NUM, f=E, v=f"{buy:.0f}"),
                 cell(f"F{dr}", ST_NUM, f=F, v=f"{dmv:.0f}"),
                 cell(f"G{dr}", ST_PCT, f=G, v=(f"{dpl/pmv:.10g}" if pmv else None))]
        rows.append(f'<row r="{dr}">' + ''.join(parts) + '</row>')

    # base_row:最早一天(orig_idx=0)的原始資料,搬回它自己單獨一列(緊接在最舊逐日列下面),
    # D/E/F/G 留空(沒有前一天可比)。(2026-08-29 Jimmy 要求把 7/1 原始資料放回列表最底)
    base_row = data_end + 1
    dt0, mv0, cost0, pl0 = hist[0]
    A0f = f'IF({H}A2="","",{H}A2)'
    B0f = f'IF({H}A2="","",{H}B2)'
    C0f = f'IF({H}A2="","",{H}D2)'
    base_parts = [cell(f"A{base_row}", ST_TXT, f=A0f, v=dt0, is_str=True),
                  cell(f"B{base_row}", ST_NUM, f=B0f, v=f"{mv0:.0f}"),
                  cell(f"C{base_row}", ST_NUM, f=C0f, v=f"{pl0:.0f}"),
                  cell(f"D{base_row}", ST_NUM), cell(f"E{base_row}", ST_NUM),
                  cell(f"F{base_row}", ST_NUM), cell(f"G{base_row}", ST_PCT)]
    rows.append(f'<row r="{base_row}">' + ''.join(base_parts) + '</row>')

    return "".join(rows), dict(title_row=title_row, header_row=header_row, agg_row=agg_row,
                                data_start=data_start, data_end=data_end, base_row=base_row)


def _build_daily_chart_xml_desc(hist, tab, header_row, data_start, data_end):
    """跟 _build_daily_chart_xml 邏輯一致(雙軸折線圖:總市值/未實現損益/單日損益漲跌),但配合
    _build_daily_sheetdata_desc 的新列序(由新到舊):資料範圍是 data_start..data_end,兩者
    傳進來時已經是純逐日列範圍(加總列另外釘在表頭正下方,不在這個範圍內,不用再排除)。
    類別軸方向反轉(maxMin),讓圖表視覺上仍是「左舊右新」,讀圖習慣不受表格列序反轉影響。"""
    from xml.sax.saxutils import escape as _esc
    T = tab
    N = len(hist)
    order = list(range(N - 1, 0, -1))     # orig_idx 由新到舊,對應 row data_start..data_end
    r0, rlast = data_start, data_end
    dates = [hist[idx][0] for idx in order]
    tot   = [hist[idx][1] for idx in order]
    pnl   = [hist[idx][3] for idx in order]
    dpl   = [hist[idx][3] - hist[idx - 1][3] for idx in order]

    def numc(vals, fmt="General"):
        pts = "".join(f'<c:pt idx="{i}"><c:v>{v:.0f}</c:v></c:pt>' for i, v in enumerate(vals))
        return (f'<c:numCache><c:formatCode>{fmt}</c:formatCode>'
                f'<c:ptCount val="{len(vals)}"/>{pts}</c:numCache>')

    def strc(vals):
        pts = "".join(f'<c:pt idx="{i}"><c:v>{_esc(str(v))}</c:v></c:pt>' for i, v in enumerate(vals))
        return f'<c:strCache><c:ptCount val="{len(vals)}"/>{pts}</c:strCache>'

    catf = f"'{T}'!$A${r0}:$A${rlast}"
    CAT = f'<c:cat><c:strRef><c:f>{_esc(catf)}</c:f>{strc(dates)}</c:strRef></c:cat>'

    def ser(idx, name_cell, name_txt, col, vals, rgb):
        txf = f"'{T}'!${name_cell}${header_row}"
        valf = f"'{T}'!${col}${r0}:${col}${rlast}"
        return (f'<c:ser><c:idx val="{idx}"/><c:order val="{idx}"/>'
                f'<c:tx><c:strRef><c:f>{_esc(txf)}</c:f>'
                f'<c:strCache><c:ptCount val="1"/><c:pt idx="0"><c:v>{_esc(name_txt)}</c:v></c:pt></c:strCache>'
                f'</c:strRef></c:tx>'
                f'<c:spPr><a:ln w="28575"><a:solidFill><a:srgbClr val="{rgb}"/></a:solidFill></a:ln></c:spPr>'
                f'<c:marker><c:symbol val="circle"/><c:size val="5"/>'
                f'<c:spPr><a:solidFill><a:srgbClr val="{rgb}"/></a:solidFill></c:spPr></c:marker>'
                f'{CAT}<c:val><c:numRef><c:f>{_esc(valf)}</c:f>{numc(vals)}</c:numRef></c:val>'
                f'<c:smooth val="0"/></c:ser>')

    AXC1, AXV1, AXV2, AXC2 = 111111111, 222222222, 333333333, 444444444
    line1 = (f'<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>'
             + ser(0, "B", "總市值", "B", tot, "2E75B6")
             + ser(1, "C", "未實現損益", "C", pnl, "548235")
             + f'<c:marker val="1"/><c:axId val="{AXC1}"/><c:axId val="{AXV1}"/></c:lineChart>')
    line2 = (f'<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>'
             + ser(2, "D", "單日損益漲跌(真實)", "D", dpl, "ED7D31")
             + f'<c:marker val="1"/><c:axId val="{AXC2}"/><c:axId val="{AXV2}"/></c:lineChart>')
    catAx1 = (f'<c:catAx><c:axId val="{AXC1}"/><c:scaling><c:orientation val="maxMin"/></c:scaling>'
              f'<c:delete val="0"/><c:axPos val="b"/>'
              f'<c:txPr><a:bodyPr rot="-2700000" vert="horz"/><a:lstStyle/>'
              f'<a:p><a:pPr><a:defRPr sz="800"/></a:pPr><a:endParaRPr lang="zh-TW"/></a:p></c:txPr>'
              f'<c:crossAx val="{AXV1}"/><c:crosses val="autoZero"/><c:auto val="1"/>'
              f'<c:lblAlgn val="ctr"/><c:lblOffset val="100"/><c:noMultiLvlLbl val="0"/></c:catAx>')
    valAx1 = (f'<c:valAx><c:axId val="{AXV1}"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
              f'<c:delete val="0"/><c:axPos val="l"/><c:majorGridlines/>'
              f'<c:title><c:tx><c:rich><a:bodyPr rot="-5400000" vert="horz"/><a:lstStyle/>'
              f'<a:p><a:pPr><a:defRPr sz="900"/></a:pPr><a:r><a:rPr lang="zh-TW" sz="900"/>'
              f'<a:t>總市值 / 未實現損益</a:t></a:r></a:p></c:rich></c:tx><c:overlay val="0"/></c:title>'
              f'<c:numFmt formatCode="#,##0" sourceLinked="0"/><c:majorTickMark val="out"/>'
              f'<c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/>'
              f'<c:crossAx val="{AXC1}"/><c:crosses val="autoZero"/><c:crossBetween val="between"/></c:valAx>')
    valAx2 = (f'<c:valAx><c:axId val="{AXV2}"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
              f'<c:delete val="0"/><c:axPos val="r"/>'
              f'<c:title><c:tx><c:rich><a:bodyPr rot="-5400000" vert="horz"/><a:lstStyle/>'
              f'<a:p><a:pPr><a:defRPr sz="900"/></a:pPr><a:r><a:rPr lang="zh-TW" sz="900"/>'
              f'<a:t>單日損益漲跌(真實)</a:t></a:r></a:p></c:rich></c:tx><c:overlay val="0"/></c:title>'
              f'<c:numFmt formatCode="#,##0" sourceLinked="0"/><c:majorTickMark val="out"/>'
              f'<c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/>'
              f'<c:crossAx val="{AXC2}"/><c:crosses val="max"/><c:crossBetween val="between"/></c:valAx>')
    catAx2 = (f'<c:catAx><c:axId val="{AXC2}"/><c:scaling><c:orientation val="maxMin"/></c:scaling>'
              f'<c:delete val="1"/><c:axPos val="b"/>'
              f'<c:crossAx val="{AXV2}"/><c:crosses val="autoZero"/><c:auto val="1"/>'
              f'<c:lblAlgn val="ctr"/><c:lblOffset val="100"/><c:noMultiLvlLbl val="0"/></c:catAx>')
    title = ('<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/>'
             '<a:p><a:pPr><a:defRPr sz="1200" b="1"/></a:pPr>'
             '<a:r><a:rPr lang="zh-TW" sz="1200" b="1"/><a:t>每日總市值與損益走勢</a:t></a:r></a:p>'
             '</c:rich></c:tx><c:overlay val="0"/></c:title>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<c:roundedCorners val="0"/><c:chart>'
            + title + '<c:autoTitleDeleted val="0"/><c:plotArea><c:layout/>'
            + line1 + line2 + catAx1 + valAx1 + valAx2 + catAx2
            + '</c:plotArea><c:legend><c:legendPos val="b"/><c:overlay val="0"/></c:legend>'
            '<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart></c:chartSpace>')


def _month_seq_desc(hist_rows):
    """回傳(年,月)由新到舊的序列,涵蓋 hist_rows 全部月份。如果最早那筆資料不是從 1 號開始
    (通常是帳戶剛開戶、只有月中幾天零碎資料),排除那個月,避免產生一份幾乎全空的月曆;
    如果最早資料剛好是從 1 號開始(完整月),則保留,不會平白少算一個月。"""
    dates = [datetime.strptime(r[0], "%Y/%m/%d").date() for r in hist_rows]
    if not dates:
        return []
    latest, earliest = max(dates), min(dates)
    y, m = latest.year, latest.month
    seq = []
    while True:
        seq.append((y, m))
        if (y, m) == (earliest.year, earliest.month):
            break
        y, m = _prev_month(y, m)
    if earliest.day != 1 and len(seq) > 1:
        seq.pop()
    return seq


# ========================= 02_每日漲跌:報酬日曆(月曆熱力圖+長條圖) =========================
def _prev_month(year, month):
    """回傳(year,month)的上一個月,跨年正確處理(1月的上月=去年12月)。"""
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _month_calendar_grid(hist, year=None, month=None):
    """依 hist(同 _build_daily_sheetdata 的 [(日期,市值,成本,損益),...],對應16_資產歷史全歷史列序)
    算出月曆結構。year/month 不給時預設抓 hist 最後一筆資料所在年月(不是系統當前日期,避免假日/
    收盤延遲資料誤判);呼叫端可明確指定其他月份(例如上個月),資料源仍是同一份全歷史 hist。
    回傳 dict(year,month,weeks(固定6週,每週=dict(days=[{day,amt,pct}|None ×5],week_amt,week_pct)),
    month_amt,month_pct,all_days([(date,amt|None),...]只含本月工作日,供長條圖用));hist 為空回傳 None。"""
    if not hist:
        return None
    by_date = {datetime.strptime(dt, "%Y/%m/%d").date(): i for i, (dt, *_r) in enumerate(hist)}
    if year is None or month is None:
        last_date = datetime.strptime(hist[-1][0], "%Y/%m/%d").date()
        year, month = last_date.year, last_date.month

    def day_value(d):
        idx = by_date.get(d)
        if idx is None or idx == 0:
            return None, None
        amt = hist[idx][3] - hist[idx - 1][3]
        pmv = hist[idx - 1][1]
        return amt, (amt / pmv if pmv else None)

    weeks, all_days = [], []
    for week_dates in _calendar_mod.Calendar(firstweekday=0).monthdatescalendar(year, month):
        wdays, amts, base_idx = [], [], None
        for d in week_dates[:5]:
            if d.month != month or d.year != year:
                wdays.append(None)
                continue
            amt, pct = day_value(d)
            wdays.append({"day": d.day, "amt": amt, "pct": pct})
            all_days.append((d, amt))
            if amt is not None:
                amts.append(amt)
                if base_idx is None:
                    base_idx = by_date[d] - 1
        week_amt = sum(amts) if amts else None
        week_pct = (week_amt / hist[base_idx][1]) if (amts and hist[base_idx][1]) else None
        weeks.append({"days": wdays, "week_amt": week_amt, "week_pct": week_pct})
    while len(weeks) < 6:      # 固定6週骨架,讓長條圖錨點計算方式不隨月份週數變動
        weeks.append({"days": [None] * 5, "week_amt": None, "week_pct": None})

    month_amts = [a for _, a in all_days if a is not None]
    month_amt = sum(month_amts) if month_amts else None
    first_idx = next((by_date[d] - 1 for d, a in all_days if a is not None), None)
    month_pct = (month_amt / hist[first_idx][1]
                 if (month_amt is not None and first_idx is not None and hist[first_idx][1]) else None)
    return dict(year=year, month=month, weeks=weeks, month_amt=month_amt, month_pct=month_pct,
                all_days=all_days)


CAL_GRID_COLOR = "FFD1D5DB"           # 上月(次要)報酬日曆格線:細灰
CAL_GRID_COLOR_CURRENT = "FF000000"   # 本月(主要)報酬日曆格線:黑色(2026-08-22 Jimmy 要求加強本月可視性)


def _ensure_calendar_border(styles_xml, color=CAL_GRID_COLOR):
    """確保 xl/styles.xml 的 <borders> 有一組「四邊細框線(指定顏色)」,append-only、冪等(已存在
    就重用索引,不重複新增)。回傳 (styles_xml_可能不變, border_id)。"""
    target = (f'<border><left style="thin"><color rgb="{color}"/></left>'
              f'<right style="thin"><color rgb="{color}"/></right>'
              f'<top style="thin"><color rgb="{color}"/></top>'
              f'<bottom style="thin"><color rgb="{color}"/></bottom><diagonal/></border>')
    m = re.search(r'(<borders count=")(\d+)(">)(.*?)(</borders>)', styles_xml, re.S)
    count, body = int(m.group(2)), m.group(4)
    borders = re.findall(r'<border\b[^>]*/>|<border\b[^>]*>.*?</border>', body, re.S)
    for i, b in enumerate(borders):
        if b == target:
            return styles_xml, i
    new_xml = (styles_xml[:m.start()] + f'<borders count="{count + 1}">' + body + target
               + '</borders>' + styles_xml[m.end():])
    return new_xml, count


def _ensure_bordered_style(styles_xml, base_id, border_id):
    """複製 cellXfs[base_id] 的字型/填色/數字格式/對齊,只把 borderId 換成 border_id,append-only、
    冪等(styles.xml 裡已有一模一樣的 xf 就直接重用該索引,不重複新增)。
    回傳 (styles_xml_可能不變, 帶框線版本的新索引)。"""
    m = re.search(r'(<cellXfs count=")(\d+)(">)(.*?)(</cellXfs>)', styles_xml, re.S)
    count, body = int(m.group(2)), m.group(4)
    xfs = re.findall(r'<xf\b[^>]*/>|<xf\b[^>]*>.*?</xf>', body, re.S)
    base = xfs[base_id]
    target = re.sub(r'borderId="\d+"', f'borderId="{border_id}"', base)
    if 'applyBorder=' not in target:
        target = target.replace('<xf ', '<xf applyBorder="1" ', 1)
    for i, xf in enumerate(xfs):
        if xf == target:
            return styles_xml, i
    new_xml = (styles_xml[:m.start()] + f'<cellXfs count="{count + 1}">' + body + target
               + '</cellXfs>' + styles_xml[m.end():])
    return new_xml, count


def _ensure_calendar_grid_styles(styles_xml, base_ids, color=CAL_GRID_COLOR):
    """確保報酬日曆用的「帶格線」樣式版本都存在(指定顏色框線 + 每個 base_id 各一組帶框 cellXfs),
    全部 append-only、冪等。base_ids: 需要帶框版本的既有樣式索引 dict(如 {"title":58,"txt":5,
    "num":9,"pct":pct_style})。回傳 (styles_xml_可能不變, {角色名: 帶框樣式索引})。"""
    styles_xml, border_id = _ensure_calendar_border(styles_xml, color)
    style_map = {}
    for role, bid in base_ids.items():
        styles_xml, new_id = _ensure_bordered_style(styles_xml, bid, border_id)
        style_map[role] = new_id
    return styles_xml, style_map


def _build_calendar_rows(cal, start_row, style_map):
    """組月曆熱力圖的 <row> XML(標題+星期表頭+固定6週×3列)、mergeCells、conditionalFormatting。
    顏色直接複用 xl/styles.xml 既有 dxfId 4(粗體紅字+淺紅底,正值)/dxfId 0(粗體綠字+淺綠底,負值)。
    每格都加細灰框線(方便肉眼分隔,見 _ensure_calendar_grid_styles),樣式索引由呼叫端傳入的
    style_map(角色名→帶框樣式索引,由 _ensure_calendar_grid_styles 動態產生)決定,不寫死。
    start_row: 標題列列號(呼叫端傳「現有資料最後列+2」,中間留一列緩衝)。
    回傳 (rows_xml, merge_xml, condfmt_xml, last_row)。"""
    ST_TITLE, ST_TXT, ST_NUM, ST_PCT = (str(style_map["title"]), str(style_map["txt"]),
                                         str(style_map["num"]), str(style_map["pct"]))
    cols = "ABCDE"

    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    def cell(ref, s, v=None, is_str=False):
        if v is None:
            return f'<c r="{ref}" s="{s}"/>'
        if is_str:
            return f'<c r="{ref}" s="{s}" t="inlineStr"><is><t>{esc(v)}</t></is></c>'
        return f'<c r="{ref}" s="{s}"><v>{v}</v></c>'

    title_row, header_row = start_row, start_row + 1
    rows = [f'<row r="{title_row}">'
            + cell(f"A{title_row}", ST_TITLE, f"{cal['year']}年{cal['month']}月報酬日曆", is_str=True)
            + cell(f"D{title_row}", ST_TXT, "月損益", is_str=True)
            + cell(f"E{title_row}", ST_NUM, f"{cal['month_amt']:.0f}" if cal["month_amt"] is not None else None)
            + cell(f"F{title_row}", ST_PCT, f"{cal['month_pct']:.10g}" if cal["month_pct"] is not None else None)
            + '</row>',
            f'<row r="{header_row}">'
            + ''.join(cell(f"{c}{header_row}", ST_TXT, h, is_str=True)
                      for c, h in zip(cols, ["一", "二", "三", "四", "五"]))
            + cell(f"F{header_row}", ST_TXT, "週損益", is_str=True) + '</row>']

    for w, week in enumerate(cal["weeks"]):
        date_row = start_row + 2 + 3 * w
        amt_row, pct_row = date_row + 1, date_row + 2
        date_parts, amt_parts, pct_parts = [], [], []
        for c, day in zip(cols, week["days"]):
            if day is None:
                date_parts.append(cell(f"{c}{date_row}", ST_TXT))
                amt_parts.append(cell(f"{c}{amt_row}", ST_NUM))
                pct_parts.append(cell(f"{c}{pct_row}", ST_PCT))
            else:
                date_parts.append(cell(f"{c}{date_row}", ST_TXT, day["day"]))
                amt_parts.append(cell(f"{c}{amt_row}", ST_NUM,
                                       f"{day['amt']:.0f}" if day["amt"] is not None else None))
                pct_parts.append(cell(f"{c}{pct_row}", ST_PCT,
                                       f"{day['pct']:.10g}" if day["pct"] is not None else None))
        amt_parts.append(cell(f"F{amt_row}", ST_NUM,
                               f"{week['week_amt']:.0f}" if week["week_amt"] is not None else None))
        pct_parts.append(cell(f"F{pct_row}", ST_PCT,
                               f"{week['week_pct']:.10g}" if week["week_pct"] is not None else None))
        rows.append(f'<row r="{date_row}">' + ''.join(date_parts) + '</row>')
        rows.append(f'<row r="{amt_row}">' + ''.join(amt_parts) + '</row>')
        rows.append(f'<row r="{pct_row}">' + ''.join(pct_parts) + '</row>')

    last_row = start_row + 2 + 3 * len(cal["weeks"]) - 1
    # 只回傳裸的 <mergeCell/>(不含外層 <mergeCells> 包裝):OOXML 一張分頁只能有一個 <mergeCells>
    # 元素,多個月曆區塊要合併進同一個 <mergeCells count="N"> 裡,由呼叫端負責組裝
    merge_cell_xml = f'<mergeCell ref="A{title_row}:C{title_row}"/>'

    sqref_parts = [f"E{title_row}", f"F{title_row}"]
    for w in range(len(cal["weeks"])):
        amt_row = start_row + 3 + 3 * w
        sqref_parts += [f"A{amt_row}:F{amt_row}", f"A{amt_row + 1}:F{amt_row + 1}"]
    condfmt_xml = (f'<conditionalFormatting sqref="{" ".join(sqref_parts)}">'
                   '<cfRule type="cellIs" dxfId="4" priority="1" operator="greaterThan"><formula>0</formula></cfRule>'
                   '<cfRule type="cellIs" dxfId="0" priority="2" operator="lessThan"><formula>0</formula></cfRule>'
                   '</conditionalFormatting>')
    return "".join(rows), merge_cell_xml, condfmt_xml, last_row


def _build_calendar_bar_chart_xml(cal, marker="本月逐日損益(長條圖)", tab=None, start_row=None):
    """依 _month_calendar_grid() 的 all_days,組逐日損益(D欄口徑)長條圖。正值紅色長條、負值綠色
    長條(逐點 c:dPt 上色,沿用第3節同一組色碼);無資料日(未來日/休市)直接不輸出對應 <c:pt>,長條圖
    自然出現缺口。marker 是固定不隨月份變動的「欄位」標識字串(例如「本月」/「上月」),供
    _find_chart_by_marker 動態辨識這張圖屬於哪個欄位用,顯示在圖表標題上,同一欄位每次呼叫都要傳
    相同 marker,不可隨實際月份改變(否則下次找不到既有圖表,會誤判成需要 bootstrap 新增)。
    tab/start_row:資料改用「多區域儲存格參照」(numRef/strRef,c:f 用括號逗號連接6週的日期列/金額
    列範圍)直接指向月曆表格自己的儲存格,而非早期版本用的 numLit/strLit 純字面資料——2026-08-22
    實測發現 numLit/strLit 版本在 Jimmy 用真正 Excel 開檔後會被判定不完整而整張圖被拿掉(既有折線圖
    因為是 numRef/strRef 接真實儲存格範圍,同一次開檔完全沒事,足以佐證);改真實參照後 Excel 才會
    正常辨識並保留此圖表。"""
    from xml.sax.saxutils import escape as _esc
    days = cal["all_days"]
    cats = [f"{d.month:02d}/{d.day:02d}" for d, _ in days]
    vals = [a for _, a in days]

    date_rows = [start_row + 2 + 3 * w for w in range(len(cal["weeks"]))]
    amt_rows = [r + 1 for r in date_rows]
    cat_f = "(" + ",".join(f"'{tab}'!$A${r}:$E${r}" for r in date_rows) + ")"
    val_f = "(" + ",".join(f"'{tab}'!$A${r}:$E${r}" for r in amt_rows) + ")"

    cat_pts = "".join(f'<c:pt idx="{i}"><c:v>{_esc(c)}</c:v></c:pt>' for i, c in enumerate(cats))
    val_pts = "".join(f'<c:pt idx="{i}"><c:v>{v:.0f}</c:v></c:pt>'
                       for i, v in enumerate(vals) if v is not None)
    dpts = "".join(
        f'<c:dPt><c:idx val="{i}"/><c:invertIfNegative val="0"/><c:bubble3D val="0"/>'
        f'<c:spPr><a:solidFill><a:srgbClr val="{"E11D48" if v > 0 else "059669"}"/></a:solidFill></c:spPr></c:dPt>'
        for i, v in enumerate(vals) if v is not None)

    AXC, AXV = 555555555, 666666666
    ser = (f'<c:ser><c:idx val="0"/><c:order val="0"/><c:tx><c:v>單日損益漲跌(真實)</c:v></c:tx>'
           f'{dpts}<c:cat><c:strRef><c:f>{_esc(cat_f)}</c:f>'
           f'<c:strCache><c:ptCount val="{len(cats)}"/>{cat_pts}</c:strCache></c:strRef></c:cat>'
           f'<c:val><c:numRef><c:f>{_esc(val_f)}</c:f><c:numCache><c:formatCode>#,##0</c:formatCode>'
           f'<c:ptCount val="{len(vals)}"/>{val_pts}</c:numCache></c:numRef></c:val></c:ser>')
    bar = (f'<c:barChart><c:barDir val="col"/><c:grouping val="clustered"/><c:varyColors val="1"/>'
           f'{ser}<c:gapWidth val="50"/><c:axId val="{AXC}"/><c:axId val="{AXV}"/></c:barChart>')
    catAx = (f'<c:catAx><c:axId val="{AXC}"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
             f'<c:delete val="0"/><c:axPos val="b"/>'
             f'<c:txPr><a:bodyPr rot="-2700000" vert="horz"/><a:lstStyle/>'
             f'<a:p><a:pPr><a:defRPr sz="700"/></a:pPr><a:endParaRPr lang="zh-TW"/></a:p></c:txPr>'
             f'<c:crossAx val="{AXV}"/><c:crosses val="autoZero"/><c:auto val="1"/>'
             f'<c:lblAlgn val="ctr"/><c:lblOffset val="100"/><c:noMultiLvlLbl val="0"/></c:catAx>')
    valAx = (f'<c:valAx><c:axId val="{AXV}"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
             f'<c:delete val="0"/><c:axPos val="l"/><c:majorGridlines/>'
             f'<c:numFmt formatCode="#,##0" sourceLinked="0"/><c:majorTickMark val="out"/>'
             f'<c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/>'
             f'<c:crossAx val="{AXC}"/><c:crosses val="autoZero"/><c:crossBetween val="between"/></c:valAx>')
    title_xml = ('<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/>'
                 '<a:p><a:pPr><a:defRPr sz="1200" b="1"/></a:pPr>'
                 f'<a:r><a:rPr lang="zh-TW" sz="1200" b="1"/><a:t>{_esc(marker)}</a:t></a:r></a:p>'
                 '</c:rich></c:tx><c:overlay val="0"/></c:title>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<c:roundedCorners val="0"/><c:chart>' + title_xml
            + '<c:autoTitleDeleted val="0"/><c:plotArea><c:layout/>' + bar + catAx + valAx
            + '</c:plotArea><c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart></c:chartSpace>')


BAR_CHART_ANCHOR_COL = 7   # H欄(0-indexed);長條圖左上角固定對齊在該月報酬日曆的標題列
                            # (2026-08-27 Jimmy 要求,連同 03_國泰漲跌 一起套用)


def _build_bar_chart_anchor_xml(rid, from_row, col=BAR_CHART_ANCHOR_COL):
    """組長條圖首次(bootstrap)加入 drawing3.xml 用的 oneCellAnchor 區塊。(col, from_row) 皆
    0-indexed,呼叫端傳「該月報酬日曆標題列 - 1」,讓長條圖左上角對齊標題列、H 欄。"""
    cnv_id = 500000000 + from_row
    return (f'<xdr:oneCellAnchor><xdr:from><xdr:col>{col}</xdr:col><xdr:colOff>0</xdr:colOff>'
            f'<xdr:row>{from_row}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
            f'<xdr:ext cx="6858000" cy="3600000"/>'
            f'<xdr:graphicFrame><xdr:nvGraphicFramePr>'
            f'<xdr:cNvPr id="{cnv_id}" name="每日漲跌月曆長條圖"/><xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr>'
            f'<xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm>'
            f'<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
            f'<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
            f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="{rid}"/>'
            f'</a:graphicData></a:graphic></xdr:graphicFrame><xdr:clientData/></xdr:oneCellAnchor>')


def _update_calendar_bar_anchor(drawing_xml, rid, from_row, col=BAR_CHART_ANCHOR_COL):
    """把 drawing3.xml 裡「圖表rId=rid」那個錨點的 <xdr:from> 更新到(col, from_row)(皆 0-indexed)。
    月曆表格每天隨16_資產歷史列數增長往下移(跟現有每日表格同一套「整段重建」邏輯),長條圖錨點
    必須跟著同步移動到對應標題列、固定 H 欄——這是每次 --full 都要做的動作,不是只在首次建立時。"""
    pattern = re.compile(rf'<xdr:oneCellAnchor>(?:(?!</xdr:oneCellAnchor>).)*?'
                          rf'<c:chart[^>]*r:id="{rid}"/>.*?</xdr:oneCellAnchor>', re.S)
    m = pattern.search(drawing_xml)
    if not m:
        sys.exit(f"drawing3.xml 找不到 rId={rid} 的錨點,無法更新長條圖位置,版面可能已改。")
    block = m.group(0)
    new_from = (f'<xdr:from><xdr:col>{col}</xdr:col><xdr:colOff>0</xdr:colOff>'
                f'<xdr:row>{from_row}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>')
    new_block = re.sub(r'<xdr:from>.*?</xdr:from>', new_from, block, count=1, flags=re.S)
    return drawing_xml[:m.start()] + new_block + drawing_xml[m.end():]


def _ensure_style_variant(styles_xml, base_id, fill_id=None, font_id=None):
    """複製 cellXfs[base_id],可同時替換 fillId／fontId,append-only、冪等。"""
    m = re.search(r'(<cellXfs count=")(\d+)(">)(.*?)(</cellXfs>)', styles_xml, re.S)
    count, body = int(m.group(2)), m.group(4)
    xfs = re.findall(r'<xf\b[^>]*/>|<xf\b[^>]*>.*?</xf>', body, re.S)
    target = xfs[base_id]
    if fill_id is not None:
        target = re.sub(r'fillId="\d+"', f'fillId="{fill_id}"', target)
        if 'applyFill=' not in target:
            target = target.replace('<xf ', '<xf applyFill="1" ', 1)
    if font_id is not None:
        target = re.sub(r'fontId="\d+"', f'fontId="{font_id}"', target)
        if 'applyFont=' not in target:
            target = target.replace('<xf ', '<xf applyFont="1" ', 1)
    for i, xf in enumerate(xfs):
        if xf == target:
            return styles_xml, i
    new_xml = (styles_xml[:m.start()] + f'<cellXfs count="{count + 1}">' + body + target
               + '</cellXfs>' + styles_xml[m.end():])
    return new_xml, count


def _get_xf_font_id(styles_xml, xf_id):
    m = re.search(r'(<cellXfs count=")(\d+)(">)(.*?)(</cellXfs>)', styles_xml, re.S)
    xfs = re.findall(r'<xf\b[^>]*/>|<xf\b[^>]*>.*?</xf>', m.group(4), re.S)
    fm = re.search(r'fontId="(\d+)"', xfs[xf_id])
    return int(fm.group(1)) if fm else 0


def _ensure_font_color(styles_xml, base_font_id, rgb, bold=True):
    """複製 fonts[base_font_id],把文字顏色換成 rgb、視需要加粗,append-only、冪等。"""
    m = re.search(r'(<fonts count=")(\d+)(">)(.*?)(</fonts>)', styles_xml, re.S)
    count, body = int(m.group(2)), m.group(4)
    fonts = re.findall(r'<font>.*?</font>|<font/>', body, re.S)
    base = fonts[base_font_id] if base_font_id < len(fonts) else '<font/>'
    if base == '<font/>':
        base = '<font></font>'
    if re.search(r'<color[^/]*/>', base):
        target = re.sub(r'<color[^/]*/>', f'<color rgb="{rgb}"/>', base, count=1)
    else:
        target = base.replace('<font>', f'<font><color rgb="{rgb}"/>', 1)
    if bold and '<b/>' not in target:
        target = target.replace('<font>', '<font><b/>', 1)
    for i, f in enumerate(fonts):
        if f == target:
            return styles_xml, i
    new_xml = (styles_xml[:m.start()] + f'<fonts count="{count + 1}">' + body + target
               + '</fonts>' + styles_xml[m.end():])
    return new_xml, count


def _ensure_font_size(styles_xml, base_font_id, size):
    """複製 fonts[base_font_id],把字級換成 size,append-only、冪等。"""
    m = re.search(r'(<fonts count=")(\d+)(">)(.*?)(</fonts>)', styles_xml, re.S)
    count, body = int(m.group(2)), m.group(4)
    fonts = re.findall(r'<font>.*?</font>|<font/>', body, re.S)
    base = fonts[base_font_id] if base_font_id < len(fonts) else '<font/>'
    if base == '<font/>':
        base = '<font></font>'
    if re.search(r'<sz val="[^"]*"/>', base):
        target = re.sub(r'<sz val="[^"]*"/>', f'<sz val="{size}"/>', base, count=1)
    else:
        target = base.replace('<font>', f'<font><sz val="{size}"/>', 1)
    for i, f in enumerate(fonts):
        if f == target:
            return styles_xml, i
    new_xml = (styles_xml[:m.start()] + f'<fonts count="{count + 1}">' + body + target
               + '</fonts>' + styles_xml[m.end():])
    return new_xml, count


def _apply_cell_font_sizes(daily_sheetdata, styles_xml, size_overrides):
    """size_overrides: [(cell_ref, size), ...]。找出各儲存格目前實際在用的樣式,動態複製
    並只換字級(其餘屬性不動),append-only、冪等,不影響其他共用同一樣式的儲存格。
    (2026-08-29 Jimmy 手動示範過字級要多少,寫進規則讓每次自動重建都保留)"""
    for ref, size in size_overrides:
        m = re.search(rf'<c r="{ref}" s="(\d+)"', daily_sheetdata)
        if not m:
            continue
        cur_style = int(m.group(1))
        font_id = _get_xf_font_id(styles_xml, cur_style)
        styles_xml, new_font_id = _ensure_font_size(styles_xml, font_id, size)
        styles_xml, new_style_id = _ensure_style_variant(styles_xml, cur_style, font_id=new_font_id)
        daily_sheetdata = daily_sheetdata.replace(
            f'<c r="{ref}" s="{cur_style}"', f'<c r="{ref}" s="{new_style_id}"', 1)
    return daily_sheetdata, styles_xml


def _ensure_numfmt(styles_xml, code):
    """確保 xl/styles.xml 有一個自訂數字格式碼(如 "0.0%"),append-only、冪等,回傳 numFmtId。"""
    m = re.search(r'(<numFmts count=")(\d+)(">)(.*?)(</numFmts>)', styles_xml, re.S)
    fmts = re.findall(r'<numFmt [^>]*/>', m.group(4))
    for f in fmts:
        if re.search(rf'formatCode="{re.escape(code)}"', f):
            return styles_xml, int(re.search(r'numFmtId="(\d+)"', f).group(1))
    existing_ids = [int(re.search(r'numFmtId="(\d+)"', f).group(1)) for f in fmts]
    new_id = max(existing_ids, default=163) + 1
    new_fmt = f'<numFmt numFmtId="{new_id}" formatCode="{code}"/>'
    new_xml = (styles_xml[:m.start()] + f'<numFmts count="{int(m.group(2)) + 1}">' + m.group(4)
               + new_fmt + '</numFmts>' + styles_xml[m.end():])
    return new_xml, new_id


def _ensure_numfmt_style(styles_xml, base_id, numfmt_id):
    """複製 cellXfs[base_id],只把 numFmtId 換成 numfmt_id,append-only、冪等。"""
    m = re.search(r'(<cellXfs count=")(\d+)(">)(.*?)(</cellXfs>)', styles_xml, re.S)
    count, body = int(m.group(2)), m.group(4)
    xfs = re.findall(r'<xf\b[^>]*/>|<xf\b[^>]*>.*?</xf>', body, re.S)
    base = xfs[base_id]
    target = re.sub(r'numFmtId="\d+"', f'numFmtId="{numfmt_id}"', base)
    if 'applyNumberFormat=' not in target:
        target = target.replace('<xf ', '<xf applyNumberFormat="1" ', 1)
    for i, xf in enumerate(xfs):
        if xf == target:
            return styles_xml, i
    new_xml = (styles_xml[:m.start()] + f'<cellXfs count="{count + 1}">' + body + target
               + '</cellXfs>' + styles_xml[m.end():])
    return new_xml, count


def _fix_daily_value_font_color(daily_sheetdata, hist_rows, styles_xml, rgb="FF000000",
                                 data_start=None, data_end=None):
    """每日漲跌表 B~F 欄(總市值/未實現損益/單日損益漲跌/淨投入資金/單日市值漲跌)固定共用
    _build_daily_sheetdata() 寫死的樣式 s="9"(ST_NUM)。這個樣式的實際顏色會隨 styles.xml
    漂移。這裡動態讀「9」目前的字型,只把文字顏色強制改成 rgb,填色/框線/數字格式/粗體都不動,
    且只作用在每日表格的列範圍(預設 3 ~ 2+len(hist_rows);報酬日曆搬到表格上方後列號會
    不一樣,呼叫端可用 data_start/data_end 明確指定),不影響報酬日曆共用同組欄名的儲存格。"""
    base_font_id = _get_xf_font_id(styles_xml, 9)
    styles_xml, black_font_id = _ensure_font_color(styles_xml, base_font_id, rgb, bold=False)
    styles_xml, black_style_id = _ensure_style_variant(styles_xml, 9, font_id=black_font_id)
    start = 3 if data_start is None else data_start
    end = (2 + len(hist_rows)) if data_end is None else data_end
    for dr in range(start, end + 1):
        for col in "BCDEF":
            daily_sheetdata = daily_sheetdata.replace(
                f'<c r="{col}{dr}" s="9"', f'<c r="{col}{dr}" s="{black_style_id}"')
    return daily_sheetdata, styles_xml


def _highlight_calendar_titles(daily_sheetdata, title_slots, styles_xml, fill_rgb="FFFFFF00"):
    """把報酬日曆標題儲存格(A欄,「YYYY年M月報酬日曆」那一格)填滿黃色、文字改黑色加粗。
    title_slots: [(title_style_id, start_row), ...]。回傳(新 sheetdata, 新 styles_xml)。"""
    styles_xml, fill_id = _ensure_solid_fill(styles_xml, fill_rgb)
    for title_style_id, start_row in title_slots:
        base_font_id = _get_xf_font_id(styles_xml, title_style_id)
        styles_xml, black_font_id = _ensure_font_color(styles_xml, base_font_id, "FF000000", bold=True)
        styles_xml, yellow_id = _ensure_style_variant(styles_xml, title_style_id,
                                                        fill_id=fill_id, font_id=black_font_id)
        daily_sheetdata = daily_sheetdata.replace(
            f'<c r="A{start_row}" s="{title_style_id}" t="inlineStr">',
            f'<c r="A{start_row}" s="{yellow_id}" t="inlineStr">', 1)
    return daily_sheetdata, styles_xml


# 報酬日曆裡手動加註的「當天備註」(用日期定位,不用儲存格座標,因為月曆區塊每天都會往下
# 移動)。例如要在 8/15 那格寫「賣出xx股」＋黃底,格式:"YYYY/MM/DD": "顯示文字"。
# 比照 03_國泰漲跌 的 CATHAY_DAY_NOTES,兩邊各自獨立、互不影響。
# (2026-08-29 Jimmy 要求先建好機制,目前是空的,之後要加註再跟我說)
MAIN_DAY_NOTES = {}


def _apply_calendar_day_notes(daily_sheetdata, placed, styles_xml, notes):
    """把 notes 裡的手動備註,依日期(而非儲存格座標)重新套用到對應的月曆日期格,
    文字＋黃底都保留,每次整段重寫都會自動反查該日期現在落在哪一格。
    placed: [(marker, cal, start_row, last_row, title_style_id, txt_style_id), ...]。
    回傳(新 sheetdata, 新 styles_xml)。"""
    if not notes:
        return daily_sheetdata, styles_xml

    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    styles_xml, fill_id = _ensure_solid_fill(styles_xml, "FFFFFF00")
    cols = "ABCDE"
    for date_str, note_text in notes.items():
        d = datetime.strptime(date_str, "%Y/%m/%d").date()
        for marker, g, start_row, last_row, title_style_id, txt_style_id in placed:
            if g["year"] != d.year or g["month"] != d.month:
                continue
            for w, week in enumerate(g["weeks"]):
                for ci, day in enumerate(week["days"]):
                    if not day or day["day"] != d.day:
                        continue
                    col = cols[ci]
                    date_row = start_row + 2 + 3 * w
                    styles_xml, yellow_id = _ensure_style_variant(styles_xml, txt_style_id, fill_id=fill_id)
                    old_cell = f'<c r="{col}{date_row}" s="{txt_style_id}"><v>{day["day"]}</v></c>'
                    new_cell = (f'<c r="{col}{date_row}" s="{yellow_id}" t="inlineStr">'
                                f'<is><t>{esc(note_text)}</t></is></c>')
                    if old_cell in daily_sheetdata:
                        daily_sheetdata = daily_sheetdata.replace(old_cell, new_cell, 1)
                    else:
                        print(f"  ⚠ 找不到 {date_str} 對應的月曆儲存格({col}{date_row}),備註未套用,版面可能已改。")
    return daily_sheetdata, styles_xml


def _ensure_solid_fill(styles_xml, rgb):
    """確保 xl/styles.xml 的 <fills> 有一組指定顏色的實心填滿,append-only、冪等。"""
    target = f'<fill><patternFill patternType="solid"><fgColor rgb="{rgb}"/><bgColor rgb="{rgb}"/></patternFill></fill>'
    m = re.search(r'(<fills count=")(\d+)(">)(.*?)(</fills>)', styles_xml, re.S)
    count, body = int(m.group(2)), m.group(4)
    fills = re.findall(r'<fill>.*?</fill>', body, re.S)
    for i, f in enumerate(fills):
        if f == target:
            return styles_xml, i
    new_xml = (styles_xml[:m.start()] + f'<fills count="{count + 1}">' + body + target
               + '</fills>' + styles_xml[m.end():])
    return new_xml, count


def _ensure_readable_text_style(styles_xml, base_id, fill_rgb="FFFFFFFF", font_rgb="FF000000"):
    """複製 cellXfs[base_id],強制套用明確的白底＋黑字(而非「無填色」＋主題相對色 theme="1")。
    問題:手機版 Google 試算表切成暗色主題時,「無填色」的儲存格會顯示成暗色背景,原本
    theme="1" 的字色在多數主題裡是深色文字,疊在暗色背景上就看不到。改成明確的白底＋黑字後,
    不管檢視者用亮色還是暗色主題,App 都會照著明確設定顯示白底黑字,永遠讀得到。
    保留原本的框線/數字格式/對齊,只換填色跟字色。append-only、冪等。
    (2026-08-27 Jimmy 從 iPhone 暗色主題檢視時發現看不清楚,先在 02_每日漲跌 套用)"""
    styles_xml, fill_id = _ensure_solid_fill(styles_xml, fill_rgb)
    base_font_id = _get_xf_font_id(styles_xml, base_id)
    styles_xml, font_id = _ensure_font_color(styles_xml, base_font_id, font_rgb, bold=False)
    styles_xml, safe_id = _ensure_style_variant(styles_xml, base_id, fill_id=fill_id, font_id=font_id)
    return styles_xml, safe_id


def _ensure_centered_style(styles_xml, base_id):
    """複製 cellXfs[base_id],確保水平置中(horizontal="center"),保留其他既有屬性(字型/填色/
    框線/垂直對齊都不動),append-only、冪等。"""
    m = re.search(r'(<cellXfs count=")(\d+)(">)(.*?)(</cellXfs>)', styles_xml, re.S)
    count, body = int(m.group(2)), m.group(4)
    xfs = re.findall(r'<xf\b[^>]*/>|<xf\b[^>]*>.*?</xf>', body, re.S)
    base = xfs[base_id]
    if '<alignment' in base:
        if 'horizontal=' in base:
            target = re.sub(r'horizontal="[^"]*"', 'horizontal="center"', base, count=1)
        else:
            target = re.sub(r'<alignment ', '<alignment horizontal="center" ', base, count=1)
    elif re.search(r'<xf\b[^>]*/>', base):
        target = re.sub(r'(<xf\b[^>]*)/>', r'\1><alignment horizontal="center"/></xf>', base)
    else:
        target = re.sub(r'(<xf\b[^>]*>)', r'\1<alignment horizontal="center"/>', base, count=1)
    if 'applyAlignment=' not in target:
        target = target.replace('<xf ', '<xf applyAlignment="1" ', 1)
    for i, xf in enumerate(xfs):
        if xf == target:
            return styles_xml, i
    new_xml = (styles_xml[:m.start()] + f'<cellXfs count="{count + 1}">' + body + target
               + '</cellXfs>' + styles_xml[m.end():])
    return new_xml, count


def _center_daily_title(daily_sheetdata, styles_xml, base_style_id=58, title_row=1):
    """每日漲跌表標題(ST_TITLE="58")改成 A{title_row}:G{title_row} 合併置中(2026-08-27
    Jimmy 要求,連同 03_國泰漲跌 一起套用)。title_row 預設 1;報酬日曆搬到表格上方後標題
    不再固定是第一列,呼叫端可指定實際列號。回傳(新 sheetdata, 新 styles_xml, mergeCell 參照字串)。"""
    styles_xml, centered_id = _ensure_centered_style(styles_xml, base_style_id)
    daily_sheetdata = daily_sheetdata.replace(
        f'<c r="A{title_row}" s="{base_style_id}" t="inlineStr">',
        f'<c r="A{title_row}" s="{centered_id}" t="inlineStr">', 1)
    return daily_sheetdata, styles_xml, f'<mergeCell ref="A{title_row}:G{title_row}"/>'


def _ensure_dxf(styles_xml, dxf_xml):
    """確保 xl/styles.xml 的 <dxfs> 有一組指定內容的差異化格式(條件式格式用),append-only、
    冪等,回傳 dxfId。dxf_xml 是完整的 <dxf>...</dxf> 字串。"""
    m = re.search(r'(<dxfs count=")(\d+)(">)(.*?)(</dxfs>)', styles_xml, re.S)
    count, body = int(m.group(2)), m.group(4)
    dxfs = re.findall(r'<dxf>.*?</dxf>|<dxf/>', body, re.S)
    for i, d in enumerate(dxfs):
        if d == dxf_xml:
            return styles_xml, i
    new_xml = (styles_xml[:m.start()] + f'<dxfs count="{count + 1}">' + body + dxf_xml
               + '</dxfs>' + styles_xml[m.end():])
    return new_xml, count



# 單日報酬率絕對值超過門檻時的漸層黃色(由深到淺;數值取自 Office 佈景主題「黃色」色階,
# 淺色的比例越高、代表門檻越低):5%=純黃、4%=偏淺、3%=最淺(2026-08-27 Jimmy 訂)
G_COLUMN_YELLOW_TIERS = (
    (0.05, "FFFFFF00"),   # 純黃(Yellow)
    (0.04, "FFFFE699"),   # 偏淺黃(Yellow, Lighter 60%)
    (0.03, "FFFFF2CC"),   # 最淺黃(Yellow, Lighter 80%)
)


def _g_column_condfmt(hist_rows, styles_xml, tiers=G_COLUMN_YELLOW_TIERS,
                       data_start=None, data_end=None):
    """每日漲跌表 G 欄(單日報酬率):正值紅字、負值綠字;絕對值超過門檻時額外整格填色,
    門檻越高顏色越深(見 G_COLUMN_YELLOW_TIERS,由高到低排列,各自 stopIfTrue,同一格只會套用
    命中的第一個門檻,不會疊加多層顏色)。回傳(conditionalFormatting XML 片段,新 styles_xml)。
    字色沿用報酬日曆既有的紅/綠色碼(FFDC2626/FF047857),只換字色不帶背景色,避免跟報酬日曆
    的「淺色底」風格混淆。(2026-08-27 Jimmy 要求,先在 03_國泰漲跌 驗證過,連同 02_每日漲跌
    一起套用;同日再度加開 3%/4% 兩層漸層黃)
    data_start/data_end:每日表格資料列範圍,預設 3~2+len(hist_rows);報酬日曆搬到表格上方後
    列號會不一樣,呼叫端可明確指定。"""
    styles_xml, red_dxf = _ensure_dxf(styles_xml, '<dxf><font><color rgb="FFDC2626"/></font></dxf>')
    styles_xml, green_dxf = _ensure_dxf(styles_xml, '<dxf><font><color rgb="FF047857"/></font></dxf>')
    start = 3 if data_start is None else data_start
    end = (2 + len(hist_rows)) if data_end is None else data_end
    sqref = f"G{start}:G{end}"
    rules = [f'<cfRule type="cellIs" dxfId="{red_dxf}" priority="1" operator="greaterThan"><formula>0</formula></cfRule>',
             f'<cfRule type="cellIs" dxfId="{green_dxf}" priority="2" operator="lessThan"><formula>0</formula></cfRule>']
    for i, (threshold, rgb) in enumerate(tiers):
        styles_xml, tier_dxf = _ensure_dxf(
            styles_xml,
            f'<dxf><fill><patternFill patternType="solid"><fgColor rgb="{rgb}"/>'
            f'<bgColor rgb="{rgb}"/></patternFill></fill></dxf>')
        rules.append(f'<cfRule type="cellIs" dxfId="{tier_dxf}" priority="{3 + i}" '
                     f'stopIfTrue="1" operator="notBetween">'
                     f'<formula>-{threshold}</formula><formula>{threshold}</formula></cfRule>')
    condfmt_xml = f'<conditionalFormatting sqref="{sqref}">' + "".join(rules) + '</conditionalFormatting>'
    return condfmt_xml, styles_xml


def _updown_color_condfmt(sqref, styles_xml):
    """幫指定範圍(可以是單一儲存格,如 "D24")套用「正紅負綠」條件式格式,沿用跟 G 欄
    同一組紅/綠色碼(_ensure_dxf 是 append-only、冪等,重複呼叫會重用同一個 dxfId,
    不會重複定義)。回傳(conditionalFormatting XML 片段,新 styles_xml)。
    (2026-08-29 Jimmy 要求先套在 D24)"""
    styles_xml, red_dxf = _ensure_dxf(styles_xml, '<dxf><font><color rgb="FFDC2626"/></font></dxf>')
    styles_xml, green_dxf = _ensure_dxf(styles_xml, '<dxf><font><color rgb="FF047857"/></font></dxf>')
    condfmt_xml = (
        f'<conditionalFormatting sqref="{sqref}">'
        f'<cfRule type="cellIs" dxfId="{red_dxf}" priority="1" operator="greaterThan"><formula>0</formula></cfRule>'
        f'<cfRule type="cellIs" dxfId="{green_dxf}" priority="2" operator="lessThan"><formula>0</formula></cfRule>'
        '</conditionalFormatting>')
    return condfmt_xml, styles_xml


# ========================= 00_正二分析 =========================
# 欄寬:A 日期 / B~F 數值 / 統計區右半 D~E 借用同組欄寬
LEV2_COLS = ('<cols><col customWidth="1" min="1" max="1" width="20.0"/>'
             '<col customWidth="1" min="2" max="6" width="14.0"/></cols>')


def _lev2_esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _lev2_cell(ref, s, v=None, is_str=False):
    """單格 XML;字串走 inlineStr(不動 sharedStrings,避免索引牽動其他分頁)。"""
    if v is None:
        return f'<c r="{ref}" s="{s}"/>'
    if is_str:
        return f'<c r="{ref}" s="{s}" t="inlineStr"><is><t>{_lev2_esc(v)}</t></is></c>'
    return f'<c r="{ref}" s="{s}"><v>{v:.10g}</v></c>'


def _build_lev2_sheetdata(data, st_title="58", st_hdr="5"):
    """組 00_正二分析 的 <cols>+<sheetData>。

    data: lev2_analysis.analyse() 的回傳(daily[] + stats{})。
    數值一律 s="0"(通用格式),讓小數完整顯示;標題/表頭沿用全檔既有樣式索引
    (cellXfs 是 workbook 級共用,故 02_每日漲跌 在用的索引在本分頁同樣有效)。
    """
    d, s = data["daily"], data["stats"]
    NUM = "0"
    rows = []

    def row(n, cells):
        rows.append(f'<row r="{n}">' + "".join(cells) + "</row>")

    def kv(n, col_k, col_v, key, val, is_str=False):
        return [_lev2_cell(f"{col_k}{n}", st_hdr, key, is_str=True),
                _lev2_cell(f"{col_v}{n}", NUM, val, is_str=is_str)]

    row(1, [_lev2_cell("A1", st_title,
                       "00_正二分析｜00631L 元大台灣50正2　vs　00663L 國泰臺灣加權正2",
                       is_str=True)])
    row(2, [_lev2_cell("A2", st_hdr,
                       f"期間 {s['start']} ~ {s['end']}（{s['n']} 個交易日）"
                       f"｜資料：TWSE 官方收盤價｜更新於 {s['generated']}", is_str=True)])

    # 第 4 列起:左半「連動與追蹤統計」(A|B)、右半「期間績效」(D|E)
    row(4, [_lev2_cell("A4", st_title, "── 連動與追蹤統計 ──", is_str=True),
            _lev2_cell("D4", st_title, "── 期間績效 ──", is_str=True)])
    stat_l = [("日報酬相關係數", s["corr"], False),
              ("Beta（631L 對 663L）", s["beta"], False),
              ("平均絕對追蹤差 (pp)", s["mad"], False),
              ("追蹤差標準差 (pp)", s["sd_diff"], False),
              ("同向天數比例 (%)", s["same_dir_pct"], False),
              ("00631L 較強天數", s["a_wins"], False),
              ("00663L 較強天數", s["b_wins"], False),
              ("單日最大偏離 (pp)", s["max_dev"]["diff"], False),
              ("最大偏離發生日", s["max_dev"]["date"], True)]
    stat_r = [("00631L 累積報酬 (%)", s["cum_a"], False),
              ("00663L 累積報酬 (%)", s["cum_b"], False),
              ("累積差距 (pp)", s["cum_gap"], False),
              ("00631L 年化波動 (%)", s["vol_a"], False),
              ("00663L 年化波動 (%)", s["vol_b"], False),
              ("上漲日 631L 日均 (%)", s["up_a"], False),
              ("上漲日 663L 日均 (%)", s["up_b"], False),
              ("下跌日 631L 日均 (%)", s["down_a"], False),
              ("下跌日 663L 日均 (%)", s["down_b"], False)]
    for i in range(9):
        n = 5 + i
        cells = kv(n, "A", "B", *stat_l[i][:2], is_str=stat_l[i][2])
        cells += kv(n, "D", "E", *stat_r[i][:2], is_str=stat_r[i][2])
        row(n, cells)

    # 第 16 列起:每日明細(最新在上,與 HTML 圖卡一致)
    row(16, [_lev2_cell("A16", st_title, "── 每日明細（最新在上）──", is_str=True)])
    hdr = ["日期", "00631L 收盤", "00631L 漲跌%", "00663L 收盤",
           "00663L 漲跌%", "差異 (pp)"]
    row(17, [_lev2_cell(f"{c}17", st_hdr, h, is_str=True)
             for c, h in zip("ABCDEF", hdr)])
    for i, x in enumerate(reversed(d)):
        n = 18 + i
        row(n, [_lev2_cell(f"A{n}", NUM, x["date"], is_str=True),
                _lev2_cell(f"B{n}", NUM, x["pa"]),
                _lev2_cell(f"C{n}", NUM, x["ra"]),
                _lev2_cell(f"D{n}", NUM, x["pb"]),
                _lev2_cell(f"E{n}", NUM, x["rb"]),
                _lev2_cell(f"F{n}", NUM, x["diff"])])

    return LEV2_COLS + "<sheetData>" + "".join(rows) + "</sheetData>", 17 + len(d)


def _refresh_lev2(zin, styles=("58", "5")):
    """回 (part, xml) 供 --full 併入 repl;抓不到資料或分頁不存在時回 (None, None)。

    Jimmy 手動建立的空白分頁可能沒有 <cols> 節點,故兩種情形都要能處理。
    """
    try:
        wbxml = zin.read("xl/workbook.xml").decode("utf-8")
        if f'name="{LEV2_TAB}"' not in wbxml:
            print(f"  ⚠ 找不到分頁「{LEV2_TAB}」,跳過正二分析。")
            return None, None
        import lev2_analysis
        data = lev2_analysis.analyse(lev2_analysis.fetch(LEV2_DAYS))
    except SystemExit as e:                      # fetch 查無資料會 sys.exit
        print(f"  ⚠ 正二分析取價失敗,跳過:{e}")
        return None, None
    except Exception as e:
        print(f"  ⚠ 正二分析失敗,跳過:{type(e).__name__}: {e}")
        return None, None

    part = _locate_sheet_part(zin, LEV2_TAB)
    xml = zin.read(part).decode("utf-8")
    body, last_row = _build_lev2_sheetdata(data, *styles)

    if "<cols>" in xml:
        xml = re.sub(r"<cols>.*?</sheetData>", body, xml, flags=re.S)
    else:                                        # 空白分頁:只換 sheetData,cols 一併補入
        xml = re.sub(r"<sheetData\s*/>|<sheetData>.*?</sheetData>", body, xml, flags=re.S)
    # dimension 一併更新,免得 Excel/Drive 預覽誤判資料範圍
    xml = re.sub(r'<dimension ref="[^"]*"/>', f'<dimension ref="A1:F{last_row}"/>', xml)
    st = data["stats"]
    print(f"  ✓ {LEV2_TAB}:{st['n']} 筆（{st['start']}~{st['end']}）"
          f" 相關 {st['corr']:.4f}｜累積差 {st['cum_gap']:+.2f} pp")
    return part, xml


# ========================= 06_阿良資產負債表 =========================
LEV2BAL_COLS = ('<cols><col customWidth="1" min="1" max="1" width="22.0"/>'
                '<col customWidth="1" min="2" max="9" width="14.0"/></cols>')


def _build_lev2bal_sheetdata(r, date_str, st_title="58", st_hdr="5"):
    """組 06_阿良資產負債表 的 <cols>+<sheetData>;r 為 lev2_balance.compute() 回傳。
    百分比一律 ×100 寫成數值(標籤註明 %),與 00_正二分析 同慣例。"""
    from lev2_balance import LABEL, code_of
    NUM = "0"
    rows = {}

    def put(ref, s, v, is_str=False):
        n = int(re.sub(r"\D", "", ref))
        if isinstance(v, float) and not is_str:
            v = round(v, 4)
        rows.setdefault(n, []).append(_lev2_cell(ref, s, v, is_str))

    def kv(n, ck, cv, key, val):
        put(f"{ck}{n}", st_hdr, key, True)
        put(f"{cv}{n}", NUM, val, isinstance(val, str))

    put("A1", st_title, f"06_阿良資產負債表｜正二人生資產負債表（自動重建 {date_str}）", True)
    put("A2", st_hdr, "資料：03_持股總表 現值 + 12_設定 參數｜本金槓桿只計信貸，房貸不計入", True)

    B = r["buckets"]
    put("A4", st_title, "── 資產負債（元）──", True)
    left = [("原型(β1) 市值", B["base"]), ("正二(β2) 市值", B["lev"]),
            ("防守(β0) 債券+現金", B["def"]), ("　其中現金", r["cash"]),
            ("股票市值合計", r["stock_mv"]), ("金融資產總額", r["total"]),
            ("信貸", r["loan"]), ("金融資產淨值", r["net"]),
            ("本金槓桿 (%)", r["leverage"] * 100), ("曝險 Beta (%)", r["beta_pct"] * 100),
            ("總曝險 (%)", r["total_exposure"] * 100),
            (f"股票市值 vs {r['prev'][0]}" if r["prev"] else "股票市值 vs 上月", r["mv_diff"]
             if r["mv_diff"] is not None else "（無上月快照）")]
    for i, (k, v) in enumerate(left):
        kv(5 + i, "A", "B", k, v)

    put("D4", st_title, "── 生活費與建議配置 ──", True)
    kv(5, "D", "E", "年花費", r["annual_spend"])
    kv(6, "D", "E", "生活費倍數 (年)", r["years"])
    kv(7, "D", "E", "建議配置代號", f"{r['tier_code']}（≥{r['tier']['years']:g} 年）")
    for c, h in zip("DEFG", ("配置", "實際 (%)", "建議 (%)", "應增減金額")):
        put(f"{c}9", st_hdr, h, True)
    for i, k in enumerate(("base", "lev", "def")):
        n = 10 + i
        put(f"D{n}", st_hdr, LABEL[k], True)
        put(f"E{n}", NUM, r["actual_w"][k] * 100)
        put(f"F{n}", NUM, r["target_w"][k] * 100)
        put(f"G{n}", NUM, r["rebalance"][k])
    a, t = r["actual_perf"], r["target_perf"]
    for n, (k, key) in enumerate((("Beta (%)", "beta"), ("5 年總報酬 (%)", "r5"),
                                  ("平均年化 (%)", "annual")), start=13):
        put(f"D{n}", st_hdr, k, True)
        put(f"E{n}", NUM, a[key] * 100)
        put(f"F{n}", NUM, t[key] * 100)

    n = 18
    put(f"A{n}", st_title, f"── 正二跌加碼梯（00631L 現價 {r['ladder_px']}）──"
        if r["ladder_px"] else "── 正二跌加碼梯（持股無 00631L）──", True)
    if r["ladder"]:
        for c, h in zip("ABCDE", ("跌幅 (%)", "00631L 價位", "加碼金額", "累計（含預金）", "約可買股數")):
            put(f"{c}{n + 1}", st_hdr, h, True)
        put(f"A{n + 2}", st_hdr, "預金", True)
        put(f"B{n + 2}", NUM, r["ladder_px"])
        put(f"C{n + 2}", NUM, r["reserve"])
        put(f"D{n + 2}", NUM, r["reserve"])
        for i, s in enumerate(r["ladder"]):
            m = n + 3 + i
            put(f"A{m}", NUM, s["drop"] * 100)
            put(f"B{m}", NUM, s["px"])
            put(f"C{m}", NUM, s["amt"])
            put(f"D{m}", NUM, s["cum"])
            put(f"E{m}", NUM, s["shares"])
        n = n + 3 + len(r["ladder"])
        ok = "足夠" if r["cash"] >= r["ladder_total"] else f"不足 {r['ladder_total'] - r['cash']:,.0f}"
        kv(n, "A", "B", "需求合計 vs 現金", f"{r['ladder_total']:,.0f} / {r['cash']:,.0f}（{ok}）")

    n += 2
    put(f"A{n}", st_title, "── 生活費年數 → 配置對照表 ──", True)
    hdr = ("年數下限", "代號", "原型 (%)", "正二 (%)", "防守 (%)", "Beta (%)", "5 年總報酬 (%)", "年化 (%)", "")
    for c, h in zip("ABCDEFGHI", hdr):
        if h:
            put(f"{c}{n + 1}", st_hdr, h, True)
    R5, BETA = r["r5"], r["beta"]
    for i, tr in enumerate(r["tiers"]):
        m = n + 2 + i
        w = {k: tr[k] for k in ("base", "lev", "def")}
        tot = sum(w[k] * R5[k] for k in w)
        bet = sum(w[k] * BETA[k] for k in w)
        put(f"A{m}", NUM, tr["years"])
        put(f"B{m}", NUM, code_of(tr), True)
        put(f"C{m}", NUM, w["base"] * 100)
        put(f"D{m}", NUM, w["lev"] * 100)
        put(f"E{m}", NUM, w["def"] * 100)
        put(f"F{m}", NUM, bet * 100)
        put(f"G{m}", NUM, tot * 100)
        put(f"H{m}", NUM, ((1 + tot) ** 0.2 - 1) * 100)
        if tr is r["tier"]:
            put(f"I{m}", st_hdr, "◀ 目前", True)
    last = n + 1 + len(r["tiers"])

    body = "".join(f'<row r="{k}">' + "".join(v) + "</row>" for k, v in sorted(rows.items()))
    return LEV2BAL_COLS + "<sheetData>" + body + "</sheetData>", last


def _refresh_lev2bal(zin, wb, S, date_str, styles=("58", "5")):
    """回 (part, xml, 摘要dict);分頁不存在/參數缺格/計算失敗時回 (None, None, None)。"""
    try:
        wbxml = zin.read("xl/workbook.xml").decode("utf-8")
        if f'name="{LEV2BAL_TAB}"' not in wbxml:
            print(f"  ⚠ 找不到分頁「{LEV2BAL_TAB}」,跳過正二資產負債表。")
            return None, None, None
        import lev2_balance
        cfg = lev2_balance.read_lev2_cfg(wb)
        holdings = [dict(name=d["name"], cat=d["cat"], sh=d["sh"], px=d["px"]) for d in S]
        r = lev2_balance.compute(holdings, cfg, lev2_balance.prev_month_mv(wb, date_str, HIST_TAB))
        body, last_row = _build_lev2bal_sheetdata(r, date_str, *styles)
    except ValueError as e:
        print(f"  ⚠ 正二資產負債表參數不完整,跳過:{e}")
        return None, None, None
    except Exception as e:
        print(f"  ⚠ 正二資產負債表失敗,跳過:{type(e).__name__}: {e}")
        return None, None, None

    part = _locate_sheet_part(zin, LEV2BAL_TAB)
    xml = zin.read(part).decode("utf-8")
    if "<cols>" in xml:
        xml = re.sub(r"<cols>.*?</sheetData>", body, xml, flags=re.S)
    else:
        xml = re.sub(r"<sheetData\s*/>|<sheetData>.*?</sheetData>", body, xml, flags=re.S)
    xml = re.sub(r'<dimension ref="[^"]*"/>', f'<dimension ref="A1:I{last_row}"/>', xml)
    print(f"  ✓ {LEV2BAL_TAB}:生活費 {r['years']:.1f} 年→建議 {r['tier_code']}"
          f"|本金槓桿 {r['leverage']:.0%}|總曝險 {r['total_exposure']:.0%}")
    return part, xml, r


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
    # 2026-08-27 Jimmy 要求(先在此分頁測試):
    #   1. A欄日期改由新到舊排序(最新在最上面)
    #   2. 報酬日曆整批搬到最上面——本月月曆+本月長條圖放最頂端,再來才是「每日總市值與損益
    #      變化」標題/表格/折線圖,再往下依序是上月、6月、5月...月曆(跟 03_國泰漲跌 一樣
    #      「往回堆疊到最早完整月份」邏輯,見 _month_seq_desc)
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

    month_seq = _month_seq_desc(hist_rows)   # [(y,m), ...] 由新到舊,排除最早零碎月份
    slots = []                               # [(marker, cal_dict), ...]
    for idx, (y, m) in enumerate(month_seq):
        g = _month_calendar_grid(hist_rows, y, m)
        if not (g and g["all_days"]):
            continue
        marker = ("本月逐日損益(長條圖)" if idx == 0 else
                  "上月逐日損益(長條圖)" if idx == 1 else f"{y}年{m}月逐日損益(長條圖)")
        slots.append((marker, g))

    base_styles_xml = zin.read("xl/styles.xml").decode("utf-8")
    cal_styles_xml = base_styles_xml
    # 手機版 Google 試算表切暗色主題時,「無填色」+主題相對字色(theme="1")的儲存格會變成
    # 暗底暗字看不到(2026-08-27 Jimmy 從 iPhone 反映)。改用明確白底黑字的安全版本樣式,
    # 取代日期欄/表頭(原樣式 5)、單日報酬率欄(pct_style)這兩組受影響的樣式
    cal_styles_xml, safe_txt_style = _ensure_readable_text_style(cal_styles_xml, 5)
    cal_styles_xml, safe_pct_style = _ensure_readable_text_style(cal_styles_xml, pct_style)
    # 報酬日曆的金額格(num 角色)沿用樣式9,但樣式9原本的字色是明確白字(配淺藍底)——這組
    # 白字配淺藍底本身在任何主題下都幾乎看不到,是每日表格 B~F 欄同一個舊問題,只是報酬
    # 日曆這邊之前沒有一併修過;這裡改成黑字、保留原本淺藍底(跟每日表格風格一致)
    _num_font_id = _get_xf_font_id(cal_styles_xml, 9)
    cal_styles_xml, _num_black_font = _ensure_font_color(cal_styles_xml, _num_font_id, "FF000000", bold=False)
    cal_styles_xml, safe_num_style = _ensure_style_variant(cal_styles_xml, 9, font_id=_num_black_font)
    blocks, merge_cells, condfmts = [], [], []
    placed = []      # (marker, cal, start_row, last_row, title_style_id, txt_style_id)
    row_cursor = 1

    def _emit_calendar(marker, g):
        nonlocal cal_styles_xml, row_cursor
        cal_styles_xml, cal_pct_numfmt = _ensure_numfmt(cal_styles_xml, "0.0%")
        cal_styles_xml, cal_pct_base = _ensure_numfmt_style(cal_styles_xml, safe_pct_style, cal_pct_numfmt)
        cal_styles_xml, style_map = _ensure_calendar_grid_styles(
            cal_styles_xml, {"title": 58, "txt": safe_txt_style, "num": safe_num_style, "pct": cal_pct_base},
            color=CAL_GRID_COLOR_CURRENT)
        start_row = row_cursor
        rows_xml, mc, cf, last_row = _build_calendar_rows(g, start_row, style_map)
        blocks.append(rows_xml); merge_cells.append(mc); condfmts.append(cf)
        placed.append((marker, g, start_row, last_row, style_map["title"], style_map["txt"]))
        row_cursor = last_row + 2

    # 1. 本月月曆(如果有)搬到最上面
    if slots:
        _emit_calendar(*slots[0])

    # 2. 每日總市值與損益變化:標題+表格(A欄新到舊排序)
    daily_title_row = row_cursor
    daily_rows_xml, daily_info = _build_daily_sheetdata_desc(
        hist_rows, safe_pct_style, daily_title_row, txt_style=safe_txt_style)
    blocks.append(daily_rows_xml)
    row_cursor = daily_info["base_row"] + 2

    # 3. 其餘月份月曆(上月、6月、5月...),維持原本相對順序
    for marker, g in slots[1:]:
        _emit_calendar(marker, g)

    daily_sheetdata = "".join(blocks)
    daily_sheetdata, cal_styles_xml = _fix_daily_value_font_color(
        daily_sheetdata, hist_rows, cal_styles_xml,
        data_start=daily_info["agg_row"], data_end=daily_info["base_row"])
    daily_sheetdata, cal_styles_xml, title_merge_ref = _center_daily_title(
        daily_sheetdata, cal_styles_xml, title_row=daily_title_row)
    merge_cells.append(title_merge_ref)
    # 個別儲存格字級微調:累計說明(A24)9級、表頭「單日損益漲跌」(D23)8級
    # (2026-08-29 Jimmy 手動示範過,寫進規則讓每次自動重建都保留)
    daily_sheetdata, cal_styles_xml = _apply_cell_font_sizes(
        daily_sheetdata, cal_styles_xml,
        [(f"A{daily_info['agg_row']}", 9), (f"D{daily_info['header_row']}", 8)])
    # G欄條件式格式(±3%/4%/5%黃底、正紅負綠)延伸到加總列(G24,2026-08-29 Jimmy 要求),
    # 不含 base_row(最早一天原始資料,G 欄本來就留空,沒有值可套用)
    g_condfmt_xml, cal_styles_xml = _g_column_condfmt(
        hist_rows, cal_styles_xml,
        data_start=daily_info["agg_row"], data_end=daily_info["data_end"])
    condfmts.append(g_condfmt_xml)
    # D24(加總列的累計損益漲跌):正紅負綠(2026-08-29 Jimmy 要求)
    d_agg_condfmt_xml, cal_styles_xml = _updown_color_condfmt(
        f"D{daily_info['agg_row']}", cal_styles_xml)
    condfmts.append(d_agg_condfmt_xml)
    if placed:
        # 每月報酬日曆標題儲存格:黃底＋黑字＋粗體(2026-08-27 Jimmy 要求,比照 03_國泰漲跌)
        daily_sheetdata, cal_styles_xml = _highlight_calendar_titles(
            daily_sheetdata, [(p[4], p[2]) for p in placed], cal_styles_xml)
        # 報酬日曆手動備註(MAIN_DAY_NOTES,目前是空的,比照 03_國泰漲跌 的機制,
        # 2026-08-29 Jimmy 要求先建好)
        daily_sheetdata, cal_styles_xml = _apply_calendar_day_notes(
            daily_sheetdata, placed, cal_styles_xml, MAIN_DAY_NOTES)
    daily_sheetdata = DAILY_COLS + '<sheetData>' + daily_sheetdata + '</sheetData>'
    dxml = re.sub(r"<cols>.*?</sheetData>", daily_sheetdata, dxml, flags=re.S)
    # mergeCells 必須排在 conditionalFormatting 之前(OOXML 節點順序規範),兩者都插在
    # </sheetData> 之後、<drawing> 之前;先清掉舊的(可能因月曆區塊數量改變而有殘留)
    # 再整批插入(冪等,不論有幾個月曆都正確)
    mergeCells_xml = f'<mergeCells count="{len(merge_cells)}">{"".join(merge_cells)}</mergeCells>'
    condfmt_xml = "".join(condfmts)
    for tag, block in (("mergeCells", mergeCells_xml), ("conditionalFormatting", condfmt_xml)):
        dxml = re.sub(rf"<{tag}[ >].*?</{tag}>", "", dxml, flags=re.S)
        dxml = dxml.replace("<drawing ", block + "<drawing ")

    # 00_正二分析:由 TWSE 抓 00631L/00663L 收盤重建整張 sheetData
    lev2_part, lev2_xml = _refresh_lev2(zin)
    # 06_阿良資產負債表:03 現值 + 12_設定 參數整張重建
    bal_part, bal_xml, bal = (_refresh_lev2bal(zin, wb, S, date_str) if LEV2BAL_ENABLED
                              else (None, None, None))

    repl = {p: x.encode("utf-8") for p, x in X.items()}
    repl["xl/sharedStrings.xml"] = sst.encode("utf-8")
    repl[daily_part] = dxml.encode("utf-8")
    if cal_styles_xml is not None:
        repl["xl/styles.xml"] = cal_styles_xml.encode("utf-8")
    new_parts = {}

    # 折線圖(總市值/未實現損益/單日損益漲跌真實):用內容指紋動態定位既有 chart 檔,取代寫死
    # "chart5.xml" 的舊判斷(該假設已證實會漂移:Excel 重新存檔會把 chart part 重新編號)
    chart_parts = _locate_chart_parts(zin, DAILY_TAB)
    line_chart_path = _find_chart_by_marker(zin, chart_parts["chart_by_rid"], "單日損益漲跌(真實)")
    if line_chart_path and len(hist_rows) > 1:
        repl[line_chart_path] = _build_daily_chart_xml_desc(
            hist_rows, DAILY_TAB, daily_info["header_row"],
            daily_info["data_start"], daily_info["data_end"]).encode("utf-8")

    # 報酬日曆長條圖:「本月」「上月」固定欄位(marker 字串不隨月份變動,只有資料變動)、
    # 更舊的月份用「YYYY年M月」固定欄位(該月份身分不會隨時間改變)。各自首次(bootstrap)
    # 新增 chart 檔+drawing anchor+rels+Content_Types,之後只覆寫內容並跟著對應月曆表格
    # 同步更新錨點列號。若同一次執行內多個都要 bootstrap,後面那個要看得到前面剛新增的
    # rId/chart 編號,故用區域變數手動串接狀態,不透過尚未寫出的 zip 檔案。
    dw_xml = zin.read(chart_parts["drawing_path"]).decode("utf-8")
    drels_xml = zin.read(chart_parts["drawing_rels_path"]).decode("utf-8")
    chart_by_rid = dict(chart_parts["chart_by_rid"])
    ct_xml = zin.read("[Content_Types].xml").decode("utf-8")
    ct_changed = False

    # 折線圖的錨點原本固定在檔案最上面(col=8/row=1),報酬日曆搬到最上面後改跟著每日表格
    # 標題列走(欄位維持原本的 I 欄=col 8,避開長條圖固定用的 H 欄),2026-08-27 Jimmy 要求
    if line_chart_path:
        line_rid = next((rid for rid, p in chart_by_rid.items() if p == line_chart_path), None)
        if line_rid:
            dw_xml = _update_calendar_bar_anchor(dw_xml, line_rid, daily_title_row - 1, col=8)

    def _apply_bar_slot(marker, cal_slot, anchor_row, cal_start_row):
        nonlocal dw_xml, drels_xml, chart_by_rid, ct_xml, ct_changed
        if not (cal_slot and cal_slot["all_days"]):
            return
        bar_xml = _build_calendar_bar_chart_xml(
            cal_slot, marker, tab=DAILY_TAB, start_row=cal_start_row).encode("utf-8")
        existing_path = None
        for p in chart_by_rid.values():
            content = new_parts.get(p) or (zin.read(p) if p in zin.namelist() else None)
            if content and marker in content.decode("utf-8"):
                existing_path = p
                break
        if existing_path:
            repl[existing_path] = bar_xml
            bar_rid = next(rid for rid, p in chart_by_rid.items() if p == existing_path)
            dw_xml = _update_calendar_bar_anchor(dw_xml, bar_rid, anchor_row)
        else:
            existing_nums = [int(m.group(1)) for n in (set(zin.namelist()) | set(new_parts))
                              for m in [re.match(r"xl/charts/chart(\d+)\.xml$", n)] if m]
            new_chart_path = f"xl/charts/chart{max(existing_nums, default=0) + 1}.xml"
            new_parts[new_chart_path] = bar_xml
            existing_rids = [int(rm) for rm in re.findall(r'Id="rId(\d+)"', drels_xml)]
            new_rid = f"rId{max(existing_rids, default=0) + 1}"
            rel_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"
            new_rel = (f'<Relationship Id="{new_rid}" Type="{rel_type}" '
                       f'Target="../charts/{new_chart_path.split("/")[-1]}"/>')
            drels_xml = drels_xml.replace("</Relationships>", new_rel + "</Relationships>")
            dw_xml = dw_xml.replace(
                "</xdr:wsDr>", _build_bar_chart_anchor_xml(new_rid, anchor_row) + "</xdr:wsDr>")
            chart_by_rid[new_rid] = new_chart_path
            ct_override = ('<Override ContentType="application/vnd.openxmlformats-officedocument.'
                            f'drawingml.chart+xml" PartName="/{new_chart_path}"/>')
            ct_xml = ct_xml.replace("</Types>", ct_override + "</Types>")
            ct_changed = True
            print(f"  ⚙ [報酬日曆] 首次建立長條圖:新增 {new_chart_path}({marker})")

    # 長條圖左上角固定對齊該月報酬日曆的標題列、H欄(2026-08-27 Jimmy 要求),0-indexed 需減 1
    for marker, g, start_row, last_row, _title_style_id, _txt_style_id in placed:
        _apply_bar_slot(marker, g, start_row - 1, start_row)
    repl[chart_parts["drawing_path"]] = dw_xml.encode("utf-8")
    repl[chart_parts["drawing_rels_path"]] = drels_xml.encode("utf-8")
    if ct_changed:
        repl["[Content_Types].xml"] = ct_xml.encode("utf-8")

    if lev2_part:
        repl[lev2_part] = lev2_xml.encode("utf-8")
    if bal_part:
        repl[bal_part] = bal_xml.encode("utf-8")
    with zipfile.ZipFile(path_out, "w") as zout:
        for info in zin.infolist():
            data = repl.get(info.filename)
            if data is None:
                data = zin.read(info.filename)
            zout.writestr(info, data, compress_type=info.compress_type)
        for path, data in new_parts.items():
            zout.writestr(path, data)
    zin.close()

    # 自檢:平時(steady-state)namelist 應完全相同;首次建立長條圖那一次(bootstrap)允許
    # 新增在 new_parts 白名單內的成員,其餘一律不得增減
    from xml.dom import minidom
    za, zb = zipfile.ZipFile(path_in), zipfile.ZipFile(path_out)
    added = set(zb.namelist()) - set(za.namelist())
    removed = set(za.namelist()) - set(zb.namelist())
    assert not removed, f"--full:元件被移除!{removed}"
    assert added <= set(new_parts), f"--full:意外新增元件 {added - set(new_parts)}"
    changed = [n for n in za.namelist() if za.read(n) != zb.read(n)] + list(added)
    assert set(changed) <= set(repl) | set(new_parts), f"--full:意外變動 {set(changed) - (set(repl) | set(new_parts))}"
    for n in changed:
        minidom.parseString(zb.read(n))
    za.close(); zb.close()
    openpyxl.load_workbook(path_out, keep_vba=True)   # 可開檔驗證
    return changed, dict(F27=F27, hi=hi, lev=lev, mx=f"{mx_d['name']} {mx:.1%}", safety=safety,
                         bal=bal)


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
    date_str = getattr(args, "date", None) or f"{datetime.now():%Y/%m/%d}"

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
        if fsum.get("bal"):
            b = fsum["bal"]
            aw = "".join(str(int(round(b["actual_w"][k] * 10))) for k in ("base", "lev", "def"))
            print(f"[正二資產負債表] 生活費 {b['years']:.1f} 年|建議 {b['tier_code']} vs 實際 {aw}"
                  f"|總曝險 {b['total_exposure']:.0%}")
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
    up.add_argument("--date", help="指定要記錄的日期(預設今天),例 2026/8/28;"
                    "排程延遲跨過午夜時可明確指定,避免用系統時鐘today()誤判成隔天")
    args = ap.parse_args()
    if args.cmd != "update":
        ap.print_help()
        return
    sync(args)


if __name__ == "__main__":
    main()
