#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跌幅分布圖卡「即時查詢」網頁：輸入代號 → 後端即時計算 → 顯示圖卡。

瀏覽器無法直接呼叫證交所 API（CORS），故以本機小伺服器代打，
計算與版面完全沿用 drop_stats_card.py（analyze / render_html）。

用法：
  python3 drop_stats_server.py              # 開 http://localhost:8766
  python3 drop_stats_server.py --lan        # 同網段手機也能連（http://<本機IP>:8766）
  python3 drop_stats_server.py --port 9000
"""
import argparse
import datetime
import html
import socket
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import drop_stats_card as card
import twse_hist

LEVERAGED_EDGES = "1,2,4,7"    # 代號以 L 結尾（正二）自動多切一桶
QUICK = ["00878", "00631L", "00663L", "00675L", "00685L", "0050", "0056", "00919", "2330"]


def form_html(q):
    e = lambda k, d="": html.escape(q.get(k, d))
    today = datetime.date.today()
    chips = "".join(f'<a href="/?code={c}">{c}</a>' for c in QUICK)
    return f"""
<form class="q" method="get" action="/">
  <input name="code" placeholder="股票代號，如 00878" value="{e('code')}" autofocus required>
  <input type="date" name="start" value="{e('start', f'{today.year}-01-01')}">
  <input type="date" name="end" value="{e('end', str(today))}">
  <input name="compare" placeholder="對照（空白＝不比）" value="{e('compare', '0050')}" size="8">
  <input name="edges" placeholder="區間 1,2,4" value="{e('edges')}" size="8" title="跌幅分桶界線；空白＝自動（正二 1,2,4,7）">
  <label><input type="checkbox" name="raw" value="1" {'checked' if q.get('raw') else ''}> 不調整除息</label>
  <button>計算</button>
  <div class="chips">{chips}</div>
</form>
<style>
.q{{width:1080px;margin:0 auto 16px;display:flex;flex-wrap:wrap;gap:8px;align-items:center;
    font:16px "PingFang TC",sans-serif}}
.q input{{padding:8px 10px;border:1px solid #c5cfdd;border-radius:8px;font-size:16px}}
.q input[name=code]{{width:170px;font-weight:700}}
.q button{{padding:8px 22px;border:0;border-radius:8px;background:#1b3a6b;color:#fff;font-size:16px;cursor:pointer}}
.q .chips{{width:100%;display:flex;gap:6px;flex-wrap:wrap}}
.q .chips a{{padding:3px 10px;border-radius:14px;background:#e3e9f3;color:#1b3a6b;text-decoration:none;font-size:14px}}
.msg{{width:1080px;margin:40px auto;font:18px "PingFang TC",sans-serif;color:#556;text-align:center}}
body.loading .card{{opacity:.35}}
</style>
<script>document.addEventListener('submit',()=>document.body.classList.add('loading'));</script>
"""


def page(q):
    """依查詢參數回傳整頁 HTML。"""
    code = q.get("code", "").strip().upper()
    if not code:
        return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>跌幅分布查詢</title></head><body style="background:#f4f7fb;padding:24px">
{form_html(q)}<div class="msg">輸入代號後按「計算」，或點上方常用代號。</div></body></html>"""

    start = datetime.date.fromisoformat(q.get("start") or f"{datetime.date.today().year}-01-01")
    end = datetime.date.fromisoformat(q.get("end") or str(datetime.date.today()))
    compare = q.get("compare", "0050").strip().upper()   # 沒帶參數＝0050；表單清空＝不比
    edges_s = q.get("edges", "").strip() or (LEVERAGED_EDGES if code.endswith("L") else "1,2,4")
    edges = [float(x) for x in edges_s.split(",") if x.strip()]

    _drop_fresh_cache()
    s = card.analyze(code, start, end,
                     compare if compare and compare != code else None,
                     bool(q.get("raw")), edges)
    doc = card.render_html(s)
    return doc.replace("<body>", "<body>" + form_html(q), 1)


def _drop_fresh_cache():
    """伺服器長駐時，當月收盤與除息表每次重抓（歷史月份沿用快取，查詢才快）。"""
    t = datetime.date.today()
    for k in [k for k in twse_hist._close_cache if (k[1], k[2]) == (t.year, t.month)]:
        del twse_hist._close_cache[k]
    twse_hist._exright_cache.clear()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/":
            self.send_error(404)
            return
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}
        try:
            body, status = page(q), 200
        except SystemExit as ex:                       # analyze 查無資料
            body, status = self._error(q, str(ex)), 404
        except Exception as ex:
            traceback.print_exc()
            body, status = self._error(q, f"計算失敗：{ex}"), 500
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _error(q, msg):
        return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>跌幅分布查詢</title></head><body style="background:#f4f7fb;padding:24px">
{form_html(q)}<div class="msg">⚠️ {html.escape(msg)}</div></body></html>"""

    def log_message(self, fmt, *args):
        print(f"[{datetime.datetime.now():%H:%M:%S}] {self.path}")


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description="跌幅分布圖卡即時查詢網頁")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--lan", action="store_true", help="開放同網段連線（手機可用）")
    a = ap.parse_args()
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    srv = ThreadingHTTPServer((host, a.port), Handler)
    print(f"跌幅分布查詢 → http://localhost:{a.port}")
    if a.lan:
        print(f"手機（同 Wi-Fi）→ http://{lan_ip()}:{a.port}")
    print("Ctrl+C 結束")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
