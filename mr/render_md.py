"""The written review, in the shape of the Smart Stocks newsletter.

Structure and voice follow the reference posts: a punchy thesis up top, then
index-by-index technicals with specific levels, then the internals each carrying
their own risk-on/risk-off verdict, then rotation, a watchlist, and the stance.

The prose is deterministic — every sentence is driven by a threshold in
config.py, so the review says the same thing given the same tape and nothing is
invented. NARRATIVE.md describes the layer that rewrites this in full voice.
"""

from __future__ import annotations

import pandas as pd

from . import config as C

LIGHT = {"GREEN": "🟢 GREEN", "YELLOW": "🟡 YELLOW", "RED": "🔴 RED"}


def _d(v, fmt="{:.1f}", dash="—"):
    return dash if v is None or pd.isna(v) else fmt.format(v)


# --------------------------------------------------------------------------- #
# Headline
# --------------------------------------------------------------------------- #

def _headline(ctx: dict) -> tuple[str, str]:
    """Title and subtitle, chosen from what actually happened."""
    v, row, prev = ctx["verdict"], ctx["row"], ctx["prev"]
    spy = ctx["indexes"].get("SPY")
    ret = float(spy["ret"].iloc[-1]) if spy is not None else 0.0
    up4, dn4 = int(row["up4"]), int(row["dn4"])
    p20 = row.get("pct_ma20")
    d20 = (p20 - prev["pct_ma20"]) if (prev is not None and pd.notna(p20)
                                       and pd.notna(prev.get("pct_ma20"))) else 0.0

    if v["changed_from"]:
        return (f"Regime Flips to {v['light'].title()} — Reset Your Size",
                f"The light changed from {v['changed_from'].lower()} today. "
                "What that means before the open.")
    if up4 >= C.BIG_COUNT and up4 > dn4 * 2:
        return ("A Real Thrust — Over 300 Names Up 4%",
                "Breadth like this does not happen in a weak tape. Now we need "
                "follow-through.")
    if dn4 >= C.BIG_COUNT and dn4 > up4 * 2:
        return ("Heavy Selling — The Washout Is Underway",
                "Cash is king until this stops. Where the flush signals sit.")
    if v["light"] == "RED":
        return ("The Market's in Trouble — Stay Ready",
                "Nothing is exciting me right now. Patience over forcing trades.")
    if v["light"] == "GREEN" and ret > 0:
        return ("Green Light, Follow-Through Confirmed",
                "The tape is paying for risk again. Where I'd be adding.")
    if d20 > 6:
        return ("Breadth Is Repairing — Character Change?",
                "More names joined the move today. One good day means very little.")
    if d20 < -6:
        return ("Participation Is Narrowing Fast",
                "The index is holding up better than the average stock. Watch this.")
    return (f"{'Constructive' if v['light'] == 'GREEN' else 'Mixed'} Tape, "
            "Selective Positioning",
            "What the internals are saying, and what I want to see next.")


# --------------------------------------------------------------------------- #
# Index sections
# --------------------------------------------------------------------------- #

def _index_block(tkr: str, idx: pd.DataFrame, row: pd.Series) -> list[str]:
    L: list[str] = []
    last = idx.iloc[-1]
    c = last["close"]

    above10 = c > last["ema10"]
    above21 = c > last["ema21"]
    above50 = pd.notna(last.get("ma50")) and c > last["ma50"]
    above200 = pd.notna(last.get("ma200")) and c > last["ma200"]
    rising = bool(last.get("ema10_rising")) and bool(last.get("ema21_rising"))

    if above10 and above21 and rising:
        verdict = "🚀 Trend Intact — Above a Rising 10 and 21 EMA"
    elif above21 and not above10:
        verdict = "⚠️ Losing the 10 EMA — First Crack"
    elif not above10 and not above21:
        verdict = "🔴 Below Both Short-Term Averages"
    else:
        verdict = "🔄 Mixed — Chopping Around the Short Averages"

    L.append(f"### {tkr}")
    L.append("")
    L.append(f"**{verdict}**")
    L.append("")
    L.append(f"{tkr} closed at **{c:.2f}** ({last['ret']:+.2f}%).")
    L.append("")

    levels = []
    for label, col in (("10 EMA", "ema10"), ("21 EMA", "ema21"),
                       ("50-day", "ma50"), ("200-day", "ma200")):
        ref = last.get(col)
        if pd.isna(ref):
            continue
        side = "above" if c > ref else "below"
        gap = abs(c / ref - 1) * 100
        levels.append(f"{side} the {label} at **{ref:.2f}** ({gap:.1f}% away)")
    if levels:
        L.append("It is " + "; ".join(levels) + ".")
        L.append("")

    dist = last.get("atr_dist")
    if pd.notna(dist):
        line = "50-day EMA" if C.ATR_LINE.lower() == "ema" else "50-day MA"
        if dist >= C.ATR_STRETCHED:
            L.append(f"> At **{dist:+.2f} ATR** from the {line}, this is stretched. "
                     "I would not be chasing an entry at this level. Ideally we get "
                     "an inside day or a couple of days of sideways action first.")
        elif dist <= C.ATR_OVERSOLD:
            L.append(f"> At **{dist:+.2f} ATR** from the {line}, the elastic band is "
                     "pulled well back. Watch for a reversal day rather than "
                     "shorting into it.")
        else:
            L.append(f"Sitting **{dist:+.2f} ATR** from the {line} — "
                     f"{'room to run' if abs(dist) < 2 else 'getting extended'}.")
        L.append("")
    return L


# --------------------------------------------------------------------------- #
# Internals, each with its own verdict
# --------------------------------------------------------------------------- #

def _internal(title: str, emoji: str, value: str, signal: str,
              body: list[str]) -> list[str]:
    L = [f"### {title}", "", f"**{signal}**", "", f"{emoji} **{value}**", ""]
    L += body + [""]
    return L


def _internals(ctx: dict) -> list[str]:
    row, prev = ctx["row"], ctx["prev"]
    L: list[str] = ["## 📊 Market Internals", ""]

    # --- % above the 20-day ------------------------------------------------ #
    p20 = row.get("pct_ma20")
    if pd.notna(p20):
        d = (p20 - prev["pct_ma20"]) if (prev is not None
                                         and pd.notna(prev.get("pct_ma20"))) else None
        sig = ("RISK ON." if p20 >= 60 else
               "RISK OFF." if p20 <= 30 else "Neutral, leaning cautious.")
        body = [
            f"{_d(p20)}% of the {int(row['universe']):,}-name universe is holding "
            f"its 20-day moving average"
            + (f", {'up' if d >= 0 else 'down'} {abs(d):.1f} points on the day."
               if d is not None else "."),
        ]
        if p20 <= C.PCT_ABOVE_EXTREME_LOW["ma20"]:
            body.append("This is washout territory. Lows are made here, not tops — "
                        "but it takes a reversal day to confirm it.")
        elif p20 >= C.PCT_ABOVE_EXTREME_HIGH["ma20"]:
            body.append("Everything is above its 20-day. That is not a sell signal, "
                        "but it is a bad place to be adding new risk.")
        elif d is not None and d > 5:
            body.append("More names joined the move today. That is the kind of "
                        "improvement that has to keep going for a day to matter.")
        L += _internal("MMTW — Stocks Above the 20-Day", "📊", f"{_d(p20)}%", sig, body)

    # --- % above the 50-day ------------------------------------------------ #
    p50 = row.get("pct_ma50")
    if pd.notna(p50):
        sig = ("RISK ON." if p50 >= 60 else
               "RISK OFF." if p50 <= 40 else "Neutral — the tape is split.")
        body = [f"{_d(p50)}% of the universe is above its 50-day.",
                "This is the intermediate trend reading. Above 60 the market pays "
                "for holding through noise; below 40 it does not."]
        L += _internal("Stocks Above the 50-Day", "📈", f"{_d(p50)}%", sig, body)

    # --- 4% movers --------------------------------------------------------- #
    up4, dn4 = int(row["up4"]), int(row["dn4"])
    ratio = up4 / dn4 if dn4 else float("inf")
    sig = ("RISK ON." if ratio >= 1.5 else
           "RISK OFF." if ratio <= 0.67 else "Balanced.")
    body = [f"**{up4}** names closed up 4% or more against **{dn4}** down 4% "
            f"({'∞' if dn4 == 0 else f'{ratio:.2f}'}× ratio)."]
    if max(up4, dn4) >= C.BIG_COUNT:
        side = "upside" if up4 > dn4 else "downside"
        body.append(f"A reading over {C.BIG_COUNT} on the {side} is a genuine "
                    "thrust, not noise. These cluster at turns.")
    if pd.notna(row.get("ratio_10d")):
        body.append(f"Over ten sessions the ratio is "
                    f"**{row['ratio_10d']:.2f}**, which is the reading I weight more "
                    "than any single day.")
    L += _internal("4% Movers — Momentum Bursts", "⚡",
                   f"{up4} up / {dn4} down", sig, body)

    # --- distribution ------------------------------------------------------ #
    for tkr, info in ctx["dd"].items():
        n = info["count"]
        sig = ("RISK OFF." if n >= C.DD_HEAVY else
               "RISK ON." if n <= C.DD_LIGHT else "Warming up — watch it.")
        body = [f"**{n}** distribution day{'s' if n != 1 else ''} on {tkr} in the "
                f"last {C.DD_WINDOW} sessions."]
        if info["days"]:
            body.append("Most recent: " + ", ".join(
                f"{d['date']:%b %-d} ({d['ret']:+.2f}%)" for d in info["days"][-4:]) + ".")
        if n >= C.DD_HEAVY:
            body.append("Institutions are selling into strength. That is the single "
                        "reading that most often precedes a real break.")
        f = ctx["ftd"].get(tkr)
        if f:
            body.append(f"Last follow-through day: **{f['date']:%b %-d}**, day "
                        f"{f['day']} of the attempt off the {f['low_date']:%b %-d} "
                        f"low, {f['gain']:+.2f}%.")
        L += _internal(f"{tkr} Distribution Days", "🚨", f"{n} of {C.DD_WINDOW}",
                       sig, body)

    # --- leadership -------------------------------------------------------- #
    if pd.notna(row.get("mli_pct")) and int(row.get("mli_n", 0)) > 0:
        mli = row["mli_pct"]
        sig = "RISK ON." if mli >= 0 else "RISK OFF."
        body = [f"The leadership basket ({int(row['mli_n'])} names) moved "
                f"**{mli:+.2f}%** today with {_d(row.get('mli_rising'))}% of members up."]
        if pd.notna(row.get("mli_10d")):
            body.append(f"Ten-session leadership return: **{row['mli_10d']:+.1f}%**. "
                        "When leaders diverge from the index, believe the leaders.")
        L += _internal("MLI — Leadership Index", "👑", f"{mli:+.2f}%", sig, body)

    # --- net new highs ----------------------------------------------------- #
    if pd.notna(row.get("net_hl")):
        net = int(row["net_hl"])
        sig = "RISK ON." if net >= 25 else "RISK OFF." if net <= -25 else "Neutral."
        L += _internal("Net New 52-Week Highs", "🏔️", f"{net:+d}", sig,
                       [f"{int(row['new_highs'])} new highs against "
                        f"{int(row['new_lows'])} new lows."])
    return L


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def render(ctx: dict) -> str:
    row, v, d = ctx["row"], ctx["verdict"], ctx["date"]
    title, subtitle = _headline(ctx)

    L: list[str] = []
    A = L.append

    A(f"# {title}")
    A("")
    A(f"*{subtitle}*")
    A("")
    A(f"**{d:%A, %B %-d, %Y}** · Market Indicator: **{LIGHT[v['light']]}** "
      f"(score {v['total']:+d} of ±{v['max']})")
    A("")

    if ctx.get("synthetic"):
        A("> **Sample output — generated market data.** Every figure below comes "
          "from a simulated tape used to exercise the pipeline. Nothing here "
          "describes a real market or a real security.")
        A("")

    A("---")
    A("")

    # ---- thesis ---------------------------------------------------------- #
    A(f"## ⚡ {'Where We Stand' if not v['changed_from'] else 'The Light Changed'}")
    A("")
    if v["changed_from"]:
        A(f"The market indicator moved from **{v['changed_from']}** to "
          f"**{v['light']}** today. {v['stance']}")
        A("")
    A(f"**{v['stance']}**")
    A("")
    spy = ctx["indexes"].get("SPY")
    if spy is not None:
        r = float(spy["ret"].iloc[-1])
        word = ("a strong day" if r > 1 else "a firm day" if r > 0.2 else
                "a nasty red day" if r < -1 else "a soft day" if r < -0.2 else
                "a flat, going-nowhere day")
        A(f"SPY had {word} at {r:+.2f}%, and "
          f"{int(row['up4'])} names closed up 4% against {int(row['dn4'])} down.")
        A("")
    for note in v["stretch"]:
        A(f"> {note}")
        A("")

    # ---- indexes ---------------------------------------------------------- #
    A("---")
    A("")
    A("## The Indexes")
    A("")
    for tkr in C.INDEX_TICKERS:
        idx = ctx["indexes"].get(tkr)
        if idx is not None and not idx.empty:
            L.extend(_index_block(tkr, idx, row))

    # ---- internals -------------------------------------------------------- #
    A("---")
    A("")
    L.extend(_internals(ctx))

    # ---- the indicator ---------------------------------------------------- #
    A("---")
    A("")
    A("## 🚦 The Market Indicator")
    A("")
    A(f"**{LIGHT[v['light']]}** — {v['stance']}")
    A("")
    A("| Component | | Reading |")
    A("|---|:--:|---|")
    for c in v["components"]:
        mark = {1: "🟢", 0: "⚪", -1: "🔴"}[c["points"]]
        A(f"| {c['name']} | {mark} | {c['reason']} |")
    A(f"| **Composite** | **{v['total']:+d}** | "
      f"green at ≥{C.REGIME_GREEN_AT}, red at ≤{C.REGIME_RED_AT} |")
    A("")

    # ---- ETF leaderboard --------------------------------------------------- #
    lb = ctx.get("leaderboard") or {}
    if lb:
        A("---")
        A("")
        A("## 🏆 ETF Leaders and Laggards")
        A("")
        A("Sectors and themes ranked together. Windows are trading sessions, so a "
          "month is 21 and six months is 126.")
        A("")
        A("| Window | SPY | Best 3 | Worst 3 |")
        A("|---|---:|---|---|")
        for key in ("d1", "w1", "m1", "m3", "m6"):
            w = lb.get(key)
            if not w:
                continue
            fmt = lambda rows: " · ".join(
                f"{r['ticker']} {r['ret']:+.1f}%" for r in rows)
            spy = "—" if w.get("spy") is None else f"{w['spy']:+.1f}%"
            A(f"| {w['label']} | {spy} | {fmt(w['best'])} | {fmt(w['worst'])} |")
        A("")

    # ---- rotation --------------------------------------------------------- #
    sectors, themes = ctx["sectors"], ctx["themes"]
    if not sectors.empty:
        A("---")
        A("")
        A("## 🔄 Where the Money Is Going")
        A("")
        lead = sectors.head(3)
        lag = sectors.tail(3).iloc[::-1]
        A("**Leading (1 month):** " + ", ".join(
            f"{r['name']} {_d(r.get('m1'), '{:+.1f}')}%" for _, r in lead.iterrows()))
        A("")
        A("**Lagging:** " + ", ".join(
            f"{r['name']} {_d(r.get('m1'), '{:+.1f}')}%" for _, r in lag.iterrows()))
        A("")
        A("| Sector | 1D | 1W | 1M | vs SPY | >50D |")
        A("|---|---:|---:|---:|---:|:--:|")
        for _, r in sectors.iterrows():
            A(f"| {r['name']} | {_d(r.get('d1'), '{:+.2f}')}% | "
              f"{_d(r.get('w1'), '{:+.2f}')}% | {_d(r.get('m1'), '{:+.2f}')}% | "
              f"{_d(r.get('rs_m1'), '{:+.2f}')}% | "
              f"{'✓' if r.get('above_ma50') else '·'} |")
        A("")
    if not themes.empty:
        A("**Themes:** " + " · ".join(
            f"{r['name']} {_d(r.get('m1'), '{:+.1f}')}%"
            for _, r in themes.sort_values('m1', ascending=False).iterrows()))
        A("")

    # ---- watchlist -------------------------------------------------------- #
    watch = ctx.get("watchlist")
    if watch is not None and not watch.empty:
        A("---")
        A("")
        A("## 📋 Tickers Pasted for Your Watchlist")
        A("")
        A(", ".join(watch["ticker"].tolist()))
        A("")
        A("| Ticker | Close | 1D | 1W | 3M RS | Posture |")
        A("|---|---:|---:|---:|---:|---|")
        from . import screen as SCR
        for _, r in watch.iterrows():
            A(f"| **{r['ticker']}** | {r['close']:.2f} | {r['day']:+.2f}% | "
              f"{r['week']:+.2f}% | {r['rs']:+.1f}% | {SCR.describe(r)} |")
        A("")
        A("*Screened on relative strength and trend, then filtered for names that "
          f"are not more than {SCR.MAX_ATR_EXTENSION:.0f} ATR above their 20-day — "
          "strength you can still buy, rather than strength you would be chasing.*")
        A("")

    # ---- the plan --------------------------------------------------------- #
    A("---")
    A("")
    A("## The Plan")
    A("")
    plan = {
        "GREEN": [
            "**Full size** on setups that trigger.",
            "Add to winners showing follow-through. Stops at logical lows.",
            "Only trim into strength if a name gets more than ~7 ATR extended.",
        ],
        "YELLOW": [
            "**Half size.** A-setups only.",
            "Cut anything that fails its entry pivot the same session.",
            "Keep a cash buffer. This tape does not pay for conviction.",
        ],
        "RED": [
            "**No new long risk** until the light turns.",
            "Honour every stop. No exceptions, no averaging down.",
            "Build the watchlist instead — names holding their 50-day while the "
            "index breaks are the next leaders.",
        ],
    }[v["light"]]
    for p in plan:
        A(f"- {p}")
    for note in v["stretch"]:
        A(f"- {note}")
    A("")
    A("---")
    A("")
    stamp = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M")
    A(f"*Universe {int(row['universe']):,} US common stocks and ADRs, close ≥ "
      f"${C.MIN_CLOSE:.0f} and volume ≥ {C.MIN_VOLUME:,}. "
      f"Generated {stamp} UTC from Polygon "
      "end-of-day data. Personal research notes — not investment advice.*")
    return "\n".join(L)
