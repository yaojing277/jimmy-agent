#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
yt_summary.py — YouTube 影片 AI 重點摘要工具

用法：
    python3 yt_summary.py "https://www.youtube.com/watch?v=XXXX"
    python3 yt_summary.py "https://youtu.be/XXXX" --no-open
    python3 yt_summary.py <網址> --model haiku
    python3 yt_summary.py <網址> --whisper-model medium   # 無字幕影片，轉錄更準但更慢

流程：
    解析網址 → oEmbed 抓標題/頻道/縮圖 → youtube-transcript-api 抓字幕
    → （無字幕則 fallback：yt-dlp 抓音軌 → faster-whisper 本地轉錄）
    → claude -p 產生 JSON 摘要 → 輸出 HTML 摘要頁 + 更新摘要庫 index.html

完全無字幕的影片會自動 fallback 用 Whisper 語音轉文字（較慢，長影片可能要幾分鐘）；
若 yt-dlp/whisper 也失敗（例如私人影片抓不到音軌）才會提示後結束（exit code 1）。
摘要核心 summarize_video() 可供其他腳本 import（例如日後接 youtube_notify.py）。
"""

import argparse
import glob
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import requests

# Whisper fallback 模型大小（tiny/base/small/medium/large-v3）；越大越準但越慢
WHISPER_MODEL_SIZE = "small"

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "yt_summaries"
# 字幕／轉錄快取（放在摘要庫外，避免被 deploy 一起推上 GitHub）
TRANSCRIPT_CACHE_DIR = BASE_DIR / ".yt_transcript_cache"
INDEX_JSON = OUT_DIR / "summaries.json"

# 字幕語言優先序（人工字幕優先於自動字幕）
LANG_PRIORITY = ["zh-TW", "zh-Hant", "zh", "zh-Hans", "en", "ja"]

# 字幕全文超過此長度就截斷（約可容納 2~3 小時影片）
MAX_TRANSCRIPT_CHARS = 150_000

# claude CLI 摘要逾時（秒）
CLAUDE_TIMEOUT = 600


# ---------------------------------------------------------------- 網址解析

def parse_video_id(url: str) -> str:
    """支援 watch?v=、youtu.be/、/shorts/、/live/、/embed/ 等格式。"""
    patterns = [
        r"(?:v=|/shorts/|/live/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    # 使用者直接貼 11 碼 video id
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url.strip()):
        return url.strip()
    raise ValueError(f"無法從網址解析出 video id：{url}")


# ---------------------------------------------------------------- 中繼資料

def fetch_metadata(video_id: str) -> dict:
    """oEmbed 免金鑰取得標題/頻道/縮圖；失敗不致命。"""
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    meta = {
        "video_id": video_id,
        "url": watch_url,
        "title": video_id,
        "channel": "",
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    }
    try:
        res = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": watch_url, "format": "json"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        res.raise_for_status()
        data = res.json()
        meta["title"] = data.get("title") or video_id
        meta["channel"] = data.get("author_name") or ""
        meta["thumbnail"] = data.get("thumbnail_url") or meta["thumbnail"]
    except Exception as e:
        print(f"⚠️  oEmbed 中繼資料抓取失敗（不影響摘要）：{e}")
    return meta


# ---------------------------------------------------------------- 字幕

def _lang_rank(code: str) -> int:
    code_l = (code or "").lower()
    for i, want in enumerate(LANG_PRIORITY):
        if code_l == want.lower():
            return i
    for i, want in enumerate(LANG_PRIORITY):
        if code_l.startswith(want.lower().split("-")[0]):
            return len(LANG_PRIORITY) + i
    return 99


class NoCaptionsError(RuntimeError):
    """影片完全沒有字幕（含自動字幕）；呼叫端可用此觸發 Whisper fallback。"""


def fetch_transcript(video_id: str):
    """回傳 (snippets, 語言碼, 是否自動字幕)；snippets = [{text, start}, ...]。

    完全沒字幕時丟出 NoCaptionsError（可 fallback 至 Whisper）。
    影片不存在等其他問題丟出 RuntimeError（不可 fallback）。
    """
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        TranscriptsDisabled,
        NoTranscriptFound,
        VideoUnavailable,
    )

    api = YouTubeTranscriptApi()
    try:
        tlist = list(api.list(video_id))
    except (TranscriptsDisabled, NoTranscriptFound) as e:
        raise NoCaptionsError("此影片無字幕（含自動字幕）。") from e
    except VideoUnavailable as e:
        raise RuntimeError("影片不存在或無法存取（可能為私人/會員限定影片）。") from e

    if not tlist:
        raise NoCaptionsError("此影片無字幕（含自動字幕）。")

    # 人工字幕優先，再依語言優先序
    tlist.sort(key=lambda t: (t.is_generated, _lang_rank(t.language_code)))
    chosen = tlist[0]

    fetched = chosen.fetch()
    snippets = [{"text": s.text, "start": s.start} for s in fetched]
    return snippets, chosen.language_code, chosen.is_generated


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def parse_ts(ts: str) -> int:
    """'MM:SS' 或 'H:MM:SS' → 秒數；解析失敗回 -1。"""
    try:
        parts = [int(p) for p in ts.strip().split(":")]
    except (ValueError, AttributeError):
        return -1
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return -1


def build_transcript_text(snippets) -> str:
    """把逐句字幕合併成帶 [時間標記] 的段落（每段約 300 字），省 token 又保留章節線索。"""
    blocks, buf, buf_start = [], [], None
    for sn in snippets:
        text = (sn["text"] or "").replace("\n", " ").strip()
        if not text:
            continue
        if buf_start is None:
            buf_start = sn["start"]
        buf.append(text)
        if sum(len(t) for t in buf) >= 300:
            blocks.append(f"[{fmt_ts(buf_start)}] " + " ".join(buf))
            buf, buf_start = [], None
    if buf:
        blocks.append(f"[{fmt_ts(buf_start)}] " + " ".join(buf))
    return "\n".join(blocks)


# ---------------------------------------------------------------- 字幕快取

def load_cached_transcript(video_id: str):
    """回傳 (snippets, lang, auto, source) 或 None。"""
    f = TRANSCRIPT_CACHE_DIR / f"{video_id}.json"
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            return d["snippets"], d["lang"], d["auto"], d.get("source", "caption")
        except (json.JSONDecodeError, KeyError):
            print("⚠️  字幕快取損毀，重新抓取。")
    return None


def save_cached_transcript(video_id: str, snippets, lang: str, auto: bool, source: str):
    """存下字幕／Whisper 轉錄結果，之後重跑（例如摘要失敗重試）免再跑一次轉錄。"""
    TRANSCRIPT_CACHE_DIR.mkdir(exist_ok=True)
    (TRANSCRIPT_CACHE_DIR / f"{video_id}.json").write_text(
        json.dumps({"snippets": snippets, "lang": lang, "auto": auto, "source": source},
                   ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------- Whisper fallback（無字幕影片）

def transcribe_with_whisper(video_id: str, model_size: str = WHISPER_MODEL_SIZE):
    """下載音軌並用本地 Whisper 轉錄，回傳格式與 fetch_transcript 相同：
    (snippets, 語言碼, 是否自動字幕=True)。失敗丟出 RuntimeError。
    """
    import yt_dlp

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.m4a")
        print("📥 無字幕，改用 yt-dlp 下載音軌...")
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": audio_path,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            # web client 目前常被 YouTube 要求 PO Token 而失敗，android client 不需要
            "extractor_args": {"youtube": {"player_client": ["android"]}},
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        except Exception as e:
            raise RuntimeError(f"音軌下載失敗，無法轉錄：{e}") from e

        if not os.path.exists(audio_path):
            # yt-dlp 依來源格式可能加副檔名，找實際產出檔
            matches = glob.glob(os.path.join(tmpdir, "audio.*"))
            if not matches:
                raise RuntimeError("音軌下載後找不到檔案，無法轉錄。")
            audio_path = matches[0]

        print(f"🎙️  Whisper 語音轉錄中（model={model_size}，影片較長時可能要幾分鐘）...")
        from faster_whisper import WhisperModel

        try:
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            segments, info = model.transcribe(audio_path, vad_filter=True)
            snippets = [
                {"text": seg.text.strip(), "start": seg.start}
                for seg in segments if seg.text and seg.text.strip()
            ]
        except Exception as e:
            raise RuntimeError(f"Whisper 轉錄失敗：{e}") from e

        if not snippets:
            raise RuntimeError("Whisper 轉錄結果為空，無法摘要。")

        lang = info.language or "unknown"
        return snippets, lang, True


# ---------------------------------------------------------------- Claude CLI

def find_claude_cli() -> str:
    from shutil import which
    path = which("claude")
    if path:
        return path
    # 桌面版 App 內建 CLI（路徑含版本號，取最新版）
    candidates = sorted(glob.glob(
        os.path.expanduser(
            "~/Library/Application Support/Claude/claude-code/*/claude.app/Contents/MacOS/claude"
        )
    ))
    if candidates:
        return candidates[-1]
    raise RuntimeError(
        "找不到 claude CLI。請先安裝 Claude Code（桌面版或 npm 版），"
        "或將 claude 加入 PATH。"
    )


SUMMARY_PROMPT = """你是專業的影片內容分析師。stdin 提供一部 YouTube 影片的字幕全文，每段前有 [時:分:秒] 時間標記。

影片標題：{title}
頻道：{channel}

請以「正體中文（台灣用語）」撰寫摘要。只輸出一個 JSON 物件，不要任何其他文字、不要 markdown code fence。結構如下：

{{
  "one_line": "一句話總結，50 字以內",
  "key_points": ["重點 1", "重點 2", "..."],
  "chapters": [
    {{"time": "MM:SS 或 H:MM:SS（必須取自字幕中實際出現的時間標記）", "title": "章節標題", "note": "該段落一句話說明"}}
  ],
  "quotes": ["值得記下的金句、關鍵數據或具體結論（可附時間）"],
  "audience": "適合誰看，一句話"
}}

要求：
- key_points 5~10 條，每條具體、可獨立閱讀，避免空泛。
- chapters 依影片實際內容切 3~8 段，時間必須對應字幕時間標記。
- quotes 0~5 條，沒有就給空陣列。
- 字幕若為自動產生可能有錯字，請依上下文合理修正後再摘要。"""


def run_claude_summary(transcript_text: str, meta: dict, model: str) -> str:
    cli = find_claude_cli()
    prompt = SUMMARY_PROMPT.format(
        title=meta["title"], channel=meta["channel"] or "（未知）"
    )
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)  # 允許在 Claude Code session 內巢狀呼叫
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)

    cmd = [cli, "-p", prompt, "--output-format", "text"]
    if model:
        cmd += ["--model", model]

    print(f"🤖 呼叫 claude CLI 產生摘要（model={model or '預設'}，最長等 {CLAUDE_TIMEOUT}s）...")
    res = subprocess.run(
        cmd, input=transcript_text, capture_output=True, text=True,
        timeout=CLAUDE_TIMEOUT, env=env,
    )
    if res.returncode != 0:
        detail = (res.stderr.strip() or res.stdout.strip())[:500]
        if ("Not logged in" in detail or "/login" in detail
                or "OAuth" in detail or "authenticate" in detail.lower()):
            raise RuntimeError(
                "claude CLI 未登入或登入已過期。請在終端機執行一次以下指令重新登入"
                "（瀏覽器 OAuth）：\n"
                f'   "{cli}"\n'
                "   進入後輸入 /login，登入完成後即可重跑本腳本。\n"
                f"   （原始訊息：{detail}）"
            )
        raise RuntimeError(f"claude CLI 執行失敗：{detail}")
    return res.stdout.strip()


def parse_summary_json(raw: str) -> dict:
    """容錯解析：剝 code fence、抓第一個 { 到最後一個 }；失敗回 fallback 純文字。"""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                data.setdefault("one_line", "")
                data.setdefault("key_points", [])
                data.setdefault("chapters", [])
                data.setdefault("quotes", [])
                data.setdefault("audience", "")
                return data
        except json.JSONDecodeError:
            pass
    print("⚠️  AI 回傳非合法 JSON，改以純文字呈現。")
    return {
        "one_line": "", "key_points": [], "chapters": [], "quotes": [],
        "audience": "", "raw_text": raw,
    }


# ---------------------------------------------------------------- HTML 輸出

PAGE_CSS = """
:root { --bg:#0f1420; --card:#1a2232; --card2:#141b29; --fg:#e8ecf4;
        --muted:#8b96ab; --accent:#4da3ff; --accent2:#ffd166; --line:#2a3550; }
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--fg); font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;
       line-height:1.7; padding:24px 16px 60px; }
.wrap { max-width:860px; margin:0 auto; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px;
        padding:20px 22px; margin-bottom:18px; }
h1 { font-size:1.35rem; line-height:1.45; margin-bottom:6px; }
h2 { font-size:1.05rem; color:var(--accent2); margin-bottom:12px;
     padding-bottom:8px; border-bottom:1px solid var(--line); }
.meta { color:var(--muted); font-size:.88rem; }
.thumb { width:100%; border-radius:10px; display:block; margin-bottom:14px; }
.oneline { font-size:1.05rem; background:var(--card2); border-left:4px solid var(--accent);
           padding:12px 16px; border-radius:8px; }
ul.points li { margin:0 0 10px 1.2em; }
.chapter { display:flex; gap:12px; padding:10px 0; border-bottom:1px dashed var(--line); }
.chapter:last-child { border-bottom:none; }
.chapter .t { flex:0 0 64px; font-variant-numeric:tabular-nums; }
.chapter .body b { display:block; }
.chapter .body span { color:var(--muted); font-size:.92rem; }
.quote { background:var(--card2); border-radius:8px; padding:10px 14px; margin-bottom:10px;
         border-left:3px solid var(--accent2); font-size:.95rem; }
.badge { display:inline-block; background:var(--card2); border:1px solid var(--line);
         border-radius:999px; padding:2px 10px; font-size:.78rem; color:var(--muted); margin-right:6px; }
.footer { text-align:center; color:var(--muted); font-size:.8rem; margin-top:30px; }
pre.raw { white-space:pre-wrap; font-family:inherit; }
/* index */
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:16px; }
.item { background:var(--card); border:1px solid var(--line); border-radius:12px;
        overflow:hidden; transition:transform .12s; }
.item:hover { transform:translateY(-3px); }
.item img { width:100%; aspect-ratio:16/9; object-fit:cover; display:block; }
.item .pad { padding:12px 14px; }
.item .pad b { display:block; font-size:.95rem; line-height:1.45; margin-bottom:6px; }
.item .pad p { color:var(--muted); font-size:.85rem;
               display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
"""


def esc(s) -> str:
    return html.escape(str(s or ""))


def render_summary_html(meta: dict, summary: dict, info: dict) -> str:
    vid = meta["video_id"]

    points = "".join(f"<li>{esc(p)}</li>" for p in summary.get("key_points", []))

    chapters = []
    for ch in summary.get("chapters", []):
        ts = str(ch.get("time", ""))
        sec = parse_ts(ts)
        t_html = (
            f'<a href="https://www.youtube.com/watch?v={vid}&t={sec}s" target="_blank">{esc(ts)}</a>'
            if sec >= 0 else esc(ts)
        )
        chapters.append(
            f'<div class="chapter"><div class="t">{t_html}</div>'
            f'<div class="body"><b>{esc(ch.get("title"))}</b>'
            f'<span>{esc(ch.get("note"))}</span></div></div>'
        )

    quotes = "".join(f'<div class="quote">{esc(q)}</div>' for q in summary.get("quotes", []))

    sections = []
    if summary.get("one_line"):
        sections.append(f'<div class="card"><div class="oneline">💡 {esc(summary["one_line"])}</div></div>')
    if points:
        sections.append(f'<div class="card"><h2>📌 重點整理</h2><ul class="points">{points}</ul></div>')
    if chapters:
        sections.append(f'<div class="card"><h2>🕐 章節導覽（點時間跳轉）</h2>{"".join(chapters)}</div>')
    if quotes:
        sections.append(f'<div class="card"><h2>✨ 金句與關鍵數據</h2>{quotes}</div>')
    if summary.get("audience"):
        sections.append(f'<div class="card"><h2>👥 適合誰看</h2><p>{esc(summary["audience"])}</p></div>')
    if summary.get("raw_text"):
        sections.append(f'<div class="card"><h2>📝 AI 摘要（原始輸出）</h2><pre class="raw">{esc(summary["raw_text"])}</pre></div>')

    if info.get("source") == "whisper":
        source_badge = "🎙️ Whisper 語音轉錄"
    else:
        source_badge = "自動字幕" if info["auto"] else "人工字幕"
    badges = (
        f'<span class="badge">字幕語言：{esc(info["lang"])}</span>'
        f'<span class="badge">{source_badge}</span>'
        f'<span class="badge">摘要日期：{esc(info["date"])}</span>'
    )
    if info.get("truncated"):
        badges += '<span class="badge">⚠️ 字幕過長已截斷</span>'

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(meta["title"])}｜影片摘要</title>
<style>{PAGE_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <a href="{esc(meta["url"])}" target="_blank"><img class="thumb" src="{esc(meta["thumbnail"])}" alt="縮圖"></a>
    <h1><a href="{esc(meta["url"])}" target="_blank">{esc(meta["title"])}</a></h1>
    <div class="meta">{esc(meta["channel"])}</div>
    <div style="margin-top:10px">{badges}</div>
  </div>
  {"".join(sections)}
  <div class="footer"><a href="index.html">← 影片摘要庫</a>　·　yt_summary.py 產生</div>
</div>
</body>
</html>"""


def render_index_html(entries: list) -> str:
    items = []
    for e in sorted(entries, key=lambda x: x.get("date", ""), reverse=True):
        items.append(
            f'<a class="item" href="{esc(e["file"])}">'
            f'<img src="{esc(e["thumbnail"])}" alt="" loading="lazy">'
            f'<div class="pad"><b>{esc(e["title"])}</b>'
            f'<p>{esc(e.get("one_line") or e.get("channel"))}</p>'
            f'<div class="meta">{esc(e.get("channel"))}　{esc(e.get("date"))}</div>'
            f'</div></a>'
        )
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>影片摘要庫</title>
<style>{PAGE_CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="card"><h1>🎬 影片摘要庫</h1>
  <div class="meta">共 {len(entries)} 部影片　·　yt_summary.py 產生</div></div>
  <div class="grid">{"".join(items)}</div>
</div>
</body>
</html>"""


def update_index(entry: dict):
    entries = []
    if INDEX_JSON.exists():
        try:
            entries = json.loads(INDEX_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("⚠️  summaries.json 損毀，重建索引。")
    entries = [e for e in entries if e.get("video_id") != entry["video_id"]]
    entries.append(entry)
    INDEX_JSON.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "index.html").write_text(render_index_html(entries), encoding="utf-8")
    return len(entries)


# ---------------------------------------------------------------- 主流程

def summarize_video(url: str, model: str = "sonnet", whisper_model: str = WHISPER_MODEL_SIZE,
                    fallback_meta: dict = None) -> Path:
    """核心流程，回傳產出的 HTML 路徑。可供其他腳本 import。

    fallback_meta：oEmbed 失敗時的備援 {"title": ..., "channel": ...}
    （yt_auto_summary.py 會把 RSS 已知的標題/頻道傳進來）。
    """
    video_id = parse_video_id(url)
    print(f"🎯 video id：{video_id}")

    meta = fetch_metadata(video_id)
    if fallback_meta:
        if meta["title"] == video_id and fallback_meta.get("title"):
            meta["title"] = fallback_meta["title"]
        if not meta["channel"] and fallback_meta.get("channel"):
            meta["channel"] = fallback_meta["channel"]
    print(f"🎬 {meta['title']}（{meta['channel'] or '未知頻道'}）")

    cached = load_cached_transcript(video_id)
    if cached:
        snippets, lang, auto, source = cached
        print(f"📥 使用快取字幕（{source}，{len(snippets):,} 段）")
    else:
        print("📥 抓取字幕中...")
        source = "caption"
        try:
            snippets, lang, auto = fetch_transcript(video_id)
        except NoCaptionsError:
            snippets, lang, auto = transcribe_with_whisper(video_id, model_size=whisper_model)
            source = "whisper"
        save_cached_transcript(video_id, snippets, lang, auto, source)

    transcript_text = build_transcript_text(snippets)
    truncated = len(transcript_text) > MAX_TRANSCRIPT_CHARS
    if truncated:
        transcript_text = transcript_text[:MAX_TRANSCRIPT_CHARS]
        print(f"⚠️  字幕超過 {MAX_TRANSCRIPT_CHARS} 字元，已截斷。")
    if source == "whisper":
        print(f"✅ Whisper 轉錄完成：{lang}，{len(transcript_text):,} 字元")
    else:
        print(f"✅ 字幕 OK：{lang}（{'自動' if auto else '人工'}字幕），{len(transcript_text):,} 字元")

    raw = run_claude_summary(transcript_text, meta, model)
    summary = parse_summary_json(raw)
    n_pts = len(summary.get("key_points", []))
    print(f"✅ 摘要完成：{n_pts} 條重點、{len(summary.get('chapters', []))} 個章節")

    OUT_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    info = {"lang": lang, "auto": auto, "source": source,
            "date": datetime.now().strftime("%Y-%m-%d"), "truncated": truncated}
    out_file = OUT_DIR / f"{today}_{video_id}.html"

    # 同影片重跑：移除舊日期檔，避免摘要庫連到失效檔案
    for old in OUT_DIR.glob(f"*_{video_id}.html"):
        if old != out_file:
            old.unlink()

    out_file.write_text(render_summary_html(meta, summary, info), encoding="utf-8")

    total = update_index({
        "video_id": video_id,
        "file": out_file.name,
        "title": meta["title"],
        "channel": meta["channel"],
        "thumbnail": meta["thumbnail"],
        "one_line": summary.get("one_line", ""),
        "key_points": summary.get("key_points", [])[:5],
        "date": info["date"],
    })
    print(f"📄 已輸出：{out_file}")
    print(f"📚 摘要庫共 {total} 部影片：{OUT_DIR / 'index.html'}")
    return out_file


def main():
    ap = argparse.ArgumentParser(description="YouTube 影片 AI 重點摘要工具")
    ap.add_argument("url", help="YouTube 影片網址（或 11 碼 video id）")
    ap.add_argument("--model", default="sonnet",
                    help="claude CLI 模型別名（sonnet/haiku/opus，預設 sonnet）")
    ap.add_argument("--whisper-model", default=WHISPER_MODEL_SIZE,
                    help=f"無字幕時 fallback 用的 Whisper 模型大小"
                         f"（tiny/base/small/medium/large-v3，預設 {WHISPER_MODEL_SIZE}）")
    ap.add_argument("--no-open", action="store_true", help="產出後不自動開啟瀏覽器")
    args = ap.parse_args()

    try:
        out_file = summarize_video(args.url, model=args.model, whisper_model=args.whisper_model)
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(f"❌ claude CLI 逾時（>{CLAUDE_TIMEOUT}s），請重試或改用 --model haiku。")
        sys.exit(1)

    if not args.no_open:
        subprocess.run(["open", str(out_file)])


if __name__ == "__main__":
    main()
