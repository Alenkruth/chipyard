#!/usr/bin/env python3
"""Per-knob LUT/FF at the built (max) size from a hierarchical utilization report. Deterministic.
usage: knob_table.py --rpt util_hier.rpt --build "<text>" --csv out.csv --md out.md"""
import argparse, re
KNOBS = [  # knob, module, instance path suffix (relative to the tile), note
 ('ROB entries', 'Rob', 'core/rob', ''),
 ('Fetch buffer entries', 'FetchBuffer', 'frontend/fb', 'neighbour f4 (Queue1_FetchBundle) moves LUTs with fb: compare fb+f4'),
 ('Fetch buffer neighbour f4', 'Queue1_FetchBundle', 'frontend/f4', 'not a knob; listed because logic moves between fb and f4'),
 ('FTQ entries', 'FetchTargetQueue', 'frontend/ftq', ''),
 ('INT PRF size (regfile)', 'RegisterFileSynthesizable_1', 'core/iregfile', 'regfile built at max; 64-bit x 192'),
 ('FP PRF size (regfile)', 'RegisterFileSynthesizable', 'core/FpPipeline/fregfile', ''),
 ('INT freelist (PRF size)', 'RenameFreeList', 'core/rename_stage/freelist', ''),
 ('FP freelist (PRF size)', 'RenameFreeList_1', 'core/fp_rename_stage/freelist', ''),
 ('INT issue queue entries', 'IssueUnitCollapsing_2', 'core/int_issue_unit', ''),
 ('MEM issue queue entries', 'IssueUnitCollapsing_1', 'core/mem_issue_unit', ''),
 ('FP issue queue entries', 'IssueUnitCollapsing', 'core/FpPipeline/fp_issue_unit', ''),
 ('LDQ/STQ entries', 'LSU', 'lsu', 'LDQ and STQ are inside LSU; not separable in this report'),
 ('DCache sets/ways', 'BoomNonBlockingDCache', 'dcache', 'includes the 8 MSHRs; mshrs = BoomMSHRFile'),
 ('DCache MSHR file (part of dcache)', 'BoomMSHRFile', 'dcache/mshrs', 'LUTs move between MSHR instances (attribution)'),
 ('ICache sets/ways', 'ICache', 'frontend/icache', ''),
 ('BTB (bank 0)', 'BTBBranchPredictorBank', 'frontend/bpd/banked_predictors_0/btb', '2 banks per tile; this is bank 0'),
 ('BTB (bank 1)', 'BTBBranchPredictorBank', 'frontend/bpd/banked_predictors_1/btb', ''),
 ('TAGE (bank 0)', 'TageBranchPredictorBank', 'frontend/bpd/banked_predictors_0/tage', '2 banks per tile'),
 ('TAGE (bank 1)', 'TageBranchPredictorBank', 'frontend/bpd/banked_predictors_1/tage', ''),
 ('uBTB (bank 0, not a fuzzed knob)', 'FAMicroBTBBranchPredictorBank', 'frontend/bpd/banked_predictors_0/ubtb', ''),
 ('uBTB (bank 1, not a fuzzed knob)', 'FAMicroBTBBranchPredictorBank', 'frontend/bpd/banked_predictors_1/ubtb', ''),
 ('Branch predictor total', 'BranchPredictor', 'frontend/bpd', 'BTB + TAGE + uBTB + composer, both banks'),
 ('Rename stage (INT, incl. freelist)', 'RenameStage', 'core/rename_stage', 'width-dependent'),
 ('Core total', 'BoomCore', 'core', ''),
 ('Frontend total', 'BoomFrontend', 'frontend', ''),
 ('Tile total', 'BoomTile', '', ''),
]
ap = argparse.ArgumentParser(); ap.add_argument('--rpt', required=True); ap.add_argument('--build', required=True)
ap.add_argument('--csv', required=True); ap.add_argument('--md', required=True); a = ap.parse_args()
hdr = None; st = []; rows = {}
for l in open(a.rpt):
    if not l.startswith('|'): continue
    c = [x.strip() for x in l.strip().strip('|').split('|')]
    if hdr is None:
        if c[0] == 'Instance': hdr = c
        continue
    if len(c) != len(hdr) or c[0].startswith('('): continue
    raw = l.split('|')[1]; d = (len(raw) - len(raw.lstrip())) // 2
    st = st[:d] + [c[0]]
    num = lambda s: int(re.match(r'\d+', s.replace(',', '')).group())
    rows['/'.join(st[1:])] = (c[1], num(c[2]), num(c[6]), num(c[7]), num(c[8]), num(c[9]), num(c[10]) if len(c) > 10 else 0)
out = []
for knob, mod, path, note in KNOBS:
    r = rows.get(path)
    out.append((knob, path or 'BoomTile', mod, r, note))
import csv
with open(a.csv, 'w', newline='') as f:
    w = csv.writer(f, lineterminator='\n')
    w.writerow(['knob', 'instance', 'module', 'luts', 'ffs', 'ramb36', 'ramb18', 'uram', 'dsp', 'note'])
    for k, p, m, r, n in out:
        w.writerow([k, p, m] + (list(r[1:]) if r else [''] * 6) + [n])
with open(a.md, 'w') as f:
    f.write('# Area at the built (maximum) size, per BOOM knob\n\nBuild: %s\n\nLUT and FF from a hierarchical post-synthesis report. One size per knob only (the built size).\n\n| knob | module | LUTs | FFs | RAMB36 | RAMB18 | note |\n|---|---|---|---|---|---|---|\n' % a.build)
    for k, p, m, r, n in out:
        vals = (r[1], r[2], r[3], r[4]) if r else ('n/a',) * 4
        f.write('| %s | %s | %s | %s | %s | %s | %s |\n' % ((k, m) + vals + (n,)))
