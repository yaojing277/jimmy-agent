#!/bin/bash
# 更新股價+同步股價 — 雙擊即可執行

cd "$(dirname "$0")"

echo "=============================="
echo "  更新股價 + 同步股價"
echo "=============================="
echo ""

echo "[1/2] 更新股價（快照滾動）..."
python3 update_stock_price.py update --yes
echo ""

echo "[2/2] 同步股價（同步到 Wealth OS）..."
python3 update_wealth_os.py update --yes
echo ""

echo "=============================="
echo "  完成！按 Enter 關閉視窗"
echo "=============================="
read
