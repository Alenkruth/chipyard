#!/usr/bin/env python3
"""power_cal_page.py -- deterministic results page for the power calibration (owner: pages are scripts, not agents).

Inputs : --root /data/akrish/scratch/power-cal (per id: window.env, v/vivado.log SAIF_FILL lines, vectorless.rpt,
         power_dhry.rpt) and ROOT/fit/power-cal.json (cal_fit.py); knob strings from cal_cfgs.env next to this script.
Output : --out claude-artifacts/ooc-synthesis/power-cal.html.  Same inputs -> byte-identical page (no clock, no randomness).
Usage  : python3 -I power_cal_page.py --root /data/akrish/scratch/power-cal --out .../power-cal.html
The fixed text (findings, limits, decision) cites its report files by sha1; numbers come from the inputs.
"""
import argparse, hashlib, html, json, os, re

IDS = ['W4', 'W2', 'W1', 'MIN4', 'HELD2', 'HELD3']
GROUPS = ['core', 'lsu', 'frontend', 'dcache', 'ptw']
ap = argparse.ArgumentParser(); ap.add_argument('--root', required=True); ap.add_argument('--out', required=True); a = ap.parse_args()
here = os.path.dirname(os.path.abspath(__file__))
fitp = os.path.join(a.root, 'fit', 'power-cal.json'); fit = json.load(open(fitp))
sha = lambda p: hashlib.sha1(open(p, 'rb').read()).hexdigest()[:8]
knobs = {}
for ln in open(os.path.join(here, 'cal_cfgs.env')):
    f = ln.split()
    if len(f) == 3 and f[0] in IDS: knobs[f[0]] = f[2]
def env(p): return dict(l.strip().split('=', 1) for l in open(p) if '=' in l)
def dyn(p):
    m = re.search(r'Dynamic \(W\)\s*\|\s*([0-9.]+)', open(p).read()); return float(m.group(1))
rows = []
for i in IDS:
    w = env(os.path.join(a.root, i, 'window.env')); log = open(os.path.join(a.root, i, 'v', 'vivado.log')).read()
    fill = {k: int(v) for k, v in re.findall(r'^SAIF_FILL (cells|named|set) (\d+)$', log, re.M)}
    c = fit['configs'][i]
    rows.append(dict(id=i, role=c['role'], A=int(w['A']), N=int(w['N']), vl=dyn(os.path.join(a.root, i, 'vectorless.rpt')),
                     meas=c['measured_total_W'], pred=c['predicted_total_W'], err=c['error_total'], area_err=c['area_only_error'],
                     loo=fit['leave_one_out'].get(i, {}).get('error'), cov=(fill['named'] + fill['set']) / fill['cells'],
                     ff=fill['cells'], groups=c['groups']))
E = html.escape
pct = lambda x: '—' if x is None else ('%+.1f%%' % (100 * x))
def cls(x, lim=0.10):
    return '' if x is None else ('ok' if abs(x) <= lim else ('warn' if abs(x) <= 0.25 else 'bad'))

# chart: measured vs predicted per config (inline SVG, one linear scale)
W, H, L, B = 640, 260, 48, 36; vmax = 2.6; bw = 34; gap = (W - L - 20) / len(rows)
y = lambda v: H - B - (H - B - 16) * v / vmax
svg = ['<svg viewBox="0 0 %d %d" role="img" aria-label="Measured and predicted dynamic power per config">' % (W, H)]
for t in [0, 0.5, 1.0, 1.5, 2.0, 2.5]:
    svg.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/><text x="%d" y="%.1f" class="tick" text-anchor="end">%.1f</text>' % (L, W - 10, y(t), y(t), L - 6, y(t) + 4, t))
for k, r in enumerate(rows):
    x0 = L + 12 + k * gap
    svg.append('<rect x="%.1f" y="%.1f" width="%d" height="%.1f" class="m"/>' % (x0, y(r['meas']), bw, H - B - y(r['meas'])))
    svg.append('<rect x="%.1f" y="%.1f" width="%d" height="%.1f" class="p"/>' % (x0 + bw + 4, y(r['pred']), bw, H - B - y(r['pred'])))
    svg.append('<text x="%.1f" y="%d" class="lab" text-anchor="middle">%s%s</text>' % (x0 + bw + 2, H - B + 18, r['id'], ' (held-out)' if r['role'] == 'held-out' else ''))
svg.append('<text x="%d" y="12" class="tick">W</text></svg>' % (L - 30))
svg = ''.join(svg)

trs = ''.join('<tr><td>%s</td><td>%s</td><td class="n">%.3f</td><td class="n">%.3f</td><td class="n">%.3f</td><td class="n %s">%s</td><td class="n %s">%s</td><td class="n">%s</td><td class="n">%.1f%%</td><td class="n">%s</td></tr>' % (
    r['id'], r['role'], r['vl'], r['meas'], r['pred'], cls(r['err']), pct(r['err']), cls(r['loo']), pct(r['loo']), pct(r['area_err']),
    100 * r['cov'], '{:,}–{:,}'.format(r['A'], r['A'] + r['N'])) for r in rows)
grp = ''.join('<tr><td>%s</td>%s</tr>' % (r['id'], ''.join('<td class="n %s">%.3f / %.3f</td>' % (cls(r['groups'][g]['error'], 0.15), r['groups'][g]['measured_W'], r['groups'][g]['predicted_W']) for g in GROUPS)) for r in rows)
kn = ''.join('<tr><td>%s</td><td class="mono">%s</td></tr>' % (i, E(knobs[i])) for i in IDS)
aG = ''.join('<tr><td>%s</td><td class="n">%.3f</td><td class="n">%.2f</td></tr>' % (g, fit['a_G'][g], fit['a_G_spread_max_over_min'][g]) for g in GROUPS)
held = fit['held_out_error_total']; verdict = fit['verdict']

page = f'''<title>BOOM Power Calibration</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono:wght@400;500&family=Fraunces:opsz,wght@9..144,600&display=swap">
<style>
/* layout: one reading column, verdict first, evidence tables after, limits and the open decision last */
:root {{ --bg:#f6f7f8; --fg:#1b1f24; --mute:#5b6570; --line:#d9dee3; --accent:#2f5d8a; --pred:#c0843a;
  --ok:#2d7a46; --warn:#a66a00; --bad:#b23a3a; --card:#ffffff;
  --display:"Fraunces",Georgia,serif; --body:"IBM Plex Sans",system-ui,sans-serif; --mono:"IBM Plex Mono",ui-monospace,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#14181c; --fg:#e4e8ec; --mute:#9aa5b0; --line:#2c343c; --accent:#7fb0e0; --pred:#e0a560; --ok:#5fbf7f; --warn:#e0a640; --bad:#f07a7a; --card:#1b2026; color-scheme:dark; }} }}
:root[data-theme="dark"] {{ --bg:#14181c; --fg:#e4e8ec; --mute:#9aa5b0; --line:#2c343c; --accent:#7fb0e0; --pred:#e0a560; --ok:#5fbf7f; --warn:#e0a640; --bad:#f07a7a; --card:#1b2026; color-scheme:dark; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.55 var(--body); padding-left:max(16px,env(safe-area-inset-left,0px)); padding-right:max(16px,env(safe-area-inset-right,0px)); padding-block:24px 48px; }}
main {{ max-width:980px; margin:0 auto; display:grid; gap:28px; }}
h1 {{ font:600 2rem/1.15 var(--display); margin:0; text-wrap:balance; }}
h2 {{ font:600 1.15rem/1.3 var(--body); margin:0 0 8px; }}
p, li {{ max-width:70ch; }} .mute {{ color:var(--mute); }}
.verdict {{ background:var(--card); border:1px solid var(--line); border-left:4px solid var(--warn); padding:14px 18px; border-radius:6px; }}
.verdict b {{ font-family:var(--mono); }}
.tbl {{ overflow-x:auto; }} table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
th, td {{ border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }}
th {{ color:var(--mute); font-weight:600; font-size:12px; letter-spacing:.03em; text-transform:uppercase; }}
.n {{ text-align:right; font-variant-numeric:tabular-nums; font-family:var(--mono); white-space:nowrap; }}
.mono {{ font-family:var(--mono); font-size:12px; word-break:break-all; }}
.ok {{ color:var(--ok); }} .warn {{ color:var(--warn); }} .bad {{ color:var(--bad); }}
svg {{ width:100%; max-width:680px; height:auto; }} .grid {{ stroke:var(--line); }} .tick, .lab {{ fill:var(--mute); font:11px var(--mono); }}
.m {{ fill:var(--accent); }} .p {{ fill:var(--pred); }} .key span {{ display:inline-block; width:10px; height:10px; margin:0 4px 0 12px; }}
code {{ font-family:var(--mono); font-size:.92em; }}
</style>
<main>
<header><h1>BOOM Power Calibration</h1>
<p class="mute">Baseline BoomTile, post-synthesis Vivado power at 100 MHz, dhrystone (100,000-cycle window in the Proc loop). Fit: <code>cal_fit.py</code>; inputs <code>power-cal.json</code> {sha(fitp)}.</p></header>

<section class="verdict"><h2>Verdict: <b>{verdict}</b> by the rule, weak evidence</h2>
<p>The held-out config HELD2 is predicted at <b>{pct(held)}</b> of its measured total (owner rule: within ±10%), and the model orders all five totals correctly.
But the calibration configs miss by up to {pct(max((r['err'] for r in rows if r['role']=='calibration'), key=abs))}, leave-one-out misses reach {pct(max((r['loo'] for r in rows if r['loo'] is not None), key=abs))} (MIN4), and an area-only proxy does about as well.
HELD2 passes partly because its dcache over-prediction cancels the under-prediction of the other groups (review report power-cal-review.md 36e65380).</p></section>

<section><h2>Measured vs predicted total dynamic power</h2>
<p class="key mute"><span class="m"></span>measured (activity)<span class="p"></span>predicted (model)</p>{svg}</section>

<section><h2>Per config</h2><div class="tbl"><table>
<tr><th>config</th><th>role</th><th class="n">vectorless W</th><th class="n">measured W</th><th class="n">predicted W</th><th class="n">error</th><th class="n">leave-one-out</th><th class="n">area-only error</th><th class="n">FF activity from sim</th><th class="n">window (core cycles)</th></tr>
{trs}</table></div>
<p class="mute">FF activity from sim = flip-flops with a SAIF name plus those filled from their RTL register, over all FFs. Vectorless = Vivado default activity on the same netlist.</p></section>

<section><h2>Per group, measured / predicted W</h2><div class="tbl"><table>
<tr><th>config</th>{''.join('<th class="n">%s</th>' % g for g in GROUPS)}</tr>{grp}</table></div>
<p class="mute">Colour: within 15% green, within 25% amber, else red. Activity factors a_G (mean over the 4 calibration configs; spread = max/min):</p>
<div class="tbl"><table><tr><th>group</th><th class="n">a_G</th><th class="n">spread</th></tr>{aG}</table></div></section>

<section><h2>Why small configs miss</h2><ul>
<li>Switching activity is lower on small configs: measured over vectorless is 0.53 on MIN4 against 0.80–0.93 on the others, so one activity factor per group cannot fit both.</li>
<li>The model treats the in-tile logic outside the knob modules as fixed per width. It is not: the core's non-knob logic is 148,333 LUT at W4 and 50,552 LUT at MIN4 (util_hier.rpt of each tile).</li>
<li>The capacitance fit fell back to LUTs per group, so the D$ block RAMs (38 / 18 / 4 RAMB36) do not change the dcache prediction.</li>
<li>No variant within the plan passes both leave-one-out and HELD2; scoring 12 variants on 5 points and picking one would be selection bias.</li></ul></section>

<section><h2>How the numbers were made</h2><ul>
<li>Verilator sims of the 5 configs (built with <code>--trace-max-array 256</code> so register files are traced), FST to SAIF with array elements, renamed registers filled from their RTL names, Vivado <code>report_power</code> on the post-synthesis checkpoint.</li>
<li>Window = dhrystone loop start (step-up in the toggle profile) + 50,000 cycles; the first windows from the S1 formula fell in the print phase and were discarded (power-cal-window-check.md 7d46ad8f).</li>
<li>E6 RTL fix (reconf-off builds had active width 2) applied before W4 and MIN4 were rebuilt.</li></ul>
<div class="tbl"><table><tr><th>config</th><th>knobs</th></tr>{kn}</table></div></section>

<section><h2>Limits</h2><ul>
<li>Post-synthesis estimates without placement or routing; BoomTile only; Baseline variant only; one workload.</li>
<li>Not compared with board power. Absolute watts are not chip power.</li>
<li>Over-prediction on small configs of up to about 75–100%, where the fuzzer spends many evaluations; the model compresses the power range about 2×.</li></ul></section>

<section><h2>Open decision (owner)</h2><p>Adopt as is for ranking mid-to-large configs, revise the model (activity term from IPC, knob-scaled fixed part, D$ BRAM term) and validate it on a new held-out config, or keep the fuzzer on area only. Asked in the ooc-synthesis session.</p></section>
</main>
'''
open(a.out, 'w').write(page)
print('wrote', a.out, sha(a.out))
