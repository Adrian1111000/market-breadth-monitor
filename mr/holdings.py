"""Top holdings for the tracked ETFs.

Polygon has no ETF constituent endpoint -- the data is not in its API at any
tier -- so this is the one place the project reaches outside Polygon for equity
data, and it uses yfinance's fund-holdings field.

Two rules govern this module:

* It never raises. Holdings are an enrichment on top of the breadth report, and
  breadth is the production output. Every failure path returns whatever is
  already cached, or nothing, and lets the run continue.

* It caches hard. Fund weights drift by fractions of a percent from one session
  to the next, so refetching twenty funds every morning would spend twenty
  network calls to change the third decimal place. The cache follows the same
  convention as the ticker reference: reuse anything younger than
  HOLDINGS_MAX_AGE_DAYS.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime

from . import config as C

log = logging.getLogger("mr.holdings")

CACHE = C.DATA_DIR / "holdings.json"


def _load_cache() -> dict:
    if not CACHE.exists():
        return {}
    try:
        return json.loads(CACHE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("holdings cache unreadable (%s); refetching", exc)
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE.write_text(json.dumps(cache, indent=1))
    except OSError as exc:
        log.warning("could not write holdings cache: %s", exc)


def _age_days(stamp: str) -> float:
    try:
        return (date.today() - datetime.fromisoformat(stamp).date()).days
    except (ValueError, TypeError):
        return 1e9


def _fetch_one(ticker: str, top_n: int) -> list[dict] | None:
    """Top-n holdings for one fund, or None if the fund did not return any.

    Weights arrive as fractions (0.101 = 10.1%) and are stored as percent, so
    every consumer downstream reads the same unit.
    """
    import yfinance as yf                      # imported late: see module docstring

    df = yf.Ticker(ticker).funds_data.top_holdings
    if df is None or df.empty:
        return None

    rows = []
    for symbol, r in df.head(top_n).iterrows():
        w = r.get("Holding Percent")
        rows.append({
            "symbol": str(symbol),
            "name": str(r.get("Name", "")).strip(),
            "weight": round(float(w) * 100.0, 2) if w is not None else None,
        })
    return rows or None


def top_holdings(tickers, top_n: int | None = None,
                 max_age_days: int | None = None, *, force: bool = False) -> dict:
    """{ticker: [{symbol, name, weight}, ...]} for every fund we could resolve.

    Funds that fail are simply absent from the result. A partial answer is worth
    more than none, so one dead ticker does not discard the other nineteen.
    """
    top_n = C.HOLDINGS_TOP_N if top_n is None else top_n
    max_age = C.HOLDINGS_MAX_AGE_DAYS if max_age_days is None else max_age_days

    cache = _load_cache()
    stale = [t for t in tickers
             if force or t not in cache or _age_days(cache[t].get("fetched", "")) > max_age]

    if stale:
        try:
            import yfinance                    # noqa: F401  -- availability probe
        except ImportError:
            log.warning("yfinance is not installed, so ETF holdings are skipped. "
                        "Install it with 'pip install yfinance' to enable them; "
                        "everything else in the review is unaffected.")
            stale = []

    if stale:
        log.info("holdings: fetching %d of %d funds (cache covers the rest)",
                 len(stale), len(tickers))
        today = date.today().isoformat()
        fetched = 0
        for t in stale:
            try:
                rows = _fetch_one(t, top_n)
            except Exception as exc:                      # noqa: BLE001
                # Network error, schema change, delisting, rate limit -- the
                # cause does not matter here. Keep any previous answer for this
                # fund and carry on to the next.
                log.warning("holdings: %s failed (%s: %s)",
                            t, type(exc).__name__, str(exc)[:90])
                continue
            if rows:
                cache[t] = {"fetched": today, "rows": rows}
                fetched += 1
        if fetched:
            _save_cache(cache)
        log.info("holdings: %d fetched, %d total in cache", fetched, len(cache))

    out = {}
    for t in tickers:
        entry = cache.get(t)
        if entry and entry.get("rows"):
            out[t] = entry["rows"][:top_n]
    return out
