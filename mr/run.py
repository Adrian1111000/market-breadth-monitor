"""Entry point: build the daily market review.

    python -m mr.run                 # latest session
    python -m mr.run --date 2026-09-04
    python -m mr.run --synthetic     # no API key needed, exercises the pipeline
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from . import __version__
from . import config as C
from . import data as D
from . import metrics as M
from . import regime as REG
from . import render_html, render_md, screen as SCR

log = logging.getLogger("mr")


def coverage(tbl, sessions: int) -> list[tuple[str, bool, str]]:
    """Which metrics have enough history to be meaningful yet."""
    return [
        ("4% movers / breadth", sessions >= 2, "needs 2 sessions"),
        ("% above 20D MA", sessions >= 20, "needs 20"),
        ("% above 50D MA", sessions >= 50, "needs 50"),
        ("ATR vs 50D EMA", sessions >= 125, "needs ~125 for the EMA to settle"),
        ("distribution days / FTD", sessions >= 25, "needs 25"),
        ("sector + theme RS", sessions >= 70, "needs 70"),
        ("net new 52-week highs", sessions >= 126, "needs 126"),
        ("% above 200D MA", sessions >= 200, "needs 200"),
        ("leadership index (MLI)", sessions >= 200, "needs 200"),
    ]


def build(as_of: date | None = None, *, synthetic: bool = False,
          max_downloads: int | None = None) -> dict:
    if synthetic:
        from . import synthetic as SYN
        bars, ref = SYN.make()
        log.info("synthetic market: %d names", ref.shape[0])
    else:
        cap = C.MAX_DOWNLOADS if max_downloads is None else max_downloads
        offline = cap == 0
        as_of = as_of or D.latest_trading_date(offline=offline)
        bars = D.load_bars(as_of, max_downloads=max_downloads)
        splits = D.splits_cached(
            as_of, as_of - timedelta(days=C.LOOKBACK_CALENDAR_DAYS), offline=offline)
        bars = D.apply_splits(bars, splits)
        ref = D.reference_cached(as_of, offline=offline)
        log.info("reference: %d tickers", len(ref))

    panel = D.build_panel(bars)
    tbl = M.monitor_table(panel, ref)
    sessions = len(tbl)
    tbl = tbl[tbl["universe"] > 0]

    if tbl.empty:
        raise RuntimeError(
            f"The universe is empty on every one of the {sessions} cached "
            f"sessions, so there is nothing to report.\n"
            f"  Most likely the cache is too shallow: a name must trade for "
            f"{C.MIN_HISTORY_BARS} sessions to count, and only {sessions} are "
            f"cached.\n"
            f"  Fix: deepen the cache with 'python -m mr.run' (no --max-downloads) "
            f"and re-run.\n"
            f"  If the cache is deep and this still happens, the exchange filter "
            f"is the next suspect: config.KEEP_EXCHANGES currently keeps "
            f"{', '.join(C.KEEP_EXCHANGES)}.")

    row = tbl.iloc[-1]
    prev = tbl.iloc[-2] if len(tbl) > 1 else None
    session = tbl.index[-1].date()

    indexes = {t: M.index_frame(panel, t) for t in C.INDEX_TICKERS}
    indexes = {t: v for t, v in indexes.items() if v is not None}
    dd = {t: M.distribution_days(v) for t, v in indexes.items() if t in ("SPY", "QQQ")}
    ftd = {t: M.follow_through_day(v) for t, v in indexes.items() if t in ("SPY", "QQQ")}

    prev_row = _previous_regime(session)
    verdict = REG.score(row, prev_row, indexes, dd)

    ctx = {
        "date": session,
        "table": tbl,
        "row": row,
        "prev": prev,
        "verdict": verdict,
        "indexes": indexes,
        "dd": dd,
        "ftd": ftd,
        "watchlist": SCR.candidates(panel, ref),
        "sectors": M.group_rs(panel, C.SECTOR_ETFS),
        "themes": M.group_rs(panel, C.THEME_ETFS),
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "synthetic": synthetic,
    }
    return ctx


def _previous_regime(session: date) -> pd.Series | None:
    """Yesterday's stored verdict, so a regime change can be flagged."""
    if not C.HISTORY_CSV.exists():
        return None
    hist = pd.read_csv(C.HISTORY_CSV, parse_dates=["date"])
    past = hist[hist["date"].dt.date < session]
    return past.iloc[-1] if len(past) else None


def persist(ctx: dict) -> None:
    """Append today's row to the rolling history CSV (idempotent per date)."""
    row = ctx["row"].to_dict()
    row["date"] = pd.Timestamp(ctx["date"])
    row["regime"] = ctx["verdict"]["light"]
    row["regime_score"] = ctx["verdict"]["total"]
    new = pd.DataFrame([row])

    if C.HISTORY_CSV.exists():
        hist = pd.read_csv(C.HISTORY_CSV, parse_dates=["date"])
        hist = hist[hist["date"] != row["date"]]
        out = pd.concat([hist, new], ignore_index=True)
    else:
        out = new
    out = out.sort_values("date")
    lead = ["date", "regime", "regime_score"]
    out = out[lead + [c for c in out.columns if c not in lead]]
    out.to_csv(C.HISTORY_CSV, index=False)
    log.info("history -> %s (%d rows)", C.HISTORY_CSV, len(out))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Daily market review")
    ap.add_argument("--date", help="session to build, YYYY-MM-DD (default: latest)")
    ap.add_argument("--synthetic", action="store_true",
                    help="run against a generated market, no API key needed")
    ap.add_argument("--out", default=str(C.OUT_DIR), help="output directory")
    ap.add_argument("--max-downloads", type=int, default=None, metavar="N",
                    help="cap new session downloads this run: -1 unlimited, "
                         "0 uses the cache only, N fetches at most N. Downloads "
                         "run newest-first, so a capped run still leaves you "
                         "with the most recent history.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("market-review %s  root=%s", __version__, C.ROOT)

    as_of = date.fromisoformat(args.date) if args.date else None
    ctx = build(as_of, synthetic=args.synthetic, max_downloads=args.max_downloads)
    persist(ctx)

    from pathlib import Path
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = ctx["date"].isoformat()

    html_path = out / f"review_{stamp}.html"
    md_path = out / f"review_{stamp}.md"
    json_path = out / f"review_{stamp}.json"

    html_path.write_text(render_html.render(ctx), encoding="utf-8")
    md_path.write_text(render_md.render(ctx), encoding="utf-8")
    json_path.write_text(
        json.dumps(render_html.build_payload(ctx), indent=2), encoding="utf-8")

    v = ctx["verdict"]
    sessions = len(ctx["table"])
    pending = [(n, need) for n, ok, need in coverage(ctx["table"], sessions) if not ok]
    if pending:
        print(f"\n{sessions} sessions cached — these metrics need more history:")
        for n, need in pending:
            print(f"    · {n}  ({need})")
        print("  Re-run to deepen the cache; everything else below is already live.")

    print(f"\nsettings  min_history={C.MIN_HISTORY_BARS}  atr={C.ATR_METHOD}/"
          f"{C.ATR_LINE}  exchanges={','.join(C.KEEP_EXCHANGES) or 'all'}")
    print(f"\n{stamp}  {v['light']}  score {v['total']:+d}/±{v['max']}")
    print(f"  up4 {int(ctx['row']['up4'])} / dn4 {int(ctx['row']['dn4'])}"
          f"   %>20D {ctx['row']['pct_ma20']:.1f}"
          f"   %>50D {ctx['row']['pct_ma50']:.1f}"
          f"   universe {int(ctx['row']['universe']):,}")
    print(f"  {html_path}\n  {md_path}\n  {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
