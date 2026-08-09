import pickle
import warnings
warnings.filterwarnings('ignore')

from google.auth.transport.requests import Request
from googleapiclient.discovery import build

TOKEN_FILE = '/Users/jimmy/Downloads/jimmy-agent/token.pickle'
SPREADSHEET_ID = '1UiqAHT2GUhKiviSz5NaLNclttlLVP3ujQMxJUn7Jyr8'
SHEET_NAME = '股票買賣紀錄'

def get_credentials():
    with open(TOKEN_FILE, 'rb') as f:
        creds = pickle.load(f)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds

def main():
    creds = get_credentials()

    # 先用 Sheets API 讀取，需要額外 scope，改用 Drive API 匯出
    drive_service = build('drive', 'v3', credentials=creds)

    # 取得所有 sheet 名稱（先試 Sheets API，若 scope 不足則提示）
    try:
        sheets_service = build('sheets', 'v4', credentials=creds)
        meta = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
        sheets = [s['properties']['title'] for s in meta.get('sheets', [])]
        print(f'試算表共有 {len(sheets)} 個分頁：')
        for s in sheets:
            print(f'  - {s}')
        print()

        if SHEET_NAME not in sheets:
            print(f'找不到「{SHEET_NAME}」分頁，請確認名稱。')
            return

        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f'{SHEET_NAME}'
        ).execute()

        rows = result.get('values', [])
        if not rows:
            print('分頁內沒有資料。')
            return

        print(f'「{SHEET_NAME}」共 {len(rows)} 列（含標題）\n')
        print('=== 原始資料預覽（前 5 列）===')
        for i, row in enumerate(rows[:5]):
            print(f'  [{i}] {row}')
        print()

        # 回傳完整資料供後續分析
        return rows

    except Exception as e:
        if 'insufficient authentication scopes' in str(e).lower() or '403' in str(e):
            print('需要重新授權以取得 Sheets 存取權限，請刪除 token.pickle 後重新執行 search_drive.py')
        else:
            raise e

if __name__ == '__main__':
    rows = main()
    if not rows or len(rows) < 2:
        exit()

    headers = rows[0]
    data = rows[1:]

    print(f'欄位：{headers}\n')
    print(f'資料筆數：{len(data)} 筆\n')

    # 嘗試識別欄位索引
    def find_col(keywords):
        for kw in keywords:
            for i, h in enumerate(headers):
                if kw in str(h):
                    return i
        return None

    col_date     = find_col(['日期', '交易日', '成交日'])
    col_stock    = find_col(['股票', '代號', '股號', '名稱'])
    col_action   = find_col(['買賣', '動作', '操作', '交易'])
    col_qty      = find_col(['數量', '股數', '張數', '成交量'])
    col_price    = find_col(['價格', '成交價', '單價', '股價'])
    col_amount   = find_col(['金額', '成交金額', '總額'])
    col_fee      = find_col(['手續費', '費用', '交易費'])
    col_tax      = find_col(['稅', '證交稅'])
    col_profit   = find_col(['損益', '獲利', '盈虧', '利潤'])

    print('=== 欄位對應 ===')
    mapping = {
        '日期': col_date, '股票': col_stock, '買賣': col_action,
        '數量': col_qty, '價格': col_price, '金額': col_amount,
        '手續費': col_fee, '稅': col_tax, '損益': col_profit
    }
    for k, v in mapping.items():
        print(f'  {k}: {headers[v] if v is not None else "（未找到）"}（欄 {v}）')
    print()

    # 統計分析
    def to_float(val):
        try:
            return float(str(val).replace(',', '').replace(' ', '').replace('%', ''))
        except:
            return None

    print('=== 資料摘要 ===')

    # 股票清單
    if col_stock is not None:
        stocks = {}
        for row in data:
            name = row[col_stock] if col_stock < len(row) else ''
            if name:
                stocks[name] = stocks.get(name, 0) + 1
        print(f'涉及股票（{len(stocks)} 檔）：')
        for s, cnt in sorted(stocks.items(), key=lambda x: -x[1]):
            print(f'  {s}: {cnt} 筆')
        print()

    # 買賣統計
    if col_action is not None:
        actions = {}
        for row in data:
            act = row[col_action] if col_action < len(row) else ''
            if act:
                actions[act] = actions.get(act, 0) + 1
        print(f'交易類型：')
        for a, cnt in actions.items():
            print(f'  {a}: {cnt} 筆')
        print()

    # 金額統計
    if col_amount is not None:
        amounts = [to_float(row[col_amount]) for row in data if col_amount < len(row)]
        amounts = [a for a in amounts if a is not None]
        if amounts:
            print(f'成交金額：')
            print(f'  總計：{sum(amounts):,.0f}')
            print(f'  平均：{sum(amounts)/len(amounts):,.0f}')
            print(f'  最大：{max(amounts):,.0f}')
            print(f'  最小：{min(amounts):,.0f}')
            print()

    # 損益統計
    if col_profit is not None:
        profits = [to_float(row[col_profit]) for row in data if col_profit < len(row)]
        profits = [p for p in profits if p is not None]
        if profits:
            gain = [p for p in profits if p > 0]
            loss = [p for p in profits if p < 0]
            print(f'損益分析：')
            print(f'  總損益：{sum(profits):,.0f}')
            print(f'  獲利筆數：{len(gain)} 筆，合計：{sum(gain):,.0f}')
            print(f'  虧損筆數：{len(loss)} 筆，合計：{sum(loss):,.0f}')
            if profits:
                win_rate = len(gain) / len(profits) * 100
                print(f'  勝率：{win_rate:.1f}%')
            print()

    # 日期範圍
    if col_date is not None:
        dates = [row[col_date] for row in data if col_date < len(row) and row[col_date]]
        if dates:
            print(f'交易日期範圍：{min(dates)} ～ {max(dates)}')
            print(f'共 {len(dates)} 筆交易紀錄')
