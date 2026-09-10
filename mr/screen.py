"""Watchlist screen — the "tickers pasted for your watchlists" section.

Ranks the eligible universe on relative strength and trend quality, then filters
for names that are actually actionable rather than merely strong: extended names
are dropped, because a stock 8 ATR above its 20-day is a chase, not a setup.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from . import metrics as M

# Screen thresholds — deliberately separate from the MLI membership rules, which
# measure the market's leadership; these measure what is buyable tomorrow.
MIN_PRICE = 10.0
MIN_DOLLAR_VOL = 20_000_000
RS_LOOKBACK = 63            # ~3 months
TIGHT_LOOKBACK = 10         # window for the contraction test
MAX_ATR_EXTENSION = 4.0     # ATRs above the 20-day MA before it is a chase
MAX_NAMES = 12


def candidates(panel: dict[str, pd.DataFrame], ref: pd.DataFrame,
               limit: int = MAX_NAMES) -> pd.DataFrame:
    """Names in a buyable posture, best relative strength first."""
    close, volume = panel["close"], panel["volume"]
    high, low = panel["high"], panel["low"]
    elig = M.eligibility(panel, ref)

    n = len(close)
    if n < 25:
        return pd.DataFrame()

    last = close.index[-1]
    ma20 = close.rolling(20, min_periods=20).mean()
    ma50 = close.rolling(50, min_periods=min(50, n)).mean()
    dv = (close * volume).rolling(20, min_periods=10).mean()

    # true range per name, then a Wilder-ish ATR via a simple mean (per-name
    # recursion across 2,000 columns is not worth the accuracy here)
    prev = close.shift(1)
    tr = pd.concat(
        [(high - low).stack(), (high - prev).abs().stack(), (low - prev).abs().stack()],
        axis=1).max(axis=1).unstack()
    atr = tr.rolling(14, min_periods=10).mean()

    rs = close.pct_change(min(RS_LOOKBACK, n - 1)) * 100
    ret1 = close.pct_change() * 100
    ret5 = close.pct_change(5) * 100

    # contraction: the last 10 sessions' range as a share of the ATR. Tight bases
    # score low; a name that just ran vertically scores high.
    rng = (high.rolling(TIGHT_LOOKBACK).max() - low.rolling(TIGHT_LOOKBACK).min())
    tightness = rng / atr.replace(0, np.nan)

    extension = (close - ma20) / atr.replace(0, np.nan)

    ok = (
        elig.loc[last]
        & (close.loc[last] >= MIN_PRICE)
        & (dv.loc[last] >= MIN_DOLLAR_VOL)
        & (close.loc[last] > ma20.loc[last])
        & (close.loc[last] > ma50.loc[last])
        & (extension.loc[last] <= MAX_ATR_EXTENSION)
        & rs.loc[last].notna()
    ).fillna(False)

    names = [t for t in close.columns if bool(ok.get(t, False))]
    if not names:
        return pd.DataFrame()

    out = pd.DataFrame({
        "ticker": names,
        "close": close.loc[last, names].values,
        "day": ret1.loc[last, names].values,
        "week": ret5.loc[last, names].values,
        "rs": rs.loc[last, names].values,
        "ext_atr": extension.loc[last, names].values,
        "tight": tightness.loc[last, names].values,
        "dollar_vol": dv.loc[last, names].values,
    })
    # rank on relative strength, then reward tightness — a strong name resting is
    # a better entry than a strong name in mid-air
    out["rs_rank"] = out["rs"].rank(pct=True)
    out["tight_rank"] = 1 - out["tight"].rank(pct=True)
    out["score"] = out["rs_rank"] * 0.7 + out["tight_rank"] * 0.3
    out = out.sort_values("score", ascending=False).head(limit).reset_index(drop=True)
    return out


def describe(row: pd.Series) -> str:
    """One-line characterisation of a candidate's posture."""
    bits = []
    if row["ext_atr"] <= 1.0:
        bits.append("sitting on its 20-day")
    elif row["ext_atr"] <= 2.5:
        bits.append("modestly extended")
    else:
        bits.append(f"{row['ext_atr']:.1f} ATR above the 20-day")
    if pd.notna(row.get("tight")) and row["tight"] <= 4:
        bits.append("range has contracted")
    return ", ".join(bits)
