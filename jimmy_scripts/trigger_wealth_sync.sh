#!/bin/bash
# trigger_wealth_sync.sh — 由本機 launchd 每個交易日定時打 GitHub API 觸發 wealth_sync.yml
#
# 【為什麼存在】
# GitHub 排程(cron)對 stock-notify 極不穩定。2026-09-25~10-07 實測:每次「建立執行」
# 遲到 5h08m~8h35m(排隊 0m、實際執行只 0.4~4.5m,遲到幅度與工作量完全無關),
# 2026-10-06 甚至整次被丟掉,當天的國泰漲跌/除息日曆/跌幅查詢頁/加碼損益全部缺,
# 需人工補跑。把 cron 從 :30 改成 :17 避開擁塞時段也無效(10/07 仍遲到 7h04m)。
# 同期 workflow_dispatch 實測是「秒級啟動」(00:11 觸發、00:12 建立執行),
# 故改由本機 launchd 當主要觸發源。
#
# 【與 GitHub cron 的關係】
# GitHub 的 cron 保留當備援:Mac 沒開機/離線那天仍會跑,只是晚幾小時(＝改用 launchd
# 之前的現狀,不會更糟)。2026-11-08 起 workflow 守衛對 schedule 觸發會先查 runlog:
# 同一交易日已有 success 就整輪跳過(不重跑、不覆寫 runlog、不多推 LINE);
# 14:30 那次失敗或 runlog 讀不到時,備援照跑(＝自動重試一次)。
# 反過來 cron 罕見地準時先跑完時,這支腳本的「不重複觸發」判斷同樣依 runlog 跳過。
#
# 【⚠ 必須自己判斷交易日】
# workflow 的「交易日守衛」對 workflow_dispatch 一律 run=yes(manual 旗標),
# 若週末/國定假日照打,它會拿「最近一個有收盤資料的交易日」重跑一次,覆寫 runlog
# 並多推 LINE。所以這支腳本先確認 TWSE 今天真的有收盤資料,沒有就安靜結束。
#
# 【⚠ 為什麼刻意不 import twse_hist / _load_pat.sh】
# launchd 叫起的程序受 macOS TCC 限制,**讀不到 ~/Downloads 底下任何檔案**
# (實測 `/bin/bash: …/trigger_wealth_sync.sh: Operation not permitted`)。
# 所以這支腳本必須完全自足:交易日判斷內建(TWSE STOCK_DAY,含官方主/備端點
# fallback,邏輯與 twse_hist._twse_month 一致)、PAT 自己讀,不依賴 repo 任何檔案。
# 本檔是版控母本,實際執行的是安裝到 ~/Library/Application Support/wealth-sync/ 的副本
# (該處不受 TCC 保護)。**改完這支要重跑安裝指令**,見 com.jimmy.wealthsync.plist 檔頭。
#
# 【手動測試】./trigger_wealth_sync.sh --dry-run   (只判斷不觸發)
#             ./trigger_wealth_sync.sh --now       (跳過交易日判斷,直接觸發)
#             TRIGGER_SCHED_HHMM=0000 ./trigger_wealth_sync.sh --dry-run  (解除時間閘)
#             TRIGGER_RETRIES=1 TRIGGER_RETRY_GAP=0 ./trigger_wealth_sync.sh --dry-run
set -uo pipefail

REPO="yaojing277/stock-notify"
WORKFLOW="wealth_sync.yml"
MODE="更新股價＋同步＋全分頁"      # 必須與 workflow 的 choice 選項逐字相同(全形＋)
PROBE_CODE="0050"                   # 用它判斷當天 TWSE 有無收盤資料
PY=/usr/bin/python3                 # 只用標準庫,系統 python3 即可(不依賴 pip)
# 排程執行紀錄(線上)。⚠ 取用一律加 ?t=<epoch> 打掉快取:GitHub Pages 對它送
#   cache-control: max-age=600,剛寫完 10 分鐘內可能讀到舊版(schedule_runlog.html
#   早就這樣做了,本腳本原本沒有——同一個檔案兩種取法遲早會對不起來)。
RUNLOG_URL="https://yaojing277.github.io/projects/runlog.json"
LOG_DIR="$HOME/Library/Logs"
LOG="$LOG_DIR/wealth_sync_trigger.log"

# TWSE 收盤資料有時晚幾十分鐘才上架 → 重試 6 次 x 10 分鐘(最多等到約 +60 分)。
# 兩個都可用環境變數覆寫,測試時可 TRIGGER_RETRIES=1 TRIGGER_RETRY_GAP=0 快速跑完。
RETRIES="${TRIGGER_RETRIES:-6}"
RETRY_GAP="${TRIGGER_RETRY_GAP:-600}"

mkdir -p "$LOG_DIR"
log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG" ; }

DRY_RUN=0; FORCE=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=1 ;;
    --now)     FORCE=1 ;;
    *) log "✗ 未知參數:$a(可用 --dry-run / --now)"; exit 2 ;;
  esac
done

# ── 0. 時間閘:早於排定時間就不動作(讓「開機自動補跑」得以成立) ──
# plist 的 RunAtLoad=true 會在每次登入/載入時叫起這支腳本,目的是補跑
# 「14:30 當下 Mac 是關機狀態」那一天——launchd 的 StartCalendarInterval
# 睡眠醒來會補跑,**關機後開機不會**(2026-10-27 實測 runs=0,當天全靠人工觸發)。
#
# 設計上刻意只加這一道閘、不開新的 job、不存任何狀態檔:
#   早於排定時間 → 安靜結束,交給 StartCalendarInterval 照原時間跑
#   晚於排定時間 → 當成補跑繼續走下去
# 後面三道既有防呆讓「補跑」天然安全:週末跳過、TWSE 當日無收盤資料跳過、
# 今天這個交易日已處理過就不重複觸發。所以同一天被叫起幾次都不會重複觸發。
# ⚠ 第三道的判斷依據是 runlog.json 的 target_date(「處理了哪一天」),
#   不是「今天有沒有建立執行」——理由見第 4 節的註解。
SCHED_HHMM="${TRIGGER_SCHED_HHMM:-1430}"   # 要與 plist 的 StartCalendarInterval 一致
NOW_HHMM=$(date '+%H%M')
# 10# 前綴:避免 "0930" 這種前導零被當成八進位
if [ "$FORCE" -eq 0 ] && [ "$((10#$NOW_HHMM))" -lt "$((10#$SCHED_HHMM))" ]; then
  log "— 現在 ${NOW_HHMM:0:2}:${NOW_HHMM:2:2},未到排定時間 ${SCHED_HHMM:0:2}:${SCHED_HHMM:2:2},不觸發(等排程)"
  exit 0
fi

# 遠晚於排定時間才被叫起(典型的開機補跑)→ TWSE 資料早已上架,不必等 60 分鐘。
# 這條是為了非交易日:否則開機補跑會白佔一小時才判定「今天沒交易」
# (2026-10-26 那次就是在那裡耗了 60 分鐘)。手動指定 TRIGGER_RETRIES 時不覆寫。
if [ "$FORCE" -eq 0 ] && [ "$((10#$NOW_HHMM))" -ge 1600 ] && [ -z "${TRIGGER_RETRIES:-}" ]; then
  RETRIES=1
  log "— 補跑模式(已過 16:00):TWSE 資料應早已上架,不做等待重試"
fi

# ── 1. 週末直接結束(不必打 TWSE) ─────────────────────────────
DOW=$(date '+%u')                   # 1=一 … 7=日
if [ "$FORCE" -eq 0 ] && [ "$DOW" -ge 6 ]; then
  log "— 今天是週末(週$DOW),不觸發"
  exit 0
fi

# ── 2. 取得 PAT(順序同 _load_pat.sh:環境變數 → ~/.config/gh_pat) ──
# launchd 不繼承登入 shell 的環境,所以實際上幾乎都是讀檔案那條路。
PAT="${GH_PAT:-}"
if [ -z "$PAT" ] && [ -f "$HOME/.config/gh_pat" ]; then
  PAT="$(tr -d ' \t\r\n' < "$HOME/.config/gh_pat")"
fi
if [ -z "$PAT" ]; then
  log "✗ 找不到 GitHub PAT(環境變數 GH_PAT 或 ~/.config/gh_pat 皆無),不觸發"
  exit 1
fi

# ── 2.5 偵測最近有沒有漏掉的交易日(只警示,不自動補) ──────────
# ⚠ 為什麼只警示不自動補:workflow_dispatch **只有 `job` 一個 input**,而守衛是用
#   datetime.date.today() 算目標日,所以「觸發」永遠只能處理今天,無法指定補跑過去某天。
#   (2026-10-07 00:11 那次之所以補到 10/06,是因為當時 TWSE 還沒有 10/07 資料、
#    守衛才算出 10/06——那是時間湊巧,不是能重複的機制。)
#   真要補跑過去某天,得先給 workflow 加 target_date input,而且 `更新股價` 那步
#   的快照滾動假設「日期往前推進」,倒著建快照會讓分頁順序與 H~K 欄位錯亂,
#   必須另外處理。所以這裡只負責讓漏掉的日子不會被默默忽略。
check_recent_gaps() {
  "$PY" - "$PROBE_CODE" "$RUNLOG_URL" <<'PY' 2>/dev/null
import sys, json, time, datetime, urllib.request
code, runlog_url = sys.argv[1], sys.argv[2]
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0"}

def fetch_runlog(base):
    """讀線上排程紀錄。加 ?t= 打掉 CDN 快取,並要求不使用快取回應。"""
    url = base + ("&" if "?" in base else "?") + "t=" + str(int(time.time()))
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                               "Pragma": "no-cache"})
    return json.load(urllib.request.urlopen(req, timeout=20))

def roc(s):
    s = str(s).strip().replace("年", "/").replace("月", "/").replace("日", "")
    p = [x for x in s.split("/") if x]
    if len(p) != 3:
        return None
    try:
        y, m, d = (int(x) for x in p)
        return datetime.date(y + 1911, m, d)
    except ValueError:
        return None

def month_days(y, m):
    for path in ("rwd/zh/afterTrading", "exchangeReport"):
        url = (f"https://www.twse.com.tw/{path}/STOCK_DAY"
               f"?date={y}{m:02d}01&stockNo={code}&response=json")
        try:
            d = json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=20))
        except Exception:
            continue
        if d.get("stat") != "OK":
            continue
        out = []
        for row in d.get("data", []):
            dt = roc(row[0])
            try:
                px = float(str(row[6]).replace(",", ""))
            except (ValueError, IndexError):
                px = 0
            if dt and px:
                out.append(dt)
        if out:
            return out
    return []

t = datetime.date.today()
days = month_days(t.year, t.month)
if t.day <= 10:                       # 月初時往前補上個月,才湊得出 5 個交易日
    py, pm = (t.year - 1, 12) if t.month == 1 else (t.year, t.month - 1)
    days = month_days(py, pm) + days
# 只看「今天之前」的交易日,今天本身由主流程負責
past = sorted(d for d in days if d < t)[-5:]
if not past:
    sys.exit(1)

try:
    log = fetch_runlog(runlog_url)
except Exception:
    print("RUNLOG_UNAVAILABLE")
    sys.exit(0)
# ⚠ 要分辨「完全沒跑」與「跑了但失敗」——兩者的處置完全不同,而且混為一談會誤導:
#   2026-11-05 就是「排程跑了、Drive 回 503 導致同步股價失敗」,舊訊息卻說
#   「可能是 Mac 關機」,害我 11/08 一度判成快取造成的誤報。
by_date = {r.get("target_date"): r for r in log if r.get("target_date")}
for d in past:
    r = by_date.get(d.strftime("%Y/%m/%d"))
    if r is None:
        print("GAP", d.strftime("%Y-%m-%d"))
    elif r.get("status") != "success":
        bad = [x for x in (r.get("steps") or []) if "：success" not in x and "：skipped" not in x]
        print("FAIL", d.strftime("%Y-%m-%d"),
              (r.get("run_url") or "(無連結)"), "|", ",".join(bad) or "(步驟不明)")
sys.exit(0)
PY
}

gaps=$(check_recent_gaps)
if [ "$gaps" = "RUNLOG_UNAVAILABLE" ]; then
  log "⚠ 讀不到線上 runlog,略過漏跑偵測"
elif [ -n "$gaps" ]; then
  log "⚠⚠ 最近 5 個交易日中,這些日子沒有成功的執行:"
  while IFS=' ' read -r kind d url _sep steps; do
    case "$kind" in
      GAP)  log "   • $d 完全沒有執行紀錄(Mac 關機,且 GitHub cron 也被丟掉?)" ;;
      FAIL) log "   • $d 有執行但失敗:${steps:-步驟不明}"
            log "       $url" ;;
    esac
  done <<< "$gaps"
  log "   無法自動補——dispatch 只能處理今天。請找 Jimmy 確認,"
  log "   或看 https://yaojing277.github.io/projects/schedule_runlog.html"
else
  log "✓ 最近 5 個交易日都有成功紀錄,無漏跑"
fi

# ── 3. 確認 TWSE 今天有收盤資料(含國定假日/颱風假判斷) ───────
# 回傳碼:0=今天有資料(交易日) / 1=今天無資料 / 2=查詢失敗(連線或解析)
check_trading_day() {
  "$PY" - "$PROBE_CODE" <<'PY' 2>/dev/null
import sys, json, datetime, urllib.request
code = sys.argv[1]
t = datetime.date.today()
UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")}
# 主端點 /rwd/ 會間歇對個別代號回假錯誤,故備用 /exchangeReport/(同參數同欄位)
# ——與 twse_hist._twse_month 相同策略。
urls = [f"https://www.twse.com.tw/{p}/STOCK_DAY"
        f"?date={t.year}{t.month:02d}01&stockNo={code}&response=json"
        for p in ("rwd/zh/afterTrading", "exchangeReport")]

def roc(s):
    s = str(s).strip().replace("年", "/").replace("月", "/").replace("日", "")
    p = [x for x in s.split("/") if x]
    if len(p) != 3:
        return None
    try:
        y, m, d = (int(x) for x in p)
        return datetime.date(y + 1911, m, d)
    except ValueError:
        return None

failed = True
for url in urls:
    try:
        d = json.load(urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=20))
    except Exception:
        continue
    if d.get("stat") != "OK":
        continue
    failed = False
    for row in d.get("data", []):
        try:
            px = float(str(row[6]).replace(",", ""))
        except (ValueError, IndexError):
            continue
        if px and roc(row[0]) == t:         # 收盤 0 視同無效
            sys.exit(0)
sys.exit(2 if failed else 1)
PY
}

if [ "$FORCE" -eq 0 ]; then
  ok=0
  for i in $(seq 1 "$RETRIES"); do
    check_trading_day; rc=$?
    if [ "$rc" -eq 0 ]; then ok=1; break; fi
    if [ "$rc" -eq 1 ]; then
      log "… 第 $i/$RETRIES 次:TWSE 尚無今日收盤資料"
    else
      log "… 第 $i/$RETRIES 次:TWSE 查詢失敗(連線或解析問題)"
    fi
    [ "$i" -lt "$RETRIES" ] && sleep "$RETRY_GAP"
  done
  if [ "$ok" -eq 0 ]; then
    log "— 等了約 $((RETRIES * RETRY_GAP / 60)) 分鐘仍無今日收盤資料,判定非交易日(或 TWSE 異常),不觸發;GitHub cron 仍會當備援"
    exit 0
  fi
  log "✓ TWSE 已有今日收盤資料,判定為交易日"
fi

# ── 4. 今天這個交易日是否已經處理過?(避免重複觸發) ─────────────
# ⚠ 2026-11-05 修正:原本的判斷是「今天有沒有建立成功的執行」,那個依據是錯的。
#   一次在凌晨建立的執行,TWSE 當時還沒有今天的收盤資料,workflow 的守衛會算出
#   「昨天」當目標日——它確實成功了,但處理的是昨天。於是 14:30 這道防呆照樣跳過,
#   當天的資料整天空缺,而且**不留任何警示**。
#   2026-11-05 實測:04:22 的手動執行(目標日 11/04)讓 14:30 靜默跳過,
#   第二波加碼報告與 LINE 停在 11/04 的收盤,事後才由人工補跑。
#   GitHub cron 遲到跨午夜也會造成同一種誤判(11/04 的 cron 遲到 10h →
#   11/05 00:17 才建立、目標日仍是 11/04)。
#
#   所以判斷依據改成「那次執行實際處理了哪一天」。這件事只有 runlog.json 記得:
#   publish_projects.py 於每次執行結束(含失敗,always())寫入 target_date,
#   同交易日重跑覆蓋,所以「今天的 target_date 有 success」⇔「今天已經處理過」。
#
# 回傳:0=今天已處理(stdout 印出判斷依據) / 1=尚未處理,應觸發
already_ran() {
  "$PY" - "$REPO" "$WORKFLOW" "$PAT" "$SCHED_HHMM" "$RUNLOG_URL" <<'PY' 2>/dev/null
import sys, json, time, datetime, urllib.request
repo, wf, pat, sched = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
runlog_url = sys.argv[5]

def fetch_runlog(base):
    """讀線上排程紀錄。加 ?t= 打掉 CDN 快取,並要求不使用快取回應。"""
    url = base + ("&" if "?" in base else "?") + "t=" + str(int(time.time()))
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                               "Pragma": "no-cache"})
    return json.load(urllib.request.urlopen(req, timeout=20))
today = datetime.datetime.now().astimezone().date()

# 主判斷:runlog.json 的 target_date ＝「那次執行處理了哪一天」
try:
    runlog = fetch_runlog(runlog_url)
except Exception:
    runlog = None

if runlog is not None:
    key = today.strftime("%Y/%m/%d")
    for r in runlog:
        if r.get("target_date") == key and r.get("status") == "success":
            print("runlog", key, r.get("run_url", ""))
            sys.exit(0)
    sys.exit(1)        # runlog 讀得到、裡面沒有今天 → 確定還沒處理

# 備援:runlog 讀不到時才退回 GitHub API。只採計「建立時間 ≥ 排定時間」的成功執行
# ——早於排定時間建立的執行不可能涵蓋今天的收盤,那正是上面那個誤判的來源。
# ⚠ 這是保守的啟發式:若 GitHub cron(14:17)罕見地準時跑完、而 runlog 又剛好讀不到,
#   會多觸發一次(冪等,只多一則 LINE)。方向刻意偏「寧可多跑,不要靜默漏掉一天」。
req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/runs?per_page=10",
    headers={"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"})
try:
    runs = json.load(urllib.request.urlopen(req, timeout=20))["workflow_runs"]
except Exception:
    sys.exit(1)        # runlog 與 API 都問不到 → 當成「還沒處理」,寧可多跑一次
for r in runs:
    c = datetime.datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).astimezone()
    if (c.date() == today and r["conclusion"] == "success"
            and int(c.strftime("%H%M")) >= int(sched)):
        print("api", r["id"], c.strftime("%H:%M"))
        sys.exit(0)
sys.exit(1)
PY
}

if [ "$FORCE" -eq 0 ] && basis=$(already_ran); then
  set -- $basis
  if [ "$1" = "runlog" ]; then
    log "— 今天($2)已有成功的執行紀錄,不重複觸發  ${3:-}"
  else
    log "⚠ 讀不到 runlog,退回 API 判斷:run $2($3 建立,晚於排定時間)已成功,不重複觸發"
  fi
  exit 0
fi

if [ "$DRY_RUN" -eq 1 ]; then
  log "[dry-run] 判定應觸發,但未實際送出(模式「$MODE」)"
  exit 0
fi

# ── 5. 觸發 workflow_dispatch ─────────────────────────────────
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  -H "Authorization: Bearer $PAT" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW/dispatches" \
  -d "{\"ref\":\"main\",\"inputs\":{\"job\":\"$MODE\"}}")

if [ "$code" != "204" ]; then
  log "✗ 觸發失敗:HTTP $code(PAT 失效或過期?workflow 被停用?)"
  exit 1
fi
log "✓ 已觸發 workflow_dispatch(模式「$MODE」)"

# ── 6. 確認 GitHub 真的建立了執行(dispatch 回 204 不等於有 run) ──
for i in $(seq 1 12); do
  sleep 5
  out=$("$PY" - "$REPO" "$WORKFLOW" "$PAT" <<'PY' 2>/dev/null
import sys, json, datetime, urllib.request
repo, wf, pat = sys.argv[1], sys.argv[2], sys.argv[3]
req = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/runs?per_page=1",
    headers={"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"})
r = json.load(urllib.request.urlopen(req, timeout=20))["workflow_runs"][0]
c = datetime.datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).astimezone()
print(r["id"], r["event"], r["status"], c.strftime("%H:%M"))
PY
)
  set -- $out
  if [ "${2:-}" = "workflow_dispatch" ]; then
    log "✓ 執行已建立:run $1($4 建立,狀態 $3);約 5 分鐘後完成"
    exit 0
  fi
done
log "⚠ 觸發已接受(204)但 60 秒內未看到新執行,GitHub 可能仍在派發;請稍後查 Actions 頁"
exit 0
