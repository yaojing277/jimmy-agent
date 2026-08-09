# LINE 股價即時通知設定說明

> 建立日期：2026-04-12  
> 作者：Jimmy  
> 功能：每個交易日指定時間點自動推播台灣股價到 LINE
> 最後更新：2026-06-26

---

## 架構概覽

```
GitHub Actions 排程（雲端，不需電腦開著）
    ↓ 週一至週五，每日 7 次（09:00/09:30/10:00/10:30/12:30/13:00/13:30）
Ubuntu 虛擬機自動啟動
    ↓
執行 stock_notify.py
    ↓
TWSE 官方即時 API 抓股價（主來源；Yahoo 僅備援，見 price_source.py）
    ↓
LINE Messaging API 推播到手機
（LINE_TOKEN / LINE_USER_ID 由 GitHub Secrets 經 workflow env 注入，
  程式以 os.environ 讀取，不再有明文金鑰）
```

---

## 追蹤股票清單

| 代號 | 名稱 |
|------|------|
| 0050.TW | 元大台灣50 |
| 6515.TW | 穎崴 |
| 2330.TW | 台積電 |
| 00878.TW | 國泰永續高股息 |
| 00919.TW | 00919 |

---

## 一、LINE Messaging API 設定

### 1. 建立 LINE Official Account

1. 前往 [manager.line.biz](https://manager.line.biz)
2. 用 LINE 帳號登入
3. 點「開始使用」→ 填寫帳號名稱（如 `股價通知`）→ 建立

### 2. 啟用 Messaging API

1. 進入建立好的帳號
2. 右上角「設定」→ 左側「Messaging API」
3. 點「啟用 Messaging API」
4. 選擇或新建 Provider（填開發者名稱）→ 確認

> ⚠️ 2024 年 9 月後，已無法從 Developers Console 直接建立 Channel，必須先從 Official Account Manager 啟用。

### 3. 取得 Channel Access Token

1. 前往 [developers.line.biz/console](https://developers.line.biz/console)
2. 點進你的 Channel → 點上方「**Messaging API**」分頁
3. 捲到最底部，找到「Channel access token (long-lived)」
4. 點「**Issue**」→ 複製產生的 Token

### 4. 取得 User ID

1. 同一個 Channel → 點「**Basic settings**」分頁
2. 找到「**Your user ID**」（`U` 開頭，32碼英數字）

### 5. 加 Bot 為好友（必做）

- 在「Messaging API」分頁掃描 QR Code 加 Bot 好友
- **未加好友則無法收到訊息**

---

## 二、GitHub Actions 排程設定

### Repository 資訊

| 項目 | 內容 |
|------|------|
| Repo | [yaojing277/stock-notify](https://github.com/yaojing277/stock-notify) |
| 腳本 | `stock_notify.py` |
| Workflow | `.github/workflows/stock_notify.yml` |
| 執行時間 | 週一至週五 09:00/09:30/10:00/10:30/12:30/13:00/13:30（共 7 次） |
| Cron 表達式 | `0,30 1-2 * * 1-5` + `30 4 * * 1-5` + `0,30 5 * * 1-5`（UTC） |

### GitHub Secrets 設定

前往 repo → Settings → Secrets and variables → Actions：

| Secret 名稱 | 說明 |
|------------|------|
| `LINE_TOKEN` | LINE Channel Access Token |
| `LINE_USER_ID` | LINE User ID（U 開頭） |

### 手動觸發測試

前往 [Actions 頁面](https://github.com/yaojing277/stock-notify/actions) → 選 `stock_notify.yml` → **Run workflow**

---

## 三、推播訊息格式

```
📈 通知 2026/04/12 09:00

0050.TW
  現價：80.75
  ▲ +1.70（+2.15%）

6515.TW
  現價：7930.00
  ▼ -30.00（-0.38%）

00878.TW
  現價：23.02
  ▲ +0.15（+0.66%）

00919.TW
  現價：22.93
  ▲ +0.06（+0.26%）

2330.TW
  現價：2000.00
  ▲ +60.00（+3.09%）
```

---

## 四、注意事項

| 項目 | 說明 |
|------|------|
| 免費額度 | LINE Messaging API 免費方案 200 則/月，本設定約 154 則/月（7次×22交易日），額度充足 |
| GitHub Actions | 免費方案每月 2000 分鐘，本設定約使用 10 分鐘/月 |
| 電腦不需開著 | 完全在 GitHub 雲端執行 |
| Token 安全 | Channel Access Token 存在 GitHub Secrets；程式以 `os.environ.get` 讀取，**程式碼與 repo 已無明文金鑰**（2026-06-26 起，缺值時 `SystemExit` 不會誤送）。注意：舊明文 token 仍留在 git 歷史，建議擇期換發 |

---

## 五、新增或移除股票

告訴 Claude Code「加入 XXXX」或「移除 XXXX」，Claude 會直接更新 GitHub 上的 `stock_notify.py`。

台股代號格式：`數字.TW`（例如鴻海 `2317.TW`）

---

## 六、傳送自訂訊息到 LINE

直接告訴 Claude Code：

```
傳 LINE：你想說的任何內容
```

Claude 會立即推播到你的 LINE 手機。
