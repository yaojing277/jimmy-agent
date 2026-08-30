#!/bin/bash
# 中越翻譯網頁 一鍵部署到 GitHub Pages
# 用法: bash deploy_translate.sh
# 前置：GitHub 上需已有 yaojing277/zh-vi-translate repo（public、含 main 分支）
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_load_pat.sh"
DEPLOY_DIR="/tmp/zh_vi_translate_deploy"
REPO="yaojing277/zh-vi-translate"
REMOTE="https://${PAT}@github.com/${REPO}.git"

echo "=== 中越翻譯 部署 ==="

# 1. 首次執行：clone
if [ ! -d "$DEPLOY_DIR/.git" ]; then
  echo "① 首次部署，clone repo..."
  git clone "$REMOTE" "$DEPLOY_DIR" 2>&1 | grep -v "token" || true
  [ -d "$DEPLOY_DIR/.git" ] || { echo "✗ clone 失敗，請確認 repo ${REPO} 已建立"; exit 1; }
fi

# 2. 複製網頁
echo "② 複製網頁..."
cp "$SCRIPT_DIR/translate_zh_vi.html" "$DEPLOY_DIR/index.html"

# 3. 推送
echo "③ 推送到 GitHub..."
cd "$DEPLOY_DIR"
git add index.html
git commit -m "更新中越翻譯：$(date '+%Y-%m-%d %H:%M')" 2>/dev/null || echo "  (無變更，跳過)"
git push "$REMOTE" HEAD:main 2>&1 | grep -v "token" || true

# 4. 確保 GitHub Pages 已啟用（main 分支根目錄）
echo "④ 檢查 GitHub Pages 設定..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: token $PAT" \
  "https://api.github.com/repos/${REPO}/pages")
if [ "$STATUS" != "200" ]; then
  curl -s -o /dev/null -X POST "https://api.github.com/repos/${REPO}/pages" \
    -H "Authorization: token $PAT" -H "Accept: application/vnd.github+json" \
    -d '{"source":{"branch":"main","path":"/"}}'
  echo "  已啟用 Pages（首次建置約需 1~2 分鐘）"
fi

echo ""
echo "✓ 完成！網頁網址："
echo "  https://yaojing277.github.io/zh-vi-translate/"
