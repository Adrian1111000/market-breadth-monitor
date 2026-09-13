"""Breadth, momentum and trend metrics computed off a wide price panel.

All functions take the panel produced by ``data.build_panel`` (date x ticker
frames) and return either a full historical Series/DataFrame or a scalar for the
latest session, so the same code produces today's reading and the rolling table.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import config as C

log = logging.getLogger("mr.metrics")


# --------------------------------------------------------------------------- #
# Universe eligibility, per session
# --------------------------------------------------------------------------- #

def eligibility(panel: dict[str, pd.DataFrame], ref: pd.DataFrame) -> pd.DataFrame:
    """date x ticker boolean: was this name in the monitored universe that day?

    Note: ``ref`` is a snapshot of currently-active tickers, so names that have
    since delisted are absent from the historical rows. Today's reading is exact;
    older rows carry a mild survivorship tilt. See README.
    """
    close, volume = panel["close"], panel["volume"]

    allowed = set(ref["ticker"])
    # The exchange test applies only when an allowlist is configured. With none
    # set (the default) membership is decided by the reference list alone.
    if C.KEEP_EXCHANGES and "primary_exchange" in ref:
        exch = ref.set_index("ticker")["primary_exchange"].to_dict()
        col_ok = pd.Series(
            [(t in allowed) and exch.get(t, "") in C.KEEP_EXCHANGES
             for t in close.columns],
            index=close.columns)
    else:
        col_ok = pd.Series([t in allowed for t in close.columns],
                           index=close.columns)

    # A name is "seasoned" once it has traded for enough of the window. With a
    # shallow cache the absolute bar count is a cliff — 59 cached sessions
    # against a 60-bar requirement makes the universe empty on every row and
    # every downstream metric undefined — so the requirement scales down to the
    # depth actually available.
    n_sessions = len(close)
    eff_min = min(C.MIN_HISTORY_BARS, max(5, int(n_sessions * 0.8)))
    if eff_min < C.MIN_HISTORY_BARS:
        log.warning(
            "only %d sessions cached: relaxing the seasoning requirement from %d "
            "bars to %d so the universe is not empty. Deepen the cache with "
            "'python -m mr.run' for a full-depth reading.",
            n_sessions, C.MIN_HISTORY_BARS, eff_min)
    seasoned = close.notna().cumsum() >= eff_min
    elig = (
        seasoned
        & (close >= C.MIN_CLOSE)
        & (volume >= C.MIN_VOLUME)
        & col_ok  # broadcasts across rows
    )
    return elig.fillna(False)


# --------------------------------------------------------------------------- #
# Core breadth
# --------------------------------------------------------------------------- #

def breadth(panel: dict[str, pd.DataFrame], elig: pd.DataFrame) -> pd.DataFrame:
    """UP4/DN4 counts, % above the 20/50/200 day MAs, net new 52-week highs."""
    close = panel["close"]
    ret = close.pct_change() * 100.0

    up4 = ((ret >= C.MOVE_PCT) & elig).sum(axis=1)
    dn4 = ((ret <= -C.MOVE_PCT) & elig).sum(axis=1)

    out = pd.DataFrame({"up4": up4, "dn4": dn4})
    out["universe"] = elig.sum(axis=1)

    for win, name in ((20, "pct_ma20"), (50, "pct_ma50"), (200, "pct_ma200")):
        ma = close.rolling(win, min_periods=win).mean()
        above = ((close > ma) & elig).sum(axis=1)
        have = (ma.notna() & elig).sum(axis=1)
        out[name] = (above / have.replace(0, np.nan)) * 100.0

    hi = close.rolling(252, min_periods=126).max()
    lo = close.rolling(252, min_periods=126).min()
    out["new_highs"] = ((close >= hi) & elig).sum(axis=1)
    out["new_lows"] = ((close <= lo) & elig).sum(axis=1)
    out["net_hl"] = out["new_highs"] - out["new_lows"]

    # 5- and 10-day breadth thrust sums (Stockbee-style momentum burst reading)
    out["up4_5d"] = out["up4"].rolling(5).sum()
    out["dn4_5d"] = out["dn4"].rolling(5).sum()
    out["ratio_10d"] = (
        out["up4"].rolling(10).sum() / out["dn4"].rolling(10).sum().replace(0, np.nan)
    )
    return out


# --------------------------------------------------------------------------- #
# Leadership index (MLI)
# --------------------------------------------------------------------------- #

def leadership(panel: dict[str, pd.DataFrame], elig: pd.DataFrame) -> pd.DataFrame:
    """Equal-weight index of the names in a momentum posture.

    Membership, re-evaluated independently on every session:
      eligible universe (common stock + ADR, no ETFs, close >= $5)
      · close >= MLI_MIN_PRICE
      · same-day dollar turnover >= MLI_MIN_TURNOVER
      · gain over MLI_QUARTER_LOOKBACK sessions >= MLI_MIN_QUARTER_GAIN

    There is no state carried between days. `member` is a full date x ticker
    matrix and each condition is evaluated per row, so the pool is rebuilt from
    scratch every session and a name drops out the day it stops qualifying.

    The quarterly-gain test is an ABSOLUTE threshold, not a percentile rank
    within the universe. The distinction matters most exactly when the reading
    matters most: a rank always finds a top decile, so in a falling market it
    keeps reporting "leaders" that are merely falling more slowly, while an
    absolute gain empties out. A count that can reach zero is the point.
    """
    close, volume = panel["close"], panel["volume"]
    ret = close.pct_change()

    turnover = close * volume
    quarter_gain = close.pct_change(C.MLI_QUARTER_LOOKBACK) * 100.0

    member = (
        elig
        & (close >= C.MLI_MIN_PRICE)
        & (turnover >= C.MLI_MIN_TURNOVER)
        & (quarter_gain >= C.MLI_MIN_QUARTER_GAIN)
    ).fillna(False)

    # Drop suspected unadjusted corporate actions before averaging. See
    # config.MLI_MAX_DAILY_MOVE for why this guard has to exist.
    artifact = member & (ret.abs() * 100.0 > C.MLI_MAX_DAILY_MOVE)
    if artifact.to_numpy().any():
        last = artifact.index[-1]
        for tkr in artifact.columns[artifact.loc[last]]:
            log.warning(
                "MLI: dropping %s on %s, %+.0f%% in one session -- suspected "
                "unadjusted corporate action, not a market move.",
                tkr, last.date(), float(ret.loc[last, tkr]) * 100.0)
    member = member & ~artifact

    n = member.sum(axis=1)
    daily = ret.where(member)
    out = pd.DataFrame({
        "mli_n": n,
        "mli_pct": daily.mean(axis=1) * 100.0,
        "mli_rising": (ret > 0).where(member).mean(axis=1) * 100.0,
    })
    out.loc[n == 0, ["mli_pct", "mli_rising"]] = np.nan
    # cumulative leadership curve, rebased to 100 at the start of the window
    out["mli_index"] = (1.0 + out["mli_pct"].fillna(0) / 100.0).cumprod() * 100.0
    out["mli_10d"] = out["mli_pct"].rolling(10).sum()
    return out


# --------------------------------------------------------------------------- #
# Index trend / stretch
# --------------------------------------------------------------------------- #

def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    return pd.concat([high - low, (high - prev).abs(), (low - prev).abs()],
                     axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = C.ATR_PERIOD, method: str | None = None) -> pd.Series:
    """14-day ATR under the configured smoothing.

    The method visibly moves the "distance from the 50D EMA" reading, so it is a
    knob rather than a hard-coded choice — see config.ATR_METHOD.
    """
    tr = true_range(high, low, close)
    m = (method or C.ATR_METHOD).lower()
    if m == "sma":
        return tr.rolling(period, min_periods=period).mean()
    if m == "ema":
        return tr.ewm(span=period, adjust=False, min_periods=period).mean()
    if m == "wilder":
        return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    raise ValueError(f"unknown ATR method {m!r} (use wilder, sma or ema)")


def ema(series: pd.Series, span: int, seed: str | None = None) -> pd.Series:
    """EMA with a selectable seed.

    Charting packages usually seed the first value from an ``span``-bar SMA;
    pandas seeds from the first observation. Over a 50-bar span the two converge
    within a few hundred bars, but on a short history they differ visibly.
    """
    s = (seed or C.EMA_SEED).lower()
    if s == "first":
        return series.ewm(span=span, adjust=False).mean()
    if s != "sma":
        raise ValueError(f"unknown EMA seed {s!r} (use sma or first)")
    out = series.copy().astype(float)
    if len(out) < span:
        return series.ewm(span=span, adjust=False).mean()
    seed_val = series.iloc[:span].mean()
    out.iloc[:span - 1] = np.nan
    out.iloc[span - 1] = seed_val
    k = 2.0 / (span + 1.0)
    vals = out.to_numpy(copy=True)
    src = series.to_numpy()
    for i in range(span, len(vals)):
        vals[i] = src[i] * k + vals[i - 1] * (1 - k)
    return pd.Series(vals, index=series.index)


def index_frame(panel: dict[str, pd.DataFrame], ticker: str) -> pd.DataFrame | None:
    """OHLCV + derived trend columns for one index ETF."""
    if ticker not in panel["close"].columns:
        return None
    df = pd.DataFrame({
        f: panel[f][ticker] for f in ("open", "high", "low", "close", "volume")
    }).dropna(subset=["close"])
    if df.empty:
        return None

    df["ret"] = df["close"].pct_change() * 100.0
    for p in (10, 21):
        df[f"ema{p}"] = ema(df["close"], p)
    df["ema50"] = ema(df["close"], C.ATR_EMA_PERIOD)
    df["ma50"] = df["close"].rolling(50, min_periods=50).mean()
    df["ma200"] = df["close"].rolling(200, min_periods=200).mean()
    # A 50-span EMA still carries (1-2/51)^(n-50) of its seed. Below ~125 bars
    # that is more than 5% of the level, which moves the ATR distance visibly.
    n = len(df)
    if n < 125:
        seed_weight = (1 - 2 / (C.ATR_EMA_PERIOD + 1)) ** max(n - C.ATR_EMA_PERIOD, 0)
        log.warning(
            "%s: only %d sessions — the 50-day EMA is still %.0f%% seed value, so "
            "the ATR distance is indicative, not exact. It settles around 125 "
            "sessions.", ticker, n, seed_weight * 100)
    df["atr"] = atr(df["high"], df["low"], df["close"])
    # the line the stretch is measured from — see config.ATR_LINE
    ref = df["ma50"] if C.ATR_LINE.lower() == "sma" else df["ema50"]
    df["atr_line"] = ref
    df["atr_dist"] = (df["close"] - ref) / df["atr"].replace(0, np.nan)
    df["ema10_rising"] = df["ema10"] > df["ema10"].shift(1)
    df["ema21_rising"] = df["ema21"] > df["ema21"].shift(1)
    return df


def distribution_days(idx: pd.DataFrame) -> dict:
    """O'Neil distribution-day count over the trailing window.

    A distribution day is a close down >= 0.20% on volume greater than the prior
    session. It is cancelled once the index closes 5% above that day's close, or
    once it ages out of the 25-session window.
    """
    down = (idx["ret"] <= -C.DD_MIN_DROP_PCT) & (idx["volume"] > idx["volume"].shift(1))
    recent = idx.tail(C.DD_WINDOW)
    last_close = idx["close"].iloc[-1]

    days = []
    for ts, row in recent.iterrows():
        if not bool(down.get(ts, False)):
            continue
        if last_close >= row["close"] * (1 + C.DD_EXPIRE_RALLY_PCT / 100.0):
            continue  # rallied away from it
        days.append({"date": ts, "ret": float(row["ret"])})
    return {"count": len(days), "days": days}


def follow_through_day(idx: pd.DataFrame, lookback: int = 60) -> dict | None:
    """Most recent follow-through day off a rally attempt low."""
    win = idx.tail(lookback)
    if len(win) < C.FTD_MIN_DAY + 2:
        return None
    lows = win["close"].rolling(C.DD_WINDOW, min_periods=5).min()
    is_low = win["close"] <= lows

    best = None
    low_positions = [i for i, flag in enumerate(is_low.values) if flag]
    for lp in low_positions:
        for offset in range(C.FTD_MIN_DAY - 1, C.FTD_MAX_DAY):
            j = lp + offset
            if j >= len(win):
                break
            row = win.iloc[j]
            prev_vol = win["volume"].iloc[j - 1]
            if row["ret"] >= C.FTD_MIN_GAIN_PCT and row["volume"] > prev_vol:
                cand = {
                    "date": win.index[j],
                    "day": offset + 1,
                    "gain": float(row["ret"]),
                    "low_date": win.index[lp],
                }
                if best is None or cand["date"] > best["date"]:
                    best = cand
                break
    return best


# --------------------------------------------------------------------------- #
# Sector / theme relative strength
# --------------------------------------------------------------------------- #

def group_rs(panel: dict[str, pd.DataFrame], mapping: dict[str, str],
             benchmark: str = "SPY") -> pd.DataFrame:
    close = panel["close"]
    rows = []
    bench = close[benchmark] if benchmark in close.columns else None
    for tkr, label in mapping.items():
        if tkr not in close.columns:
            continue
        s = close[tkr].dropna()
        if len(s) < 70:
            continue
        row = {"ticker": tkr, "name": label, "close": float(s.iloc[-1])}
        for win, key in ((1, "d1"), (5, "w1"), (21, "m1"), (63, "m3"), (126, "m6")):
            row[key] = float(s.iloc[-1] / s.iloc[-1 - win] - 1) * 100 if len(s) > win else np.nan
        if bench is not None:
            b = bench.dropna()
            for win, key in ((5, "w1"), (21, "m1")):
                if len(b) > win:
                    row[f"rs_{key}"] = row[key] - (float(b.iloc[-1] / b.iloc[-1 - win] - 1) * 100)
        ma50 = s.rolling(50, min_periods=50).mean()
        row["above_ma50"] = bool(s.iloc[-1] > ma50.iloc[-1]) if ma50.notna().iloc[-1] else None
        rows.append(row)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("m1", ascending=False).reset_index(drop=True)
    return df


LEADERBOARD_WINDOWS = (
    ("d1", "1 day", 1),
    ("w1", "1 week", 5),
    ("m1", "1 month", 21),
    ("m3", "3 months", 63),
    ("m6", "6 months", 126),
)


def etf_leaderboard(panel: dict[str, pd.DataFrame],
                    groups: dict[str, dict[str, str]],
                    n: int = 3, benchmark: str = "SPY") -> dict:
    """Best and worst performing ETFs over each window.

    `groups` maps a group label to its ticker->name mapping, so sectors and
    themes are ranked in one combined table while each row still remembers which
    group it came from -- a theme fund outrunning every sector is exactly the
    kind of thing worth seeing, and separate tables would hide it.

    Windows are counted in TRADING SESSIONS, not calendar days: 21 sessions for
    a month, 126 for six. A calendar-day lookback would silently vary its own
    length with holidays and weekends.

    An ETF is ranked over a window only if it has that much history. The newest
    fund in the universe would otherwise appear in the six-month table on the
    strength of a much shorter run.
    """
    close = panel["close"]
    bench = close[benchmark].dropna() if benchmark in close.columns else None

    rows = []
    for group, mapping in groups.items():
        for tkr, label in mapping.items():
            if tkr not in close.columns:
                continue
            srs = close[tkr].dropna()
            if srs.empty:
                continue
            row = {"ticker": tkr, "name": label, "group": group,
                   "close": float(srs.iloc[-1])}
            for key, _, win in LEADERBOARD_WINDOWS:
                row[key] = (float(srs.iloc[-1] / srs.iloc[-1 - win] - 1) * 100
                            if len(srs) > win else np.nan)
            rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return {}

    out = {}
    for key, label, win in LEADERBOARD_WINDOWS:
        ranked = df.dropna(subset=[key]).sort_values(key, ascending=False)
        if ranked.empty:
            continue

        spy = np.nan
        if bench is not None and len(bench) > win:
            spy = float(bench.iloc[-1] / bench.iloc[-1 - win] - 1) * 100

        def pack(frame):
            return [{"ticker": r.ticker, "name": r.name, "group": r.group,
                     "ret": float(getattr(r, key)),
                     "vs_spy": (float(getattr(r, key)) - spy
                                if np.isfinite(spy) else None)}
                    for r in frame.itertuples()]

        out[key] = {
            "label": label, "sessions": win, "universe": len(ranked),
            "spy": spy if np.isfinite(spy) else None,
            "best": pack(ranked.head(n)),
            "worst": pack(ranked.tail(n).iloc[::-1]),
        }
    return out


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #

def monitor_table(panel: dict[str, pd.DataFrame], ref: pd.DataFrame) -> pd.DataFrame:
    """The rolling daily monitor table — one row per session."""
    elig = eligibility(panel, ref)
    tbl = breadth(panel, elig).join(leadership(panel, elig))

    for tkr in ("SPY", "QQQ"):
        idx = index_frame(panel, tkr)
        tbl[f"{tkr.lower()}_atr"] = idx["atr_dist"] if idx is not None else np.nan

    spy = index_frame(panel, "SPY")
    tbl["spy_close"] = spy["close"] if spy is not None else np.nan

    tbl.index.name = "date"
    return tbl
