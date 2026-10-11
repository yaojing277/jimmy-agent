#!/bin/bash
# backup_private.sh — 把「只存在這台電腦、又不進版控」的設定備份到私人 repo
#
# 觸發語：「備份私密設定」
#
# 【為什麼需要】
# 有些檔案含個人財務數字，不能進公開的 jimmy-agent，但換電腦時又一定要有。
# 其中 cathay_report_notes.json 是**人工撰寫、無法自動重建**的——只要這台電腦掛了就沒了。
# 備份目的地用 yaojing277/dotfiles（Private Repo，換電腦指南已指定它為私密設定存放處）。
#
# 【⚠ 刻意不備份「憑證」】
# gh_pat / credentials.json / token.json 都**不在清單裡**。
# 那是金鑰，放進 git repo（即使是 private）是另一個層級的風險決定，
# 而且三者都能重新產生（PAT 重發、credentials 從 GCP 下載、token 跑 reauth_sheets.py）。
# 真正救不回的只有人工撰寫的內容，那才是這支要保護的東西。
#
# 用法：
#   ./backup_private.sh            # 預覽要備份什麼（不推送）
#   ./backup_private.sh --push     # 實際提交並推送
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="yaojing277/dotfiles"
SUBDIR="jimmy_scripts_private"       # 在 dotfiles 裡的存放資料夾

# 要備份的檔案（相對 jimmy_scripts/）。新增項目直接加在這裡。
FILES=(
  "cathay_report_notes.json"         # 國泰報告人工註記 ← 無法自動重建
  "loan_config.json"                 # 信貸參數（與 GitHub secret LOAN_CONFIG_JSON 同內容）
  "line_secrets.json"                # LINE 金鑰（目前可能尚未建立，不存在就略過）
)

PUSH=0
[ "${1:-}" = "--push" ] && PUSH=1

# shellcheck source=/dev/null
source "$HERE/_load_pat.sh"          # 取得 $PAT；找不到會自己 exit 1

echo "=== 備份私密設定到 $REPO/$SUBDIR ==="
have=()
for f in "${FILES[@]}"; do
  if [ -f "$HERE/$f" ]; then
    printf "  ✓ %-32s %s bytes\n" "$f" "$(stat -f%z "$HERE/$f")"
    have+=("$f")
  else
    printf "  — %-32s（不存在，略過）\n" "$f"
  fi
done
[ ${#have[@]} -eq 0 ] && { echo "沒有可備份的檔案。"; exit 0; }

if [ "$PUSH" -eq 0 ]; then
  echo
  echo "[預覽] 未推送。要實際備份請加 --push"
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
if ! git clone -q --depth 1 "https://${PAT}@github.com/${REPO}.git" "$TMP/repo" 2>/dev/null; then
  echo "✗ clone $REPO 失敗（PAT 權限不足或 repo 不存在）" >&2
  exit 1
fi
mkdir -p "$TMP/repo/$SUBDIR"
for f in "${have[@]}"; do
  cp "$HERE/$f" "$TMP/repo/$SUBDIR/$f"
done
cat > "$TMP/repo/$SUBDIR/README.md" <<EOF
# jimmy_scripts 私密設定備份

由 \`jimmy_scripts/backup_private.sh\` 自動同步，最後更新：$(date '+%Y-%m-%d %H:%M')。

這些檔案**刻意不進公開的 \`yaojing277/jimmy-agent\`**（含個人財務數字）。
換電腦時複製回 \`jimmy_scripts/\` 即可，細節見該 repo 的 \`換電腦環境還原指南.md\`。

⚠ **憑證不在這裡**（\`gh_pat\`／\`credentials.json\`／\`token.json\`）——
那些能重新產生，且不該放進 git。
EOF

cd "$TMP/repo"
git add "$SUBDIR"
if git diff --staged --quiet; then
  echo "— 內容與上次備份相同，無需提交。"
  exit 0
fi
git -c user.name="jimmy" -c user.email="yaojing277@gmail.com" \
    commit -q -m "備份 jimmy_scripts 私密設定（$(date '+%Y-%m-%d %H:%M')）"
git push -q
echo "✅ 已備份 ${#have[@]} 個檔案到 $REPO/$SUBDIR"
