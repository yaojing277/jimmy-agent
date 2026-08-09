# 股價試算 — 試算表維護工具

維護 Google 試算表「**股價試算**」(`1UiqAHT2GUhKiviSz5NaLNclttlLVP3ujQMxJUn7Jyr8`) 的兩支命令列工具。

| 腳本 | 功能 |
|---|---|
| `update_close_price.py` | 維護 **Jimmy_260612** 持股快照分頁:抓收盤價、補整列、健檢 |
| `sheet_value_guard.py` | 改公式的安全網:改前拍快照、改後比對計算值(任何分頁通用) |
| `trade_entry_server.py` | **買進紀錄輸入網頁**:填代號/價格/股數/日期,自動算成本並新增到「股票買賣紀錄」最前列 |

資料源:Yahoo Finance chart API(免金鑰)。寫入:Google Sheets API(OAuth)。

---

## 前置需求(只需做一次)

```bash
pip3 install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib
```

需要同資料夾的 **`token.json`**(OAuth 授權後產生)。取得方式見 `SHEETS_API_SETUP.md`;
`credentials.json` 為 GCP 下載的用戶端憑證,首次授權用。

---

## 1. `update_close_price.py`

維護 **Jimmy_260612** 分頁。欄位結構:A 股票代號 / B 買進價 / C 持有股數 / D~G 成本市值損益 /
H~K… 多個日期收盤欄(標頭=日期,最右為「現今價」) / L 購入數 M 購入成本 N 平均股價 /
O 配息數 P 除息月 Q 最近股利 R 最近股利所得 / S~W 價差與殖利率。

| 指令 | 用途 |
|---|---|
| `inspect` | 看 A 欄與價格欄現況、標出缺值列 |
| `fill` | 只補某個日期欄的收盤價 |
| `fill-row` | **補整列**:各日期欄收盤 + D~N/S~W 公式 + O/P/Q/R 配息,一鍵到位 |
| `audit` | **全表健檢**:公式樣式 / 日期價缺值 / 錯誤值 / 收盤價對照 Yahoo |

```bash
# 看現況、標出缺值
python3 update_close_price.py inspect

# 只補 K 欄(6/12)收盤價;--auto 自動補所有缺值列
python3 update_close_price.py fill --rows 23,24
python3 update_close_price.py fill --auto

# 補整列(新列只填好 A/B/C 後執行)
python3 update_close_price.py fill-row --rows 25
python3 update_close_price.py fill-row --rows 25 --code 2330    # 代號覆寫
python3 update_close_price.py fill-row --rows 25,26 --yes       # 多列、免確認
python3 update_close_price.py fill-row --rows 25 --freq 4       # 指定一年配 4 次

# 健檢
python3 update_close_price.py audit
python3 update_close_price.py audit --no-price                  # 跳過抓價,較快
```

**設計重點**
- **標頭定位**:欄位靠標頭關鍵字解析,日後插入日期欄、欄位右移也不會誤判。
- **現今價自動指向最後日期欄**:新增 6/13、6/14… 欄後,公式自動跟上。
- **公式單一來源**:`fill-row` 寫入與 `audit` 比對共用 `expected_formulas()`,不會兩邊走鐘。
- **配息頻率**:`--freq` > 既有 O > 自動偵測;槓桿/累積型 ETF(不配息)→ O/Q/R=0。
- 中文/英文名(WW=6515 穎崴、台達電=2308…)需在腳本 `ALIAS` 補對照。

**典型流程**:新增股票 → 填好 A/B/C → `fill-row --rows N` → `audit` 確認無誤。

---

## 2. `sheet_value_guard.py`

批次改公式時的安全網:**改前拍快照、改後比對計算值**,確保「只動寫法、不動結果」。
唯讀 scope,自己不會改任何資料。適用任何分頁/範圍。

```bash
# 1) 改前拍快照
python3 sheet_value_guard.py snapshot --sheet '股票買賣紀錄' --range C1:E200

# 2)（動手改公式）

# 3) 改後比對 — 任何被改動的值會逐格列出
python3 sheet_value_guard.py diff --sheet '股票買賣紀錄' --range C1:E200
```

| 選項 | 說明 |
|---|---|
| `--sheet` | 分頁名稱(必填) |
| `--range` | 範圍,例 `C1:E200`(必填) |
| `--name` | 快照名稱(預設 `sheet_range`) |
| `--ssid` | 試算表 ID(預設「股價試算」) |

- 比的是**計算值**(不是公式);值變成 `#DIV/0!`、`#REF!` 也算差異。
- 輸出格式:`C143: 前 203.79 → 後 #DIV/0!`,精準定位。
- 快照存於 `.value_snapshots/`。

> 由來:批次清理「股票買賣紀錄」C/D/E 公式時,靠這套比對當場抓到 3 列賣出紀錄
> 變成 `#DIV/0!`(因賣出列的 B 欄參考賣出日而非買進日),避免改壞。

---

## 3. `trade_entry_server.py`

買進紀錄快速輸入網頁。填「代號 / 買進價格 / 買進股數 / 買進日期」,自動算成交金額、
元大手續費(0.1425%、最低20元)、買進成本,並插入到「股票買賣紀錄」**最前一筆資料列
(動態偵測,不寫死列號)**;B(幾天前)、C~E(均價)、I(成本) 自動帶入,A 純數字代號存成文字。

```bash
python3 trade_entry_server.py
# 瀏覽器開 http://localhost:8765
```

- **試算**:即時算成本 + 抓當日收盤比對(買價偏離 >6% 會黃字提醒),不寫入。
- **確認新增**:寫入分頁。零額外套件(Python 內建 http.server),只綁 127.0.0.1。
- 手續費率欄可改(有折讓時,如電子下單 6 折填 0.0855)。

---

## 檔案一覽

| 檔案 | 說明 |
|---|---|
| `update_close_price.py` | Jimmy_260612 分頁維護(inspect/fill/fill-row/audit) |
| `sheet_value_guard.py` | 改公式安全網(snapshot/diff) |
| `trade_entry_server.py` | 買進紀錄輸入網頁(localhost:8765) |
| `sheets_writer.py` | 「股票買賣紀錄」分頁寫入(交割明細匯入用) |
| `token.json` / `credentials.json` | OAuth 憑證 |
| `SHEETS_API_SETUP.md` | 憑證取得步驟 |
| `.value_snapshots/` | value_guard 快照存放處 |

---
## 2026-07-20 Claude 執行紀錄

觸發語「更新股價+同步股價」已被 Claude (Cowork) 接收並處理。

**已建立執行檔：**
- `~/Desktop/執行股價更新.command` — 雙擊即可執行

**執行順序：**
1. `python3 update_stock_price.py update --yes`
2. `python3 update_wealth_os.py update --yes`

注意：2026-07-20 為週日，台股休市，收盤價可能留白。
