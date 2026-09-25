#!/bin/bash
# 專案總覽頁 projects.html 一鍵部署到 GitHub Pages
# 用法: bash deploy_projects.sh
# repo 不存在會自動建立（public）；Pages 未啟用會自動啟用
#
# 部署內容：projects.html（複製一份為 index.html）+ 頁內所有相對連結的檔案
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_load_pat.sh"
DEPLOY_DIR="/tmp/projects_deploy"
REPO="yaojing277/projects"
REMOTE="https://${PAT}@github.com/${REPO}.git"

# projects.html 內以相對路徑連到的檔案，改這裡即可增減
FILES=(
  projects.html
  stock_automation_devlog.html
  wealth_os_devlog.html
  schedule_runlog.html
  00631L_vs_0050_returns.html
  00631L_vs_0050_returns_light.html
  stock_sector_chart.html
  drop_stats_00878.html
  drop_stats_00631L.html
)
DIRS=(
  yt_summaries
)

echo "=== 專案總覽頁 部署 ==="

# 0. 來源檔存在性檢查（避免推上去一堆 404）
for f in "${FILES[@]}"; do
  [ -f "$SCRIPT_DIR/$f" ] || { echo "✗ 找不到 $SCRIPT_DIR/$f"; exit 1; }
done
for d in "${DIRS[@]}"; do
  [ -d "$SCRIPT_DIR/$d" ] || { echo "✗ 找不到目錄 $SCRIPT_DIR/$d"; exit 1; }
done

# 1. repo 不存在就自動建立
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: token $PAT" \
  "https://api.github.com/repos/${REPO}")
if [ "$STATUS" != "200" ]; then
  echo "① repo 不存在，自動建立 ${REPO}..."
  curl -s -o /dev/null -X POST "https://api.github.com/user/repos" \
    -H "Authorization: token $PAT" -H "Accept: application/vnd.github+json" \
    -d '{"name":"projects","description":"Jimmy 的專案總覽頁（projects.html）","auto_init":true}'
  sleep 3
else
  echo "① repo 已存在"
fi

# 2. 首次執行：clone
if [ ! -d "$DEPLOY_DIR/.git" ]; then
  echo "② 首次部署，clone repo..."
  rm -rf "$DEPLOY_DIR"
  git clone "$REMOTE" "$DEPLOY_DIR" 2>&1 | grep -v "token" || true
  [ -d "$DEPLOY_DIR/.git" ] || { echo "✗ clone 失敗"; exit 1; }
else
  echo "② 同步遠端..."
  git -C "$DEPLOY_DIR" pull "$REMOTE" main 2>&1 | grep -v "token" || true
fi

# 3. 同步網頁
echo "③ 複製網頁..."
for f in "${FILES[@]}"; do
  cp "$SCRIPT_DIR/$f" "$DEPLOY_DIR/$f"
done
for d in "${DIRS[@]}"; do
  rsync -a --delete "$SCRIPT_DIR/$d/" "$DEPLOY_DIR/$d/"
done
# GitHub Pages 首頁：projects.html 另存一份 index.html
cp "$SCRIPT_DIR/projects.html" "$DEPLOY_DIR/index.html"
# 停用 Jekyll，避免底線開頭的檔名/目錄被吃掉
touch "$DEPLOY_DIR/.nojekyll"

# 4. 推送
echo "④ 推送到 GitHub..."
cd "$DEPLOY_DIR"
git add -A
git commit -m "更新專案總覽：$(date '+%Y-%m-%d %H:%M')" 2>/dev/null || echo "  (無變更，跳過)"
git push "$REMOTE" HEAD:main 2>&1 | grep -v "token" || true

# 5. 確保 GitHub Pages 已啟用（main 分支根目錄）
echo "⑤ 檢查 GitHub Pages 設定..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: token $PAT" \
  "https://api.github.com/repos/${REPO}/pages")
if [ "$STATUS" != "200" ]; then
  curl -s -o /dev/null -X POST "https://api.github.com/repos/${REPO}/pages" \
    -H "Authorization: token $PAT" -H "Accept: application/vnd.github+json" \
    -d '{"source":{"branch":"main","path":"/"}}'
  echo "  已啟用 Pages（首次建置約需 1~2 分鐘）"
fi

echo ""
echo "✓ 完成！網址："
echo "  https://yaojing277.github.io/projects/"
