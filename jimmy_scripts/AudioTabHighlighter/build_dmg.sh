#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$PROJECT_DIR/AudioTabHighlighter.xcodeproj"
SCHEME="AudioTabHighlighter (macOS)"
APP_NAME="AudioTabHighlighter"
BUILD_DIR="$PROJECT_DIR/dist"
DMG_NAME="${APP_NAME}.dmg"

echo "🔨 Building Release..."
xcodebuild \
  -project "$PROJECT" \
  -scheme "$SCHEME" \
  -configuration Release \
  -derivedDataPath "$PROJECT_DIR/build" \
  build

APP_PATH=$(find "$PROJECT_DIR/build/Build/Products/Release" -name "${APP_NAME}.app" | head -1)
if [ -z "$APP_PATH" ]; then
  echo "❌ 找不到 .app 檔案"
  exit 1
fi

echo "📦 打包成 DMG..."
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/dmg_staging"
cp -R "$APP_PATH" "$BUILD_DIR/dmg_staging/"
ln -s /Applications "$BUILD_DIR/dmg_staging/Applications"

hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$BUILD_DIR/dmg_staging" \
  -ov \
  -format UDZO \
  "$BUILD_DIR/$DMG_NAME"

rm -rf "$BUILD_DIR/dmg_staging"

VERSION=$(grep '"version"' "$PROJECT_DIR/../audio-tab-highlighter/manifest.json" | grep -o '"[0-9.]*"' | tr -d '"')

cat > "$BUILD_DIR/安裝說明.md" << README
# Audio Tab Highlighter v${VERSION} — 安裝說明

## 功能說明

在 Safari 側邊欄中，自動標示正在播放聲音的分頁：
- 分頁標題前加上 🔊 符號
- Favicon 替換為藍色喇叭圖示

支援 YouTube、Spotify Web、Netflix 等所有含 audio/video 的網頁。

---

## 系統需求

- macOS 13 Ventura 以上
- Safari 16 以上

---

## 安裝步驟

1. 雙擊 \`AudioTabHighlighter.dmg\`
2. 將 \`AudioTabHighlighter.app\` 拖曳到 \`Applications\` 資料夾
3. 從 Applications 開啟 \`AudioTabHighlighter\`（只需執行一次以完成註冊）
4. Safari → 設定（⌘,）→ 延伸功能 → 勾選 **AudioTabHighlighter**
5. 若提示「允許存取網站」，請選擇「**所有網站**」

---

## 疑難排解

| 問題 | 解法 |
|------|------|
| 延伸功能沒出現 | 確認 App 已在 Applications 並執行過一次 |
| 啟用後沒反應 | Safari → 延伸功能 → 關掉再重新打開 |

README

echo ""
echo "✅ 完成：$BUILD_DIR/$DMG_NAME"
echo ""
echo "安裝步驟："
echo "1. 開啟 $DMG_NAME"
echo "2. 將 ${APP_NAME}.app 拖到 Applications"
echo "3. 執行 App 一次"
echo "4. Safari → 設定 → 延伸功能 → 啟用 AudioTabHighlighter"
