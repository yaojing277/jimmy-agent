# YouTube 頻道新影片通知設定說明

> 建立日期：2026-04-12
> 作者：Jimmy
> 功能：每天早上 08:30 / 晚上 21:00，自動檢查 YouTube 頻道有無新影片，有則推播 LINE 通知

---

## 架構概覽

```
GitHub Actions 排程（雲端，不需電腦開著）
    ↓ 每天 08:30 / 21:00（台灣時間）
Ubuntu 虛擬機自動啟動
    ↓
執行 youtube_notify.py
    ↓
YouTube RSS Feed 抓取最新影片
    ↓
比對是否為 13 小時內的新影片
    ↓
LINE Messaging API 推播到手機
```

---

## 追蹤頻道

| 頻道名稱 | Channel ID |
|---------|------------|
| 卡哇KAWA | UCe4meHPGhNBTDsmzM0ChiDQ |

---

## 通知格式

```
🎬 卡哇KAWA 有新影片！

《影片標題》

上傳時間：2026/04/12 21:00
▶ https://youtube.com/watch?v=xxxxx
```

---

## 一、GitHub Repository 資訊

| 項目 | 內容 |
|------|------|
| Repo | [yaojing277/stock-notify](https://github.com/yaojing277/stock-notify) |
| 腳本 | `youtube_notify.py` |
| Workflow | `.github/workflows/youtube_notify.yml` |
| 執行時間 | 每天 08:30 / 21:00（台灣時間） |
| Cron 表達式 | `30 0 * * *`（UTC 00:30）/ `0 13 * * *`（UTC 13:00） |

---

## 二、GitHub Secrets

與股價通知共用，無需重新設定：

| Secret 名稱 | 說明 |
|------------|------|
| `LINE_TOKEN` | LINE Channel Access Token |
| `LINE_USER_ID` | LINE User ID（U 開頭） |

---

## 三、手動觸發測試

前往 [Actions 頁面](https://github.com/yaojing277/stock-notify/actions) → 選 `YouTube 頻道新影片通知` → **Run workflow**

> 注意：若頻道在最近 13 小時內沒有發布新影片，不會收到通知，屬正常行為。

---

## 四、新增追蹤頻道

1. 前往目標頻道頁面
2. 用 curl 抓取 Channel ID：
   ```bash
   curl -sL "https://www.youtube.com/@頻道名稱" -A "Mozilla/5.0" | grep -o 'channel_id=[^"&]*' | head -1
   ```
3. 告訴 Claude Code「幫我追加 XXX 頻道」，Claude 會更新 `youtube_notify.py`

---

## 五、注意事項

| 項目 | 說明 |
|------|------|
| 僅限公開影片 | 會員專屬影片無法透過 RSS 偵測，請用 YouTube App 鈴鐺通知 |
| 免費額度 | GitHub Actions 免費方案每月 2000 分鐘，本設定約使用 2 分鐘/月 |
| Token 安全 | Token 存於 GitHub Secrets，程式碼中不含任何敏感資訊 |
