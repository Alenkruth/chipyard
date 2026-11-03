#!/usr/bin/env python3
"""Predict whole-tile area of a configuration from the per-module sweep and compare with measured tiles. Deterministic.
Prediction = measured Baseline tile at full size (same width) + sum over modules of [module(config size) - module(full size)],
module numbers from the Baseline sweep (fuzzer_area.json). Each module's own offset cancels; LDQ x STQ pairs not in the sweep
are taken as additive.  usage: validate_predict.py --area fuzzer_area.json --tiles DIR --ref4 R --ref2 R --ref1 R --md out.md --csv out.csv"""
import argparse, csv, glob, json, os
COLS = ['total_luts', 'ffs', 'ramb36', 'ramb18']
ap = argparse.ArgumentParser()
for k in ('area', 'tiles', 'ref4', 'ref2', 'ref1', 'md', 'csv'): ap.add_argument('--' + k, required=True)
a = ap.parse_args()
A = json.load(open(a.area))
def tab(m):  # module -> list of (paramdict, cols)
    out = []
    for k, v in A[m].items():
        d = {}
        for kv in k.split(','):
            x, y = kv.split('='); d[x] = int(y) if y.isdigit() else y
        out.append((d, v))
    return out
T = {m: tab(m) for m in A}
def look(m, want, w):
    best = None
    for d, v in T[m]:
        if 'repl' in d: continue
        if any(d.get(k) != val for k, val in want.items()): continue
        if 'width' in d and d['width'] != w:
            continue
        best = v
    if best is None and any('width' in d for d, _ in T[m]):   # module measured at width 4 only for this size
        for d, v in T[m]:
            if 'repl' in d: continue
            if all(d.get(k) == val for k, val in want.items()) and d.get('width') == 4: best = v
    return best
FULL = dict(rob=256, fb=64, ftq=32, ras=32, iprf=192, fprf=96, iq_int=32, iq_mem=32, iq_fp=32, ldq=40, stq=40,
            btb_sets=128, btb_ways=2, tage=7, ic_sets=64, ic_ways=8, dc_sets=128, dc_ways=8)
MODS = [('Rob', {'entries': 'rob'}, 1), ('FetchBuffer', {'entries': 'fb'}, 1), ('FetchTargetQueue', {'entries': 'ftq'}, 1),
        ('BoomRAS', {'entries': 'ras'}, 1), ('RegfileInt', {'pregs': 'iprf'}, 1), ('FreelistInt', {'pregs': 'iprf'}, 1),
        ('RegfileFp', {'pregs': 'fprf'}, 1), ('FreelistFp', {'pregs': 'fprf'}, 1), ('IssueInt', {'entries': 'iq_int'}, 1),
        ('IssueMem', {'entries': 'iq_mem'}, 1), ('IssueFp', {'entries': 'iq_fp'}, 1),
        ('BTB', {'sets': 'btb_sets', 'ways': 'btb_ways'}, 2), ('Tage', {'tables': 'tage'}, 2),
        ('ICache', {'sets': 'ic_sets', 'ways': 'ic_ways'}, 1), ('DCache', {'sets': 'dc_sets', 'ways': 'dc_ways'}, 1)]
def area(m, keys, cfg, w):
    if m == 'DCache': w = 4   # D$ sizes were swept at width 4 only; use width 4 on both sides of the difference
    v = look(m, {p: cfg[k] for p, k in keys.items()}, w)
    if v is None: raise SystemExit('no sweep point for %s %s width %d' % (m, {p: cfg[k] for p, k in keys.items()}, w))
    return v
def lsu(cfg, w):
    l, s = cfg['ldq'], cfg['stq']
    v = look('Lsu', {'ldq': l, 'stq': s}, w)
    if v is not None: return v, False
    a1, a2, f = look('Lsu', {'ldq': l, 'stq': 40}, w), look('Lsu', {'ldq': 40, 'stq': s}, w), look('Lsu', {'ldq': 40, 'stq': 40}, w)
    return {c: a1[c] + a2[c] - f[c] for c in COLS}, True
refs = {4: json.load(open(a.ref4))['total'], 2: json.load(open(a.ref2))['total'], 1: json.load(open(a.ref1))['total']}
rows = []
for f in sorted(glob.glob(a.tiles + '/points/Tile/base/*/result.json')):
    r = json.load(open(f))
    if r.get('status') != 'ok': continue
    cfg = dict(FULL); w = 4
    for kv in r['params'].split(','):
        k, v = kv.split('=')
        if k == 'width': w = int(v)
        elif k in cfg: cfg[k] = int(v)
    if all(cfg[k] == FULL[k] for k in FULL): continue
    pred = dict((c, refs[w][c]) for c in COLS)
    for m, keys, n in MODS:
        x = area(m, keys, cfg, w); y = area(m, keys, FULL, w)
        for c in COLS: pred[c] += n * (x[c] - y[c])
    x, additive = lsu(cfg, w); y = look('Lsu', {'ldq': 40, 'stq': 40}, w)
    for c in COLS: pred[c] += x[c] - y[c]
    meas = r['total']
    rows.append((r['params'], w, additive, pred, meas))
with open(a.csv, 'w', newline='') as fh:
    wr = csv.writer(fh, lineterminator='\n')
    wr.writerow(['config', 'width', 'lsu_additive'] + ['pred_' + c for c in COLS] + ['meas_' + c for c in COLS] + ['err_pct_' + c for c in COLS])
    for p, w, ad, pr, me in rows:
        wr.writerow([p, w, ad] + [pr[c] for c in COLS] + [me[c] for c in COLS] + ['%.1f' % (100.0 * (pr[c] - me[c]) / me[c]) if me[c] else '' for c in COLS])
with open(a.md, 'w') as fh:
    fh.write('# Whole-tile area: predicted from the module sweep vs measured\n\nPrediction = measured Baseline tile at full size (same width) + sum of module changes from full size (Baseline sweep). Error = (predicted - measured) / measured.\n\n| config | width | predicted LUTs | measured LUTs | LUT error | predicted FFs | measured FFs | FF error | RAMB36 pred/meas | RAMB18 pred/meas |\n|---|---|---|---|---|---|---|---|---|---|\n')
    for p, w, ad, pr, me in rows:
        e = lambda c: '%.1f%%' % (100.0 * (pr[c] - me[c]) / me[c]) if me[c] else 'n/a'
        fh.write('| %s%s | %d | %s | %s | %s | %s | %s | %s | %d / %d | %d / %d |\n' % (p, ' (LSU additive)' if ad else '', w, format(pr['total_luts'], ','), format(me['total_luts'], ','), e('total_luts'), format(pr['ffs'], ','), format(me['ffs'], ','), e('ffs'), pr['ramb36'], me['ramb36'], pr['ramb18'], me['ramb18']))
print(len(rows), 'configs compared')
for p, w, ad, pr, me in rows: print('w%d LUT pred %d meas %d (%.1f%%)  FF pred %d meas %d (%.1f%%)' % (w, pr['total_luts'], me['total_luts'], 100.0*(pr['total_luts']-me['total_luts'])/me['total_luts'], pr['ffs'], me['ffs'], 100.0*(pr['ffs']-me['ffs'])/me['ffs']))
