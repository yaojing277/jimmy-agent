import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

LINE_TOKEN   = os.environ.get("LINE_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")

CHANNEL_ID   = "UCe4meHPGhNBTDsmzM0ChiDQ"  # 卡哇KAWA
CHANNEL_NAME = "卡哇KAWA"
RSS_URL      = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"

# 檢查最近 13 小時內的影片（早上9點 / 晚上9點各檢查一次，間隔12小時，多1小時緩衝）
HOURS_BACK = 13


def send_line(message):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_TOKEN}",
    }
    body = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": message}],
    }
    res = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers=headers,
        json=body,
    )
    return res.status_code == 200


def fetch_new_videos():
    res = requests.get(RSS_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    res.raise_for_status()

    ns = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    root = ET.fromstring(res.text)

    now_utc = datetime.now(timezone.utc)
    cutoff  = now_utc - timedelta(hours=HOURS_BACK)

    new_videos = []
    for entry in root.findall("atom:entry", ns):
        published_str = entry.find("atom:published", ns).text  # 2026-04-12T10:00:00+00:00
        published = datetime.fromisoformat(published_str)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)

        if published >= cutoff:
            title   = entry.find("atom:title", ns).text
            link    = entry.find("atom:link", ns).attrib.get("href", "")
            new_videos.append({"title": title, "link": link, "published": published})

    return new_videos


def main():
    print(f"[{datetime.now()}] 開始檢查 {CHANNEL_NAME} 頻道新影片...")

    try:
        videos = fetch_new_videos()
    except Exception as e:
        print(f"RSS 抓取失敗：{e}")
        return

    if not videos:
        print("沒有新影片。")
        return

    tw_tz = timezone(timedelta(hours=8))
    for v in videos:
        pub_tw = v["published"].astimezone(tw_tz).strftime("%Y/%m/%d %H:%M")
        message = (
            f"🎬 {CHANNEL_NAME} 有新影片！\n\n"
            f"《{v['title']}》\n\n"
            f"上傳時間：{pub_tw}\n"
            f"▶ {v['link']}"
        )
        ok = send_line(message)
        status = "推播成功" if ok else "推播失敗"
        print(f"{status}：{v['title']}")


if __name__ == "__main__":
    main()
