#!/usr/bin/env python3
"""OOC synthesis results page (gap 5, owner 2026-11-02 "do it"). Deterministic: reads only result files, writes one HTML page.
Inputs: claude-artifacts/ooc-synthesis/{tile-area-width{1,2,4}.csv, ift-overhead-per-module.csv, intile-module-table.json,
designed-tiles-intile.json, flatten-none-intile.json, rest-of-tile.json} and the run dirs under /data/akrish/scratch/ooc-gaps.
usage: results_page.py --out ooc-results.html"""
import argparse, csv, glob, html, itertools, json, os, re
ap = argparse.ArgumentParser(); ap.add_argument('--out', required=True); a = ap.parse_args()
O = '/home/jht9sy/work/chipyard/claude-artifacts/ooc-synthesis/'
G = '/data/akrish/scratch/ooc-gaps/'
K = 'rob=256,fb=64,ftq=32,ras=32,iprf=192,fprf=96,iq_int=32,iq_mem=32,iq_fp=32,ldq=40,stq=40,btb_sets=128,btb_ways=2,tage=7,ic_sets=64,ic_ways=8,dc_sets=128,dc_ways=8,'
E = html.escape
n = lambda x: format(int(round(x)), ',')
pct = lambda x: '0.0%' if abs(x) < 0.05 else ('+' if x > 0 else '−') + '%.1f%%' % abs(x)

def table(head, rows, cls='', num_from=1):
    h = ''.join('<th%s>%s</th>' % (' class="n"' if i >= num_from else '', E(c)) for i, c in enumerate(head))
    b = ''.join('<tr>' + ''.join('<td%s>%s</td>' % (' class="n"' if i >= num_from else '', c) for i, c in enumerate(r)) + '</tr>' for r in rows)
    return '<div class="tw"><table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (cls, h, b)

# ---- tile area by width
tiles = {}
RENAME = {'Baseline': 'Baseline', 'Baseline, static width 2': 'Baseline', 'Baseline, static width 1': 'Baseline',
          'Baseline + reconfiguration': 'Baseline + reconfiguration',
          'IFT without bridge export': 'IFT', 'IFT without bridge export, static width 2': 'IFT', 'IFT without bridge export, static width 1': 'IFT'}
for w in (4, 2, 1):
    tiles[w] = [dict(r, variant=RENAME[r['variant']]) for r in csv.DictReader(open(O + 'tile-area-width%d.csv' % w)) if r['variant'] in RENAME]
w4 = {r['variant']: r for r in tiles[4]}
base4, ift4, rec4 = w4['Baseline'], w4['IFT'], w4['Baseline + reconfiguration']
# storage and logic added by IFT: RTL memory arrays (depth x width from the macro names) + flip-flops; logic = LUTs not used as memory
TP = {4: ('A/points/Tile/base/width=4', 'B/points/Tile/ift_nobridge/width=4'), 2: ('A/points/Tile/base/width=2', 'E/points/Tile/ift_nobridge/width=2'),
      1: ('A/points/Tile/base/width=1', 'E/points/Tile/ift_nobridge/width=1')}
def arr_bits(f):
    hdr = None; tot = 0
    for l in open(f):
        if not l.startswith('|'): continue
        c = [x.strip() for x in l.strip().strip('|').split('|')]
        if hdr is None:
            if c[0] == 'Instance': hdr = c
            continue
        if len(c) != len(hdr) or c[0].startswith('('): continue
        m = re.search(r'_(\d+)x(\d+)(?:_\d+)?$', c[1])
        if m: tot += int(m[1]) * int(m[2])
    return tot
stor = {}
for w, (b, i) in TP.items():
    S = '/data/akrish/scratch/ooc-tiles/'
    tb, ti = json.load(open(S + b + '/result.json'))['total'], json.load(open(S + i + '/result.json'))['total']
    ab = arr_bits(S + i + '/util_hier.rpt') - arr_bits(S + b + '/util_hier.rpt')
    stor[w] = dict(arr=ab, ff=ti['ffs'] - tb['ffs'], logic=ti['logic_luts'] - tb['logic_luts'], logic_pct=100 * (ti['logic_luts'] / tb['logic_luts'] - 1),
                   lutram=ti['lutram'] - tb['lutram'], r18=ti['ramb18'] - tb['ramb18'], r36=ti['ramb36'] - tb['ramb36'], ff_pct=100 * (ti['ffs'] / tb['ffs'] - 1))
kb = lambda bits: '%.1f Kb' % (bits / 1024)
stor_rows = [[str(w), kb(x['arr']), n(x['ff']), kb(x['arr'] + x['ff']), n(x['logic']) + ' (' + pct(x['logic_pct']) + ')', n(x['lutram']), '%d / %d' % (x['r18'], x['r36'])] for w, x in sorted(stor.items(), reverse=True)]
d = lambda r, b, c: 100.0 * (int(r[c]) / int(b[c]) - 1)
def tile_rows(w):
    rs = tiles[w]; b = rs[0]
    return [[E(r['variant']), n(int(r['total_luts'])), n(int(r['ffs'])), r['ramb36'], r['ramb18'], r['dsp'],
             pct(d(r, b, 'total_luts')) if r is not b else '—', pct(d(r, b, 'ffs')) if r is not b else '—'] for r in rs]
TH = ['Variant', 'LUTs', 'FFs', 'RAMB36', 'RAMB18', 'DSP', 'LUT vs Baseline', 'FF vs Baseline']

# ---- IFT split by module
ift = list(csv.DictReader(open(O + 'ift-overhead-per-module.csv')))
keep = [r for r in ift if r['module'] not in ('BoomTile', 'BoomCore', 'BoomFrontend', 'BranchPredictor') and int(r['ift_tracking_luts'] or 0) > 2000]
keep.sort(key=lambda r: -int(r['ift_tracking_luts']))
ift_rows = [[E(r['knob']), n(int(r['baseline_luts'])), n(int(r['ift_tracking_luts'])), n(int(r['ift_tracking_ffs'])), r['ift_tracking_ramb36']] for r in keep]

# ---- additive area vs measured
R = json.load(open(O + 'rest-of-tile.json'))['rest']
T = [('earlier', t) for t in json.load(open(O + 'intile-module-table.json'))] + [('designed', t) for t in json.load(open(O + 'designed-tiles-intile.json'))]
def add(t, c): return sum(g['ooc'][c] for g in t['groups'].values()) + R[str(t['width'])][c]
over = []
for src, t in T:
    s = {c: add(t, c) for c in ('total_luts', 'ffs')}; m = t['total']
    over.append((t['width'], src, t, s, m, 100 * (s['total_luts'] / m['total_luts'] - 1), 100 * (s['ffs'] / m['ffs'] - 1)))
over.sort(key=lambda x: (-x[0], x[4]['total_luts']))
def rank_stats(sel, c):
    xs = [x for x in over if sel(x)]; ad = [x[3][c] for x in xs]; me = [x[4][c] for x in xs]
    pairs = list(itertools.combinations(range(len(xs)), 2)); ok = sum((ad[i] - ad[j]) * (me[i] - me[j]) > 0 for i, j in pairs)
    rat = sorted((ad[i] - ad[j]) / (me[i] - me[j]) for i, j in pairs if abs(me[i] - me[j]) > 0.05 * max(me[i], me[j]))
    return len(xs), ok, len(pairs), rat[len(rat) // 2]
ov_sum = []
for w in (4, 2, 1):
    xs = [x for x in over if x[0] == w]
    nl, okl, pl, ml = rank_stats(lambda x: x[0] == w, 'total_luts'); _, okf, pf, mf = rank_stats(lambda x: x[0] == w, 'ffs')
    ov_sum.append([str(w), str(len(xs)), '%s to %s' % (pct(min(x[5] for x in xs)), pct(max(x[5] for x in xs))),
                   '%s to %s' % (pct(min(x[6] for x in xs)), pct(max(x[6] for x in xs))),
                   '%d of %d (%.0f%%)' % (okl, pl, 100 * okl / pl), '%.2f×' % ml])
_, oka, pa, ma = rank_stats(lambda x: True, 'total_luts')
lo_all = min(x[5] for x in over); hi_all = max(x[5] for x in over); below = sum(1 for x in over if x[5] < 0 or x[6] < 0)
def cfg_short(t):
    c = t['cfg']; return 'ROB %d · PRF %d/%d · IQ %d/%d/%d · LDQ/STQ %d/%d · D$ %dx%d' % (c['rob'], c['iprf'], c['fprf'], c['iq_int'], c['iq_mem'], c['iq_fp'], c['ldq'], c['stq'], c['dc_sets'], c['dc_ways'])
ov_rows = [[str(w), src, E(cfg_short(t)), n(m['total_luts']), n(s['total_luts']), pct(ol), n(m['ffs']), n(s['ffs']), pct(of)] for w, src, t, s, m, ol, of in over]

# ---- clock effect + power
def pw(f):
    s = open(f).read(); return float(re.search(r'\| Dynamic \(W\)\s*\|\s*([\d.]+)', s).group(1)), float(re.search(r'\| Device Static \(W\)\s*\|\s*([\d.]+)', s).group(1))
unclk = {}
for f in glob.glob('/data/akrish/scratch/ooc-tiles/*/points/Tile/*/*/result.json') + glob.glob('/data/akrish/scratch/ooc-core-cal2/points/Tile/*/*/result.json') + glob.glob(G + 'power/points/Tile/*/*/result.json'):
    r = json.load(open(f))
    if r.get('status') == 'ok': unclk[(r['variant'], r['params'].replace(K, ''))] = r['total']
NAME = {'base': 'Baseline', 'reconf_base': 'Baseline + reconfiguration', 'ift_nobridge': 'IFT'}
def wlabel(p):
    w = re.search(r'width=(\d)', p).group(1); return 'width %s%s' % (w, ', decode-width only' if 'dispatch_only' in p else ', static')
clk_rows, pow_rows = [], []
for f in sorted(glob.glob(G + 'power-clk/points/Tile/*/*/result.json')):
    r = json.load(open(f)); v = r['variant']; p = r['params'].replace(K, ''); t = r['total']
    if v not in NAME or 'dispatch_only' in p: continue
    dyn, st = pw(os.path.join(os.path.dirname(f), 'power_hier.rpt')); u = unclk.get((v, p))
    key = (-int(re.search(r'width=(\d)', p).group(1)), 'dispatch' in p, list(NAME).index(v))
    clk_rows.append((key, [E(wlabel(p)), E(NAME[v]), n(u['total_luts']), n(t['total_luts']), pct(100 * (t['total_luts'] / u['total_luts'] - 1)), pct(100 * (t['ffs'] / u['ffs'] - 1))]))
    pow_rows.append((key, [E(wlabel(p)), E(NAME[v]), '%.3f' % dyn, '%.2f' % (1e6 * dyn / t['total_luts'])]))
clk_rows = [r for _, r in sorted(clk_rows)]; pow_rows = [r for _, r in sorted(pow_rows)]

# ---- gap 1 (boundaries kept) and gap 3 (mux mapping)
FL = json.load(open(O + 'flatten-none-intile.json')); D0 = json.load(open(O + 'intile-module-table.json'))
g1 = []
for t in sorted(FL, key=lambda t: -t['total']['total_luts']):
    ref = [x for x in D0 if x['cfg'] == t['cfg'] and x['width'] == t['width']][0]
    lab = 'full size' if t['cfg']['rob'] == 256 else 'minimal'
    for g, name in (('RegfileInt', 'Integer register file'), ('RegfileFp', 'FP register file'), ('Rob', 'ROB')):
        g1.append([E('%s, %s' % (name, lab)), n(ref['groups'][g]['intile']['total_luts']), n(t['groups'][g]['intile']['total_luts']), n(t['groups'][g]['ooc']['total_luts'])])
    g1.append([E('Whole BoomTile, %s' % lab), n(ref['total']['total_luts']), n(t['total']['total_luts']), '—'])
def prims(m):
    f = '/data/akrish/scratch/ooc-gaps/prims/points/RegfileFp/base/pregs=%d,width=4/' % m
    s = open(f + 'util_flat.rpt').read(); get = lambda k: int((re.search(r'\| %s\s*\|\s*(\d+)\s*\|\s*CLB' % k, s) or [0, 0])[1])
    return [str(m), n(get('LUT6')), n(get('LUT5')), n(get('LUT4')), n(get('MUXF7')), n(get('MUXF8')), n(json.load(open(f + 'result.json'))['total']['total_luts'])]
g3 = [prims(40), prims(48)]

cfgnote = 'Full size: ROB 256, fetch buffer 64, FTQ 32, RAS 32, integer PRF 192, FP PRF 96, issue queues 32/32/32, LDQ/STQ 40/40, BTB 128×2, TAGE 7 tables, I$ 64×8, D$ 128×8.'
page = f'''<title>BOOM IFT Tile Area</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,500;8..60,600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: single reading column (max ~72rem) with full-width tables that scroll in their own box; sections are plain headed blocks. */
:root {{
  --bg: #f6f7f9; --surface: #ffffff; --fg: #1c2430; --muted: #5a6677; --rule: #d9dee6; --accent: #0f6e6a; --accent-soft: #e2f0ef; --warn: #8a5a00; --warn-soft: #fbf1dc;
  --display: "Source Serif 4", Georgia, "Times New Roman", serif; --body: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif; --mono: "IBM Plex Mono", ui-monospace, Menlo, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg: #12161c; --surface: #1a2029; --fg: #e3e8ef; --muted: #9aa6b6; --rule: #2c3440; --accent: #5cc3bc; --accent-soft: #173533; --warn: #e3b25c; --warn-soft: #33290f; color-scheme: dark; }} }}
:root[data-theme="dark"] {{ --bg: #12161c; --surface: #1a2029; --fg: #e3e8ef; --muted: #9aa6b6; --rule: #2c3440; --accent: #5cc3bc; --accent-soft: #173533; --warn: #e3b25c; --warn-soft: #33290f; color-scheme: dark; }}
body {{ background: var(--bg); color: var(--fg); font: 15px/1.6 var(--body); }}
.wrap {{ max-width: 72rem; margin: 0 auto; padding-block: 40px 72px; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); display: grid; gap: 44px; }}
header {{ display: grid; gap: 10px; }}
.eyebrow {{ font: 500 12px/1.4 var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--accent); }}
h1 {{ font: 600 clamp(28px, 4vw, 40px)/1.15 var(--display); margin: 0; text-wrap: balance; }}
h2 {{ font: 600 24px/1.25 var(--display); margin: 0; text-wrap: balance; }}
h3 {{ font: 600 16px/1.4 var(--body); margin: 0; }}
p {{ margin: 0; max-width: 68ch; }}
.lede {{ color: var(--muted); font-size: 16px; }}
section {{ display: grid; gap: 14px; }}
.figs {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); gap: 12px; }}
.fig {{ background: var(--surface); border: 1px solid var(--rule); border-radius: 6px; padding: 14px 16px; display: grid; gap: 4px; min-width: 0; }}
.fig b {{ font: 500 26px/1.1 var(--mono); color: var(--accent); font-variant-numeric: tabular-nums; }}
.fig span {{ color: var(--muted); font-size: 13px; }}
.tw {{ overflow-x: auto; border: 1px solid var(--rule); border-radius: 6px; background: var(--surface); }}
table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
th, td {{ padding: 7px 12px; border-bottom: 1px solid var(--rule); text-align: left; vertical-align: top; white-space: nowrap; }}
th {{ font: 600 12px/1.3 var(--body); color: var(--muted); letter-spacing: .02em; background: var(--bg); }}
td.n, th.n {{ text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; }}
tbody tr:last-child td {{ border-bottom: 0; }}
.note {{ font-size: 13.5px; color: var(--muted); }}
.callout {{ border-left: 3px solid var(--warn); background: var(--warn-soft); padding: 12px 14px; border-radius: 0 6px 6px 0; display: grid; gap: 6px; max-width: 72ch; }}
.callout h3 {{ color: var(--warn); }}
code, .m {{ font-family: var(--mono); font-size: 13px; }}
.formula {{ font: 14px/1.6 var(--mono); background: var(--accent-soft); padding: 10px 14px; border-radius: 6px; overflow-x: auto; }}
details {{ display: grid; gap: 10px; }}
summary {{ cursor: pointer; color: var(--accent); font-weight: 500; }}
summary:focus-visible, a:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
ul {{ margin: 0; padding-left: 1.2em; display: grid; gap: 4px; max-width: 72ch; }}
footer {{ font-size: 12.5px; color: var(--muted); border-top: 1px solid var(--rule); padding-top: 14px; }}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">Out-of-context synthesis · Vivado 2023.1.1 · xcu250-figd2104-2L-e</div>
  <h1>What information flow tracking costs a BOOM tile</h1>
  <p class="lede">Area of the whole BoomTile (pipeline, frontend, LSU and D$) for the Baseline design, IFT, and the runtime reconfiguration hardware, plus the per-module numbers the fuzzer uses to price configurations. All counts are post-synthesis.</p>
</header>

<section>
  <h2>Headline, width 4</h2>
  <div class="figs">
    <div class="fig"><b>{kb(stor[4]['arr'] + stor[4]['ff'])}</b><span>storage added by IFT: {kb(stor[4]['arr'])} of memory arrays + {n(stor[4]['ff'])} flip-flops</span></div>
    <div class="fig"><b>{n(stor[4]['logic'])}</b><span>logic LUTs added by IFT ({pct(stor[4]['logic_pct'])} over Baseline)</span></div>
    <div class="fig"><b>{pct(d(ift4, base4, 'total_luts'))}</b><span>all LUTs, IFT vs Baseline ({n(int(base4['total_luts']))} → {n(int(ift4['total_luts']))})</span></div>
    <div class="fig"><b>{pct(d(ift4, base4, 'ffs'))}</b><span>flip-flops, IFT vs Baseline</span></div>
    <div class="fig"><b>{pct(d(rec4, base4, 'total_luts'))}</b><span>Reconfiguration hardware on Baseline ({n(int(rec4['total_luts']) - int(base4['total_luts']))} LUTs)</span></div>
  </div>
  <p class="note">{E(cfgnote)}</p>
</section>

<section>
  <h2>Whole-tile area by issue width</h2>
  <p>Width 4 is the built design; widths 2 and 1 are cores built at that width. IFT here is the information flow tracking logic in the tile; the logic that exports IFT records to the FireSim bridge is a simulation aid and is not counted.</p>
  <h3>Width 4</h3>{table(TH, tile_rows(4))}
  <h3>Width 2</h3>{table(TH, tile_rows(2))}
  <h3>Width 1</h3>{table(TH, tile_rows(1))}
</section>

<section>
  <h2>Storage and logic IFT adds</h2>
  <p>Storage is counted from the design itself: memory arrays by their depth × width, plus flip-flops. Logic is LUTs used as logic (LUTs used as distributed RAM are memory and are counted as storage). The last two columns show how Vivado implemented the arrays.</p>
  {table(['Width', 'Memory arrays', 'Flip-flops (bits)', 'Total storage', 'Logic LUTs added', 'LUTs used as RAM added', 'RAMB18 / RAMB36 added'], stor_rows, num_from=1)}
  <p class="note">Flip-flops inside the arrays (array output registers) grow by about 0.8k, so the two storage columns barely overlap.</p>
  <h2>Where IFT’s area goes</h2>
  <p>Module rows below are read from the hierarchy of the full-size tile. Vivado moves logic across module boundaries when it rebuilds the hierarchy, so a module row shows where Vivado booked the logic; the tile totals are the numbers to cite.</p>
  {table(['Knob', 'Baseline LUTs', 'IFT added LUTs', 'IFT added FFs', 'IFT added RAMB36'], ift_rows)}
  <p class="note">Modules with more than 2,000 added LUTs. Full table: ift-overhead-per-module.csv.</p>
</section>

<section>
  <h2>The fuzzer’s area estimate</h2>
  <p>The fuzzer prices each configuration by adding the out-of-context area of the sixteen size-dependent modules at that configuration’s sizes, plus one fixed term for the rest of the tile measured in the full-size tile of the same width.</p>
  <div class="formula">area(config, w) = Σ<sub>m</sub> n<sub>m</sub> · OOC<sub>m</sub>(size<sub>m</sub>, w) + rest(w) &nbsp;&nbsp; n = 2 for BTB and TAGE (two banks); rest(4) = {n(R['4']['total_luts'])}, rest(2) = {n(R['2']['total_luts'])}, rest(1) = {n(R['1']['total_luts'])} LUTs</div>
  <p>Checked against {len(over)} whole tiles synthesized at mixed sizes, the estimate is above the measured tile every time ({below} below): LUTs {pct(lo_all)} to {pct(hi_all)}. It overcounts for two measured reasons. A module synthesized alone keeps every micro-op field in every queue entry, while the tile removes the fields it never reads (issue-queue FFs in the tile are about a third of the stand-alone count). And the rest-of-tile term is fixed at its full-size value, while the real rest shrinks with the configuration, so small configurations are overcounted most.</p>
  {table(['Width', 'Tiles', 'LUT overcount', 'FF overcount', 'Pairs ordered correctly (LUTs)', 'Median size of a difference, estimate / measured'], ov_sum, num_from=1)}
  <p class="note">Across all widths together, {oka} of {pa} pairs ({100*oka/pa:.0f}%) are ordered correctly; differences between configurations are inflated by {ma:.2f}× in the median. Shrinking issue queues, the LSU or the fetch buffer is credited with 2–3.5× the LUTs it saves in the tile; register files come out about right.</p>
  <details><summary>All {len(over)} measured tiles</summary>
  {table(['Width', 'Set', 'Configuration', 'Measured LUTs', 'Estimated LUTs', 'LUT over', 'Measured FFs', 'Estimated FFs', 'FF over'], ov_rows, num_from=3)}
  </details>
</section>

<section>
  <h2>Method and what to disclose</h2>
  <ul>
    <li>Flow: out-of-context <span class="m">synth_design</span> with the FireSim build’s directives (<span class="m">-no_lc -shreg_min_size 5 -fsm_extraction one_hot -resource_sharing auto -retiming</span>), Vivado 2023.1.1, part xcu250-figd2104-2L-e, one flow for every number on this page.</li>
    <li>RTL: BoomTile cut from a full TestHarness elaboration (generator jar f1e55838 for the designed tiles and the reruns; the full-size tiles of 10-29 used the jar of that date, and the fresh full-size Baseline at width 2 differs from it by 0.5%).</li>
  </ul>
  <div class="callout"><h3>No clock constraint in the area numbers</h3>
  <p>The synthesis script wrapped its <span class="m">create_clock</span> in a Tcl <span class="m">if</span>, which Vivado’s constraint reader rejects, so every area number here was synthesized without a clock and retiming never ran. The script is fixed. Re-synthesizing 13 tiles with a 100 MHz clock changes whole-tile area by very little:</p></div>
  {table(['Width', 'Variant', 'LUTs, no clock', 'LUTs, 100 MHz clock', 'LUT change', 'FF change'], clk_rows, num_from=2)}

  <h3>Why stand-alone modules differ from the same modules inside the tile</h3>
  <p>With Vivado’s default hierarchy handling, the register files look two to five times smaller inside the tile and the ROB looks larger. Synthesizing the tile with module boundaries kept (<span class="m">-flatten_hierarchy none</span>) shows this is bookkeeping: the register file read multiplexers are booked under the modules that consume them, and about 16k LUTs of neighbouring logic are booked under the ROB. With boundaries kept, both match their stand-alone numbers, and the whole tile grows only a few percent.</p>
  {table(['LUTs', 'Inside tile, default', 'Inside tile, boundaries kept', 'Stand-alone'], g1)}
  <p>One stand-alone oddity: the FP register file is larger at 40 entries than at 48. Forty-eight entries are three groups of sixteen, which fill the LUT6 + MUXF7/MUXF8 multiplexer trees exactly; forty leave an eight-entry remainder built from wider LUT logic. Inside the tile it is monotonic.</p>
  {table(['FP register file entries', 'LUT6', 'LUT5', 'LUT4', 'MUXF7', 'MUXF8', 'Total LUTs'], g3)}

  <div class="callout"><h3>Power is not a result</h3>
  <p>Vivado’s post-synthesis power estimate with default switching activity is shown only for completeness. It works out to roughly LUT count times a constant (4.9–7.6 µW per LUT) and is not consistent across widths, so it carries no information beyond area. A power claim needs switching activity from real runs.</p></div>
  {table(['Width', 'Variant', 'Dynamic W (100 MHz, default activity)', 'µW per LUT'], pow_rows, num_from=2)}
</section>

<footer>Generated by tools/ooc-synth/analysis/results_page.py from the files in claude-artifacts/ooc-synthesis and the run directories under /data/akrish/scratch/ooc-gaps. Reruns of the script give the same page.</footer>
</div>
'''
open(a.out, 'w').write(page)
print('wrote', a.out, len(page), 'bytes')
