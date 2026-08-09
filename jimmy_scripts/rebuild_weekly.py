import csv
from collections import defaultdict

HISTORY = '/Users/jimmy/Downloads/jimmy-agent/jimmy_scripts/539_history.csv'
WEEKLY  = '/Users/jimmy/Downloads/jimmy-agent/jimmy_scripts/539_weekly.csv'

draws = list(csv.DictReader(open(HISTORY, encoding='utf-8-sig')))
weeks = defaultdict(lambda: {'iso_week':'','year':0,'draw_count':0,
                             'Mon':'','Tue':'','Wed':'','Thu':'','Fri':'','Sat':'','Sun':''})
for d in draws:
    wk, wd = d['iso_week'], d['weekday']
    weeks[wk]['iso_week'] = wk
    weeks[wk]['year'] = d['year']
    weeks[wk][wd] = f"{d['n1']},{d['n2']},{d['n3']},{d['n4']},{d['n5']}"
    weeks[wk]['draw_count'] += 1

rows = sorted(weeks.values(), key=lambda x: x['iso_week'])

with open(WEEKLY, 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.DictWriter(f, fieldnames=['iso_week','year','draw_count','Mon','Tue','Wed','Thu','Fri','Sat','Sun'])
    writer.writeheader()
    writer.writerows(rows)

print(f'完成！共 {len(rows)} 週')
print(f'範圍: {rows[0]["iso_week"]} ~ {rows[-1]["iso_week"]}')
for r in rows[-3:]:
    print(f'  {r["iso_week"]} ({r["draw_count"]}筆): Mon={r["Mon"]} Sat={r["Sat"]}')
