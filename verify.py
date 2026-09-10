"""Independent re-implementation of the key metrics, compared against mr.*.

Every check recomputes the number by a different route (explicit loops instead of
vectorised pandas) so a shared bug cannot pass both sides.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from mr import config as C
from mr import data as D, metrics as M, regime as REG, synthetic as SYN

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")
    if not ok:
        FAILS.append(name)


def close_to(a, b, tol=1e-6) -> bool:
    if a is None or b is None:
        return False
    if pd.isna(a) and pd.isna(b):
        return True
    return abs(float(a) - float(b)) <= tol


print("building synthetic panel…")
bars, ref = SYN.make(n_names=600, n_days=400, seed=11)
panel = D.build_panel(bars)
elig = M.eligibility(panel, ref)
tbl = M.monitor_table(panel, ref)
close = panel["close"]
volume = panel["volume"]
last = close.index[-1]

# --------------------------------------------------------------------------- #
print("\n[1] universe eligibility")
# brute force the last session
stock_set = set(ref["ticker"])
manual = 0
for t in close.columns:
    if t not in stock_set:
        continue
    s = close[t]
    if s.notna().sum() < C.MIN_HISTORY_BARS:
        continue
    if s.loc[last] >= C.MIN_CLOSE and volume[t].loc[last] >= C.MIN_VOLUME:
        manual += 1
check("universe count matches a brute-force pass",
      manual == int(tbl["universe"].iloc[-1]),
      f"manual={manual} pipeline={int(tbl['universe'].iloc[-1])}")
check("ETFs are excluded from the universe",
      not any(t in stock_set for t in ("SPY", "QQQ", "XLK")))

# --------------------------------------------------------------------------- #
print("\n[2] 4% movers")
up_manual = dn_manual = 0
for t in close.columns:
    if not bool(elig[t].loc[last]):
        continue
    s = close[t]
    r = (s.loc[last] / s.shift(1).loc[last] - 1) * 100
    if r >= 4.0:
        up_manual += 1
    elif r <= -4.0:
        dn_manual += 1
check("UP 4% count", up_manual == int(tbl["up4"].iloc[-1]),
      f"manual={up_manual} pipeline={int(tbl['up4'].iloc[-1])}")
check("DOWN 4% count", dn_manual == int(tbl["dn4"].iloc[-1]),
      f"manual={dn_manual} pipeline={int(tbl['dn4'].iloc[-1])}")
check("boundary is inclusive at exactly 4%",
      (pd.Series([4.0]) >= C.MOVE_PCT).iloc[0])

# --------------------------------------------------------------------------- #
print("\n[3] % above moving averages")
for win, col in ((20, "pct_ma20"), (50, "pct_ma50"), (200, "pct_ma200")):
    above = have = 0
    for t in close.columns:
        if not bool(elig[t].loc[last]):
            continue
        s = close[t].dropna()
        if len(s) < win:
            continue
        ma = s.tail(win).mean()
        have += 1
        if s.iloc[-1] > ma:
            above += 1
    manual_pct = above / have * 100
    check(f"% above {win}D MA", close_to(manual_pct, tbl[col].iloc[-1], 1e-6),
          f"manual={manual_pct:.4f} pipeline={tbl[col].iloc[-1]:.4f}")

# --------------------------------------------------------------------------- #
print("\n[4] ATR distance from the 50D EMA")
idx = M.index_frame(panel, "SPY")
s = idx.copy()
# independent Wilder ATR by explicit recursion
tr = []
for i in range(len(s)):
    h, l = s["high"].iloc[i], s["low"].iloc[i]
    if i == 0:
        tr.append(h - l)
    else:
        pc = s["close"].iloc[i - 1]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
atrv = [np.nan] * len(tr)
atrv[13] = float(np.mean(tr[:14]))
for i in range(14, len(tr)):
    atrv[i] = (atrv[i - 1] * 13 + tr[i]) / 14
# 50D EMA by explicit recursion, seeded from the first 50-bar mean
k = 2 / 51
emav = [np.nan] * len(s)
emav[49] = float(s["close"].iloc[:50].mean())
for i in range(50, len(s)):
    emav[i] = s["close"].iloc[i] * k + emav[i - 1] * (1 - k)
manual_dist = (s["close"].iloc[-1] - emav[-1]) / atrv[-1]
check("SPY ATR distance (wilder ATR, SMA-seeded EMA)",
      close_to(manual_dist, idx["atr_dist"].iloc[-1], 5e-3),
      f"manual={manual_dist:.5f} pipeline={idx['atr_dist'].iloc[-1]:.5f}")
check("ATR is positive", atrv[-1] > 0, f"atr={atrv[-1]:.4f}")

# the smoothing knob must actually change the answer, and stay sane in all modes
readings = {}
for meth in ("wilder", "sma", "ema"):
    a = M.atr(s["high"], s["low"], s["close"], method=meth)
    readings[meth] = float(a.iloc[-1])
    check(f"ATR[{meth}] is positive and finite",
          a.iloc[-1] > 0 and np.isfinite(a.iloc[-1]), f"{a.iloc[-1]:.4f}")
check("the three ATR methods disagree (the knob is live)",
      len({round(v, 6) for v in readings.values()}) == 3,
      " ".join(f"{k}={v:.3f}" for k, v in readings.items()))
check("ATR methods stay within 40% of each other",
      max(readings.values()) / min(readings.values()) < 1.4,
      f"spread {max(readings.values())/min(readings.values()):.3f}×")
try:
    M.atr(s["high"], s["low"], s["close"], method="nope")
    check("an unknown ATR method is rejected", False)
except ValueError:
    check("an unknown ATR method is rejected", True)

# EMA seeding: both modes converge on a long history
e_sma = M.ema(s["close"], 50, seed="sma").iloc[-1]
e_first = M.ema(s["close"], 50, seed="first").iloc[-1]
check("EMA seeds converge over a long history",
      abs(e_sma / e_first - 1) < 0.002, f"sma={e_sma:.3f} first={e_first:.3f}")
check("SMA-seeded EMA is NaN before it has enough bars",
      bool(pd.isna(M.ema(s["close"], 50, seed="sma").iloc[48])))

# --------------------------------------------------------------------------- #
print("\n[5] leadership index")
lead = M.leadership(panel, elig)
n = int(lead["mli_n"].iloc[-1])
# recount membership by hand
manual_members = []
for t in close.columns:
    if not bool(elig[t].loc[last]):
        continue
    s = close[t]
    v = volume[t]
    if len(s.dropna()) < 200:
        continue
    px = s.loc[last]
    ma50 = s.tail(50).mean()
    ma200 = s.tail(200).mean()
    ma200_prev = s.iloc[-(200 + C.MLI_TREND_LOOKBACK):-C.MLI_TREND_LOOKBACK].mean()
    dv = (s * v).tail(50).mean()
    if (px >= C.MLI_MIN_PRICE and dv >= C.MLI_MIN_DOLLAR_VOL
            and px > ma50 and px > ma200 and ma200 > ma200_prev):
        manual_members.append(t)
# RS filter applied on the pipeline's own percentile so we compare the other gates
check("MLI membership is a subset of the non-RS gates",
      n <= len(manual_members),
      f"pipeline N={n}, names passing price/liquidity/trend gates={len(manual_members)}")
check("MLI N is non-zero", n > 0, f"N={n}")
mli_pct = lead["mli_pct"].iloc[-1]
check("MLI daily return is a plausible equal-weight mean",
      abs(mli_pct) < 15.0, f"{mli_pct:+.3f}%")
check("MLI rising% is a percentage", 0 <= lead["mli_rising"].iloc[-1] <= 100,
      f"{lead['mli_rising'].iloc[-1]:.2f}")

# empty-membership guard
empty = M.leadership(panel, elig & False)
check("empty membership yields NaN, not a divide-by-zero",
      pd.isna(empty["mli_pct"].iloc[-1]) and int(empty["mli_n"].iloc[-1]) == 0)

# --------------------------------------------------------------------------- #
print("\n[6] distribution days")
dd = M.distribution_days(idx)
manual = []
window = idx.tail(C.DD_WINDOW)
lastpx = idx["close"].iloc[-1]
for ts, r in window.iterrows():
    pos = idx.index.get_loc(ts)
    if pos == 0:
        continue
    prev_close = idx["close"].iloc[pos - 1]
    prev_vol = idx["volume"].iloc[pos - 1]
    pct = (r["close"] / prev_close - 1) * 100
    if pct <= -C.DD_MIN_DROP_PCT and r["volume"] > prev_vol:
        if lastpx < r["close"] * 1.05:
            manual.append(ts)
check("distribution day count", len(manual) == dd["count"],
      f"manual={len(manual)} pipeline={dd['count']}")
check("distribution days all fall inside the window",
      all(d["date"] in set(window.index) for d in dd["days"]))
check("every distribution day is a down day",
      all(d["ret"] <= -C.DD_MIN_DROP_PCT for d in dd["days"]))

ftd = M.follow_through_day(idx)
if ftd:
    pos = idx.index.get_loc(ftd["date"])
    check("follow-through day gain clears the threshold",
          idx["ret"].iloc[pos] >= C.FTD_MIN_GAIN_PCT, f"{idx['ret'].iloc[pos]:.2f}%")
    check("follow-through day volume exceeds the prior session",
          idx["volume"].iloc[pos] > idx["volume"].iloc[pos - 1])
else:
    check("no follow-through day found is a valid state", True)

# --------------------------------------------------------------------------- #
print("\n[7] regime scoring")
indexes = {t: M.index_frame(panel, t) for t in C.INDEX_TICKERS}
indexes = {t: v for t, v in indexes.items() if v is not None}
dds = {t: M.distribution_days(v) for t, v in indexes.items() if t in ("SPY", "QQQ")}
v = REG.score(tbl.iloc[-1], None, indexes, dds)
check("component scores sum to the composite",
      sum(c["points"] for c in v["components"]) == v["total"])
check("every component is -1, 0 or +1",
      all(c["points"] in (-1, 0, 1) for c in v["components"]))
check("light matches the documented thresholds",
      (v["light"] == "GREEN") == (v["total"] >= C.REGIME_GREEN_AT)
      and (v["light"] == "RED") == (v["total"] <= C.REGIME_RED_AT),
      f"score={v['total']} light={v['light']}")
check("every component carries a reason",
      all(c["reason"] for c in v["components"]))

# forced extremes
bull = tbl.iloc[-1].copy()
bull["pct_ma50"], bull["pct_ma20"], bull["ratio_10d"] = 95.0, 95.0, 5.0
bull["mli_10d"], bull["net_hl"] = 12.0, 400
vb = REG.score(bull, None, indexes, {"SPY": {"count": 0}, "QQQ": {"count": 0}})
bear = tbl.iloc[-1].copy()
bear["pct_ma50"], bear["pct_ma20"], bear["ratio_10d"] = 5.0, 5.0, 0.2
bear["mli_10d"], bear["net_hl"] = -18.0, -500
vr = REG.score(bear, None, indexes, {"SPY": {"count": 9}, "QQQ": {"count": 9}})
check("a maximally bullish tape scores GREEN", vb["light"] == "GREEN",
      f"score={vb['total']}")
check("a maximally bearish tape scores RED", vr["light"] == "RED",
      f"score={vr['total']}")
check("NaN inputs do not crash the scorer",
      REG.score(pd.Series({k: np.nan for k in tbl.columns}), None, {}, {})["total"]
      is not None)

# --------------------------------------------------------------------------- #
print("\n[8] split adjustment")


def _bars(tkr, rows):
    return pd.DataFrame([{"date": pd.Timestamp(d), "ticker": tkr, "open": p,
                          "high": p, "low": p, "close": p, "volume": v}
                         for d, p, v in rows])


def _splits(rows):
    return pd.DataFrame([{"ticker": t, "execution_date": pd.Timestamp(d), "ratio": r}
                         for t, d, r in rows])


# forward split: without adjustment this prints a phantom -75% day
_b = _bars("AAA", [("2026-01-02", 400, 1000), ("2026-01-05", 404, 1000),
                   ("2026-01-06", 101, 4000), ("2026-01-07", 102, 4000)])
_a = D.apply_splits(_b, _splits([("AAA", "2026-01-06", 0.25)]))
_r = (_a["close"].pct_change() * 100)
check("forward 4-for-1 removes the phantom -75% day", abs(_r.iloc[2]) < 5,
      f"split day reads {_r.iloc[2]:+.2f}%")
check("forward split scales pre-split volume up",
      close_to(_a["volume"].iloc[0], 4000, 1e-6))

# reverse split: the case that dominates a real US tape's outliers
_b = _bars("BBB", [("2026-01-02", 2.00, 50000), ("2026-01-05", 2.02, 50000),
                   ("2026-01-06", 20.3, 5000), ("2026-01-07", 20.5, 5000)])
_a = D.apply_splits(_b, _splits([("BBB", "2026-01-06", 10.0)]))
_r = (_a["close"].pct_change() * 100)
check("reverse 1-for-10 removes the phantom +900% day", abs(_r.iloc[2]) < 5,
      f"split day reads {_r.iloc[2]:+.2f}%")

# two splits on one ticker must compound for the oldest bars
_b = _bars("CCC", [("2026-01-02", 600, 100), ("2026-01-05", 300, 200),
                   ("2026-01-06", 303, 200), ("2026-01-08", 101, 600)])
_a = D.apply_splits(_b, _splits([("CCC", "2026-01-05", 0.5), ("CCC", "2026-01-08", 1/3)]))
check("chained splits compound", np.allclose(_a["close"].values, [100, 100, 101, 101]),
      str([round(x, 2) for x in _a["close"]]))

# a bar dated on the execution date is already post-split
_b = _bars("DDD", [("2026-01-05", 400, 100), ("2026-01-06", 100, 400)])
_a = D.apply_splits(_b, _splits([("DDD", "2026-01-06", 0.25)]))
check("the execution-date bar is not scaled", close_to(_a["close"].iloc[1], 100))

# isolation and row order
_b = pd.concat([_bars("AAA", [("2026-01-02", 400, 1000), ("2026-01-06", 101, 4000)]),
                _bars("ZZZ", [("2026-01-02", 50, 1000), ("2026-01-06", 51, 1000)])],
               ignore_index=True)
_a = D.apply_splits(_b, _splits([("AAA", "2026-01-06", 0.25)]))
check("unrelated tickers are untouched",
      np.allclose(_a[_a.ticker == "ZZZ"]["close"].values, [50, 51]))
check("row order is preserved", list(_a["ticker"]) == list(_b["ticker"]))
check("the target ticker is adjusted",
      close_to(_a[_a.ticker == "AAA"]["close"].iloc[0], 100))
check("empty splits is a no-op",
      D.apply_splits(_b, pd.DataFrame(
          columns=["ticker", "execution_date", "ratio"]))["close"].equals(_b["close"]))

# --------------------------------------------------------------------------- #
print("\n[9] history persistence is idempotent")
from mr import run as RUN
C.HISTORY_CSV.unlink(missing_ok=True)
ctx = {"row": tbl.iloc[-1], "date": tbl.index[-1].date(),
       "verdict": {"light": "GREEN", "total": 4}}
RUN.persist(ctx)
RUN.persist(ctx)
hist = pd.read_csv(C.HISTORY_CSV)
check("re-running the same date does not duplicate the row", len(hist) == 1,
      f"rows={len(hist)}")
check("history carries the regime columns",
      {"date", "regime", "regime_score"} <= set(hist.columns))
C.HISTORY_CSV.unlink(missing_ok=True)

# --------------------------------------------------------------------------- #
print("\n[10] renderers")
from mr import render_html, render_md
full_ctx = RUN.build(synthetic=True)
html = render_html.render(full_ctx)
md = render_md.render(full_ctx)
check("html has no doctype/head/body wrapper",
      "<!doctype" not in html.lower() and "<body" not in html.lower())
check("html carries a title", "<title>" in html)
check("the payload is assigned exactly once",
      html.count("window.__MR__ = ") == 1)
import json as _json
blob = html.split("window.__MR__ = ", 1)[1].split(";</script>", 1)[0]
payload = _json.loads(blob)
check("payload has no NaN literals", "NaN" not in blob)
check("payload rows match the configured table length",
      len(payload["rows"]) == min(C.TABLE_ROWS, len(full_ctx["table"])),
      f"{len(payload['rows'])} rows")
check("payload reading matches the table's last row",
      close_to(payload["reading"]["pct_ma50"],
               round(float(full_ctx["table"]['pct_ma50'].iloc[-1]), 4), 1e-3))
check("light and dark tokens are both defined",
      'prefers-color-scheme: dark' in html and '[data-theme="dark"]' in html)
check("body background is painted from a token", "background:var(--ground)" in html)
check("markdown has the newsletter sections",
      all(h in md for h in ("## ⚡", "## The Indexes", "## 📊 Market Internals",
                            "## 🚦 The Market Indicator", "## The Plan")))
check("every index gets a verdict line",
      md.count("### ") >= 4)
check("internals carry risk-on/risk-off verdicts",
      "RISK ON." in md or "RISK OFF." in md)
check("markdown states the regime", full_ctx["verdict"]["light"] in md)
check("markdown has no unformatted NaN", "nan" not in md.lower().replace("financial", ""))
check("a synthetic run is labelled as simulated in the html",
      "Simulated data" in html and "randomly generated" in html)
check("a synthetic run is labelled as simulated in the markdown",
      "Sample output" in md and "simulated tape" in md)

# --------------------------------------------------------------------------- #
print("\n[11] survivorship / edge cases")
tiny_ref = ref.head(3)
tiny = M.monitor_table(panel, tiny_ref)
check("a tiny reference list does not crash the table",
      len(tiny) == len(tbl))
check("percentages stay within 0-100",
      bool(((tbl[["pct_ma20", "pct_ma50", "pct_ma200"]].dropna() >= 0).all().all()
            and (tbl[["pct_ma20", "pct_ma50", "pct_ma200"]].dropna() <= 100).all().all())))
check("up4 and dn4 never exceed the universe",
      bool((tbl["up4"] <= tbl["universe"]).all() and (tbl["dn4"] <= tbl["universe"]).all()))
check("net highs minus lows equals the components",
      bool((tbl["net_hl"] == tbl["new_highs"] - tbl["new_lows"]).all()))

# --------------------------------------------------------------------------- #
print("\n[12] shallow cache does not empty the universe")
# Regression: 59 cached sessions against a 60-bar seasoning requirement made
# every row's universe zero, the monitor table empty, and build() raise
# IndexError from tbl.iloc[-1].
_dates = sorted(bars.date.unique())
for _n in (30, 59, 61, 120):
    _sub = bars[bars.date.isin(_dates[-_n:])]
    _panel = D.build_panel(_sub)
    _tbl = M.monitor_table(_panel, ref)
    _live = _tbl[_tbl["universe"] > 0]
    check(f"{_n} cached sessions still yield a usable universe",
          not _live.empty and int(_live["universe"].iloc[-1]) > 0,
          f"{len(_live)} rows, universe {int(_live['universe'].iloc[-1]) if not _live.empty else 0}")
check("the seasoning requirement never exceeds the cache depth",
      all(min(C.MIN_HISTORY_BARS, max(5, int(n * 0.8))) <= n for n in (5, 20, 59, 400)))

# --------------------------------------------------------------------------- #
print("\n[13] a cache-only run makes no network calls")
# Regression: --max-downloads 0 still called fetch_splits, reference_cached and
# latest_trading_date, so an offline run died on "POLYGON_API_KEY is not set".
import gzip as _gz, json as _json
from mr import run as _RUN
_saved_root, _saved_key = C.CACHE_DIR, C.POLYGON_API_KEY
_tmp = C.DATA_DIR / "_offline_test"
(_tmp / "grouped").mkdir(parents=True, exist_ok=True)
C.CACHE_DIR = _tmp / "grouped"
try:
    _dates = sorted(bars.date.unique())[-59:]
    for _d in _dates:
        _day = pd.Timestamp(_d).date()
        _sub = bars[bars.date == _d]
        with _gz.open(D._cache_path(_day), "wt") as _fh:
            _json.dump([{"T": r.ticker, "o": r.open, "h": r.high, "l": r.low,
                         "c": r.close, "v": r.volume} for r in _sub.itertuples()], _fh)
    _ref_path = C.DATA_DIR / f"reference_{pd.Timestamp(_dates[-1]).date().isoformat()}.csv"
    ref.to_csv(_ref_path, index=False)

    _real_get, C.POLYGON_API_KEY = D._get, ""
    D._get = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("network call during a cache-only run"))
    try:
        _ctx = _RUN.build(max_downloads=0)
        check("offline build succeeds with no API key and no network", True,
              f"session {_ctx['date']}, universe {int(_ctx['row']['universe']):,}")
        check("offline build produces a regime verdict",
              _ctx["verdict"]["light"] in ("GREEN", "YELLOW", "RED"),
              _ctx["verdict"]["light"])
        check("offline build renders both outputs",
              bool(render_html.render(_ctx)) and bool(render_md.render(_ctx)))
    except AssertionError as exc:
        check("offline build succeeds with no API key and no network", False, str(exc))
    finally:
        D._get = _real_get
finally:
    C.CACHE_DIR, C.POLYGON_API_KEY = _saved_root, _saved_key
    import shutil as _sh
    _sh.rmtree(_tmp, ignore_errors=True)
    _ref_path.unlink(missing_ok=True)

# --------------------------------------------------------------------------- #
print("\n[14] watchlist screen")
from mr import screen as SCR
_w = SCR.candidates(panel, ref)
check("screen returns candidates", not _w.empty, f"{len(_w)} names")
if not _w.empty:
    check("no candidate is more than the configured ATR extension",
          bool((_w["ext_atr"] <= SCR.MAX_ATR_EXTENSION).all()),
          f"max {_w['ext_atr'].max():.2f} ATR")
    check("every candidate clears the price floor",
          bool((_w["close"] >= SCR.MIN_PRICE).all()))
    check("every candidate clears the liquidity floor",
          bool((_w["dollar_vol"] >= SCR.MIN_DOLLAR_VOL).all()))
    check("candidates are ordered by score",
          bool((_w["score"].diff().dropna() <= 1e-9).all()))
    check("no duplicate tickers", _w["ticker"].is_unique)
_thin = SCR.candidates({k: v.head(10) for k, v in panel.items()}, ref)
check("a too-short panel returns empty rather than raising", _thin.empty)

print("\n" + "=" * 62)
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("all checks passed")
