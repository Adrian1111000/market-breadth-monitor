#!/usr/bin/env bash
# One-shot installer for the daily market review on macOS.
#
#   bash setup.sh
#
# Creates a virtualenv, installs dependencies, stores your Polygon key in a
# local .env (chmod 600, never leaves this machine), builds the price cache, and
# installs a launchd agent that runs the review every weekday morning.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
ROOT="$(pwd)"
LABEL="com.dllm.marketreview"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# Say plainly where we are. A shell left sitting in a deleted directory reports
# the old name in its prompt, which makes every later error look mysterious.
if ! pwd -P >/dev/null 2>&1; then
  printf '\033[31m%s\033[0m\n' \
    "This shell's working directory no longer exists. Open a new Terminal window,
cd to the install folder, and run setup.sh again." >&2
  exit 1
fi
printf '\033[1mInstalling in\033[0m %s\n' "$ROOT"
case "$ROOT" in
  "$HOME"/Downloads/*|"$HOME"/Desktop/*)
    warn "Note: $ROOT is inside a folder macOS restricts and that people tend to
empty. The price cache lives here and takes an hour to rebuild — somewhere like
~/market-review is a better home." ;;
esac

# --------------------------------------------------------------------------- #
say "1/5  Checking Python"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || die "python3 not found. Install it, or set PYTHON=/path/to/python3."
"$PY" - <<'EOF' || die "Python 3.10 or newer is required."
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
EOF
echo "  $("$PY" --version) at $(command -v "$PY")"

# --------------------------------------------------------------------------- #
say "2/5  Creating the virtualenv"

if [ ! -d .venv ]; then
  "$PY" -m venv .venv
  echo "  created .venv"
else
  echo "  .venv already exists, reusing it"
fi
if [ ! -x .venv/bin/pip ]; then
  warn "  venv has no pip (common with Anaconda) — bootstrapping it"
  ./.venv/bin/python -m ensurepip --upgrade >/dev/null 2>&1 \
    || { rm -rf .venv; "$PY" -m venv --without-pip .venv 2>/dev/null || "$PY" -m venv .venv
         ./.venv/bin/python -m ensurepip --upgrade >/dev/null 2>&1; }
  [ -x .venv/bin/pip ] || die "Could not bootstrap pip inside .venv. Try: $PY -m venv --clear .venv"
fi
./.venv/bin/pip install --quiet --upgrade pip
# Retry: pip reports success even after a flaky partial download on some networks.
for attempt in 1 2 3; do
  if ./.venv/bin/pip install --quiet --retries 5 --timeout 60 -r requirements.txt; then
    break
  fi
  warn "  pip attempt $attempt failed, retrying"
done

# Never take pip's word for it — import the packages for real.
if ! ./.venv/bin/python -c "import pandas, numpy, requests" 2>/dev/null; then
  warn "  Packages did not import cleanly. Reinstalling from scratch."
  ./.venv/bin/pip install --force-reinstall --no-cache-dir -r requirements.txt
  ./.venv/bin/python -c "import pandas, numpy, requests" \
    || die "Dependencies still broken. Check your network and re-run setup.sh."
fi
echo "  dependencies verified: $(./.venv/bin/python -c 'import pandas;print("pandas "+pandas.__version__)')"

# --------------------------------------------------------------------------- #
say "3/5  Polygon API key"

if [ -f .env ] && grep -q '^POLYGON_API_KEY=.\+' .env; then
  echo "  .env already has a key — leaving it alone"
else
  echo "  Paste your Polygon key (get one at polygon.io/dashboard/api-keys)."
  echo "  It is written to $ROOT/.env with permissions 600 and never sent anywhere"
  echo "  except api.polygon.io."
  printf '  key: '
  read -rs POLY_KEY
  echo
  [ -n "$POLY_KEY" ] || die "No key entered."

  echo
  echo "  Is this a paid Polygon plan, or the free tier?"
  echo "    1) Free tier      — 5 requests/minute. First build takes about an hour."
  echo "    2) Paid plan      — no limit. First build takes a few minutes."
  printf '  [1/2] '
  read -r TIER
  case "$(printf '%s' "${TIER:-1}" | tr '[:upper:]' '[:lower:]')" in
    2|paid|p) RATE=0 ;;
    *)        RATE=5 ;;
  esac
  if [ "$RATE" = "0" ]; then
    echo "  -> paid plan, no throttling"
  else
    echo "  -> free tier, throttling to 5 requests/minute"
  fi

  umask 077
  cat > .env <<EOF
POLYGON_API_KEY=$POLY_KEY
# Requests per minute. 5 = free tier. 0 = no limit (paid plans).
# If this is wrong the client detects a 429 and throttles itself anyway.
MR_RATE_LIMIT_PER_MIN=$RATE
# Which 50-day line the ATR stretch is measured from: ema (matches the Patreon
# table) or sma (matches the +2.19 reading).
MR_ATR_LINE=ema
EOF
  chmod 600 .env
  echo "  written to .env (600)"
fi

# --------------------------------------------------------------------------- #
say "4/5  Building the price cache"

set -a; . ./.env; set +a
mkdir -p data/grouped out logs
# find, not ls: on a fresh checkout data/grouped may not exist, and under
# `set -euo pipefail` a failing ls inside $( ) kills the script silently.
CACHED=$(find data/grouped -name '*.json.gz' -type f | wc -l | tr -d ' ')
REMAIN=$(( 380 - CACHED )); [ "$REMAIN" -lt 0 ] && REMAIN=0

echo "  The first build downloads roughly 380 trading days, one request each."
echo "  Already cached: $CACHED"
if [ "${MR_RATE_LIMIT_PER_MIN:-0}" != "0" ]; then
  echo "  At ${MR_RATE_LIMIT_PER_MIN}/min that is about $(( REMAIN / MR_RATE_LIMIT_PER_MIN )) minutes for the rest."
fi
echo "  It downloads newest sessions first and caches every day, so a partial"
echo "  build is a shorter history rather than a broken one. Ctrl-C is safe."
echo
echo "    1) Full build now          — everything works when it finishes"
echo "    2) Quick start (90 days)   — usable dashboard in ~18 min; the daily"
echo "                                 job deepens the cache from then on"
echo "    3) Skip"
printf '  [1/2/3] '
read -r BUILD
case "${BUILD:-1}" in
  2) ./.venv/bin/python -u -m mr.run --max-downloads 90 \
       || warn "  Stopped early — run ./run_daily.sh to continue." ;;
  3) echo "  Skipped. Run ./run_daily.sh when you're ready." ;;
  *) ./.venv/bin/python -u -m mr.run \
       || warn "  Stopped early — re-run ./run_daily.sh to continue where it left off." ;;
esac

# --------------------------------------------------------------------------- #
say "5/5  Installing the scheduled job"

mkdir -p "$HOME/Library/LaunchAgents" logs
sed -e "s|__LABEL__|$LABEL|g" -e "s|__ROOT__|$ROOT|g" \
    launchd.plist.template > "$PLIST"

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl enable "gui/$UID/$LABEL"

cat <<EOF

  Installed $PLIST
  Runs 06:00 local, Tuesday through Saturday — each run covers the previous
  US session (Monday's close lands Tuesday morning your time).

  Useful commands:
    ./run_daily.sh                          run it right now
    tail -f logs/run.log                    watch the log
    launchctl kickstart gui/$UID/$LABEL     force a scheduled run
    launchctl bootout gui/$UID/$LABEL       stop the schedule

  Outputs land in $ROOT/out/ and the rolling table in $ROOT/data/history.csv.
  If your Mac is asleep at 06:00 the job runs at the next wake.

Done.
EOF
