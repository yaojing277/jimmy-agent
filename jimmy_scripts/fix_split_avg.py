"""
「股票買賣紀錄」C~E 均價公式改為只算分割後的價格（2026-10-04）。

做法：
  - 新增分頁「股票分割」：A 代號（文字）／B 恢復交易日／C 分割比例／D 來源
  - C~E 的 FILTER 多一個條件：F >= 該代號「最近一次 <= 本列日期」的分割日
      MAXIFS(股票分割!$B:$B, 股票分割!$A:$A, A#, 股票分割!$B:$B, "<="&F#)
    沒分割過的代號 MAXIFS 回 0，條件恆成立，結果不變。
    分割前的列往下看的本來就都是分割前的價格，所以也不受影響。
  - 日後再有分割，只要在「股票分割」加一列，公式不用動。

用法：python3 fix_split_avg.py dry-run | write
"""
import re
import sys
import warnings

warnings.filterwarnings("ignore")
from sheets_writer import get_service, SPREADSHEET_ID, SHEET_NAME

SPLIT_SHEET = "股票分割"
SPLITS = [  # 代號, 恢復交易日（TWSE STOCK_DAY 分割後第一個交易日）, 比例, 來源
    ["'0050", "2025/06/18", "1拆4", "TWSE STOCK_DAY：6/10 188.65 → 6/18 47.57"],
    ["00631L", "2026/03/31", "1拆22", "TWSE STOCK_DAY：3/24 443.15 → 3/31 19.26"],
    ["00685L", "2026/07/07", "1拆24", "TWSE STOCK_DAY：6/30 收盤 306.00；7/07 收盤 12.23、漲跌 −0.52 → 參考價 12.75 = 306÷24"],
]
LO = 'MAXIFS({s}!$B:$B, {s}!$A:$A, A{r}, {s}!$B:$B, "<="&F{r})'


def new_formulas(r):
    lo = LO.format(s=SPLIT_SHEET, r=r)
    return [
        f"=AVERAGE(FILTER(G{r}:G, A{r}:A=A{r}, F{r}:F<=(TODAY()-B{r}), F{r}:F>={lo}))",
        # D／E：以「該筆交易日」往前推半年／一年（2026-10-04 起；原本以 TODAY() 推算，舊列會變 #N/A）
        f"=AVERAGE(FILTER(G{r}:G, A{r}:A=A{r}, F{r}:F<=F{r}, F{r}:F>=(F{r}-180), F{r}:F>={lo}))",
        f"=AVERAGE(FILTER(G{r}:G, A{r}:A=A{r}, F{r}:F<=F{r}, F{r}:F>=(F{r}-365), F{r}:F>={lo}))",
    ]


def target_rows(svc):
    f = svc.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID, range=f"{SHEET_NAME}!C1:E2000",
        valueRenderOption="FORMULA").execute().get("values", [])
    rows = []
    for i, r in enumerate(f, 1):
        c = (r + [""])[0]
        if isinstance(c, str) and re.fullmatch(
                rf"=AVERAGE\(FILTER\(G{i}:G, A{i}:A=A{i}, F{i}:F<=\(TODAY\(\)-B{i}\)\)\)", c):
            rows.append(i)
    return rows


def main(cmd):
    svc = get_service()
    rows = target_rows(svc)
    print(f"符合原均價公式的列：{len(rows)} 列（{rows[0]}~{rows[-1]}）")
    print("第一列新公式 C：", new_formulas(rows[0])[0])
    if cmd != "write":
        return
    ss = svc.spreadsheets()
    titles = [s["properties"]["title"] for s in ss.get(spreadsheetId=SPREADSHEET_ID).execute()["sheets"]]
    if SPLIT_SHEET not in titles:
        ss.batchUpdate(spreadsheetId=SPREADSHEET_ID, body={"requests": [
            {"addSheet": {"properties": {"title": SPLIT_SHEET}}}]}).execute()
    ss.values().update(spreadsheetId=SPREADSHEET_ID, range=f"{SPLIT_SHEET}!A1:D{len(SPLITS) + 1}",
                       valueInputOption="USER_ENTERED",
                       body={"values": [["代號", "恢復交易日", "比例", "來源"]] + SPLITS}).execute()
    data = [{"range": f"{SHEET_NAME}!C{r}:E{r}", "values": [new_formulas(r)]} for r in rows]
    ss.values().batchUpdate(spreadsheetId=SPREADSHEET_ID, body={
        "valueInputOption": "USER_ENTERED", "data": data}).execute()
    print(f"✓ 分頁「{SPLIT_SHEET}」已寫入 {len(SPLITS)} 筆；C~E 已更新 {len(rows)} 列")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dry-run")
