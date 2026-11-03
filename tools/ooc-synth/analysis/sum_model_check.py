#!/usr/bin/env python3
"""'Sum of modules' area (owner question 2026-10-31): sum of the OOC module numbers at the configuration's sizes plus the
in-tile rest of the tile (tile minus the knob-driven modules, measured in the full-size tile of the same width).
Compared with the measured Baseline tiles and with model v2 (leave-one-tile-out values from its validation CSV). Deterministic.
usage: sum_model_check.py --intile intile-module-table.json --v2csv config-area-model-v2-validation.csv --md out.md"""
import argparse, csv, json
ap = argparse.ArgumentParser()
for k in ('intile', 'v2csv', 'md'): ap.add_argument('--' + k, required=True)
a = ap.parse_args()
D = json.load(open(a.intile))
FULL = dict(rob=256, fb=64, ftq=32, ras=32, iprf=192, fprf=96, iq_int=32, iq_mem=32, iq_fp=32, ldq=40, stq=40,
            btb_sets=128, btb_ways=2, tage=7, ic_sets=64, ic_ways=8, dc_sets=128, dc_ways=8)
full = {t['width']: t for t in D if t['cfg'] == FULL}
V = {r['config']: r for r in csv.DictReader(open(a.v2csv))}
L = ['# "Sum of modules" vs model v2 vs measured (Baseline BoomTile)\n',
     'Sum of modules = sum of the out-of-context module numbers at the configuration sizes + rest of the tile (in-tile, full-size tile of the same width). '
     'Algebraically this is the plain-delta model plus a constant per width (sum of OOC modules minus sum of the same modules in the tile, at full size), '
     'so the differences between configurations are those of the plain-delta model.\n',
     '| width | constant overcount at full size, LUTs | FFs |', '|---|---|---|']
for w in sorted(full):
    t = full[w]
    L.append('| %d | %s | %s |' % (w, format(sum(g['ooc']['total_luts'] - g['intile']['total_luts'] for g in t['groups'].values()), ','),
                                   format(sum(g['ooc']['ffs'] - g['intile']['ffs'] for g in t['groups'].values()), ',')))
L += ['', '| config | width | measured LUTs | sum LUTs | sum error | v2 LUTs (left out) | v2 error | measured FFs | sum FFs | sum error | v2 FFs | v2 error |',
      '|---|---|---|---|---|---|---|---|---|---|---|---|']
for t in sorted(D, key=lambda t: (t['width'], t['total']['total_luts'])):
    n = t['point'].split('/')[-1]; w = t['width']
    s = {c: sum(g['ooc'][c] for g in t['groups'].values()) + full[w]['rest'][c] for c in ('total_luts', 'ffs')}
    m = t['total']; v = V.get(n)
    e = lambda p, c: '%+.1f%%' % (100.0 * (p - m[c]) / m[c])
    vl = (format(int(v['pred_total_luts']), ','), e(int(v['pred_total_luts']), 'total_luts'), format(int(v['pred_ffs']), ','), e(int(v['pred_ffs']), 'ffs')) if v else ('full size (reference)', '0%', 'full size (reference)', '0%')
    L.append('| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (n, w, format(m['total_luts'], ','), format(s['total_luts'], ','), e(s['total_luts'], 'total_luts'), vl[0], vl[1],
             format(m['ffs'], ','), format(s['ffs'], ','), e(s['ffs'], 'ffs'), vl[2], vl[3]))
open(a.md, 'w').write('\n'.join(L) + '\n')
print('\n'.join(L))
