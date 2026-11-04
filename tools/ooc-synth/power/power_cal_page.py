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
IDS = [i for i in IDS if i in fit['configs']]  # v1 fit covers only its own configs (HELD3 is v2-only)
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

# ---- v2 section (shown first when ROOT/fit-v2/power-cal-v2.json exists); every number below comes from the four v2 JSON files
v2html = ''
v2p = os.path.join(a.root, 'fit-v2', 'power-cal-v2.json')
if os.path.exists(v2p):
    pj = lambda *p: os.path.join(a.root, *p)
    v2 = json.load(open(v2p)); prm = json.load(open(pj('fit-v2', 'params.json')))
    prd = json.load(open(pj('fit-v2', 'predict-HELD3.json'))); pins = json.load(open(pj('pins-v2.json')))
    fs = lambda p: hashlib.sha1(open(p, 'rb').read()).hexdigest()
    tot = v2['all_totals']; ORD = sorted(tot, key=lambda k: (tot[k]['role'] == 'held-out', k))
    lim = v2['threshold']; e3 = v2['error_total']
    c2 = lambda x: cls(x, lim)
    # chart: measured vs predicted total per config, v2
    vm2 = 3.0; W2_, H2_ = 640, 280; L2, B2 = 56, 44; g2 = (W2_ - L2 - 20) / len(ORD)
    y2 = lambda v: H2_ - B2 - (H2_ - B2 - 16) * v / vm2
    s2 = ['<svg viewBox="0 0 %d %d" role="img" aria-label="v2 model: measured and predicted total dynamic power per config">' % (W2_, H2_)]
    for t in [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
        s2.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/><text x="%d" y="%.1f" class="tick" text-anchor="end">%.1f</text>' % (L2, W2_ - 10, y2(t), y2(t), L2 - 6, y2(t) + 4, t))
    for k, i in enumerate(ORD):
        x0 = L2 + 12 + k * g2; r = tot[i]
        s2.append('<rect x="%.1f" y="%.1f" width="%d" height="%.1f" class="m"/>' % (x0, y2(r['measured']), bw, H2_ - B2 - y2(r['measured'])))
        s2.append('<rect x="%.1f" y="%.1f" width="%d" height="%.1f" class="p"/>' % (x0 + bw + 4, y2(r['predicted']), bw, H2_ - B2 - y2(r['predicted'])))
        s2.append('<text x="%.1f" y="%.1f" class="lab" text-anchor="middle">%s</text>' % (x0 + bw + 2, H2_ - B2 + 16, i))
        if r['role'] == 'held-out': s2.append('<text x="%.1f" y="%.1f" class="lab" text-anchor="middle">held-out</text>' % (x0 + bw + 2, H2_ - B2 + 30))
    s2.append('<text x="%d" y="%d" class="tick" text-anchor="middle">config</text>' % ((L2 + W2_) // 2, H2_ - 2))
    s2.append('<text x="12" y="%d" class="tick" text-anchor="middle" transform="rotate(-90 12 %d)">total dynamic power (W)</text></svg>' % (H2_ // 2, H2_ // 2))
    svg2 = ''.join(s2)
    t6 = ''.join('<tr><td>%s</td><td>%s</td><td class="n">%.3f</td><td class="n">%.3f</td><td class="n %s">%s</td></tr>' % (
        i, tot[i]['role'], tot[i]['measured'], tot[i]['predicted'], c2(tot[i]['error']), pct(tot[i]['error'])) for i in ORD)
    gr2 = v2['groups']
    tg = ''.join('<tr><td>%s</td><td class="n">%.4f</td><td class="n">%.4f</td><td class="n %s">%s</td></tr>' % (g, gr2[g]['measured'], gr2[g]['predicted'], cls(gr2[g]['error'], 0.15), pct(gr2[g]['error'])) for g in GROUPS)
    P = v2['params']
    cal = [i for i in ORD if tot[i]['role'] != 'held-out']
    tl = ''.join('<tr><td>%s</td><td class="n %s">%s</td><td class="n %s">%s</td><td class="n">%.3f</td><td class="n">%.3f</td><td class="n">%.3f</td><td class="n">%.1f</td></tr>' % (
        i, c2(v2['in_sample'][i]['error']), pct(v2['in_sample'][i]['error']), c2(v2['loo'][i]['error']), pct(v2['loo'][i]['error']),
        v2['loo'][i]['theta'], v2['loo'][i]['beta']['core'], v2['loo'][i]['beta']['frontend'], v2['loo'][i]['lambda_B']) for i in cal)
    aG2 = ''.join('<tr><td>%s</td><td class="n">%.3e</td></tr>' % (g, P['a_G'][g]) for g in GROUPS)
    seal = [('pins-v2.json', fs(pj('pins-v2.json')), 'params.json pins_file_sha1 = %s' % prm['pins_file_sha1']),
            ('fit-v2/params.json', fs(pj('fit-v2', 'params.json')), 'power-cal-v2.json params_sha1 = %s; predict-HELD3.json params_sha1 = %s' % (v2['params_sha1'], prd['params_sha1'])),
            ('fit-v2/predict-HELD3.json', fs(pj('fit-v2', 'predict-HELD3.json')), 'power-cal-v2.json predict_sha1 = %s' % v2['predict_sha1']),
            ('HELD3 power_dhry.rpt', v2['power_dhry_sha1'], 'measured power report read after the prediction was sealed'),
            ('fit script', prm['script_sha1'], 'predict script_sha1 = %s' % prd['script_sha1']),
            ('S1 log of HELD3', prd['s1_log_sha1'], 'input of the sealed prediction')]
    sl = ''.join('<tr><td>%s</td><td class="mono">%s</td><td class="mono">%s</td></tr>' % (E(a_), E(b_), E(c_)) for a_, b_, c_ in seal)
    pl = ''.join('<tr><td>%s</td><td class="mono">%s</td></tr>' % (i, E(' '.join('%s:%s' % (f, h[:8]) for f, h in sorted(pins[i].items())))) for i in sorted(pins))
    v2html = f"""
<section class="verdict"><h2>v2 model: verdict <b>{E(v2['verdict'])}</b></h2>
<p>Held-out config {v2['id']}: measured <b>{v2['measured_total_W']:.3f} W</b>, predicted <b>{v2['pred_total_W']:.3f} W</b>, error <b class="{c2(e3)}">{pct(e3)}</b> against the owner rule of &plusmn;{100*lim:.0f}%.
Totals order {'match' if v2['order_match'] else 'do not match'} (measured low to high {E(' &lt; '.join(v2['order_measured']))}; predicted {E(' &lt; '.join(v2['order_predicted']))}).
Sensitivity of the error to u &plusmn;3.5%: {pct(v2['sens_u_minus_error'])} / {pct(v2['sens_u_plus_error'])}.</p></section>

<section><h2>v2: measured vs predicted total, six configs</h2>
<p class="key mute"><span class="m"></span>measured (activity)<span class="p"></span>predicted (v2)</p>{svg2}</section>

<section><h2>v2: the six configs</h2><div class="tbl"><table>
<tr><th>config</th><th>role</th><th class="n">measured W</th><th class="n">predicted W</th><th class="n">error</th></tr>{t6}</table></div></section>

<section><h2>v2: per group, {v2['id']}</h2><div class="tbl"><table>
<tr><th>group</th><th class="n">measured W</th><th class="n">predicted W</th><th class="n">error</th></tr>{tg}
<tr><td><b>total</b></td><td class="n">{v2['measured_total_W']:.4f}</td><td class="n">{v2['pred_total_W']:.4f}</td><td class="n {c2(e3)}">{pct(e3)}</td></tr></table></div>
<p class="mute">Colour: groups within 15% green, within 25% amber, else red; totals use the {100*lim:.0f}% rule.</p></section>

<section><h2>v2: fitted parameters</h2>
<p>theta = <b>{P['theta']:.3f}</b>, beta core = <b>{P['beta']['core']:.3f}</b>, beta frontend = <b>{P['beta']['frontend']:.3f}</b>, lambda_B = <b>{P['lambda_B']:.1f}</b> (condition number {v2['lambda_condition_number']:.2f}); {v2['id']} utilisation u = {v2['u']:.4f}.
J is flat within 1.5x of its minimum for theta in [{v2['J_flat_interval_1p5x'][0]:.3f}, {v2['J_flat_interval_1p5x'][1]:.3f}]. Model: <code>{E(prm['model'])}</code>.</p>
<div class="tbl"><table><tr><th>calibration id</th><th class="n">in-sample error</th><th class="n">leave-one-out error</th><th class="n">LOO theta</th><th class="n">LOO beta core</th><th class="n">LOO beta frontend</th><th class="n">LOO lambda_B</th></tr>{tl}</table></div>
<div class="tbl"><table><tr><th>group</th><th class="n">a_G</th></tr>{aG2}</table></div>
<p class="mute">In-tile F check: {E(v2['F_check'])}.</p></section>

<section><h2>v2: comparison with v1 and area-only</h2>
<p>v1 model (calibrated without HELD2, as printed in power-cal-v2.json): predicted <b>{v2['v1_pred_total_W']:.3f} W</b>, error <b class="{c2(v2['v1_error_total'])}">{pct(v2['v1_error_total'])}</b>.
Area-only proxy (one factor times the sum of C_G): predicted <b>{v2['area_only_total_W']:.3f} W</b>, error <b class="{c2(v2['area_only_error'])}">{pct(v2['area_only_error'])}</b>.
v2 error <b class="{c2(e3)}">{pct(e3)}</b>.</p></section>

<section><h2>v2: seal chain</h2>
<p>The {v2['id']} prediction (<code>predict-HELD3.json</code>, sha1 {E(v2['predict_sha1'][:8])}) was sealed from the parameters (sha1 {E(v2['params_sha1'][:8])}) before the power of {v2['id']} existed: its power report is hashed here only for the comparison, and the prediction file records the parameter sha1 it was made with.</p>
<div class="tbl"><table><tr><th>item</th><th>sha1 (computed or recorded)</th><th>cross-reference</th></tr>{sl}</table></div>
<p class="mute">Input pins per calibration id (first 8 hex of sha1, file:hash):</p>
<div class="tbl"><table>{pl}</table></div></section>

<h2 style="font-size:1.4rem;border-top:1px solid var(--line);padding-top:20px">v1 model on the same data</h2>
"""

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

{v2html}<section class="verdict"><h2>v1: verdict <b>{verdict}</b> by the rule, weak evidence</h2>
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
