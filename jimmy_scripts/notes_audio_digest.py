#!/usr/bin/env python3
"""
備忘錄錄音 → 逐字稿＋摘要 批次整理

把「備忘錄」(Notes.app／iCloud) 裡「內文只有錄音」的備忘錄，補上 AI 擬的標題、摘要與逐字稿
（放在錄音前面），並集中搬到同一個資料夾。

分階段執行，每階段結果都落地在 .notes_audio_cache/，可中斷續跑：

    python3 notes_audio_digest.py scan                 # 1 列出符合條件的備忘錄 → manifest.json
    python3 notes_audio_digest.py export               # 2 匯出錄音（同時就是備份）＋量長度
    python3 notes_audio_digest.py transcribe           # 3 Whisper 本機轉錄（mlx GPU 優先）
    python3 notes_audio_digest.py summarize            # 4 claude CLI 擬標題／摘要／待辦
    python3 notes_audio_digest.py preview              # 5 產本機 HTML 對照表供過目
    python3 notes_audio_digest.py apply --yes          # 6 寫回備忘錄（⚠ 會改備忘錄；無語音的直接刪除）
    python3 notes_audio_digest.py move --yes           # 7 搬到「錄音整理」資料夾（⚠ 會改備忘錄）
    python3 notes_audio_digest.py status               # 各階段進度

共通參數：--limit N（只處理前 N 則）、--only p1670,p1669（指定備忘錄）。

注意：
- 錄音一律直接從備忘錄資料庫（~/Library/Group Containers/group.com.apple.notes）取完整檔：
  AppleScript `save attachment` 與介面「儲存附件⋯」拿到的常只是片段（2026-10-13 實測 1:06:54 只拿到 56 秒）。
  該資料夾受 macOS TCC 保護，執行 export 的程式需要「完全取用磁碟」權限。
- 只有錄音的備忘錄 body 是 `<div><br><br></div>`，HTML 內**不含附件**——用 AppleScript `set body`
  寫回會把錄音洗掉（2026-10-13 實測）。apply 改走輔助使用 API，只把文字插到錄音前面；
  寫完驗證附件數與標題，不符就立刻停下。執行 apply 時備忘錄視窗會逐則切換，期間勿操作備忘錄。
- 轉錄完全在本機；摘要會把逐字稿送給 claude CLI（雲端）。
- .notes_audio_cache/ 內是私人錄音與逐字稿，不進版控。
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from yt_summary import find_claude_cli, fmt_ts

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / ".notes_audio_cache"
AUDIO_DIR = CACHE_DIR / "audio"
TRANSCRIPT_DIR = CACHE_DIR / "transcript"
SUMMARY_DIR = CACHE_DIR / "summary"
MANIFEST = CACHE_DIR / "manifest.json"
STATE = CACHE_DIR / "state.json"
PREVIEW_HTML = CACHE_DIR / "preview.html"

TARGET_FOLDER = "錄音整理"
AUDIO_EXT = (".m4a", ".mp3", ".wav", ".aac", ".caf")
# 內文（不含附件）長度不超過這個字數就視為「只有錄音」
MAX_BODY_CHARS = 20

WHISPER_MODEL_SIZE = "medium"
WHISPER_PROMPT = "以下是正體中文（台灣用語）的會議與談話錄音逐字稿。"
# 逐字稿分段：累積超過這個秒數、或遇到這麼長的停頓就換段
PARA_SECONDS = 60
PARA_GAP = 2.5

CLAUDE_TIMEOUT = 600
# 逐字稿超過此長度，送摘要時截斷（寫回備忘錄的逐字稿仍是完整的）
MAX_SUMMARY_CHARS = 120_000

FS, RS = "\x1f", "\x1e"  # AppleScript 輸出用的欄位／紀錄分隔字元


# ---------------------------------------------------------------- 基礎工具

def osa(script: str, timeout: int = 300) -> str:
    res = subprocess.run(["osascript", "-"], input=script, capture_output=True,
                         text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip())
    return res.stdout.rstrip("\n")


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_manifest() -> list:
    notes = load_json(MANIFEST, None)
    if notes is None:
        sys.exit("❌ 找不到 manifest.json，請先跑 scan。")
    return notes


def select(notes: list, args) -> list:
    if args.only:
        want = {p.strip() for p in args.only.split(",") if p.strip()}
        notes = [n for n in notes if n["pk"] in want]
    if args.limit:
        notes = notes[:args.limit]
    return notes


def set_state(pk: str, **kv):
    state = load_json(STATE, {})
    state.setdefault(pk, {}).update(kv)
    save_json(STATE, state)


def as_quote(s: str) -> str:
    """Python 字串 → AppleScript 字串常值。"""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------- 1 掃描

SCAN_SCRIPT = f'''
set FS to (ASCII character 31)
set RS to (ASCII character 30)
set out to ""
tell application "Notes"
    repeat with f in folders
        set fn to name of f
        if fn is not "Recently Deleted" then
            repeat with n in notes of f
                set atts to attachments of n
                if (count of atts) > 0 then
                    set an to ""
                    repeat with a in atts
                        try
                            set an to an & (name of a) & ";"
                        on error
                            set an to an & "?;"
                        end try
                    end repeat
                    set d to creation date of n
                    set ds to ((year of d) as string) & "-" & ((month of d as integer) as string) & "-" & ((day of d) as string) & " " & ((hours of d) as string) & ":" & ((minutes of d) as string)
                    set pt to plaintext of n
                    set out to out & (id of n) & FS & fn & FS & (name of n) & FS & ((length of pt) as string) & FS & an & FS & ds & FS & ((password protected of n) as string) & RS
                end if
            end repeat
        end if
    end repeat
end tell
return out
'''


def cmd_scan(args):
    print("🔎 掃描備忘錄（唯讀）...")
    raw = osa(SCAN_SCRIPT, timeout=600)
    notes, skipped_body = [], 0
    for rec in raw.split(RS):
        f = rec.strip("\n").split(FS)
        if len(f) < 7:
            continue
        note_id, folder, name, blen, atts, created, locked = f[:7]
        att_names = [a for a in atts.split(";") if a]
        if not any(a.lower().endswith(AUDIO_EXT) for a in att_names):
            continue
        if folder == TARGET_FOLDER:
            continue
        if int(blen) > MAX_BODY_CHARS:
            skipped_body += 1
            continue
        y, mo, rest = created.split("-")
        d, hm = rest.split(" ")
        h, mi = hm.split(":")
        notes.append({
            "pk": note_id.rsplit("/", 1)[-1],
            "id": note_id,
            "folder": folder,
            "old_title": name.strip(),
            "attachments": att_names,
            "created": f"{int(y):04d}-{int(mo):02d}-{int(d):02d} {int(h):02d}:{int(mi):02d}",
            "locked": locked == "true",
        })
    notes.sort(key=lambda n: n["created"])
    save_json(MANIFEST, notes)
    multi = [n for n in notes if len(n["attachments"]) != 1]
    locked = [n for n in notes if n["locked"]]
    print(f"✅ 只有錄音的備忘錄：{len(notes)} 則（另有 {skipped_body} 則錄音備忘錄已有內文，不處理）")
    if notes:
        print(f"   期間 {notes[0]['created']} ～ {notes[-1]['created']}")
    if multi:
        print(f"   ⚠ 附件不只一個：{[n['pk'] for n in multi]}")
    if locked:
        print(f"   ⚠ 已上鎖：{[n['pk'] for n in locked]}")
    print(f"   → {MANIFEST}")


# ---------------------------------------------------------------- 2 匯出

def audio_path(pk: str):
    hits = sorted(AUDIO_DIR.glob(f"{pk}.*"))
    return hits[0] if hits else None


def probe_seconds(path: Path) -> float:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(res.stdout.strip())
    except ValueError:
        return 0.0


NOTES_DIR = Path.home() / "Library/Group Containers/group.com.apple.notes"


def open_notes_db():
    """把備忘錄資料庫（含 WAL）複製到暫存目錄後唯讀開啟，不碰正在使用中的原檔。"""
    import shutil
    import sqlite3
    tmp = Path(tempfile.mkdtemp(prefix="notesdb_"))
    try:
        for f in NOTES_DIR.glob("NoteStore.sqlite*"):
            shutil.copy2(f, tmp / f.name)
    except PermissionError:
        sys.exit("❌ 讀不到備忘錄資料夾。請在「系統設定 → 隱私權與安全性 → 完全取用磁碟」加入實際執行\n"
                 "   本腳本的程式（終端機；由 Claude 執行時是 ~/Library/Application Support/Claude/\n"
                 "   claude-code/<版本>/<雜湊>/claude.app，不是 /Applications/Claude.app），加完要重開。")
    return sqlite3.connect(tmp / "NoteStore.sqlite")


def media_file(db, media_pk):
    row = db.execute("select ZIDENTIFIER from ZICCLOUDSYNCINGOBJECT where Z_PK=?", (media_pk,)).fetchone()
    if not row:
        return None
    hits = sorted(NOTES_DIR.glob(f"Accounts/*/Media/{row[0]}/*/*"))
    return hits[0] if hits else None


def resolve_recording(db, att_pk: int, media_pk, db_secs: float):
    """一個錄音附件 → (要串接的檔案清單, 說明)。

    ⚠ AppleScript 的 `save attachment`（與介面的「儲存附件⋯」）拿到的只是附件本身那個檔，
    常常只是片段（實測 1:06:54 的錄音只拿到 56 秒）。完整錄音放在「子附件」的檔案裡，
    子附件可能一個（已合併的完整檔）或多個（分段，依序相接）。這裡以資料庫記載的長度為準挑檔。
    """
    top = media_file(db, media_pk) if media_pk else None
    subs = [media_file(db, m) for (m,) in db.execute(
        "select ZMEDIA from ZICCLOUDSYNCINGOBJECT where ZPARENTATTACHMENT=? order by Z_PK", (att_pk,))]
    subs = [f for f in subs if f]
    cands = ([top] if top else []) + subs
    if not cands:
        return [], "找不到實體檔（可能尚未從 iCloud 下載）"
    durs = {f: probe_seconds(f) for f in cands}
    sub_total = sum(durs[f] for f in subs)
    if db_secs and db_secs > 0:
        for f in cands:
            if abs(durs[f] - db_secs) <= 3:
                # 資料庫長度有時只對到最後一小段（實測 7 秒對上、另有一段 6 分鐘）——寧可多收不可漏
                if len(subs) > 1 and max(durs.values()) > durs[f] * 2:
                    return subs, f"{len(subs)} 段相接（資料庫長度只對到其中一小段，全收）"
                return [f], "單檔"
        if subs and abs(sub_total - db_secs) <= 3 * len(subs):
            return subs, f"{len(subs)} 段相接"
    # 資料庫沒記長度或對不上：取「最長單檔」與「子附件相接」中較長者
    longest = max(cands, key=lambda f: durs[f])
    if len(subs) > 1 and sub_total > durs[longest] + 3:
        return subs, f"{len(subs)} 段相接（資料庫長度 {db_secs or 0:.0f}s 對不上，取總和）"
    note = "" if not db_secs else f"（資料庫長度 {db_secs:.0f}s 對不上，取最長檔）"
    return [longest], "單檔" + note


def cmd_export(args):
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    notes = select(load_manifest(), args)
    db = open_notes_db()
    state = load_json(STATE, {})
    total, failed, changed = 0.0, [], []
    for i, n in enumerate(notes, 1):
        pk = n["pk"]
        atts = db.execute(
            "select Z_PK, ZMEDIA, ZDURATION from ZICCLOUDSYNCINGOBJECT "
            "where ZNOTE=? and ZPARENTATTACHMENT is null and ZTYPEUTI like '%audio%' "
            "and coalesce(ZMARKEDFORDELETION,0)=0 order by Z_PK", (int(pk[1:]),)).fetchall()
        files, how = [], []
        for att_pk, media_pk, secs in atts:
            fs, h = resolve_recording(db, att_pk, media_pk, secs)
            files += [f for f in fs if f not in files]
            how.append(h)
        if not files:
            failed.append(pk)
            set_state(pk, export=f"failed: {'; '.join(how) or '資料庫裡找不到錄音附件'}")
            print(f"[{i}/{len(notes)}] {pk} ❌ {'; '.join(how) or '資料庫裡找不到錄音附件'}")
            continue
        for old in AUDIO_DIR.glob(f"{pk}.*"):
            old.unlink()
        dst = AUDIO_DIR / f"{pk}.m4a"
        if len(files) == 1:
            import shutil
            dst = AUDIO_DIR / f"{pk}{files[0].suffix.lower()}"
            shutil.copy2(files[0], dst)
        else:
            cmd = ["ffmpeg", "-v", "error", "-y"]
            for f in files:
                cmd += ["-i", str(f)]
            cmd += ["-filter_complex", f"concat=n={len(files)}:v=0:a=1", "-c:a", "aac", "-b:a", "64k", str(dst)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                failed.append(pk)
                set_state(pk, export=f"failed: ffmpeg {res.stderr.strip()[:200]}")
                print(f"[{i}/{len(notes)}] {pk} ❌ ffmpeg {res.stderr.strip()[:200]}")
                continue
        secs = probe_seconds(dst)
        total += secs
        before = state.get(pk, {}).get("seconds")
        if before is not None and abs(before - secs) > 3:
            changed.append(pk)
            # 先前以片段做的轉錄／摘要作廢，下次重做
            for d in (TRANSCRIPT_DIR, SUMMARY_DIR):
                (d / f"{pk}.json").unlink(missing_ok=True)
        set_state(pk, export="ok", seconds=round(secs, 1), bytes=dst.stat().st_size)
        mark = f"  ← 原 {fmt_ts(before)}" if pk in changed else ""
        print(f"[{i}/{len(notes)}] {pk} {n['created']} {fmt_ts(secs)}  {dst.stat().st_size / 1e6:.1f} MB  "
              f"{'; '.join(how)}{mark}")
    print(f"\n✅ 匯出 {len(notes) - len(failed)} 則，總長 {total / 3600:.2f} 小時；失敗 {len(failed)} 則 {failed or ''}")
    if changed:
        print(f"   長度與上次匯出不同（已清掉舊的轉錄／摘要快取）：{len(changed)} 則 {changed}")


# ---------------------------------------------------------------- 3 轉錄

MLX_REPO = "mlx-community/whisper-large-v3-turbo"


def transcribe_mlx(src: Path, language):
    """Apple Silicon GPU（mlx-whisper＋large-v3-turbo）。回傳 (片段, 語言, 長度秒)。"""
    import mlx_whisper
    r = mlx_whisper.transcribe(
        str(src), path_or_hf_repo=MLX_REPO, language=language or None,
        initial_prompt=WHISPER_PROMPT, condition_on_previous_text=False, verbose=None)
    segs = []
    for s in r["segments"]:
        text = s["text"].strip()
        # mlx 版沒有 VAD：靠無語音機率與壓縮比擋掉靜音段的幻覺與鬼打牆
        if not text or s.get("no_speech_prob", 0) > 0.6 or s.get("compression_ratio", 0) > 2.4:
            continue
        segs.append({"start": round(s["start"], 1), "end": round(s["end"], 1), "text": text})
    dur = r["segments"][-1]["end"] if r["segments"] else probe_seconds(src)
    return segs, r.get("language") or language, dur


def transcribe_faster(model, src: Path, language):
    """CPU（faster-whisper），沒裝 mlx-whisper 時的備援。"""
    segments, info = model.transcribe(
        str(src), language=language or None, vad_filter=True,
        initial_prompt=WHISPER_PROMPT, condition_on_previous_text=False)
    segs = [{"start": round(s.start, 1), "end": round(s.end, 1), "text": s.text.strip()}
            for s in segments if s.text and s.text.strip()]
    return segs, info.language, info.duration


def cmd_transcribe(args):
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    notes = [n for n in select(load_manifest(), args)
             if audio_path(n["pk"]) and not (TRANSCRIPT_DIR / f"{n['pk']}.json").exists()]
    if not notes:
        print("✅ 沒有待轉錄的錄音。")
        return
    engine = args.engine
    if engine == "auto":
        try:
            import mlx_whisper  # noqa: F401
            engine = "mlx"
        except ImportError:
            engine = "faster"
    model = None
    if engine == "faster":
        from faster_whisper import WhisperModel
        print(f"🎙️  faster-whisper（{args.whisper_model}，CPU）...")
        model = WhisperModel(args.whisper_model, device="cpu", compute_type="int8")
    else:
        print(f"🎙️  mlx-whisper（{MLX_REPO}，GPU）...")
    t_all, audio_all = time.time(), 0.0
    for i, n in enumerate(notes, 1):
        pk = n["pk"]
        src = audio_path(pk)
        t0 = time.time()
        try:
            if engine == "mlx":
                segs, lang, dur = transcribe_mlx(src, args.language)
            else:
                segs, lang, dur = transcribe_faster(model, src, args.language)
        except Exception as e:
            set_state(pk, transcribe=f"failed: {e}")
            print(f"[{i}/{len(notes)}] {pk} ❌ {e}", flush=True)
            continue
        save_json(TRANSCRIPT_DIR / f"{pk}.json",
                  {"engine": engine, "language": lang, "duration": dur, "segments": segs})
        kept = clean_segs(segs)
        set_state(pk, transcribe="ok" if kept else "empty")
        audio_all += dur
        chars = sum(len(s["text"]) for s in kept)
        print(f"[{i}/{len(notes)}] {pk} {fmt_ts(dur)} → {chars} 字"
              f"（{time.time() - t0:.0f}s）{'' if kept else '  ⚠ 無語音'}", flush=True)
    el = time.time() - t_all
    print(f"\n✅ 轉錄 {audio_all / 3600:.2f} 小時音訊，耗時 {el / 60:.1f} 分（{audio_all / max(el, 1):.1f} 倍速）")


def paragraphs(segs: list) -> list:
    """把 Whisper 片段併成段落：[(起始秒, 文字)]。"""
    paras, cur, start, last_end = [], [], 0.0, 0.0
    for s in segs:
        if cur and (s["start"] - start >= PARA_SECONDS or s["start"] - last_end >= PARA_GAP):
            paras.append((start, "，".join(cur)))
            cur = []
        if not cur:
            start = s["start"]
        cur.append(s["text"])
        last_end = s["end"]
    if cur:
        paras.append((start, "，".join(cur)))
    return paras


# Whisper 在無聲／音樂段落常見的幻覺句（訓練資料裡的片尾字幕），以及把 initial_prompt 原樣吐回來
HALLUCINATION_RE = re.compile(
    r"點贊|點讚|訂閱|轉發|打賞|明鏡|点赞|订阅|转发|打赏|明镜|栏目|字幕(由|提供|製作|志願)|謝謝(觀看|收看|大家收看)|"
    r"逐字稿與談話錄音|正體中文（台灣用語）|Amara\.org|請按讚|下集再見|下期再見")


def clean_segs(segs: list) -> list:
    """濾掉 Whisper 幻覺句與連續重複句。"""
    out = []
    for s in segs:
        if HALLUCINATION_RE.search(s["text"]):
            continue
        if len(out) >= 2 and out[-1]["text"] == out[-2]["text"] == s["text"]:
            continue
        out.append(s)
    return out


# ---------------------------------------------------------------- 4 摘要

SUMMARY_PROMPT = """stdin 是一段手機錄音的 Whisper 自動逐字稿，每段前有 [時間] 標記。錄音時間：{created}。
內容多為工作會議、討論、教學或個人備忘；自動轉錄可能有錯字、同音字，請依上下文判讀。

請以「正體中文（台灣用語）」整理，只輸出一個 JSON 物件，不要任何其他文字、不要 markdown code fence：

{{
  "title": "能一眼看出這段錄音在談什麼的標題，8~20 字，不要含日期、不要用「錄音」「會議紀錄」這類空泛字眼",
  "points": ["重點 1", "重點 2"],
  "todos": ["待辦或決議（誰／做什麼／何時，逐字稿有提到才寫）"]
}}

要求：
- points 3~8 條，每條具體、可獨立閱讀；內容很短就少寫，不要湊數。
- todos 只列逐字稿中明確提到的決議或待辦，沒有就給空陣列，不要自行推測。
- 人名、系統名、數字以逐字稿為準；聽不清楚而無法確定的不要編造。
- 若逐字稿幾乎沒有可辨識內容（雜音、音樂、斷續單字），title 用「內容無法辨識」，points 給一條說明。"""


def run_claude(prompt: str, stdin_text: str, model: str) -> str:
    import os
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)  # 允許在 Claude Code session 內巢狀呼叫
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    cmd = [find_claude_cli(), "-p", prompt, "--output-format", "text"]
    if model:
        cmd += ["--model", model]
    res = subprocess.run(cmd, input=stdin_text, capture_output=True, text=True,
                         timeout=CLAUDE_TIMEOUT, env=env)
    if res.returncode != 0:
        raise RuntimeError((res.stderr.strip() or res.stdout.strip())[:500])
    return res.stdout.strip()


def parse_json(raw: str):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not str(data.get("title", "")).strip():
        return None
    return {"title": str(data["title"]).strip(),
            "points": [str(p).strip() for p in data.get("points") or [] if str(p).strip()],
            "todos": [str(p).strip() for p in data.get("todos") or [] if str(p).strip()]}


def cmd_summarize(args):
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    notes = [n for n in select(load_manifest(), args)
             if (TRANSCRIPT_DIR / f"{n['pk']}.json").exists()
             and not (SUMMARY_DIR / f"{n['pk']}.json").exists()]
    if not notes:
        print("✅ 沒有待摘要的逐字稿。")
        return
    for i, n in enumerate(notes, 1):
        pk = n["pk"]
        segs = clean_segs(load_json(TRANSCRIPT_DIR / f"{pk}.json", {})["segments"])
        if not segs:
            data = {"title": "（無語音）", "points": [], "todos": [], "empty": True}
        else:
            text = "\n".join(f"[{fmt_ts(t)}] {p}" for t, p in paragraphs(segs))
            if len(text) > MAX_SUMMARY_CHARS:
                text = text[:MAX_SUMMARY_CHARS] + "\n（後段過長已截斷）"
            try:
                raw = run_claude(SUMMARY_PROMPT.format(created=n["created"]), text, args.model)
            except Exception as e:
                detail = str(e)
                print(f"[{i}/{len(notes)}] {pk} ❌ {detail}")
                if any(k in detail for k in ("Not logged in", "/login", "OAuth", "authenticate")):
                    sys.exit("❌ claude CLI 未登入或登入已過期，請先 /login 後重跑 summarize。")
                set_state(pk, summarize=f"failed: {detail[:200]}")
                continue
            data = parse_json(raw)
            if data is None:
                set_state(pk, summarize="failed: 非合法 JSON")
                print(f"[{i}/{len(notes)}] {pk} ❌ 回傳非合法 JSON：{raw[:120]}")
                continue
        save_json(SUMMARY_DIR / f"{pk}.json", data)
        set_state(pk, summarize="ok")
        print(f"[{i}/{len(notes)}] {pk} {n['created']} → {data['title']}", flush=True)


# ---------------------------------------------------------------- 內文組裝

def new_title(n: dict, summ: dict) -> str:
    # 日期在前：資料夾改按標題排序時仍是時間順序（Jimmy 2026-10-13 指定）
    return f"{n['created']} {summ['title']}"


def build_text(n: dict, summ: dict, segs: list) -> str:
    """要插到錄音前面的純文字（第一列＝標題）。"""
    out = [new_title(n, summ)]
    if summ.get("points"):
        out.append("【摘要】")
        out += [f"• {p}" for p in summ["points"]]
    if summ.get("todos"):
        out.append("【決議／待辦】")
        out += [f"• {p}" for p in summ["todos"]]
    if segs:
        out += ["", "【逐字稿】（AI 轉錄，可能有誤）"]
        out += [f"[{fmt_ts(t)}] {p}" for t, p in paragraphs(segs)]
    else:
        out.append("（這段錄音沒有偵測到語音）")
    return "\n".join(out) + "\n\n"


def ready(n: dict):
    """回傳 (摘要, 片段)；尚未備齊回 None。"""
    pk = n["pk"]
    summ = load_json(SUMMARY_DIR / f"{pk}.json", None)
    tr = load_json(TRANSCRIPT_DIR / f"{pk}.json", None)
    if summ is None or tr is None:
        return None
    return summ, clean_segs(tr["segments"])


# ---------------------------------------------------------------- 5 預覽

def cmd_preview(args):
    state = load_json(STATE, {})
    rows = []
    for n in select(load_manifest(), args):
        r = ready(n)
        if not r:
            continue
        summ, segs = r
        secs = state.get(n["pk"], {}).get("seconds", 0)
        pts = "".join(f"<li>{html.escape(p)}</li>" for p in summ["points"])
        todos = "".join(f"<li>{html.escape(p)}</li>" for p in summ["todos"])
        rows.append(
            f"<tr><td>{n['pk']}<br><small>{fmt_ts(secs)}</small></td>"
            f"<td>{html.escape(n['old_title'] or '（空）')}</td>"
            f"<td><b>{html.escape(new_title(n, summ))}</b><ul>{pts}</ul>"
            + (f"<i>決議／待辦</i><ul>{todos}</ul>" if todos else "")
            + f"<details><summary>逐字稿（{sum(len(s['text']) for s in segs)} 字）</summary>"
            + "".join(f"<p>[{fmt_ts(t)}] {html.escape(p)}</p>" for t, p in paragraphs(segs))
            + "</details></td></tr>")
    PREVIEW_HTML.write_text(
        "<!doctype html><meta charset='utf-8'><title>備忘錄錄音整理預覽</title>"
        "<style>body{font-family:-apple-system,'PingFang TC',sans-serif;margin:24px;line-height:1.6}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px;vertical-align:top}"
        "td:nth-child(1),td:nth-child(2){white-space:nowrap;color:#666}details p{margin:4px 0;color:#444}"
        "@media(prefers-color-scheme:dark){body{background:#111;color:#ddd}td,th{border-color:#444}"
        "td:nth-child(1),td:nth-child(2){color:#999}details p{color:#bbb}}</style>"
        f"<h2>備忘錄錄音整理預覽（{len(rows)} 則）</h2>"
        "<table><tr><th>編號／長度</th><th>原標題</th><th>新標題／摘要</th></tr>"
        + "".join(rows) + "</table>", encoding="utf-8")
    print(f"✅ 預覽 {len(rows)} 則 → {PREVIEW_HTML}")


# ---------------------------------------------------------------- 6 寫入

def note_facts(note_id: str):
    """回傳 (附件數, plaintext 第一列)。"""
    raw = osa(f'''tell application "Notes"
    set n to note id {as_quote(note_id)}
    return ((count of attachments of n) as string) & (ASCII character 31) & (name of n)
end tell''')
    cnt, name = raw.split(FS, 1)
    return int(cnt), name


OBJ = "\ufffc"  # 編輯區文字裡代表附件（錄音）的佔位字元
AX_SRC = BASE_DIR / "notes_ax_insert.swift"
AX_BIN = CACHE_DIR / "bin" / "notes_ax_insert"


def ax(*argv) -> str:
    """呼叫 notes_ax_insert（首次或原始碼更新時自動編譯）。"""
    if not AX_BIN.exists() or AX_BIN.stat().st_mtime < AX_SRC.stat().st_mtime:
        AX_BIN.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["swiftc", "-O", str(AX_SRC), "-o", str(AX_BIN)], check=True)
    res = subprocess.run([str(AX_BIN), *map(str, argv)], capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip())
    return res.stdout


def insert_before_recording(note_id: str, text: str, force: bool = False):
    """把 text 換掉「第一個附件之前」的內容（原標題／空白列），附件本身不動。

    ⚠ 不可用 AppleScript `set body`：body 的 HTML 不含附件，整則覆寫會把錄音洗掉（2026-10-13 實測）。
    這裡走輔助使用 API 只改選取範圍內的文字。
    """
    osa(f'tell application "Notes" to show note id {as_quote(note_id)}')
    time.sleep(1.2)
    # 確認視窗真的切到這一則才動手，避免把文字插進別則（切換較慢、或視窗被使用者點走）
    shown = osa('tell application "Notes" to return id of item 1 of (get selection)')
    if shown != note_id:
        raise RuntimeError("備忘錄視窗沒有切到這一則，略過")
    cur = ax("read")
    if OBJ not in cur:
        raise RuntimeError("編輯區找不到附件，可能沒切到正確的備忘錄")
    head = cur[:cur.index(OBJ)]
    if len(head.strip()) > MAX_BODY_CHARS and not force:
        raise RuntimeError(f"錄音前已有 {len(head.strip())} 字內文，與掃描時不符，略過")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        tmp = fh.name
    try:
        # AX 範圍以 UTF-16 code unit 計
        ax("replace", 0, len(head.encode("utf-16-le")) // 2, tmp)
        # 游標移回最前面，把第一列套成「大標題」樣式（純外觀，失敗不影響內容）
        ax("caret", 0)
        try:
            osa('tell application "System Events" to tell process "Notes" to click menu item "大標題" '
                'of menu 1 of menu bar item "格式" of menu bar 1')
        except Exception as e:
            print(f"   ⚠ 套用標題樣式失敗：{e}")
    finally:
        Path(tmp).unlink(missing_ok=True)
    time.sleep(0.8)


def cmd_apply(args):
    notes = select(load_manifest(), args)
    state = load_json(STATE, {})
    todo = [n for n in notes if ready(n) and state.get(n["pk"], {}).get("apply") != "deleted"
            and (args.redo or state.get(n["pk"], {}).get("apply") != "ok")]
    print(f"待寫入 {len(todo)} 則" + ("" if args.yes else "（預覽，未加 --yes 不會寫入）"))
    if args.yes and todo:
        # 寫入靠操作備忘錄視窗，螢幕一鎖定就讀不到視窗——執行期間不讓螢幕休眠（腳本結束自動解除）
        subprocess.Popen(["caffeinate", "-d", "-i", "-w", str(os.getpid())])
    for i, n in enumerate(todo, 1):
        pk = n["pk"]
        summ, segs = ready(n)
        title = new_title(n, summ)
        if not args.yes:
            print(f"  {pk}  {n['old_title'] or '（空）'} → {'🗑 無語音，將刪除' if summ.get('empty') else title}")
            continue
        if not audio_path(pk):
            sys.exit(f"❌ {pk} 沒有錄音備份，拒絕寫入。請先跑 export。")
        if summ.get("empty"):
            # 沒偵測到語音的錄音直接刪除（Jimmy 2026-10-13 指示）；進「最近刪除」30 天內可救回，
            # 錄音備份仍在 .notes_audio_cache/audio/
            try:
                osa(f'tell application "Notes" to delete note id {as_quote(n["id"])}')
            except Exception as e:
                set_state(pk, apply=f"failed: 刪除失敗 {e}")
                print(f"[{i}/{len(todo)}] {pk} ❌ 刪除失敗 {e}")
                continue
            set_state(pk, apply="deleted")
            print(f"[{i}/{len(todo)}] {pk} 🗑 無語音，已刪除（{n['created']}，{n['old_title'] or '無標題'}）", flush=True)
            continue
        before, _ = note_facts(n["id"])
        try:
            insert_before_recording(n["id"], build_text(n, summ, segs), force=args.redo)
        except Exception as e:
            set_state(pk, apply=f"failed: {e}")
            print(f"[{i}/{len(todo)}] {pk} ❌ {e}")
            continue
        after, name = note_facts(n["id"])
        if after != before:
            set_state(pk, apply=f"failed: 附件數 {before} → {after}")
            sys.exit(f"❌ {pk} 寫入後附件數由 {before} 變成 {after}，已停止。備份在 {audio_path(pk)}")
        if name.strip() != title.strip():
            set_state(pk, apply=f"failed: 標題不符（{name}）")
            sys.exit(f"❌ {pk} 寫入後標題是「{name}」，預期「{title}」，已停止。")
        set_state(pk, apply="ok", new_title=title)
        print(f"[{i}/{len(todo)}] {pk} ✅ {name}", flush=True)


# ---------------------------------------------------------------- 7 搬移

def cmd_move(args):
    state = load_json(STATE, {})
    todo = [n for n in select(load_manifest(), args)
            if state.get(n["pk"], {}).get("apply") == "ok" and state[n["pk"]].get("move") != "ok"]
    print(f"待搬移 {len(todo)} 則 → 「{TARGET_FOLDER}」" + ("" if args.yes else "（預覽，未加 --yes 不會搬）"))
    if not args.yes or not todo:
        return
    osa(f'''tell application "Notes"
    tell account "iCloud"
        if not (exists folder {as_quote(TARGET_FOLDER)}) then make new folder with properties {{name:{as_quote(TARGET_FOLDER)}}}
    end tell
end tell''')
    for i, n in enumerate(todo, 1):
        try:
            osa(f'''tell application "Notes"
    move (note id {as_quote(n["id"])}) to folder {as_quote(TARGET_FOLDER)} of account "iCloud"
end tell''')
        except Exception as e:
            set_state(n["pk"], move=f"failed: {e}")
            print(f"[{i}/{len(todo)}] {n['pk']} ❌ {e}")
            continue
        set_state(n["pk"], move="ok")
        print(f"[{i}/{len(todo)}] {n['pk']} ✅", flush=True)


# ---------------------------------------------------------------- 進度

def cmd_status(args):
    notes = load_manifest()
    state = load_json(STATE, {})
    print(f"備忘錄 {len(notes)} 則")
    for stage in ("export", "transcribe", "summarize", "apply", "move"):
        vals = [state.get(n["pk"], {}).get(stage) for n in notes]
        ok = sum(v == "ok" for v in vals)
        empty = sum(v in ("empty", "deleted") for v in vals)
        bad = [n["pk"] for n, v in zip(notes, vals) if v and v.startswith("failed")]
        print(f"  {stage:<10} 完成 {ok}" + (f"、無語音{'已刪除' if stage == 'apply' else ''} {empty}" if empty else "")
              + (f"、失敗 {len(bad)} {bad}" if bad else ""))
    secs = sum(state.get(n["pk"], {}).get("seconds", 0) for n in notes)
    print(f"  錄音總長 {secs / 3600:.2f} 小時")


def main():
    ap = argparse.ArgumentParser(description="備忘錄錄音 → 逐字稿＋摘要 批次整理")
    ap.add_argument("cmd", choices=["scan", "export", "transcribe", "summarize",
                                    "preview", "apply", "move", "status"])
    ap.add_argument("--limit", type=int, help="只處理前 N 則")
    ap.add_argument("--only", help="只處理指定備忘錄（逗號分隔，如 p1670,p1669）")
    ap.add_argument("--yes", action="store_true", help="apply／move 真的寫入（否則只預覽）")
    ap.add_argument("--redo", action="store_true",
                    help="apply：已寫入過的也重寫（把錄音前面的既有文字整段換掉；務必搭配 --only）")
    ap.add_argument("--engine", choices=["auto", "mlx", "faster"], default="auto",
                    help="轉錄引擎：mlx＝GPU large-v3-turbo（預設，有裝就用）、faster＝CPU faster-whisper")
    ap.add_argument("--whisper-model", default=WHISPER_MODEL_SIZE,
                    help=f"Whisper 模型（tiny/base/small/medium/large-v3，預設 {WHISPER_MODEL_SIZE}）")
    ap.add_argument("--language", default="zh", help="轉錄語言（預設 zh；給空字串則自動偵測）")
    ap.add_argument("--model", default="sonnet", help="claude CLI 模型別名（預設 sonnet）")
    args = ap.parse_args()
    {"scan": cmd_scan, "export": cmd_export, "transcribe": cmd_transcribe,
     "summarize": cmd_summarize, "preview": cmd_preview, "apply": cmd_apply,
     "move": cmd_move, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    main()
