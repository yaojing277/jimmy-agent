"""「財自計算機」台灣版 v2：修正模型問題＋補台灣項目＋負債。
接在 localize_fire_calc_tw.py 之後執行（列號以插列後為準）。
  1. 報酬率預設 7%       2. 提領率改輸入欄（取代寫死的 ×25）
  4. 逐年推估統一算法    5. 年終獎金月數       6. 勞健保自付比例
  負債：房貸／信貸月付＋剩餘年數（到期後月付消失；目標線含未繳貸款）
用法：python3 upgrade_fire_calc_tw.py [--dry-run]
"""
import sys, warnings
warnings.filterwarnings('ignore')
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SID = '1FmpyX-6ddFyhBM5zj6vmrCzxvHondx3fTeCqSp4YZCc'
CALC, LIST = 0, 925851502
C = "'計算機'!"
NEG = {'type': 'NUMBER', 'pattern': '"NT$"#,##0;[Red]-"NT$"#,##0'}

# 插列後的位置（原列號 → 新列號：≤11 不動、12~17 +2、18~31 +4、32 以後 +5）
EXPECT = {'B19': '每月薪資收入', 'B22': '其他被動收入（股利／租金等）', 'B23': '薪資年成長率',
          'B24': '綜所稅平均稅率（估算）', 'B30': '目前年齡', 'B32': '目前可投資資產',
          'B34': '年通膨率', 'B35': '年投資報酬率', 'D42': '預估年數（不投資）',
          'D43': '預估年數（有投資）', 'B51': '📘 小提醒', 'B58': '台灣綜合所得稅參考（115 年度）',
          'K66': '年數'}

TAX = ('=IFERROR(LET(年薪,E19*(12+E20), 淨額,MAX(年薪-101000-136000-227000,0),\n'
       ' 稅,IF(淨額<=610000,淨額*5%,IF(淨額<=1380000,淨額*12%-42700,\n'
       '  IF(淨額<=2770000,淨額*20%-153100,IF(淨額<=5190000,淨額*30%-430100,淨額*40%-949100)))),\n'
       ' ROUND(稅,0)/年薪),0)')
TAX_NOTE = ('台灣綜合所得稅參考（115 年度，2027 年 5 月申報）\n單身、僅薪資所得估算：\n'
            '年薪＝月薪 ×（12＋年終月數）\n'
            '所得淨額＝年薪 − 免稅額 101,000 − 標準扣除額 136,000 − 薪資特別扣除額 227,000\n'
            '0～610,000：5%\n610,001～1,380,000：12%（累進差額 42,700）\n'
            '1,380,001～2,770,000：20%（累進差額 153,100）\n'
            '2,770,001～5,190,000：30%（累進差額 430,100）\n'
            '5,190,001 以上：40%（累進差額 949,100）\n※ 股利分開計稅、列舉扣除等未計入。')

TEXT = {
    'C6': '剩餘年數', 'B7': '房租／管理費',
    'B66': '※ 應納稅額＝所得淨額 × 稅率 − 累進差額；年薪已含年終，股利分開計稅與列舉扣除未計入',
    'B12': '房貸月付（選填）', 'B13': '信貸／車貸月付（選填）',
    'F12': '<-- 選填', 'F13': '<-- 選填',
    'D15': '每月總支出（含貸款）',
    'B20': '年終獎金（月數）', 'B21': '勞健保自付比例',
    'F20': '<-- 請輸入', 'F21': '<-- 請輸入',
    'D26': '每月稅後總收入（含年終平均、已扣勞健保）',
    'B28': 'Step 3️⃣｜設定財務目標（安全提領率）',
    'B36': '安全提領率', 'F36': '<-- 請輸入',
    'D38': '每月可投資淨額（＝稅後收入 − 總支出，可為負）',
    'D39': '退休所需資產（＝年生活支出 ÷ 提領率）',
    'B54': '．提領率：退休所需資產＝年生活支出 ÷ 提領率。4% 對應約 30 年的退休期，提早退休建議 3～3.5%。',
    'B55': '．藍線＝資產推估（實質）、紅線＝退休目標（含尚未繳完的貸款）；藍線穿越紅線＝大約可以退休。',
}
NUMS = {'E12': 0, 'C12': 0, 'E13': 0, 'C13': 0, 'E20': 1.5, 'E21': 0.04, 'E35': 0.07, 'E36': 0.035}
NOTES = {
    'B12': '沒有房貸填 0。剩餘年數＝還要繳幾年。\n月付是名目金額，推估時會依通膨折成實質；到期後自動不再扣。\n'
           '⚠ 退休所需資產不含房貸，但紅線會加上「尚未繳完的月付總額」，代表退休前要先還清。',
    'B13': '同房貸。沒有就填 0。',
    'B20': '台灣常見 1～3 個月。年薪與綜所稅都以「月薪 ×（12＋年終月數）」計算，\n每月收入取全年平均。',
    'B21': '員工自付：勞保（含就保）約 2.3～2.5%＋健保約 1.55%（未含眷屬），合計約 4%。\n'
           '高薪者有投保級距上限，實際比例會較低。\n勞退自提不必算在這裡——那筆錢其實是你的退休資產。',
    'B35': '名目年報酬率（含息）。參考：保守 5%／中性 7%／樂觀 9%。\n'
           '10% 是美股長期牛市的水準，拿來做退休規劃偏樂觀。',
    'B36': '每年從資產提領的比例。退休所需資產＝年生活支出 ÷ 提領率。\n'
           '4%（×25）出自美國 30 年退休期的研究；40 歲左右退休、要撐 50 年以上，\n建議 3～3.5%（×28.6～33）。',
}

def a1(ref):
    col = ''.join(ch for ch in ref if ch.isalpha()); ci = 0
    for ch in col: ci = ci * 26 + ord(ch) - 64
    return int(ref[len(col):]) - 1, ci - 1

def grid(sheet, ref, ref2=None):
    r, c = a1(ref); r2, c2 = a1(ref2 or ref)
    return {'sheetId': sheet, 'startRowIndex': r, 'endRowIndex': r2 + 1,
            'startColumnIndex': c, 'endColumnIndex': c2 + 1}

def put(sheet, ref, value=None, note=None):
    r, c = a1(ref); data, fields = {}, []
    if value is not None:
        k = 'formulaValue' if isinstance(value, str) and value.startswith('=') else \
            'stringValue' if isinstance(value, str) else 'numberValue'
        data['userEnteredValue'] = {k: value}; fields.append('userEnteredValue')
    if note is not None:
        data['note'] = note; fields.append('note')
    return {'updateCells': {'rows': [{'values': [data]}], 'fields': ','.join(fields),
            'start': {'sheetId': sheet, 'rowIndex': r, 'columnIndex': c}}}

def nfmt(sheet, ref, ref2, nf):
    return {'repeatCell': {'range': grid(sheet, ref, ref2), 'cell': {'userEnteredFormat': {'numberFormat': nf}},
            'fields': 'userEnteredFormat.numberFormat'}}

def dv(ref, cond, vals, msg=None):
    rule = {'condition': {'type': cond, 'values': [{'userEnteredValue': v} for v in vals]}, 'strict': True}
    if msg: rule['inputMessage'] = msg
    return {'setDataValidation': {'range': grid(CALC, ref), 'rule': rule}}

def insert(after_row, n):  # 在第 after_row 列之後插 n 列，沿用上一列格式
    return {'insertDimension': {'range': {'sheetId': CALC, 'dimension': 'ROWS', 'startIndex': after_row,
            'endIndex': after_row + n}, 'inheritFromBefore': True}}

def list_row(i):
    """List 分頁第 i 列（年數 k＝i−2），全部年度同一套算法。"""
    k, r, pi = f'A{i}', f'{C}$E$41', f'{C}$E$34'
    income = (f"{C}$E$19*POWER(1+{C}$E$40,{k})*(12+{C}$E$20)/12*(1-{C}$E$21-{C}$E$24)"
              f"+{C}$E$22*(1-{C}$E$24)")
    debt = f"({C}$E$12*({k}<{C}$C$12)+{C}$E$13*({k}<{C}$C$13))/POWER(1+{pi},{k})"
    left = (f"({C}$E$12*12*MAX(0,{C}$C$12-{k}-1)+{C}$E$13*12*MAX(0,{C}$C$13-{k}-1))"
            f"/POWER(1+{pi},{k}+1)")
    return {
        f'B{i}': f"={C}E32" if i == 2 else f'=D{i - 1}',
        f'C{i}': f"=({income})-SUM({C}$E$7:$E$11)-{debt}",
        # 每月月底投入、年內按月複利：年金終值係數 r/((1+r)^(1/12)-1)；r=0 時為 12
        f'D{i}': f"=B{i}*(1+{r})+C{i}*IF({r}=0,12,{r}/((1+{r})^(1/12)-1))",
        f'E{i}': f"={C}$E$39+{left}",
    }

def main(dry):
    creds = Credentials.from_authorized_user_file('token.json')
    if not creds.valid: creds.refresh(Request())
    s = build('sheets', 'v4', credentials=creds)
    get = lambda rng: s.spreadsheets().values().get(spreadsheetId=SID, range=rng).execute().get('values', [[]])

    # ── 階段 1：插列（由下往上，索引才不會互相影響）
    if get('計算機!B12')[0][:1] == ['房貸月付（選填）']:
        print('已插過列，跳過階段 1')
    elif dry:
        print('dry-run：階段 1 會插入 5 列（11 後 2 列、17 後 2 列、31 後 1 列）'); return
    else:
        s.spreadsheets().batchUpdate(spreadsheetId=SID, body={'requests': [
            insert(31, 1), insert(17, 2), insert(11, 2)]}).execute()
        print('階段 1：已插入 5 列')

    # ── 位置驗證：任何一格不符就中止
    bad = [(ref, want, (get(f'計算機!{ref}')[0] or [''])[0]) for ref, want in EXPECT.items()]
    bad = [b for b in bad if b[1] != b[2]]
    if bad:
        for b in bad: print('位置不符', b)
        sys.exit(1)

    # ── 階段 2：文字、數值、公式、格式
    reqs = []
    for ref, v in TEXT.items(): reqs.append(put(CALC, ref, v))
    for ref, v in NUMS.items(): reqs.append(put(CALC, ref, v))
    for ref, v in NOTES.items(): reqs.append(put(CALC, ref, note=v))
    reqs.append(put(CALC, 'B24', note=TAX_NOTE))
    reqs += [
        put(CALC, 'E15', '=SUM(E7:E13)'),
        put(CALC, 'E24', TAX),
        put(CALC, 'E26', '=E19*(12+E20)/12*(1-E21-E24)+E22*(1-E24)'),
        put(CALC, 'E38', '=E26-E15'),
        put(CALC, 'E39', '=SUM(E7:E11)*12/E36'),
        put(CALC, 'E42', '=IF(E38<=0,"無法達成",MAX(E39-E32,0)/(E38*12))'),
        # List!D 是「第 k 年年底」資產，所以達標年數＝k＋1
        put(CALC, 'E43', '=IFERROR(INDEX(List!A2:A42, MATCH(TRUE, List!D2:D42>=List!E2:E42, 0))+1, "")'),
        put(CALC, 'C46', '=E39'), put(CALC, 'F46', '=E38'),
        put(CALC, 'F47', '=IF(ISNUMBER(E42),E30+E42,"無法達成")'),
        put(CALC, 'F48', '=IF(E43="","40 年內無法達成",E30+E43)'),
        put(LIST, 'E1', '退休目標（含未繳貸款）'),
    ]
    for i in range(2, 43):
        for ref, f in list_row(i).items(): reqs.append(put(LIST, ref, f))
    # 計算機 K:O 是原版留下、沒被任何公式或圖表使用的副本 → 改成鏡像 List，免得兩份不同步
    reqs += [{'updateCells': {'range': grid(CALC, 'L66', 'O107'), 'fields': 'userEnteredValue'}},
             put(CALC, 'K66', '=ARRAYFORMULA(List!A1:E42)')]
    # 格式：剩餘年數欄沿用藍色輸入格、負數紅字
    reqs += [
        {'copyPaste': {'source': grid(CALC, 'E6'), 'destination': grid(CALC, 'C6'), 'pasteType': 'PASTE_FORMAT'}},
        {'copyPaste': {'source': grid(CALC, 'E12', 'E13'), 'destination': grid(CALC, 'C12', 'C13'),
                       'pasteType': 'PASTE_FORMAT'}},
        nfmt(CALC, 'C12', 'C13', {'type': 'NUMBER', 'pattern': '0" 年"'}),
        nfmt(CALC, 'E20', 'E20', {'type': 'NUMBER', 'pattern': '0.0" 個月"'}),
        nfmt(CALC, 'E21', 'E21', {'type': 'PERCENT', 'pattern': '0.00%'}),
        nfmt(CALC, 'E36', 'E36', {'type': 'PERCENT', 'pattern': '0.0%'}),
        nfmt(CALC, 'E38', 'E38', NEG), nfmt(CALC, 'F46', 'F46', NEG), nfmt(LIST, 'C2', 'C42', NEG),
        dv('C12', 'NUMBER_BETWEEN', ['0', '40']), dv('C13', 'NUMBER_BETWEEN', ['0', '40']),
        dv('E20', 'NUMBER_BETWEEN', ['0', '12']),
        dv('E21', 'NUMBER_BETWEEN', ['0', '0.2'], '員工自付勞健保約 4%'),
        dv('E36', 'NUMBER_BETWEEN', ['0.02', '0.06'], '建議 3～4%；提早退休建議 3～3.5%'),
    ]
    print(f'階段 2：requests={len(reqs)}')
    if dry: return
    s.spreadsheets().batchUpdate(spreadsheetId=SID, body={'requests': reqs}).execute()
    print('done')

if __name__ == '__main__':
    main('--dry-run' in sys.argv)
