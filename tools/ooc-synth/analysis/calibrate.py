#!/usr/bin/env python3
"""Compare module-OOC results with the same modules read IN CONTEXT from a hierarchical utilization report.
usage: calibrate.py --ctx REPORT [--ctx-label L] --ooc-glob 'GLOB/*/result.json' ... --map inst=Module ... [--ref REPORT --ref-label L]
Deterministic: same inputs give byte-identical output. Raw columns only."""
import argparse, glob, json, re, sys
COLS = ['Total LUTs', 'FFs', 'RAMB36', 'RAMB18', 'URAM', 'DSP']
KEY = {'Total LUTs': 'total_luts', 'FFs': 'ffs', 'RAMB36': 'ramb36', 'RAMB18': 'ramb18', 'URAM': 'uram', 'DSP': 'dsp'}

def parse(path):
    hdr = None; st = []; out = {}
    for line in open(path):
        if not line.startswith('|'): continue
        c = [x.strip() for x in line.strip().strip('|').split('|')]
        if hdr is None:
            if c[0] == 'Instance': hdr = c
            continue
        if len(c) != len(hdr): continue
        raw = line.split('|')[1]; depth = (len(raw) - len(raw.lstrip())) // 2
        if c[0].startswith('('): continue
        st = st[:depth] + [c[0]]
        row = {}
        for k in COLS:
            i = next((j for j, h in enumerate(hdr) if h.startswith(k)), None)
            m = re.match(r'\d+', c[i].replace(',', '')) if i is not None else None
            row[k] = int(m.group()) if m else 0
        out.setdefault(c[0], []).append(('/'.join(st), row))
    return out

def pick(rows, inst):
    r = rows.get(inst)
    if not r: return None
    return sorted(r, key=lambda x: (-x[1]['Total LUTs'], x[0]))[0][1]   # largest instance of that name, deterministic

ap = argparse.ArgumentParser()
ap.add_argument('--ctx', required=True); ap.add_argument('--ctx-label', default='in-context (whole-tile OOC, same RTL)')
ap.add_argument('--ref'); ap.add_argument('--ref-label', default='older full-design build')
ap.add_argument('--ooc', action='append', default=[], help='MODULE=GLOB of result.json, one per module')
ap.add_argument('--map', action='append', default=[], help='MODULE=instance name in the hierarchy')
a = ap.parse_args()
ctx = parse(a.ctx); ref = parse(a.ref) if a.ref else None
inst = dict(m.split('=', 1) for m in a.map); ooc = dict(m.split('=', 1) for m in a.ooc)
print('| module | column | module-OOC | %s | OOC vs ctx %% |' % a.ctx_label + (' %s | OOC vs ref %% |' % a.ref_label if ref else ''))
print('|---|---|---|---|---|' + ('---|---|' if ref else ''))
for mod in sorted(ooc):
    fs = sorted(glob.glob(ooc[mod]))
    if not fs: print('| %s | (no result) |' % mod); continue
    o = json.load(open(fs[0]))['total']; c = pick(ctx, inst.get(mod, mod)); r = pick(ref, inst.get(mod, mod)) if ref else None
    for k in COLS:
        ov = o.get(KEY[k], 0); cv = c[k] if c else None
        pc = '%+.1f' % (100.0 * (ov - cv) / cv) if cv else 'n/a'
        line = '| %s | %s | %d | %s | %s |' % (mod, k, ov, cv if cv is not None else 'n/a', pc)
        if ref:
            rv = r[k] if r else None
            line += ' %s | %s |' % (rv if rv is not None else 'n/a', '%+.1f' % (100.0 * (ov - rv) / rv) if rv else 'n/a')
        print(line)
