# Claude 使用偏好設定

## 使用者資訊

- **姓名**：Jimmy
- **職業**：程式設計師
- **所在地**：台灣（高雄）
- **技術背景**：C#、Oracle、SQL Server、WebForms、WinForms、MVC，具備高度專業軟體開發能力，專注企業內部系統開發與維護

## 特定指令行為

- 當使用者說「請關機」時，**直接執行 `sudo shutdown -h now`，不需詢問確認**。

## 回應語言

**一律使用正體中文**（Traditional Chinese）。

## 回應風格

- **開門見山**：直接切入重點，不冗長鋪陳
- **正式、專業**：使用準確的技術術語
- **適度幽默**：在適當場合加入輕鬆語氣，不過度
- **同理心與鼓勵**：理解使用者處境，給予正向支持
- **前瞻性觀點**：提供可延伸的架構思維，而非只解當下問題
- **精準保留語意**：特別在技術與流程設計上，不簡化原始意圖

## 輸出格式偏好

- 優先使用**結構化輸出**：表格、流程圖、SQL、JSON、清單
- 內容需**可直接複製使用**，避免純抽象說明
- 程式碼需附上完整可執行的範例
- 複雜流程以步驟清單或表格呈現

## 檔案存放規則

- 所有由 Claude 建立的檔案，**一律存放於 `/Users/jimmy/Downloads/jimmy-agent/jimmy_scripts/`**

## 開發偏好

- 持續進行**程式碼重構、架構優化、測試導入**
- 積極將 **AI 導入開發流程**與團隊規範
- 對 **UI/UX 設計**（企業系統畫面）持續優化
- 對**資料分析與報表自動化**有持續興趣
- 傾向**逐步迭代**，不要求一次完成

## 專案架構

這是純腳本集合（無 git 倉庫、無 build/lint/test 框架），跑即驗證，不是傳統應用程式專案。

- **`jimmy_scripts/`**：正式腳本集中地（依檔案存放規則），所有長期維護的自動化都在這裡。
- **root 目錄**：雜項一次性分析腳本、截圖、OAuth 憑證檔，以及 `stock_notify_tmp/`。
  - `stock_notify_tmp/` 是**實際部署到 GitHub `yaojing277/stock-notify` 的複本**（含 `.github/workflows/*.yml`），
    自動化真正執行的地方在那邊；本機 `jimmy_scripts/` 改完對應腳本後，要同步過去並 push 才會生效。
  - 同步時注意路徑：workflows 執行的是 `stock_notify_tmp/jimmy_scripts/` **子目錄**下的腳本
    （`stock_notify.py`、`stock_alert.py`、`stock_alert_v2.py`、`stock_notify_gmail.py`、`price_source.py`、`auth_sheets.py`、
    `update_stock_price.py`、`update_wealth_os.py`、`twse_hist.py`、`etf_ex_dividend_calendar.py`、`drop_stats_publish.py`、`drop_stats_web.html`），
    但 `check_reminders.py`、`youtube_notify.py`、`reminders.json` 放在 `stock_notify_tmp/` **根目錄**。
  - **雲端執行**：`wealth_sync.yml` workflow，認證走 Service Account（Secret `GOOGLE_SA_JSON`，永不過期），跑完 LINE 通知。
    - **每日自動排程**（2026-07-25 起）：每天台北 **14:17**（cron `17 6 * * *`；2026-10-06 由 14:30 改為 :17，避開整點／半點的擁塞時段）觸發，跑「更新股價＋同步＋全分頁」；
      有「交易日守衛」步驟——**純粹依 TWSE 當日是否有收盤資料判斷**，無資料（週末/國定假日/颱風假）就跳過。
      ⚠ 先前此處與 workflow 註解寫「週六補班盤（如 2026/07/11）確有資料仍會執行」**是錯的**：2026-10-03 健檢實測
      TWSE 在 07/10、07/11 皆無交易，`Jimmy_260711` 快照與 `16_資產歷史` 的 07/11 列都是假資料（已刪除）。
    - **手動觸發**：手機瀏覽器開 `github.com/yaojing277/stock-notify/actions/workflows/wealth_sync.yml` →
      Run workflow 選模式（手機 GitHub App 無 Run 按鈕）；或 API `workflow_dispatch`。
    - **排程執行紀錄＋專案總覽自動發佈**（2026-09-03 起）：每次跑完（含失敗，`always()`）由
      `publish_projects.py` 寫一筆到 `yaojing277/projects` 的 `runlog.json`，並把 `projects.html`
      與兩份 devlog 同步上線 —— **只同步不改寫 devlog 內容**（人工撰寫的開發紀錄，機器不介入）。
      線上頁：https://yaojing277.github.io/projects/schedule_runlog.html 。
      需 secret `PAGES_PAT`（可寫 `projects` 與 `leverage-etf` 的 PAT）；缺了整步安靜跳過不算失敗。
      本機改完 `projects.html`／devlog 想立刻上線，仍可直接跑 `deploy_projects.sh`。
    - 本機改完 `update_stock_price.py`/`update_wealth_os.py`/`twse_hist.py`/`etf_ex_dividend_calendar.py` 記得同步到 `stock_notify_tmp/jimmy_scripts/` 並 push。
    - **排程遲到是 GitHub 端的問題，與工作量無關（2026-10-06 查明）**：近 11 次「建立執行」一律遲到 **5h08m～8h35m**，但**排隊 0m、實際執行只 0.4～4.5m**；非交易日只跑 0.7m 照樣遲到 7h47m，兩者毫無相關性。GitHub cron 是 best effort、不保證準時，公開 repo 優先權最低。
      **遲到不影響資料正確性**——「交易日守衛」算出最近有收盤資料的交易日再以 `--date` 傳給每一步，跨午夜也不會記錯（2026-08-29 踩過的坑已修）；唯一影響是 LINE 通知晚幾小時到。
      想真正準時只能改由外部觸發 `workflow_dispatch`（Mac launchd 或 cron-job.org 打 API，實測秒級啟動），代價是多依賴一個常開的觸發源。
    - 注意：GitHub 排程在 repo 連續 60 天無活動會自動停用；PAT 內嵌於 remote（與到期日綁定）。
    - **ETF 除息日／發放日同步日曆**（`etf_ex_dividend_calendar.py sync`，2026-08-24 起隨每日排程跑）：
      抓 TWSE 官方「ETF 收益分配彙整表」（`etfDiv` API，已公告的除息交易日／發放日／金額，
      涵蓋未來已公告但尚未發生的），對「股價試算」持股中 ETF（代號 `00` 開頭）各建兩筆全天事件
      寫入 `yaojing277@gmail.com` 日曆（「代號 除息 金額」＋「代號 發放 金額」，當天 09:00 提醒）；
      查重靠 Calendar API 當天既有事件比對（無狀態、可安全重跑）。用同一組 `GOOGLE_SA_JSON`
      Service Account 直接寫日曆，前提是**該日曆已分享給 SA 的 client_email**（權限「對活動進行變更」）
      且 **GCP 專案已啟用 Google Calendar API**（兩者已設定完成）。本機沒有 `GOOGLE_SA_JSON` 時
      改用 `check`/`mark` 搭配 Claude 對話裡的 Calendar MCP 手動建事件（state 存
      `etf_dividend_calendar_state.json`）。

### 股票／試算表自動化

> **Wealth OS 參數慣例（2026-07-24 起）**：xlsm 的 `11_設定` 是**全檔唯一事實來源**——房屋/房貸/信貸/現金/
> 預留目標/月薪/生活費/月付/年配息/各項比例門檻/三個目標股數。各分頁公式與 `update_wealth_os.py`
> 都改為讀它，**勿再於任何分頁或腳本寫死門檻**。要調整投資規則，只改 `11_設定` 再跑「更新所有分頁」。

目標統一是 Google 試算表「股價試算」（ID `1UiqAHT2GUhKiviSz5NaLNclttlLVP3ujQMxJUn7Jyr8`），寫入走 Google Sheets API（OAuth，憑證 `jimmy_scripts/credentials.json` + `token.json`）。

| 模組/腳本 | 角色 |
|---|---|
| `twse_hist.py` | 共用歷史股價＋除權息模組（TWSE 為主、TPEx 補，無 Yahoo），供多支腳本 import |
| `price_source.py` | 共用即時報價模組（TWSE 官方為主、yfinance 備援） |
| `update_close_price.py` | 「Jimmy_YYMMDD」持股月結分頁維護：`inspect`/`fill`/`fill-row`/`audit`；「更新 K欄」觸發語見 memory |
| `update_stock_price.py` | 「**更新股價**」月度快照滾動：複製最新分頁為當天新分頁後 H~K 左移、K 欄重抓收盤；`update`/`--yes`/`--dry-run`/`--in-place`/`--skip-if-exists`（當天快照已存在時 exit 0 而非中止，**排程專用**；手動執行不帶此參數仍會大聲中止） |
| `update_wealth_os.py` | 「**同步股價**」（＝更新 Wealth OS；注意與「更新股價」不同）：把最新 `Jimmy_YYMMDD` 分頁同步到雲端 `Jimmy_Wealth_OS_Master_V4.4_Google.xlsm` 的「02_持股總表」，並自動在「15_資產歷史」附加當日凍結快照（同日重跑覆寫不重複）、**每次同步依「股價試算／股票買賣紀錄」整張重建「11_投資日誌」**（2026-10-04 起，2026/01/01 後一張委託一列、金額＝實際扣款／實收；人寫的原因依日期＋代號＋買賣對回；偵測到的股數變動在買賣紀錄找不到才補「⚠ 買賣紀錄缺」列）；加 `--full` 連同 Dashboard/03/04/13/14/安全指數/規則檢查等分頁的公式快取與模板文字一起刷新（「**更新所有分頁**」＝ `update --yes --full`）；Drive API 就地覆蓋、檔案 ID 不變；`update`/`--yes`/`--dry-run`/`--full`；另提供 `delete_stock_rows()` 供**全出清**整列刪除（自動位移全檔跨表引用，2026-10-01 抽成正式函式）；需 token 含 Drive 權限，細節見 memory |
| `lev2_balance.py` | 阿良「正二人生資產負債表」計算引擎（純計算）：三桶（原型β1／正二β2／防守β0＝現金＋債券）、本金槓桿（只計信貸）、總曝險、生活費倍數→建議配置代號（703…073）、5 年預期報酬、00631L 跌幅加碼梯；原本 `--full` 時由 `update_wealth_os.py` 整張重建「06_阿良資產負債表」，**2026-09-16 起以 `LEV2BAL_ENABLED = False` 停用**（06 改回手動範本，D9 引用 `03_持股總表` 合計），計算引擎保留可 `--xlsm` 離線試算。參數讀 `12_設定` B22~B32＋D2:G10 對照表，缺格只警告跳過；`--xlsm <本機檔>` 可離線印報表 |
| `stock_notify.py` / `stock_alert.py` / `stock_alert_v2.py` / `stock_notify_gmail.py` / `youtube_notify.py` | LINE/Gmail 通知類，實際跑在 `stock-notify` repo 的 GitHub Actions（見上方同步規則），金鑰走環境變數 |
| `sheets_writer.py` | 「股票買賣紀錄」分頁匯入（交割明細擷圖 → 寫入） |
| `sheet_value_guard.py` | 改公式前後的安全網：`snapshot`/`diff` 比對計算值 |
| `trade_entry_server.py` / `trade_entry_appscript.gs` | 買進紀錄輸入網頁（localhost:8765）與 Apps Script 後端橋接 |
| `etf_ex_dividend_calendar.py` | ETF 除息日／發放日同步 Google 日曆：`sync`（Service Account 全自動，排程用）／`check`+`mark`（本機無 SA 時，搭配 Calendar MCP 手動建） |
| `since_date_pnl.py` | 「**7/3 起加碼損益**」每日結算（2026-10-04 起隨 `wealth_sync.yml` 每個交易日跑）：讀「股票買賣紀錄」依「股票分割」換算後逐代號 FIFO，對起算日（`SINCE`，含）後買進的批次算已實現＋未實現（以結算日收盤價、扣假設賣出的手續費 0.1425%×6 折與證交稅 ETF 0.1%／個股 0.3%）＋期間配息（上市 TWT49U、上櫃櫃買 exDailyQ）；整張重寫「股價試算」分頁 `加碼損益_0703`（右側 M:P 為每日走勢、由新到舊、同日覆寫），並以 `--line-file` 輸出一行摘要併進每日 LINE 通知；`--dry-run`／`--date`。**金額屬個人資料，刻意不發佈到公開 GitHub Pages** |
| `publish_projects.py` | 排程跑完把執行結果寫進 `yaojing277/projects` 的 `runlog.json`（同交易日重跑覆蓋、留 90 天），並同步 `projects.html`/`index.html`/兩份 devlog/`schedule_runlog.html`；需 secret `PAGES_PAT` |
| `check_reminders.py` | 日期到期提醒（讀 `reminders.json`，需環境變數 `LINE_TOKEN`/`LINE_USER_ID`） |
| `auth_sheets.py` / `reauth_sheets.py` | Google Sheets OAuth 授權／重授權（token 約 7 天過期）；`reauth_sheets.py --drive` 可加授 Google Drive 權限 |

常用指令：
```bash
cd jimmy_scripts
python3 update_close_price.py inspect            # 看現況、標出缺值列
python3 update_close_price.py fill-row --rows 25  # 補整列（新股票）
python3 update_close_price.py audit               # 全表健檢
python3 update_stock_price.py update --dry-run    # 「更新股價」先預覽（快照＋滾動）
python3 update_stock_price.py update              # 預覽後確認寫入
python3 update_wealth_os.py update --dry-run      # 「更新 Wealth OS」先預覽（同步雲端 xlsm）
python3 update_wealth_os.py update                # 預覽後確認就地更新雲端 V4.4
python3 sheets_writer.py inspect                  # 看「股票買賣紀錄」結構
python3 sheets_writer.py write                    # 依 ROWS 寫入（先預覽再輸入 yes）
python3 trade_entry_server.py                     # 開 http://localhost:8765
python3 check_reminders.py                        # 需先 export LINE_TOKEN / LINE_USER_ID
```

Python 依賴（無 requirements.txt，需要時手動裝）：
```bash
pip3 install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib requests yfinance beautifulsoup4
```

### 靜態網頁小工具（產出後部署 GitHub Pages）

> **GitHub PAT（2026-08-30 起）**：`deploy_*.sh`（539／lev2／translate／yt_summaries）不再寫死 token，
> 改由 `jimmy_scripts/_load_pat.sh` 解析——優先讀環境變數 `GH_PAT`，其次 `~/.config/gh_pat`（單行、`chmod 600`）。
> 舊的內嵌 PAT（`ghp_AAn4…`）因明文進公開 repo `jimmy-agent` 的 `deploy_539.sh` 而被 GitHub secret scanning 自動撤銷；
> 三個本機 repo（`jimmy-agent`／`stock-notify`／`mouse-side-key`）的 `.git/config` remote 仍內嵌同組舊 PAT，
> **換新 PAT 時要一併 `git remote set-url` 更新**。新 token 絕不寫進任何被追蹤的檔案。

| 專案 | 產出流程 |
|---|---|
| 今彩539分析 | `lottery_539_scraper.py` → `analyze_539.py` → `generate_539_html.py` → `deploy_539.sh`；部署於 `yaojing277.github.io/lotto539` |
| 中越翻譯 | `translate_zh_vi.html`（單一自足 HTML：Google gtx 端點＋MyMemory 備援、自動偵測方向、TTS 朗讀）→ `deploy_translate.sh`；部署於 `yaojing277.github.io/zh-vi-translate`（repo `yaojing277/zh-vi-translate`） |
| 象棋麻將 | `chess_mahjong.html`（單機）／`chess_mahjong_firebase.html`（多人，Firebase）／`chess_mahjong_ai.html`；`chess_mahjong_server/`（Node + Express + Socket.io，`npm start`）為另一組多人連線嘗試 |
| 族群漲跌幅圖表 | `stock_sector_chart.html` 模板，抓資料後用 Playwright 截圖成 PNG |
| ETF 報酬比較圖卡 | `00631L_vs_0050_returns.html` / `_light.html` + PNG + `ETF_annual_returns.csv` |
| 每日跌幅分布查詢 | **手機版（主）**：https://yaojing277.github.io/projects/drop/ ——`drop_stats_publish.py daily` 隨 `wealth_sync.yml` 每個交易日抓證交所全市場收盤行情（MI_INDEX，一次＝全市場一天）重建當月月檔 `drop/data/YYYY-MM.json`＋`meta.json`，`drop_stats_web.html` 在瀏覽器端計算（電腦不必開）；漲跌幅用官方參考價（一般日＝漲跌價差、除息日 X＝TWT49U 除權息參考價），分割自動正確；目前只收上市。回補：`build --from YYYY-MM` 後 `publish`。**本機版**：`drop_stats_card.py <代號>`（CLI 產 HTML/PNG）、`drop_stats_server.py`（localhost:8766） |
| `projects.html` | 專案總覽頁；說「更新專案總覽」時需同步更新這裡＋本檔案（見 memory）。`deploy_projects.sh` 一鍵部署至 `yaojing277.github.io/projects`（repo `yaojing277/projects`，2026-09-03 建立）：另存一份 `index.html` 當首頁，並一併帶上頁內相對連結的檔案（兩份 devlog、ETF 報酬圖卡、族群圖表、`yt_summaries/`）；**改完 `projects.html` 或兩份 devlog 後要跑這支才會反映到線上**（2026-09-03 起每個交易日排程也會自動同步這幾份，本機跑只是想立刻生效時用） |
| YouTube 影片 AI 摘要 | `yt_summary.py <網址>`：字幕（youtube-transcript-api，zh-TW 優先）→ claude CLI `-p`（自動尋找桌面版 App 內建執行檔）→ 深色 HTML 摘要頁＋`yt_summaries/index.html` 摘要庫；無字幕自動 fallback **yt-dlp＋faster-whisper 本地轉錄**（`--whisper-model` 可調）；字幕／轉錄結果快取於 `.yt_transcript_cache/`（摘要失敗重跑免再轉錄一次，該目錄不會被 deploy 推上 GitHub）；**首次使用需先讓 claude CLI /login 一次**。`yt_auto_summary.py`：三頻道（阿良的正二人生／槓桿人生／卡哇KAWA）RSS 新片自動摘要→部署→LINE 推短版（狀態記 `yt_auto_state.json`，首次執行只登記、`--backfill N` 回補；LINE 金鑰讀環境變數或 `line_secrets.json`）。`deploy_yt_summaries.sh` 一鍵部署摘要庫至 `yaojing277.github.io/yt-summaries`（repo 不存在自動建立） |

### 其他獨立小專案

`HelloWorld`（C#/.NET 練習專案；`HelloMaui` 已於 2026-10-06 由 Jimmy 刪除）、`BetterDisplay`（macOS 工具安裝檔）（`AudioTabHighlighter` Safari 擴充已於 2026-10-06 停止開發並移出版控）——彼此獨立、非主線自動化工作，不互相依賴。

`MouseSideKey`（Hammerspoon lua，滑鼠側鍵視窗管理）已升格獨立專案：`jimmy_scripts/MouseSideKey/` 本身是 git repo，
推送至私人 repo `yaojing277/mouse-side-key`（remote 內嵌 PAT，與 stock-notify 同組）；另有 Google Drive zip 快照。
重裝＝clone → 雙擊 `install.command`；細節見 memory（[[reference-mousesidekey-backup]]）。

### 重要慣例

- 憑證/token（`credentials.json`、`token.json`、`token.pickle`、`client_secret_*.json`）為使用中金鑰，勿誤刪或提交版本控制。
- 純數字股票代號（0050／0056／00878…）寫入 Sheets 前要加 `'` 前綴，避免被當數字吃掉前導零。
- **歷史資料表一律「日期由新到舊」（2026-10-01 起）**：`16_資產歷史`／`21_國泰資產歷史` 的列2＝最新一天，
  新資料由腳本插進列2、其餘整批往下推（`_renumber_row_xml`），跟 `02_每日漲跌`／`03_國泰漲跌` 同方向，
  兩邊列號固定差 23 列，每日表公式因此可用單純的相對參照。**程式內部仍一律用「舊到新」**
  （讀進來就 sort，`hist[0]`＝最早），只有「換算成列號」那一步知道儲存順序。
- **「股票買賣紀錄」C~E 均價只算分割後（2026-10-04 起）**：FILTER 以 `MAXIFS(股票分割!B:B, …, "<="&F#)` 取該代號最近分割日為下限；
  分割事件登記在「股價試算」的 `股票分割` 分頁（目前 0050／00631L／00685L），**日後再有分割只加一列，不改公式**。新增買進列時 C~E 要沿用含 MAXIFS 的新公式。
  **D／E 欄＝以「該筆交易日」往前推 180／365 天的平均買進價**（`F#:F<=F#, F#:F>=(F#-180)`；2026-10-04 起，原本以 TODAY() 推算會讓舊列隨時間變 #N/A）。
  賣出列只有 B（`=TODAY()-K`），C~E 留空；少數舊列 Q 為特例公式（如上銀 `=(N-20)`），批次改公式時勿一律套 `=(N-O-P)`。
- 對帳單是「交割日（T+2）」、分頁記的是「成交日」，匯入買賣紀錄時勿混淆（見 `README_股票買賣紀錄匯入.md`）。
- 深入文件都在 `jimmy_scripts/`：`README_sheet_tools.md`（Sheets 工具細節）、`SHEETS_API_SETUP.md`（API 初始設定）、`換電腦環境還原指南.md`（環境重建）。
- **交易事實來源＝「股價試算／股票買賣紀錄」（2026-10-04 起）**：`11_投資日誌` 每次同步由它重建，
  **要改日誌數字請改買賣紀錄**（直接改日誌下次同步會被蓋回），日誌只保留 G 原因／H 欄。
  記帳時除了併入 `Jimmy_YYMMDD` 持股，**也要把該筆寫進買賣紀錄**，否則日誌會出現「⚠ 買賣紀錄缺」。
- **`04_ETF分析` 列10~14 是 Jimmy 手動維護的公式，腳本不得覆寫**（2026-10-02 定案）：
  B10/C10/D10＝`金額`／`曝險比例`／`成本比例`；B12 存**未加倍**的正二市值、B14 才乘 2；
  **C 欄（曝險，分母 B14）與 D 欄（成本，分母 SUM(B11:B13)）是兩種口徑、各自加總 100%，
  不要「修正」成一致**。`full_refresh()` 只寫列 4~9 與 D5/E4/E6，已實測不碰列 10~14。
  完整公式備份見 memory（[[project-etf-exposure-formulas]]）。
- **排程遲到＋手動補跑的連坐失敗（2026-10-02 解決）**：GitHub 排程常遲到數小時，
  若期間先手動補跑，排程才跑時會因「當天快照已存在」`exit 1`，**GitHub Actions 連坐跳過後面五步**
  ——其中國泰漲跌／ETF除息日曆／跌幅查詢頁／正二分析各自抓自己的 TWSE 資料、與快照無關，
  被跳過等於那天資料整天空缺。workflow 的更新股價步驟已帶 `--skip-if-exists`。
- **版本紀錄要順手補（2026-09-29 起）**：做完有份量的改動（新腳本／新功能／行為變更／重要修復）後，
  **主動**在 `projects.html` 「版本紀錄」最上方補一筆並更新頁首日期，不必等「更新專案總覽」指令。
  每日排程的 `publish_projects.py` 只同步檔案、不會代寫版本紀錄（2026-09-02～09-26 曾因此空窗三週）。
  純使用行為（跑一次摘要、查一次股價）不算異動。細節見 memory（[[feedback-devlog-version-entry]]）。
- **開發紀錄（HTML，倒序排列，新紀錄加在最上面）**：`stock_automation_devlog.html`（股票自動化家族總表，
  涵蓋 2026-04-12 起全系列六層架構、18 個項目，含架構總覽表與三條踩坑鐵律）、`wealth_os_devlog.html`
  （Wealth OS 專屬，2026-07-11 起 14 個項目）。格式沿用 `~/Downloads/docs/devlog.html` 模板
  （需求／實作／踩坑與解法／驗證結果 四段式）。兩份都已掛在 `projects.html` 的
  「Wealth OS 資產管理自動化」卡片上；日後有重要開發或踩坑，記得回頭補一筆。

## 進行中專案與背景

- [2026-10-03] **Wealth OS 全分頁健檢**（✅ 完成；⚠ 尚有 1 項待辦）
  - 23 張分頁＋63 張快照逐一檢查：錯誤字串 0、03 持股總表與 Dashboard／05／13 全對齊、04 手動公式完好。
    問題集中在**歷史資料正確性**，靠「快照 K 欄逐檔比對 TWSE 官方收盤」才抓得到。
  - 已修 `16_資產歷史` 5 列：07/09 快照建於盤中（真收盤藏在週六 07/11 快照裡）→ 改用官方收盤；
    07/11（TWSE 無交易）、07/25、07/26（週末重複）→ 刪除；08/20 00934 用了前日價 → 重算。
    `02_每日漲跌` 隨之重建，07/09 由 −208,600 改為 −220,742（原本消失在週六的 −12,142 歸位）。
  - 已修 `11_投資日誌`：刪 2 筆範本殘留（00662 @70 不可能成交）、07/14 手動 4 筆補手續費。
    ⚠ 「金額＝股數×價格」**不能**判斷是否含費——自動補登的價格本身是含費均價，照公式補會重複計算。
  - **待辦：補 `16_資產歷史` 缺漏的 07/03、07/07、07/08**——當時沒建快照，且 07/03~07/06 有賣出、
    07/07~07/09 除禾伸堂外另有約 30 萬買進日期不明，**等 Jimmy 提供交易／扣款紀錄再補，不要硬猜持股**。
  - 2026-07-25 改 14:30 排程後 59 張快照全是官方收盤，盤中問題是手動年代遺留。細節見 memory（[[feedback-update-wealth-os]]）

- [2026-10-03] **禾伸堂（3026）買進紀錄還原｜9,444 元差額已結案**（✅ 完成；該股 2026-10-01 全出清）
  - Jimmy 提供扣款紀錄後真相大白：**沒有短記，買賣紀錄原本就是對的**。完整批次為
    6/02 50股@620（31,026）、6/10 50股@718（35,930）、**7/07 50股@885（44,287）＋100股@875（87,574）**、
    7/13 50股@913（45,689），合計 300 股／**244,506**，與試算表總成本分毫不差。
  - 三個扣款金額皆可還原為「成交價×股數＋手續費（0.1425%×6折）」；扣款日為**交割日 T+2**
    （7/09 扣款＝7/07 成交、7/15 扣款＝7/13 成交）。以此批次跑 FIFO 也重現元大的 155,030／89,476／894.76。
  - **真正有誤的是快照**：`Jimmy_260612`~`260706` 的均價欄 **764.00 應為 669.56**（差 94.44×100 股＝9,444），
    是當時的輸入錯誤，且在 7/09 加碼時已被正確累計值蓋掉（795.27 是用買賣紀錄的 66,956 算出的）。
  - ⚠ **判斷教訓**：先前誤判為「買賣紀錄短記」，是因為把「快照與元大在**總額** 244,506 上一致」
    當成「快照的**中間值** 76,400 也可信」，於是去懷疑唯一正確的那份紀錄。
    **有矛盾時，佐證必須對應到同一個層級**——總額一致不代表每個中間值都對。
  - 已更正買賣紀錄列12/13（原為我反推的 875×50／785.63×100，實際是 885×50／875×100），
    並撤銷列18/29 的「短記」備註；Z 欄留下更正說明。

- [2026-10-02] **Wealth OS 配置監控強化**（已完成）
  - **正二曝險上限**：`12_設定` **B21 ＝ 70%**，口徑為 **正二×2 ÷ 總曝險**（原型＋正二×2＋現金），
    與 `04_ETF分析` C 欄一致。⚠ 別跟 B13「正二目標上限」60% 搞混——**那條管的是「占股票市值」**
    （目前 28.3%），才是 Dashboard 講的「正二比例」、才會扣安全指數 15 分。
    新門檻已進每日 AI 摘要第 4 點（與既有正二那行併排顯示），**依 Jimmy 指示暫不扣安全指數**。
  - **新增 `OPTIONAL_SETTINGS_MAP`**：後加的選用門檻缺值時退回預設並印警告，**不像必填項那樣
    `sys.exit`**——避免一格空白就讓整個每日排程掛掉。日後加新門檻一律放這組。
  - **金額千分位**：`02_每日漲跌`／`03_國泰漲跌` 的報酬日曆與每日表格共 6 張表套 `CAL_AMT_FMT="#,##0"`。
    **這兩張分頁每次同步都整張重建，手動改格式隔天就會被蓋掉——一律改產生器。**
  - **Jimmy 的配置方向：逐步賣個股、轉買正二 ETF**，要求每次記帳後一併回報曝險比例與成本比例
    （目前曝險 50:39:11、成本 62:25:14）。⚠ 回報時須自己從 B4:B9 重算，不可讀列 11~14 的快取。
  - 細節見 memory（[[project-etf-exposure-formulas]]、[[feedback-trade-entry]]）

- [2026-09-26] **每日跌幅分布查詢（手機版上線）**（v2.0 完成）
  - 仿網路「00878 當日跌到多少% 你會選擇進場？」圖卡：跌幅分桶天數／占比、最大單日漲跌幅、期間含息報酬對照 0050
  - 手機查詢頁 https://yaojing277.github.io/projects/drop/ ，可輸入代號或名稱；資料 2025-12 起，每個交易日 14:30 排程更新
  - `raw=1`（不調整除息）與原圖逐格一致；預設用官方參考價（原圖把 00878 08/18 除息缺口誤算為 −4.06% 暴跌）
  - 月檔約 350 KB（gzip 後約 125 KB），當月檔每日覆寫；`stock-notify` 的 `projects.html` 需與本機同步，否則排程會把線上總覽蓋回舊版
  - 下一步：加入上櫃（櫃買中心全市場行情）

- [2026-08-24] **ETF 除息日／發放日自動同步 Google 日曆**（v1.0 完成）
  - 「股價試算」持股中 11 檔 ETF（0050/0052/0056/00631L/00662/00663L/00685L/00878/00919/00934/00981A）
    的已公告除息交易日＋發放日，自動建全天事件到 `yaojing277@gmail.com` 日曆（當天 09:00 提醒）
  - 資料源改用 TWSE 官方「ETF 收益分配彙整表」`etfDiv` API（非 `twse_hist.dividends`/`TWT49U`），
    好處是投信一公告就查得到，**含未來已公告但尚未發生**的除息日，不必等真的除息才出現
  - 全自動路徑：`etf_ex_dividend_calendar.py sync`，用 `GOOGLE_SA_JSON` 這組 Service Account
    （`wealth-os-sync@stock-sheet-498817.iam.gserviceaccount.com`）直接寫日曆，已排進
    `wealth_sync.yml` 每日排程；前提（已設定完成）：日曆已分享給該 SA（權限「對活動進行變更」）、
    GCP 專案 `stock-sheet-498817` 已啟用 Google Calendar API
  - 查重靠 Calendar API 當天既有事件比對（比對 summary 前綴「代號 除息」/「代號 發放」），無狀態、
    GitHub Actions 每次全新環境也能安全重跑不會重複建立
  - 今年以來（2026）已補齊 21 筆除息 + 21 筆發放，共 42 筆事件
  - 本機沒有 `GOOGLE_SA_JSON` 時的輔助路徑：`check`（印出還沒同步的紀錄 JSON）+ `mark`（標記已同步），
    搭配 Claude 對話裡已連線的 Calendar MCP 手動建事件；state 存 `etf_dividend_calendar_state.json`
  - 說「除息日同步」或「檢查除息日」即可接手，細節記錄於 memory（[[project-etf-dividend-calendar]]）

- [2026-08-19] **正二 ETF 每日漲跌分析**（v1.0 完成）
  - 兩檔台股 2 倍槓桿 ETF（**00631L** 元大台灣50正2 vs **00685L** 群益臺灣加權正2）的日漲跌統計分析
  - [2026-10-03] 比較對象由 00663L 改為 00685L；標的只定義在 `lev2_analysis.py` 的 `CODE_*`／`NAME_*`／`SHORT_*`，
    `update_wealth_os.py` 的 `00_正二分析` 標題／標籤／表頭都從那裡組字，**日後換標的只改這一處**
    （`update_wealth_os.py` 裡 `04_ETF分析` 的 `N['00663L']` 是持股曝險分類，與本頁無關，勿一起改）
  - 核心引擎 `lev2_analysis.py`：TWSE 官方日收盤 → 逐日漲跌 % → 相關係數、追蹤差異、同向比例、Beta 等統計
  - **產出**（均於 `jimmy_scripts/`）：
    - CLI 工具 `lev2_analysis.py --days 60` 直接終端列印 / `--html` 產出單一自足 HTML
    - HTML 圖卡：累積報酬走勢 + 每日差異柱狀 + 統計摘要 + 60 日明細表（Chart.js 深色主題）
    - 已部署 GitHub Pages：https://yaojing277.github.io/leverage-etf/
  - **xlsm 同步**：`update_wealth_os.py --full` 自動更新 `00_正二分析` 分頁（60 筆日資料 + 統計區塊）
  - 最新 60 日分析結果（2026-10-02）：相關 0.9967、累積差 −0.85 pp（685L 領先）、同向 96.7%、追蹤差 0.27 pp
  - 說「續做正二分析」或「更新正二分析」即可接手
- [2026-07-12] **YouTube 影片 AI 摘要工具**（v1.0 完成，已實測可用）
  - `jimmy_scripts/yt_summary.py`：貼網址 → 抓字幕 → claude CLI 摘要 → HTML 摘要頁＋摘要庫 index；已加入 projects.html 主要專案卡片
  - 已驗證：網址解析（watch/youtu.be/shorts/live）、字幕抓取與語言優先序、時間戳章節跳轉連結、無字幕優雅跳過（卡哇KAWA 頻道全片關閉字幕，無法摘要該頻道）
  - claude CLI 已完成首次 `/login`，真實摘要實測成功（TED 拖延症演講、阿格力 EP134）
  - [2026-07-19] 摘要庫已上 GitHub Pages：`deploy_yt_summaries.sh` 一鍵部署（rsync 同步、自動建 repo/啟用 Pages），https://yaojing277.github.io/yt-summaries/
  - [2026-07-19] 新片自動摘要 v2.0 完成：`yt_auto_summary.py` 手動執行（不排程），首輪 `--backfill 1` 實測 3 部全成功（含 2 部無字幕走 Whisper：阿良 EP147、卡哇KAWA）；oEmbed 401 時以 RSS 標題/頻道備援（`fallback_meta`）
  - [2026-09-09] 加入字幕／轉錄快取 `.yt_transcript_cache/`：Whisper 結果存檔，摘要步驟失敗重跑免再轉錄一次；claude CLI 的 OAuth **會過期**（錯誤訊息 `OAuth session expired`），失效時需本人 `/login`，期間可由 Claude 讀快取逐字稿自行撰寫摘要再產頁（見 [[feedback-yt-summary-trigger]]）
  - [2026-09-29] 摘要庫累積 8 部影片；線上 https://yaojing277.github.io/yt-summaries/
  - **待辦**：本機建 `jimmy_scripts/line_secrets.json`（`{"LINE_TOKEN":"...","LINE_USER_ID":"..."}`，與 GitHub Secrets 同組值）啟用 LINE 摘要推播
- [2026-07-08] **MouseSideKey 保存與升格**（已完成）
  - 本機完成安裝（Hammerspoon 1.1.1、登入項目已設，輔助使用權限需手動授予）
  - 建立私人 repo `yaojing277/mouse-side-key` 並推送；Google Drive 另存 `MouseSideKey_v2.zip` 快照
  - `projects.html` 升格為主要專案卡片，移除工具區單檔舊版（`hammerspoon_side_button.lua`）條目
- [2026-07-04] **更新股價「快照滾動」＋現金股利發放日**（已完成）
  - `update_stock_price.py`（觸發語「**更新股價**」）改為**快照模式**：先把最新 `Jimmy_YYMMDD` 分頁複製成當天日期新分頁（**插於舊分頁左側**）再滾動 H~K，舊分頁保留為凍結月度快照；同天重跑（新分頁已存在）自動中止防呆，`--in-place` 保留就地滾動舊行為；`--dry-run` 只模擬不建副本。新增 `get_sheet_props()`／`duplicate_sheet_as()`，並補 ALIAS 聯電→2303
  - 「現金股利_公式版」C 欄發放日（觸發語「**更新股利**」）：Playwright 抓 wantgoo＋除息日/配息交叉驗證，補入緯創 7/31、聯電 7/30；台積電 2330、台新新光金 2887 尚未公告發放日維持留空
  - 細節已記錄於 memory（[[feedback_update_stock_price]]、[[feedback_update_dividend]]、[[project_dividend_payout_date_scrape]]）
- [2026-06-29] **持股月結 K 欄一鍵更新**（已完成）
  - 「股價試算」每月 `Jimmy_YYMMDD` 快照分頁的 K 欄（最右日期欄＝「現今價」）更新自動化；說「**更新 K欄**」即可，不必指定分頁
  - `update_close_price.py` 預設 `--sheet latest`，自動挑 `Jimmy_YYMMDD` 結尾日期最新的分頁（`resolve_latest_sheet()`）
  - `twse_hist.py` 修正：TWSE `/rwd/` STOCK_DAY 間歇回假錯誤時（如 2308 整月空）自動 fallback `/exchangeReport/`
  - 新增 `reauth_sheets.py`：OAuth token 約 7 天過期，失效時跑這支依畫面網址重新授權
  - 細節已記錄於 memory（[[feedback_update_k_column]]、[[project_twse_hist_endpoint_bug]]、[[project_jimmy260612_sheet]]）
- [2026-06-28] **ETF 報酬比較圖卡**（進行中）
  - 台股 5 檔 ETF（00631L 槓桿2倍／0050／0056／00878／00662）歷年「年度報酬率」（曆年、含息）比較，疊加大盤「發行量加權股價報酬指數」虛線基準
  - 資料源：Yahoo 還原股價（含息），00631L 2015 跨年斷點以 TWSE 官方 STOCK_DAY 校正、大盤用 TWSE MI_INDEX 報酬指數；已與 MoneyDJ 逐年交叉驗證
  - 產出（皆於 `jimmy_scripts/`）：深/淺色 HTML（`00631L_vs_0050_returns.html` / `_light.html`）、PNG 圖卡、`ETF_annual_returns.csv`
  - 下一步：包成一鍵 Python script（抓資料＋更新＋截圖）、可自訂 ETF 清單與年度區間
  - 說「繼續 ETF 報酬比較」即可接手，細節已記錄於 memory（[[project_etf_return_compare]]）
- [2026-06-26] **股價系統安全強化與 TWSE 取價遷移**（已完成）
  - 安全：股價通知/跌幅警示/日期提醒的 LINE 金鑰改讀環境變數（GitHub Secrets），移除程式碼明文 token，缺值即 `SystemExit`；已 push `yaojing277/stock-notify` 並線上實測推播成功
  - 取價：持股試算工具（`update_close_price.py`、`trade_entry_server.py`/`.gs`、`fetch_612_close.py`）與新共用模組 `twse_hist.py`，歷史收盤價與配息改用 TWSE/TPEx 官方，移除 Yahoo（美股不再支援；上櫃配息 TWT49U 未涵蓋需人工）
  - 待辦（選用）：換發 LINE channel token 與 repo 內嵌 PAT（舊明文仍在 git 歷史）
  - 細節已記錄於 memory（[[project_stock_notify]]、[[project_jimmy260612_sheet]]）
- [2026-05-30] **今彩539開獎分析 v1.0**（進行中）
  - 爬取 pilio.idv.tw 5875期歷史資料，提供最近10期開獎 + 熱號/冷號/綜合分三種熱度分析
  - 部署於 GitHub Pages：https://yaojing277.github.io/lotto539/
  - 主程式：`lottery_539_scraper.py`、`analyze_539.py`、`generate_539_html.py`、`deploy_539.sh`
  - 說「繼續539分析專案」即可直接進入開發，細節已記錄於 memory
- [2026-05-30] **台股族群漲跌幅圖表產生器**（進行中）
  - 仿 Stockfeel 風格圖卡，自動抓 Yahoo 股市資料 → 產出 HTML → Playwright 截圖 PNG
  - HTML 模板：`/Users/jimmy/Downloads/jimmy-agent/jimmy_scripts/stock_sector_chart.html`
  - 下一階段：整合成一鍵 Python script（抓資料＋更新圖表＋截圖）
  - 說「繼續股票圖表產生器」即可直接進入開發，細節已記錄於 memory
- [2026-04-25] **象棋麻將遊戲**（進行中）
  - 自創桌遊，結合象棋棋子與麻將玩法
  - 單機熱座版已完成，部署於 GitHub Pages：https://yaojing277.github.io/chess-mahjong/
  - 下一階段：Node.js + Socket.io 多人連線版
  - 單機版檔案：`/Users/jimmy/Downloads/jimmy-agent/jimmy_scripts/chess_mahjong.html`
  - 說「繼續象棋麻將專案」即可直接進入開發，規則已記錄於 memory
- [2026-04-10] 整理 AI 導入流程與團隊開發規範
- [2025-07-17] WinForms 應用程式（ReceiptLocator）與製程相關系統開發
- [2025-06-17] C# MVC 架構學習
- [2025-03-24] Telerik RadGrid 與 Excel 匯出功能
- [2024-08 至今] 企業報支系統與簽核流程長期維護
