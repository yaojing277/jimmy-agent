import json, re

src = "/Users/jimmy/.claude/projects/-Users-jimmy-Downloads-jimmy-agent/b6aaa515-e3d3-4315-a7e1-2c686b788a92/tool-results/mcp-claude_ai_Google_Drive-read_file_content-1780937297637.txt"
out = "/Users/jimmy/Downloads/jimmy-agent/jimmy_scripts/_sheet_extract.txt"

with open(src, encoding="utf-8") as f:
    content = json.load(f)["fileContent"]

# 表頭起點
start = content.find("幾天前")
seg = content[start: start+75000]   # 到下一個買賣加總(~78000)前

# 以 markdown 表格列切分
rows = re.split(r'\n', seg)
# 真正的資料列像: | 00631L | 51 | 269.49 | ... |
data = []
for r in rows:
    cells = [c.strip() for c in r.split("|")]
    cells = [c for c in cells if c != ""]
    if len(cells) < 5:
        continue
    # 第一格是股票代號/名稱 (含數字英文中文), 且該列含日期格式
    if re.search(r'20\d\d/\d', r):
        data.append(cells)

lines = []
lines.append("可解析交易列數: %d" % len(data))
lines.append("欄位順序: 股票|幾天前|至今均價|半年均價|一年均價|買進日|買進價格|買進股數|買進成本|賣出日|賣出價格|賣出股數|賣出成本|手續費|交易稅|應收付|...")
lines.append("")
lines.append("=== 前 40 筆(原始列) ===")
for d in data[:40]:
    lines.append(" | ".join(d[:13]))

# 統計股票出現次數
from collections import Counter
c = Counter(d[0] for d in data)
lines.append("")
lines.append("=== 各標的出現次數 ===")
for k,v in c.most_common():
    lines.append("%s: %d" % (k, v))

with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
