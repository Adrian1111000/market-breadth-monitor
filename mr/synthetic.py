"""Synthetic market generator — used to exercise the pipeline without an API key.

Produces a panel with the same shape as the Polygon path: a market factor, sector
factors, idiosyncratic noise, a deliberate regime shift late in the window, and
index/sector ETFs built as baskets so their breadth relationship is coherent.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C


def make(n_names: int = 2400, n_days: int = 420, seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp("2026-09-04"), periods=n_days)

    # market factor: long grind up, a sharp break, then a partial repair
    drift = np.concatenate([
        np.full(int(n_days * 0.55), 0.0007),
        np.full(int(n_days * 0.20), -0.0016),
        np.full(n_days - int(n_days * 0.55) - int(n_days * 0.20), 0.0005),
    ])
    mkt = drift + rng.normal(0, 0.0085, n_days)

    n_sectors = 11
    sec_id = rng.integers(0, n_sectors, n_names)
    sec_ret = rng.normal(0, 0.006, (n_days, n_sectors))
    beta = rng.uniform(0.5, 1.9, n_names)
    idio = rng.normal(0, 0.018, (n_days, n_names))

    rets = mkt[:, None] * beta[None, :] + sec_ret[:, sec_id] + idio
    # a handful of true leaders with persistent drift
    leaders = rng.choice(n_names, size=min(380, max(1, n_names // 6)), replace=False)
    rets[:, leaders] += 0.0011

    start = rng.uniform(6, 260, n_names)
    close = start * np.exp(np.cumsum(rets, axis=0))

    tickers = [f"SYN{i:04d}" for i in range(n_names)]
    close_df = pd.DataFrame(close, index=dates, columns=tickers)

    # index / sector ETFs as equal-weight baskets of their constituents
    extra = {}
    extra["SPY"] = close_df.mean(axis=1) / close_df.mean(axis=1).iloc[0] * 640.0
    tech = [t for t, s in zip(tickers, sec_id) if s == 0]
    extra["QQQ"] = close_df[tech].mean(axis=1) / close_df[tech].mean(axis=1).iloc[0] * 560.0
    small = [t for t, b in zip(tickers, beta) if b > 1.4]
    extra["IWM"] = close_df[small].mean(axis=1) / close_df[small].mean(axis=1).iloc[0] * 245.0
    extra["MDY"] = (extra["SPY"] * 0.55 + extra["IWM"] * 0.45)

    etf_names = list(C.SECTOR_ETFS) + list(C.THEME_ETFS)
    for k, etf in enumerate(etf_names):
        members = [t for t, s in zip(tickers, sec_id) if s == k % n_sectors]
        base = close_df[members].mean(axis=1)
        tilt = np.exp(np.cumsum(rng.normal(0.0002 * ((k % 5) - 2), 0.004, n_days)))
        extra[etf] = base / base.iloc[0] * (40 + 9 * k) * tilt

    for k, v in extra.items():
        close_df[k] = v

    # OHLCV consistent with the closes
    all_t = list(close_df.columns)
    prev = close_df.shift(1).fillna(close_df.iloc[0])
    noise = rng.uniform(0.002, 0.02, close_df.shape)
    high = np.maximum(close_df.values, prev.values) * (1 + noise)
    low = np.minimum(close_df.values, prev.values) * (1 - noise)
    open_ = prev.values * (1 + rng.normal(0, 0.004, close_df.shape))
    vol = np.abs(rng.lognormal(13.4, 1.15, close_df.shape)).round()

    long = []
    for field, arr in (("open", open_), ("high", high), ("low", low),
                       ("close", close_df.values), ("volume", vol)):
        long.append(pd.DataFrame(arr, index=dates, columns=all_t)
                    .stack().rename(field))
    bars = pd.concat(long, axis=1).reset_index()
    bars.columns = ["date", "ticker", "open", "high", "low", "close", "volume"]

    # ETFs stay out of the reference list, exactly as in the Polygon path, so
    # they are available for index maths but never counted in breadth.
    stocks = [t for t in all_t if t.startswith("SYN")]
    ref = pd.DataFrame({
        "ticker": stocks,
        "type": ["CS"] * len(stocks),
        "primary_exchange": ["XNAS"] * len(stocks),
        "name": stocks,
    })
    return bars, ref
