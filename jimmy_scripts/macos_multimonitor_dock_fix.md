# macOS 多顯示器獨立空間 + 固定 Dock 位置

> 適用系統：macOS Ventura 以上  
> 建立日期：2026-04-11

---

## 問題描述

macOS 預設在多顯示器環境下，Dock 會跟著滑鼠跳到任何一個螢幕的底部邊緣，造成干擾。

---

## 步驟一：啟用各顯示器獨立空間

1. 開啟 **系統設定 → 桌面與 Dock**
2. 向下捲動找到「**Mission Control**」區塊
3. 勾選 ✅ **「顯示器具有獨立空間」（Displays have separate Spaces）**
4. **登出再登入**後生效

---

## 步驟二：固定 Dock，防止跟著滑鼠亂跑

### 方法 A：Terminal 指令（推薦）

開啟終端機（Terminal），執行以下指令：

```bash
defaults write com.apple.dock workspaces-auto-swoosh -bool NO
killall Dock
```

- Dock 會短暫消失後重新出現，設定即生效
- 設定永久保留，重開機後不需重設

若要恢復預設行為（Dock 跟隨滑鼠）：

```bash
defaults write com.apple.dock workspaces-auto-swoosh -bool YES
killall Dock
```

---

### 方法 B：調整 Dock 位置（降低誤觸）

1. 開啟 **系統設定 → 桌面與 Dock**
2. 「**螢幕上的位置**」改為「**左側**」或「**右側**」
3. Dock 在側邊時觸發面積小，誤觸機率大幅降低

---

### 方法 C：第三方工具（完全獨立 Dock）

| 工具 | 說明 |
|------|------|
| HiDock | 每個螢幕擁有自己的獨立 Dock |
| Bartender | 搭配管理 Menu Bar 使用效果更佳 |

---

## 建議組合

| 目標 | 建議做法 |
|------|----------|
| 各螢幕有獨立空間 | 步驟一（啟用獨立空間）|
| Dock 不亂跑 | 步驟二 方法 A（Terminal 指令）|
| 完全獨立 Dock | 步驟二 方法 C（HiDock）|
