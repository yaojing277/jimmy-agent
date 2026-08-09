import pickle
import warnings
warnings.filterwarnings('ignore')

from google.auth.transport.requests import Request
from googleapiclient.discovery import build

TOKEN_FILE = '/Users/jimmy/Downloads/jimmy-agent/token.pickle'
SPREADSHEET_ID = '1UiqAHT2GUhKiviSz5NaLNclttlLVP3ujQMxJUn7Jyr8'
SHEET_NAME = '股票買賣紀錄'

C_NAME     = 0
C_BUY_DATE = 5
C_BUY_PX   = 6
C_BUY_SH   = 7
C_BUY_AMT  = 8
C_SELL_DATE= 9
C_SELL_SH  = 11
C_HOLD_SH  = 17

def to_float(row, col):
    try:
        if col >= len(row): return None
        v = str(row[col]).replace(',', '').replace(' ', '').strip()
        return float(v) if v else None
    except:
        return None

def get_cell(row, col):
    return row[col].strip() if col < len(row) and row[col].strip() else None

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
    # 排除標題、空列、備註列（買進日或名稱為空者視為非交易列）
    SKIP = {'開始質押', '從這開始', '國泰台灣', '2021/06/28 第一筆五萬投入開始買賣股票', '940'}
    data = []
    for r in rows[1:]:
        name = get_cell(r, C_NAME)
        if not name or name in SKIP:
            continue
        buy_amt = to_float(r, C_BUY_AMT)
        if buy_amt is None or buy_amt == 0:
            continue
        data.append(r)

    # 分群：持有中 / 已賣出
    holding = [r for r in data if not get_cell(r, C_SELL_DATE)]
    sold    = [r for r in data if get_cell(r, C_SELL_DATE)]

    # ── 持有中：各股加權平均成本 ──
    stocks = {}
    for row in holding:
        name    = get_cell(row, C_NAME)
        buy_px  = to_float(row, C_BUY_PX)
        buy_sh  = to_float(row, C_BUY_SH)
        buy_amt = to_float(row, C_BUY_AMT)
        buy_date= get_cell(row, C_BUY_DATE) or ''

        if name not in stocks:
            stocks[name] = {
                'total_amt': 0, 'total_sh': 0,
                'lots': [], 'earliest': buy_date, 'latest': buy_date
            }
        s = stocks[name]
        if buy_amt: s['total_amt'] += buy_amt
        if buy_sh:  s['total_sh']  += buy_sh
        s['lots'].append({'date': buy_date, 'px': buy_px, 'sh': buy_sh, 'amt': buy_amt})
        if buy_date and buy_date < s['earliest']: s['earliest'] = buy_date
        if buy_date and buy_date > s['latest']:   s['latest']   = buy_date

    print('=' * 85)
    print('【持有中各股票平均成本】')
    print('=' * 85)
    print(f'  {"股票":<10} {"持有股數":>8} {"總成本":>12} {"均價":>8} {"最早買進":>12} {"最近買進":>12} {"筆數":>4}')
    print('  ' + '─' * 70)

    total_all = 0
    sorted_stocks = sorted(stocks.items(), key=lambda x: -x[1]['total_amt'])
    for name, s in sorted_stocks:
        sh  = s['total_sh']
        amt = s['total_amt']
        avg = amt / sh if sh else 0
        total_all += amt
        print(f'  {name:<10} {sh:>8,.0f} {amt:>12,.0f} {avg:>8.2f} {s["earliest"]:>12} {s["latest"]:>12} {len(s["lots"]):>4}')

    print('  ' + '─' * 70)
    print(f'  {"合計":<10} {"":>8} {total_all:>12,.0f}')
    print()

    # ── 已賣出：各股加權平均買進成本 vs 應收付 ──
    sold_stocks = {}
    for row in sold:
        name    = get_cell(row, C_NAME)
        buy_px  = to_float(row, C_BUY_PX)
        buy_sh  = to_float(row, C_BUY_SH)
        buy_amt = to_float(row, C_BUY_AMT)
        sell_sh = to_float(row, C_SELL_SH)

        if name not in sold_stocks:
            sold_stocks[name] = {'buy_amt': 0, 'buy_sh': 0, 'sell_sh': 0, 'cnt': 0}
        s = sold_stocks[name]
        if buy_amt: s['buy_amt'] += buy_amt
        if buy_sh:  s['buy_sh']  += buy_sh
        if sell_sh: s['sell_sh'] += sell_sh
        s['cnt'] += 1

    print('=' * 65)
    print('【已賣出各股票平均買進成本】')
    print('=' * 65)
    print(f'  {"股票":<10} {"買進股數":>8} {"買進均價":>8} {"賣出股數":>8} {"筆數":>4}')
    print('  ' + '─' * 48)
    for name, s in sorted(sold_stocks.items(), key=lambda x: -x[1]['buy_amt']):
        avg = s['buy_amt'] / s['buy_sh'] if s['buy_sh'] else 0
        print(f'  {name:<10} {s["buy_sh"]:>8,.0f} {avg:>8.2f} {s["sell_sh"]:>8,.0f} {s["cnt"]:>4}')
    print()

    # ── 明細：前三大持股逐筆列出 ──
    print('=' * 65)
    print('【主要持股逐筆明細（前 3 大成本）】')
    for name, s in sorted_stocks[:3]:
        avg = s['total_amt'] / s['total_sh'] if s['total_sh'] else 0
        print(f'\n  ▶ {name}  均價 {avg:.2f}  總股數 {s["total_sh"]:.0f}  總成本 {s["total_amt"]:,.0f}')
        print(f'    {"買進日":<12} {"價格":>8} {"股數":>6} {"成本":>10}')
        print('    ' + '─' * 38)
        for lot in sorted(s['lots'], key=lambda x: x['date']):
            d  = lot['date'] or ''
            px = f'{lot["px"]:.2f}' if lot['px'] else ''
            sh = f'{int(lot["sh"])}' if lot['sh'] else ''
            am = f'{lot["amt"]:,.0f}' if lot['amt'] else ''
            print(f'    {d:<12} {px:>8} {sh:>6} {am:>10}')

if __name__ == '__main__':
    main()
