#!/usr/bin/env python3
"""Configuration area model v2 for a Baseline BoomTile ("slope-calibrated module deltas"). Deterministic.

  area_c(cfg, w) = tile_full_c(w) + sum_m n_m * f_m,c(w) * [OOC_m,c(size_m(cfg), w) - OOC_m,c(full, w)]     c in {LUTs, FFs}
  RAMB36/RAMB18: same with f = 1; the DCache BRAM delta is scaled by the width-w/width-4 sweep ratio at full size (0.5).

OOC_m  = out-of-context module sweep (fuzzer_area.json); n_m = 2 for BTB and Tage (two banks), else 1.
f_m    = 1 for the register files (RegfileInt, RegfileFp): their read-port mux LUTs are booked by Vivado's rebuilt
         hierarchy under the consuming modules (ALU units, register read, FPU), so the in-tile register file
         instance undercounts them, while regfile + consumers in the tile track the OOC regfile delta (slope 1.00).
       = least-squares slope (through 0) of in-tile delta vs OOC delta over the measured mixed tiles for the
         queue-like modules (ROB, issue queues, LSU, fetch buffer, FTQ, freelists), where OOC keeps per-entry
         micro-op fields the tile prunes; per width when >= 3 mixed tiles exist at that width, else pooled.
       = 1 for the rest (BTB, TAGE, RAS, ICache, DCache: in-tile ~ OOC, and small).
Validation: leave-one-tile-out (the predicted tile is excluded from every slope).
usage: model_m2.py --area fuzzer_area.json --intile intile.json --factors out.json --md out.md --csv out.csv
                   [--check 'name|w=2,rob=64,...' ...] [--checks-md out.md]"""
import argparse, csv, json
ap = argparse.ArgumentParser()
for k in ('area', 'intile', 'factors', 'md', 'csv'): ap.add_argument('--' + k, required=True)
ap.add_argument('--check', action='append', default=[]); ap.add_argument('--checks-md')
a = ap.parse_args()
C2 = ['total_luts', 'ffs']; CB = ['ramb36', 'ramb18']; COLS = C2 + CB
FULL = dict(rob=256, fb=64, ftq=32, ras=32, iprf=192, fprf=96, iq_int=32, iq_mem=32, iq_fp=32, ldq=40, stq=40,
            btb_sets=128, btb_ways=2, tage=7, ic_sets=64, ic_ways=8, dc_sets=128, dc_ways=8)
MODS = [('Rob', {'entries': 'rob'}), ('FetchBuffer', {'entries': 'fb'}), ('FetchTargetQueue', {'entries': 'ftq'}),
        ('BoomRAS', {'entries': 'ras'}), ('RegfileInt', {'pregs': 'iprf'}), ('FreelistInt', {'pregs': 'iprf'}),
        ('RegfileFp', {'pregs': 'fprf'}), ('FreelistFp', {'pregs': 'fprf'}), ('IssueInt', {'entries': 'iq_int'}),
        ('IssueMem', {'entries': 'iq_mem'}), ('IssueFp', {'entries': 'iq_fp'}), ('BTB', {'sets': 'btb_sets', 'ways': 'btb_ways'}),
        ('Tage', {'tables': 'tage'}), ('ICache', {'sets': 'ic_sets', 'ways': 'ic_ways'}), ('DCache', {'sets': 'dc_sets', 'ways': 'dc_ways'}),
        ('Lsu', None)]
NB = {'BTB': 2, 'Tage': 2}
SLOPED = ['Rob', 'FetchBuffer', 'FetchTargetQueue', 'FreelistInt', 'FreelistFp', 'IssueInt', 'IssueMem', 'IssueFp', 'Lsu']
A = json.load(open(a.area))
def pk(k):
    d = {}
    for kv in k.split(','):
        x, y = kv.split('='); d[x] = int(y) if y.isdigit() else y
    return d
T = {m: [(pk(k), v) for k, v in A[m].items()] for m in A}
def look(m, want, w):
    hit = [v for d, v in T[m] if 'repl' not in d and all(d.get(k) == x for k, x in want.items()) and d.get('width', w) == w]
    if not hit: hit = [v for d, v in T[m] if 'repl' not in d and all(d.get(k) == x for k, x in want.items()) and d.get('width') == 4]
    return hit[-1] if hit else None
def ooc(m, keys, cfg, w):
    if m == 'Lsu':
        v = look('Lsu', {'ldq': cfg['ldq'], 'stq': cfg['stq']}, w)
        if v is None:   # LDQ x STQ pair not swept: additive
            p, q, f = look('Lsu', {'ldq': cfg['ldq'], 'stq': 40}, w), look('Lsu', {'ldq': 40, 'stq': cfg['stq']}, w), look('Lsu', {'ldq': 40, 'stq': 40}, w)
            if None in (p, q, f): raise SystemExit('no Lsu sweep point for ldq=%d stq=%d width %d' % (cfg['ldq'], cfg['stq'], w))
            v = {c: p[c] + q[c] - f[c] for c in COLS}
    else:
        v = look(m, {p: cfg[k] for p, k in keys.items()}, 4 if m == 'DCache' else w)
        if v is None: raise SystemExit('no sweep point for %s %s width %d' % (m, {p: cfg[k] for p, k in keys.items()}, w))
    return {c: NB.get(m, 1) * v[c] for c in COLS}
def dooc(m, keys, cfg, w):
    x, y = ooc(m, keys, cfg, w), ooc(m, keys, FULL, w)
    d = {c: x[c] - y[c] for c in COLS}
    if m == 'DCache' and w != 4:   # D$ swept at width 4 (data array duplicated per memory port); width 1/2 has one copy
        s = look('DCache', {'sets': FULL['dc_sets'], 'ways': FULL['dc_ways']}, w)['ramb36'] / look('DCache', {'sets': FULL['dc_sets'], 'ways': FULL['dc_ways']}, 4)['ramb36']
        for c in CB: d[c] = d[c] * s
    return d
D = json.load(open(a.intile))
def isfull(t): return all(t['cfg'][k] == v for k, v in FULL.items())
full = {t['width']: t for t in D if isfull(t)}
mixed = [t for t in D if not isfull(t)]
def slopes(excl=None):
    f = {}
    for w in sorted(full):
        f[w] = {}
        for m, keys in MODS:
            f[w][m] = {}
            for c in C2:
                if m not in SLOPED: f[w][m][c] = 1.0; continue
                pool = [t for t in mixed if t is not excl and t['width'] == w]
                if len(pool) < 3: pool = [t for t in mixed if t is not excl]
                pts = [(dooc(m, keys, t['cfg'], t['width'])[c], t['groups'][m]['intile'][c] - full[t['width']]['groups'][m]['intile'][c]) for t in pool]
                pts = [p for p in pts if p[0] != 0]
                f[w][m][c] = round(sum(x * y for x, y in pts) / sum(x * x for x, y in pts), 4) if pts else 1.0
    return f
def predict(cfg, w, f):
    p = {c: full[w]['total'][c] for c in COLS}
    for m, keys in MODS:
        d = dooc(m, keys, cfg, w)
        for c in COLS: p[c] += (f[w][m][c] if c in C2 else 1.0) * d[c]
    return {c: int(round(p[c])) for c in COLS}
F = slopes()
json.dump({'model': 'Baseline BoomTile configuration area v2 (slope-calibrated module deltas)',
           'formula': 'area_c(cfg,w) = tile_full_c(w) + sum_m n_m * f_m,c(w) * (OOC_m,c(size,w) - OOC_m,c(full,w)); n=2 for BTB and Tage; f=1 for ramb36/ramb18; OOC from fuzzer_area.json (DCache at width 4; LDQ x STQ pairs not swept are additive)',
           'full_size': FULL, 'n': NB,
           'tile_full': {str(w): {c: full[w]['total'][c] for c in COLS} for w in sorted(full)},
           'factors': {str(w): F[w] for w in sorted(F)}}, open(a.factors, 'w'), indent=1, sort_keys=True)
rows = []
for t in sorted(mixed, key=lambda t: (t['width'], t['total']['total_luts'])):
    p = predict(t['cfg'], t['width'], slopes(excl=t)); me = t['total']
    rows.append((t['point'].split('/')[-1], t['width'], p, me))
err = lambda p, m, c: 100.0 * (p[c] - m[c]) / m[c] if m[c] else float('nan')
with open(a.csv, 'w', newline='') as fh:
    wr = csv.writer(fh, lineterminator='\n')
    wr.writerow(['config', 'width'] + ['pred_' + c for c in COLS] + ['meas_' + c for c in COLS] + ['err_pct_' + c for c in COLS])
    for n, w, p, m in rows: wr.writerow([n, w] + [p[c] for c in COLS] + [m[c] for c in COLS] + ['%.1f' % err(p, m, c) for c in COLS])
with open(a.md, 'w') as fh:
    fh.write('# Configuration area model v2 (Baseline BoomTile): predicted vs measured, leave-one-tile-out\n\n'
             'Formula and factors: config-area-model-v2.json. Each tile is predicted with factors fitted without that tile. Error = (predicted - measured) / measured.\n\n'
             '| config | width | predicted LUTs | measured LUTs | LUT error | predicted FFs | measured FFs | FF error | RAMB36 pred/meas | RAMB18 pred/meas |\n|---|---|---|---|---|---|---|---|---|---|\n')
    for n, w, p, m in rows:
        fh.write('| %s | %d | %s | %s | %+.1f%% | %s | %s | %+.1f%% | %d / %d | %d / %d |\n' % (n, w, format(p['total_luts'], ','), format(m['total_luts'], ','), err(p, m, 'total_luts'),
                 format(p['ffs'], ','), format(m['ffs'], ','), err(p, m, 'ffs'), p['ramb36'], m['ramb36'], p['ramb18'], m['ramb18']))
for n, w, p, m in rows: print('w%d %-60s LUT %7d/%7d %+5.1f%%  FF %7d/%7d %+5.1f%%  RAMB36 %d/%d RAMB18 %d/%d' % (w, n[:60], p['total_luts'], m['total_luts'], err(p, m, 'total_luts'), p['ffs'], m['ffs'], err(p, m, 'ffs'), p['ramb36'], m['ramb36'], p['ramb18'], m['ramb18']))
if a.check:
    out = ['# Configuration area model v2: predictions for requested configurations\n', 'Factors fitted on all 8 measured mixed tiles (config-area-model-v2.json).\n',
           '| name | width | config | predicted LUTs | predicted FFs | RAMB36 | RAMB18 | measured LUTs | measured FFs |', '|---|---|---|---|---|---|---|---|---|']
    for ch in a.check:
        name, spec = ch.split('|'); cfg = dict(FULL); w = 4
        for kv in spec.split(','):
            k, v = kv.split('=')
            if k == 'w': w = int(v)
            else: cfg[k] = int(v)
        p = predict(cfg, w, F)
        meas = [t for t in D if t['width'] == w and t['cfg'] == cfg]
        ms = (format(meas[0]['total']['total_luts'], ','), format(meas[0]['total']['ffs'], ',')) if meas else ('not measured', 'not measured')
        out.append('| %s | %d | %s | %s | %s | %d | %d | %s | %s |' % (name, w, ','.join('%s=%d' % (k, cfg[k]) for k in FULL), format(p['total_luts'], ','), format(p['ffs'], ','), p['ramb36'], p['ramb18'], ms[0], ms[1]))
        print('CHECK', name, 'w%d' % w, p, 'measured' if meas else 'not measured', meas[0]['total'] if meas else '')
    if a.checks_md: open(a.checks_md, 'w').write('\n'.join(out) + '\n')
