#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
消費明細分析器（AndroMoney 類記帳 CSV）
- 讀取 UTF-16 CSV，穩健合併「跨行備註」造成的碎片列
- 產出多維度彙整（年度、月度、主分類、子分類、主分類×年度、Top 單筆）
- 以 JSON 輸出，供 build_expense_dashboard.py 寫入 Google 試算表

用法:
    python3 expense_analyze.py <來源CSV路徑> [--out out.json]
"""
import csv
import io
import json
import re
import sys
from collections import defaultdict
from datetime import datetime

DATE_RE = re.compile(r'^\d{4}/\d{1,2}/\d{1,2}$')
COLS = ['日期', '類別', '主分類', '子分類', '帳戶', '專案', '金額', '匯率',
        '小計', '建檔時間', 'GPS', '地址', '發票號碼', '轉帳', '備註']


def load_rows(path):
    """讀 UTF-16 / UTF-8 皆可；用 csv 正確處理引號內換行後，再以日期開頭辨識有效列。"""
    raw = open(path, 'rb').read()
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        text = raw.decode('utf-16')
    else:
        text = raw.decode('utf-8-sig')
    reader = csv.reader(io.StringIO(text), delimiter='\t')
    header = next(reader)
    records = []
    for r in reader:
        if not r or all(c.strip() == '' for c in r):
            continue
        first = r[0].strip().strip('"').strip()
        if DATE_RE.match(first):
            records.append([c.strip().strip('"').strip() for c in r])
        elif records:
            # 碎片列：併回上一筆備註（跨行備註被拆開的殘骸）
            records[-1][-1] = (records[-1][-1] + ' ' + ' '.join(r)).strip()
    return records


def to_float(s):
    try:
        return float(str(s).replace(',', ''))
    except Exception:
        return 0.0


def analyze(path):
    recs = load_rows(path)

    def col(r, name):
        i = COLS.index(name)
        return r[i] if i < len(r) else ''

    total = 0.0
    year_amt = defaultdict(float); year_cnt = defaultdict(int)
    month_amt = defaultdict(float); month_cnt = defaultdict(int)
    main_amt = defaultdict(float); main_cnt = defaultdict(int)
    sub_amt = defaultdict(float); sub_cnt = defaultdict(int)
    main_year = defaultdict(float)   # (main, year) -> amt
    top_single = []
    dmin, dmax = None, None

    for r in recs:
        d = col(r, '日期')
        amt = to_float(col(r, '小計')) or to_float(col(r, '金額'))
        main = col(r, '主分類') or '(未分類)'
        sub = col(r, '子分類') or '(未分類)'
        try:
            dt = datetime.strptime(d, '%Y/%m/%d')
        except Exception:
            continue
        dmin = dt if dmin is None or dt < dmin else dmin
        dmax = dt if dmax is None or dt > dmax else dmax
        ym = f'{dt.year:04d}-{dt.month:02d}'
        total += amt
        year_amt[dt.year] += amt; year_cnt[dt.year] += 1
        month_amt[ym] += amt; month_cnt[ym] += 1
        main_amt[main] += amt; main_cnt[main] += 1
        sub_amt[(main, sub)] += amt; sub_cnt[(main, sub)] += 1
        main_year[(main, dt.year)] += amt
        top_single.append((amt, d, main, sub, col(r, '備註')[:40]))

    years = sorted(year_amt)
    mains = sorted(main_amt, key=lambda k: -main_amt[k])
    top_single.sort(reverse=True)

    result = {
        'meta': {
            'records': len(recs),
            'date_min': dmin.strftime('%Y/%m/%d') if dmin else '',
            'date_max': dmax.strftime('%Y/%m/%d') if dmax else '',
            'total': round(total),
            'years': years,
        },
        'yearly': [[y, year_cnt[y], round(year_amt[y]),
                    round(year_amt[y] / year_cnt[y]) if year_cnt[y] else 0]
                   for y in years],
        'monthly': [[ym, month_cnt[ym], round(month_amt[ym])]
                    for ym in sorted(month_amt)],
        'main': [[m, main_cnt[m], round(main_amt[m]),
                  round(main_amt[m] / total * 100, 1) if total else 0]
                 for m in mains],
        'sub_top': [[m, s, sub_cnt[(m, s)], round(sub_amt[(m, s)])]
                    for (m, s) in sorted(sub_amt, key=lambda k: -sub_amt[k])[:40]],
        'main_year': {
            'mains': mains,
            'years': years,
            'grid': [[round(main_year.get((m, y), 0)) for y in years] for m in mains],
        },
        'top_single': [[round(a), d, m, s, note]
                       for (a, d, m, s, note) in top_single[:50]],
    }
    return result


if __name__ == '__main__':
    src = sys.argv[1]
    out = 'expense_analysis.json'
    if '--out' in sys.argv:
        out = sys.argv[sys.argv.index('--out') + 1]
    res = analyze(src)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    m = res['meta']
    print(f"有效筆數 {m['records']}  期間 {m['date_min']}~{m['date_max']}  "
          f"累計 ${m['total']:,}")
    print(f"→ 已輸出 {out}")
