"""YouTube 頻道新影片 → LINE 推播（GitHub Actions：youtube_notify.yml，每天兩次）。

2026-11-08 修正（兩個月推播 0 則的根因）：
  1. 早班（UTC 00:30 排程，實際約 05~07 UTC 才跑）幾乎每次 RSS 回 404／500，
     晚班全部成功——不是頻道 ID 失效（同一 URL 晚班與本機都 200）。
     舊版抓失敗只 print 後 return，workflow 永遠綠燈 → 改為重試＋備援端點，
     全部失敗就 exit 1（紅燈）。
  2. 舊版用「最近 13 小時」判斷新片，而卡哇幾乎都在 22:40 UTC 上片，
     只有那個天天失敗的早班視窗涵蓋得到 → 就算有新片也會整部漏掉。
     改為以 videoId 去重（state 檔由 workflow commit 回 repo），
     任何一班成功都會補推之前漏掉的影片。
"""
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

LINE_TOKEN   = os.environ.get("LINE_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")

CHANNEL_ID   = "UCe4meHPGhNBTDsmzM0ChiDQ"  # 卡哇KAWA
CHANNEL_NAME = "卡哇KAWA"
# 主端點＋備援（頻道上傳清單 UC→UU，同一份資料、不同查詢參數）
RSS_URLS = [
    f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}",
    f"https://www.youtube.com/feeds/videos.xml?playlist_id=UU{CHANNEL_ID[2:]}",
]
RETRY_WAITS = [15, 45, 90]          # 每個端點最多 4 次，間隔遞增
# 實測 404 在這個端點是暫時性的（同一 URL 晚班成功），所以也列入重試
RETRY_STATUS = {404, 429, 500, 502, 503, 504}

STATE_FILE   = Path(__file__).with_name("youtube_notify_state.json")
KEEP_IDS     = 100                  # state 只留最近 N 筆 videoId
MAX_AGE_DAYS = 7                    # 超過 7 天的舊片不補推（防 state 遺失時洗版）
NO_STATE_PUSH_HOURS = 48            # 沒有 state 時，48 小時內的片照推（寧可多推一次）

NS = {"atom": "http://www.w3.org/2005/Atom",
      "yt": "http://www.youtube.com/xml/schemas/2015"}


class FeedError(Exception):
    pass


def send_line(message):
    res = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {LINE_TOKEN}"},
        json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]},
        timeout=15,
    )
    if res.status_code != 200:
        print(f"  LINE 回應 {res.status_code}：{res.text[:200]}")
    return res.status_code == 200


def parse_feed(text):
    root = ET.fromstring(text)
    videos = []
    for entry in root.findall("atom:entry", NS):
        vid = entry.find("yt:videoId", NS)
        pub = entry.find("atom:published", NS)
        if vid is None or not vid.text or pub is None:
            continue
        published = datetime.fromisoformat(pub.text)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        title = entry.find("atom:title", NS)
        link = entry.find("atom:link", NS)
        videos.append({
            "id": vid.text,
            "title": title.text if title is not None else vid.text,
            "link": link.attrib.get("href", "") if link is not None
                    else f"https://www.youtube.com/watch?v={vid.text}",
            "published": published,
        })
    # 頻道一定有影片：0 筆代表回應內容不對，不能當成「沒有新影片」
    if not videos:
        raise FeedError("feed 解析後 0 筆影片")
    return videos


def fetch_feed():
    """依序試各端點，每個端點重試數次；全部失敗才拋 FeedError。"""
    errors = []
    for url in RSS_URLS:
        for attempt in range(len(RETRY_WAITS) + 1):
            try:
                res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
                if res.status_code == 200:
                    videos = parse_feed(res.text)
                    print(f"RSS 抓取成功（{url.split('?')[1].split('=')[0]}，第 {attempt + 1} 次，"
                          f"{len(videos)} 筆，最新 {videos[0]['published']:%Y-%m-%d %H:%M} UTC）")
                    return videos
                msg = f"HTTP {res.status_code}"
                retryable = res.status_code in RETRY_STATUS
            except (requests.RequestException, ET.ParseError, FeedError) as e:
                msg = f"{type(e).__name__}: {e}"
                retryable = True
            errors.append(f"{url.split('?')[1].split('=')[0]} 第 {attempt + 1} 次：{msg}")
            print(f"  ⚠ {errors[-1]}")
            if not retryable or attempt == len(RETRY_WAITS):
                break
            time.sleep(RETRY_WAITS[attempt])
    raise FeedError("；".join(errors))


def load_state():
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(ids):
    STATE_FILE.write_text(
        json.dumps({"notified": ids[:KEEP_IDS]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


def main():
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC] 開始檢查 {CHANNEL_NAME} 頻道新影片...")

    try:
        videos = fetch_feed()
    except FeedError as e:
        print(f"::error::RSS 抓取失敗（所有端點與重試皆失敗）：{e}")
        sys.exit(1)

    now = datetime.now(timezone.utc)
    state = load_state()
    if state is None:
        print("::warning::沒有 state 檔，48 小時內的影片照推，其餘只登記。")
        notified = []
        max_age = timedelta(hours=NO_STATE_PUSH_HOURS)
    else:
        notified = state.get("notified", [])
        max_age = timedelta(days=MAX_AGE_DAYS)

    known = set(notified)
    pending = [v for v in videos if v["id"] not in known]
    to_push = [v for v in pending if now - v["published"] <= max_age]
    skipped = [v for v in pending if v not in to_push]

    # 太舊的直接登記，不推
    newly = list(skipped)
    failed = 0
    if not to_push:
        print("沒有新影片。")
    tw_tz = timezone(timedelta(hours=8))
    for v in sorted(to_push, key=lambda x: x["published"]):
        pub_tw = v["published"].astimezone(tw_tz).strftime("%Y/%m/%d %H:%M")
        message = (
            f"🎬 {CHANNEL_NAME} 有新影片！\n\n"
            f"《{v['title']}》\n\n"
            f"上傳時間：{pub_tw}\n"
            f"▶ {v['link']}"
        )
        if send_line(message):
            print(f"推播成功：{v['title']}")
            newly.append(v)
        else:
            # 不登記 → 下一班會再推一次
            print(f"::error::推播失敗：{v['title']}")
            failed += 1

    if newly or state is None:
        newly.sort(key=lambda x: x["published"], reverse=True)   # 新到舊，截斷時丟最舊的
        save_state([v["id"] for v in newly] + notified)
        print(f"state 更新：新增 {len(newly)} 筆")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
