#!/usr/bin/env python3
"""Per-module reconfiguration-only overhead read in context from whole-tile runs. usage: reconf_split_tile.py --base b.csv --reconf r.csv --ift i.csv --ift-reconf ir.csv --md out.md --csv out.csv"""
import argparse, csv
ap = argparse.ArgumentParser()
for k in ('base', 'reconf', 'ift', 'ift-reconf', 'md', 'csv'): ap.add_argument('--' + k, required=True)
a = ap.parse_args()
def load(p): return {r['knob']: r for r in csv.DictReader(open(p))}
B, R, I, IR = load(a.base), load(a.reconf), load(a.ift), load(getattr(a, 'ift_reconf'))
def g(r, c): return int(r[c]) if r and r[c] != '' else None
def d(x, y): return (x - y) if x is not None and y is not None else ''
H = ['knob', 'module']
for c in ('luts', 'ffs', 'ramb36', 'ramb18'): H += ['baseline_' + c, 'reconfiguration_only_added_' + c, 'ift_' + c, 'reconfiguration_added_on_ift_' + c]
rows = []
for k in B:
    row = [k, B[k]['module']]
    for c in ('luts', 'ffs', 'ramb36', 'ramb18'):
        b, r, i, ir = g(B[k], c), g(R.get(k), c), g(I.get(k), c), g(IR.get(k), c)
        row += [b, d(r, b), i, d(ir, i)]
    rows.append(row)
with open(a.csv, 'w', newline='') as f:
    w = csv.writer(f, lineterminator='\n'); w.writerow(H); w.writerows(rows)
with open(a.md, 'w') as f:
    f.write('# Area added by runtime reconfiguration, per module (BoomTile, width 4, built size, post-synthesis, one flow; each module read in context)\n\n"reconfiguration_only_added" = Baseline + reconfiguration minus Baseline. "reconfiguration_added_on_ift" = IFT + reconfiguration minus IFT. Raw values; logic can move between neighbouring modules, so compare sums over neighbours. Names as in glossary.md.\n\n| ' + ' | '.join(H) + ' |\n|' + '---|' * len(H) + '\n')
    for r in rows: f.write('| ' + ' | '.join(str(x) for x in r) + ' |\n')
