#!/usr/bin/env bash
# Wrapper the scheduler calls. Safe to run by hand at any time.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

mkdir -p logs out data
LOG="logs/run.log"
# Create the log immediately. Backgrounding this script and tailing the log in
# the next breath is the obvious thing to do, and it used to lose the race.
touch "$LOG"

# Keep the log from growing without bound.
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 5000000 ]; then
  mv "$LOG" "$LOG.1"
fi

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
  if [ -f .env ]; then set -a; . ./.env; set +a; fi
  if [ -z "${POLYGON_API_KEY:-}" ]; then
    echo "POLYGON_API_KEY is not set — run 'bash setup.sh' first." >&2
    exit 1
  fi
  ./.venv/bin/python -u -m mr.run "$@"
} >> "$LOG" 2>&1

# Surface the newest outputs so a caller (or Claude) can find them without
# guessing the date.
LATEST_HTML="$(ls -t out/review_*.html 2>/dev/null | head -1 || true)"
LATEST_MD="$(ls -t out/review_*.md 2>/dev/null | head -1 || true)"
if [ -n "$LATEST_HTML" ]; then
  ln -sf "$(basename "$LATEST_HTML")" out/latest.html
  ln -sf "$(basename "$LATEST_MD")"   out/latest.md
  echo "$LATEST_HTML"

  # Show the review as soon as it is built. Set MR_AUTO_OPEN=0 to suppress,
  # which is what you want when running this from another script or over ssh.
  #
  # Guarded on the run having actually produced a file: opening a stale
  # latest.html after a failed run would quietly show yesterday's numbers as if
  # they were today's, which is worse than showing nothing.
  if [ "${MR_AUTO_OPEN:-1}" = "1" ]; then
    open "out/latest.html" 2>/dev/null || true
  fi
fi
