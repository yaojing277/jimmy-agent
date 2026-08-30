#!/bin/bash
# 由各 deploy_*.sh 以 `source "$SCRIPT_DIR/_load_pat.sh"` 載入。
# 解析 GitHub Personal Access Token，來源優先序：
#   1. 環境變數 GH_PAT
#   2. ~/.config/gh_pat（單行純文字，建議 chmod 600）
# 結果放進 $PAT。找不到即中止（不再把 token 寫死在版控檔案裡）。

PAT="${GH_PAT:-}"

if [ -z "$PAT" ] && [ -f "$HOME/.config/gh_pat" ]; then
  PAT="$(tr -d ' \t\r\n' < "$HOME/.config/gh_pat")"
fi

if [ -z "$PAT" ]; then
  echo "✗ 找不到 GitHub PAT，請擇一設定：" >&2
  echo "    export GH_PAT=ghp_xxxxxxxx" >&2
  echo "    或  mkdir -p ~/.config && printf %s 'ghp_xxxxxxxx' > ~/.config/gh_pat && chmod 600 ~/.config/gh_pat" >&2
  exit 1
fi
