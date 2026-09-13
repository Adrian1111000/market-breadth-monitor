# Daily Market Review

A breadth-and-momentum monitor for US equities. One command produces three things
from Polygon end-of-day data:

- `out/review_YYYY-MM-DD.html` — dashboard (reading tiles, rolling monitor table,
  sector rotation, regime scorecard). Self-contained, publishable as an artifact.
- `out/review_YYYY-MM-DD.md` — the written review: thesis, index-by-index
  technicals, internals each with a risk-on/risk-off verdict, rotation, a
  screened watchlist and the plan. Deterministic prose; `NARRATIVE.md` specs the
  layer that rewrites it in full voice.
- `data/history.csv` — every session's readings, appended. This is the source of
  truth; the dashboard is regenerated from it plus fresh bars.

## Install (macOS, one command)

Clone somewhere permanent — the price cache lives beside the code and you don't
want to rebuild it — then:

```bash
git clone https://github.com/Adrian1111000/market-breadth-monitor.git
cd market-breadth-monitor
bash setup.sh
```

It creates a virtualenv, installs dependencies, asks for your Polygon key and
writes it to a `chmod 600` `.env` (gitignored, never committed — see
`.env.example` for the shape), builds the price cache, and installs a launchd
agent that runs the review at **06:00 local, Tuesday–Saturday** — each run
covering the previous US session. If the Mac is asleep at 06:00 the job runs at
the next wake.

```bash
./run_daily.sh                                 # run it now
tail -f logs/run.log                           # watch it
launchctl kickstart gui/$UID/com.dllm.marketreview   # force a scheduled run
launchctl bootout  gui/$UID/com.dllm.marketreview    # stop the schedule
```

The key never leaves the machine: it is read from `.env` by `run_daily.sh` and
sent only to `api.polygon.io`.

### Manual install

```bash
pip install -r requirements.txt
export POLYGON_API_KEY=your_key_here
```

Optional:

```bash
export MR_RATE_LIMIT_PER_MIN=5     # throttle on the free tier (0 = no limit)
export MR_LOOKBACK_DAYS=560        # calendar days of history to pull
export MR_ROOT=/path/to/store      # where data/ and out/ live
export MR_ATR_LINE=ema             # or sma — see "Matching someone else's ATR reading"
```

## Run

```bash
python -m mr.run                  # latest completed session
python -m mr.run --date 2026-09-04
python -m mr.run --synthetic      # generated market, no API key needed
python verify.py                  # 55 checks against independent re-implementations
```

## API budget

The free plan allows **5 calls/minute** and 2 years of history, which is enough —
it just shapes the first build.

| | calls |
|---|---|
| First build, ~380 trading days | ~380, so about **76 minutes** at 5/min |
| Each day after that | 1 grouped bar + 1 splits = **2** |
| Ticker reference list | ~13, but reused for 7 days, so ~2/day amortised |

Downloads run **newest-first** and every session is cached, so an interrupted
build leaves you with a shorter history rather than a hole. Progress is logged
every 10 sessions with a live rate and ETA.

```bash
python -m mr.run --max-downloads 60   # quick start: ~12 min, usable dashboard
python -m mr.run --max-downloads 0    # rebuild from cache, zero API calls
python -m mr.run                      # unlimited: fill the cache
```

With a shallow cache the metrics that need depth simply stay blank and the run
tells you which ones and how many sessions they need — nothing breaks, and the
regime score just scores fewer components. Roughly:

| sessions | what's live |
|---|---|
| 25 | 4% movers, %>20D, distribution days |
| 50 | + %>50D, ATR vs the 50D line |
| 70 | + sector and theme RS |
| 126 | + net new 52-week highs |
| 200 | + %>200D and the leadership index — full fidelity |

If the key turns out to be rate limited when the config says otherwise, the
client learns that from the first 429 and throttles itself rather than failing.

## ETF leaders, laggards and holdings

Two ETF views sit below the regime panel.

**Leaders and laggards** ranks the sector and theme funds *together* over five
windows -- 1 day, 1 week, 1 month, 3 months, 6 months -- showing the best three
and worst three in each with SPY's return for the same window beside it. Windows
are counted in trading sessions (21 for a month, 126 for six), not calendar days,
which would silently change length with holidays. A fund is ranked over a window
only if it has that much history, so a young fund cannot enter the six-month
table on a shorter run. Sectors and themes share one ranking deliberately: a
theme fund outrunning every sector is the thing worth seeing, and separate tables
would hide it.

**Largest holdings** shows each fund's top five positions by weight, with the
combined top-five share of the fund. That last number is the point -- it is how
concentrated the exposure actually is, and it varies enormously: roughly half the
fund for XLY or XLE, under a tenth for the equal-weighted XBI and KRE.

Holdings are the one thing here that does not come from Polygon, which has no
ETF constituent endpoint at any tier. They come from yfinance, are cached for
`HOLDINGS_MAX_AGE_DAYS` (7 by default, the same reasoning as the ticker
reference -- weights drift by fractions of a percent daily), and are wrapped so
that **any failure leaves the breadth report publishing normally** and simply
omits the section. Breadth is the production output; this is an enrichment.
Funds holding no equities, such as a spot bitcoin trust, are omitted.

## What gets measured

**Universe** — US common stock + ADRs from Polygon's reference data (type `CS`
and `ADRC`, major exchanges only, no ETFs), filtered each session to close ≥ $5
and volume ≥ 300,000. Roughly 2,000–2,600 names.

| Metric | Definition |
|---|---|
| Up 4% / Down 4% | Names closing ≥ 4% up / down vs the prior close. ≥ 300 on either side is a thrust. |
| % > 20D / 50D / 200D | Share of the universe above each simple moving average. |
| SPY / QQQ ATR | `(close − 50-day EMA) ÷ 14-day ATR`. How many ATRs the index sits from its 50D EMA. Past ±5 is stretched. Smoothing is configurable — see below. |
| MLI | Equal-weight leadership index. Membership: price ≥ $10, 50-day average dollar volume ≥ $10m, above the 50D MA and a rising 200D MA, 126-day return in the top 30% of the universe. Reports the basket's daily return, the share of members up, and the member count. |
| Net new highs | 52-week closing highs minus lows inside the universe. |
| Distribution days | Close down ≥ 0.20% on volume above the prior session, counted over 25 sessions, cancelled once the index closes 5% above that day. |
| Follow-through day | Day 4–15 of a rally attempt off a 25-day closing low, index up ≥ 1.25% on rising volume. |
| Sector / theme RS | 1D / 1W / 1M / 3M returns for 11 SPDR sectors and 9 themes, and 1M return relative to SPY. |

## Matching someone else's ATR reading

Three independent choices go into "distance from the 50-day line in ATR units",
and different sources make them differently:

- **ATR smoothing** — `MR_ATR_METHOD=wilder|sma|ema` (default `wilder`)
- **50-day line** — `MR_ATR_LINE=ema|sma` (default `ema`). This is the big one: on SPY at 2026-09-04 the same ATR gave **+1.81** against the EMA and **+2.19** against the SMA.
- **EMA seeding** — `MR_EMA_SEED=sma|first` (default `sma`, what charting
  packages do; pandas' own default is `first`)

To find which combination reproduces a number you're trying to match:

```bash
python -m mr.reconcile SPY 2026-09-04 --target 1.81 --target 2.19
```

It prints every combination for that session and names the closest cell for each
target. If no cell lands close, the gap is structural rather than cosmetic — a
different ATR period, a different index, or a different date — and no amount of
smoothing config will close it.

## What counts as a momentum stock

Membership follows the reference monitor's definition, re-evaluated from
scratch every session:

    common stock + ADR (no ETFs) · close >= $5
    · same-day dollar turnover >= $5m
    · gain over 63 sessions >= 20%

Two things about this are easy to get wrong.

**The quarterly test is absolute, not a rank.** An earlier version took the top
30% of the universe by 126-day return. A percentile always finds a top decile,
so in a falling market it keeps reporting "leaders" that are merely falling more
slowly. An absolute threshold empties out instead. A count that can reach zero is
the signal, and that is the whole point of the reading.

**Turnover is same-day, not a rolling average.** A 20- or 50-day average shifts
the base by a few dozen names.

There are no moving-average filters. Every threshold is env-overridable
(`MR_MLI_MIN_PRICE`, `MR_MLI_MIN_TURNOVER`, `MR_MLI_QUARTER_LOOKBACK`,
`MR_MLI_MIN_QUARTER_GAIN`).

### The corporate-action guard

Bars are cached unadjusted and split-corrected in memory from Polygon's splits
endpoint, so a split that endpoint has not published yet shows up as a price
cliff. On 2026-09-11 GOSS printed 0.16, 0.14, then 10.73 -- an unreported
reverse split reading as **+7,435% in one session**. One such name in a 416-name
equal-weight mean moved the MLI from +1.05% to +18.92%.

`MLI_MAX_DAILY_MOVE` (100% by default) drops these from the aggregate and logs
each one by name. The old `price >= $10` filter hid this class of bug by
accident, because the affected names trade in pennies until the split lands.

## The regime signal

Eight components each score −1, 0 or +1: SPY trend, QQQ trend, % > 50D, % > 20D,
10-day UP4:DN4 ratio, distribution day count, 10-session leadership return, net
new highs. The sum maps to a light:

- **GREEN** (≥ +3) — full size, press winners
- **YELLOW** (−1 to +2) — half size, best setups only
- **RED** (≤ −2) — no new risk, honour stops

Every component prints its own reason, so the light is always explainable rather
than a black box. Thresholds live in `mr/config.py`; change them there and both
outputs follow.

The ATR stretch reading is deliberately *not* part of the score. It governs where
you add, not whether you're allowed to — a green tape with SPY +6 ATR above its
50D EMA still says "don't chase", and folding that into one number would hide it.

## When a number looks wrong

```bash
python -m mr.doctor > doctor.txt
```

Reads the cache only, no API calls. It prints the cache depth, the reference
list broken down by type and primary exchange, **how many names each universe
filter removes one step at a time**, raw index OHLCV you can check against any
public source, any move beyond ±35% (almost always an unadjusted split), and the
latest breadth readings. A universe that should be ~2,000-2,600 names and isn't
will show exactly which filter ate it.

## Known limits

- **Survivorship in the historical rows.** Eligibility uses Polygon's list of
  *currently active* tickers, so names delisted during the lookback are missing
  from older rows. Today's reading is exact; rows from months back read very
  slightly cleaner than the tape actually was. Fixing it properly means storing a
  point-in-time ticker snapshot each day — `data/reference_*.csv` already writes
  one, so the fix is to read the snapshot nearest each date.
- **MLI membership is a definition, not a standard.** The reference monitor that
  inspired it does not publish its rule. The thresholds in `config.py` produce a
  basket in the same size range (roughly 400–600 names on a healthy tape); tune
  `MLI_RS_PERCENTILE` if you want it tighter or looser.
- **The S&P 500 column shows SPY**, not the index itself — Polygon indices need a
  separate entitlement.
- Grouped bars are cached **unadjusted** and split-adjusted in memory from the
  splits endpoint, so the cache never silently rots after a split.

## Layout

```
mr/config.py       every threshold, in one place
mr/data.py         Polygon client, caching, splits, universe reference
mr/metrics.py      breadth, leadership, ATR, distribution days, sector RS
mr/regime.py       the eight-component composite
mr/render_html.py  dashboard
mr/render_md.py    written review
mr/run.py          orchestration + CLI
mr/synthetic.py    generated market for testing without an API key
mr/reconcile.py    which ATR/EMA convention reproduces a given reading
mr/doctor.py       diagnostic dump when a number looks wrong
mr/screen.py       the watchlist screen — strength you can still buy
verify.py          independent re-implementations of every metric
NARRATIVE.md       style spec for the layer that rewrites the review in voice
setup.sh           one-shot macOS installer (venv, key, cache, schedule)
run_daily.sh       what the scheduler calls; safe to run by hand
launchd.plist.template  the schedule setup.sh installs
```
