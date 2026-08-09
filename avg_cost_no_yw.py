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
C_SELL_PX  = 10
C_SELL_SH  = 11
C_NET      = 15
C_RECV     = 21

SKIP_NAMES = {'開始質押', '從這開始', '國泰台灣', '2021/06/28 第一筆五萬投入開始買賣股票',
              '940', '穎崴', '穎崴_省略', '亞航'}

def get(row, col):
    return row[col].strip() if col < len(row) else ''

def flt(row, col):
    try:
        v = get(row, col).replace(',', '')
        return float(v) if v else None
    except:
        return None

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
    data = []
    for r in rows[1:]:
        name = get(r, C_NAME)
        if not name or name in SKIP_NAMES:
            continue
        buy_amt = flt(r, C_BUY_AMT)
        if buy_amt is None or buy_amt == 0:
            continue
        data.append(r)

    holding = [r for r in data if not get(r, C_SELL_DATE)]
    sold    = [r for r in data if get(r, C_SELL_DATE)]

    # ── 持有中各股加權均價 ──
    stocks = {}
    for row in holding:
        name    = get(row, C_NAME)
        buy_px  = flt(row, C_BUY_PX)
        buy_sh  = flt(row, C_BUY_SH)
        buy_amt = flt(row, C_BUY_AMT)
        buy_date= get(row, C_BUY_DATE) or ''

        if name not in stocks:
            stocks[name] = {'total_amt': 0, 'total_sh': 0, 'cnt': 0,
                            'earliest': buy_date, 'latest': buy_date}
        s = stocks[name]
        if buy_amt: s['total_amt'] += buy_amt
        if buy_sh:  s['total_sh']  += buy_sh
        s['cnt'] += 1
        if buy_date and buy_date < s['earliest']: s['earliest'] = buy_date
        if buy_date and buy_date > s['latest']:   s['latest']   = buy_date

    print('=' * 90)
    print('【持有中各股票平均成本（排除穎崴）】')
    print('=' * 90)
    print(f'  {"股票":<10} {"持有股數":>8} {"總成本":>12} {"均價":>8} {"最早買進":>12} {"最近買進":>12} {"筆數":>4}')
    print('  ' + '─' * 72)

    total_all = 0
    sorted_stocks = sorted(stocks.items(), key=lambda x: -x[1]['total_amt'])
    for name, s in sorted_stocks:
        sh  = s['total_sh']
        amt = s['total_amt']
        avg = amt / sh if sh else 0
        total_all += amt
        print(f'  {name:<10} {sh:>8,.0f} {amt:>12,.0f} {avg:>8.2f} {s["earliest"]:>12} {s["latest"]:>12} {s["cnt"]:>4}')

    print('  ' + '─' * 72)
    print(f'  {"合計":<10} {"":>8} {total_all:>12,.0f}')
    print()

    # ── 已賣出各股損益 ──
    sold_stocks = {}
    for row in sold:
        name    = get(row, C_NAME)
        buy_sh  = flt(row, C_BUY_SH)
        buy_amt = flt(row, C_BUY_AMT)
        sell_sh = flt(row, C_SELL_SH)
        net     = flt(row, C_NET)
        recv    = flt(row, C_RECV)

        if name not in sold_stocks:
            sold_stocks[name] = {'buy_amt': 0, 'buy_sh': 0, 'sell_sh': 0,
                                 'net': 0, 'recv': 0, 'cnt': 0}
        s = sold_stocks[name]
        if buy_amt: s['buy_amt'] += buy_amt
        if buy_sh:  s['buy_sh']  += buy_sh
        if sell_sh: s['sell_sh'] += sell_sh
        if net:     s['net']     += net
        if recv:    s['recv']    += recv
        s['cnt'] += 1

    print('=' * 75)
    print('【已賣出各股損益（排除穎崴）】')
    print('=' * 75)
    print(f'  {"股票":<10} {"賣出股數":>8} {"應收付":>12} {"實拿":>10} {"筆數":>4}')
    print('  ' + '─' * 50)

    total_net = total_recv = 0
    for name, s in sorted(sold_stocks.items(), key=lambda x: -abs(x[1]['net'])):
        net  = s['net']
        recv = s['recv']
        total_net  += net
        total_recv += recv
        print(f'  {name:<10} {s["sell_sh"]:>8,.0f} {net:>12,.0f} {recv:>10,.0f} {s["cnt"]:>4}')

    print('  ' + '─' * 50)
    print(f'  {"合計":<10} {"":>8} {total_net:>12,.0f} {total_recv:>10,.0f}')
    print()

    # ── 整體摘要 ──
    print('=' * 60)
    print('【整體摘要（排除穎崴）】')
    print(f'  涉及股票（持有中）：{len(stocks)} 檔')
    print(f'  持有中總成本：{total_all:,.0f} 元')
    print(f'  已賣出股票：{len(sold_stocks)} 檔')
    print(f'  已實現損益（應收付）：{total_net:,.0f} 元')
    print(f'  已實現損益（實拿）：{total_recv:,.0f} 元')

    # 持股比重
    print()
    print('【持股成本比重】')
    for name, s in sorted_stocks:
        pct = s['total_amt'] / total_all * 100
        bar = '█' * int(pct / 2)
        print(f'  {name:<8} {pct:>5.1f}%  {bar}')

if __name__ == '__main__':
    main()
