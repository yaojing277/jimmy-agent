import pickle
import warnings
warnings.filterwarnings('ignore')

from google.auth.transport.requests import Request
from googleapiclient.discovery import build

TOKEN_FILE = '/Users/jimmy/Downloads/jimmy-agent/token.pickle'
SPREADSHEET_ID = '1UiqAHT2GUhKiviSz5NaLNclttlLVP3ujQMxJUn7Jyr8'
SHEET_NAME = 'Jimmy_260410'

HIGH_DIV = {'56', '00713', '00919', '00878', '00934'}
ETF      = {'50', '52', '56', '00631L', '00663L', '00713', '00878', '00919', '00934'}

def flt(v):
    try:
        return float(str(v).replace(',', '').replace('%', '').strip())
    except:
        return 0.0

def main():
    with open(TOKEN_FILE, 'rb') as f:
        creds = pickle.load(f)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    service = build('sheets', 'v4', credentials=creds)
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=SHEET_NAME
    ).execute()

    rows = result.get('values', [])
    headers = rows[0]
    data_rows = rows[1:-1]  # 排除最後合計列
    total_row = rows[-1]

    stocks = []
    for row in data_rows:
        if not row or not row[0].strip():
            continue
        s = {
            'name':      row[0].strip(),
            'buy_px':    flt(row[1])  if len(row) > 1  else 0,
            'hold_sh':   flt(row[2])  if len(row) > 2  else 0,
            'hold_cost': flt(row[3])  if len(row) > 3  else 0,
            'mkt_val':   flt(row[4])  if len(row) > 4  else 0,
            'profit':    flt(row[5])  if len(row) > 5  else 0,
            'profit_pct':flt(row[6])  if len(row) > 6  else 0,
            'prev_px':   flt(row[7])  if len(row) > 7  else 0,
            'curr_px':   flt(row[8])  if len(row) > 8  else 0,
            'new_sh':    flt(row[9])  if len(row) > 9  else 0,
            'new_cost':  flt(row[10]) if len(row) > 10 else 0,
            'avg_px':    flt(row[11]) if len(row) > 11 else 0,
            'div_freq':  row[12].strip() if len(row) > 12 else '',
            'div_month': row[13].strip() if len(row) > 13 else '',
            'last_div':  flt(row[14]) if len(row) > 14 else 0,
            'div_income':flt(row[15]) if len(row) > 15 else 0,
            'yield_pct': flt(row[19]) if len(row) > 19 else 0,
        }
        # 分類
        n = s['name']
        if n in HIGH_DIV:
            s['type'] = '高股息ETF'
        elif n in ETF:
            s['type'] = '非高股息ETF'
        else:
            s['type'] = '個股'
        stocks.append(s)

    # WW = 穎崴
    for s in stocks:
        if s['name'] == 'WW':
            s['name'] = '穎崴(WW)'
            s['type'] = '個股'
        if s['name'] == 'TSMC':
            s['name'] = '台積電'
            s['type'] = '個股'

    total_cost = flt(total_row[3])
    total_mkt  = flt(total_row[4])
    total_prof = flt(total_row[5])
    total_div  = flt(total_row[15].replace('NT$', '').replace(',', '')) if len(total_row) > 15 else 0
    total_yield= flt(total_row[19]) if len(total_row) > 19 else 0

    print('=' * 90)
    print(f'【{SHEET_NAME} 持股快照分析】')
    print('=' * 90)
    print(f'\n  總持有成本：{total_cost:>12,.0f}')
    print(f'  總市值    ：{total_mkt:>12,.0f}')
    print(f'  總損益    ：{total_prof:>+12,.0f}  ({total_prof/total_cost*100:+.2f}%)')
    print(f'  年度股利預估：{total_div:>11,.0f}')
    print(f'  整體殖利率 ：{total_yield:.2f}%')

    print()
    print('─' * 90)
    print(f'  {"股票":<10} {"類型":<8} {"持有股":>7} {"持有成本":>10} {"現今市值":>10} {"損益":>10} {"損益%":>7} {"現價":>8} {"均價":>8} {"殖利率":>6} {"年股利":>8}')
    print('  ' + '─' * 88)

    for s in sorted(stocks, key=lambda x: -x['mkt_val']):
        pf = s['profit']
        pct = s['profit_pct']
        sign = '+' if pf >= 0 else ''
        print(f'  {s["name"]:<10} {s["type"]:<8} {s["hold_sh"]:>7,.0f} '
              f'{s["hold_cost"]:>10,.0f} {s["mkt_val"]:>10,.0f} '
              f'{sign}{pf:>9,.0f} {sign}{pct:>6.1f}% '
              f'{s["curr_px"]:>8,.2f} {s["avg_px"]:>8,.2f} '
              f'{s["yield_pct"]:>5.1f}% {s["div_income"]:>8,.0f}')

    # 分類彙總
    print()
    print('=' * 60)
    print('【分類彙總】')
    cats = {'高股息ETF': [], '非高股息ETF': [], '個股': []}
    for s in stocks:
        cats[s['type']].append(s)

    cat_total_cost = 0
    cat_total_mkt  = 0
    rows_summary = []
    for cat, lst in cats.items():
        cost = sum(s['hold_cost'] for s in lst)
        mkt  = sum(s['mkt_val']  for s in lst)
        prof = mkt - cost
        div  = sum(s['div_income'] for s in lst)
        cat_total_cost += cost
        cat_total_mkt  += mkt
        rows_summary.append((cat, lst, cost, mkt, prof, div))

    print(f'  {"類別":<10} {"檔數":>4} {"持有成本":>12} {"成本佔比":>8} {"現今市值":>12} {"損益":>10} {"損益%":>7} {"年股利":>10}')
    print('  ' + '─' * 75)
    for cat, lst, cost, mkt, prof, div in rows_summary:
        pct_cost = cost / cat_total_cost * 100
        pct_prof = prof / cost * 100 if cost else 0
        print(f'  {cat:<10} {len(lst):>4} {cost:>12,.0f} {pct_cost:>7.1f}% {mkt:>12,.0f} '
              f'{prof:>+10,.0f} {pct_prof:>+6.1f}% {div:>10,.0f}')
    print('  ' + '─' * 75)
    print(f'  {"合計":<10} {len(stocks):>4} {cat_total_cost:>12,.0f} {"100.0%":>8} {cat_total_mkt:>12,.0f} '
          f'{cat_total_mkt-cat_total_cost:>+10,.0f} {(cat_total_mkt-cat_total_cost)/cat_total_cost*100:>+6.1f}%')

    # 各類持股成本佔比圖
    print()
    print('【成本佔比】')
    for cat, lst, cost, mkt, prof, div in rows_summary:
        pct = cost / cat_total_cost * 100
        bar = '█' * int(pct / 2)
        print(f'  {cat:<10} {pct:>5.1f}%  {bar}')

    # 損益排行
    print()
    print('【損益排行（現值損益）】')
    for s in sorted(stocks, key=lambda x: -x['profit']):
        pf = s['profit']
        sign = '+' if pf >= 0 else ''
        print(f'  {s["name"]:<10}  {sign}{pf:>12,.0f}  ({sign}{s["profit_pct"]:.1f}%)')

if __name__ == '__main__':
    main()
