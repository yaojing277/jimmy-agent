#!/bin/bash
# 正二 ETF 每日漲跌分析 一鍵部署到 GitHub Pages
# 用法: bash deploy_lev2.sh            # 用現有 lev2_site/index.html 部署
#       bash deploy_lev2.sh --refresh  # 先重抓資料重產 HTML 再部署
# repo 不存在會自動建立（public）；Pages 未啟用會自動啟用
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_load_pat.sh"
SRC_DIR="$SCRIPT_DIR/lev2_site"
DEPLOY_DIR="/tmp/lev2_deploy"
REPO="yaojing277/leverage-etf"
REMOTE="https://${PAT}@github.com/${REPO}.git"

echo "=== 正二 ETF 分析 部署 ==="

# 0. --refresh：重抓 TWSE 資料重產 HTML
if [ "$1" = "--refresh" ]; then
  echo "⓪ 重抓資料並重產 HTML..."
  python3 "$SCRIPT_DIR/lev2_analysis.py" --html >/dev/null
fi

[ -f "$SRC_DIR/index.html" ] || {
  echo "✗ 找不到 $SRC_DIR/index.html"
  echo "  請先執行: python3 lev2_analysis.py --html"
  exit 1
}

# 1. repo 不存在就自動建立
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: token $PAT" \
  "https://api.github.com/repos/${REPO}")
if [ "$STATUS" != "200" ]; then
  echo "① repo 不存在，自動建立 ${REPO}..."
  curl -s -o /dev/null -X POST "https://api.github.com/user/repos" \
    -H "Authorization: token $PAT" -H "Accept: application/vnd.github+json" \
    -d '{"name":"leverage-etf","description":"00631L vs 00663L 正二 ETF 每日漲跌分析（lev2_analysis.py 產生）","auto_init":true}'
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
rsync -a --delete --exclude='.git' "$SRC_DIR/" "$DEPLOY_DIR/"

# 4. 推送
echo "④ 推送到 GitHub..."
cd "$DEPLOY_DIR"
git add -A
git commit -m "更新正二分析：$(date '+%Y-%m-%d %H:%M')" 2>/dev/null || echo "  (無變更，跳過)"
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
echo "  https://yaojing277.github.io/leverage-etf/"
