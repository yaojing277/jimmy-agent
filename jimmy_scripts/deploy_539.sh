#!/bin/bash
# 今彩539 一鍵更新並部署到 GitHub Pages
# 用法: bash deploy_539.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_load_pat.sh"
DEPLOY_DIR="/tmp/lotto539_deploy"
REMOTE="https://${PAT}@github.com/yaojing277/lotto539.git"

echo "=== 今彩539 更新部署 ==="

# 1. 爬取最新開獎資料
echo "① 抓取最新開獎資料..."
python3 "$SCRIPT_DIR/lottery_539_scraper.py"

# 2. 產生最新 HTML
echo "② 產生網頁..."
python3 "$SCRIPT_DIR/generate_539_html.py"

# 3. 推送到 GitHub Pages
echo "③ 部署到 GitHub Pages..."
cp "$SCRIPT_DIR/lottery_539_viewer.html" "$DEPLOY_DIR/index.html"
cd "$DEPLOY_DIR"
git add index.html
git commit -m "更新開獎資料：$(date '+%Y-%m-%d')" 2>/dev/null || echo "  (無變更，跳過)"
git push "$REMOTE" main 2>&1 | grep -v "token"

echo ""
echo "✓ 完成！網頁已更新："
echo "  https://yaojing277.github.io/lotto539/"
