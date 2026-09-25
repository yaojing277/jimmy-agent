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
    `update_stock_price.py`、`update_wealth_os.py`、`twse_hist.py`、`etf_ex_dividend_calendar.py`），
    但 `check_reminders.py`、`youtube_notify.py`、`reminders.json` 放在 `stock_notify_tmp/` **根目錄**。
  - **雲端執行**：`wealth_sync.yml` workflow，認證走 Service Account（Secret `GOOGLE_SA_JSON`，永不過期），跑完 LINE 通知。
    - **每日自動排程**（2026-07-25 起）：每天台北 14:30（cron `30 6 * * *`）觸發，跑「更新股價＋同步＋全分頁」；
      有「交易日守衛」步驟——當日無 TWSE 收盤資料（週末/國定假日）自動跳過，週六補班盤仍會執行。
    - **手動觸發**：手機瀏覽器開 `github.com/yaojing277/stock-notify/actions/workflows/wealth_sync.yml` →
      Run workflow 選模式（手機 GitHub App 無 Run 按鈕）；或 API `workflow_dispatch`。
    - **排程執行紀錄＋專案總覽自動發佈**（2026-09-03 起）：每次跑完（含失敗，`always()`）由
      `publish_projects.py` 寫一筆到 `yaojing277/projects` 的 `runlog.json`，並把 `projects.html`
      與兩份 devlog 同步上線 —— **只同步不改寫 devlog 內容**（人工撰寫的開發紀錄，機器不介入）。
      線上頁：https://yaojing277.github.io/projects/schedule_runlog.html 。
      需 secret `PAGES_PAT`（可寫 `projects` 與 `leverage-etf` 的 PAT）；缺了整步安靜跳過不算失敗。
      本機改完 `projects.html`／devlog 想立刻上線，仍可直接跑 `deploy_projects.sh`。
    - 本機改完 `update_stock_price.py`/`update_wealth_os.py`/`twse_hist.py`/`etf_ex_dividend_calendar.py` 記得同步到 `stock_notify_tmp/jimmy_scripts/` 並 push。
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
| `update_stock_price.py` | 「**更新股價**」月度快照滾動：複製最新分頁為當天新分頁後 H~K 左移、K 欄重抓收盤；`update`/`--yes`/`--dry-run`/`--in-place` |
| `update_wealth_os.py` | 「**同步股價**」（＝更新 Wealth OS；注意與「更新股價」不同）：把最新 `Jimmy_YYMMDD` 分頁同步到雲端 `Jimmy_Wealth_OS_Master_V4.4_Google.xlsm` 的「02_持股總表」，並自動在「15_資產歷史」附加當日凍結快照（同日重跑覆寫不重複）、偵測持股變動自動補登「10_投資日誌」（均價由成本差回推）；加 `--full` 連同 Dashboard/03/04/13/14/安全指數/規則檢查等分頁的公式快取與模板文字一起刷新（「**更新所有分頁**」＝ `update --yes --full`）；Drive API 就地覆蓋、檔案 ID 不變；`update`/`--yes`/`--dry-run`/`--full`；需 token 含 Drive 權限，細節見 memory |
| `lev2_balance.py` | 阿良「正二人生資產負債表」計算引擎（純計算）：三桶（原型β1／正二β2／防守β0＝現金＋債券）、本金槓桿（只計信貸）、總曝險、生活費倍數→建議配置代號（703…073）、5 年預期報酬、00631L 跌幅加碼梯；原本 `--full` 時由 `update_wealth_os.py` 整張重建「06_阿良資產負債表」，**2026-09-16 起以 `LEV2BAL_ENABLED = False` 停用**（06 改回手動範本，D9 引用 `03_持股總表` 合計），計算引擎保留可 `--xlsm` 離線試算。參數讀 `12_設定` B22~B32＋D2:G10 對照表，缺格只警告跳過；`--xlsm <本機檔>` 可離線印報表 |
| `stock_notify.py` / `stock_alert.py` / `stock_alert_v2.py` / `stock_notify_gmail.py` / `youtube_notify.py` | LINE/Gmail 通知類，實際跑在 `stock-notify` repo 的 GitHub Actions（見上方同步規則），金鑰走環境變數 |
| `sheets_writer.py` | 「股票買賣紀錄」分頁匯入（交割明細擷圖 → 寫入） |
| `sheet_value_guard.py` | 改公式前後的安全網：`snapshot`/`diff` 比對計算值 |
| `trade_entry_server.py` / `trade_entry_appscript.gs` | 買進紀錄輸入網頁（localhost:8765）與 Apps Script 後端橋接 |
| `etf_ex_dividend_calendar.py` | ETF 除息日／發放日同步 Google 日曆：`sync`（Service Account 全自動，排程用）／`check`+`mark`（本機無 SA 時，搭配 Calendar MCP 手動建） |
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
| 每日跌幅分布圖卡 | `drop_stats_card.py <代號>`（CLI 產 `drop_stats_<代號>.html`，`--edges`/`--raw`/`--png`）；`drop_stats_server.py` 即時查詢頁 `localhost:8766`（`--lan` 開放手機）。除息日以前收−股利、分割日以 STOCK_DAY 漲跌價差回推官方參考價。因證交所 CORS 限制只能本機跑；`projects.html` 卡片附 00878／00631L 靜態範例（已列入 `deploy_projects.sh`） |
| `projects.html` | 專案總覽頁；說「更新專案總覽」時需同步更新這裡＋本檔案（見 memory）。`deploy_projects.sh` 一鍵部署至 `yaojing277.github.io/projects`（repo `yaojing277/projects`，2026-09-03 建立）：另存一份 `index.html` 當首頁，並一併帶上頁內相對連結的檔案（兩份 devlog、ETF 報酬圖卡、族群圖表、`yt_summaries/`）；**改完 `projects.html` 或兩份 devlog 後要跑這支才會反映到線上**（2026-09-03 起每個交易日排程也會自動同步這幾份，本機跑只是想立刻生效時用） |
| YouTube 影片 AI 摘要 | `yt_summary.py <網址>`：字幕（youtube-transcript-api，zh-TW 優先）→ claude CLI `-p`（自動尋找桌面版 App 內建執行檔）→ 深色 HTML 摘要頁＋`yt_summaries/index.html` 摘要庫；無字幕自動 fallback **yt-dlp＋faster-whisper 本地轉錄**（`--whisper-model` 可調）；字幕／轉錄結果快取於 `.yt_transcript_cache/`（摘要失敗重跑免再轉錄一次，該目錄不會被 deploy 推上 GitHub）；**首次使用需先讓 claude CLI /login 一次**。`yt_auto_summary.py`：三頻道（阿良的正二人生／槓桿人生／卡哇KAWA）RSS 新片自動摘要→部署→LINE 推短版（狀態記 `yt_auto_state.json`，首次執行只登記、`--backfill N` 回補；LINE 金鑰讀環境變數或 `line_secrets.json`）。`deploy_yt_summaries.sh` 一鍵部署摘要庫至 `yaojing277.github.io/yt-summaries`（repo 不存在自動建立） |

### 其他獨立小專案

`HelloWorld`／`HelloMaui`（C#/.NET 練習專案）、`AudioTabHighlighter`（Safari extension，Xcode 專案）、`BetterDisplay`（macOS 工具安裝檔）——彼此獨立、非主線自動化工作，不互相依賴。

`MouseSideKey`（Hammerspoon lua，滑鼠側鍵視窗管理）已升格獨立專案：`jimmy_scripts/MouseSideKey/` 本身是 git repo，
推送至私人 repo `yaojing277/mouse-side-key`（remote 內嵌 PAT，與 stock-notify 同組）；另有 Google Drive zip 快照。
重裝＝clone → 雙擊 `install.command`；細節見 memory（[[reference-mousesidekey-backup]]）。

### 重要慣例

- 憑證/token（`credentials.json`、`token.json`、`token.pickle`、`client_secret_*.json`）為使用中金鑰，勿誤刪或提交版本控制。
- 純數字股票代號（0050／0056／00878…）寫入 Sheets 前要加 `'` 前綴，避免被當數字吃掉前導零。
- 對帳單是「交割日（T+2）」、分頁記的是「成交日」，匯入買賣紀錄時勿混淆（見 `README_股票買賣紀錄匯入.md`）。
- 深入文件都在 `jimmy_scripts/`：`README_sheet_tools.md`（Sheets 工具細節）、`SHEETS_API_SETUP.md`（API 初始設定）、`換電腦環境還原指南.md`（環境重建）。
- **開發紀錄（HTML，倒序排列，新紀錄加在最上面）**：`stock_automation_devlog.html`（股票自動化家族總表，
  涵蓋 2026-04-12 起全系列六層架構、18 個項目，含架構總覽表與三條踩坑鐵律）、`wealth_os_devlog.html`
  （Wealth OS 專屬，2026-07-11 起 14 個項目）。格式沿用 `~/Downloads/docs/devlog.html` 模板
  （需求／實作／踩坑與解法／驗證結果 四段式）。兩份都已掛在 `projects.html` 的
  「Wealth OS 資產管理自動化」卡片上；日後有重要開發或踩坑，記得回頭補一筆。

## 進行中專案與背景

- [2026-09-26] **每日跌幅分布圖卡＋即時查詢頁**（v1.0 完成）
  - 仿網路「00878 當日跌到多少% 你會選擇進場？」圖卡：跌幅分桶天數／占比、最大單日漲跌幅、期間漲幅對照 0050
  - `--raw` 模式與原圖逐格一致（原圖把 00878 08/18 除息缺口誤算為 −4.06% 暴跌，預設模式已修正）
  - 00631L 2026-03-31 恢復買賣 1 拆 22，已自動偵測還原；正二代號（L 結尾）查詢頁自動用 1,2,4,7 分桶
  - 已加入 `projects.html` 主要專案卡片；下一步可考慮雲端化（需後端代理）或排進每日排程批次產圖

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
  - 兩檔台股 2 倍槓桿 ETF（**00631L** 元大台灣50正2 vs **00663L** 國泰臺灣加權正2）的日漲跌統計分析
  - 核心引擎 `lev2_analysis.py`：TWSE 官方日收盤 → 逐日漲跌 % → 相關係數、追蹤差異、同向比例、Beta 等統計
  - **產出**（均於 `jimmy_scripts/`）：
    - CLI 工具 `lev2_analysis.py --days 60` 直接終端列印 / `--html` 產出單一自足 HTML
    - HTML 圖卡：累積報酬走勢 + 每日差異柱狀 + 統計摘要 + 60 日明細表（Chart.js 深色主題）
    - 已部署 GitHub Pages：https://yaojing277.github.io/leverage-etf/
  - **xlsm 同步**：`update_wealth_os.py --full` 自動更新 `00_正二分析` 分頁（60 筆日資料 + 統計區塊）
  - 最新 60 日分析結果：相關 0.9966、累積差 +0.87 pp（631L 領先）、同向 96.7%、追蹤差 0.34 pp
  - 說「續做正二分析」或「更新正二分析」即可接手
- [2026-07-12] **YouTube 影片 AI 摘要工具**（v1.0 完成，已實測可用）
  - `jimmy_scripts/yt_summary.py`：貼網址 → 抓字幕 → claude CLI 摘要 → HTML 摘要頁＋摘要庫 index；已加入 projects.html 主要專案卡片
  - 已驗證：網址解析（watch/youtu.be/shorts/live）、字幕抓取與語言優先序、時間戳章節跳轉連結、無字幕優雅跳過（卡哇KAWA 頻道全片關閉字幕，無法摘要該頻道）
  - claude CLI 已完成首次 `/login`，真實摘要實測成功（TED 拖延症演講、阿格力 EP134）
  - [2026-07-19] 摘要庫已上 GitHub Pages：`deploy_yt_summaries.sh` 一鍵部署（rsync 同步、自動建 repo/啟用 Pages），https://yaojing277.github.io/yt-summaries/
  - [2026-07-19] 新片自動摘要 v2.0 完成：`yt_auto_summary.py` 手動執行（不排程），首輪 `--backfill 1` 實測 3 部全成功（含 2 部無字幕走 Whisper：阿良 EP147、卡哇KAWA）；oEmbed 401 時以 RSS 標題/頻道備援（`fallback_meta`）
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
