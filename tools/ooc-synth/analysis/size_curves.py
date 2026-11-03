#!/usr/bin/env python3
"""Per-module area vs size from result.json files: sanity checks (clamp / pruning) and the fuzzer table. Deterministic.
usage: size_curves.py --work OOC_WORK --json fuzzer_area.json --md size_curves.md"""
import argparse, glob, json, os, collections
COLS = ['total_luts', 'ffs', 'ramb36', 'ramb18', 'uram', 'dsp']
ap = argparse.ArgumentParser(); ap.add_argument('--work', required=True); ap.add_argument('--json', required=True); ap.add_argument('--md', required=True)
a = ap.parse_args()
pts = []
for f in sorted(glob.glob(a.work + '/points/*/*/*/result.json')):
    r = json.load(open(f))
    if r.get('status') != 'ok': continue
    p = {}
    for kv in r['params'].split(','):
        k, v = kv.split('='); p[k] = int(v) if v.lstrip('-').isdigit() else v
    pts.append((r['module'], r['variant'], p, r['total']))
def key(p, drop): return tuple(sorted((k, v) for k, v in p.items() if k != drop))
viol = []; collapse = []
grp = collections.defaultdict(list)
for m, v, p, t in pts: grp[(m, v)].append((p, t))
for (m, v), L in sorted(grp.items()):
    sizekeys = [k for k in L[0][0] if k not in ('width', 'repl')]
    for p, t in L:
        for k in sizekeys:
            for q, u in L:
                if key(p, k) == key(q, k) and q[k] > p[k] and t['total_luts'] > 1.05 * u['total_luts'] + 50:
                    viol.append((m, v, p, q, t['total_luts'], u['total_luts']))
    mx = max(t['total_luts'] for _, t in L)
    for p, t in L:
        if mx > 2000 and t['total_luts'] < 0.05 * mx: collapse.append((m, v, p, t['total_luts'], mx))
out = collections.OrderedDict()
for m, v, p, t in pts:
    if v != 'base': continue
    k = ','.join('%s=%s' % kv for kv in sorted(p.items()))
    out.setdefault(m, collections.OrderedDict())[k] = {c: t[c] for c in COLS}
json.dump(out, open(a.json, 'w'), indent=1, sort_keys=True)
with open(a.md, 'w') as f:
    f.write('# Module area vs size (base variant, post-synthesis, one flow)\n\nPoints: %d ok. Monotonicity violations (smaller size has >5%% more LUTs than a larger size, others equal): %d. Collapsed points (<5%% of the module max): %d.\n\n' % (len(pts), len(viol), len(collapse)))
    for m, v, p, q, a1, b1 in viol: f.write('- VIOLATION %s/%s %s (%d LUT) vs %s (%d LUT)\n' % (m, v, p, a1, q, b1))
    for m, v, p, l, mx in collapse: f.write('- COLLAPSE %s/%s %s %d LUT vs module max %d\n' % (m, v, p, l, mx))
    f.write('\n')
    for m in sorted(out):
        f.write('## %s\n\n| params | LUTs | FFs | RAMB36 | RAMB18 | URAM | DSP |\n|---|---|---|---|---|---|---|\n' % m)
        for k, t in sorted(out[m].items()): f.write('| %s | %d | %d | %d | %d | %d | %d |\n' % ((k,) + tuple(t[c] for c in COLS)))
        f.write('\n')
print(len(pts), 'points;', len(viol), 'violations;', len(collapse), 'collapsed')
for m, v, p, q, a1, b1 in viol[:12]: print('VIOL', m, v, p, a1, 'vs', q, b1)
for m, v, p, l, mx in collapse[:12]: print('COLL', m, v, p, l, mx)
