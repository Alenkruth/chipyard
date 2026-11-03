#!/usr/bin/env python3
"""Per-module IFT split read IN CONTEXT from whole-tile runs (same flow). usage: ift_split_tile.py --base b.csv --nobridge n.csv --ift i.csv --md out.md --csv out.csv"""
import argparse, csv
ap = argparse.ArgumentParser()
for k in ('base', 'nobridge', 'ift', 'md', 'csv'): ap.add_argument('--' + k, required=True)
a = ap.parse_args()
def load(p): return {r['knob']: r for r in csv.DictReader(open(p))}
B, N, I = load(a.base), load(a.nobridge), load(a.ift)
def g(r, c): return int(r[c]) if r and r[c] != '' else None
rows = []
for k in B:
    b, n, i = B[k], N.get(k), I.get(k)
    row = [k, b['module']]
    for c in ('luts', 'ffs', 'ramb36'):
        bv, nv, iv = g(b, c), g(n, c), g(i, c)
        tot = (iv - bv) if iv is not None and bv is not None else ''
        pipe = (nv - bv) if nv is not None and bv is not None else ''
        rec = (iv - nv) if iv is not None and nv is not None else ''
        row += [bv, tot, pipe, rec]
    rows.append(row)
H = []
for c in ('luts', 'ffs', 'ramb36'):
    H += ['baseline_' + c, 'ift_added_' + c, 'ift_tracking_' + c, 'ift_bridge_export_' + c]
H = ['knob', 'module'] + H
with open(a.csv, 'w', newline='') as f:
    w = csv.writer(f, lineterminator='\n'); w.writerow(H); w.writerows(rows)
with open(a.md, 'w') as f:
    f.write('# Area added by IFT, per module (BoomTile, width 4, built size, post-synthesis, one flow; each module read in context)\n\n"ift_added" = IFT minus Baseline. It splits into "IFT tracking" (IFT without bridge export, minus Baseline) and "IFT bridge export" (IFT minus IFT without bridge export). Names as in glossary.md. Raw values; the tool moves logic between neighbouring modules, so compare sums over neighbours (fetch buffer + f4).\n\n| ' + ' | '.join(H) + ' |\n|' + '---|' * len(H) + '\n')
    for r in rows: f.write('| ' + ' | '.join(str(x) for x in r) + ' |\n')
