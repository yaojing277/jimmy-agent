#!/usr/bin/env python3
"""
從 CSV 讀取最新 N 筆 + 分析結果，更新 lottery_539_viewer.html。
用法: python3 generate_539_html.py [筆數，預設10]
流程: lottery_539_scraper.py → analyze_539.py → generate_539_html.py
"""
import csv, json, re, subprocess, sys
from pathlib import Path

N    = int(sys.argv[1]) if len(sys.argv) > 1 else 10
BASE = Path(__file__).parent

# 1. 執行分析腳本（產出 539_analysis.json）
subprocess.run(["python3", str(BASE / "analyze_539.py")], check=True)

# 2. 讀取資料
rows   = list(csv.DictReader(open(BASE / "539_history.csv", encoding="utf-8-sig")))
latest = rows[-N:]
analysis = json.loads((BASE / "539_analysis.json").read_text())

# 3. 建立 DRAWS JS 字串
draw_lines = []
for r in latest:
    nums = f"[{r['n1']},{r['n2']},{r['n3']},{r['n4']},{r['n5']}]"
    draw_lines.append(f'  {{ date:"{r["date"]}", weekday:"{r["weekday"]}", numbers:{nums} }},')
draws_js = "const DRAWS = [\n" + "\n".join(draw_lines) + "\n];"

# 4. 建立 ANALYSIS JS 字串
analysis_js = "const ANALYSIS = " + json.dumps(analysis, ensure_ascii=False) + ";"

# 5. 寫入 HTML
html = (BASE / "lottery_539_viewer.html").read_text(encoding="utf-8")
html = re.sub(r"const DRAWS = \[.*?\];",   draws_js,    html, flags=re.DOTALL)
html = re.sub(r"const ANALYSIS = \{.*?\};", analysis_js, html, flags=re.DOTALL)
(BASE / "lottery_539_viewer.html").write_text(html, encoding="utf-8")

print(f"✓ lottery_539_viewer.html 已更新（最新{N}期 + 分析）")
print(f"  最新：{rows[-1]['date']}  共{len(rows)}期")
