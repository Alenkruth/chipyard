#!/usr/bin/env python3
"""Configuration area model 'scaled module deltas' + its validation. Deterministic.
area(config, w) = Baseline tile at full size (width w) + sum_m n_m * f_m(w) * [module_m(size, w) - module_m(full, w)]
module_m from the Baseline module sweep (fuzzer_area.json); f_m(w) = module_m in context (tile hierarchy, full size)
/ module_m out of context (full size), per column.  Writes the factors (JSON) and a predicted-vs-measured table.
usage: model_scaled.py --area fuzzer_area.json --intile4 c --intile2 c --intile1 c --ref4 r --ref2 r --ref1 r --tiles DIR --factors out.json --md out.md"""
import argparse, csv, glob, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
for k in ('area', 'intile4', 'intile2', 'intile1', 'ref4', 'ref2', 'ref1', 'tiles', 'factors', 'md'): ap.add_argument('--' + k, required=True)
a = ap.parse_args()
src = open(os.path.join(HERE, 'validate_predict.py')).read().split('refs = {4:')[0]
ns = {'__name__': 'x'}; sys.argv = ['x', '--area', a.area, '--tiles', 'x', '--ref4', 'x', '--ref2', 'x', '--ref1', 'x', '--md', 'x', '--csv', 'x']
exec(src, ns)
area, lsu, look, FULL, MODS = ns['area'], ns['lsu'], ns['look'], ns['FULL'], ns['MODS']
COLS = ['total_luts', 'ffs']
KN = {'Rob': ['ROB entries'], 'FetchBuffer': ['Fetch buffer entries', 'Fetch buffer neighbour f4'], 'FetchTargetQueue': ['FTQ entries'],
      'RegfileInt': ['INT PRF size (regfile)'], 'FreelistInt': ['INT freelist (PRF size)'], 'RegfileFp': ['FP PRF size (regfile)'],
      'FreelistFp': ['FP freelist (PRF size)'], 'IssueInt': ['INT issue queue entries'], 'IssueMem': ['MEM issue queue entries'],
      'IssueFp': ['FP issue queue entries'], 'BTB': ['BTB (bank 0)'], 'Tage': ['TAGE (bank 0)'], 'ICache': ['ICache sets/ways'],
      'DCache': ['DCache sets/ways'], 'Lsu': ['LDQ/STQ entries']}
fac = {}
for w, f in ((4, a.intile4), (2, a.intile2), (1, a.intile1)):
    R = {r['knob']: r for r in csv.DictReader(open(f))}
    fac[w] = {}
    for m, ks in KN.items():
        ww = 4 if m == 'DCache' else w
        full = look('Lsu', {'ldq': 40, 'stq': 40}, ww) if m == 'Lsu' else area(m, [x for x in MODS if x[0] == m][0][1], FULL, ww)
        fac[w][m] = {c: round(sum(int(R[k]['luts' if c == 'total_luts' else c] or 0) for k in ks) / max(1, full[c]), 4) for c in COLS}
    fac[w]['BoomRAS'] = {c: 1.0 for c in COLS}
json.dump({'formula': 'area = tile_full(w) + sum_m n_m * f_m(w) * (module_m(size,w) - module_m(full,w)); n=2 for BTB and Tage (2 banks)',
           'tile_full': {str(w): {c: json.load(open(p))['total'][c] for c in COLS} for w, p in ((4, a.ref4), (2, a.ref2), (1, a.ref1))},
           'factors': {str(w): fac[w] for w in fac}}, open(a.factors, 'w'), indent=1, sort_keys=True)
refs = {4: json.load(open(a.ref4))['total'], 2: json.load(open(a.ref2))['total'], 1: json.load(open(a.ref1))['total']}
lines = []
for f in sorted(glob.glob(a.tiles + '/points/Tile/base/*/result.json')):
    r = json.load(open(f)); cfg = dict(FULL); w = 4
    if r.get('status') != 'ok': continue
    for kv in r['params'].split(','):
        k, v = kv.split('=')
        if k == 'width': w = int(v)
        elif k in cfg: cfg[k] = int(v)
    pred = {c: refs[w][c] for c in COLS}
    for m, keys, n in MODS + [('Lsu', None, 1)]:
        ww = 4 if m == 'DCache' else w
        if m == 'Lsu': x, _ = lsu(cfg, w); y = look('Lsu', {'ldq': 40, 'stq': 40}, w)
        else: x = area(m, keys, cfg, w); y = area(m, keys, FULL, w)
        for c in COLS: pred[c] += n * fac[w][m][c] * (x[c] - y[c])
    me = r['total']
    lines.append('| %s | %d | %s | %s | %.1f%% | %s | %s | %.1f%% |' % (r['params'], w, format(int(pred['total_luts']), ','), format(me['total_luts'], ','), 100 * (pred['total_luts'] / me['total_luts'] - 1), format(int(pred['ffs']), ','), format(me['ffs'], ','), 100 * (pred['ffs'] / me['ffs'] - 1)))
open(a.md, 'w').write('# Configuration area model "scaled module deltas": predicted vs measured whole tile\n\nFormula and factors in config-area-model-factors.json. Error = (predicted - measured) / measured.\n\n| config | width | predicted LUTs | measured LUTs | LUT error | predicted FFs | measured FFs | FF error |\n|---|---|---|---|---|---|---|---|\n' + '\n'.join(lines) + '\n')
print('\n'.join(lines))
