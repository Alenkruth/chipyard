#!/usr/bin/env python3
"""In-context (inside the whole BoomTile) area of each knob-driven module, read from the hierarchical report of every
measured Baseline tile, next to the out-of-context sweep value of the same module at the same size. Deterministic.
usage: intile_table.py --area fuzzer_area.json --tiles 'GLOB_OF_TILE_POINT_DIRS' [...] --json out.json"""
import argparse, glob, json, os, re
ap = argparse.ArgumentParser()
ap.add_argument('--area', required=True); ap.add_argument('--tiles', nargs='+', required=True); ap.add_argument('--json', required=True)
a = ap.parse_args()
COLS = ['total_luts', 'ffs', 'ramb36', 'ramb18']
FULL = dict(rob=256, fb=64, ftq=32, ras=32, iprf=192, fprf=96, iq_int=32, iq_mem=32, iq_fp=32, ldq=40, stq=40,
            btb_sets=128, btb_ways=2, tage=7, ic_sets=64, ic_ways=8, dc_sets=128, dc_ways=8)
# group -> (in-context instance paths summed, sweep module, sweep keys)
GROUPS = [('Rob', ['core/rob'], 'Rob', {'entries': 'rob'}),
          ('FetchBuffer', ['frontend/fb'], 'FetchBuffer', {'entries': 'fb'}),
          ('FetchTargetQueue', ['frontend/ftq'], 'FetchTargetQueue', {'entries': 'ftq'}),
          ('BoomRAS', ['frontend/ras'], 'BoomRAS', {'entries': 'ras'}),
          ('RegfileInt', ['core/iregfile'], 'RegfileInt', {'pregs': 'iprf'}),
          ('FreelistInt', ['core/rename_stage/freelist'], 'FreelistInt', {'pregs': 'iprf'}),
          ('RegfileFp', ['core/FpPipeline/fregfile'], 'RegfileFp', {'pregs': 'fprf'}),
          ('FreelistFp', ['core/fp_rename_stage/freelist'], 'FreelistFp', {'pregs': 'fprf'}),
          ('IssueInt', ['core/int_issue_unit'], 'IssueInt', {'entries': 'iq_int'}),
          ('IssueMem', ['core/mem_issue_unit'], 'IssueMem', {'entries': 'iq_mem'}),
          ('IssueFp', ['core/FpPipeline/fp_issue_unit'], 'IssueFp', {'entries': 'iq_fp'}),
          ('BTB', ['frontend/bpd/banked_predictors_0/btb', 'frontend/bpd/banked_predictors_1/btb'], 'BTB', {'sets': 'btb_sets', 'ways': 'btb_ways'}),
          ('Tage', ['frontend/bpd/banked_predictors_0/tage', 'frontend/bpd/banked_predictors_1/tage'], 'Tage', {'tables': 'tage'}),
          ('ICache', ['frontend/icache'], 'ICache', {'sets': 'ic_sets', 'ways': 'ic_ways'}),
          ('DCache', ['dcache'], 'DCache', {'sets': 'dc_sets', 'ways': 'dc_ways'}),
          ('Lsu', ['lsu'], 'Lsu', None)]
NBANK = {'BTB': 2, 'Tage': 2}
A = json.load(open(a.area))
def parse_key(k):
    d = {}
    for kv in k.split(','):
        x, y = kv.split('='); d[x] = int(y) if y.isdigit() else y
    return d
T = {m: [(parse_key(k), v) for k, v in A[m].items()] for m in A}
def look(m, want, w):
    hit = [v for d, v in T[m] if 'repl' not in d and all(d.get(k) == x for k, x in want.items()) and d.get('width', w) == w]
    if not hit:  # module measured at width 4 only
        hit = [v for d, v in T[m] if 'repl' not in d and all(d.get(k) == x for k, x in want.items()) and d.get('width') == 4]
    return hit[-1] if hit else None
def ooc(g, m, keys, cfg, w):
    if m == 'Lsu':
        v = look('Lsu', {'ldq': cfg['ldq'], 'stq': cfg['stq']}, w)
        if v is None:
            a1, a2, f = look('Lsu', {'ldq': cfg['ldq'], 'stq': 40}, w), look('Lsu', {'ldq': 40, 'stq': cfg['stq']}, w), look('Lsu', {'ldq': 40, 'stq': 40}, w)
            v = {c: a1[c] + a2[c] - f[c] for c in COLS}
        return v
    v = look(m, {p: cfg[k] for p, k in keys.items()}, 4 if m == 'DCache' else w)
    return None if v is None else {c: NBANK.get(g, 1) * v[c] for c in COLS}
def rpt(f):
    hdr = None; st = []; rows = {}
    for l in open(f):
        if not l.startswith('|'): continue
        c = [x.strip() for x in l.strip().strip('|').split('|')]
        if hdr is None:
            if c[0] == 'Instance': hdr = c
            continue
        if len(c) != len(hdr): continue
        raw = l.split('|')[1]; d = (len(raw) - len(raw.lstrip())) // 2
        if c[0].startswith('('):
            if d == 0: rows[''] = c
            continue
        st = st[:d] + [c[0]]; rows['/'.join(st[1:])] = c
    num = lambda s: int(re.match(r'\d+', s.replace(',', '')).group())
    return {p: dict(module=c[1], total_luts=num(c[2]), ffs=num(c[6]), ramb36=num(c[7]), ramb18=num(c[8])) for p, c in rows.items()}
out = []
for pat in a.tiles:
    for d in sorted(glob.glob(pat)):
        r = json.load(open(os.path.join(d, 'result.json')))
        if r.get('status') != 'ok' or r.get('variant') != 'base': continue
        cfg = dict(FULL); w = 4; mode = None
        for kv in r['params'].split(','):
            k, v = kv.split('=')
            if k == 'width': w = int(v)
            elif k == 'width_mode': mode = v
            elif k in cfg: cfg[k] = int(v)
        if mode: continue
        R = rpt(os.path.join(d, 'util_hier.rpt'))
        tot = {c: r['total'][c] for c in COLS}
        groups = {}
        for g, paths, m, keys in GROUPS:
            ic = {c: sum(R[p][c] for p in paths if p in R) for c in COLS}
            missing = [p for p in paths if p not in R]
            groups[g] = dict(intile=ic, ooc=ooc(g, m, keys, cfg, w), missing=missing)
        rest = {c: tot[c] - sum(groups[g]['intile'][c] for g in groups) for c in COLS}
        top = {p: {c: R[p][c] for c in COLS} for p in R if p.count('/') <= 1 and p}
        out.append(dict(point=r['point'], width=w, cfg=cfg, total=tot, groups=groups, rest=rest, top=top))
json.dump(out, open(a.json, 'w'), indent=1, sort_keys=True)
print(len(out), 'tiles')
