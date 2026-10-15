#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yt_channel_summary.py — 把整個 YouTube 頻道的影片批次摘要

觸發語：「摘要整個頻道」

用法：
  python3 yt_channel_summary.py "https://www.youtube.com/@ps541021/videos"
  python3 yt_channel_summary.py <頻道網址> --limit 5        # 先試跑 5 部
  python3 yt_channel_summary.py <頻道網址> --oldest-first   # 由舊到新（預設由新到舊）
  python3 yt_channel_summary.py <頻道網址> --no-deploy      # 跑完不部署

為什麼不用 yt_auto_summary.py：那支讀 RSS，**YouTube RSS 只回最新 15 筆**，
而且設計目的是「偵測新片」。要回補整個頻道得用 yt-dlp 列出完整清單。

設計重點：
  * **可續跑**：已在摘要庫（summaries.json）的影片直接跳過，中斷後重跑不重工。
  * **會重試暫時性錯誤**：claude CLI 遇到 529 Overloaded／逾時是伺服器端容量問題，
    指數退避重試；永久性錯誤（影片不存在等）記錄後跳過，不拖垮整批。
  * **只在最後部署一次**：68 部影片若每部都 deploy，等於推 68 次 git。
    中途每 --deploy-every 部存檔一次（預設 20），中斷也不會白跑。
  * 逐字稿由 yt_summary 快取，重跑同一部不會再抓一次。
"""
import argparse
import json
import os
import subprocess
import sys
import time
import warnings
import datetime as dt

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yt_summary as ys                                      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.join(HERE, "deploy_yt_summaries.sh")
STATE = os.path.join(HERE, ".yt_channel_state.json")         # 失敗紀錄（不進版控）

# ⚠ 分類原則：**預設可重試**，只有明確認定的永久性才跳過。
#   理由是兩種誤判的代價不對稱——誤判成暫時性只是多等一下，
#   誤判成永久性會把有效影片**靜默丟掉**（2026-10-14 實測一次丟掉 55 部）。
# ⚠ 絕對不要只靠「錯誤訊息含哪些字」判斷：youtube_transcript_api 的
#   HTTPError／CouldNotRetrieveTranscript **沒有 CAUSE_MESSAGE、str() 是空字串**，
#   而 YouTube 速率限制回的正是這類，比對任何關鍵字都不會中。
PERMANENT_TYPES = (
    "NoCaptionsError",      # 真的沒有字幕(含自動字幕)
    "InvalidVideoId",
    "VideoUnavailable",     # 影片不存在／私人／會員限定
    "AgeRestricted",
)
PERMANENT_HINTS = ("影片不存在", "無法存取", "私人", "會員限定", "age")

# ⚠ 第三類：**全域封鎖**,不是某一部影片的問題。
#   YouTube 的字幕 API 對連續請求會回 IpBlocked——實測約 10 部就觸發,休息幾分鐘後自動解封。
#   這類**絕對不能 per-video 重試**:逐部退避等於持續敲一個已封鎖的端點,只會讓封鎖延長
#   (2026-10-14 實測:第二批 30 部全中,每部還各自燒掉 30+60+120+240 秒退避)。
#   正確做法是**整批冷卻**再回頭重試同一部,讓一次執行能慢慢跑完。
#   [2026-10-15 修] 只列 IpBlocked／RequestBlocked **不夠**:限流也會以
#   YouTubeRequestFailed／PoTokenRequired／YouTubeDataUnparsable 等子類出現,
#   以前這些掉進 per-video 退避(30+60+120+240＝450 秒/部),30 部正好燒掉約 3.75 小時
#   ——這就是 2026-10-14 那次「跑 4 小時零進度」的真相。
#   現在由 yt_summary 統一轉成 TranscriptBlocked,並以 isinstance 兜底(見 classify)。
BLOCK_TYPES = ("IpBlocked", "RequestBlocked", "TranscriptBlocked",
               "YouTubeRequestFailed", "PoTokenRequired", "YouTubeDataUnparsable")


def list_channel(url):
    """用 yt-dlp 取頻道完整影片清單（flat，不下載）。回傳 [(id, title), ...] 由新到舊。"""
    import yt_dlp
    opts = {"extract_flat": True, "quiet": True, "no_warnings": True,
            # web client 常被要求 PO Token，android client 不需要
            "extractor_args": {"youtube": {"player_client": ["android"]}}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    name = info.get("channel") or info.get("title") or "（未知頻道）"
    vids = [(e["id"], e.get("title") or e["id"])
            for e in (info.get("entries") or []) if e.get("id")]
    return name, vids


def already_done():
    if not ys.INDEX_JSON.exists():
        return set()
    try:
        return {e["video_id"] for e in json.loads(ys.INDEX_JSON.read_text(encoding="utf-8"))}
    except json.JSONDecodeError:
        return set()


def load_state():
    if os.path.isfile(STATE):
        try:
            with open(STATE, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return {"failed": {}}


def save_state(st):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def classify(exc):
    """回傳 (是否可重試, 是否全域封鎖, 給人看的描述)。預設可重試。"""
    name = type(exc).__name__
    msg = str(exc).strip()
    shown = msg or f"（{name} 未帶訊息）"      # 空訊息也要留下型別，否則無從診斷
    if name in BLOCK_TYPES:
        return True, True, f"{name}：字幕端點暫時取不到（限流／封鎖），整批冷卻"
    # ⚠ 型別名單兜底:凡是 CouldNotRetrieveTranscript 家族的「其他」子類,
    #   一律當**全域封鎖**而非 per-video 重試。
    #   理由:真正的「沒有字幕」在 yt_summary.fetch_transcript 已被轉成
    #   NoCaptionsError,所以還能走到這裡的這個家族,必然是傳輸層問題。
    #   日後 youtube_transcript_api 新增子類也不必回頭改名單。
    try:
        from youtube_transcript_api._errors import CouldNotRetrieveTranscript
        if isinstance(exc, CouldNotRetrieveTranscript):
            return True, True, f"{name}：字幕端點暫時取不到（限流／封鎖），整批冷卻"
    except ImportError:
        pass
    if name in PERMANENT_TYPES or any(h in msg.lower() for h in PERMANENT_HINTS):
        return False, False, shown
    return True, False, shown


def quota_exhausted(exc):
    """claude 訂閱用量用罄？回傳 (是否用罄, 重置時間字串)。

    ⚠ 這類**不可**當 per-video 可重試:額度要到**固定時間**才回來,
      每部白等 30+60+120+240＝450 秒毫無意義。
      2026-10-15 實測:批次做完 13 部後撞上用量上限,接下來每部都重試到用盡
      才放棄(日誌 24 行 session limit),應該一撞到就整批收工。
    ⚠ 也不是「全域封鎖」——冷卻幾分鐘不會讓額度回來,要等到重置時刻。
    """
    msg = str(exc)
    if "session limit" not in msg and "usage limit" not in msg:
        return False, ""
    import re as _re
    m = _re.search(r"resets?\s+([^)\n]*\))", msg) or _re.search(r"resets?\s+(\S+)", msg)
    return True, (m.group(1).strip() if m else "（訊息未帶重置時間）")


def probe_transcript(sample_id):
    """起跑前預檢：**必須呼叫 fetch()**，不能只 list()。

    ⚠ 2026-10-15 實測：封鎖期間 list() 照樣成功、fetch() 才被 IpBlocked 擋。
      只用 list() 探測會得到**假綠燈**，批次一啟動就在第 1 部撞牆
      （連續四批都是這樣死的，誤以為是節流不夠而一路把 pause 從 3s 調到 25s，
      其實那兩個值從未在未封鎖狀態下跑過）。
    ⚠ 探測本身會吃配額，所以只打一部、不重複打。
    """
    from youtube_transcript_api import YouTubeTranscriptApi
    try:
        tl = list(YouTubeTranscriptApi().list(sample_id))
        if not tl:
            return True, "（該樣本無字幕，無法判斷；視為可試跑）"
        tl.sort(key=lambda t: t.is_generated)
        tl[0].fetch()
        return True, "字幕端點可用"
    except Exception as e:                                   # noqa: BLE001
        return False, f"{type(e).__name__}"


def run_deploy():
    r = subprocess.run(["bash", DEPLOY], capture_output=True, text=True)
    tail = [l for l in (r.stdout or "").strip().splitlines() if l.strip()]
    print("   " + (tail[-1] if tail else "(無輸出)"))
    if r.returncode != 0:
        print(f"   ⚠ 部署失敗：{(r.stderr or '').strip()[:200]}")
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser(description="整個頻道批次摘要")
    ap.add_argument("channel", help="頻道網址，例 https://www.youtube.com/@xxx/videos")
    ap.add_argument("--limit", type=int, help="只處理前 N 部（試跑用）")
    ap.add_argument("--oldest-first", action="store_true", help="由舊到新處理")
    ap.add_argument("--model", default="sonnet", help="claude CLI 模型別名")
    ap.add_argument("--retries", type=int, default=4, help="暫時性錯誤的重試次數")
    ap.add_argument("--pause", type=float, default=45.0,
                help="每部之間的間隔秒數（預設 45，分多天跑的配套）")
    ap.add_argument("--deploy-every", type=int, default=20, help="每 N 部部署一次（0＝只在最後）")
    ap.add_argument("--no-deploy", action="store_true", help="完全不部署")
    ap.add_argument("--block-cooldown", type=int, default=120,
                    help="遇到 IpBlocked 時整批冷卻幾秒（預設 600＝10 分鐘）")
    # 這是「**連續**」冷卻次數(成功一部就歸零),所以個位數就夠。
    # 配上遞增階梯,連續 4 次＝120+240+480+960≈30 分鐘;
    # 上限開太大(曾經是 8 配固定 10 分鐘＝80 分鐘)只是在真的被硬擋時白等更久。
    ap.add_argument("--max-cooldowns", type=int, default=4,
                    help="全域封鎖冷卻次數上限，超過就中止整批")
    ap.add_argument("--daily-limit", type=int, default=12,
                    help="單輪最多完成幾部就收工（預設 12）。"
                         "分多天跑用：連續抓太多部必定被 IpBlocked，"
                         "實測 pause=3s 時約 10~11 部觸發")
    ap.add_argument("--skip-probe", action="store_true",
                    help="跳過起跑前的 fetch() 預檢（不建議）")
    ap.add_argument("--retry-failed", action="store_true",
                    help="連同前次記錄為失敗的影片一起重試")
    args = ap.parse_args()

    print(f"📡 取得頻道影片清單…（{args.channel}）")
    name, vids = list_channel(args.channel)
    if args.oldest_first:
        vids = list(reversed(vids))

    st = load_state()
    done = already_done()
    failed_before = set(st["failed"]) if not args.retry_failed else set()

    todo = [(v, t) for v, t in vids if v not in done and v not in failed_before]
    if args.limit:
        todo = todo[:args.limit]

    print(f"🎬 頻道「{name}」共 {len(vids)} 部")
    print(f"   已在摘要庫 {len([1 for v, _ in vids if v in done])} 部"
          + (f"、前次失敗跳過 {len([1 for v, _ in vids if v in failed_before])} 部"
             if failed_before else ""))
    print(f"   本次要處理 {len(todo)} 部\n")
    if not todo:
        print("沒有待處理的影片。")
        return

    if not args.skip_probe:
        ok_probe, why = probe_transcript(todo[0][0])
        if not ok_probe:
            print(f"🛑 起跑前預檢未通過：{why}")
            print("   字幕端點目前仍被封鎖，**今天這輪不啟動**（避免再觸發、延長封鎖）。")
            print("   隔幾小時或明天同一道指令再跑即可，已完成的部分都保留。")
            return
        print(f"✅ 預檢通過：{why}\n")

    if args.daily_limit and len(todo) > args.daily_limit:
        print(f"📆 本輪只做前 {args.daily_limit} 部（分多天跑），"
              f"剩下的下一輪繼續。\n")
        todo = todo[:args.daily_limit]

    ok, skipped, failed = [], [], []
    cooldowns, aborted = [0], [False]      # 用 list 讓內層迴圈可改
    t0 = time.time()
    for i, (vid, title) in enumerate(todo, 1):
        el = time.time() - t0
        eta = (el / max(i - 1, 1)) * (len(todo) - i + 1) if i > 1 else 0
        print(f"[{i}/{len(todo)}] {title[:52]}"
              + (f"   （已花 {el/60:.0f} 分，預估還要 {eta/60:.0f} 分）" if i > 1 else ""))

        for attempt in range(1, args.retries + 2):
            try:
                ys.summarize_video(vid, model=args.model,
                                   fallback_meta={"title": title, "channel": name})
                ok.append(vid)
                cooldowns[0] = 0                 # 成功了就把「連續冷卻」歸零
                break
            except Exception as e:                      # noqa: BLE001
                # claude 額度用罄 → **整批收工**,不做任何重試或冷卻。
                spent, resets = quota_exhausted(e)
                if spent:
                    print(f"    🛑 claude 訂閱用量已用罄（重置：{resets}）。"
                          f"\n       本輪就此收工——額度要到重置時刻才回來，"
                          f"重試或冷卻都沒用。\n       重置後同一道指令再跑即可。")
                    aborted[0] = True
                    break
                retryable, blocked, shown = classify(e)
                if blocked:
                    cooldowns[0] += 1
                    # ⚠ 這個計數是「**連續**冷卻次數」,每成功一部就歸零(見下方 ok.append)。
                    #   若用累計值,一次長跑裡零星撞上幾次封鎖就會提早中止;
                    #   若不設上限,從第一部就被封鎖時會把所有冷卻燒在同一部上。
                    if cooldowns[0] > args.max_cooldowns:
                        print(f"    🛑 連續冷卻 {args.max_cooldowns} 次仍被封鎖，中止整批"
                              f"（已完成 {len(ok)} 部，不會白費）。"
                              f"\n       YouTube 的封鎖比預期久，建議隔幾小時或隔天加"
                              f" --retry-failed 續跑。")
                        aborted[0] = True
                        break
                    # 冷卻時間**遞增**(首次 block_cooldown,之後倍增、上限 8 倍)。
                    # 2026-10-15 實測:這是**速率**限制而非長時封鎖——單次請求馬上就通,
                    # 連續請求才被擋。固定 10 分鐘太重(每次觸發就白等);
                    # 從 2 分鐘開始遞增,踩一次只付小代價,真的被硬擋才慢慢拉長。
                    cd = min(args.block_cooldown * (2 ** (cooldowns[0] - 1)),
                             args.block_cooldown * 8)
                    print(f"    🧊 {shown}\n       整批冷卻 {cd//60} 分 {cd%60} 秒後重試同一部"
                          f"（第 {cooldowns[0]}/{args.max_cooldowns} 次冷卻）")
                    time.sleep(cd)
                    continue                     # 不計入 per-video 重試次數
                if retryable and attempt <= args.retries:
                    wait = min(30 * (2 ** (attempt - 1)), 480)
                    print(f"    ⏳ 可重試（第 {attempt} 次），等 {wait}s："
                          f"{shown[:90]}")
                    time.sleep(wait)
                    continue
                kind = "重試用盡" if retryable else "永久性"
                print(f"    ❌ 放棄（{kind}）：{shown[:140]}")
                st["failed"][vid] = {
                    "title": title, "type": type(e).__name__, "error": shown[:300],
                    "at": dt.datetime.now().isoformat(timespec="seconds")}
                save_state(st)
                (failed if retryable else skipped).append(vid)
                break

        if aborted[0]:
            break

        if args.deploy_every and not args.no_deploy and len(ok) and len(ok) % args.deploy_every == 0:
            print(f"   🚀 中途部署（已完成 {len(ok)} 部）…")
            run_deploy()

        if i < len(todo):
            time.sleep(args.pause)

    mins = (time.time() - t0) / 60
    print(f"\n{'='*60}")
    print(f"  完成 {len(ok)}　永久性跳過 {len(skipped)}　重試用盡 {len(failed)}"
          f"　耗時 {mins:.0f} 分")
    print(f"{'='*60}")
    if st["failed"]:
        print(f"\n失敗紀錄（{len(st['failed'])} 筆）存於 {os.path.basename(STATE)}，"
              f"修好問題後加 --retry-failed 重跑：")
        for v, d in list(st["failed"].items())[:10]:
            print(f"  {v}  {d['title'][:40]}  ← {d['error'][:70]}")

    if ok and not args.no_deploy:
        print("\n🚀 最後部署…")
        run_deploy()

    left = len(vids) - len(already_done() & {v for v, _ in vids})
    if left > 0:
        print(f"\n📆 此頻道還剩 {left} 部未摘要。下一輪（建議隔一天）直接重跑同一道指令：")
        print(f"   python3 yt_channel_summary.py \"{args.channel}\" --retry-failed")
    else:
        print(f"\n🎉 此頻道 {len(vids)} 部已全部摘要完成。")


if __name__ == "__main__":
    main()
