"""「財自計算機」台灣版 v3：接在 upgrade_fire_calc_tw.py（v2）之後執行。
  3. 反推：想在「理想退休年齡」退休，每月需投入多少（取消隱藏第 31 列）
  7. 勞保＋勞退年金：退休所需資產＝(生活支出−年金)÷提領率＋領年金前的銜接期支出
用法：python3 upgrade_fire_calc_tw_v3.py [--dry-run]
"""
import sys, warnings
warnings.filterwarnings('ignore')
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from upgrade_fire_calc_tw import SID, CALC, LIST, C, NEG, a1, grid, put, nfmt, dv, insert

# 插列後位置（v2 列號 → v3：≤36 不動、37~43 +2、44 以後 +4）
EXPECT = {'B30': '目前年齡', 'B31': '理想退休年齡', 'B32': '目前可投資資產', 'B36': '安全提領率',
          'D44': '預估年數（不投資）', 'D45': '預估年數（有投資）', 'B49': 'Step 4️⃣｜結果與圖表',
          'D51': '🧮 退休年齡（不投資）', 'B55': '📘 小提醒',
          'B62': '台灣綜合所得稅參考（115 年度）', 'K70': '年數'}

LIVE = f"SUM({C}$E$7:$E$11)"
TEXT = {
    'B37': '勞保＋勞退預估月領（選填）', 'F37': '<-- 選填',
    'B38': '開始領取年齡', 'F38': '<-- 請輸入', 'F31': '<-- 請輸入',
    'D41': '退休所需資產（理想退休年齡、已扣年金）',
    'D46': '理想退休年齡需每月投入（實質）',
    'D47': '與目前可投資淨額的差距',
    'B59': '．藍線＝資產推估（實質）、紅線＝退休目標（含年金銜接期、未繳貸款）；藍線穿越紅線＝大約可以退休。',
    'B58': '．退休所需資產＝（年生活支出 − 年金）÷ 提領率＋領年金前的銜接期支出；提早退休建議提領率 3～3.5%。',
}
NUMS = {'E37': 0, 'E38': 65}
NOTES = {
    'B31': '想幾歲退休。用來：\n① 反推「每月需投入多少」才來得及\n② 計算領年金前要自己負擔幾年（銜接期）',
    'B37': '退休後每月可領的勞保老年年金＋勞退，以「今天的購買力」填（實質金額）。\n'
           '粗估：\n．勞保 ≈ 平均月投保薪資（上限 45,800）× 年資 × 1.55%\n'
           '．勞退 ≈ 個人專戶累積金額 ÷ 平均餘命月數\n'
           '可上勞保局「老年年金試算」查。勞保財務有疑慮的話，建議自行打折（例如 7 折）。\n沒有或不想計入就填 0。',
    'B38': '勞保老年年金法定請領年齡 65 歲；提前請領每提前 1 年減給 4%（最多提前 5 年）。',
}
# 某年退休（年齡 age）所需資產：長期部分＋銜接期（不折現，保守）
def target(age):
    return (f"MAX(0,{LIVE}-{C}$E$37)*12/{C}$E$36"
            f"+MIN({C}$E$37,{LIVE})*12*MAX(0,{C}$E$38-({age}))")

def main(dry):
    creds = Credentials.from_authorized_user_file('token.json')
    if not creds.valid: creds.refresh(Request())
    s = build('sheets', 'v4', credentials=creds)
    get = lambda rng: s.spreadsheets().values().get(spreadsheetId=SID, range=rng).execute().get('values', [[]])

    if get('計算機!B37')[0][:1] == [TEXT['B37']]:
        print('已插過列，跳過階段 1')
    elif dry:
        print('dry-run：階段 1 會插入 4 列（43 後 2 列、36 後 2 列）並取消隱藏第 31 列'); return
    else:
        s.spreadsheets().batchUpdate(spreadsheetId=SID, body={'requests': [
            insert(43, 2), insert(36, 2),
            {'updateDimensionProperties': {'range': {'sheetId': CALC, 'dimension': 'ROWS', 'startIndex': 30,
             'endIndex': 31}, 'properties': {'hiddenByUser': False}, 'fields': 'hiddenByUser'}}]}).execute()
        print('階段 1：已插入 4 列、取消隱藏第 31 列')

    bad = [(r, w, (get(f'計算機!{r}')[0] or [''])[0]) for r, w in EXPECT.items()]
    bad = [b for b in bad if b[1] != b[2]]
    row53 = [x for x in get('計算機!D53:G53')[0] if x]
    if row53 and not row53[0].startswith('🎯'):
        bad.append(('D53:G53', '空白', row53))
    if bad:
        for b in bad: print('位置不符', b)
        sys.exit(1)

    reqs = []
    for ref, v in TEXT.items(): reqs.append(put(CALC, ref, v))
    for ref, v in NUMS.items(): reqs.append(put(CALC, ref, v))
    for ref, v in NOTES.items(): reqs.append(put(CALC, ref, note=v))
    reqs += [
        put(CALC, 'E41', '=' + target('E31').replace(C, '')),
        put(CALC, 'E44', '=IF(E40<=0,"無法達成",MAX(E41-E32,0)/(E40*12))'),
        # 第 n 年年底的目標（含未繳貸款）就是 List!E 第 n 列；用年金終值係數反推每月定額投入
        put(CALC, 'E46', '=IF(OR(E31<=E30,E31-E30>41),"請確認年齡",LET(n,E31-E30, r,E43,'
                         ' t,INDEX(List!E2:E42,n), MAX(0,(t-E32*(1+r)^n)/IF(r=0,12*n,'
                         '((1+r)^n-1)/((1+r)^(1/12)-1)))))'),
        put(CALC, 'E47', '=IF(ISNUMBER(E46),E46-E40,"")'),
        put(CALC, 'D53', '="🎯 "&E31&" 歲退休每月需投入"'),
        put(CALC, 'F53', '=E46'),
        put(LIST, 'E1', '退休目標（含年金銜接、未繳貸款）'),
    ]
    for i in range(2, 43):
        k = f'A{i}'
        left = (f"({C}$E$12*12*MAX(0,{C}$C$12-{k}-1)+{C}$E$13*12*MAX(0,{C}$C$13-{k}-1))"
                f"/POWER(1+{C}$E$34,{k}+1)")
        reqs.append(put(LIST, f'E{i}', f"={target(f'{C}$E$30+{k}+1')}+{left}"))
    reqs += [
        nfmt(CALC, 'E37', 'E37', {'type': 'NUMBER', 'pattern': '"NT$"#,##0'}),
        nfmt(CALC, 'E38', 'E38', {'type': 'NUMBER', 'pattern': '0" 歲"'}),
        nfmt(CALC, 'E46', 'E46', {'type': 'NUMBER', 'pattern': '"NT$"#,##0'}),
        nfmt(CALC, 'E47', 'E47', {'type': 'NUMBER', 'pattern': '[Red]"不足 NT$"#,##0;"有餘裕 NT$"#,##0;"剛好"'}),
        dv('E37', 'NUMBER_GREATER_THAN_EQ', ['0']),
        dv('E38', 'NUMBER_BETWEEN', ['60', '70'], '勞保老年年金法定 65 歲，可提前 5 年（減給）或延後 5 年（增給）'),
        dv('E31', 'NUMBER_BETWEEN', ['18', '80']),
        # 結果區第 53 列沿用上一列的樣式
        {'copyPaste': {'source': grid(CALC, 'D52', 'G52'), 'destination': grid(CALC, 'D53', 'G53'),
                       'pasteType': 'PASTE_FORMAT'}},
        {'mergeCells': {'range': grid(CALC, 'D53', 'E53'), 'mergeType': 'MERGE_ALL'}},
        nfmt(CALC, 'F53', 'F53', {'type': 'NUMBER', 'pattern': '"NT$"#,##0'}),
    ]
    print(f'階段 2：requests={len(reqs)}')
    if dry: return
    s.spreadsheets().batchUpdate(spreadsheetId=SID, body={'requests': reqs}).execute()
    print('done')



def chart_in_wan():
    """圖表縱軸改用「萬元」：List F/G 放 D/E ÷ 10000，圖表系列改指這兩欄（A~E 不動）。
    原本 NT$60,000,000 這種標籤太長會折行。用法：python3 upgrade_fire_calc_tw_v3.py --chart"""
    creds = Credentials.from_authorized_user_file('token.json')
    if not creds.valid: creds.refresh(Request())
    s = build('sheets', 'v4', credentials=creds)
    ncol = s.spreadsheets().get(spreadsheetId=SID, fields='sheets.properties').execute()['sheets'][1]['properties']
    assert ncol['sheetId'] == LIST
    ncol = ncol['gridProperties']['columnCount']
    reqs = [{'appendDimension': {'sheetId': LIST, 'dimension': 'COLUMNS', 'length': 7 - ncol}}] if ncol < 7 else []
    reqs += [put(LIST, 'F1', '資產推估（實質）'), put(LIST, 'G1', '退休目標（含年金銜接、未繳貸款）')]
    for i in range(2, 43):
        reqs += [put(LIST, f'F{i}', f'=D{i}/10000'), put(LIST, f'G{i}', f'=E{i}/10000')]
    reqs.append(nfmt(LIST, 'F2', 'G42', {'type': 'NUMBER', 'pattern': '#,##0" 萬"'}))
    chart = s.spreadsheets().get(spreadsheetId=SID, fields='sheets.charts').execute()['sheets'][0]['charts'][0]
    spec = chart['spec']
    for ser, col in zip(spec['basicChart']['series'], (5, 6)):   # D→F、E→G
        src = ser['series']['sourceRange']['sources'][0]
        assert src['sheetId'] == LIST and src['startColumnIndex'] in (3, 4, col), src
        src['startColumnIndex'], src['endColumnIndex'] = col, col + 1
    for ax in spec['basicChart']['axis']:
        if ax['position'] == 'LEFT_AXIS': ax['title'] = '萬元'  # 帶括號的長標題實測會被丟掉
    reqs.append({'updateChartSpec': {'chartId': chart['chartId'], 'spec': spec}})
    s.spreadsheets().batchUpdate(spreadsheetId=SID, body={'requests': reqs}).execute()
    print('圖表已改為萬元')


if __name__ == '__main__':
    if '--chart' in sys.argv: chart_in_wan(); sys.exit(0)
    main('--dry-run' in sys.argv)
