"""Self-contained HTML dashboard for the daily market review.

Emits an Artifact-ready fragment: no doctype/head/body wrapper, all CSS and JS
inline, data embedded as JSON. Google Fonts is the one external host used.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import config as C

# --------------------------------------------------------------------------- #
# Design tokens
#
# Palette validated with the dataviz validator against the panel surfaces:
#   light #12866B / #C93B3B  -> CVD dE 8.5 (deutan), normal-vision 27.2  PASS
#   dark  #25A188 / #E05F4E  -> CVD dE 9.6 (deutan), normal-vision 26.4  PASS
# The brass accent is UI chrome only and never appears as a data mark beside
# the up/down pair. Every heat cell prints its own signed number, so colour
# never carries meaning on its own.
# --------------------------------------------------------------------------- #

CSS = """
:root{
  color-scheme: light;
  --ground:#f4f6f8; --panel:#ffffff; --panel-2:#fafbfc;
  --ink:#111820; --ink-2:#3a4756; --muted:#5d6b7c;
  --rule:#dce2e9; --rule-strong:#c3ccd6;
  --up:#12866b; --down:#c93b3b; --accent:#8a6a12; --accent-soft:#f3ead3;
  --grid:#e8ecf1;
  --shadow:0 1px 2px rgba(17,24,32,.06), 0 8px 24px -16px rgba(17,24,32,.24);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme: dark;
    --ground:#0d1117; --panel:#161b22; --panel-2:#1b212a;
    --ink:#e7edf5; --ink-2:#c2ccd9; --muted:#8b9aac;
    --rule:#232b36; --rule-strong:#33404f;
    --up:#25a188; --down:#e05f4e; --accent:#d9a441; --accent-soft:#2a2415;
    --grid:#1f2731;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 28px -18px rgba(0,0,0,.9);
  }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --ground:#0d1117; --panel:#161b22; --panel-2:#1b212a;
  --ink:#e7edf5; --ink-2:#c2ccd9; --muted:#8b9aac;
  --rule:#232b36; --rule-strong:#33404f;
  --up:#25a188; --down:#e05f4e; --accent:#d9a441; --accent-soft:#2a2415;
  --grid:#1f2731;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 28px -18px rgba(0,0,0,.9);
}

*{box-sizing:border-box}
body{
  background:var(--ground); color:var(--ink);
  font-family:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  font-size:14px; line-height:1.55; margin:0;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1180px; margin:0 auto; padding:28px 20px 64px; display:flex;
      flex-direction:column; gap:20px}

/* ---------- typography ---------- */
h1,h2,h3,.lbl,.chip{font-family:"Archivo",-apple-system,"Segoe UI",sans-serif}
h1{font-size:26px; font-weight:700; letter-spacing:-.018em; margin:0; text-wrap:balance}
h2{font-size:12px; font-weight:700; letter-spacing:.1em; text-transform:uppercase;
   color:var(--muted); margin:0 0 12px}
.num{font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
     font-variant-numeric:tabular-nums; font-feature-settings:"tnum" 1}
.lbl{font-size:10px; font-weight:600; letter-spacing:.11em; text-transform:uppercase;
     color:var(--muted)}

/* ---------- masthead ---------- */
.mast{display:flex; flex-wrap:wrap; gap:20px; align-items:flex-start;
      justify-content:space-between; padding-bottom:18px;
      border-bottom:2px solid var(--ink)}
.mast .meta{display:flex; flex-wrap:wrap; gap:6px 16px; margin-top:8px}
.mast .meta span{color:var(--muted); font-size:12px}
.mast .meta b{color:var(--ink-2); font-weight:500}

.regime{display:flex; align-items:center; gap:14px; padding:12px 18px;
        border-radius:3px; border:1px solid var(--rule-strong);
        background:var(--panel); box-shadow:var(--shadow)}
.regime .dot{width:12px; height:12px; border-radius:50%; flex:none}
.regime.green .dot{background:var(--up)}
.regime.yellow .dot{background:var(--accent)}
.regime.red .dot{background:var(--down)}
.regime.green{border-left:4px solid var(--up)}
.regime.yellow{border-left:4px solid var(--accent)}
.regime.red{border-left:4px solid var(--down)}
.regime .word{font-family:"Archivo",sans-serif; font-size:22px; font-weight:800;
              letter-spacing:.04em; line-height:1}
.regime .stance{font-size:12px; color:var(--muted); max-width:24ch; line-height:1.35}
.regime .score{margin-left:auto; text-align:right}
.regime .score .v{font-size:20px; font-weight:600}

.synth{padding:16px 20px; border-radius:3px; background:var(--down); color:#fff;
       font-size:14px; line-height:1.55; order:-1}
.synth .hd{font-family:"Archivo",sans-serif; font-weight:800; font-size:16px;
           letter-spacing:.04em; text-transform:uppercase; margin-bottom:5px}
.synth b{font-weight:600}
.alert{padding:10px 14px; border-left:3px solid var(--accent);
       background:var(--accent-soft); border-radius:0 3px 3px 0; font-size:13px}

/* ---------- reading tiles ---------- */
/* four across, two rows — the eight tiles fill the grid exactly, so no cell
   is ever left empty showing the rule colour through */
.tiles{display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:1px;
       background:var(--rule); border:1px solid var(--rule); border-radius:3px;
       overflow:hidden}
@media (max-width:900px){.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:480px){.tiles{grid-template-columns:minmax(0,1fr)}}
.tile{background:var(--panel); padding:13px 15px 12px; display:flex;
      flex-direction:column; gap:5px; min-height:104px}
.tile .v{font-size:27px; font-weight:500; line-height:1.05; letter-spacing:-.02em}
.tile .v.up{color:var(--up)} .tile .v.down{color:var(--down)}
.tile .sub{font-size:11px; color:var(--muted); margin-top:auto; line-height:1.35}
.tile .spark{margin-top:auto; height:26px}
.tile .split{display:flex; align-items:baseline; gap:9px}
.tile .split .sep{color:var(--rule-strong); font-size:19px; font-weight:300}

/* ---------- panels ---------- */
.panel{background:var(--panel); border:1px solid var(--rule); border-radius:3px;
       padding:18px}
.cols{display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:20px;
      align-items:start}
@media (max-width:860px){.cols{grid-template-columns:minmax(0,1fr)}}

/* ---------- tables ---------- */
.scroll{overflow-x:auto; margin:0 -18px; padding:0 18px}
table{border-collapse:collapse; width:100%; font-size:12px}
th{font-family:"Archivo",sans-serif; font-size:9.5px; font-weight:700;
   letter-spacing:.08em; text-transform:uppercase; color:var(--muted);
   text-align:right; padding:0 9px 7px; white-space:nowrap;
   border-bottom:1px solid var(--rule-strong)}
th:first-child,td:first-child{text-align:left}
td{padding:5px 9px; text-align:right; white-space:nowrap;
   border-bottom:1px solid var(--grid)}
tbody tr:hover td{background:var(--panel-2)}
.monitor td:first-child{font-weight:600; color:var(--ink-2)}
.leadgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(196px,1fr));gap:12px}
.leadcard{border:1px solid var(--rule);border-radius:8px;padding:11px 12px 9px}
.leadcard .hd{display:flex;justify-content:space-between;align-items:baseline;
  margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid var(--rule)}
.leadcard .hd .w{font-weight:700;font-size:12.5px;letter-spacing:.02em}
.leadcard .hd .bm{font-size:10.5px;color:var(--muted)}
.leadrow{display:grid;grid-template-columns:42px 1fr auto;gap:7px;align-items:baseline;
  padding:2.5px 0;font-size:11.5px}
.leadrow .tk{font-weight:600}
.leadrow .nm{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.leadrow .rt{text-align:right;font-weight:600}
.leadrow.up .tk,.leadrow.up .rt{color:var(--up)}
.leadrow.dn .tk,.leadrow.dn .rt{color:var(--down)}
.leadsep{height:1px;background:var(--rule);margin:6px 0}
.leadcard .thm{font-size:9px;color:var(--muted);border:1px solid var(--rule);
  border-radius:3px;padding:0 3px;margin-left:4px;vertical-align:1px}
.holdgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(218px,1fr));gap:12px}
.holdcard{border:1px solid var(--rule);border-radius:8px;padding:10px 11px 8px}
.holdcard .hd{display:flex;justify-content:space-between;align-items:baseline;
  margin-bottom:7px;padding-bottom:5px;border-bottom:1px solid var(--rule)}
.holdcard .hd .tk{font-weight:700;font-size:12.5px}
.holdcard .hd .nm{font-size:10px;color:var(--muted);overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;margin-left:6px}
.holdrow{display:grid;grid-template-columns:48px 1fr auto;gap:6px;align-items:center;
  padding:2px 0;font-size:11.5px}
.holdrow .sym{font-weight:600}
.holdrow .bar{height:5px;border-radius:3px;background:var(--grid);overflow:hidden}
.holdrow .bar i{display:block;height:100%;background:var(--accent,#4C72B0);border-radius:3px}
.holdrow .wt{text-align:right;font-size:11px;color:var(--muted)}
.holdcard .tot{margin-top:6px;padding-top:5px;border-top:1px solid var(--rule);
  font-size:10px;color:var(--muted);display:flex;justify-content:space-between}
.monitor .gsep{border-left:1px solid var(--rule)}

/* heat cells: the number is always printed, so colour is never the only cue */
.h-up-1{background:color-mix(in srgb,var(--up) 13%,transparent)}
.h-up-2{background:color-mix(in srgb,var(--up) 32%,transparent); font-weight:600}
.h-dn-1{background:color-mix(in srgb,var(--down) 13%,transparent)}
.h-dn-2{background:color-mix(in srgb,var(--down) 32%,transparent); font-weight:600}
.t-up{color:var(--up)} .t-dn{color:var(--down)}

/* ---------- component scoring ---------- */
.comp{display:flex; flex-direction:column; gap:0}
.comp .row{display:grid; grid-template-columns:15px 1fr auto; gap:11px;
           align-items:baseline; padding:8px 0; border-bottom:1px solid var(--grid)}
.comp .row:last-child{border-bottom:0}
.comp .pip{width:15px; height:15px; border-radius:2px; align-self:center;
           display:grid; place-items:center; font-size:9px; font-weight:700;
           font-family:"IBM Plex Mono",monospace}
.comp .pip.p{background:color-mix(in srgb,var(--up) 20%,transparent); color:var(--up)}
.comp .pip.n{background:color-mix(in srgb,var(--down) 20%,transparent); color:var(--down)}
.comp .pip.z{background:var(--grid); color:var(--muted)}
.comp .name{font-weight:600; font-size:12.5px}
.comp .why{font-size:11.5px; color:var(--muted); grid-column:2/4; margin-top:-4px}
.comp .total{display:flex; justify-content:space-between; padding-top:11px;
             margin-top:4px; border-top:2px solid var(--ink); font-weight:700}

/* ---------- index rows ---------- */
.idx{display:flex; flex-direction:column}
.idx .r{display:grid; grid-template-columns:52px 1fr; gap:12px; padding:9px 0;
        border-bottom:1px solid var(--grid); align-items:center}
.idx .r:last-child{border-bottom:0}
.idx .tk{font-family:"Archivo",sans-serif; font-weight:700; font-size:14px}
.idx .px{display:flex; flex-wrap:wrap; gap:4px 12px; align-items:baseline}
.idx .px .p{font-size:14px; font-weight:500}
.idx .px .c{font-size:12px}
.idx .rel{display:flex; gap:4px; margin-left:auto}
.pill{font-family:"IBM Plex Mono",monospace; font-size:9.5px; padding:2px 5px;
      border-radius:2px; border:1px solid var(--rule-strong); color:var(--muted);
      letter-spacing:.02em}
.pill.on{border-color:transparent; background:color-mix(in srgb,var(--up) 18%,transparent);
         color:var(--up)}
.pill.off{border-color:transparent; background:color-mix(in srgb,var(--down) 18%,transparent);
          color:var(--down)}

/* ---------- distribution ---------- */
.dd{display:flex; flex-direction:column; gap:11px}
.dd .r{display:flex; align-items:center; gap:11px; flex-wrap:wrap}
.dd .ticks{display:flex; gap:2px}
.dd .tick{width:7px; height:19px; border-radius:1px; background:var(--grid)}
.dd .tick.hit{background:var(--down)}
.dd .verdict{font-size:11.5px; color:var(--muted)}

/* ---------- charts ---------- */
.chart{width:100%}
.chart text{font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums}
.tip{position:fixed; pointer-events:none; z-index:50; opacity:0; transition:opacity .1s;
     background:var(--panel); border:1px solid var(--rule-strong); border-radius:3px;
     padding:6px 9px; font-size:11.5px; box-shadow:var(--shadow); white-space:nowrap;
     font-family:"IBM Plex Mono",monospace}
.tip.on{opacity:1}
.legend{display:flex; gap:14px; flex-wrap:wrap; font-size:11px; color:var(--muted);
        margin-top:10px}
.legend i{display:inline-block; width:9px; height:9px; border-radius:2px;
          margin-right:5px; vertical-align:baseline}

/* ---------- key ---------- */
.key{display:flex; flex-wrap:wrap; gap:7px 18px; font-size:10.5px; color:var(--muted);
     margin-top:12px}
.key span{display:flex; align-items:center; gap:6px}
.key i{width:11px; height:11px; border-radius:2px; border:1px solid var(--rule)}

footer{font-size:11px; color:var(--muted); border-top:1px solid var(--rule);
       padding-top:14px; line-height:1.6}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
"""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _n(v):
    """JSON-safe number."""
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if pd.isna(v) else round(float(v), 4)
    return v


def _series(tbl: pd.DataFrame, cols: list[str], n: int = 70) -> dict:
    tail = tbl.tail(n)
    out = {"dates": [d.strftime("%Y-%m-%d") for d in tail.index]}
    for c in cols:
        out[c] = [_n(v) for v in tail[c]] if c in tail else []
    return out


def build_payload(ctx: dict) -> dict:
    tbl, row, prev = ctx["table"], ctx["row"], ctx["prev"]
    cols = ["up4", "dn4", "pct_ma20", "pct_ma50", "pct_ma200", "mli_pct", "mli_rising",
            "mli_n", "mli_index", "mli_10d", "net_hl", "new_highs", "new_lows",
            "spy_atr", "qqq_atr", "universe", "spy_close", "ratio_10d"]

    rows = []
    for ts, r in tbl.tail(C.TABLE_ROWS).iloc[::-1].iterrows():
        rows.append({"date": ts.strftime("%m-%d"),
                     **{c: _n(r.get(c)) for c in cols}})

    idx_snap = {}
    for tkr, idf in ctx["indexes"].items():
        if idf is None or idf.empty:
            continue
        last = idf.iloc[-1]
        idx_snap[tkr] = {
            "close": _n(last["close"]), "ret": _n(last["ret"]),
            "atr_dist": _n(last.get("atr_dist")),
            "vs": {
                "10 EMA": bool(last["close"] > last["ema10"]),
                "21 EMA": bool(last["close"] > last["ema21"]),
                "50 MA": None if pd.isna(last.get("ma50")) else bool(last["close"] > last["ma50"]),
                "200 MA": None if pd.isna(last.get("ma200")) else bool(last["close"] > last["ma200"]),
            },
            "rising": bool(last.get("ema10_rising")) and bool(last.get("ema21_rising")),
        }

    dd = {t: {"count": v["count"],
              "days": [d["date"].strftime("%m-%d") for d in v["days"]],
              "window": [d.strftime("%m-%d") for d in
                         ctx["indexes"][t].tail(C.DD_WINDOW).index]}
          for t, v in ctx["dd"].items() if ctx["indexes"].get(t) is not None}

    ftd = {t: (None if f is None else
               {"date": f["date"].strftime("%Y-%m-%d"), "day": f["day"],
                "gain": round(f["gain"], 2), "low": f["low_date"].strftime("%m-%d")})
           for t, f in ctx["ftd"].items()}

    def _delta(col):
        if prev is None or pd.isna(prev.get(col)) or pd.isna(row.get(col)):
            return None
        return round(float(row[col]) - float(prev[col]), 2)

    return {
        "meta": {
            "date": ctx["date"].strftime("%Y-%m-%d"),
            "date_long": ctx["date"].strftime("%a %d %b %Y"),
            "universe": _n(row["universe"]),
            "generated": ctx["generated"],
            "min_close": C.MIN_CLOSE, "min_volume": C.MIN_VOLUME,
            "dd_window": C.DD_WINDOW,
            "synthetic": bool(ctx.get("synthetic")),
            "green_at": C.REGIME_GREEN_AT, "red_at": C.REGIME_RED_AT,
            "thresholds": {
                "low": C.PCT_ABOVE_LOW, "high": C.PCT_ABOVE_HIGH,
                "xlow": C.PCT_ABOVE_EXTREME_LOW, "xhigh": C.PCT_ABOVE_EXTREME_HIGH,
                "big": C.BIG_COUNT, "atr_hi": C.ATR_STRETCHED, "atr_lo": C.ATR_OVERSOLD,
            },
        },
        "reading": {c: _n(row.get(c)) for c in cols},
        "deltas": {c: _delta(c) for c in ("pct_ma20", "pct_ma50", "pct_ma200")},
        "rows": rows,
        "series": _series(tbl, ["pct_ma20", "pct_ma50", "pct_ma200", "mli_index",
                                "net_hl", "up4", "dn4"]),
        "regime": {
            "light": ctx["verdict"]["light"], "stance": ctx["verdict"]["stance"],
            "total": ctx["verdict"]["total"], "max": ctx["verdict"]["max"],
            "components": ctx["verdict"]["components"],
            "stretch": ctx["verdict"]["stretch"],
            "changed_from": ctx["verdict"]["changed_from"],
        },
        "indexes": idx_snap,
        "dd": dd, "ftd": ftd,
        "sectors": ctx["sectors"].replace({np.nan: None}).to_dict("records"),
        "themes": ctx["themes"].replace({np.nan: None}).to_dict("records"),
        "leaderboard": ctx.get("leaderboard") or {},
        "holdings": ctx.get("holdings") or {},
        "etf_names": {**C.SECTOR_ETFS, **C.THEME_ETFS},
    }


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

JS = r"""
const D = window.__MR__;
const $ = (s,r=document)=>r.querySelector(s);
const el = (t,c,txt)=>{const n=document.createElement(t); if(c)n.className=c;
  if(txt!==undefined)n.textContent=txt; return n;};
const f1=v=>v==null?'—':v.toFixed(1);
const f2=v=>v==null?'—':v.toFixed(2);
const sg=(v,d=2)=>v==null?'—':(v>=0?'+':'')+v.toFixed(d);
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();

/* ---------- masthead ---------- */
const m = D.meta, R = D.regime;
$('#date').textContent = m.date_long;
$('#universe').textContent = m.universe.toLocaleString();
if (m.synthetic){
  const b = document.createElement('div');
  b.className = 'synth';
  b.innerHTML = '<div class="hd">Simulated data — not the real market</div>'
    + 'Every price, count and percentage on this page was <b>randomly generated</b> to '
    + 'exercise the pipeline. The tickers are placeholders, the index levels are '
    + 'invented, and none of it describes any real security on any real date. '
    + 'Do not read a single number here as a market fact.';
  document.querySelector('.wrap').prepend(b);
}
const rg = $('#regime');
rg.className = 'regime ' + R.light.toLowerCase();
$('#rgword').textContent = R.light;
$('#rgstance').textContent = R.stance;
$('#rgscore').textContent = (R.total>=0?'+':'') + R.total;
$('#rgmax').textContent = '± ' + R.max;
if (R.changed_from){
  const a = el('div','alert');
  a.innerHTML = 'Regime changed from <b>'+R.changed_from+'</b> to <b>'+R.light+
                '</b> today — reset position size before the open.';
  $('#alerts').appendChild(a);
}
R.stretch.forEach(s=>{const a=el('div','alert'); a.textContent=s; $('#alerts').appendChild(a);});

/* ---------- tiles ---------- */
const rd = D.reading, dl = D.deltas;
function tile(label, body, sub, spark){
  const t = el('div','tile');
  t.appendChild(el('div','lbl',label));
  t.appendChild(body);
  if (spark) t.appendChild(spark);
  else if (sub) t.appendChild(el('div','sub',sub));
  return t;
}
function big(v, cls){ const d=el('div','v num'+(cls?' '+cls:'')); d.textContent=v; return d; }

const tiles = $('#tiles');

/* UP4 / DN4 — the two counts sit in fixed positions, so the pairing is
   structural and does not rely on the green/red hues alone. */
{
  const b = el('div','split');
  const u = el('span','v num up'); u.textContent = rd.up4;
  const s = el('span','sep'); s.textContent='/';
  const d = el('span','v num down'); d.textContent = rd.dn4;
  b.append(u,s,d);
  const ratio = rd.dn4 ? (rd.up4/rd.dn4) : null;
  tiles.appendChild(tile('Up 4% / Down 4%', b,
    (ratio==null?'no down side':(ratio>=1? f2(ratio)+'× more up than down'
                                         : f2(1/ratio)+'× more down than up'))
    + (Math.max(rd.up4,rd.dn4)>=m.thresholds.big ? ' · thrust ≥'+m.thresholds.big : '')));
}

function pctTile(label, key, sparkKey){
  const b = big(f1(rd[key]));
  const d = dl[key];
  const t = tile(label, b, null, spark(sparkKey, 'pct'));
  const s = el('div','sub');
  s.textContent = (d==null? '' : sg(d,1)+' on the day');
  t.insertBefore(s, t.lastChild);
  return t;
}
tiles.appendChild(pctTile('% above 20D MA','pct_ma20','pct_ma20'));
tiles.appendChild(pctTile('% above 50D MA','pct_ma50','pct_ma50'));
tiles.appendChild(pctTile('% above 200D MA','pct_ma200','pct_ma200'));

for (const tk of ['spy','qqq']){
  const v = rd[tk+'_atr'];
  const cls = v==null?'':(v>=m.thresholds.atr_hi?'down':(v<=m.thresholds.atr_lo?'up':''));
  tiles.appendChild(tile(tk.toUpperCase()+' vs 50D EMA', big(sg(v), cls),
    'in 14-day ATR units' + (v!=null && v>=m.thresholds.atr_hi ? ' · extended'
      : (v!=null && v<=m.thresholds.atr_lo ? ' · washed out' : ''))));
}
tiles.appendChild(tile('Leadership index (MLI)',
  big(sg(rd.mli_pct), rd.mli_pct==null?'':(rd.mli_pct>=0?'up':'down')),
  rd.mli_n + ' names · ' + f1(rd.mli_rising) + '% of them up', spark('mli_index','mli')));
tiles.appendChild(tile('Net new 52-week highs',
  big(sg(rd.net_hl,0), rd.net_hl==null?'':(rd.net_hl>=0?'up':'down')),
  rd.new_highs + ' highs · ' + rd.new_lows + ' lows'));

/* ---------- sparklines ---------- */
function spark(key, kind){
  const vals = D.series[key]; if(!vals || vals.length<5) return null;
  const dates = D.series.dates;
  const W=190,H=26,P=2;
  const clean = vals.map(v=>v==null?NaN:v);
  const lo=Math.min(...clean.filter(v=>!isNaN(v))), hi=Math.max(...clean.filter(v=>!isNaN(v)));
  const rng = (hi-lo)||1;
  const x=i=>P+i*(W-2*P)/(clean.length-1);
  const y=v=>H-P-((v-lo)/rng)*(H-2*P);
  let dstr='', started=false;
  clean.forEach((v,i)=>{ if(isNaN(v)) return;
    dstr += (started?'L':'M')+x(i).toFixed(1)+' '+y(v).toFixed(1)+' '; started=true; });
  const last = clean.length-1;
  const stroke = kind==='mli'
    ? (clean[last]>=clean[0] ? css('--up') : css('--down')) : css('--accent');
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`); svg.setAttribute('class','spark chart');
  svg.setAttribute('preserveAspectRatio','none'); svg.setAttribute('width','100%');
  svg.setAttribute('height',H);
  svg.innerHTML =
    `<path d="${dstr}" fill="none" stroke="${stroke}" stroke-width="1.6"
       stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
     <circle cx="${x(last).toFixed(1)}" cy="${y(clean[last]).toFixed(1)}" r="2.4"
       fill="${stroke}"/>`;
  svg.setAttribute('role','img');
  svg.setAttribute('aria-label',
    `${key} over the last ${clean.length} sessions, ${f1(lo)} to ${f1(hi)}`);
  svg.addEventListener('pointermove', ev=>{
    const bb=svg.getBoundingClientRect();
    const i=Math.round(((ev.clientX-bb.left)/bb.width)*(clean.length-1));
    if(i<0||i>=clean.length||isNaN(clean[i])) return;
    showTip(ev, `${dates[i]}  ${f1(clean[i])}`);
  });
  svg.addEventListener('pointerleave', hideTip);
  return svg;
}

/* ---------- tooltip ---------- */
const tip = el('div','tip'); document.body.appendChild(tip);
function showTip(ev, text){
  tip.textContent = text; tip.classList.add('on');
  tip.style.left = Math.min(ev.clientX+12, innerWidth-tip.offsetWidth-8)+'px';
  tip.style.top  = (ev.clientY-34)+'px';
}
function hideTip(){ tip.classList.remove('on'); }

/* ---------- index rows ---------- */
const idxWrap = $('#indexes');
for (const [tk,v] of Object.entries(D.indexes)){
  const r = el('div','r');
  r.appendChild(el('div','tk',tk));
  const px = el('div','px');
  const p = el('span','p num'); p.textContent = f2(v.close);
  const c = el('span','c num '+(v.ret>=0?'t-up':'t-dn')); c.textContent = sg(v.ret)+'%';
  px.append(p,c);
  const rel = el('div','rel');
  for (const [name,ok] of Object.entries(v.vs)){
    if (ok===null) continue;
    const b = el('span','pill '+(ok?'on':'off'), (ok?'▲ ':'▼ ')+name);
    rel.appendChild(b);
  }
  if (v.atr_dist!=null) rel.appendChild(el('span','pill', sg(v.atr_dist)+' ATR'));
  px.appendChild(rel);
  r.appendChild(px);
  idxWrap.appendChild(r);
}

/* ---------- regime components ---------- */
const comp = $('#components');
R.components.forEach(c=>{
  const row = el('div','row');
  const cls = c.points>0?'p':(c.points<0?'n':'z');
  row.appendChild(el('div','pip '+cls, c.points>0?'+':(c.points<0?'−':'·')));
  row.appendChild(el('div','name', c.name));
  row.appendChild(el('div','num', (c.points>0?'+':'')+c.points));
  const why = el('div','why', c.reason);
  comp.append(row, why);
});
const tot = el('div','total');
tot.append(el('span',null,'Composite'), el('span','num',(R.total>=0?'+':'')+R.total));
comp.appendChild(tot);
$('#rgrule').textContent =
  `GREEN at ≥ ${m.green_at} · RED at ≤ ${m.red_at} · otherwise YELLOW`;

/* ---------- sector RS: diverging bars around zero ---------- */
function rsChart(mount, data, key, label){
  if(!data.length){ mount.textContent='No data.'; return; }
  const rows = data.filter(d=>d[key]!=null).sort((a,b)=>b[key]-a[key]);
  if(!rows.length){ mount.textContent='No data.'; return; }
  /* Value labels live in their own right-hand column rather than beside the bar
     end, so a long bar can never collide with its own number or with the
     category label on the left. */
  const W=520, rowH=25, padL=150, padR=52, padT=22, H=padT+rows.length*rowH+10;
  const mx = Math.max(...rows.map(d=>Math.abs(d[key]))) || 1;
  const span = mx*1.12;
  const zero = padL+(W-padL-padR)/2;
  const half = (W-padL-padR)/2;
  const x = v => zero + (v/span)*half;

  const up=css('--up'), dn=css('--down'), muted=css('--muted'), grid=css('--grid');
  let s = `<line x1="${zero}" y1="${padT-8}" x2="${zero}" y2="${H-8}"
             stroke="${css('--rule-strong')}" stroke-width="1"/>`;
  for (const t of [-span/2, span/2]){
    s += `<line x1="${x(t).toFixed(1)}" y1="${padT-8}" x2="${x(t).toFixed(1)}" y2="${H-8}"
            stroke="${grid}" stroke-width="1"/>`;
    s += `<text x="${x(t).toFixed(1)}" y="${padT-13}" fill="${muted}" font-size="9"
            text-anchor="middle">${sg(t,1)}%</text>`;
  }
  s += `<text x="${zero}" y="${padT-13}" fill="${muted}" font-size="9"
          text-anchor="middle">0</text>`;

  rows.forEach((d,i)=>{
    const y = padT + i*rowH;
    const v = d[key], w = Math.abs(x(v)-zero);
    const bx = v>=0 ? zero+1 : x(v);
    const col = v>=0?up:dn;
    const r = 3;
    s += `<rect x="${bx.toFixed(1)}" y="${(y+4).toFixed(1)}" width="${Math.max(w-1,1).toFixed(1)}"
            height="13" fill="${col}" rx="${r}" ry="${r}"
            data-i="${i}" class="bar"/>`;
    s += `<text x="${padL-12}" y="${(y+14).toFixed(1)}" fill="${css('--ink-2')}"
            font-size="11" text-anchor="end">${d.name}</text>`;
    s += `<text x="${W-8}" y="${(y+14).toFixed(1)}"
            fill="${css('--ink-2')}" font-size="10.5"
            text-anchor="end">${sg(v,1)}%</text>`;
  });

  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  svg.setAttribute('class','chart'); svg.setAttribute('width','100%');
  svg.setAttribute('role','img'); svg.setAttribute('aria-label',label);
  svg.innerHTML = s;
  svg.querySelectorAll('.bar').forEach(b=>{
    b.addEventListener('pointermove', ev=>{
      const d = rows[+b.dataset.i];
      showTip(ev, `${d.ticker} ${d.name} · 1W ${sg(d.w1,1)}% · 1M ${sg(d.m1,1)}%`);
    });
    b.addEventListener('pointerleave', hideTip);
  });
  mount.appendChild(svg);
}
rsChart($('#sectors'), D.sectors, 'rs_m1',
  'Sector one-month return relative to SPY, diverging around zero');

leaderboard();
holdingsGrid();

/* ---------- themes ---------- */
const th = $('#themes');
D.themes.filter(t=>t.m1!=null).sort((a,b)=>b.m1-a.m1).forEach(t=>{
  const r = el('div','r');
  r.appendChild(el('div','tk',t.ticker));
  const px = el('div','px');
  px.appendChild(el('span','p num', f2(t.close)));
  px.appendChild(el('span','c', t.name));
  const rel = el('div','rel');
  rel.appendChild(el('span','pill '+(t.w1>=0?'on':'off'), '1W '+sg(t.w1,1)+'%'));
  rel.appendChild(el('span','pill '+(t.m1>=0?'on':'off'), '1M '+sg(t.m1,1)+'%'));
  rel.appendChild(el('span','pill '+(t.above_ma50?'on':'off'), t.above_ma50?'▲ 50 MA':'▼ 50 MA'));
  px.appendChild(rel);
  r.appendChild(px);
  th.appendChild(r);
});

/* ---------- distribution days ---------- */
const ddw = $('#dd');
for (const [tk,v] of Object.entries(D.dd)){
  const r = el('div','r');
  r.appendChild(el('div','tk',tk));
  const ticks = el('div','ticks');
  v.window.forEach(d=>{
    const t = el('div','tick'+(v.days.includes(d)?' hit':''));
    t.addEventListener('pointermove', ev=>showTip(ev,
      d + (v.days.includes(d)?' · distribution day':' · no distribution')));
    t.addEventListener('pointerleave', hideTip);
    ticks.appendChild(t);
  });
  r.appendChild(ticks);
  const n = v.count;
  r.appendChild(el('div','verdict',
    `${n} in ${m.dd_window} sessions — ${n<=2?'clean':(n>=5?'under real pressure':'warming up')}`));
  ddw.appendChild(r);
  const f = D.ftd[tk];
  if (f){
    const s = el('div','verdict',
      `Last follow-through day ${f.date} — day ${f.day} off the ${f.low} low, ${sg(f.gain)}%`);
    s.style.marginLeft='63px'; ddw.appendChild(s);
  }
}

/* ---------- monitor table ---------- */
const T = m.thresholds;
function leaderboard(){
  const mount = $('#leaders'); if(!mount) return;
  const L = D.leaderboard || {};
  const keys = ['d1','w1','m1','m3','m6'];
  mount.innerHTML = '';

  for (const k of keys){
    const w = L[k]; if(!w) continue;
    const card = el('div','leadcard');

    const hd = el('div','hd');
    const lab = el('div','w'); lab.textContent = w.label; hd.appendChild(lab);
    const bm = el('div','bm');
    bm.textContent = w.spy==null ? `${w.universe} ETFs` : `SPY ${sg(w.spy,2)}%`;
    hd.appendChild(bm);
    card.appendChild(hd);

    const row = (r, dir) => {
      const d = el('div','leadrow '+dir);
      const tk = el('div','tk'); tk.textContent = r.ticker;
      const nm = el('div','nm'); nm.textContent = r.name;
      if (r.group === 'Theme'){
        const b = el('span','thm'); b.textContent = 'thm'; nm.appendChild(b);
      }
      const rt = el('div','rt num'); rt.textContent = sg(r.ret,2)+'%';
      d.appendChild(tk); d.appendChild(nm); d.appendChild(rt);
      d.title = r.vs_spy==null ? r.name
              : `${r.name} — ${sg(r.ret,2)}%, ${sg(r.vs_spy,2)}% vs SPY`;
      return d;
    };

    (w.best  || []).forEach(r => card.appendChild(row(r,'up')));
    card.appendChild(el('div','leadsep'));
    (w.worst || []).forEach(r => card.appendChild(row(r,'dn')));
    mount.appendChild(card);
  }
  if(!mount.children.length) mount.textContent = 'Not enough history yet.';
}

function holdingsGrid(){
  const mount = $('#holdings'); if(!mount) return;
  const H = D.holdings || {}, names = D.etf_names || {};
  const order = Object.keys(names).filter(t => (H[t]||[]).length);
  mount.innerHTML = '';

  if(!order.length){
    const w = $('#holdwrap');
    if(w) w.style.display = 'none';     /* nothing to show: drop the section */
    return;
  }

  for (const t of order){
    const rows = H[t];
    const card = el('div','holdcard');

    const hd = el('div','hd');
    hd.appendChild(el('div','tk', t));
    hd.appendChild(el('div','nm', names[t] || ''));
    card.appendChild(hd);

    const mx = Math.max(...rows.map(r => r.weight || 0)) || 1;
    for (const r of rows){
      const d = el('div','holdrow');
      d.appendChild(el('div','sym', r.symbol));
      const bar = el('div','bar');
      const fill = document.createElement('i');
      fill.style.width = ((r.weight||0)/mx*100).toFixed(1)+'%';
      bar.appendChild(fill);
      d.appendChild(bar);
      d.appendChild(el('div','wt num', r.weight==null?'—':r.weight.toFixed(1)+'%'));
      d.title = `${r.symbol} — ${r.name} — ${r.weight}% of ${t}`;
      card.appendChild(d);
    }

    const tot = rows.reduce((a,r)=>a+(r.weight||0),0);
    const f = el('div','tot');
    f.appendChild(el('span','', `top ${rows.length}`));
    f.appendChild(el('span','num', tot.toFixed(1)+'% of fund'));
    card.appendChild(f);

    mount.appendChild(card);
  }
}

function heatCount(up, dn, isUp){
  const v = isUp?up:dn, other = isUp?dn:up;
  if (v<=other) return '';
  return (v>=T.big ? (isUp?'h-up-2':'h-dn-2') : (isUp?'h-up-1':'h-dn-1'));
}
function heatPct(v, kind){
  if (v==null) return '';
  if (v<=T.xlow[kind]) return 'h-up-2';
  if (v<=T.low)        return 'h-up-1';
  if (v>=T.xhigh[kind]) return 'h-dn-2';
  if (v>=T.high)        return 'h-dn-1';
  return '';
}
function heatAtr(v){
  if (v==null) return '';
  if (v>=T.atr_hi) return 'h-dn-1';
  if (v<=T.atr_lo) return 'h-up-1';
  return '';
}
const tb = $('#monitorBody');
D.rows.forEach(r=>{
  const tr = document.createElement('tr');
  const cells = [
    ['td', r.date, ''],
    ['td', r.up4, heatCount(r.up4,r.dn4,true)+' gsep'],
    ['td', r.dn4, heatCount(r.up4,r.dn4,false)],
    ['td', f1(r.pct_ma20), heatPct(r.pct_ma20,'ma20')+' gsep'],
    ['td', f1(r.pct_ma50), heatPct(r.pct_ma50,'ma50')],
    ['td', f1(r.pct_ma200), heatPct(r.pct_ma200,'ma200')],
    ['td', sg(r.spy_atr), heatAtr(r.spy_atr)+' gsep'],
    ['td', sg(r.qqq_atr), heatAtr(r.qqq_atr)],
    ['td', sg(r.mli_pct), (r.mli_pct==null?'':(r.mli_pct>=0?'h-up-1':'h-dn-1'))+' gsep'],
    ['td', f1(r.mli_rising), ''],
    ['td', r.mli_n, ''],
    ['td', r.net_hl==null?'—':sg(r.net_hl,0), 'gsep'],
    ['td', r.universe==null?'—':r.universe.toLocaleString(), ''],
    ['td', r.spy_close==null?'—':f2(r.spy_close), ''],
  ];
  cells.forEach(([tag,val,cls],i)=>{
    const td = document.createElement(tag);
    td.className = ((i===0?'':'num ')+(cls||'')).trim();
    td.textContent = val;
    tr.appendChild(td);
  });
  tb.appendChild(tr);
});
"""


def render(ctx: dict) -> str:
    payload = build_payload(ctx)
    data_json = json.dumps(payload, separators=(",", ":"))
    m = payload["meta"]

    return f"""<title>Breadth &amp; Momentum Monitor</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;700;800&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>{CSS}</style>

<div class="wrap">

  <header class="mast">
    <div>
      <h1>Breadth &amp; Momentum Monitor</h1>
      <div class="meta">
        <span>Close <b id="date"></b></span>
        <span>Universe <b id="universe"></b> US common stocks + ADRs, no ETFs</span>
        <span>Close ≥ ${m['min_close']:.0f} · volume ≥ {m['min_volume']:,}</span>
      </div>
    </div>
    <div class="regime" id="regime">
      <div class="dot"></div>
      <div>
        <div class="word" id="rgword"></div>
        <div class="stance" id="rgstance"></div>
      </div>
      <div class="score">
        <div class="lbl">Score</div>
        <div class="v num" id="rgscore"></div>
        <div class="lbl num" id="rgmax"></div>
      </div>
    </div>
  </header>

  <div id="alerts" style="display:flex;flex-direction:column;gap:10px"></div>

  <section>
    <h2>Today's reading</h2>
    <div class="tiles" id="tiles"></div>
  </section>

  <div class="cols">
    <section class="panel">
      <h2>Index trend</h2>
      <div class="idx" id="indexes"></div>
      <h2 style="margin-top:22px">Distribution days</h2>
      <div class="dd" id="dd"></div>
    </section>

    <section class="panel">
      <h2>Why the light is where it is</h2>
      <div class="comp" id="components"></div>
      <div class="lbl" id="rgrule" style="margin-top:12px;letter-spacing:.04em"></div>
    </section>
  </div>

  <div class="cols">
    <section class="panel">
      <h2>Sector strength vs SPY · 1 month</h2>
      <div id="sectors"></div>
      <div class="legend">
        <span><i style="background:var(--up)"></i>outperforming SPY</span>
        <span><i style="background:var(--down)"></i>lagging SPY</span>
        <span>every bar is labelled with its value</span>
      </div>
    </section>

    <section class="panel">
      <h2>Themes</h2>
      <div class="idx" id="themes"></div>
    </section>
  </div>

  <section class="panel">
    <h2>ETF leaders and laggards</h2>
    <div class="leadgrid" id="leaders"></div>
    <div class="legend">
      <span><i style="background:var(--up)"></i>best three over the window</span>
      <span><i style="background:var(--down)"></i>worst three</span>
      <span>sectors and themes ranked together · vs SPY shown beside each return</span>
    </div>
  </section>

  <section class="panel" id="holdwrap">
    <h2>Largest holdings · top {C.HOLDINGS_TOP_N} by weight</h2>
    <div class="holdgrid" id="holdings"></div>
    <div class="legend">
      <span>weights are each fund's own published percentages</span>
      <span>bar length is the holding's weight relative to the largest in that fund</span>
      <span>funds holding no equities (e.g. a spot bitcoin trust) are omitted</span>
    </div>
  </section>

  <section class="panel">
    <h2>Daily monitor · last {C.TABLE_ROWS} sessions</h2>
    <div class="scroll">
      <table class="monitor">
        <thead>
          <tr>
            <th>Date</th>
            <th class="gsep">Up 4%</th><th>Dn 4%</th>
            <th class="gsep">%&gt;20D</th><th>%&gt;50D</th><th>%&gt;200D</th>
            <th class="gsep">SPY ATR</th><th>QQQ ATR</th>
            <th class="gsep">MLI %</th><th>MLI up%</th><th>MLI N</th>
            <th class="gsep">Net H−L</th><th>Universe</th><th>SPY</th>
          </tr>
        </thead>
        <tbody id="monitorBody"></tbody>
      </table>
    </div>
    <div class="key">
      <span><i style="background:color-mix(in srgb,var(--up) 32%,transparent)"></i>
        Up 4% side leads with ≥{C.BIG_COUNT} names</span>
      <span><i style="background:color-mix(in srgb,var(--up) 13%,transparent)"></i>
        Up 4% side leads</span>
      <span><i style="background:color-mix(in srgb,var(--down) 13%,transparent)"></i>
        Dn 4% side leads</span>
      <span><i style="background:color-mix(in srgb,var(--down) 32%,transparent)"></i>
        Dn 4% side leads with ≥{C.BIG_COUNT} names</span>
      <span><i style="background:color-mix(in srgb,var(--up) 32%,transparent)"></i>
        % above MA washed out (20D ≤{C.PCT_ABOVE_EXTREME_LOW['ma20']:.0f}, 50D ≤{C.PCT_ABOVE_EXTREME_LOW['ma50']:.0f})</span>
      <span><i style="background:color-mix(in srgb,var(--down) 32%,transparent)"></i>
        % above MA overheated (20D ≥{C.PCT_ABOVE_EXTREME_HIGH['ma20']:.0f}, 50D ≥{C.PCT_ABOVE_EXTREME_HIGH['ma50']:.0f})</span>
      <span>ATR cells shade past ±{C.ATR_STRETCHED:.0f} ATR from the 50D EMA</span>
    </div>
  </section>

  <footer>
    Generated {m['generated']} from Polygon end-of-day data.
    MLI membership: price ≥ ${C.MLI_MIN_PRICE:.0f}, 50-day average dollar volume ≥
    ${C.MLI_MIN_DOLLAR_VOL/1e6:.0f}m, above a rising 200-day MA and the 50-day MA,
    {C.MLI_RS_LOOKBACK}-day return in the top {100-C.MLI_RS_PERCENTILE:.0f}% of the universe.
    Distribution day: close down ≥ {C.DD_MIN_DROP_PCT:.2f}% on rising volume,
    cancelled once the index closes {C.DD_EXPIRE_RALLY_PCT:.0f}% above it.
    Historical rows use the current active-ticker list, so names delisted since
    carry a mild survivorship tilt; today's reading is exact.
    <br>Personal research notes — not investment advice.
  </footer>
</div>

<script>window.__MR__ = {data_json};</script>
<script>{JS}</script>
"""
