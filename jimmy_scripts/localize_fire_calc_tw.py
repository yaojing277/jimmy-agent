"""把「財自計算機」香港版就地改成台灣版（文字、幣別、綜所稅公式、圖表、地區）。
用法：python3 localize_fire_calc_tw.py [--dry-run]
⚠ 已執行過，且 upgrade_fire_calc_tw.py 已插列——列號全部改變，勿再重跑本檔。
"""
import sys, json, warnings
warnings.filterwarnings('ignore')
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SID = '1FmpyX-6ddFyhBM5zj6vmrCzxvHondx3fTeCqSp4YZCc'
CALC, LIST = 0, 925851502
NEW_TITLE = '「 財自計算機 」_台灣版本'

# 115 年度（2026 年所得，2027/5 申報）單身、薪資所得者：免稅額 101,000＋標準扣除 136,000＋薪資特扣 227,000
TAX_FORMULA = ('=IFERROR(LET(年薪,E17*12, 淨額,MAX(年薪-101000-136000-227000,0),\n'
               ' 稅,IF(淨額<=610000,淨額*5%,IF(淨額<=1380000,淨額*12%-42700,\n'
               '  IF(淨額<=2770000,淨額*20%-153100,IF(淨額<=5190000,淨額*30%-430100,淨額*40%-949100)))),\n'
               ' ROUND(稅,0)/年薪),0)')

TAX_NOTE = ('台灣綜合所得稅參考（115 年度，2027 年 5 月申報）\n'
            '單身、僅薪資所得估算：\n'
            '所得淨額＝年薪 − 免稅額 101,000 − 標準扣除額 136,000 − 薪資特別扣除額 227,000\n'
            '0～610,000：5%\n610,001～1,380,000：12%（累進差額 42,700）\n'
            '1,380,001～2,770,000：20%（累進差額 153,100）\n'
            '2,770,001～5,190,000：30%（累進差額 430,100）\n'
            '5,190,001 以上：40%（累進差額 949,100）\n'
            '※ 未計勞健保自付額、年終獎金、股利分開計稅等；有年終請折算進月薪。')

# 每行長度不超過原版，否則置中的合併儲存格會左右被切掉
DISCLAIMER = ('免責聲明：本工具僅供學術交流與個人參考，不構成投資或理財建議，實際情況請自行評估。\n'
              '金錢塔有限公司 (Money Tab Limited) 及 MTTV TECH LIMITED（合稱「本公司」）旗下平台提供之香港原版內容，\n'
              '台灣版之稅制與幣別由使用者自行改編。內容所載觀點屬作者個人意見，不構成任何投資建議、要約或推薦。\n'
              '投資涉及風險，價格可漲可跌，過往績效不代表未來表現。本公司不保證內容之正確性及完整性，\n'
              '亦不承擔因使用或依賴本內容而產生之任何損失或責任。投資前請審慎評估個人狀況，\n'
              '並諮詢專業顧問（包括法律、會計及投資專業人士）。')

# 頁尾台灣綜所稅表（取代原版的「香港薪俸稅參考」圖片；圖片 API 刪不掉，需手動刪）
BRACKETS = [('0 ～ 610,000', 0.05, 0), ('610,001 ～ 1,380,000', 0.12, 42700),
            ('1,380,001 ～ 2,770,000', 0.20, 153100), ('2,770,001 ～ 5,190,000', 0.30, 430100),
            ('5,190,001 以上', 0.40, 949100)]
BLACK, WHITE = {'red': 0, 'green': 0, 'blue': 0}, {'red': 1, 'green': 1, 'blue': 1}
YELLOW, CREAM = {'red': 1, 'green': 1, 'blue': 0.2}, {'red': 1, 'green': 0.95, 'blue': 0.8}
GRID = {'style': 'SOLID', 'color': {'red': 0.85, 'green': 0.8, 'blue': 0.65}}

def tax_table_requests(top=52):  # 0-based 列號，52＝第 53 列
    def rng(r, c0, c1): return {'sheetId': CALC, 'startRowIndex': r, 'endRowIndex': r + 1,
                                'startColumnIndex': c0, 'endColumnIndex': c1}
    def fmt(r, c0, c1, bg, bold=False, fg=None, size=12, align='LEFT', nf=None):
        f = {'backgroundColor': bg, 'horizontalAlignment': align, 'verticalAlignment': 'MIDDLE',
             'textFormat': {'bold': bold, 'fontSize': size, 'fontFamily': 'Calibri',
                            'foregroundColor': fg or BLACK},
             'borders': {k: GRID for k in ('top', 'bottom', 'left', 'right')}}
        if nf: f['numberFormat'] = nf
        return {'repeatCell': {'range': {**rng(r, c0, c1)}, 'cell': {'userEnteredFormat': f},
                'fields': 'userEnteredFormat'}}
    def merge(r, c0, c1): return {'mergeCells': {'range': rng(r, c0, c1), 'mergeType': 'MERGE_ALL'}}
    B, E, F, G = 1, 4, 5, 6
    rows = [(top, [('B', '台灣綜合所得稅參考（115 年度）')]),
            (top + 1, [('B', '免稅額＋標準扣除額＋薪資特別扣除額（單身）'), ('E', 464000)]),
            (top + 2, [('B', '所得淨額'), ('E', '稅率'), ('F', '累進差額')])]
    rows += [(top + 3 + i, [('B', t), ('E', p), ('F', d)]) for i, (t, p, d) in enumerate(BRACKETS)]
    rows.append((top + 8, [('B', '※ 應納稅額＝所得淨額 × 稅率 − 累進差額；未計勞健保自付額、年終獎金與股利分開計稅')]))
    reqs = [{'unmergeCells': {'range': {'sheetId': CALC, 'startRowIndex': top, 'endRowIndex': top + 9,
             'startColumnIndex': B, 'endColumnIndex': G}}}]
    reqs += [merge(top, B, G), fmt(top, B, G, BLACK, True, WHITE, 16, 'CENTER'),
             merge(top + 1, B, E), fmt(top + 1, B, E, YELLOW, size=14),
             merge(top + 1, E, G), fmt(top + 1, E, G, YELLOW, size=14, align='RIGHT',
                                       nf={'type': 'NUMBER', 'pattern': '"NT$"#,##0'}),
             merge(top + 2, B, E), fmt(top + 2, B, E, CREAM, True, align='CENTER'),
             fmt(top + 2, E, G, CREAM, True, align='CENTER')]
    for i in range(5):
        r = top + 3 + i
        reqs += [merge(r, B, E), fmt(r, B, E, CREAM),
                 fmt(r, E, F, CREAM, align='RIGHT', nf={'type': 'PERCENT', 'pattern': '0%'}),
                 fmt(r, F, G, CREAM, align='RIGHT', nf={'type': 'NUMBER', 'pattern': '"NT$"#,##0'})]
    reqs += [merge(top + 8, B, G),
             {'repeatCell': {'range': rng(top + 8, B, G), 'cell': {'userEnteredFormat': {
                 'textFormat': {'fontSize': 9, 'foregroundColor': {'red': .4, 'green': .4, 'blue': .4}}}},
                 'fields': 'userEnteredFormat.textFormat'}}]
    for r, cells in rows:
        for col, v in cells: reqs.append(cell(CALC, f'{col}{r + 1}', v))
    return reqs

TEXT = {  # 計算機分頁
    'B1': '財務自由計算機（台灣版）',
    'B2': '🔔【重要提醒‼️】請先建立副本再使用，只需在『藍色儲存格』內輸入資料',
    'B4': 'Step 1️⃣｜輸入每月生活支出',
    'E6': '每月金額 (NT$)',
    'B7': '房租／房貸', 'B8': '水電瓦斯、網路電話費', 'B9': '餐飲支出',
    'B10': '交通支出', 'B11': '其他生活支出', 'D13': '每月總支出',
    'B15': 'Step 2️⃣｜輸入收入與稅率',
    'B17': '每月薪資收入', 'B18': '其他被動收入（股利／租金等）', 'B19': '薪資年成長率',
    'B20': '綜所稅平均稅率（估算）', 'B21': '（級距請參考頁尾表格）',
    'G17': '台灣綜所稅級距（115 年度，所得淨額）',
    'G18': '0 ～ 61 萬：5%', 'G19': '61 ～ 138 萬：12%', 'G20': '138 ～ 277 萬：20%',
    'G21': '277 ～ 519 萬：30%', 'G22': '519 萬以上：40%',
    'B24': 'Step 3️⃣｜設定財務目標（4% 法則）',
    'B30': '年通膨率', 'B31': '年投資報酬率',
    'D33': '每月可投資淨額（＝稅後收入 − 總支出）',
    'D34': '退休所需資產（4% 法則＝年總支出 × 25）',
    'D35': '實質薪資年成長率（扣除通膨）', 'D36': '實質年報酬率（扣除通膨）',
    'D37': '預估年數（不投資）', 'D38': '預估年數（有投資）',
    'B40': 'Step 4️⃣｜結果與圖表',
    'D42': '🧮 退休年齡（不投資）', 'D43': '🧮 退休年齡（有投資）',
    'B46': '📘 小提醒',
    'B47': '．「實質年報酬率」＝投資報酬扣除通膨後，反映每年購買力真正的成長。',
    'B48': '．「實質薪資年成長率」＝薪資調幅扣除通膨後，每年實際可支配金額增加多少。',
    'B49': '．4% 法則：退休所需資產 ≈ 年支出 × 25（本表以此作為目標線）。',
    'B50': '．藍線＝資產推估（實質）、紅線＝退休所需資產；藍線穿越紅線＝大約可以退休。',
    'B51': DISCLAIMER,
    'N61': '資產推估（實質）',
}
LIST_TEXT = {'D1': '資產推估（實質）'}
# 範例輸入值改成台灣水準（藍色格子，使用者本來就會自己改）
NUMS = {'E7': 15000, 'E8': 2500, 'E9': 9000, 'E10': 2000, 'E11': 5000,
        'E17': 55000, 'E28': 1000000}

def a1(ref):
    c = ''.join(ch for ch in ref if ch.isalpha()); r = int(ref[len(c):])
    ci = 0
    for ch in c: ci = ci * 26 + ord(ch) - 64
    return r - 1, ci - 1

def cell(sheet, ref, value=None, note=None):
    r, c = a1(ref)
    data, fields = {}, []
    if value is not None:
        k = 'formulaValue' if isinstance(value, str) and value.startswith('=') else \
            'stringValue' if isinstance(value, str) else 'numberValue'
        data['userEnteredValue'] = {k: value}; fields.append('userEnteredValue')
    if note is not None:
        data['note'] = note; fields.append('note')
    return {'updateCells': {'rows': [{'values': [data]}], 'fields': ','.join(fields),
            'start': {'sheetId': sheet, 'rowIndex': r, 'columnIndex': c}}}

def main(dry):
    creds = Credentials.from_authorized_user_file('token.json')
    if not creds.valid: creds.refresh(Request())
    s = build('sheets', 'v4', credentials=creds)
    meta = s.spreadsheets().get(spreadsheetId=SID, includeGridData=True, fields=(
        'sheets(properties.sheetId,charts,data.rowData.values(userEnteredFormat.numberFormat,dataValidation))')).execute()
    reqs = []
    for ref, v in TEXT.items(): reqs.append(cell(CALC, ref, v))
    for ref, v in LIST_TEXT.items(): reqs.append(cell(LIST, ref, v))
    for ref, v in NUMS.items(): reqs.append(cell(CALC, ref, v))
    reqs.append(cell(CALC, 'E20', TAX_FORMULA))
    reqs.append(cell(CALC, 'B20', note=TAX_NOTE))
    reqs += tax_table_requests()
    # 幣別格式 HK$ → NT$；E31 驗證提示文字
    n_fmt = 0
    for sh in meta['sheets']:
        sid = sh['properties']['sheetId']
        for r, row in enumerate(sh['data'][0].get('rowData', [])):
            for c, v in enumerate(row.get('values', [])):
                nf = v.get('userEnteredFormat', {}).get('numberFormat')
                if nf and 'HK$' in nf.get('pattern', ''):
                    nf = dict(nf, pattern=nf['pattern'].replace('HK$', 'NT$')); n_fmt += 1
                    reqs.append({'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': r, 'endRowIndex': r+1,
                                 'startColumnIndex': c, 'endColumnIndex': c+1},
                                 'cell': {'userEnteredFormat': {'numberFormat': nf}},
                                 'fields': 'userEnteredFormat.numberFormat'}})
                dv = v.get('dataValidation')
                if dv and 'inputMessage' in dv:
                    dv = dict(dv, inputMessage='請輸入 2% 以上的年投資報酬率')
                    reqs.append({'setDataValidation': {'range': {'sheetId': sid, 'startRowIndex': r, 'endRowIndex': r+1,
                                 'startColumnIndex': c, 'endColumnIndex': c+1}, 'rule': dv}})
    # 圖表
    for sh in meta['sheets']:
        for ch in sh.get('charts', []):
            spec = ch['spec']
            spec['title'] = '資產成長推估（含報酬、已扣除通膨）vs 退休目標'
            for ax in spec['basicChart']['axis']:
                if ax.get('title') == 'HK$': ax['title'] = 'NT$'
            reqs.append({'updateChartSpec': {'chartId': ch['chartId'], 'spec': spec}})
    reqs.append({'updateSpreadsheetProperties': {'properties': {'title': NEW_TITLE, 'locale': 'zh_TW',
                 'timeZone': 'Asia/Taipei'}, 'fields': 'title,locale,timeZone'}})
    print(f'requests={len(reqs)}  幣別格式={n_fmt}')
    if dry: return
    s.spreadsheets().batchUpdate(spreadsheetId=SID, body={'requests': reqs}).execute()
    print('done')

if __name__ == '__main__':
    main('--dry-run' in sys.argv)
