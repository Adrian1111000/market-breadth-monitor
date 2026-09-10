"""Configuration for the daily market review.

Every threshold that drives a colour, a signal or a membership rule lives here so
the whole system can be re-tuned without touching the metric code.
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

ROOT = Path(os.environ.get("MR_ROOT", Path(__file__).resolve().parent.parent))
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "grouped"       # one gzipped json per trading day
OUT_DIR = ROOT / "out"
HISTORY_CSV = DATA_DIR / "history.csv"  # rolling monitor table, source of truth

for _d in (DATA_DIR, CACHE_DIR, OUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# Polygon
# --------------------------------------------------------------------------- #

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")
POLYGON_BASE = "https://api.polygon.io"

# Free tier is 5 requests/minute. Set MR_RATE_LIMIT_PER_MIN=0 to disable throttling
# (paid plans). The client sleeps to stay under whatever is configured.
RATE_LIMIT_PER_MIN = int(os.environ.get("MR_RATE_LIMIT_PER_MIN", "0"))

# Calendar days of history to pull. 200D MA + 52-week high/low needs ~1.5 years.
# The free plan caps history at 2 years, so keep this under ~700.
LOOKBACK_CALENDAR_DAYS = int(os.environ.get("MR_LOOKBACK_DAYS", "560"))

# The ticker reference list is ~13 paginated calls and barely moves day to day.
# On a 5-calls-per-minute plan, refetching it daily costs more than the price
# data itself, so a snapshot this many days old is good enough to reuse.
REFERENCE_MAX_AGE_DAYS = int(os.environ.get("MR_REFERENCE_MAX_AGE", "7"))

# Cap new downloads per run: -1 unlimited, 0 means use the cache only (no API
# calls for price data), N > 0 fetches at most N new sessions. On a rate-limited
# plan this lets the cache deepen a little each day instead of blocking on one
# long first build — the newest sessions are always fetched first, so a partial
# cache is a shallower history rather than a broken one.
MAX_DOWNLOADS = int(os.environ.get("MR_MAX_DOWNLOADS", "-1"))

# --------------------------------------------------------------------------- #
# Universe definition  (mirrors the reference monitor)
# --------------------------------------------------------------------------- #

# Polygon ticker types kept: CS = common stock, ADRC = ADR common share.
UNIVERSE_TYPES = ("CS", "ADRC")

MIN_CLOSE = 5.00           # close >= $5
MIN_VOLUME = 300_000       # same-day share volume >= 300k
# Sessions a name must have traded to count. This is the knob that most moves
# the universe size: at 60 the count is 2,321, at 47 it is 2,330. Tune it to
# match whatever reference you are tracking.
MIN_HISTORY_BARS = int(os.environ.get("MR_MIN_HISTORY_BARS", "60"))

# Optional primary-exchange allowlist, as MIC codes. EMPTY BY DEFAULT and that
# is deliberate: the reference list is already fetched with market=stocks and
# the bars with include_otc=false, so OTC is excluded upstream. An allowlist
# here is a silent-drop hazard — vendors report Nasdaq tiers as XNGS / XNMS /
# XNCM as well as XNAS, and a list missing one of those quietly deletes most of
# the Nasdaq from the universe. Run `python -m mr.doctor` to see the actual
# distribution before setting one.
KEEP_EXCHANGES = tuple(
    x.strip() for x in os.environ.get("MR_KEEP_EXCHANGES", "").split(",") if x.strip()
)

# --------------------------------------------------------------------------- #
# Breadth thresholds
# --------------------------------------------------------------------------- #

MOVE_PCT = 4.0             # UP 4% / DOWN 4% day
BIG_COUNT = 300            # a >=300 reading on either side is a "thrust" day

# --------------------------------------------------------------------------- #
# Leadership index (MLI)
# --------------------------------------------------------------------------- #

MLI_MIN_PRICE = 10.0
MLI_MIN_DOLLAR_VOL = 10_000_000     # 50-day average dollar volume
MLI_RS_PERCENTILE = 70              # 126-day return percentile within universe
MLI_RS_LOOKBACK = 126
MLI_TREND_LOOKBACK = 21             # 200D MA must be rising over this window

# --------------------------------------------------------------------------- #
# Index / ETF tickers
# --------------------------------------------------------------------------- #

INDEX_TICKERS = ("SPY", "QQQ", "IWM", "MDY")
ATR_PERIOD = 14
ATR_EMA_PERIOD = 50

# How the 14-day ATR is smoothed. Sources disagree, and the choice visibly moves
# the "distance from the 50D EMA" reading — Wilder's RMA holds onto an earlier
# volatility spike far longer than a plain mean, so it reads a *larger* ATR and
# therefore a *smaller* ATR distance in a calming tape.
#   "wilder" — Wilder's RMA, alpha = 1/period (the classic Welles Wilder ATR)
#   "sma"    — simple 14-day mean of true range
#   "ema"    — standard EMA, span = period
ATR_METHOD = os.environ.get("MR_ATR_METHOD", "wilder")

# Seeding for the 50-day EMA. "sma" seeds from the first 50-bar mean (what most
# charting packages do); "first" seeds from the first close (pandas' default).
EMA_SEED = os.environ.get("MR_EMA_SEED", "sma")

# Which 50-day line the ATR distance is measured from. This is the single choice
# that most moves the reading — on SPY at 2026-09-04 the same ATR gave +1.81
# against the EMA and +2.19 against the SMA.
ATR_LINE = os.environ.get("MR_ATR_LINE", "ema")   # "ema" or "sma"

SECTOR_ETFS = {
    "XLK": "Technology",
    "XLC": "Communication",
    "XLY": "Cons. Discretionary",
    "XLP": "Cons. Staples",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLE": "Energy",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
}

THEME_ETFS = {
    "SMH": "Semiconductors",
    "IGV": "Software",
    "XBI": "Biotech",
    "KRE": "Regional Banks",
    "ITB": "Homebuilders",
    "XRT": "Retail",
    "GDX": "Gold Miners",
    "ARKK": "Spec. Growth",
    "IBIT": "Bitcoin",
}

# --------------------------------------------------------------------------- #
# Distribution days / follow-through days  (O'Neil style)
# --------------------------------------------------------------------------- #

DD_WINDOW = 25             # rolling count window, sessions
DD_MIN_DROP_PCT = 0.20     # close must be down at least this much
DD_EXPIRE_RALLY_PCT = 5.0  # a DD is cancelled once the index closes this far above it
DD_HEAVY = 5               # >= this many DDs in the window is a real problem
DD_LIGHT = 2               # <= this many is a clean tape

FTD_MIN_GAIN_PCT = 1.25    # follow-through day gain on rising volume
FTD_MIN_DAY = 4            # earliest session of a rally attempt that can be an FTD
FTD_MAX_DAY = 15           # latest session that still counts

# --------------------------------------------------------------------------- #
# Colour thresholds for the monitor table
# --------------------------------------------------------------------------- #

PCT_ABOVE_EXTREME_LOW = {"ma20": 10.0, "ma50": 20.0, "ma200": 20.0}
PCT_ABOVE_LOW = 30.0
PCT_ABOVE_HIGH = 70.0
PCT_ABOVE_EXTREME_HIGH = {"ma20": 90.0, "ma50": 80.0, "ma200": 80.0}

ATR_STRETCHED = 5.0        # >= +5 ATR above the 50D EMA is overextended
ATR_OVERSOLD = -5.0        # <= -5 ATR below is washed out

# --------------------------------------------------------------------------- #
# Regime scoring
# --------------------------------------------------------------------------- #

REGIME_GREEN_AT = 3        # composite score >= this -> GREEN
REGIME_RED_AT = -2         # composite score <= this -> RED

TABLE_ROWS = 22            # rows shown in the dashboard's rolling table
