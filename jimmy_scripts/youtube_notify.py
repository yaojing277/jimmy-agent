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

2026-11-14 第二輪：上線後一週，早班仍每天紅燈——log 顯示 5 分鐘內兩端點 8 次全 404，
  是「那段時間整段壞」，重試救不回來。加第三來源＝頻道影片頁（/videos 的 ytInitialData，
  不同後端），只有它也失敗才 exit 1。
  ⚠ RSS 不含會員專屬影片（2026-11 起該頻道新片多為會員專屬【洞八洞洞】系列），
  影片頁有，以 INCLUDE_MEMBERS_ONLY 統一口徑（預設 False，與 RSS 相同）。
"""
import json
import os
import re
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
# 2026-11-09~14 實測：早班 404 是「整段時間都壞」（5 分鐘內兩端點 8 次全 404），
# 重試救不回來，所以 RSS 只各重試一次，真正的備援是下面的頻道影片頁（不同後端）。
RETRY_WAITS = [20]
RETRY_STATUS = {404, 429, 500, 502, 503, 504}
VIDEOS_URL = f"https://www.youtube.com/channel/{CHANNEL_ID}/videos?hl=en&gl=US"
HTML_RETRY_WAITS = [30]
# RSS 本來就不含會員專屬影片；影片頁有，預設也濾掉，兩個來源口徑一致
INCLUDE_MEMBERS_ONLY = False
UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}

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


_REL_UNITS = [("mo", 30 * 86400), ("month", 30 * 86400), ("min", 60), ("minute", 60),
              ("s", 1), ("sec", 1), ("second", 1),
              ("m", 60), ("h", 3600), ("hour", 3600), ("d", 86400), ("day", 86400),
              ("w", 7 * 86400), ("week", 7 * 86400), ("y", 365 * 86400), ("year", 365 * 86400)]


def parse_relative(text, now):
    """'1d ago'／'2mo ago'／'Streamed 3 hours ago' → 約略的 UTC 時間；看不懂回 None。"""
    m = re.search(r"(\d+)\s*([a-z]+)", text or "")
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    for name, sec in _REL_UNITS:
        if unit == name or unit == name + "s":
            return now - timedelta(seconds=n * sec)
    return None


def parse_videos_page(html, now):
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S)
    if not m:
        raise FeedError("影片頁找不到 ytInitialData")
    data = json.loads(m.group(1))
    lockups = []

    def walk(o):
        if isinstance(o, dict):
            if "lockupViewModel" in o:
                lockups.append(o["lockupViewModel"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)

    videos, members = [], 0
    for lv in lockups:
        vid = lv.get("contentId")
        md = lv.get("metadata", {}).get("lockupMetadataViewModel", {})
        title = md.get("title", {}).get("content")
        if not vid or not title:
            continue
        if "BADGE_MEMBERS_ONLY" in json.dumps(lv) and not INCLUDE_MEMBERS_ONLY:
            members += 1
            continue
        parts = [p["text"]["content"]
                 for r in md.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
                 for p in r.get("metadataParts", []) if "text" in p]
        rel = next((t for t in parts if "ago" in t), None)
        videos.append({"id": vid, "title": title,
                       "link": f"https://www.youtube.com/watch?v={vid}",
                       "published": parse_relative(rel, now), "approx": True, "rel": rel})
    if not videos:
        raise FeedError(f"影片頁解析後 0 筆公開影片（會員專屬 {members} 筆）")
    return videos


def fetch_exact_published(vid):
    """從觀看頁取精確發佈時間；取不到回 None（只影響訊息顯示）。"""
    try:
        res = requests.get(f"https://www.youtube.com/watch?v={vid}&hl=en", headers=UA, timeout=20)
        m = re.search(r'"publishDate":"([^"]+)"', res.text)
        return datetime.fromisoformat(m.group(1)) if m else None
    except (requests.RequestException, ValueError):
        return None


def fetch_videos_page(errors):
    for attempt in range(len(HTML_RETRY_WAITS) + 1):
        try:
            res = requests.get(VIDEOS_URL, headers=UA, timeout=20)
            if res.status_code == 200:
                videos = parse_videos_page(res.text, datetime.now(timezone.utc))
                print(f"影片頁抓取成功（第 {attempt + 1} 次，{len(videos)} 筆公開影片）")
                return videos
            msg = f"HTTP {res.status_code}"
        except (requests.RequestException, ValueError, FeedError) as e:
            msg = f"{type(e).__name__}: {e}"
        errors.append(f"videos 頁 第 {attempt + 1} 次：{msg}")
        print(f"  ⚠ {errors[-1]}")
        if attempt < len(HTML_RETRY_WAITS):
            time.sleep(HTML_RETRY_WAITS[attempt])
    return None


def fetch_feed():
    """RSS 兩端點 → 頻道影片頁；全部失敗才拋 FeedError。"""
    if os.environ.get("YT_FORCE_HTML") == "true":   # 手動觸發的診斷選項：只驗證備援那條路
        print("::notice::force_html：跳過 RSS，只抓頻道影片頁")
        errors = []
        videos = fetch_videos_page(errors)
        if videos is None:
            raise FeedError("；".join(errors))
        return videos
    try:
        return fetch_rss()
    except FeedError as e:
        errors = [str(e)]
    print("::warning::RSS 全部失敗，改抓頻道影片頁")
    videos = fetch_videos_page(errors)
    if videos is None:
        raise FeedError("；".join(errors))
    return videos


def fetch_rss():
    errors = []
    for url in RSS_URLS:
        for attempt in range(len(RETRY_WAITS) + 1):
            try:
                res = requests.get(url, headers=UA, timeout=20)
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
        print(f"::error::抓取失敗（RSS 兩端點與頻道影片頁皆失敗）：{e}")
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
    # 影片頁的相對時間看不懂（版面改了）：不推也不登記，每班都會亮警告直到修好
    for v in [v for v in pending if v["published"] is None]:
        print(f"::warning::看不懂發佈時間「{v.get('rel')}」，暫不處理：{v['title']}")
    pending = [v for v in pending if v["published"] is not None]
    to_push = [v for v in pending if now - v["published"] <= max_age]
    skipped = [v for v in pending if v not in to_push]

    # 太舊的直接登記，不推
    newly = list(skipped)
    failed = 0
    if not to_push:
        print("沒有新影片。")
    tw_tz = timezone(timedelta(hours=8))
    for v in sorted(to_push, key=lambda x: x["published"]):
        if v.get("approx"):
            exact = fetch_exact_published(v["id"])
            if exact:
                v["published"], v["approx"] = exact, False
        pub_tw = (f"約 {v['rel']}" if v.get("approx")
                  else v["published"].astimezone(tw_tz).strftime("%Y/%m/%d %H:%M"))
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
