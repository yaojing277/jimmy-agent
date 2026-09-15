#!/usr/bin/env python3
"""專案總覽頁的雲端發佈器（GitHub Actions 專用）

兩件事：
  1. 把本次排程執行結果附加到 yaojing277/projects 的 runlog.json（保留最近 90 天）
  2. 把 projects.html / 兩份 devlog / schedule_runlog.html 同步到同一個 repo
     —— 內容完全相同就跳過，不會產生空提交

刻意「不」做的事：不改寫兩份 devlog 的內容。那是人工撰寫的開發紀錄，
排程當天沒有開發行為，機器寫不出「需求／實作／踩坑與解法／驗證結果」。
排程只負責讓線上版永遠等於 repo 內的最新版。

用法（workflow 內）：
    python3 publish_projects.py \
        --target-date 2026/09/03 --trigger 排程 --mode 更新股價＋同步＋全分頁 \
        --status success --step "更新股價 OK" --step "同步股價 OK"

環境變數 PROJECTS_PAT（缺就整支安靜跳過，讓 workflow 不會因此變紅）。
"""
import argparse
import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

REPO = "yaojing277/projects"
API = f"https://api.github.com/repos/{REPO}/contents"
KEEP_DAYS = 90
TPE = timezone(timedelta(hours=8))

# 要同步到 Pages 的檔案：本機路徑 → repo 內路徑
SYNC_FILES = {
    "projects.html": "projects.html",
    "projects.html#index": "index.html",          # 同一份內容另存首頁
    "stock_automation_devlog.html": "stock_automation_devlog.html",
    "wealth_os_devlog.html": "wealth_os_devlog.html",
    "schedule_runlog.html": "schedule_runlog.html",
}


def _headers(pat):
    return {"Authorization": f"token {pat}", "Accept": "application/vnd.github+json"}


def _get(path, pat):
    """回傳 (內容 bytes 或 None, sha 或 None)。404 視為不存在，不是錯誤。"""
    r = requests.get(f"{API}/{path}", headers=_headers(pat), timeout=20)
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    d = r.json()
    return base64.b64decode(d["content"]), d["sha"]


def _put(path, content: bytes, sha, pat, message):
    data = {
        "message": message,
        "content": base64.b64encode(content).decode(),
        "branch": "main",
    }
    if sha:
        data["sha"] = sha
    r = requests.put(f"{API}/{path}", headers=_headers(pat), json=data, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"PUT {path} 失敗 {r.status_code}: {r.text[:300]}")


def update_runlog(args, pat, now):
    raw, sha = _get("runlog.json", pat)
    try:
        runs = json.loads(raw.decode()) if raw else []
        if not isinstance(runs, list):
            runs = []
    except (ValueError, UnicodeDecodeError):
        # 檔案壞掉不該讓整個排程失敗，重新開始記就好
        print("⚠ runlog.json 解析失敗，重建")
        runs = []

    entry = {
        "target_date": args.target_date or "—",
        "ran_at": now.strftime("%Y/%m/%d %H:%M"),
        "trigger": args.trigger,
        "mode": args.mode,
        "status": args.status,
        "steps": args.step or [],
        "run_url": os.environ.get("RUN_URL", ""),
    }

    # 同一個目標交易日重跑就覆蓋，避免手動補跑後出現兩筆
    runs = [r for r in runs if r.get("target_date") != entry["target_date"]]
    runs.insert(0, entry)

    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y/%m/%d")
    kept = [r for r in runs if (r.get("target_date") or "9999") >= cutoff]
    dropped = len(runs) - len(kept)

    body = json.dumps(kept, ensure_ascii=False, indent=1).encode()
    if raw is not None and body == raw:
        print("• runlog.json 無變化")
        return
    _put("runlog.json", body, sha, pat, f"排程紀錄：{entry['target_date']} {entry['status']}")
    print(f"✓ runlog.json 已更新（{len(kept)} 筆"
          + (f"，清掉 {dropped} 筆逾 {KEEP_DAYS} 天）" if dropped else "）"))


def sync_pages(pat):
    here = os.path.dirname(os.path.abspath(__file__))
    changed, same, missing = [], [], []
    for src, dst in SYNC_FILES.items():
        local = os.path.join(here, src.split("#")[0])
        if not os.path.isfile(local):
            missing.append(src.split("#")[0])
            continue
        with open(local, "rb") as f:
            content = f.read()
        remote, sha = _get(dst, pat)
        if remote == content:
            same.append(dst)
            continue
        _put(dst, content, sha, pat, f"同步 {dst}：{datetime.now(TPE):%Y-%m-%d}")
        changed.append(dst)

    if changed:
        print(f"✓ 已同步 {len(changed)} 個檔案：{', '.join(changed)}")
    if same:
        print(f"• {len(same)} 個檔案內容相同，跳過：{', '.join(same)}")
    if missing:
        print(f"⚠ 找不到來源檔（跳過）：{', '.join(sorted(set(missing)))}")


def main():
    p = argparse.ArgumentParser(description="更新排程執行紀錄並同步專案總覽頁")
    p.add_argument("--target-date", default="", help="本次記錄的目標交易日，例 2026/09/03")
    p.add_argument("--trigger", default="排程", help="排程 / 手動")
    p.add_argument("--mode", default="", help="執行模式")
    p.add_argument("--status", default="success", help="success / failure / skipped")
    p.add_argument("--step", action="append", help="步驟結果，可重複給")
    p.add_argument("--no-sync", action="store_true", help="只寫 runlog，不同步 HTML")
    args = p.parse_args()

    pat = os.environ.get("PROJECTS_PAT", "").strip()
    if not pat:
        print("無 PROJECTS_PAT，跳過發佈（不視為失敗）")
        return 0

    now = datetime.now(TPE)
    try:
        update_runlog(args, pat, now)
        if not args.no_sync:
            sync_pages(pat)
    except Exception as e:
        # 發佈失敗不該讓「股價已經正確寫進試算表」的排程被標成紅字
        print(f"✗ 發佈失敗（不影響前面的資料更新）：{e}")
        return 1
    print(f"\n✓ 完成：https://yaojing277.github.io/projects/schedule_runlog.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
