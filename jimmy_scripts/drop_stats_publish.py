#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跌幅分布「手機查詢頁」資料建置＋發佈（GitHub Pages：yaojing277/projects 的 drop/）。

瀏覽器不能直接打證交所 API（CORS），所以改成：排程每天抓「全市場收盤行情」存成
每月一個 JSON，網頁（drop_stats_web.html）在手機上直接載入 JSON 當場計算，電腦不必開。

資料源（皆官方、一次請求＝全市場一天）：
  MI_INDEX  每日收盤行情（ALLBUT0999）：收盤價＋官方漲跌價差 → 分割/反分割已由證交所還原
  TWT49U    除權除息計算結果表：除息日（MI_INDEX 標「X」、價差 0）改用官方「除權息參考價」
  → 每日漲跌幅 pct = (收盤 − 參考價) / 參考價，與證交所／券商 App 顯示的漲跌幅一致

月檔格式 drop/data/YYYY-MM.json：
  {"m": "2026-08", "dates": ["2026-08-03", ...],
   "n": {代號: 名稱}, "c": {代號: [收盤|null, ...]}, "p": {代號: [漲跌%|null, ...]}}
  c/p 與 dates 對齊；null＝當日無成交。另有 drop/data/meta.json 記月份清單與最後交易日。

用法：
  python3 drop_stats_publish.py build --from 2026-01 [--to 2026-09]   # 本機建置到 drop_site/
  python3 drop_stats_publish.py publish                               # 把 drop_site/ 推上 Pages
  python3 drop_stats_publish.py daily                                  # 排程用：重建當月(月初連上月)＋發佈

PAT：環境變數 PAGES_PAT → GH_PAT → ~/.config/gh_pat；都沒有時 publish 安靜跳過（不讓排程變紅）。
"""
import argparse
import base64
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "drop_site")           # 本機建置輸出（不進版控也無妨，可隨時重建）
DATA = os.path.join(SITE, "data")
WEB_SRC = os.path.join(HERE, "drop_stats_web.html")

REPO = "yaojing277/projects"
PREFIX = "drop"                                   # repo 內子目錄 → yaojing277.github.io/projects/drop/
API = f"https://api.github.com/repos/{REPO}/contents"

UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")}
THROTTLE = 1.0        # 證交所對密集請求會暫時封鎖 IP，每次請求間隔 1 秒


# ========================= 證交所 =========================
def _get_json(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as ex:
            if i == tries - 1:
                raise
            print(f"  ⚠ {ex}，{5 * (i + 1)} 秒後重試")
            time.sleep(5 * (i + 1))
        finally:
            time.sleep(THROTTLE)


def _num(s):
    s = str(s).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _roc(s):
    """'115年08月18日' → date"""
    m = re.match(r"(\d+)年(\d+)月(\d+)日", s)
    return datetime.date(int(m[1]) + 1911, int(m[2]), int(m[3])) if m else None


def fetch_day(d):
    """某日全市場收盤行情 → {代號: (名稱, 收盤, 符號, 價差)}；非交易日回 None。"""
    j = _get_json("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
                  f"?date={d:%Y%m%d}&type=ALLBUT0999&response=json")
    if j.get("stat") != "OK":
        return None
    for t in j.get("tables", []):
        if "每日收盤行情" in t.get("title", ""):
            out = {}
            for r in t["data"]:
                sign = re.sub(r"<[^>]+>", "", r[9]).strip()        # '+' '-' 'X' ''
                out[r[0].strip()] = (r[1].strip(), _num(r[8]), sign, _num(r[10]))
            return out
    return None


def fetch_exright(start, end):
    """期間除權息 → {(date, 代號): 除權息參考價}"""
    j = _get_json("https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
                  f"?startDate={start:%Y%m%d}&endDate={end:%Y%m%d}&response=json")
    out = {}
    for r in j.get("data", []) if j.get("stat") == "OK" else []:
        d, ref = _roc(r[0]), _num(r[4])
        if d and ref:
            out[(d, r[1].strip())] = ref
    return out


# ========================= 建置 =========================
def month_range(ym_from, ym_to):
    y, m = map(int, ym_from.split("-"))
    ty, tm = map(int, ym_to.split("-"))
    while (y, m) <= (ty, tm):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def build_month(y, m, prev_close=None):
    """建一個月的月檔；prev_close 為上月最後收盤（X 日查無 TWT49U 時的備援參考價）。
    回傳 (月檔 dict, 本月最後收盤 dict)。"""
    first = datetime.date(y, m, 1)
    last = (datetime.date(y + (m == 12), m % 12 + 1, 1) - datetime.timedelta(days=1))
    last = min(last, datetime.date.today())
    exr = fetch_exright(first, last)
    prev = dict(prev_close or {})
    dates, names, c, p = [], {}, {}, {}
    d = first
    while d <= last:
        if d.weekday() < 6:                     # 週日必休；週六可能補班，照查
            day = fetch_day(d)
            if day:
                i = len(dates)
                dates.append(d.isoformat())
                for code, (name, close, sign, diff) in day.items():
                    names[code] = name
                    cc = c.setdefault(code, [None] * i)
                    pp = p.setdefault(code, [None] * i)
                    pct = None
                    if close is not None:
                        if sign in ("+", "-") and diff is not None:
                            ref = close - diff if sign == "+" else close + diff
                        elif sign == "X":
                            ref = exr.get((d, code)) or prev.get(code)
                        else:                                   # 平盤
                            ref = close
                        if ref:
                            pct = round((close - ref) / ref * 100, 2)
                        prev[code] = close
                    cc.append(close)
                    pp.append(pct)
                for code in c:                      # 當日沒出現的代號補 null 對齊
                    if len(c[code]) < len(dates):
                        c[code].append(None)
                        p[code].append(None)
                print(f"  {d} ✓ {len(day)} 檔")
        d += datetime.timedelta(days=1)
    return {"m": f"{y}-{m:02d}", "dates": dates, "n": names, "c": c, "p": p}, prev


def last_closes(month_doc):
    out = {}
    for code, arr in month_doc["c"].items():
        for v in reversed(arr):
            if v is not None:
                out[code] = v
                break
    return out


def load_local(ym):
    path = os.path.join(DATA, f"{ym}.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_month(doc):
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, f"{doc['m']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    kb = os.path.getsize(path) / 1024
    print(f"✓ {doc['m']}：{len(doc['dates'])} 個交易日、{len(doc['c'])} 檔、{kb:.0f} KB")


def prev_month_doc(ym):
    """上個月月檔（先找本機，再讀線上 Pages）：當作 X 日查無 TWT49U 時的備援前收。
    排程環境只重建當月，沒有這步的話月初／冷門標的的除息日會算不出漲跌幅。"""
    y, m = map(int, ym.split("-"))
    pym = f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"
    doc = load_local(pym)
    if doc:
        return doc
    try:
        url = f"https://yaojing277.github.io/projects/{PREFIX}/data/{pym}.json"
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            return json.load(r)
    except Exception:
        print(f"  ⚠ 讀不到上月 {pym} 月檔，除息日備援前收從本月開始累積")
        return None


def build(ym_from, ym_to, prev_doc=None):
    prev_doc = prev_doc or prev_month_doc(ym_from)
    prev = last_closes(prev_doc) if prev_doc else None
    for y, m in month_range(ym_from, ym_to):
        print(f"建置 {y}-{m:02d} …")
        doc, prev = build_month(y, m, prev)
        if doc["dates"]:
            save_month(doc)


# ========================= 發佈 =========================
def _pat():
    for k in ("PAGES_PAT", "GH_PAT"):
        if os.environ.get(k):
            return os.environ[k].strip()
    path = os.path.expanduser("~/.config/gh_pat")
    if os.path.isfile(path):
        return open(path).read().strip()
    return None


def _gh(method, path, pat, body=None):
    req = urllib.request.Request(
        f"{API}/{path}", method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"token {pat}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", **UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            return None
        raise RuntimeError(f"{method} {path} 失敗 {ex.code}: {ex.read()[:300]}")


def _blob_sha(content: bytes):
    import hashlib
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def _put(path, content: bytes, pat, listing_cache):
    parent, name = path.rsplit("/", 1)
    if parent not in listing_cache:
        lst = _gh("GET", parent, pat)
        listing_cache[parent] = {it["name"]: it["sha"] for it in lst} if isinstance(lst, list) else {}
    sha = listing_cache[parent].get(name)
    if sha == _blob_sha(content):
        print(f"  • {path} 無變化")
        return False
    body = {"message": f"跌幅查詢資料：{path} {datetime.date.today()}",
            "content": base64.b64encode(content).decode(), "branch": "main"}
    if sha:
        body["sha"] = sha
    _gh("PUT", path, pat, body)
    print(f"  ✓ {path}")
    return True


def publish(only_months=None):
    pat = _pat()
    if not pat:
        print("無 PAT（PAGES_PAT / GH_PAT / ~/.config/gh_pat），跳過發佈")
        return
    cache = {}
    local = sorted(f[:-5] for f in os.listdir(DATA) if re.fullmatch(r"\d{4}-\d{2}\.json", f))
    for ym in local:
        if only_months and ym not in only_months:
            continue
        with open(os.path.join(DATA, f"{ym}.json"), "rb") as f:
            _put(f"{PREFIX}/data/{ym}.json", f.read(), pat, cache)

    # meta：月份清單以「遠端已有＋本次上傳」為準（排程每天只建當月，本機不一定有全部月檔）
    remote = cache.get(f"{PREFIX}/data") or {}
    months = sorted({n[:-5] for n in remote if re.fullmatch(r"\d{4}-\d{2}\.json", n)} | set(local))
    latest = load_local(local[-1]) if local else None
    meta = {"months": months,
            "last_date": latest["dates"][-1] if latest and latest["dates"] else None,
            "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
    _put(f"{PREFIX}/data/meta.json",
         json.dumps(meta, ensure_ascii=False).encode(), pat, cache)
    with open(WEB_SRC, "rb") as f:
        _put(f"{PREFIX}/index.html", f.read(), pat, cache)
    print(f"發佈完成 → https://yaojing277.github.io/projects/{PREFIX}/（資料到 {meta['last_date']}）")


def daily():
    """排程用：重建當月；月初 7 天內連上月一起重建（補上月底漏跑）。"""
    t = datetime.date.today()
    prev_ym = f"{t.year - (t.month == 1)}-{(t.month - 2) % 12 + 1:02d}"
    cur_ym = f"{t:%Y-%m}"
    todo = [prev_ym, cur_ym] if t.day <= 7 else [cur_ym]
    build(todo[0], todo[-1])
    publish(only_months=set(todo))


def main():
    ap = argparse.ArgumentParser(description="跌幅分布手機查詢頁：資料建置與發佈")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--from", dest="ym_from", required=True, help="起始月 YYYY-MM")
    b.add_argument("--to", dest="ym_to", default=f"{datetime.date.today():%Y-%m}")
    sub.add_parser("publish")
    sub.add_parser("daily")
    a = ap.parse_args()
    if a.cmd == "build":
        build(a.ym_from, a.ym_to)
    elif a.cmd == "publish":
        publish()
    else:
        daily()


if __name__ == "__main__":
    sys.exit(main())
