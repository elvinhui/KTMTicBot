#!/usr/bin/env bash
# ec2_watchdog.sh — EC2 Pull-Based Auto-Deploy & Runtime Crash Self-Healing Daemon
# Runs on AWS EC2 (e.g. via cron `*/2 * * * * ~/ktm-sniper/ec2_watchdog.sh >> ~/ktm-sniper/watchdog.log 2>&1`)
#
# Responsibilities:
# 1. Auto-Pull & Hot-Reload: Checks if `origin/main` has new verified commits (including AI auto-healed fixes).
#    If updated, pulls latest code and runs `docker compose up -d --build --remove-orphans`.
# 2. Runtime Crash Reporting: Scans recent `ktm_sniper_bot` container logs for unhandled Python Tracebacks.
#    If a new crash is found and GH_PAT is set in .env, dispatches `auto_healer.yml` on GitHub Actions!

set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/ktm-sniper}"
cd "$APP_DIR"

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source <(grep -v '^#' .env | sed 's/\r$//') || true
  set +a
fi

# ── 1. Check for new commits on origin/main (Auto-Deploy) ────────────────────
git fetch origin main --quiet
LOCAL_SHA=$(git rev-parse HEAD)
REMOTE_SHA=$(git rev-parse origin/main)

if [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
  echo "[$(date -Is)] 🚀 New verified commit detected on origin/main ($LOCAL_SHA -> $REMOTE_SHA). Auto-deploying..."
  git pull --rebase --autostash origin main
  docker compose up -d --build --remove-orphans
  echo "[$(date -Is)] ✅ EC2 container updated to $REMOTE_SHA."
fi

# ── 2. Check container logs for unhandled Tracebacks or runtime ERRORs ───────
RECENT_LOGS=$(docker compose logs --since=2m --no-log-prefix ktm_sniper_bot 2>/dev/null || true)

# Match either raw Python Tracebacks OR caught logger.error() entries from ktm_sniper
if echo "$RECENT_LOGS" | grep -Eq "Traceback \(most recent call last\):|\[ERROR\]|Encountered cycle error:|Browser scraping error:|Fatal error"; then
  CRASH_HASH=$(echo "$RECENT_LOGS" | grep -E "Traceback|Error|Exception|\[ERROR\]" | tail -n 20 | sha256sum | awk '{print $1}')
  LAST_HASH_FILE="/tmp/ktm_last_crash_hash.txt"
  PREV_HASH=$(cat "$LAST_HASH_FILE" 2>/dev/null || echo "")

  if [ "$CRASH_HASH" != "$PREV_HASH" ]; then
    echo "$CRASH_HASH" > "$LAST_HASH_FILE"
    echo "[$(date -Is)] ⚠️ Runtime error/traceback detected in ktm_sniper_bot!"

    if [ -n "${GH_PAT:-}" ]; then
      ESCAPED_LOG=$(echo "$RECENT_LOGS" | tail -n 80 | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
      curl -sS -X POST \
        -H "Authorization: Bearer $GH_PAT" \
        -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/elvinhui/KTMTicBot/actions/workflows/auto_healer.yml/dispatches" \
        -d "{\"ref\":\"main\",\"inputs\":{\"ec2_error_log\":$ESCAPED_LOG}}"
      echo "[$(date -Is)] 🤖 Dispatched runtime error log to GitHub Actions Auto-Healer!"
    fi
  fi
fi

