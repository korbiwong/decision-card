#!/usr/bin/env bash
# 由 quotes-loop.yml 行。每 10 分鐘醒一次：
#   · 港股／美股交易時段：行 quotes.py（報價、即日 5 分鐘線、提醒）
#   · 收市後：每個市場每日行一次 scanner.py（收市掃描）
# 一個 GitHub job 最長 6 小時，所以行夠 ~5 小時 40 分就叫醒下一個自己再走，
# 唔使靠 GitHub cron 準時（佢成日遲幾個鐘）。週末停，星期日晚由 cron 重新開始。
set -u
STATE=loop_state.json
MAX_SECS=$((320 * 60))   # 留位俾最後一次 sleep + 掃描，唔好撞 6 小時上限
start=$(date +%s)

hm()  { TZ=$1 date +%H%M; }       # 時分，例如 0930
dow() { TZ=$1 date +%u; }         # 1=星期一 … 7=星期日
ymd() { TZ=$1 date +%F; }
in_range() { (( 10#$1 >= 10#$2 && 10#$1 < 10#$3 )); }

# 交易時段（開市前 10 分鐘至收市後 15 分鐘）
us_open() { (( $(dow America/New_York) <= 5 )) && in_range "$(hm America/New_York)" 0920 1615; }
hk_open() { (( $(dow Asia/Hong_Kong) <= 5 )) && in_range "$(hm Asia/Hong_Kong)" 0920 1625; }

# 收市後要唔要掃描：當地星期一至五、16:20 之後，而今日未掃過
scanned() { grep -o "\"$1\": *\"[0-9-]*\"" "$STATE" 2>/dev/null | grep -o '[0-9]\{4\}-[0-9-]*'; }
need_scan() {
  local tz=$2
  (( $(dow "$tz") <= 5 )) && in_range "$(hm "$tz")" 1620 2359 && [ "$(scanned "$1")" != "$(ymd "$tz")" ]
}
mark_scanned() {
  local hk us
  hk=$(scanned HK); us=$(scanned US)
  [ "$1" = HK ] && hk=$2; [ "$1" = US ] && us=$2
  printf '{"HK": "%s", "US": "%s"}\n' "$hk" "$us" > "$STATE"
}

# 週末（UTC 星期六全日至星期日 20:00）唔使行
weekend() {
  local d t; d=$(date -u +%u); t=$(date -u +%H%M)
  (( d == 6 )) || { (( d == 7 )) && (( 10#$t < 2000 )); }
}

push() {
  git add quotes.json intraday.json state.json signals_*.json "$STATE" 2>/dev/null
  git diff --cached --quiet && return
  git commit -q -m "$1"
  for i in 1 2 3 4 5; do git pull --rebase -X theirs -q && git push -q && return; sleep 10; done
}

tick() {
  git pull --rebase -X theirs -q || true      # 攞你最新嘅 alerts / trades / watch
  local m tz did=
  for m in HK US; do
    [ $m = HK ] && tz=Asia/Hong_Kong || tz=America/New_York
    if need_scan $m $tz; then
      echo "== $(date -u +%H:%M) UTC · 掃描 $m =="
      if python scanner.py $m; then mark_scanned $m "$(ymd $tz)"; fi
      python quotes.py || true
      push "scan $(ymd $tz)"; did=1
    fi
  done
  if [ -z "$did" ] && { hk_open || us_open; }; then
    echo "== $(date -u +%H:%M) UTC · 報價 =="
    python quotes.py || echo "quotes.py 出錯，下次再試"
    push quotes
  fi
}

[ "${LOOP_TEST:-}" ] && return 0 2>/dev/null   # 測試用：只載入函數

while :; do
  if weekend; then echo "週末，停；星期日晚 cron 會再開始"; exit 0; fi
  tick
  if (( $(date +%s) - start > MAX_SECS )); then
    echo "就到 6 小時上限，叫醒下一個"
    gh workflow run quotes-loop.yml --ref "${GITHUB_REF_NAME:-main}" || echo "叫唔醒，等 cron 後備"
    exit 0
  fi
  sleep 600
done
