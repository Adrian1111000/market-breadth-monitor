"""Polygon data access: grouped daily bars, ticker reference, split adjustment.

Design notes
------------
* Grouped daily aggregates give the whole US tape in one request per session, so
  a full-universe breadth run costs 1 API call per day instead of ~2,300.
* Bars are cached **unadjusted** on disk. Split adjustment is applied in memory
  from the splits reference endpoint, so the cache never goes stale after a
  split (which is the usual silent-corruption bug with adjusted=true caching).
* Everything is keyed by trading date, so a re-run for an old date is free.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Iterable

import numpy as np
import pandas as pd
import requests

from . import config as C

log = logging.getLogger("mr.data")

_SESSION = requests.Session()
_LAST_CALL: list[float] = []

# Discovered at runtime. If the account turns out to be rate limited, the client
# learns the limit from the first 429 instead of burning its retry budget on
# every subsequent call — a free-tier key then just runs slowly rather than
# failing the whole build.
_AUTO_LIMIT = 0


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def _effective_limit() -> int:
    return max(C.RATE_LIMIT_PER_MIN, _AUTO_LIMIT)


def _throttle() -> None:
    """Keep under the effective requests-per-rolling-minute limit."""
    limit = _effective_limit()
    if limit <= 0:
        return
    now = time.time()
    _LAST_CALL[:] = [t for t in _LAST_CALL if now - t < 60.0]
    if len(_LAST_CALL) >= limit:
        sleep_for = 60.0 - (now - _LAST_CALL[0]) + 0.25
        if sleep_for > 0:
            time.sleep(sleep_for)
    _LAST_CALL.append(time.time())


def _get(path: str, params: dict | None = None, *, retries: int = 8) -> dict:
    global _AUTO_LIMIT
    if not C.POLYGON_API_KEY:
        raise RuntimeError(
            "POLYGON_API_KEY is not set. Export it before running, e.g.\n"
            "  export POLYGON_API_KEY=your_key_here"
        )
    params = dict(params or {})
    params["apiKey"] = C.POLYGON_API_KEY
    url = path if path.startswith("http") else f"{C.POLYGON_BASE}{path}"

    for attempt in range(retries):
        _throttle()
        try:
            r = _SESSION.get(url, params=params, timeout=60)
        except requests.RequestException as exc:
            if attempt == retries - 1:
                raise
            log.warning("request error %s, retrying", exc)
            time.sleep(2 ** attempt)
            continue

        if r.status_code == 429:
            if _AUTO_LIMIT == 0 and C.RATE_LIMIT_PER_MIN == 0:
                _AUTO_LIMIT = 5
                log.warning(
                    "Polygon returned 429 — this key is rate limited. Throttling to "
                    "%d requests/minute for the rest of this run. The build will take "
                    "about an hour; it is cached and resumable. Set "
                    "MR_RATE_LIMIT_PER_MIN=5 in .env to skip this message next time.",
                    _AUTO_LIMIT)
            wait = float(r.headers.get("Retry-After") or min(60, 5 * (attempt + 1)))
            time.sleep(wait)
            continue
        if r.status_code == 403:
            raise RuntimeError(
                f"Polygon returned 403 for {url.split('?')[0]} — your plan does "
                "not include this endpoint."
            )
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"exhausted retries for {url}")


def _paginate(path: str, params: dict) -> Iterable[dict]:
    payload = _get(path, params)
    while True:
        yield from payload.get("results", []) or []
        nxt = payload.get("next_url")
        if not nxt:
            return
        payload = _get(nxt)


# --------------------------------------------------------------------------- #
# Grouped daily bars
# --------------------------------------------------------------------------- #

def _cache_path(d: date):
    return C.CACHE_DIR / f"{d.isoformat()}.json.gz"


def fetch_grouped(d: date, *, force: bool = False) -> list[dict] | None:
    """Unadjusted OHLCV for every US ticker on one session.

    Returns None for non-trading days. Result is cached on disk.
    """
    path = _cache_path(d)
    if path.exists() and not force:
        with gzip.open(path, "rt") as fh:
            cached = json.load(fh)
        return cached or None

    payload = _get(
        f"/v2/aggs/grouped/locale/us/market/stocks/{d.isoformat()}",
        {"adjusted": "false", "include_otc": "false"},
    )
    results = payload.get("results") or []
    with gzip.open(path, "wt") as fh:
        json.dump(results, fh)
    return results or None


def load_bars(end: date, calendar_days: int = C.LOOKBACK_CALENDAR_DAYS,
              max_downloads: int | None = None) -> pd.DataFrame:
    """Long-format unadjusted bars for the lookback window.

    Downloads run newest-first, so if ``max_downloads`` cuts the run short the
    cache still holds the most recent sessions — a partial cache is a shallower
    history, not a hole in the middle. Columns: date, ticker, OHLCV.
    """
    cap = C.MAX_DOWNLOADS if max_downloads is None else max_downloads
    unlimited = cap < 0
    start = end - timedelta(days=calendar_days)
    weekdays = [start + timedelta(days=i)
                for i in range((end - start).days + 1)
                if (start + timedelta(days=i)).weekday() < 5]

    missing = [d for d in weekdays if not _cache_path(d).exists()]
    todo = len(missing) if unlimited else min(len(missing), max(cap, 0))
    if missing and not todo:
        log.info("%d sessions are not cached; downloads are capped at 0 for this "
                 "run, so the review is built from cache only.", len(missing))
    if todo:
        log.info("%d of %d sessions still to download%s. Cached and resumable — "
                 "Ctrl-C loses nothing.", len(missing), len(weekdays),
                 f", fetching {todo} this run" if todo < len(missing) else "")

    # newest first
    downloaded = 0
    t0 = time.time()
    for day in sorted(missing, reverse=True):
        if downloaded >= todo:
            break
        fetch_grouped(day)
        downloaded += 1
        if downloaded % 10 == 0 or downloaded == todo:
            rate = downloaded / max(time.time() - t0, 1e-6)
            eta = (todo - downloaded) / rate if rate > 0 else 0
            log.info("  %d/%d downloaded  (%.1f/min, about %s left)",
                     downloaded, todo, rate * 60,
                     f"{eta/60:.0f} min" if eta >= 60 else f"{eta:.0f} sec")

    frames = []
    for day in weekdays:
        if not _cache_path(day).exists():
            continue
        rows = fetch_grouped(day)
        if rows:
            df = pd.DataFrame(rows)
            df["date"] = pd.Timestamp(day)
            frames.append(df)

    if not frames:
        raise RuntimeError(
            f"no cached sessions up to {end}. The first run needs at least a few "
            "downloads to succeed — check the API key and the rate limit.")

    out = pd.concat(frames, ignore_index=True)
    out = out.rename(
        columns={"T": "ticker", "o": "open", "h": "high", "l": "low",
                 "c": "close", "v": "volume", "n": "transactions"}
    )
    keep = ["date", "ticker", "open", "high", "low", "close", "volume"]
    out = out[keep]
    out["ticker"] = out["ticker"].astype(str)
    log.info("loaded %d sessions, %d rows", out["date"].nunique(), len(out))
    return out


# --------------------------------------------------------------------------- #
# Split adjustment
# --------------------------------------------------------------------------- #

def splits_cached(as_of: date, since: date, *, offline: bool = False) -> pd.DataFrame:
    """Splits for the window, cached and reusable, and skippable when offline.

    A cache-only run must make no network calls at all, so if there is no usable
    snapshot it returns an empty frame and says what that costs: prices stay
    unadjusted across any split inside the window.
    """
    path = C.DATA_DIR / f"splits_{as_of.isoformat()}.csv"
    if path.exists():
        df = pd.read_csv(path, parse_dates=["execution_date"])
        return df

    newest, newest_age = None, None
    for snap in C.DATA_DIR.glob("splits_*.csv"):
        try:
            snap_date = date.fromisoformat(snap.stem.removeprefix("splits_"))
        except ValueError:
            continue
        age = (as_of - snap_date).days
        if 0 <= age <= C.REFERENCE_MAX_AGE_DAYS and (newest_age is None or age < newest_age):
            newest, newest_age = snap, age
    if newest is not None:
        return pd.read_csv(newest, parse_dates=["execution_date"])

    if offline:
        log.warning("cache-only run and no splits snapshot on disk — prices are "
                    "left unadjusted. Any stock that split inside the window will "
                    "show a false gap. Run once with downloads enabled to fix.")
        return pd.DataFrame(columns=["ticker", "execution_date", "ratio"])

    df = fetch_splits(since)
    df.to_csv(path, index=False)
    for old in sorted(C.DATA_DIR.glob("splits_*.csv"))[:-2]:
        old.unlink(missing_ok=True)
    return df


def fetch_splits(since: date) -> pd.DataFrame:
    rows = list(_paginate(
        "/v3/reference/splits",
        {"execution_date.gte": since.isoformat(), "limit": 1000, "order": "asc"},
    ))
    if not rows:
        return pd.DataFrame(columns=["ticker", "execution_date", "ratio"])
    df = pd.DataFrame(rows)
    df["execution_date"] = pd.to_datetime(df["execution_date"])
    # ratio to multiply a pre-split price by, e.g. 4-for-1 -> 0.25
    df["ratio"] = df["split_from"].astype(float) / df["split_to"].astype(float)
    return df[["ticker", "execution_date", "ratio"]]


def apply_splits(bars: pd.DataFrame, splits: pd.DataFrame) -> pd.DataFrame:
    """Back-adjust cached unadjusted bars so history is continuous.

    A bar dated d is scaled by the product of the ratios of every split whose
    execution date is strictly after d. Bars on or after the execution date are
    already post-split and must not be touched — hence allow_exact_matches=False.

    Vectorised with merge_asof rather than a mask per split: the naive version is
    O(splits x rows), which on 1,000 splits and 4.5m rows is minutes of work.
    """
    if splits.empty or bars.empty:
        return bars

    sp = splits.dropna(subset=["ticker", "execution_date", "ratio"]).copy()
    sp = sp[sp["ratio"] > 0]
    if sp.empty:
        return bars
    sp["execution_date"] = pd.to_datetime(sp["execution_date"])

    # factor at split e = product of ratios of every split at e or later, so a
    # bar before e picks up the whole chain of subsequent splits
    sp = sp.sort_values(["ticker", "execution_date"], ascending=[True, False])
    sp["factor"] = sp.groupby("ticker")["ratio"].cumprod()
    # merge_asof requires both sides sorted by the *on* key globally, not just
    # within the `by` group — sorting by ticker first raises "keys must be sorted".
    sp = sp.sort_values("execution_date", kind="mergesort")

    order = bars.index
    left = bars[["ticker", "date"]].copy()
    left["_pos"] = np.arange(len(left))
    left = left.sort_values("date", kind="mergesort")

    # pandas 3.0 requires both merge_asof keys to carry the SAME datetime
    # resolution and raises MergeError otherwise; pandas 2.x silently coerced.
    # The two sides arrive with different units -- bar dates are built from
    # datetime.date objects while execution dates are parsed from ISO strings --
    # so pin both to nanoseconds, which is valid on 2.x and 3.x alike.
    right = sp[["ticker", "execution_date", "factor"]].copy()
    left["date"] = left["date"].astype("datetime64[ns]")
    right["execution_date"] = right["execution_date"].astype("datetime64[ns]")

    merged = pd.merge_asof(
        left, right,
        left_on="date", right_on="execution_date", by="ticker",
        direction="forward", allow_exact_matches=False,
    )
    factor = merged.sort_values("_pos")["factor"].to_numpy()
    factor = np.where(np.isnan(factor), 1.0, factor)

    if np.allclose(factor, 1.0):
        return bars

    out = bars.copy()
    for col in ("open", "high", "low", "close"):
        if col in out:
            out[col] = out[col].to_numpy() * factor
    if "volume" in out:
        out["volume"] = out["volume"].to_numpy() / factor
    out.index = order
    n = int((factor != 1.0).sum())
    log.info("split-adjusted %d bars across %d tickers",
             n, sp["ticker"].nunique())
    return out


# --------------------------------------------------------------------------- #
# Ticker reference / universe
# --------------------------------------------------------------------------- #

def fetch_reference() -> pd.DataFrame:
    """Active US equities with their Polygon type and primary exchange."""
    rows: list[dict] = []
    for t in C.UNIVERSE_TYPES:
        rows.extend(_paginate(
            "/v3/reference/tickers",
            {"market": "stocks", "type": t, "active": "true", "limit": 1000},
        ))
    if not rows:
        return pd.DataFrame(columns=["ticker", "type", "primary_exchange", "name"])
    df = pd.DataFrame(rows)
    for col in ("ticker", "type", "primary_exchange", "name"):
        if col not in df.columns:
            df[col] = ""
    return df[["ticker", "type", "primary_exchange", "name"]].drop_duplicates("ticker")


def reference_cached(as_of: date, *, offline: bool = False) -> pd.DataFrame:
    """Ticker reference, reusing any snapshot younger than REFERENCE_MAX_AGE_DAYS.

    This list costs ~13 paginated calls. On a 5-per-minute plan that is more
    expensive than the price data, and the membership of "US common stocks and
    ADRs" does not change materially inside a week.
    """
    path = C.DATA_DIR / f"reference_{as_of.isoformat()}.csv"
    if path.exists():
        return pd.read_csv(path)

    newest, newest_age = None, None
    for snap in C.DATA_DIR.glob("reference_*.csv"):
        try:
            snap_date = date.fromisoformat(snap.stem.removeprefix("reference_"))
        except ValueError:
            continue
        age = (as_of - snap_date).days
        if 0 <= age <= C.REFERENCE_MAX_AGE_DAYS and (newest_age is None or age < newest_age):
            newest, newest_age = snap, age
    if newest is not None:
        log.info("reusing ticker reference from %s (%d day%s old, saves ~13 calls)",
                 newest.stem.removeprefix("reference_"), newest_age,
                 "" if newest_age == 1 else "s")
        return pd.read_csv(newest)

    if offline:
        raise RuntimeError(
            "This is a cache-only run (--max-downloads 0) but there is no ticker "
            "reference snapshot on disk, and the universe cannot be defined "
            "without one.\n"
            "  Fix: run once with downloads enabled — 'python -m mr.run' — which "
            "costs about 13 calls, then cache-only runs work offline for a week.")

    log.info("refreshing the ticker reference list (~13 calls)")
    df = fetch_reference()
    df.to_csv(path, index=False)
    for old in sorted(C.DATA_DIR.glob("reference_*.csv"))[:-2]:
        old.unlink(missing_ok=True)
    return df


# --------------------------------------------------------------------------- #
# Panel construction
# --------------------------------------------------------------------------- #

def build_panel(bars: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Pivot long bars into wide date x ticker frames, one per field."""
    panel = {}
    for field in ("open", "high", "low", "close", "volume"):
        wide = bars.pivot_table(index="date", columns="ticker", values=field,
                                aggfunc="last")
        panel[field] = wide.sort_index()
    return panel


def eligible_mask(panel: dict[str, pd.DataFrame], ref: pd.DataFrame) -> pd.Series:
    """Boolean per ticker: is it in the monitored universe on the latest session?"""
    close = panel["close"]
    volume = panel["volume"]
    last_close = close.iloc[-1]
    last_vol = volume.iloc[-1]

    ok_ref = pd.Series(False, index=close.columns)
    allowed = set(ref["ticker"])
    ok_ref.loc[[t for t in close.columns if t in allowed]] = True

    if "primary_exchange" in ref.columns:
        exch = ref.set_index("ticker")["primary_exchange"]
        on_exch = pd.Series(
            [exch.get(t, "") in C.KEEP_EXCHANGES for t in close.columns],
            index=close.columns,
        )
    else:
        on_exch = pd.Series(True, index=close.columns)

    enough_history = close.notna().sum() >= C.MIN_HISTORY_BARS

    return (
        ok_ref
        & on_exch
        & enough_history
        & (last_close >= C.MIN_CLOSE)
        & (last_vol >= C.MIN_VOLUME)
    ).fillna(False)


def latest_cached_date() -> date | None:
    """Newest session already on disk with actual rows in it."""
    best = None
    for path in C.CACHE_DIR.glob("*.json.gz"):
        try:
            d = date.fromisoformat(path.name.removesuffix(".json.gz"))
        except ValueError:
            continue
        if best is not None and d <= best:
            continue
        with gzip.open(path, "rt") as fh:
            if json.load(fh):          # skip holidays cached as an empty list
                best = d
    return best


def latest_trading_date(today: date | None = None, *, offline: bool = False) -> date:
    """Most recent session that has grouped data available.

    An offline run must not probe the API for it, so it reads the newest session
    already cached instead.
    """
    if offline:
        d = latest_cached_date()
        if d is None:
            raise RuntimeError(
                "This is a cache-only run (--max-downloads 0) but no sessions are "
                "cached yet, so there is no data to build from.\n"
                "  Fix: run 'python -m mr.run' once with downloads enabled.")
        return d

    d = today or datetime.utcnow().date()
    for _ in range(10):
        if d.weekday() < 5:
            try:
                if fetch_grouped(d):
                    return d
            except Exception as exc:  # noqa: BLE001 - fall back to the prior day
                log.debug("no data for %s (%s)", d, exc)
        d -= timedelta(days=1)
    raise RuntimeError("could not find a recent trading session")
