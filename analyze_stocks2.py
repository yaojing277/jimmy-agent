import pickle
import warnings
warnings.filterwarnings('ignore')

from google.auth.transport.requests import Request
from googleapiclient.discovery import build

TOKEN_FILE = '/Users/jimmy/Downloads/jimmy-agent/token.pickle'
SPREADSHEET_ID = '1UiqAHT2GUhKiviSz5NaLNclttlLVP3ujQMxJUn7Jyr8'
SHEET_NAME = '股票買賣紀錄'

# 欄位索引（依實際資料）
C_NAME    = 0   # 股票名稱/代號
C_DAYS    = 1   # 幾天前
C_AVG_NOW = 2   # 至今均價
C_BUY_DATE= 5   # 買進日
C_BUY_PX  = 6   # 買進價格
C_BUY_SH  = 7   # 買進股數
C_BUY_AMT = 8   # 買進成本
C_SELL_DATE=9   # 賣出日
C_SELL_PX = 10  # 賣出價格
C_SELL_SH = 11  # 賣出股數
C_SELL_AMT= 12  # 賣出成本
C_FEE     = 13  # 手續費
C_TAX     = 14  # 交易稅
C_NET     = 15  # 應收付
C_HOLD_PX = 16  # 持有價格
C_HOLD_SH = 17  # 持有股數
C_HOLD_AMT= 18  # 持有成本
C_DIFF_PX = 19  # 價差
C_DIFF_AMT= 20  # 價差金額
C_RECV    = 21  # 實拿金額

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
    data = [r for r in rows[1:] if r and get_cell(r, C_NAME)]  # 跳過標題與空列

    print(f'=== 股票買賣紀錄分析 ===')
    print(f'有效資料：{len(data)} 筆\n')

    # ── 持有中 vs 已賣出 ──
    holding = []
    sold = []
    for row in data:
        sell_date = get_cell(row, C_SELL_DATE)
        if sell_date:
            sold.append(row)
        else:
            holding.append(row)

    print(f'【持有中】{len(holding)} 筆  |  【已賣出】{len(sold)} 筆\n')

    # ── 持有部位明細 ──
    print('─' * 75)
    print(f'【持有中部位】')
    print(f'  {"股票":<10} {"買進日":<12} {"買進價":>8} {"股數":>6} {"買進成本":>10} {"持有成本":>10} {"價差":>8} {"價差金額":>10}')
    print('  ' + '─' * 73)

    total_buy_cost = 0
    total_hold_cost = 0
    total_diff = 0

    for row in holding:
        name    = get_cell(row, C_NAME) or ''
        bdate   = get_cell(row, C_BUY_DATE) or ''
        buy_px  = to_float(row, C_BUY_PX)
        buy_sh  = to_float(row, C_BUY_SH)
        buy_amt = to_float(row, C_BUY_AMT)
        hold_amt= to_float(row, C_HOLD_AMT)
        diff_px = to_float(row, C_DIFF_PX)
        diff_amt= to_float(row, C_DIFF_AMT)

        if buy_amt: total_buy_cost += buy_amt
        if hold_amt: total_hold_cost += hold_amt
        if diff_amt: total_diff += diff_amt

        buy_px_s  = f'{buy_px:.2f}' if buy_px is not None else ''
        buy_sh_s  = f'{int(buy_sh)}' if buy_sh is not None else ''
        buy_amt_s = f'{buy_amt:,.0f}' if buy_amt is not None else '0'
        hold_amt_s= f'{hold_amt:,.0f}' if hold_amt is not None else '0'
        diff_px_s = f'{diff_px:.1f}' if diff_px is not None else '0.0'
        diff_amt_s= f'{diff_amt:,.0f}' if diff_amt is not None else '0'
        print(f'  {name:<10} {bdate:<12} '
              f'{buy_px_s:>8} {buy_sh_s:>6} '
              f'{buy_amt_s:>10} {hold_amt_s:>10} '
              f'{diff_px_s:>8} {diff_amt_s:>10}')

    print('  ' + '─' * 73)
    print(f'  {"合計":<10} {"":12} {"":>8} {"":>6} {total_buy_cost:>10,.0f} {total_hold_cost:>10,.0f} {"":>8} {total_diff:>10,.0f}')
    print()

    # ── 已賣出損益 ──
    print('─' * 75)
    print(f'【已賣出紀錄】')
    print(f'  {"股票":<10} {"買進日":<12} {"賣出日":<12} {"股數":>6} {"應收付":>10} {"實拿":>10}')
    print('  ' + '─' * 60)

    total_net = 0
    total_recv = 0
    win = 0
    lose = 0

    for row in sold:
        name     = get_cell(row, C_NAME) or ''
        bdate    = get_cell(row, C_BUY_DATE) or ''
        sdate    = get_cell(row, C_SELL_DATE) or ''
        sell_sh  = to_float(row, C_SELL_SH)
        net      = to_float(row, C_NET)
        recv     = to_float(row, C_RECV)

        if net: total_net += net
        if recv: total_recv += recv
        if recv:
            if recv > 0: win += 1
            else: lose += 1

        print(f'  {name:<10} {bdate:<12} {sdate:<12} '
              f'{int(sell_sh) if sell_sh else "":>6} '
              f'{net if net else 0:>10,.0f} '
              f'{recv if recv else 0:>10,.0f}')

    print('  ' + '─' * 60)
    print(f'  {"合計":<10} {"":12} {"":12} {"":>6} {total_net:>10,.0f} {total_recv:>10,.0f}')
    print()

    # ── 整體摘要 ──
    print('─' * 75)
    print('【整體摘要】')
    all_fees = sum(to_float(r, C_FEE) or 0 for r in data)
    all_tax  = sum(to_float(r, C_TAX) or 0 for r in data)

    # 股票種類統計
    stock_count = {}
    for row in data:
        n = get_cell(row, C_NAME)
        if n: stock_count[n] = stock_count.get(n, 0) + 1

    print(f'  涉及股票：{len(stock_count)} 檔')
    print(f'  交易總筆數：{len(data)} 筆（買進）')
    print(f'  持有中成本：{total_buy_cost:,.0f} 元')
    print(f'  持有中未實現價差：{total_diff:,.0f} 元')
    print(f'  已實現損益（應收付）：{total_net:,.0f} 元')
    print(f'  已實現損益（實拿）：{total_recv:,.0f} 元')
    print(f'  累計手續費：{all_fees:,.0f} 元')
    print(f'  累計交易稅：{all_tax:,.0f} 元')
    if win + lose > 0:
        print(f'  勝率：{win}/{win+lose} = {win/(win+lose)*100:.1f}%')

    print()
    print('【各股票交易次數】')
    for s, cnt in sorted(stock_count.items(), key=lambda x: -x[1]):
        print(f'  {s}: {cnt} 筆')

if __name__ == '__main__':
    main()
