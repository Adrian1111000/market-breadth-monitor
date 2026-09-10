"""Settle which convention a reference table is using.

Sources disagree on the "distance from the 50-day line in ATR units" reading
because three independent choices go into it: how the ATR is smoothed, how the
50-day line is built, and how an EMA is seeded. This prints every combination for
one ticker on one date, so you can see which cell reproduces the number you are
trying to match instead of guessing.

    python -m mr.reconcile SPY 2026-09-04 --target 1.81 --target 2.19
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

import pandas as pd

from . import config as C
from . import data as D
from . import metrics as M

ATR_METHODS = ("wilder", "sma", "ema")
LINE_KINDS = ("ema50-sma-seed", "ema50-first-seed", "sma50")


def line(close: pd.Series, kind: str) -> pd.Series:
    if kind == "sma50":
        return close.rolling(C.ATR_EMA_PERIOD, min_periods=C.ATR_EMA_PERIOD).mean()
    seed = "sma" if kind.endswith("sma-seed") else "first"
    return M.ema(close, C.ATR_EMA_PERIOD, seed=seed)


def matrix(panel: dict[str, pd.DataFrame], ticker: str,
           when: pd.Timestamp | None = None) -> pd.DataFrame:
    df = M.index_frame(panel, ticker)
    if df is None:
        raise SystemExit(f"{ticker} not present in the panel")
    if when is not None:
        df = df.loc[:when]

    rows = []
    for meth in ATR_METHODS:
        a = M.atr(df["high"], df["low"], df["close"], method=meth)
        row = {"atr_method": meth, "atr": round(float(a.iloc[-1]), 4)}
        for kind in LINE_KINDS:
            ln = line(df["close"], kind)
            row[kind] = round(float((df["close"].iloc[-1] - ln.iloc[-1]) / a.iloc[-1]), 2)
        rows.append(row)
    out = pd.DataFrame(rows)
    out.attrs["close"] = float(df["close"].iloc[-1])
    out.attrs["date"] = df.index[-1].date()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ticker", nargs="?", default="SPY")
    ap.add_argument("when", nargs="?", help="session date, YYYY-MM-DD")
    ap.add_argument("--target", type=float, action="append", default=[],
                    help="a reading you are trying to reproduce (repeatable)")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--online", action="store_true",
                    help="allow API calls to fill gaps (default: cache only)")
    args = ap.parse_args(argv)

    if args.synthetic:
        from . import synthetic as SYN
        bars, _ = SYN.make()
    else:
        # Cache-only by default. This is a diagnostic you run while staring at a
        # number you distrust; it should never need a key or a network round trip.
        offline = not args.online
        as_of = (date.fromisoformat(args.when) if args.when
                 else D.latest_trading_date(offline=offline))
        bars = D.load_bars(as_of, max_downloads=None if args.online else 0)
        bars = D.apply_splits(bars, D.splits_cached(
            as_of, as_of - timedelta(days=C.LOOKBACK_CALENDAR_DAYS), offline=offline))

    panel = D.build_panel(bars)
    when = pd.Timestamp(args.when) if args.when else None
    m = matrix(panel, args.ticker, when)

    print(f"\n{args.ticker} close {m.attrs['close']:.2f} on {m.attrs['date']}")
    print("distance from the 50-day line, in ATR units\n")
    print(m.to_string(index=False))

    if args.target:
        print("\nclosest cell for each target:")
        for t in args.target:
            best, gap = None, 1e9
            for _, r in m.iterrows():
                for kind in LINE_KINDS:
                    d = abs(r[kind] - t)
                    if d < gap:
                        best, gap = (r["atr_method"], kind, r[kind]), d
            mark = "exact" if gap < 0.005 else f"off by {gap:.2f}"
            print(f"  {t:+.2f}  ->  ATR={best[0]}, line={best[1]}, "
                  f"gives {best[2]:+.2f}  ({mark})")
        print("\nSet the winner with MR_ATR_METHOD and MR_EMA_SEED, "
              "or edit mr/config.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
