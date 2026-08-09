#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
yt_auto_summary.py — 頻道新片自動摘要（接 yt_summary.py 核心）

用法：
    python3 yt_auto_summary.py                # 檢查新片 → 摘要 → 部署 → LINE 推播
    python3 yt_auto_summary.py --dry-run      # 只看會處理哪些影片，不做任何事
    python3 yt_auto_summary.py --backfill 2   # 首次執行時，每頻道回補最新 2 部
    python3 yt_auto_summary.py --no-deploy    # 不部署 GitHub Pages
    python3 yt_auto_summary.py --no-line      # 不推 LINE

行為：
    - 各頻道 RSS 比對 yt_auto_state.json 的已見清單，找出新影片
    - 首次執行的頻道只「登記現有影片」不摘要（避免一口氣回補整個頻道），
      想回補請加 --backfill N
    - 無字幕影片由 yt_summary.py 自動 fallback Whisper 轉錄（較慢）；
      連轉錄都失敗才登記跳過
    - 其他錯誤（網路等）不登記，下次執行自動重試
    - LINE 金鑰：環境變數 LINE_TOKEN / LINE_USER_ID，或同目錄 line_secrets.json
      {"LINE_TOKEN": "...", "LINE_USER_ID": "..."}；缺值時只跳過推播，不影響摘要
"""

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

import yt_summary

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "yt_auto_state.json"
DEPLOY_SCRIPT = BASE_DIR / "deploy_yt_summaries.sh"
PAGES_URL = "https://yaojing277.github.io/yt-summaries"
SEEN_CAP = 60  # RSS 一次最多 15 筆，留寬裕即可

CHANNELS = [
    {"name": "阿良的正二人生", "id": "UCN587kml9afmLDTOfBfmPYw"},
    {"name": "槓桿人生",       "id": "UCm9y7UOusDTjQ0rspg7NT5w"},
    {"name": "卡哇KAWA",       "id": "UCe4meHPGhNBTDsmzM0ChiDQ"},  # 全頻道無字幕，走 Whisper 轉錄（較慢）
]


# ---------------------------------------------------------------- RSS / 狀態

def fetch_rss_videos(channel_id: str):
    """回傳 [(video_id, title), ...]，RSS 由新到舊。"""
    res = requests.get(
        f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
        headers={"User-Agent": "Mozilla/5.0"}, timeout=15,
    )
    res.raise_for_status()
    ns = {"atom": "http://www.w3.org/2005/Atom",
          "yt": "http://www.youtube.com/xml/schemas/2015"}
    root = ET.fromstring(res.text)
    videos = []
    for entry in root.findall("atom:entry", ns):
        vid = entry.find("yt:videoId", ns)
        title = entry.find("atom:title", ns)
        if vid is not None and vid.text:
            videos.append((vid.text, title.text if title is not None else vid.text))
    return videos


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("⚠️  yt_auto_state.json 損毀，重建。")
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def summarized_video_ids() -> set:
    """摘要庫已有的影片（手動摘要過的也算已見）。"""
    if yt_summary.INDEX_JSON.exists():
        try:
            return {e["video_id"] for e in
                    json.loads(yt_summary.INDEX_JSON.read_text(encoding="utf-8"))}
        except json.JSONDecodeError:
            pass
    return set()


# ---------------------------------------------------------------- LINE

def load_line_creds():
    token = os.environ.get("LINE_TOKEN", "")
    user_id = os.environ.get("LINE_USER_ID", "")
    secrets_file = BASE_DIR / "line_secrets.json"
    if (not token or not user_id) and secrets_file.exists():
        try:
            data = json.loads(secrets_file.read_text(encoding="utf-8"))
            token = token or data.get("LINE_TOKEN", "")
            user_id = user_id or data.get("LINE_USER_ID", "")
        except json.JSONDecodeError:
            print("⚠️  line_secrets.json 格式錯誤。")
    return token, user_id


def send_line(token: str, user_id: str, message: str) -> bool:
    res = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
        json={"to": user_id, "messages": [{"type": "text", "text": message}]},
        timeout=15,
    )
    return res.status_code == 200


def build_line_message(entry: dict) -> str:
    points = "\n".join(f"・{p}" for p in entry.get("key_points", [])[:3])
    lines = [f"🤖 新片 AI 摘要｜{entry.get('channel', '')}",
             "",
             f"《{entry.get('title', '')}》"]
    if entry.get("one_line"):
        lines += ["", f"💡 {entry['one_line']}"]
    if points:
        lines += ["", points]
    lines += ["",
              f"📄 {PAGES_URL}/{entry['file']}",
              f"▶ https://www.youtube.com/watch?v={entry['video_id']}"]
    return "\n".join(lines)


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="頻道新片自動摘要")
    ap.add_argument("--dry-run", action="store_true", help="只列出會處理的影片")
    ap.add_argument("--backfill", type=int, default=0,
                    help="首次執行的頻道回補最新 N 部（預設 0＝只登記不摘要）")
    ap.add_argument("--no-deploy", action="store_true", help="不部署 GitHub Pages")
    ap.add_argument("--no-line", action="store_true", help="不推 LINE 通知")
    ap.add_argument("--model", default="sonnet", help="claude CLI 模型別名")
    ap.add_argument("--whisper-model", default=yt_summary.WHISPER_MODEL_SIZE,
                    help="無字幕影片的 Whisper 轉錄模型（tiny/base/small/medium）")
    args = ap.parse_args()

    state = load_state()
    already = summarized_video_ids()
    done, skipped, failed = [], [], []

    for ch in CHANNELS:
        cid, cname = ch["id"], ch["name"]
        print(f"\n📡 檢查頻道：{cname}")
        try:
            rss = fetch_rss_videos(cid)
        except Exception as e:
            print(f"  ⚠️ RSS 抓取失敗，跳過：{e}")
            continue

        ch_state = state.setdefault(cid, {"name": cname, "seen": []})
        seen = set(ch_state["seen"]) | already
        first_run = not ch_state["seen"]

        new = [(v, t) for v, t in rss if v not in seen]
        if first_run and new:
            keep = new[:args.backfill] if args.backfill > 0 else []
            if not keep:
                print(f"  🆕 首次執行：登記現有 {len(new)} 部影片（不摘要，"
                      f"想回補請加 --backfill N）")
            else:
                print(f"  🆕 首次執行：回補最新 {len(keep)} 部，其餘 "
                      f"{len(new) - len(keep)} 部僅登記")
            if not args.dry_run:
                ch_state["seen"] = [v for v, _ in rss]
            new = keep

        if not new:
            if not first_run:
                print("  沒有新影片。")
            continue

        for vid, title in reversed(new):  # 由舊到新處理
            print(f"  ▶ 新影片：{title}")
            if args.dry_run:
                done.append({"video_id": vid, "title": title, "channel": cname,
                             "dry": True})
                continue
            try:
                yt_summary.summarize_video(
                    vid, model=args.model, whisper_model=args.whisper_model,
                    fallback_meta={"title": title, "channel": cname})
                done.append({"video_id": vid})
                seen_ok = True
            except RuntimeError as e:
                print(f"    ⏭️ 跳過：{e}")
                skipped.append((cname, title))
                seen_ok = True  # 無字幕屬永久狀態，登記不重試
            except Exception as e:
                print(f"    ❌ 失敗（下次執行重試）：{e}")
                failed.append((cname, title))
                seen_ok = False
            if seen_ok and vid not in ch_state["seen"]:
                ch_state["seen"].append(vid)
                ch_state["seen"] = ch_state["seen"][-SEEN_CAP:]
                save_state(state)  # 每部即存，中斷不重工

    if args.dry_run:
        print(f"\n[dry-run] 將摘要 {len(done)} 部影片，不寫入任何狀態。")
        return

    save_state(state)
    real_done = [d for d in done if not d.get("dry")]
    print(f"\n═══ 結果：摘要 {len(real_done)} 部、跳過 {len(skipped)} 部、"
          f"失敗 {len(failed)} 部 ═══")
    if not real_done:
        return

    # 部署 GitHub Pages
    if not args.no_deploy:
        print("\n🚀 部署摘要庫到 GitHub Pages...")
        r = subprocess.run(["bash", str(DEPLOY_SCRIPT)],
                           capture_output=True, text=True)
        print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "")
        if r.returncode != 0:
            print(f"⚠️  部署失敗：{r.stderr.strip()[:300]}")

    # LINE 推播
    if not args.no_line:
        token, user_id = load_line_creds()
        if not token or not user_id:
            print("⚠️  未設定 LINE_TOKEN / LINE_USER_ID（或 line_secrets.json），"
                  "跳過推播。")
            return
        index = {e["video_id"]: e for e in
                 json.loads(yt_summary.INDEX_JSON.read_text(encoding="utf-8"))}
        for d in real_done:
            entry = index.get(d["video_id"])
            if not entry:
                continue
            ok = send_line(token, user_id, build_line_message(entry))
            print(f"  {'📱 LINE 推播成功' if ok else '⚠️ LINE 推播失敗'}：{entry['title']}")


if __name__ == "__main__":
    main()
