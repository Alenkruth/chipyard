#!/usr/bin/env python3
"""build_cg_inputs.py -- write <W_BASE>/<ID>/cg_inputs.json (the R_G(cfg) resources per group) for cal_fit.py.

Spec: claude-artifacts/ooc-synthesis/plans/power-calibration-plan.md section 1.5 ("C_G source, chosen"):

    R_G(cfg) = sum_{m in G} n_m OOC_m(cfg, w)  +  F_G(w)

  * knob part: boom_ift.descriptor.area_breakdown(cfg)['modules'][m]['value'] (= n_m x OOC point, the fuzzer's
    own lookup incl. its instance counts and Lsu / D$ / fetch-buffer substitution rules), imported as a library
    from sims/verilator/base-attacks/fuzzer.  AREA_COLS are (total_luts, ffs, ramb36, ramb18); every knob module
    has dsp = 0 in fuzzer_area.json, so the knob part carries dsp = 0.
  * fixed part F_G(w): in-tile resources of group G minus the in-tile knob modules of G, from util_hier.rpt
    (report_utilization -hierarchical) of the full-size Baseline tile of width w.  'rest' = BoomTile minus the
    five groups (no knob modules), the same definition cal_fit.parse_util uses.
        w4: <W_BASE>/W4/ooc/points/Tile/base/<W4 PT_PARAMS>/util_hier.rpt   (P2 output; was the pilot, pre-E6)
        w2: <W_BASE>/W2/ooc/points/Tile/base/<W2 PT_PARAMS>/util_hier.rpt   (P2 output)
        w1: <W_BASE>/W1/ooc/points/Tile/base/<W1 PT_PARAMS>/util_hier.rpt   (P2 output)
    MIN4 uses F(4), HELD2 uses F(2).
  * group membership (plan 1.5): core = Rob, RegfileInt, RegfileFp, FreelistInt, FreelistFp, IssueInt,
    IssueMem, IssueFp; lsu = Lsu; frontend = FetchBuffer, FetchTargetQueue, BoomRAS, BTB (x2), Tage (x2),
    ICache; dcache = DCache; ptw, rest = none.  In-tile instance paths from tools/ooc-synth/scala/points.py REG
    (BTB / Tage: both banks frontend.bpd.banked_predictors_{0,1}).

Output format (cal_fit.parse_cg, "modules" + "fixed" form; summed per group by cal_fit):
    {"modules": {M: {"group", "lut", "ff", "ramb36", "ramb18", "dsp", "n", "key"}},
     "fixed":   {G: {"lut", "ff", "ramb36", "ramb18", "dsp"}},  "per_group": {...}, "meta": {...}}
Sorted keys, integers, no timestamps: a rerun is byte-identical.

Check: sum over all six groups of F_G(w) (= tile minus in-tile knob modules) vs rest(w) of
claude-artifacts/ooc-synthesis/rest-of-tile.json, LUT and FF within 5% (else FLAG); BRAM printed.

Usage:  python3 -I build_cg_inputs.py [ID ...]        (default: W4 W2 W1 MIN4 HELD2)
        options: --w-base DIR (default CAL_W_BASE of cal_cfgs.env)  --dry (print only, write nothing)
Exit: 0 all requested ids written; 3 some id skipped as missing (its F source is absent, nothing written for
it); 2 bad input (unknown id, parse error, knob mismatch, fuzzer lookup missing a value).
Reads only; no Vivado, sims or builds.
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
FUZZER = os.path.join(REPO, 'sims', 'verilator', 'base-attacks', 'fuzzer')
CFGS_ENV = os.path.join(HERE, 'cal_cfgs.env')
REST_JSON = os.path.join(REPO, 'claude-artifacts', 'ooc-synthesis', 'rest-of-tile.json')
PILOT_TILE_BASE = '/data/akrish/scratch/power-pilot/ooc/points/Tile/base'

GROUPS = ['core', 'lsu', 'frontend', 'dcache', 'ptw']
ALLG = GROUPS + ['rest']
RES = ['lut', 'ff', 'ramb36', 'ramb18', 'dsp']
IDS = ['W4', 'W2', 'W1', 'MIN4', 'HELD2', 'HELD3']
F_SOURCE_ID = {4: 'W4', 2: 'W2', 1: 'W1'}      # full-size Baseline tile per width
TOL = 0.05

# module -> (group, in-tile instance paths under BoomTile); paths from tools/ooc-synth/scala/points.py REG
KNOB = {
    'Rob':              ('core', ['core.rob']),
    'RegfileInt':       ('core', ['core.iregfile']),
    'RegfileFp':        ('core', ['core.FpPipeline.fregfile']),
    'FreelistInt':      ('core', ['core.rename_stage.freelist']),
    'FreelistFp':       ('core', ['core.fp_rename_stage.freelist']),
    'IssueInt':         ('core', ['core.int_issue_unit']),
    'IssueMem':         ('core', ['core.mem_issue_unit']),
    'IssueFp':          ('core', ['core.FpPipeline.fp_issue_unit']),
    'Lsu':              ('lsu', ['lsu']),
    'FetchBuffer':      ('frontend', ['frontend.fb']),
    'FetchTargetQueue': ('frontend', ['frontend.ftq']),
    'BoomRAS':          ('frontend', ['frontend.ras']),
    'BTB':              ('frontend', ['frontend.bpd.banked_predictors_0.btb', 'frontend.bpd.banked_predictors_1.btb']),
    'Tage':             ('frontend', ['frontend.bpd.banked_predictors_0.tage', 'frontend.bpd.banked_predictors_1.tage']),
    'ICache':           ('frontend', ['frontend.icache']),
    'DCache':           ('dcache', ['dcache']),
}


class InputError(Exception):
    pass


def sha1(path):
    with open(path, 'rb') as f:
        return hashlib.sha1(f.read()).hexdigest()


def read_cfgs():
    env, rows = {}, {}
    for line in open(CFGS_ENV):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line.split()[0] and line.split('=', 1)[0].isidentifier():
            k, v = line.split('=', 1)
            env[k] = v
            continue
        f = line.split()
        if len(f) == 3:
            rows[f[0]] = {'config': f[1], 'pt': f[2]}
    return env, rows


def pt_dict(pt):
    return {k: int(v) for k, v in (x.split('=', 1) for x in pt.split(','))}


def fuzzer_cfg(p):
    """Translate the OOC point knob string to the fuzzer's knob dict; refuse what the fuzzer cannot express."""
    import boom_ift.csr_map as cm
    if not (p['iq_int'] == p['iq_mem'] == p['iq_fp']):
        raise InputError(f'iq_int/iq_mem/iq_fp differ ({p}); the fuzzer has one numIssueSlots knob')
    ints, fps = cm._OPTS_PREG_INT[0], cm._OPTS_PREG_FP[0]
    if p['iprf'] not in ints or fps[ints.index(p['iprf'])] != p['fprf']:
        raise InputError(f'iprf={p["iprf"]} fprf={p["fprf"]}: not the fuzzer pairing (0xbc7 index)')
    btb = cm.btb_entries(p['btb_sets'], p['btb_ways'])
    if btb is None:
        raise InputError(f'btb {p["btb_sets"]}x{p["btb_ways"]} is not a btbEntries geometry')
    return {'numRobEntries': p['rob'], 'fetchBufferEntries': p['fb'], 'numFtqEntries': p['ftq'],
            'rasEntryCount': p['ras'], 'pregFileSize': p['iprf'], 'numIssueSlots': p['iq_int'],
            'numLdqEntries': p['ldq'], 'numStqEntries': p['stq'], 'btbEntries': btb,
            'tagetableCount': p['tage'], 'icacheNSets': p['ic_sets'], 'icacheNWays': p['ic_ways'],
            'dcacheNSets': p['dc_sets'], 'dcacheNWays': p['dc_ways'], 'coreWidth': p['width']}


def parse_util_tree(path):
    """{dotted instance path under BoomTile: {res}}; 'BoomTile' = the tile row.  Indent 2 per level."""
    cols, out, stack = None, {}, []
    for line in open(path):
        if not line.startswith('|'):
            continue
        cells = line.rstrip('\n').strip('|').split('|')
        c = [x.strip() for x in cells]
        if c[0] == 'Instance':
            cols = {n: i for i, n in enumerate(c)}
            continue
        if cols is None or c[0].startswith('('):
            continue
        ind = len(cells[0]) - len(cells[0].lstrip(' '))
        depth = (ind - 1) // 2          # BoomTile has indent 1 -> depth 0
        try:
            v = {'lut': int(c[cols['Total LUTs']]), 'ff': int(c[cols['FFs']]), 'ramb36': int(c[cols['RAMB36']]),
                 'ramb18': int(c[cols['RAMB18']]), 'dsp': int(c[cols['DSP Blocks']])}
        except (KeyError, ValueError, IndexError):
            raise InputError(f'{path}: utilization columns not parsed in line {line.strip()[:80]}')
        del stack[depth:]
        stack.append(c[0])
        key = 'BoomTile' if depth == 0 else '.'.join(stack[1:])
        if stack[0] != 'BoomTile':
            raise InputError(f'{path}: top instance is {stack[0]}, not BoomTile')
        out.setdefault(key, v)
    if 'BoomTile' not in out or any(g not in out for g in GROUPS):
        raise InputError(f'{path}: BoomTile / depth-2 groups not found')
    return out


def fixed_part(util_path):
    t = parse_util_tree(util_path)
    top = t['BoomTile']
    grp = {g: dict(t[g]) for g in GROUPS}
    grp['rest'] = {r: top[r] - sum(t[g][r] for g in GROUPS) for r in RES}
    intile = {}
    for m, (g, paths) in sorted(KNOB.items()):
        s = {r: 0 for r in RES}
        for p in paths:
            if p not in t:
                raise InputError(f'{util_path}: knob instance {p} ({m}) not found')
            for r in RES:
                s[r] += t[p][r]
        intile[m] = s
        for r in RES:
            grp[g][r] -= s[r]
    for g in GROUPS:
        for r in RES:
            if grp[g][r] < 0:
                raise InputError(f'{util_path}: F_{g} {r} = {grp[g][r]} < 0')
    # rest may be slightly negative: Vivado's hierarchical counts of the five groups can sum above the BoomTile
    # row (w4 pilot: LUT 471,243 vs 471,121).  Kept as is (same value cal_fit.parse_util derives); a rest with
    # C <= 0 is skipped in cal_fit.fit_a (a_rest = 0).  Reported as WARN.
    return grp, intile, top


def rest_check(F, w):
    rest = json.load(open(REST_JSON))['rest'][str(w)]
    ref = {'lut': rest['total_luts'], 'ff': rest['ffs'], 'ramb36': rest['ramb36'], 'ramb18': rest['ramb18']}
    out = {}
    for r in ['lut', 'ff', 'ramb36', 'ramb18']:
        s = sum(F[g][r] for g in ALLG)
        rel = (s - ref[r]) / ref[r] if ref[r] else None
        out[r] = {'sum_F': s, 'rest_json': ref[r], 'rel': None if rel is None else round(rel, 6),
                  'ok': None if r.startswith('ramb') else (rel is not None and abs(rel) <= TOL)}
    return out


def build(cid, rows, wbase):
    p = pt_dict(rows[cid]['pt'])
    w = p['width']
    sid = F_SOURCE_ID[w]
    spt = rows[sid]['pt']
    # every width from its own P2 output (w4 no longer reuses the pilot checkpoint: E6, boom parameters.scala
    # 1c3b5339, changed the reconf-off w4 RTL, so the pre-E6 pilot netlist is not the W4 tile)
    util = os.path.join(wbase, sid, 'ooc', 'points', 'Tile', 'base', spt, 'util_hier.rpt')
    if not os.path.isfile(util):
        return None, f'missing F({w}) source {util} (P2 of {sid} not done)'
    sys.path.insert(0, FUZZER)
    import boom_ift.descriptor as D
    cfg = fuzzer_cfg(p)
    ab = D.area_breakdown(cfg)
    if ab['missing']:
        raise InputError(f'{cid}: fuzzer lookup has no value for {ab["missing"]}')
    if set(ab['modules']) != set(KNOB):
        raise InputError(f'{cid}: fuzzer modules {sorted(ab["modules"])} != plan 1.5 groups {sorted(KNOB)}')
    F, intile, top = fixed_part(util)
    mods = {}
    for m, (g, _paths) in sorted(KNOB.items()):
        e = ab['modules'][m]
        v = dict(zip(['lut', 'ff', 'ramb36', 'ramb18'], (int(x) for x in e['value'])))
        v.update({'dsp': 0, 'group': g, 'n': e['n'], 'key': e['key'], 'intile_full_size': intile[m]})
        mods[m] = v
    per = {g: {'knob': {r: sum(mods[m][r] for m in mods if mods[m]['group'] == g) for r in RES},
               'fixed': F[g]} for g in ALLG}
    for g in ALLG:
        per[g]['R'] = {r: per[g]['knob'][r] + per[g]['fixed'][r] for r in RES}
    chk = rest_check(F, w)
    tot = {r: sum(per[g]['R'][r] for g in ALLG) for r in ['lut', 'ff', 'ramb36', 'ramb18']}
    meta = {'id': cid, 'config': rows[cid]['config'], 'pt_params': rows[cid]['pt'], 'width': w,
            'fixed_source_id': sid, 'util_hier': util, 'util_hier_sha1': sha1(util),
            'rest_of_tile_json_sha1': sha1(REST_JSON), 'fuzzer_area_model': D.AREA_MODEL_TAG,
            'fuzzer_cfg': cfg, 'fuzzer_substituted': list(ab['substituted']),
            'fuzzer_area_total': {'lut': ab['lut'], 'ff': ab['ff'], 'ramb36': ab['ramb36'], 'ramb18': ab['ramb18']},
            'R_total': tot, 'tile_full_size_intile': top, 'rest_check_5pct': chk,
            'script': 'tools/ooc-synth/power/build_cg_inputs.py', 'script_sha1': sha1(os.path.abspath(__file__)),
            'note': 'knob dsp = 0 (fuzzer_area.json has dsp 0 for every knob module); rest = BoomTile - 5 groups'}
    # cal_fit.parse_cg reads only 'modules' (group + RES) and 'fixed'; never write a top-level 'groups' key here
    return {'modules': mods, 'fixed': F, 'per_group': per, 'meta': meta}, None


def show(cid, d):
    print(f'== {cid}  width={d["meta"]["width"]}  F from {d["meta"]["fixed_source_id"]}  '
          f'subst={d["meta"]["fuzzer_substituted"] or "none"}')
    print(f'   {"group":<9}{"part":<6}' + ''.join(f'{r:>10}' for r in RES))
    for g in ALLG:
        for part in ['knob', 'fixed', 'R']:
            print(f'   {g:<9}{part:<6}' + ''.join(f'{d["per_group"][g][part][r]:>10}' for r in RES))
    t = d['meta']['R_total']
    print(f'   R total: lut {t["lut"]}  ff {t["ff"]}  ramb36 {t["ramb36"]}  ramb18 {t["ramb18"]}  '
          f'(fuzzer area_breakdown total lut {d["meta"]["fuzzer_area_total"]["lut"]} ff {d["meta"]["fuzzer_area_total"]["ff"]})')
    for r in RES:
        if d['fixed']['rest'][r] < 0:
            print(f'   WARN F_rest {r} = {d["fixed"]["rest"][r]} < 0 (groups sum above the BoomTile row)')
    for r, c in d['meta']['rest_check_5pct'].items():
        st = 'info' if c['ok'] is None else ('OK' if c['ok'] else 'FLAG')
        rel = 'n/a' if c['rel'] is None else f'{100 * c["rel"]:+.2f}%'
        print(f'   check sum_G F_G {r}: {c["sum_F"]} vs rest-of-tile {c["rest_json"]} ({rel}) {st}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('ids', nargs='*', default=IDS)
    ap.add_argument('--w-base', default=None)
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    env, rows = read_cfgs()
    wbase = a.w_base or env.get('CAL_W_BASE', '/data/akrish/scratch/power-cal')
    rc = 0
    for cid in a.ids:
        try:
            if cid not in rows or cid not in IDS:
                raise InputError(f'unknown id {cid}; known {IDS}')
            d, why = build(cid, rows, wbase)
        except (InputError, ValueError, OSError) as e:
            print(f'ERROR {cid}: {e}', file=sys.stderr)
            return 2
        if d is None:
            print(f'MISSING {cid}: {why}; nothing written for {cid}')
            rc = 3
            continue
        show(cid, d)
        out = os.path.join(wbase, cid, 'cg_inputs.json')
        if a.dry:
            print(f'   (dry) would write {out}')
            continue
        os.makedirs(os.path.dirname(out), exist_ok=True)
        txt = json.dumps(d, indent=1, sort_keys=True) + '\n'
        tmp = out + '.tmp'
        with open(tmp, 'w') as f:
            f.write(txt)
        os.replace(tmp, out)
        print(f'   wrote {out} sha1 {sha1(out)}')
    return rc


if __name__ == '__main__':
    sys.exit(main())
