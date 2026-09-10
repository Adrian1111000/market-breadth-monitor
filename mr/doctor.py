"""Diagnostic dump — run this when a number looks wrong.

    python -m mr.doctor            > doctor.txt

Prints what is actually in the data rather than what the pipeline concluded:
cache depth, the reference list's shape, how many names each universe filter
removes, raw index closes to check against a public source, and any move large
enough to be a split artefact. No API calls — it reads the cache only.
"""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter
from datetime import timedelta

import numpy as np
import pandas as pd

from . import __version__
from . import config as C
from . import data as D
from . import metrics as M


def hdr(t: str) -> None:
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", default="SPY,QQQ,IWM,MDY",
                    help="comma-separated tickers to print raw closes for")
    ap.add_argument("--rows", type=int, default=8, help="how many sessions to show")
    args = ap.parse_args(argv)

    hdr("VERSION AND SETTINGS")
    print(f"market-review   {__version__}")
    print(f"min_history     {C.MIN_HISTORY_BARS}")
    print(f"atr_method      {C.ATR_METHOD}")
    print(f"atr_line        {C.ATR_LINE}")
    print(f"ema_seed        {C.EMA_SEED}")

    hdr("CACHE")
    files = sorted(C.CACHE_DIR.glob("*.json.gz"))
    print(f"root            {C.ROOT}")
    print(f"cached files    {len(files)}")
    if not files:
        print("nothing cached — run 'python -m mr.run' first")
        return 1
    empties = 0
    for f in files:
        with gzip.open(f, "rt") as fh:
            if not json.load(fh):
                empties += 1
    print(f"empty (holiday) {empties}")
    print(f"date range      {files[0].name[:10]} .. {files[-1].name[:10]}")

    as_of = D.latest_cached_date()
    print(f"latest session  {as_of}")

    bars = D.load_bars(as_of, max_downloads=0)
    # Apply splits exactly as the pipeline does. Reading the raw cache here would
    # report thousands of phantom moves that the real run never sees.
    _splits = D.splits_cached(
        as_of, as_of - timedelta(days=C.LOOKBACK_CALENDAR_DAYS), offline=True)
    print(f"splits applied  {len(_splits):,} rows"
          + ("" if len(_splits) else "   <-- NONE, prices are unadjusted"))
    bars = D.apply_splits(bars, _splits)
    panel = D.build_panel(bars)
    close = panel["close"]
    print(f"panel           {close.shape[0]} sessions x {close.shape[1]} tickers")

    # ---------------------------------------------------------------- reference
    hdr("REFERENCE LIST")
    ref = None
    snaps = sorted(C.DATA_DIR.glob("reference_*.csv"))
    if snaps:
        ref = pd.read_csv(snaps[-1])
        print(f"file            {snaps[-1].name}")
        print(f"tickers         {len(ref):,}")
        if "type" in ref:
            print("by type         " +
                  ", ".join(f"{k}={v:,}" for k, v in Counter(ref['type']).most_common()))
        if "primary_exchange" in ref:
            print("\nby primary_exchange (this is where a bad allowlist bites):")
            for k, v in Counter(ref["primary_exchange"].fillna("<blank>")).most_common(15):
                keep = ""
                if C.KEEP_EXCHANGES:
                    keep = "  KEPT" if k in C.KEEP_EXCHANGES else "  >>> DROPPED <<<"
                print(f"  {str(k):<12} {v:>7,}{keep}")
        print(f"\nMR_KEEP_EXCHANGES = {C.KEEP_EXCHANGES or '(empty — no exchange filter)'}")
    else:
        print("no reference snapshot on disk")
        return 1

    # ----------------------------------------------------------------- universe
    hdr("UNIVERSE FILTERS, LATEST SESSION")
    last = close.index[-1]
    lc, lv = close.loc[last], panel["volume"].loc[last]
    allowed = set(ref["ticker"])

    traded = lc.notna()
    in_ref = pd.Series([t in allowed for t in close.columns], index=close.columns)
    if C.KEEP_EXCHANGES and "primary_exchange" in ref:
        ex = ref.set_index("ticker")["primary_exchange"].to_dict()
        on_ex = pd.Series([ex.get(t, "") in C.KEEP_EXCHANGES for t in close.columns],
                          index=close.columns)
    else:
        on_ex = pd.Series(True, index=close.columns)
    n = len(close)
    eff_min = min(C.MIN_HISTORY_BARS, max(5, int(n * 0.8)))
    seasoned = close.notna().cumsum().loc[last] >= eff_min
    priced = lc >= C.MIN_CLOSE
    liquid = lv >= C.MIN_VOLUME

    steps = [
        ("tickers with a bar today", traded),
        ("… also in the reference list", traded & in_ref),
        (f"… also on a kept exchange", traded & in_ref & on_ex),
        (f"… also seasoned ({eff_min} bars)", traded & in_ref & on_ex & seasoned),
        (f"… also close >= ${C.MIN_CLOSE:.0f}", traded & in_ref & on_ex & seasoned & priced),
        (f"… also volume >= {C.MIN_VOLUME:,}",
         traded & in_ref & on_ex & seasoned & priced & liquid),
    ]
    prev = None
    for label, mask in steps:
        cnt = int(mask.sum())
        drop = "" if prev is None else f"   (-{prev - cnt:,})"
        print(f"  {label:<38} {cnt:>7,}{drop}")
        prev = cnt
    print(f"\n  A healthy universe is roughly 2,000-2,600 names. A big drop at one "
          f"step is your bug.")

    # ------------------------------------------------------------- index prices
    hdr("RAW INDEX CLOSES  (check these against any public source)")
    for tkr in [t.strip().upper() for t in args.tickers.split(",")]:
        if tkr not in close.columns:
            print(f"\n{tkr}: NOT PRESENT in the cached bars")
            continue
        df = pd.DataFrame({f: panel[f][tkr] for f in
                           ("open", "high", "low", "close", "volume")}).dropna()
        print(f"\n{tkr}   ({len(df)} sessions cached)")
        print("  date         open      high       low     close        volume")
        for ts, r in df.tail(args.rows).iterrows():
            print(f"  {ts:%Y-%m-%d} {r.open:9.2f} {r.high:9.2f} {r.low:9.2f} "
                  f"{r.close:9.2f} {int(r.volume):>13,}")
        idx = M.index_frame(panel, tkr)
        l = idx.iloc[-1]
        seed_w = (1 - 2 / 51) ** max(len(idx) - 50, 0)
        print(f"    ema10 {l.ema10:.2f}   ema21 {l.ema21:.2f}   ema50 {l.ema50:.2f}"
              f"   ma50 {l.ma50 if pd.notna(l.ma50) else float('nan'):.2f}")
        print(f"    atr14 {l.atr:.3f}   atr_dist {l.atr_dist:+.2f}  "
              f"(50D EMA is {seed_w*100:.0f}% seed value — "
              f"{'UNRELIABLE' if seed_w > 0.05 else 'settled'})")

    # ------------------------------------------------------------- split checks
    hdr("SUSPECT MOVES  (likely unadjusted splits, which corrupt 4% counts)")
    ret = close.pct_change() * 100.0
    elig = M.eligibility(panel, ref)
    big = ((ret.abs() > 35) & elig)
    hits = [(d, t, ret.loc[d, t]) for d, t in zip(*np.where(big.values))
            for d, t in [(big.index[d], big.columns[t])]]
    if not hits:
        print("  none — no move beyond +/-35% in the eligible universe")
    else:
        print(f"  {len(hits)} move(s) beyond +/-35%. A -50% or -75% day is almost")
        print("  always a split the cache has not adjusted for:")
        for d, t, v in sorted(hits, key=lambda x: abs(x[2]), reverse=True)[:25]:
            print(f"    {d:%Y-%m-%d}  {t:<8} {v:+8.1f}%")
    splits = sorted(C.DATA_DIR.glob("splits_*.csv"))
    print(f"\n  splits snapshot: {splits[-1].name if splits else 'NONE — prices are unadjusted'}")

    # ------------------------------------------------------------------ breadth
    hdr("BREADTH, LATEST SESSION")
    tbl = M.monitor_table(panel, ref)
    tbl = tbl[tbl["universe"] > 0]
    if tbl.empty:
        print("  universe empty on every session — see the filter table above")
        return 1
    r = tbl.iloc[-1]
    for k, label, fmt in [
        ("universe", "universe", "{:,.0f}"), ("up4", "up 4%", "{:,.0f}"),
        ("dn4", "down 4%", "{:,.0f}"), ("pct_ma20", "% > 20D MA", "{:.2f}"),
        ("pct_ma50", "% > 50D MA", "{:.2f}"), ("pct_ma200", "% > 200D MA", "{:.2f}"),
        ("new_highs", "52w highs", "{:,.0f}"), ("new_lows", "52w lows", "{:,.0f}"),
        ("mli_n", "MLI members", "{:,.0f}"), ("spy_atr", "SPY ATR dist", "{:+.2f}"),
        ("qqq_atr", "QQQ ATR dist", "{:+.2f}"),
    ]:
        v = r.get(k)
        print(f"  {label:<16} {'—' if pd.isna(v) else fmt.format(v)}")
    print(f"\n  rows with a non-empty universe: {len(tbl)}")

    hdr("SEND THIS WHOLE OUTPUT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
