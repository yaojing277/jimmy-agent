# Google Sheets API 設定步驟(OAuth 2.0)

只需做一次,完成後 `sheets_writer.py` 就能讀寫你的「股價試算」。

## 1. 安裝 Python 套件
```bash
pip3 install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib
```

## 2. 在 Google Cloud 建立專案並啟用 API
1. 進入 https://console.cloud.google.com/ (用 yaojing277@gmail.com 登入)
2. 建立一個新專案(例如 `stock-sheet`)
3. 左上選單 → 「API 和服務」→「程式庫」→ 搜尋 **Google Sheets API** → 啟用

## 3. 設定 OAuth 同意畫面
1. 「API 和服務」→「OAuth 同意畫面」
2. User Type 選 **External(外部)**→ 建立
3. 填應用程式名稱(隨意,如 `stock-sheet`)、使用者支援電子郵件、開發人員聯絡資訊 → 儲存
4. 「測試使用者(Test users)」→ 新增 **yaojing277@gmail.com**
   (不送審、保持「測試中」狀態即可,個人自用足夠)

## 4. 建立 OAuth 用戶端憑證
1. 「API 和服務」→「憑證」→「建立憑證」→「OAuth 用戶端 ID」
2. 應用程式類型選 **桌面應用程式(Desktop app)**
3. 建立後按「下載 JSON」
4. 把下載的檔案改名為 **`credentials.json`**,放到本資料夾:
   `/Users/jimmy/Downloads/jimmy-agent/jimmy_scripts/credentials.json`

## 5. 第一次執行(會開瀏覽器授權)
```bash
cd /Users/jimmy/Downloads/jimmy-agent/jimmy_scripts
python3 sheets_writer.py inspect
```
- 瀏覽器會跳出 Google 登入 → 選 yaojing277@gmail.com
- 出現「Google 尚未驗證這個應用程式」→ 點「進階」→「前往 stock-sheet(不安全)」→ 允許
- 授權成功後會自動產生 `token.json`,之後就不必再登入

## 安全提醒
- `credentials.json` 與 `token.json` 等同你的試算表存取金鑰,**不要 commit 到 GitHub**、不要外流。
- 若已用 git 管理這個資料夾,記得把這兩個檔案加進 `.gitignore`。
