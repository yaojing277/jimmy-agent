# macOS 開機自動重啟藍牙

## 用途

解決 macOS 開機後藍牙裝置無法自動連線的問題，透過 LaunchAgent 在登入時自動關閉再重新開啟藍牙。

---

## 環境需求

- macOS
- Homebrew（若未安裝：https://brew.sh）

---

## 安裝步驟

### 步驟 1：安裝 blueutil

```bash
brew install blueutil
```

### 步驟 2：建立腳本目錄與腳本

```bash
mkdir -p ~/Scripts
```

建立腳本檔案 `~/Scripts/restart-bluetooth.sh`，內容如下：

```bash
#!/bin/bash
sleep 10  # 等開機穩定後再執行
blueutil --power 0
sleep 2
blueutil --power 1
```

設定執行權限：

```bash
chmod +x ~/Scripts/restart-bluetooth.sh
```

> 腳本路徑可自訂，但後續 plist 內的路徑需對應修改。

### 步驟 3：建立 LaunchAgent

```bash
cat > ~/Library/LaunchAgents/com.jimmy.restart-bluetooth.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.jimmy.restart-bluetooth</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/jimmy/Scripts/restart-bluetooth.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
EOF
```

> 若 `~/Library/LaunchAgents/` 擁有者為 root，需先執行：
> ```bash
> sudo chown $USER ~/Library/LaunchAgents
> ```

### 步驟 4：載入 LaunchAgent

```bash
launchctl load ~/Library/LaunchAgents/com.jimmy.restart-bluetooth.plist
```

完成後下次登入即自動執行。

---

## 常用管理指令

| 指令 | 說明 |
|------|------|
| `launchctl load ~/Library/LaunchAgents/com.jimmy.restart-bluetooth.plist` | 啟用 |
| `launchctl unload ~/Library/LaunchAgents/com.jimmy.restart-bluetooth.plist` | 停用 |
| `blueutil --power` | 查看藍牙目前狀態（0=關, 1=開）|
| `blueutil --power 0` | 手動關閉藍牙 |
| `blueutil --power 1` | 手動開啟藍牙 |

---

## 調整建議

- `sleep 10`：若開機較慢可調高（例如 `sleep 20`）
- 若搬到新電腦，注意腳本路徑中的使用者名稱（`/Users/jimmy/`）需對應修改
