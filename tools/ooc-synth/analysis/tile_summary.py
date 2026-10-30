#!/usr/bin/env python3
"""Whole-tile headline table from result.json files. Deterministic.
usage: tile_summary.py --md out.md --csv out.csv  NAME=result.json ...   (first NAME must be the base variant)"""
import argparse, csv, json
ap = argparse.ArgumentParser(); ap.add_argument('--md', required=True); ap.add_argument('--csv', required=True); ap.add_argument('pts', nargs='+')
a = ap.parse_args()
pts = [(p.split('=', 1)[0], json.load(open(p.split('=', 1)[1]))) for p in a.pts]
COLS = ['total_luts', 'ffs', 'ramb36', 'ramb18', 'dsp']; base = pts[0][1]['total']
with open(a.csv, 'w', newline='') as f:
    w = csv.writer(f, lineterminator='\n'); w.writerow(['variant'] + COLS + ['lut_vs_base_pct', 'ff_vs_base_pct', 'wall_s', 'peak_mb'])
    for n, r in pts:
        t = r['total']; w.writerow([n] + [t[c] for c in COLS] + ['%.2f' % (100.0 * (t['total_luts'] / base['total_luts'] - 1)), '%.2f' % (100.0 * (t['ffs'] / base['ffs'] - 1)), r.get('wall_s'), r.get('peak_mb')])
with open(a.md, 'w') as f:
    f.write('# Whole-tile area, width 4 (BoomTile, out-of-context, post-synthesis, one flow)\n\n| variant | LUTs | FFs | RAMB36 | RAMB18 | DSP | LUT vs base | FF vs base |\n|---|---|---|---|---|---|---|---|\n')
    for n, r in pts:
        t = r['total']; f.write('| %s | %d | %d | %d | %d | %d | %+.1f%% | %+.1f%% |\n' % (n, t['total_luts'], t['ffs'], t['ramb36'], t['ramb18'], t['dsp'], 100.0 * (t['total_luts'] / base['total_luts'] - 1), 100.0 * (t['ffs'] / base['ffs'] - 1)))
