import pickle
import warnings
warnings.filterwarnings('ignore')

from google.auth.transport.requests import Request
from googleapiclient.discovery import build

TOKEN_FILE = '/Users/jimmy/Downloads/jimmy-agent/token.pickle'
SPREADSHEET_ID = '1UiqAHT2GUhKiviSz5NaLNclttlLVP3ujQMxJUn7Jyr8'
SHEET_NAME = '股票買賣紀錄'

C_NAME      = 0
C_DAYS      = 1
C_AVG_NOW   = 2
C_BUY_DATE  = 5
C_BUY_PX    = 6
C_BUY_SH    = 7
C_BUY_AMT   = 8
C_SELL_DATE = 9
C_SELL_PX   = 10
C_SELL_SH   = 11
C_SELL_AMT  = 12
C_FEE       = 13
C_TAX       = 14
C_NET       = 15
C_HOLD_PX   = 16
C_HOLD_SH   = 17
C_HOLD_AMT  = 18
C_DIFF_PX   = 19
C_DIFF_AMT  = 20
C_RECV      = 21

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
    headers = rows[0]

    # 找出所有穎崴相關列（含原始列號），忽略穎崴_省略
    yw_rows = []
    for i, row in enumerate(rows[1:], start=2):  # 列號從2開始（含標題）
        name = get(row, C_NAME)
        if name == '穎崴':
            yw_rows.append((i, row))

    print(f'找到穎崴相關紀錄：{len(yw_rows)} 筆\n')

    # 顯示原始所有欄位
    print('=' * 110)
    print(f'  {"列":<4} {"名稱":<8} {"買進日":<12} {"買進價":>8} {"買進股":>6} {"買進成本":>10}'
          f' {"賣出日":<12} {"賣出價":>8} {"賣出股":>6} {"賣出成本":>10}'
          f' {"手續費":>6} {"交易稅":>6} {"應收付":>10} {"實拿":>10}')
    print('  ' + '─' * 108)

    total_buy_amt  = 0
    total_sell_amt = 0
    total_net      = 0
    total_recv     = 0
    total_buy_sh   = 0
    total_sell_sh  = 0

    buy_rows  = []
    sell_rows = []

    for lineno, row in yw_rows:
        buy_date  = get(row, C_BUY_DATE)
        buy_px    = flt(row, C_BUY_PX)
        buy_sh    = flt(row, C_BUY_SH)
        buy_amt   = flt(row, C_BUY_AMT)
        sell_date = get(row, C_SELL_DATE)
        sell_px   = flt(row, C_SELL_PX)
        sell_sh   = flt(row, C_SELL_SH)
        sell_amt  = flt(row, C_SELL_AMT)
        fee       = flt(row, C_FEE)
        tax       = flt(row, C_TAX)
        net       = flt(row, C_NET)
        recv      = flt(row, C_RECV)

        has_sell = bool(sell_date or sell_px or sell_sh)

        tag = '【賣】' if has_sell else '    '

        bpx  = f'{buy_px:.0f}'   if buy_px   else ''
        bsh  = f'{int(buy_sh)}'  if buy_sh   else ''
        bamt = f'{buy_amt:,.0f}' if buy_amt  else ''
        spx  = f'{sell_px:.0f}'  if sell_px  else ''
        ssh  = f'{int(sell_sh)}' if sell_sh  else ''
        samt = f'{sell_amt:,.0f}'if sell_amt else ''
        fe   = f'{fee:,.0f}'     if fee      else ''
        tx   = f'{tax:,.0f}'     if tax      else ''
        nt   = f'{net:,.0f}'     if net      else ''
        rc   = f'{recv:,.0f}'    if recv     else ''

        print(f'{tag} {lineno:<4} {buy_date:<12} {bpx:>8} {bsh:>6} {bamt:>10}'
              f' {sell_date:<12} {spx:>8} {ssh:>6} {samt:>10}'
              f' {fe:>6} {tx:>6} {nt:>10} {rc:>10}')

        if buy_amt:  total_buy_amt  += buy_amt
        if sell_amt: total_sell_amt += sell_amt
        if net:      total_net      += net
        if recv:     total_recv     += recv
        if buy_sh:   total_buy_sh   += buy_sh
        if sell_sh:  total_sell_sh  += sell_sh

        if has_sell:
            sell_rows.append((lineno, row))
        else:
            buy_rows.append((lineno, row))

    print('  ' + '─' * 108)
    print(f'  合計  買進股：{total_buy_sh:.0f}  買進成本：{total_buy_amt:,.0f}'
          f'  賣出股：{total_sell_sh:.0f}  賣出成本：{total_sell_amt:,.0f}'
          f'  應收付：{total_net:,.0f}  實拿：{total_recv:,.0f}')

    print()
    print(f'【持有中】{len(buy_rows)} 筆  |  【已賣出】{len(sell_rows)} 筆')

    # 持有中統計
    hold_sh  = sum(flt(r, C_BUY_SH)  or 0 for _, r in buy_rows)
    hold_amt = sum(flt(r, C_BUY_AMT) or 0 for _, r in buy_rows)
    avg = hold_amt / hold_sh if hold_sh else 0
    print(f'\n持有中：{hold_sh:.0f} 股  總成本：{hold_amt:,.0f}  加權均價：{avg:.2f}')

    # 賣出損益
    if sell_rows:
        print(f'\n已賣出明細：')
        for lineno, row in sell_rows:
            net  = flt(row, C_NET)
            recv = flt(row, C_RECV)
            diff_amt = flt(row, C_DIFF_AMT)
            print(f'  列{lineno}  賣出日:{get(row,C_SELL_DATE)}  賣出價:{get(row,C_SELL_PX)}'
                  f'  賣出股:{get(row,C_SELL_SH)}  應收付:{net:,.0f}' if net else
                  f'  列{lineno}  賣出日:{get(row,C_SELL_DATE)}  賣出價:{get(row,C_SELL_PX)}'
                  f'  賣出股:{get(row,C_SELL_SH)}  應收付:（空）')
            # 顯示原始所有欄位值（debug用）
            print(f'    原始資料：{row}')

if __name__ == '__main__':
    main()
