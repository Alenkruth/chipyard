#!/usr/bin/env python3
"""Designed whole-tile Baseline configurations for refitting/validating the configuration area model (owner 2026-10-31,
"16 tiles"). Deterministic (fixed seed). Draws each knob uniformly from the fuzzer's option list (options.json from
elaborate.sh --dump-options), skipping values the module sweep does not cover (fetch buffer 8), configs equal to an
already measured tile, and duplicates. 6 at width 1, 6 at width 2, 4 'small' at width 4 (lower half of each option list).
usage: design_tiles.py --options options.json --area fuzzer_area.json --exclude intile-module-table.json --out list.txt"""
import argparse, json, random
ap = argparse.ArgumentParser()
for k in ('options', 'area', 'exclude', 'out'): ap.add_argument('--' + k, required=True)
ap.add_argument('--seed', type=int, default=20261031)
a = ap.parse_args()
O = json.load(open(a.options))
KN = [('rob', 'robEntryOptions'), ('fb', 'fetchBufferEntryOptions'), ('ftq', 'ftQueueEntryOptions'), ('ras', 'rasEntryCountOptions'),
      ('iprf', 'pregFileSizeOptions'), ('fprf', 'fpPregFileSizeOptions'), ('iq_int', 'issueQueueEntryOptions'),
      ('iq_mem', 'issueQueueEntryOptions'), ('iq_fp', 'issueQueueEntryOptions'), ('ldq', 'ldQueueEntryOptions'),
      ('stq', 'stQueueEntryOptions'), ('btb_sets', 'btbSetOptions'), ('btb_ways', 'btbWayOptions'), ('tage', 'tagetableCountOptions'),
      ('ic_sets', 'icacheSetOptions'), ('ic_ways', 'cacheWayOptions'), ('dc_sets', 'dcacheSetOptions'), ('dc_ways', 'cacheWayOptions')]
SKIP = {'fb': {8}}   # fetch buffer 8 is not in the module sweep
opts = {k: sorted(set(O[o]) - SKIP.get(k, set()), reverse=True) for k, o in KN}
seen = {tuple(sorted(list(t['cfg'].items()) + [('width', t['width'])])) for t in json.load(open(a.exclude))}
rng = random.Random(a.seed)
rows = []
for w, n, small in ((1, 6, False), (2, 6, False), (4, 4, True)):
    got = 0
    while got < n:
        cfg = {}
        for k, _ in KN:
            vals = opts[k]
            if small: vals = vals[len(vals) // 2:]          # lower half (lists are descending)
            cfg[k] = rng.choice(vals)
        key = tuple(sorted(list(cfg.items()) + [('width', w)]))
        if key in seen: continue
        seen.add(key); got += 1
        rows.append('base ' + ','.join('%s=%d' % (k, cfg[k]) for k, _ in KN) + ',width=%d' % w)
open(a.out, 'w').write('\n'.join(rows) + '\n')
print('\n'.join(rows))
