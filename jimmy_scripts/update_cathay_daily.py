#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""update_cathay_daily.py — 國泰帳戶每日市值/損益快照,寫入雲端 Wealth OS「21_國泰資產歷史」,
   並依此重建「03_國泰漲跌」(每日表格＋雙軸走勢圖＋報酬日曆月曆熱力圖／長條圖)。

觸發語:「更新國泰漲跌」

原理(比照 update_stock_price.py「更新股價」的精神):
  國泰帳戶股數/成本寫死在本檔 CATHAY_HOLDINGS(股數不變時完全自動),每次執行:
    1. 抓 TWSE 官方今日收盤價(twse_hist.close_on,gap=0,非交易日/尚無資料直接跳過不寫)
    2. 市值 = Σ(股數×收盤價);成本沿用 CATHAY_HOLDINGS 設定值;損益=市值-成本
    3. 寫入「21_國泰資產歷史」(同日重跑覆寫該列,否則附加新列,邏輯比照 16_資產歷史)
    4. 重建「03_國泰漲跌」整張 sheetData/圖表/報酬日曆(zip 手術式,比照 update_wealth_os.py --full)

⚠ 一旦國泰帳戶有買賣(股數/成本異動),要先手動更新本檔最上面的 CATHAY_HOLDINGS 常數,
  否則市值會用舊股數計算而失真。

固定格式規則(2026-09-08 起與「02_每日漲跌」完全一致——版面規則一律共用
update_wealth_os 的同一組函式,那邊改格式這邊會自動跟上,不再各自維護一份複製品。
_rebuild_daily_and_calendar() 每次執行都會強制套用,不管資料怎麼增加、Google Sheets
樣式索引怎麼漂移,結果都收斂到同一份規則,冪等):
  1. 版面由上到下:本月報酬日曆＋本月長條圖 →「每日總市值與損益變化」標題/表格＋折線圖
     → 上月、更早月份的報酬日曆(較舊的在下面)
  2. 每日表格 A 欄日期由新到舊(最新在最上面);「加總列」(累計)釘在表頭正下方,
     每天新資料插進它下一列;最早一天的原始資料獨立放在表格最後一列(base_row,D~G 留空)
     (uwo._build_daily_sheetdata_desc)
  3. A 欄(日期):一律是真日期數值＋日期樣式,樣式動態抓自 21_國泰資產歷史 A2
     (_hist_date_style,不寫死索引——寫死曾經因為 Google 重存檔漂移而顯示成序號數字)
  4. G 欄(單日報酬率):百分比樣式動態抓自 21_國泰資產歷史 E2(_hist_pct_style,同上不寫死)
  5. B~F 欄(總市值/損益/漲跌相關數字):文字強制黑色(uwo._fix_daily_value_font_color),
     不管共用樣式 s="9" 目前實際字色漂移成什麼顏色
  6. 手機暗色主題可讀性:日期欄/表頭/報酬率欄改用明確白底黑字的安全樣式
     (uwo._ensure_readable_text_style),不用「無填色＋主題相對字色」
  7. 報酬日曆由本月往回堆疊到最早有完整資料的月份(uwo._month_seq_desc,最早那筆若不是
     1 號才排除該零碎月份);月曆數量隨歷史資料增長自動變多,不用手動加
  8. 每月報酬日曆標題儲存格:黃底(FFFFFF00)＋黑字＋粗體(uwo._highlight_calendar_titles)
  9. 報酬日曆的漲跌百分比(日/週/月):固定用「0.0%」(四捨五入到小數點第一位),
     跟每日表 G 欄的百分比格式分開處理(cal_pct_base,不受規則 4 影響)
 10. 每月「逐日損益(長條圖)」左上角固定對齊該月報酬日曆的標題列、H 欄;折線圖錨點跟著
     每日表格標題列走(I 欄,避開長條圖用的 H 欄)
 11. 字級微調:加總列 A 欄(累計說明)9 級、表頭「單日損益漲跌」D 欄 8 級
     (uwo._apply_cell_font_sizes)
 12. 條件式格式:G 欄(加總列起算)正紅負綠＋絕對值超過 ±3/4/5% 三層黃底
     (uwo._g_column_condfmt);加總列 D 欄(累計損益漲跌)正紅負綠(uwo._updown_color_condfmt)
 13. CATHAY_DAY_NOTES 裡登記的手動備註(用日期定位,不用儲存格座標):
     每次重建都會重新算出該日期現在落在哪一格,把文字＋黃底套回去
     (uwo._apply_calendar_day_notes),不會被整表重建蓋掉;沒登記的日期/儲存格
     則不受保護,會照規則重算

用法:
  python3 update_cathay_daily.py update            # 預覽後輸入 yes 寫入
  python3 update_cathay_daily.py update --yes      # 不詢問直接寫入
  python3 update_cathay_daily.py update --dry-run  # 只預覽,不寫入不上傳
"""
import os
import sys
import re
import zipfile
import argparse
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twse_hist
import update_wealth_os as uwo
from googleapiclient.http import MediaFileUpload
import openpyxl

# ========================= 國泰帳戶目前持股(股數不變時自動,異動要手動改這裡) =========================
# 2026-08-26 依「證券未實現彙總」CSV 建立
# 2026-09-11 現買 00631L 500 股,淨收付 -17,872(含手續費):14000→14500 股、成本 391019→408891
CATHAY_HOLDINGS = {
    "0050":   {"shares": 500,   "cost": 36289},     # 元大台灣50
    "00631L": {"shares": 14500, "cost": 408891},    # 元大台灣50正2
    "00662":  {"shares": 500,   "cost": 61174},     # 富邦NASDAQ
    "00685L": {"shares": 17000, "cost": 200402},    # 群益臺灣加權正2
}
CATHAY_COST_TOTAL = sum(h["cost"] for h in CATHAY_HOLDINGS.values())

# 報酬日曆裡手動加註的「當天備註」(用日期定位,不用儲存格座標,因為月曆區塊每天都會往下移)。
# 例如 Jimmy 手動在 6/8 那格寫「8，匯入50萬」＋黃底,要在每次自動重建時都保留,格式:
#   "YYYY/MM/DD": "顯示文字"
CATHAY_DAY_NOTES = {
    "2026/06/08": "8，匯入50萬",
}

HIST_TAB = "21_國泰資產歷史"
DAILY_TAB = "03_國泰漲跌"
DAILY_TITLE = "03_國泰漲跌｜每日總市值與損益變化"
EXCEL_EPOCH = date(1899, 12, 30)


def fetch_today_snapshot(d):
    """回傳 (mv, cost, pl, ret) 或 None(非交易日/尚無收盤資料)。"""
    mv = 0.0
    for code, h in CATHAY_HOLDINGS.items():
        px, note = twse_hist.close_on(code, d, gap=0)
        if px is None:
            print(f"  ⚠ {code} 查無 {d} 收盤價({note}),視為非交易日,跳過。")
            return None
        mv += h["shares"] * px
    cost = CATHAY_COST_TOTAL
    pl = mv - cost
    ret = pl / cost if cost else 0
    return mv, cost, pl, ret


def _read_hist_rows(path):
    """讀「21_國泰資產歷史」現有資料列,回傳 [(日期字串YYYY/MM/DD, mv, cost, pl), ...]。
    A欄正常應是套了日期格式的真日期數值,openpyxl 會自動轉成 datetime;但若該列樣式漂移成
    非日期格式(2026-08-27 曾因 _upsert_hist_row 寫死樣式索引導致),openpyxl 只會給裸數字,
    這裡多一層防呆:抓到純數字就當 Excel 序號手動換算,不要整支腳本直接炸掉。"""
    wb = openpyxl.load_workbook(path, data_only=False)
    hws = wb[HIST_TAB]
    rows = []
    for r in range(2, hws.max_row + 1):
        dt = hws.cell(r, 1).value
        if dt is None or str(dt).strip() == "":
            continue
        if hasattr(dt, "strftime"):
            dt_str = dt.strftime("%Y/%m/%d")
        elif isinstance(dt, (int, float)):
            dt_str = (EXCEL_EPOCH + timedelta(days=int(dt))).strftime("%Y/%m/%d")
        else:
            dt_str = str(dt).replace("-", "/")[:10]
        rows.append((dt_str, float(hws.cell(r, 2).value), float(hws.cell(r, 3).value),
                     float(hws.cell(r, 4).value)))
    return rows


def _upsert_hist_row(hxml, date_str, mv, cost, pl, ret):
    """同日覆寫、否則附加一列到「21_國泰資產歷史」原始 XML。回傳 (新xml, 動作說明)。
    A~E 各欄樣式索引一律動態抓自現有第一筆資料列(row2),不可寫死——cellXfs 索引會隨 Google
    Sheets 重新存檔漂移。2026-08-27 曾因寫死 A欄=75/B~D欄=5/E欄=26,當時實際日期樣式已漂移
    成62(其餘欄漂移成2/25),導致寫入的日期欄不是日期格式,openpyxl 讀回來變成裸數字字串,
    後續 datetime.strptime 直接崩潰;修好後改成動態讀取,冪等且不受 styles.xml 變動影響。"""
    d = datetime.strptime(date_str, "%Y/%m/%d").date()
    serial = (d - EXCEL_EPOCH).days

    data_rows = []   # (列號, 日期序號, 原文, {欄字母: 樣式索引})
    for m in re.finditer(r'<row r="(\d+)"[^>]*>(.*?)</row>', hxml, re.S):
        rn, content = int(m.group(1)), m.group(2)
        if rn < 2:
            continue
        am = re.search(r'<c r="A\d+"[^>]*><v>([\d.]+)</v></c>', content)
        if not am:
            continue
        styles = dict(re.findall(r'<c r="([A-E])\d+" s="(\d+)"', content))
        data_rows.append((rn, float(am.group(1)), m.group(0), styles))
    if not data_rows:
        sys.exit(f"「{HIST_TAB}」找不到資料列,版面可能已改。")

    ref_style = data_rows[0][3]   # 以最早一筆(row2)的樣式為準,不受後面列可能已寫壞的樣式污染
    sA, sB, sC, sD, sE = (ref_style.get(c, fb) for c, fb in
                          zip("ABCDE", ("62", "2", "2", "2", "25")))

    def cell(ref, s, v):
        return f'<c r="{ref}" s="{s}"><v>{v}</v></c>'

    def build_row(n):
        return (f'<row r="{n}">' + cell(f"A{n}", sA, serial) + cell(f"B{n}", sB, round(mv))
                + cell(f"C{n}", sC, round(cost)) + cell(f"D{n}", sD, round(pl))
                + cell(f"E{n}", sE, f"{ret:.10g}") + '</row>')

    same_day = next((r for r in data_rows if int(r[1]) == serial), None)
    if same_day:
        new_row = build_row(same_day[0])
        return hxml.replace(same_day[2], new_row, 1), f"同日重跑,覆寫列{same_day[0]}({date_str})"
    n = max(r[0] for r in data_rows) + 1
    new_row = build_row(n)
    last = max(data_rows, key=lambda r: r[0])
    return hxml.replace(last[2], last[2] + new_row, 1), f"附加 {date_str} 至列{n}"


def _hist_date_style(zin, fallback=75):
    """讀 21_國泰資產歷史 A2 儲存格目前的樣式索引,當作日期欄該用的樣式。跟 _hist_pct_style
    同理:cellXfs 索引會隨 Google Sheets 重新存檔漂移,不可寫死,每次都要動態抓最新的。"""
    try:
        hist_part = uwo._locate_sheet_part(zin, HIST_TAB)
        hxml = zin.read(hist_part).decode("utf-8")
        m = re.search(r'<c r="A2" s="(\d+)"', hxml)
        return int(m.group(1)) if m else fallback
    except Exception:
        return fallback


def _fix_date_column_as_real_dates(daily_sheetdata, hist_rows, date_style, info):
    """uwo._build_daily_sheetdata_desc() 產出的 A 欄公式快取一律是文字(t="str",沿用
    16_資產歷史存文字日期的慣例)。21_國泰資產歷史 的 A 欄是 Google Sheets 轉存後的真日期
    數值,套用文字快取會讓 03_國泰漲跌 的日期欄顯示成序號文字而非日期格式。這裡把每一列
    A 欄的快取改成真數值(Excel 日期序號)＋日期樣式(date_style 動態抓自 21_國泰資產歷史,
    見 _hist_date_style,不寫死索引),公式本身不動,Excel/Sheets 開檔重算後型別跟顯示格式
    才會正確。
    列序是「新到舊」且加總列釘在表頭正下方,所以用 info(data_start/data_end/base_row)
    反查該列對應 hist_rows 的哪一筆;樣式索引也不再寫死 s="5"(呼叫端傳的是暗色主題安全
    樣式,索引是每次動態算出來的)。加總列 A 是 inlineStr(「累計YY/MM至今」)、月曆日期格是
    純數字,都不會被這條 regex 命中,再加上列範圍守衛,雙保險。"""
    N = len(hist_rows)

    def row_to_idx(dr):
        if dr == info["base_row"]:
            return 0                                     # 最早一天的原始資料列
        if info["data_start"] <= dr <= info["data_end"]:
            return (N - 1) - (dr - info["data_start"])   # 逐日列:新到舊
        return None

    def repl_cell(m):
        dr, formula = int(m.group(1)), m.group(2)
        i = row_to_idx(dr)
        if i is None or not (0 <= i < N):
            return m.group(0)
        d = datetime.strptime(hist_rows[i][0], "%Y/%m/%d").date()
        return (f'<c r="A{dr}" s="{date_style}"><f>{formula}</f>'
                f'<v>{(d - EXCEL_EPOCH).days}</v></c>')

    return re.sub(r'<c r="A(\d+)" s="\d+" t="str"><f>(.*?)</f><v>(.*?)</v></c>',
                  repl_cell, daily_sheetdata)



def _hist_pct_style(zin, fallback=26):
    """讀 21_國泰資產歷史 E2 儲存格目前的樣式索引,當作報酬率該用的樣式。cellXfs 索引會隨
    Google Sheets 重新存檔(或我們自己的 zip 手術)漂移,不可寫死,每次都要動態抓最新的。"""
    try:
        hist_part = uwo._locate_sheet_part(zin, HIST_TAB)
        hxml = zin.read(hist_part).decode("utf-8")
        m = re.search(r'<c r="E2" s="(\d+)"', hxml)
        return int(m.group(1)) if m else fallback
    except Exception:
        return fallback


def _rebuild_daily_and_calendar(zin, hist_rows, repl, new_parts):
    """重建 03_國泰漲跌 整張 sheetData(報酬日曆＋每日表格)＋折線圖＋長條圖。就地更新 repl/new_parts。
    版面規則完全比照 update_wealth_os.full_refresh() 的「02_每日漲跌」區塊(2026-09-08 Jimmy
    確認 02 的新版面滿意後同步過來):本月報酬日曆在最上面 → 每日表格(日期新到舊、加總列
    釘在表頭正下方、最早一天原始資料獨立放最後一列) → 上月與更早月份的報酬日曆。
    所有格式函式都直接用 uwo 的,不再各自維護複製品,以後那邊改這邊自動跟上。"""
    uwo.DAILY_TAB = DAILY_TAB
    uwo.HIST_TAB = HIST_TAB
    pct_style = _hist_pct_style(zin)

    daily_part = uwo._locate_sheet_part(zin, DAILY_TAB)
    dxml = zin.read(daily_part).decode("utf-8")

    # slots: [(marker, cal_dict), ...],由新到舊;前兩個沿用「本月/上月」固定標籤(隨時間滑動),
    # 第三個起用「YYYY年M月」固定標籤(該月份身分不會隨時間改變,不需要每次都重新辨識)
    slots = []
    for idx, (y, m) in enumerate(uwo._month_seq_desc(hist_rows)):
        g = uwo._month_calendar_grid(hist_rows, y, m)
        if not (g and g["all_days"]):
            continue
        if idx == 0:
            marker = "本月逐日損益(長條圖)"
        elif idx == 1:
            marker = "上月逐日損益(長條圖)"
        else:
            marker = f"{y}年{m}月逐日損益(長條圖)"
        slots.append((marker, g))

    cal_styles_xml = zin.read("xl/styles.xml").decode("utf-8")
    # 手機版 Google 試算表暗色主題下,「無填色」+主題相對字色的儲存格會變成暗底暗字看不到,
    # 改用明確白底黑字的安全樣式(日期欄/表頭=樣式5、報酬率欄=pct_style、月曆金額格=樣式9)
    cal_styles_xml, safe_txt_style = uwo._ensure_readable_text_style(cal_styles_xml, 5)
    cal_styles_xml, safe_pct_style = uwo._ensure_readable_text_style(cal_styles_xml, pct_style)
    _num_font_id = uwo._get_xf_font_id(cal_styles_xml, 9)
    cal_styles_xml, _num_black_font = uwo._ensure_font_color(cal_styles_xml, _num_font_id,
                                                             "FF000000", bold=False)
    cal_styles_xml, safe_num_style = uwo._ensure_style_variant(cal_styles_xml, 9,
                                                               font_id=_num_black_font)

    blocks, merge_cells, condfmts = [], [], []
    placed = []   # (marker, cal, start_row, last_row, title_style_id, txt_style_id)
    row_cursor = 1

    def _emit_calendar(marker, g):
        nonlocal cal_styles_xml, row_cursor
        # 報酬日曆的漲跌百分比固定用「0.0%」(四捨五入到小數點第一位),跟每日漲跌表 G 欄
        # (pct_style)分開處理,不影響那邊的顯示格式
        cal_styles_xml, cal_pct_numfmt = uwo._ensure_numfmt(cal_styles_xml, "0.0%")
        cal_styles_xml, cal_pct_base = uwo._ensure_numfmt_style(cal_styles_xml, safe_pct_style,
                                                                cal_pct_numfmt)
        cal_styles_xml, style_map = uwo._ensure_calendar_grid_styles(
            cal_styles_xml,
            {"title": 58, "txt": safe_txt_style, "num": safe_num_style, "pct": cal_pct_base},
            color=uwo.CAL_GRID_COLOR_CURRENT)
        start_row = row_cursor
        rows_xml, mc, cf, last_row = uwo._build_calendar_rows(g, start_row, style_map)
        blocks.append(rows_xml); merge_cells.append(mc); condfmts.append(cf)
        placed.append((marker, g, start_row, last_row, style_map["title"], style_map["txt"]))
        row_cursor = last_row + 2

    # 1. 本月報酬日曆搬到最上面
    if slots:
        _emit_calendar(*slots[0])

    # 2. 每日總市值與損益變化:標題+表格(日期新到舊、加總列釘在表頭正下方)
    daily_title_row = row_cursor
    daily_rows_xml, daily_info = uwo._build_daily_sheetdata_desc(
        hist_rows, safe_pct_style, daily_title_row, txt_style=safe_txt_style)
    blocks.append(daily_rows_xml)
    row_cursor = daily_info["base_row"] + 2

    # 3. 其餘月份報酬日曆(上月、更早…),維持原本相對順序
    for marker, g in slots[1:]:
        _emit_calendar(marker, g)

    daily_sheetdata = "".join(blocks)
    daily_sheetdata = daily_sheetdata.replace("02_每日漲跌｜每日總市值與損益變化", DAILY_TITLE)
    daily_sheetdata = _fix_date_column_as_real_dates(
        daily_sheetdata, hist_rows, _hist_date_style(zin), daily_info)
    daily_sheetdata, cal_styles_xml = uwo._fix_daily_value_font_color(
        daily_sheetdata, hist_rows, cal_styles_xml,
        data_start=daily_info["agg_row"], data_end=daily_info["base_row"])
    # 標題列合併置中(跟 02_每日漲跌 共用同一支函式)
    daily_sheetdata, cal_styles_xml, title_merge_ref = uwo._center_daily_title(
        daily_sheetdata, cal_styles_xml, title_row=daily_title_row)
    merge_cells.append(title_merge_ref)
    # 個別儲存格字級微調:加總列的累計說明 9 級、表頭「單日損益漲跌」8 級
    daily_sheetdata, cal_styles_xml = uwo._apply_cell_font_sizes(
        daily_sheetdata, cal_styles_xml,
        [(f"A{daily_info['agg_row']}", 9), (f"D{daily_info['header_row']}", 8)])
    # G欄(單日報酬率):正紅負綠,絕對值超過 ±3/4/5% 另外填三層漸層黃底;範圍從加總列起算,
    # 不含 base_row(最早一天原始資料,G 欄本來就留空)
    g_condfmt_xml, cal_styles_xml = uwo._g_column_condfmt(
        hist_rows, cal_styles_xml,
        data_start=daily_info["agg_row"], data_end=daily_info["data_end"])
    condfmts.append(g_condfmt_xml)
    # 加總列 D 欄(累計損益漲跌):正紅負綠
    d_agg_condfmt_xml, cal_styles_xml = uwo._updown_color_condfmt(
        f"D{daily_info['agg_row']}", cal_styles_xml)
    condfmts.append(d_agg_condfmt_xml)
    if placed:
        daily_sheetdata, cal_styles_xml = uwo._highlight_calendar_titles(
            daily_sheetdata, [(p[4], p[2]) for p in placed], cal_styles_xml)
        daily_sheetdata, cal_styles_xml = uwo._apply_calendar_day_notes(
            daily_sheetdata, placed, cal_styles_xml, CATHAY_DAY_NOTES)
    daily_sheetdata = uwo.DAILY_COLS + '<sheetData>' + daily_sheetdata + '</sheetData>'
    dxml = re.sub(r"<cols>.*?</sheetData>", daily_sheetdata, dxml, flags=re.S)
    # merge_cells 至少有標題合併這一筆,不管有沒有報酬日曆都要跑
    mergeCells_xml = f'<mergeCells count="{len(merge_cells)}">{"".join(merge_cells)}</mergeCells>'
    condfmt_xml = "".join(condfmts)
    for tag, block in (("mergeCells", mergeCells_xml), ("conditionalFormatting", condfmt_xml)):
        dxml = re.sub(rf"<{tag}[ >].*?</{tag}>", "", dxml, flags=re.S)
        dxml = dxml.replace("<drawing ", block + "<drawing ")

    repl[daily_part] = dxml.encode("utf-8")
    if cal_styles_xml is not None:
        repl["xl/styles.xml"] = cal_styles_xml.encode("utf-8")

    chart_parts = uwo._locate_chart_parts(zin, DAILY_TAB)
    line_chart_path = uwo._find_chart_by_marker(zin, chart_parts["chart_by_rid"], "單日損益漲跌(真實)")
    if line_chart_path and len(hist_rows) > 1:
        repl[line_chart_path] = uwo._build_daily_chart_xml_desc(
            hist_rows, DAILY_TAB, daily_info["header_row"],
            daily_info["data_start"], daily_info["data_end"]).encode("utf-8")

    dw_xml = zin.read(chart_parts["drawing_path"]).decode("utf-8")
    drels_xml = zin.read(chart_parts["drawing_rels_path"]).decode("utf-8")
    chart_by_rid = dict(chart_parts["chart_by_rid"])
    ct_xml = zin.read("[Content_Types].xml").decode("utf-8")
    state = {"ct_changed": False}

    # 折線圖錨點跟著每日表格標題列走(報酬日曆搬到最上面後表格不再從第1列開始);
    # 欄位固定 I 欄(col=8),避開長條圖固定用的 H 欄
    if line_chart_path:
        line_rid = next((rid for rid, p in chart_by_rid.items() if p == line_chart_path), None)
        if line_rid:
            dw_xml = uwo._update_calendar_bar_anchor(dw_xml, line_rid, daily_title_row - 1, col=8)

    def _apply_bar_slot(marker, cal_slot, anchor_row, cal_start_row_):
        nonlocal dw_xml, drels_xml, chart_by_rid, ct_xml
        if not (cal_slot and cal_slot["all_days"]):
            return
        bar_xml = uwo._build_calendar_bar_chart_xml(
            cal_slot, marker, tab=DAILY_TAB, start_row=cal_start_row_).encode("utf-8")
        existing_path = None
        for p in chart_by_rid.values():
            content = new_parts.get(p) or (zin.read(p) if p in zin.namelist() else None)
            if content and marker in content.decode("utf-8"):
                existing_path = p
                break
        if existing_path:
            repl[existing_path] = bar_xml
            bar_rid = next(rid for rid, p in chart_by_rid.items() if p == existing_path)
            dw_xml = uwo._update_calendar_bar_anchor(dw_xml, bar_rid, anchor_row)
        else:
            existing_nums = [int(m.group(1)) for n in (set(zin.namelist()) | set(new_parts))
                              for m in [re.match(r"xl/charts/chart(\d+)\.xml$", n)] if m]
            new_chart_path = f"xl/charts/chart{max(existing_nums, default=0) + 1}.xml"
            new_parts[new_chart_path] = bar_xml
            existing_rids = [int(rm) for rm in re.findall(r'Id="rId(\d+)"', drels_xml)]
            new_rid = f"rId{max(existing_rids, default=0) + 1}"
            rel_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"
            new_rel = f'<Relationship Id="{new_rid}" Type="{rel_type}" Target="../charts/{new_chart_path.split("/")[-1]}"/>'
            drels_xml = drels_xml.replace("</Relationships>", new_rel + "</Relationships>")
            dw_xml = dw_xml.replace("</xdr:wsDr>", uwo._build_bar_chart_anchor_xml(new_rid, anchor_row) + "</xdr:wsDr>")
            chart_by_rid[new_rid] = new_chart_path
            ct_override = ('<Override ContentType="application/vnd.openxmlformats-officedocument.'
                            f'drawingml.chart+xml" PartName="/{new_chart_path}"/>')
            ct_xml = ct_xml.replace("</Types>", ct_override + "</Types>")
            state["ct_changed"] = True

    for marker, g, start_row, last_row, _title_style_id, _txt_style_id in placed:
        # 長條圖左上角固定對齊在該月報酬日曆的標題列(H欄),0-indexed 需減 1
        _apply_bar_slot(marker, g, start_row - 1, start_row)

    repl[chart_parts["drawing_path"]] = dw_xml.encode("utf-8")
    repl[chart_parts["drawing_rels_path"]] = drels_xml.encode("utf-8")
    if state["ct_changed"]:
        repl["[Content_Types].xml"] = ct_xml.encode("utf-8")


def sync(args):
    today = date.today()
    if args.date:
        today = datetime.strptime(args.date, "%Y/%m/%d").date()

    print(f"[國泰持股] {CATHAY_HOLDINGS}")
    snap = fetch_today_snapshot(today)
    if snap is None:
        print(f"今日({today})無 TWSE 收盤資料,跳過(非交易日或資料尚未產生)。")
        return
    mv, cost, pl, ret = snap
    print(f"[{today}] 市值={mv:,.0f} 成本={cost:,.0f} 損益={pl:,.0f} 報酬率={ret:.2%}")

    creds = uwo.get_creds()
    workdir = tempfile.mkdtemp(prefix="cathay_daily_")
    drive, meta, path_in, backup = uwo.download_xlsm(creds, workdir)
    print(f"[目標] {meta['name']}(雲端最後修改 {meta['modifiedTime']})")
    print(f"[備份] {backup}")

    hist_rows = _read_hist_rows(path_in)
    date_str = today.strftime("%Y/%m/%d")
    hist_rows = [r for r in hist_rows if r[0] != date_str] + [(date_str, mv, cost, pl)]
    hist_rows.sort(key=lambda r: datetime.strptime(r[0], "%Y/%m/%d"))

    if args.dry_run:
        print("--dry-run:只預覽,不寫入。")
        return
    if not args.yes:
        if input("確定寫入雲端 Wealth OS?(yes/no) ").strip().lower() != "yes":
            print("已取消。")
            return

    zin = zipfile.ZipFile(path_in)
    hist_part = uwo._locate_sheet_part(zin, HIST_TAB)
    hxml = zin.read(hist_part).decode("utf-8")
    new_hxml, note = _upsert_hist_row(hxml, date_str, mv, cost, pl, ret)
    print(f"[{HIST_TAB}] {note}")

    repl = {hist_part: new_hxml.encode("utf-8")}
    new_parts = {}
    _rebuild_daily_and_calendar(zin, hist_rows, repl, new_parts)

    path_out = path_in.replace(".xlsm", "_cathay.xlsm")
    with zipfile.ZipFile(path_out, "w") as zout:
        for info in zin.infolist():
            data = repl.get(info.filename)
            if data is None:
                data = zin.read(info.filename)
            zout.writestr(info, data, compress_type=info.compress_type)
        for p, data in new_parts.items():
            zout.writestr(p, data, compress_type=zipfile.ZIP_DEFLATED)
    zin.close()

    # 自檢:元件清單一致(除白名單新增)、XML 合法、可開檔
    from xml.dom import minidom
    za, zb = zipfile.ZipFile(path_in), zipfile.ZipFile(path_out)
    added = set(zb.namelist()) - set(za.namelist())
    removed = set(za.namelist()) - set(zb.namelist())
    assert not removed, f"元件被移除!{removed}"
    assert added <= set(new_parts), f"意外新增元件 {added - set(new_parts)}"
    changed = [n for n in za.namelist() if za.read(n) != zb.read(n)] + list(added)
    assert set(changed) <= set(repl) | set(new_parts), f"意外變動 {set(changed) - (set(repl) | set(new_parts))}"
    for n in changed:
        minidom.parseString(zb.read(n))
    za.close(); zb.close()
    openpyxl.load_workbook(path_out)   # 可開檔驗證

    media = MediaFileUpload(path_out, mimetype=uwo.XLSM_MIME, resumable=True)
    res = drive.files().update(fileId=uwo.XLSM_FILE_ID, media_body=media,
                                fields="id,name,size,version,modifiedTime").execute()
    print(f"✓ 已上傳(version {res['version']})")


def main():
    ap = argparse.ArgumentParser(description="國泰帳戶每日市值/損益快照 → Wealth OS")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_up = sub.add_parser("update", help="抓今日收盤價、更新 21_國泰資產歷史／03_國泰漲跌")
    p_up.add_argument("--date", help="指定日期(預設今天),例 2026/8/26")
    p_up.add_argument("--yes", action="store_true", help="免確認直接寫")
    p_up.add_argument("--dry-run", action="store_true", help="只預覽不寫入")
    args = ap.parse_args()
    if args.cmd == "update":
        sync(args)


if __name__ == "__main__":
    main()
