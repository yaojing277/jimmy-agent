# 用 iPad mini 7 單獨作為 Mac mini 螢幕

## 目標
不接任何實體螢幕，僅用 iPad mini 7 透過 Sidecar 顯示 Mac mini 畫面。

## 需要的軟體
- **BetterDisplay v4.2.3**（本資料夾內的 `BetterDisplay-v4.2.3.dmg`）
- 來源：https://github.com/waydabber/BetterDisplay

---

## 前置條件

| 項目 | 需求 |
|------|------|
| Mac | macOS Catalina 以上 |
| iPad | iPad mini 7（iPadOS 13 以上）✅ |
| Apple ID | 兩台登入同一帳號，開啟雙重驗證 |
| 網路 | 同一 Wi-Fi，距離 10 公尺內 |
| 其他 | Bluetooth 與 Handoff 均需開啟 |

---

## 安裝步驟

### 步驟一：安裝 BetterDisplay
1. 開啟本資料夾中的 `BetterDisplay-v4.2.3.dmg`
2. 拖曳 BetterDisplay 到 Applications 資料夾
3. 開啟 BetterDisplay，允許系統權限要求

### 步驟二：建立虛擬顯示器
1. 開啟 BetterDisplay
2. 點選「**Create Virtual Display**」
3. 解析度建議設為 **2388 x 1668**（iPad mini 7 原生解析度）
4. 長寬比選 **4:3**

### 步驟三：調整 BetterDisplay 設定
- 勾選「**Connect this virtual display**」
- 勾選「**Launch at login**」（開機自動啟動）
- 啟用「**CLI access**」
- 啟用「**Notification-based integration**」

### 步驟四：修改 iPad 名稱（移除空格）
1. iPad → 設定 → 一般 → 關於本機 → 名稱
2. 將「iPad mini」改為「**iPadmini**」（移除空格，避免指令錯誤）

### 步驟五：記下 Mac mini 的 IP 位址
1. Mac → 系統設定 → Wi-Fi → 詳細資訊 → TCP/IP
2. 記下「**IP 位址**」，例如：`192.168.1.100`

### 步驟六：開啟 Mac mini 自動登入
1. 系統設定 → 使用者與群組
2. 啟用「**自動登入**」

### 步驟七：在 iPad 建立一鍵連線捷徑

#### 7-1 建立捷徑

1. 開啟 iPad 上內建的「**捷徑**」App
2. 右上角點「**＋**」建立新捷徑
3. 在右方搜尋欄輸入「**開啟 URL**」
4. 捲動找到 Safari 區塊，點選「**開啟 URL**」動作
5. 在「打開」欄位輸入以下網址：

```
http://[Mac的IP]:55777/toggle?sidecarConnected&specifier=[iPad名稱]
```

**實際範例**（請替換成你自己的 IP 與 iPad 名稱）：
```
http://192.168.1.100:55777/toggle?sidecarConnected&specifier=iPadmini
```

> IP 位址請參考步驟五所記錄的數值

#### 7-2 命名捷徑

1. 點左上角「**＜ 捷徑**」旁的空白區域
2. 輸入名稱，例如「**開啟 Sidecar**」
3. 點右上角「**完成**」儲存

#### 7-3 加到 iPad 主畫面（一鍵啟動）

1. 在捷徑清單中**長按**剛建立的捷徑
2. 點「**分享**」
3. 選「**加至主畫面**」
4. 可自訂圖示與名稱，點「**加入**」
5. 主畫面會出現捷徑圖示，點一下即自動連線 Sidecar

---

## 使用方式

1. 開啟 Mac mini 電源（不接任何螢幕）
2. 在 iPad 點一下「**連線 Mac**」捷徑
3. 自動啟動 Sidecar，iPad 即成為 Mac 主螢幕

---

## 常見問題

| 問題 | 解法 |
|------|------|
| 找不到 iPad | 確認兩台在同一 Wi-Fi、Bluetooth 已開啟 |
| 畫面解析度不對 | 在 BetterDisplay 調整虛擬顯示器解析度 |
| 每次都要手動連 | 確認已設定 iPad 捷徑，或設定 Mac 開機自動執行 |
| 斷線 | Wi-Fi 訊號弱時改用 USB-C 線連接 |

---

## USB 有線連接（更穩定）

不需 Wi-Fi，延遲更低：
1. 用 **USB-C to USB-C** 線連接 Mac mini 與 iPad
2. iPad 點「信任此電腦」
3. 執行同樣的捷徑或從控制中心手動選擇

> 注意：USB 連線時 Bluetooth 仍需開啟（用於裝置識別）

---

*整理日期：2026-04-13*
*軟體版本：BetterDisplay v4.2.3*
