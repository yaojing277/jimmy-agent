#!/usr/bin/env python3
"""
今彩539 號碼熱度分析
三種方法並列，輸出 JSON 給 HTML 使用
"""
import csv, json
from pathlib import Path
from datetime import date

HISTORY = Path(__file__).parent / "539_history.csv"
OUTPUT  = Path(__file__).parent / "539_analysis.json"

rows = list(csv.DictReader(open(HISTORY, encoding="utf-8-sig")))
total = len(rows)

# ── 所有期的號碼清單（List[List[int]]）
all_nums = [[int(r["n1"]),int(r["n2"]),int(r["n3"]),int(r["n4"]),int(r["n5"])]
            for r in rows]

NUMBERS = list(range(1, 40))

# ─────────────────────────────────────────────
# 方法一：近50期熱號
# ─────────────────────────────────────────────
def hot_score(n_last: int):
    draws = all_nums[-n_last:]
    cnt = {n: 0 for n in NUMBERS}
    for draw in draws:
        for n in draw:
            cnt[n] += 1
    expected = n_last * 5 / 39          # 期望出現次數
    score = {n: round(cnt[n] / expected, 4) for n in NUMBERS}
    return cnt, score

hot50_cnt,  hot50_score  = hot_score(50)
hot200_cnt, hot200_score = hot_score(200)

# ─────────────────────────────────────────────
# 方法二：遺漏值（冷號）
# 上次出現距今的期數，越大越「欠出」
# ─────────────────────────────────────────────
last_seen = {n: -1 for n in NUMBERS}
for idx, draw in enumerate(all_nums):
    for n in draw:
        last_seen[n] = idx

miss = {n: (total - 1 - last_seen[n]) for n in NUMBERS}

# ─────────────────────────────────────────────
# 方法三：加權綜合分
# 50期(50%) + 200期(30%) + 全部(20%)，皆除以期望值正規化
# ─────────────────────────────────────────────
all_cnt,_ = hot_score(total)
expected_all = total * 5 / 39

composite = {}
for n in NUMBERS:
    s50  = hot50_cnt[n]  / (50  * 5 / 39)
    s200 = hot200_cnt[n] / (200 * 5 / 39)
    sall = all_cnt[n]    / expected_all
    composite[n] = round(0.5 * s50 + 0.3 * s200 + 0.2 * sall, 4)

# ─────────────────────────────────────────────
# 排名，取前10
# ─────────────────────────────────────────────
def rank10(score_dict, reverse=True):
    sorted_items = sorted(score_dict.items(), key=lambda x: x[1], reverse=reverse)
    return [{"num": n, "score": s} for n, s in sorted_items[:10]]

result = {
    "generated":    date.today().isoformat(),
    "total_draws":  total,
    "latest_date":  rows[-1]["date"],
    # 方法一：近50期熱號（分數越高越熱）
    "hot50": rank10(hot50_score),
    # 方法二：遺漏值冷號（score=遺漏期數，越高越久沒出現）
    "cold":  rank10(miss),
    # 方法三：加權綜合分
    "composite": rank10(composite),
}

OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

# ── 終端機輸出 ──
print(f"=== 今彩539 號碼分析（共 {total} 期，最新：{rows[-1]['date']}）===\n")

print("【方法一】近50期熱號 TOP 10（分數 >1.0 表示高於期望出現率）")
print(f"  期望每號出現次數：{50*5/39:.1f} 次")
for i, x in enumerate(result["hot50"], 1):
    bar = "█" * hot50_cnt[x["num"]]
    print(f"  {i:2}. 號碼 {x['num']:2d}  出現{hot50_cnt[x['num']]:2d}次  熱度 {x['score']:.2f}  {bar}")

print(f"\n【方法二】遺漏值冷號 TOP 10（距上次出現的期數）")
for i, x in enumerate(result["cold"], 1):
    pct = x["score"] / (39/5)
    print(f"  {i:2}. 號碼 {x['num']:2d}  遺漏 {x['score']:3d} 期  ({pct:.1f}倍平均間隔)")

print(f"\n【方法三】加權綜合分 TOP 10（50期50%+200期30%+全部20%，>1.0高於期望）")
for i, x in enumerate(result["composite"], 1):
    print(f"  {i:2}. 號碼 {x['num']:2d}  綜合分 {x['score']:.3f}")

print(f"\n結果已輸出至 {OUTPUT.name}")
