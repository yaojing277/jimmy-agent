import json, re

src = "/Users/jimmy/.claude/projects/-Users-jimmy-Downloads-jimmy-agent/b6aaa515-e3d3-4315-a7e1-2c686b788a92/tool-results/mcp-claude_ai_Google_Drive-read_file_content-1780937297637.txt"
out = "/Users/jimmy/Downloads/jimmy-agent/jimmy_scripts/_sheet_extract2.txt"

with open(src, encoding="utf-8") as f:
    content = json.load(f)["fileContent"]

start = content.find("幾天前")
seg = content[start: start+75000]
rows = seg.split("\n")

records = []   # (markdown行序, cells)
for i, r in enumerate(rows):
    if not re.search(r'20\d\d/\d', r):
        continue
    cells = [c.strip() for c in r.split("|")]
    cells = [c for c in cells if c != ""]
    if len(cells) < 5:
        continue
    records.append(cells)

def is_misaligned(c):
    # 正常列: 第2欄(index1)為純整數天數; 錯位: 第2欄直接是日期
    if len(c) < 2:
        return True
    return bool(re.match(r'^20\d\d/\d', c[1]))

lines = []
lines.append("總交易列: %d" % len(records))
mis = [(idx, c) for idx, c in enumerate(records, 1) if is_misaligned(c)]
norm = [(idx, c) for idx, c in enumerate(records, 1) if not is_misaligned(c)]
lines.append("正常列: %d   錯位列: %d" % (len(norm), len(mis)))
lines.append("")
lines.append("=== 錯位列清單 (序號=本次讀取由新到舊第N筆) ===")
lines.append("序 | 股票 | 日期 | 價 | 股數 | 欄數 | 整列原始")
for idx, c in mis:
    lines.append("%d | %s | %s | %s | %s | (%d欄) | %s" % (
        idx, c[0], c[1] if len(c)>1 else "", c[2] if len(c)>2 else "",
        c[3] if len(c)>3 else "", len(c), " | ".join(c)))

with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
