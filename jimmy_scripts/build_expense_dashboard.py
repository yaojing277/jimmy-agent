#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 expense_analyze.py 產出的 JSON 建成一份 Google 試算表消費分析儀表板。
分頁：總覽 / 年度趨勢 / 月度明細 / 主分類 / 主分類×年度 / 子分類Top40 / Top50單筆
含 KPI、直條圖、圓餅圖、折線圖與基本格式。

用法:
    python3 build_expense_dashboard.py <analysis.json> [--title 名稱]
"""
import json
import os
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/spreadsheets',
          'https://www.googleapis.com/auth/drive']
HERE = os.path.dirname(os.path.abspath(__file__))


def get_service():
    creds = Credentials.from_authorized_user_file(
        os.path.join(HERE, 'token.json'), SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(os.path.join(HERE, 'token.json'), 'w') as f:
            f.write(creds.to_json())
    return build('sheets', 'v4', credentials=creds, cache_discovery=False)


# 分頁 sheetId 常數
SID = dict(overview=0, yearly=1, monthly=2, main=3, mainyear=4, sub=5, top=6)


def money_fmt():
    return {'numberFormat': {'type': 'CURRENCY', 'pattern': '"NT$"#,##0'}}


def main():
    src = sys.argv[1]
    title = '消費分析儀表板_2013-2026'
    if '--title' in sys.argv:
        title = sys.argv[sys.argv.index('--title') + 1]
    js = json.load(open(src, encoding='utf-8'))
    svc = get_service()

    # 1) 建立空白試算表 + 7 分頁
    sheets_meta = [
        ('總覽', SID['overview']),
        ('年度趨勢', SID['yearly']),
        ('月度明細', SID['monthly']),
        ('主分類', SID['main']),
        ('主分類x年度', SID['mainyear']),
        ('子分類Top40', SID['sub']),
        ('Top50單筆', SID['top']),
    ]
    body = {
        'properties': {'title': title, 'locale': 'zh_TW'},
        'sheets': [{'properties': {'sheetId': sid, 'title': name,
                                   'gridProperties': {'rowCount': 400,
                                                      'columnCount': 26}}}
                   for name, sid in sheets_meta],
    }
    ss = svc.spreadsheets().create(body=body).execute()
    ssid = ss['spreadsheetId']
    url = ss['spreadsheetUrl']
    print('已建立試算表:', url)

    m = js['meta']
    yearly = js['yearly']            # [year, cnt, amt, avg]
    monthly = js['monthly']          # [ym, cnt, amt]
    main = js['main']                # [name, cnt, amt, pct]
    sub_top = js['sub_top']          # [main, sub, cnt, amt]
    top_single = js['top_single']    # [amt, date, main, sub, note]
    my = js['main_year']             # {mains, years, grid}

    # 2) 準備各分頁值
    val = []

    def put(sheet, rng, values):
        val.append({'range': f'{sheet}!{rng}', 'values': values})

    # --- 總覽 ---
    days = 1
    from datetime import datetime as _dt
    try:
        d0 = _dt.strptime(m['date_min'], '%Y/%m/%d')
        d1 = _dt.strptime(m['date_max'], '%Y/%m/%d')
        days = max((d1 - d0).days, 1)
    except Exception:
        pass
    months = max(days / 30.44, 1)
    total = m['total']
    ov = [
        ['消費分析儀表板'],
        [f"資料期間　{m['date_min']} ～ {m['date_max']}"],
        [],
        ['關鍵指標', ''],
        ['累計支出', total],
        ['有效筆數', m['records']],
        ['日均支出', round(total / days)],
        ['月均支出', round(total / months)],
        ['年均支出', round(total / max(len(m['years']), 1))],
        ['平均每筆', round(total / max(m['records'], 1))],
        ['最大單筆', top_single[0][0] if top_single else 0],
    ]
    put('總覽', 'A1', ov)

    # --- 年度趨勢 ---
    put('年度趨勢', 'A1',
        [['年份', '筆數', '支出金額', '平均每筆']] + yearly)

    # --- 月度明細 ---
    put('月度明細', 'A1',
        [['年月', '筆數', '支出金額']] + monthly)

    # --- 主分類 ---
    put('主分類', 'A1',
        [['主分類', '筆數', '金額', '佔比%']] + main)

    # --- 主分類×年度 ---
    header = ['主分類'] + [str(y) for y in my['years']] + ['合計']
    grid = []
    for i, mn in enumerate(my['mains']):
        rowv = my['grid'][i]
        grid.append([mn] + rowv + [sum(rowv)])
    # 年度合計列
    col_tot = ['年度合計']
    for j in range(len(my['years'])):
        col_tot.append(sum(my['grid'][i][j] for i in range(len(my['mains']))))
    col_tot.append(sum(col_tot[1:]))
    put('主分類x年度', 'A1', [header] + grid + [col_tot])

    # --- 子分類Top40 ---
    put('子分類Top40', 'A1',
        [['主分類', '子分類', '筆數', '金額']] + sub_top)

    # --- Top50單筆 ---
    put('Top50單筆', 'A1',
        [['金額', '日期', '主分類', '子分類', '備註(前40字)']] + top_single)

    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=ssid,
        body={'valueInputOption': 'RAW', 'data': val}).execute()
    print('資料已寫入')

    # 3) 格式 + 圖表
    reqs = []

    def bold_header(sid, cols):
        return {'repeatCell': {
            'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1,
                      'startColumnIndex': 0, 'endColumnIndex': cols},
            'cell': {'userEnteredFormat': {
                'backgroundColor': {'red': 0.17, 'green': 0.24, 'blue': 0.31},
                'textFormat': {'bold': True,
                               'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
                'horizontalAlignment': 'CENTER'}},
            'fields': 'userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)'}}

    def freeze(sid, rows=1, cols=0):
        return {'updateSheetProperties': {
            'properties': {'sheetId': sid,
                           'gridProperties': {'frozenRowCount': rows,
                                              'frozenColumnCount': cols}},
            'fields': 'gridProperties.frozenRowCount,gridProperties.frozenColumnCount'}}

    def money_col(sid, col, start=1, end=400):
        return {'repeatCell': {
            'range': {'sheetId': sid, 'startRowIndex': start, 'endRowIndex': end,
                      'startColumnIndex': col, 'endColumnIndex': col + 1},
            'cell': {'userEnteredFormat': money_fmt()},
            'fields': 'userEnteredFormat.numberFormat'}}

    # 表頭格式與凍結
    for sid, cols, fr in [(SID['yearly'], 4, (1, 0)), (SID['monthly'], 3, (1, 0)),
                          (SID['main'], 4, (1, 0)),
                          (SID['mainyear'], len(header), (1, 1)),
                          (SID['sub'], 4, (1, 0)), (SID['top'], 5, (1, 0))]:
        reqs.append(bold_header(sid, cols))
        reqs.append(freeze(sid, fr[0], fr[1]))

    # 金額欄貨幣格式
    reqs.append(money_col(SID['yearly'], 2))
    reqs.append(money_col(SID['yearly'], 3))
    reqs.append(money_col(SID['monthly'], 2))
    reqs.append(money_col(SID['main'], 2))
    reqs.append(money_col(SID['sub'], 3))
    reqs.append(money_col(SID['top'], 0))
    for c in range(1, len(header)):
        reqs.append(money_col(SID['mainyear'], c))

    # 總覽格式：標題大字、KPI 貨幣
    reqs.append({'repeatCell': {
        'range': {'sheetId': 0, 'startRowIndex': 0, 'endRowIndex': 1,
                  'startColumnIndex': 0, 'endColumnIndex': 1},
        'cell': {'userEnteredFormat': {'textFormat': {'bold': True, 'fontSize': 18}}},
        'fields': 'userEnteredFormat.textFormat'}})
    reqs.append({'repeatCell': {
        'range': {'sheetId': 0, 'startRowIndex': 3, 'endRowIndex': 4,
                  'startColumnIndex': 0, 'endColumnIndex': 2},
        'cell': {'userEnteredFormat': {
            'backgroundColor': {'red': 0.17, 'green': 0.24, 'blue': 0.31},
            'textFormat': {'bold': True,
                           'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}}}},
        'fields': 'userEnteredFormat(backgroundColor,textFormat)'}})
    reqs.append({'repeatCell': {
        'range': {'sheetId': 0, 'startRowIndex': 4, 'endRowIndex': 11,
                  'startColumnIndex': 1, 'endColumnIndex': 2},
        'cell': {'userEnteredFormat': money_fmt()},
        'fields': 'userEnteredFormat.numberFormat'}})
    # 有效筆數改回一般數字
    reqs.append({'repeatCell': {
        'range': {'sheetId': 0, 'startRowIndex': 5, 'endRowIndex': 6,
                  'startColumnIndex': 1, 'endColumnIndex': 2},
        'cell': {'userEnteredFormat': {'numberFormat': {'type': 'NUMBER',
                                                        'pattern': '#,##0'}}},
        'fields': 'userEnteredFormat.numberFormat'}})

    n_year = len(yearly)
    n_month = len(monthly)
    n_main = len(main)

    # 圖1：年度支出直條圖（放總覽 D1）
    reqs.append({'addChart': {'chart': {'spec': {
        'title': '年度支出趨勢',
        'basicChart': {
            'chartType': 'COLUMN', 'legendPosition': 'BOTTOM_LEGEND',
            'axis': [{'position': 'BOTTOM_AXIS', 'title': '年份'},
                     {'position': 'LEFT_AXIS', 'title': '金額'}],
            'domains': [{'domain': {'sourceRange': {'sources': [{
                'sheetId': SID['yearly'], 'startRowIndex': 0,
                'endRowIndex': n_year + 1, 'startColumnIndex': 0,
                'endColumnIndex': 1}]}}}],
            'series': [{'series': {'sourceRange': {'sources': [{
                'sheetId': SID['yearly'], 'startRowIndex': 0,
                'endRowIndex': n_year + 1, 'startColumnIndex': 2,
                'endColumnIndex': 3}]}}, 'targetAxis': 'LEFT_AXIS'}],
            'headerCount': 1}},
        'position': {'overlayPosition': {'anchorCell': {
            'sheetId': 0, 'rowIndex': 0, 'columnIndex': 3},
            'widthPixels': 560, 'heightPixels': 320}}}}})

    # 圖2：主分類占比圓餅圖（總覽 D18）
    reqs.append({'addChart': {'chart': {'spec': {
        'title': '主分類支出佔比',
        'pieChart': {
            'legendPosition': 'RIGHT_LEGEND',
            'domain': {'sourceRange': {'sources': [{
                'sheetId': SID['main'], 'startRowIndex': 1,
                'endRowIndex': n_main + 1, 'startColumnIndex': 0,
                'endColumnIndex': 1}]}},
            'series': {'sourceRange': {'sources': [{
                'sheetId': SID['main'], 'startRowIndex': 1,
                'endRowIndex': n_main + 1, 'startColumnIndex': 2,
                'endColumnIndex': 3}]}}}},
        'position': {'overlayPosition': {'anchorCell': {
            'sheetId': 0, 'rowIndex': 17, 'columnIndex': 3},
            'widthPixels': 560, 'heightPixels': 340}}}}})

    # 圖3：月度支出折線圖（總覽 D37）
    reqs.append({'addChart': {'chart': {'spec': {
        'title': '月度支出趨勢',
        'basicChart': {
            'chartType': 'LINE', 'legendPosition': 'NO_LEGEND',
            'axis': [{'position': 'BOTTOM_AXIS', 'title': '年月'},
                     {'position': 'LEFT_AXIS', 'title': '金額'}],
            'domains': [{'domain': {'sourceRange': {'sources': [{
                'sheetId': SID['monthly'], 'startRowIndex': 0,
                'endRowIndex': n_month + 1, 'startColumnIndex': 0,
                'endColumnIndex': 1}]}}}],
            'series': [{'series': {'sourceRange': {'sources': [{
                'sheetId': SID['monthly'], 'startRowIndex': 0,
                'endRowIndex': n_month + 1, 'startColumnIndex': 2,
                'endColumnIndex': 3}]}}, 'targetAxis': 'LEFT_AXIS'}],
            'headerCount': 1}},
        'position': {'overlayPosition': {'anchorCell': {
            'sheetId': 0, 'rowIndex': 37, 'columnIndex': 3},
            'widthPixels': 720, 'heightPixels': 320}}}}})

    # 欄寬自動
    for sid, cols in [(0, 5), (SID['yearly'], 4), (SID['monthly'], 3),
                      (SID['main'], 4), (SID['mainyear'], len(header)),
                      (SID['sub'], 4), (SID['top'], 5)]:
        reqs.append({'autoResizeDimensions': {'dimensions': {
            'sheetId': sid, 'dimension': 'COLUMNS',
            'startIndex': 0, 'endIndex': cols}}})

    svc.spreadsheets().batchUpdate(
        spreadsheetId=ssid, body={'requests': reqs}).execute()
    print('格式與圖表已套用')
    print('\n完成 →', url)
    return url


if __name__ == '__main__':
    main()
