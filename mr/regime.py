"""Composite market regime / exposure signal.

Eight independent components each score -1, 0 or +1. The sum maps to
GREEN (press), YELLOW (selective), RED (defensive). Every component carries its
own one-line reason so the narrative can explain *why* the light is what it is
rather than just printing a colour.
"""

from __future__ import annotations

import math

import pandas as pd

from . import config as C


def _sign(value, lo, hi) -> int:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0
    if value >= hi:
        return 1
    if value <= lo:
        return -1
    return 0


def score(row: pd.Series, prev: pd.Series | None, indexes: dict,
          dd: dict) -> dict:
    """Build the regime verdict for one session.

    ``row``/``prev`` are rows of the monitor table; ``indexes`` maps a ticker to
    its ``metrics.index_frame`` result; ``dd`` maps a ticker to its distribution
    day dict.
    """
    comps: list[dict] = []

    def add(name: str, pts: int, reason: str) -> None:
        comps.append({"name": name, "points": pts, "reason": reason})

    # 1 & 2 — index trend: above a rising 10 and 21 EMA
    for tkr in ("SPY", "QQQ"):
        idx = indexes.get(tkr)
        if idx is None or idx.empty:
            add(f"{tkr} trend", 0, "no data")
            continue
        last = idx.iloc[-1]
        above = last["close"] > last["ema10"] and last["close"] > last["ema21"]
        rising = bool(last["ema10_rising"]) and bool(last["ema21_rising"])
        below_both = last["close"] < last["ema10"] and last["close"] < last["ema21"]
        if above and rising:
            add(f"{tkr} trend", 1, f"above rising 10/21 EMA ({last['close']:.2f})")
        elif below_both:
            add(f"{tkr} trend", -1, f"below both 10 and 21 EMA ({last['close']:.2f})")
        else:
            add(f"{tkr} trend", 0, "mixed vs 10/21 EMA")

    # 3 — intermediate participation
    p50 = row.get("pct_ma50")
    add("% > 50D MA", _sign(p50, 40.0, 60.0),
        f"{p50:.1f}% of the universe above its 50-day" if pd.notna(p50) else "no data")

    # 4 — short-term participation
    p20 = row.get("pct_ma20")
    add("% > 20D MA", _sign(p20, 30.0, 60.0),
        f"{p20:.1f}% above the 20-day" if pd.notna(p20) else "no data")

    # 5 — 10-day up4/dn4 pressure
    ratio = row.get("ratio_10d")
    add("4% pressure (10d)", _sign(ratio, 0.75, 1.30),
        f"10-day UP4:DN4 ratio {ratio:.2f}" if pd.notna(ratio) else "no data")

    # 6 — distribution days on the worse of the two indexes
    dd_count = max((v["count"] for v in dd.values()), default=0)
    dd_pts = 1 if dd_count <= C.DD_LIGHT else (-1 if dd_count >= C.DD_HEAVY else 0)
    add("Distribution days", dd_pts, f"{dd_count} in the last {C.DD_WINDOW} sessions")

    # 7 — leadership behaviour
    mli10 = row.get("mli_10d")
    add("Leadership (10d)", _sign(mli10, -1.5, 1.5),
        f"leaders {mli10:+.1f}% over 10 sessions on {int(row.get('mli_n', 0))} names"
        if pd.notna(mli10) else "no data")

    # 8 — net new highs vs lows
    net = row.get("net_hl")
    add("Net new highs", _sign(net, -25, 25),
        f"net 52-week highs {int(net):+d}" if pd.notna(net) else "no data")

    total = sum(c["points"] for c in comps)
    if total >= C.REGIME_GREEN_AT:
        light, stance = "GREEN", "Press winners; new buys at full size."
    elif total <= C.REGIME_RED_AT:
        light, stance = "RED", "No new risk; raise cash, honour stops."
    else:
        light, stance = "YELLOW", "Half size, best setups only, quick stops."

    # Stretch flag: independent of the light, it governs *where* you add
    stretch = []
    for tkr in ("SPY", "QQQ"):
        v = row.get(f"{tkr.lower()}_atr")
        if pd.notna(v):
            if v >= C.ATR_STRETCHED:
                stretch.append(f"{tkr} is {v:+.1f} ATR above its 50D EMA — extended, "
                               "chase nothing here")
            elif v <= C.ATR_OVERSOLD:
                stretch.append(f"{tkr} is {v:+.1f} ATR below its 50D EMA — washed out, "
                               "watch for a reversal day")

    prev_light = None
    if prev is not None and "regime" in prev.index and pd.notna(prev.get("regime")):
        prev_light = prev["regime"]

    return {
        "light": light,
        "stance": stance,
        "total": total,
        "max": len(comps),
        "components": comps,
        "stretch": stretch,
        "changed_from": prev_light if prev_light and prev_light != light else None,
    }
