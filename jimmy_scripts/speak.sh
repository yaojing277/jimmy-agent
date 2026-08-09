#!/bin/bash
# speak.sh — 將文字透過 macOS say 指令朗讀（台灣中文）
# 用法：
#   echo "你好" | ./speak.sh
#   ./speak.sh "你好世界"
#   pbpaste | ./speak.sh        ← 朗讀剪貼簿內容

VOICE="${SPEAK_VOICE:-Meijia}"
RATE="${SPEAK_RATE:-180}"

if [ $# -gt 0 ]; then
    # 直接傳入文字參數
    say -v "$VOICE" -r "$RATE" "$*"
elif [ ! -t 0 ]; then
    # 從 stdin 讀取（pipe 模式）
    INPUT=$(cat)
    # 移除 Markdown 符號，讓朗讀更自然
    CLEAN=$(echo "$INPUT" | sed \
        -e 's/```[^`]*```//g' \
        -e 's/`[^`]*`//g' \
        -e 's/#{1,6} //g' \
        -e 's/\*\*//g' \
        -e 's/\*//g' \
        -e 's/|/ /g' \
        -e 's/---/ /g')
    say -v "$VOICE" -r "$RATE" "$CLEAN"
else
    echo "用法："
    echo "  ./speak.sh \"要朗讀的文字\""
    echo "  echo \"文字\" | ./speak.sh"
    echo "  pbpaste | ./speak.sh   ← 朗讀剪貼簿"
    echo ""
    echo "環境變數："
    echo "  SPEAK_VOICE=Meijia    （預設，可換 Reed、Flo、Grandma 等）"
    echo "  SPEAK_RATE=180        （語速，預設 180）"
fi
