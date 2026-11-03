#!/usr/bin/env python3
"""cal_fit.py - fit and check the calibrated power model (power-calibration-plan.md section 1.5).

MODEL (binding text: claude-artifacts/ooc-synthesis/plans/power-calibration-plan.md 1.5)
  Groups G = core, lsu, frontend, dcache, ptw (depth-2 children of BoomTile) + rest (BoomTile minus their sum).
  C_G(cfg) = kLUT*LUT_G + kFF*FF_G + kBRAM*(RAMB36_G + RAMB18_G/2) + kDSP*DSP_G
     LUT_G..DSP_G = R_G(cfg) from <ID>/cg_inputs.json (fuzzer OOC lookup of the knob modules + fixed in-tile
     part of G at the config's width; assembled by the caller, e.g. via boom_ift.descriptor.area_breakdown).
     kLUT, kFF, kBRAM: non-negative least squares over (calibration configs x 5 groups) rows of IN-TILE
     resources (<ID>/util_hier.rpt) vs VECTORLESS group dynamic power (<ID>/vectorless.rpt) minus kDSP*DSP_G.
     kDSP = DSP class power / DSP count of the On-Chip Components table of the first calibration vectorless report.
     Fallback (any row with vectorless power > 0.1 W missing by > 15 %): per-group kappa_G on LUT only
     (kappa_G = sum_c P_G,c / sum_c LUT_G,c); the 'rest' group uses the pooled LUT kappa. Reported as 'fallback'.
  a_G = mean over the 4 calibration configs of Pact_G,c / C_G(c)   (mean of ratios; spread = max/min, > 1.3 flagged)
  P_hat(cfg) = sum_G a_G C_G(cfg)     (dynamic, device static excluded)
  Pact_G = group dynamic power of <ID>/power_<WL>.rpt (activity-based report_power -hierarchical).
  Held-out error e = (P_hat - P_act)/P_act on the BoomTile dynamic total of HELD2.
  Owner decisions (RULES C "Power calibration decisions"): workload DHRYSTONE ONLY; ADOPT iff |e| <= 10 %.
  Reported, not gating: per-group error per config, leave-one-config-out (refit kappa and a_G on the other 3
  calibration configs, predict the left-out one), area-only proxy P_hat = a_tot * sum_G C_G (one factor), and
  whether predicted order of the 5 totals equals measured order.

INPUT LAYOUT   --root ROOT  (ids W4 W2 W1 MIN4 are calibration, HELD2 is held out)
  ROOT/<ID>/vectorless.rpt   report_power -hierarchical [-hierarchical_depth >= 2], no activity file
  ROOT/<ID>/power_<WL>.rpt   same command with the SAIF activity (WL default 'dhry')
  ROOT/<ID>/util_hier.rpt    report_utilization -hierarchical (needed for the 4 calibration ids; used for kappa)
  ROOT/<ID>/cg_inputs.json   {"groups": {"core": {"lut":..,"ff":..,"ramb36":..,"ramb18":..,"dsp":..}, ... }}
                             or {"modules": {"Rob": {"group":"core","lut":..,...}, ...},
                                 "fixed": {"core": {...}, ...}}   (summed per group; missing keys = 0)
USAGE
  cal_fit.py --root ROOT --out OUTDIR [--workload dhry] [--cal W4,W2,W1,MIN4] [--held HELD2]
  cal_fit.py --selftest SCRATCHDIR [--pilot-activity RPT --pilot-vectorless RPT --pilot-util RPT]
OUTPUT  OUTDIR/power-cal.json (sorted keys, floats rounded to 9 decimals, no timestamps) and
        OUTDIR/power-cal.md. Deterministic: same inputs -> byte-identical outputs. Exit 0 always on a clean fit
        (verdict is printed: ADOPT or REJECT, last line); exit 2 on missing/malformed input.
"""
import argparse
import hashlib
import itertools
import json
import os
import re
import sys

import numpy as np

GROUPS = ['core', 'lsu', 'frontend', 'dcache', 'ptw']
ALLG = GROUPS + ['rest']
RES = ['lut', 'ff', 'ramb36', 'ramb18', 'dsp']
THRESH = 0.10
SPREAD_FLAG = 1.3


class InputError(Exception):
    pass


# ---------------------------------------------------------------- parsing
def _first_cell_indent(line):
    body = line[1:]
    return len(body) - len(body.lstrip(' '))


def parse_power(path):
    """Return {'total': BoomTile dynamic W, 'groups': {G: W} (rest derived), 'dsp_w', 'dsp_n'}."""
    sec = False
    top = None
    grp = {}
    dsp_w = dsp_n = None
    try:
        lines = open(path).read().split('\n')
    except OSError as e:
        raise InputError(f'cannot read {path}: {e}')
    for line in lines:
        if line.startswith('| DSPs') or line.startswith('|   DSP'):
            c = [x.strip() for x in line.strip('|').split('|')]
            try:
                dsp_w, dsp_n = float(c[1]), float(c[2])
            except (ValueError, IndexError):
                pass
        if re.match(r'^3\.1 By Hierarchy', line):
            sec = True
        if not sec or not line.startswith('|'):
            continue
        c = [x.strip() for x in line.strip('|').split('|')]
        ind = _first_cell_indent(line)
        try:
            v = float(c[1])
        except (ValueError, IndexError):
            continue
        if c[0] == 'BoomTile' and ind == 1 and top is None:
            top = v
        elif ind == 3 and c[0] in GROUPS and c[0] not in grp:
            grp[c[0]] = v
    if top is None or any(g not in grp for g in GROUPS):
        raise InputError(f'{path}: BoomTile row or depth-2 groups {GROUPS} not found in "3.1 By Hierarchy" '
                         f'(need report_power -hierarchical, depth >= 2)')
    g = dict(grp)
    g['rest'] = top - sum(grp.values())
    return {'total': top, 'groups': g, 'dsp_w': dsp_w, 'dsp_n': dsp_n}


def parse_util(path):
    """Return {G: {lut, ff, ramb36, ramb18, dsp}} in-tile (rest = top - sum of the five groups)."""
    cols = None
    top = None
    grp = {}
    try:
        lines = open(path).read().split('\n')
    except OSError as e:
        raise InputError(f'cannot read {path}: {e}')
    for line in lines:
        if not line.startswith('|'):
            continue
        c = [x.strip() for x in line.strip('|').split('|')]
        if c[0] == 'Instance':
            cols = {n: i for i, n in enumerate(c)}
            continue
        if cols is None:
            continue
        ind = _first_cell_indent(line)

        def val():
            try:
                return {'lut': float(c[cols['Total LUTs']]), 'ff': float(c[cols['FFs']]),
                        'ramb36': float(c[cols['RAMB36']]), 'ramb18': float(c[cols['RAMB18']]),
                        'dsp': float(c[cols['DSP Blocks']])}
            except (KeyError, ValueError, IndexError):
                raise InputError(f'{path}: utilization columns not found')
        if c[0] == 'BoomTile' and ind == 1 and top is None:
            top = val()
        elif ind == 3 and c[0] in GROUPS and c[0] not in grp:
            grp[c[0]] = val()
    if top is None or any(g not in grp for g in GROUPS):
        raise InputError(f'{path}: BoomTile / depth-2 groups not found')
    out = {g: grp[g] for g in GROUPS}
    out['rest'] = {r: top[r] - sum(grp[g][r] for g in GROUPS) for r in RES}
    return out


def parse_cg(path):
    try:
        d = json.load(open(path))
    except (OSError, ValueError) as e:
        raise InputError(f'cannot read {path}: {e}')
    out = {g: {r: 0.0 for r in RES} for g in ALLG}
    if 'groups' in d:
        for g, v in d['groups'].items():
            if g not in out:
                raise InputError(f'{path}: unknown group {g}')
            for r in RES:
                out[g][r] += float(v.get(r, 0))
    else:
        for m, v in d.get('modules', {}).items():
            if v.get('group') not in out:
                raise InputError(f'{path}: module {m} has no valid group')
            for r in RES:
                out[v['group']][r] += float(v.get(r, 0))
        for g, v in d.get('fixed', {}).items():
            if g not in out:
                raise InputError(f'{path}: unknown group {g}')
            for r in RES:
                out[g][r] += float(v.get(r, 0))
    return out


def sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()[:8]


# ---------------------------------------------------------------- fit
def feat(r):
    """(LUT, FF, BRAM-equivalent) of a resource dict."""
    return [r['lut'], r['ff'], r['ramb36'] + r['ramb18'] / 2.0]


def nnls3(A, b):
    """Exact NNLS for <= 3 variables by enumerating active sets (deterministic, no scipy)."""
    A = np.asarray(A, float)
    b = np.asarray(b, float)
    n = A.shape[1]
    sc = np.abs(A).max(axis=0)
    sc[sc == 0] = 1.0
    As = A / sc
    best = None
    for k in range(1, n + 1):
        for S in itertools.combinations(range(n), k):
            x, *_ = np.linalg.lstsq(As[:, S], b, rcond=None)
            if (x < 0).any():
                continue
            full = np.zeros(n)
            full[list(S)] = x
            res = float(np.sum((As @ full - b) ** 2))
            if best is None or res < best[0] - 1e-18:
                best = (res, full)
    if best is None:
        best = (float(np.sum(b ** 2)), np.zeros(n))
    return best[1] / sc, float(np.linalg.cond(As))


def fit_kappa(cal, util, vl, kdsp):
    """Return dict(kind, k=[kLUT,kFF,kBRAM] or per-group, cond, rows)."""
    A, b, names = [], [], []
    for c in cal:
        for g in GROUPS:
            A.append(feat(util[c][g]))
            b.append(vl[c]['groups'][g] - kdsp * util[c][g]['dsp'])
            names.append((c, g))
    k, cond = nnls3(A, b)
    pred = np.array(A) @ k + np.array([kdsp * util[c][g]['dsp'] for c, g in names])
    meas = np.array(b) + np.array([kdsp * util[c][g]['dsp'] for c, g in names])
    bad = [(n, p, m) for n, p, m in zip(names, pred, meas) if m > 0.1 and abs(p - m) > 0.15 * m]
    if not bad:
        return {'kind': 'nnls', 'k': [float(x) for x in k], 'cond': cond, 'bad_rows': 0}
    kg = {}
    for g in GROUPS:
        lt = sum(util[c][g]['lut'] for c in cal)
        kg[g] = (sum(vl[c]['groups'][g] - kdsp * util[c][g]['dsp'] for c in cal) / lt) if lt else 0.0
    pooled = (sum(sum(vl[c]['groups'][g] - kdsp * util[c][g]['dsp'] for g in GROUPS) for c in cal) /
              max(1.0, sum(util[c][g]['lut'] for c in cal for g in GROUPS)))
    kg['rest'] = pooled
    return {'kind': 'fallback-lut-per-group', 'kg': kg, 'cond': cond, 'bad_rows': len(bad)}


def cap(kfit, kdsp, r, g):
    if kfit['kind'] == 'nnls':
        k = kfit['k']
        f = feat(r)
        return k[0] * f[0] + k[1] * f[1] + k[2] * f[2] + kdsp * r['dsp']
    return kfit['kg'][g] * r['lut'] + kdsp * r['dsp']


def fit_a(cal, kfit, kdsp, cg, act):
    a, sp = {}, {}
    for g in ALLG:
        ratios = []
        for c in cal:
            C = cap(kfit, kdsp, cg[c][g], g)
            if C > 0:
                ratios.append(act[c]['groups'][g] / C)
        if ratios:
            a[g] = sum(ratios) / len(ratios)
            sp[g] = (max(ratios) / min(ratios)) if min(ratios) > 0 else None
        else:
            a[g], sp[g] = 0.0, None
    return a, sp


def predict(a, kfit, kdsp, cgc):
    pg = {g: a[g] * cap(kfit, kdsp, cgc[g], g) for g in ALLG}
    return pg, sum(pg.values())


def area_only(cal, kfit, kdsp, cg, act):
    ratios = [act[c]['total'] / sum(cap(kfit, kdsp, cg[c][g], g) for g in ALLG) for c in cal]
    return sum(ratios) / len(ratios)


def rel(p, m):
    return (p - m) / m if m else None


def run(root, workload, cal, held):
    ids = cal + [held]
    vl, act, cg, util, shas = {}, {}, {}, {}, {}
    for i in ids:
        d = os.path.join(root, i)
        vl[i] = parse_power(os.path.join(d, 'vectorless.rpt'))
        act[i] = parse_power(os.path.join(d, f'power_{workload}.rpt'))
        cg[i] = parse_cg(os.path.join(d, 'cg_inputs.json'))
        files = ['vectorless.rpt', f'power_{workload}.rpt', 'cg_inputs.json']
        if i in cal:
            util[i] = parse_util(os.path.join(d, 'util_hier.rpt'))
            files.append('util_hier.rpt')
        shas[i] = {f: sha1(os.path.join(d, f)) for f in files}
    d0 = vl[cal[0]]
    if not d0['dsp_n']:
        raise InputError('DSP row of On-Chip Components not found in the first calibration vectorless report')
    kdsp = d0['dsp_w'] / d0['dsp_n']

    kfit = fit_kappa(cal, util, vl, kdsp)
    a, spread = fit_a(cal, kfit, kdsp, cg, act)
    kres = []
    for c in cal:
        for g in GROUPS:
            r = util[c][g]
            p = cap(kfit, kdsp, r, g) if kfit['kind'] == 'nnls' else None
            kres.append({'config': c, 'group': g, 'vectorless_W': vl[c]['groups'][g], 'fit_W': p})
    per = {}
    for i in ids:
        pg, tot = predict(a, kfit, kdsp, cg[i])
        per[i] = {'role': 'held-out' if i == held else 'calibration',
                  'measured_total_W': act[i]['total'], 'predicted_total_W': tot, 'error_total': rel(tot, act[i]['total']),
                  'groups': {g: {'measured_W': act[i]['groups'][g], 'predicted_W': pg[g],
                                 'error': rel(pg[g], act[i]['groups'][g]) if abs(act[i]['groups'][g]) > 1e-9 else None}
                             for g in ALLG}}
    atot = area_only(cal, kfit, kdsp, cg, act)
    for i in ids:
        pa = atot * sum(cap(kfit, kdsp, cg[i][g], g) for g in ALLG)
        per[i]['area_only_total_W'] = pa
        per[i]['area_only_error'] = rel(pa, act[i]['total'])
    loo = {}
    for c in cal:
        rest = [x for x in cal if x != c]
        kf = fit_kappa(rest, util, vl, kdsp)
        aa, _ = fit_a(rest, kf, kdsp, cg, act)
        _, t = predict(aa, kf, kdsp, cg[c])
        at = area_only(rest, kf, kdsp, cg, act) * sum(cap(kf, kdsp, cg[c][g], g) for g in ALLG)
        loo[c] = {'predicted_total_W': t, 'measured_total_W': act[c]['total'], 'error': rel(t, act[c]['total']),
                  'area_only_error': rel(at, act[c]['total']), 'kappa_kind': kf['kind']}
    meas_order = sorted(ids, key=lambda i: (act[i]['total'], i))
    pred_order = sorted(ids, key=lambda i: (per[i]['predicted_total_W'], i))
    e = per[held]['error_total']
    verdict = 'ADOPT' if (e is not None and abs(e) <= THRESH) else 'REJECT'
    out = {'workload': workload, 'calibration': cal, 'held_out': held, 'threshold': THRESH,
           'kappa': {'kind': kfit['kind'], 'condition_number': kfit['cond'], 'rows_over_15pct_miss': kfit['bad_rows'],
                     'kDSP_W_per_block': kdsp,
                     **({'kLUT': kfit['k'][0], 'kFF': kfit['k'][1], 'kBRAM': kfit['k'][2]} if kfit['kind'] == 'nnls'
                        else {'kLUT_per_group': kfit['kg']}),
                     'rows': kres},
           'a_G': a, 'a_G_spread_max_over_min': spread,
           'a_G_not_single_factor': sorted(g for g in ALLG if spread[g] is not None and spread[g] > SPREAD_FLAG),
           'configs': per, 'leave_one_out': loo, 'area_only_a_tot': atot,
           'order_measured': meas_order, 'order_predicted': pred_order, 'order_matches': meas_order == pred_order,
           'held_out_error_total': e, 'verdict': verdict, 'inputs_sha1_8': shas}
    return out


# ---------------------------------------------------------------- output
def rnd(o):
    if isinstance(o, float):
        return round(o, 9)
    if isinstance(o, dict):
        return {k: rnd(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [rnd(v) for v in o]
    return o


def pct(x):
    return 'n/a' if x is None else f'{100 * x:+.2f}%'


def markdown(r):
    L = [f'# Power calibration fit (workload {r["workload"]})', '',
         f'Verdict: **{r["verdict"]}** - held-out {r["held_out"]} total dynamic error {pct(r["held_out_error_total"])} '
         f'(threshold +-{100 * r["threshold"]:.0f}%).', '',
         f'kappa fit: {r["kappa"]["kind"]}, condition number {r["kappa"]["condition_number"]:.3g}, '
         f'rows over 15% miss: {r["kappa"]["rows_over_15pct_miss"]}; kDSP {r["kappa"]["kDSP_W_per_block"]:.6f} W/block.', '']
    if 'kLUT' in r['kappa']:
        L += [f'kLUT {r["kappa"]["kLUT"]:.4e}, kFF {r["kappa"]["kFF"]:.4e}, kBRAM {r["kappa"]["kBRAM"]:.4e} W per unit.', '']
    L += ['## Activity factors', '', '| group | a_G | spread max/min | flag |', '|---|---|---|---|']
    for g in ALLG:
        s = r['a_G_spread_max_over_min'][g]
        L.append(f'| {g} | {r["a_G"][g]:.4f} | {"n/a" if s is None else f"{s:.3f}"} | '
                 f'{"not single-factor" if g in r["a_G_not_single_factor"] else ""} |')
    L += ['', '## Per config, per group (W)', '', '| config | role | group | measured | predicted | error |', '|---|---|---|---|---|---|']
    for c, v in r['configs'].items():
        for g in ALLG:
            x = v['groups'][g]
            L.append(f'| {c} | {v["role"]} | {g} | {x["measured_W"]:.4f} | {x["predicted_W"]:.4f} | {pct(x["error"])} |')
        L.append(f'| {c} | {v["role"]} | **total** | {v["measured_total_W"]:.4f} | {v["predicted_total_W"]:.4f} | '
                 f'{pct(v["error_total"])} |')
    L += ['', f'## Held-out total ({r["held_out"]}, workload {r["workload"]})', '',
          f'error {pct(r["held_out_error_total"])} -> **{r["verdict"]}**', '',
          '## Leave-one-config-out (kappa and a_G refit on the other 3)', '',
          '| left out | measured | predicted | error | area-only error |', '|---|---|---|---|---|']
    for c, v in r['leave_one_out'].items():
        L.append(f'| {c} | {v["measured_total_W"]:.4f} | {v["predicted_total_W"]:.4f} | {pct(v["error"])} | '
                 f'{pct(v["area_only_error"])} |')
    L += ['', '## Area-only comparison (one factor a_tot x sum_G C_G)', '',
          f'a_tot {r["area_only_a_tot"]:.4f}', '', '| config | measured | area-only | error | per-group model error |',
          '|---|---|---|---|---|']
    for c, v in r['configs'].items():
        L.append(f'| {c} | {v["measured_total_W"]:.4f} | {v["area_only_total_W"]:.4f} | {pct(v["area_only_error"])} | '
                 f'{pct(v["error_total"])} |')
    L += ['', f'Order of totals (low to high) measured: {" < ".join(r["order_measured"])}; predicted: '
          f'{" < ".join(r["order_predicted"])}; match: {r["order_matches"]}.', '']
    return '\n'.join(L)


def write_outputs(r, outdir):
    os.makedirs(outdir, exist_ok=True)
    rr = rnd(r)
    with open(os.path.join(outdir, 'power-cal.json'), 'w') as f:
        json.dump(rr, f, indent=1, sort_keys=True)
        f.write('\n')
    with open(os.path.join(outdir, 'power-cal.md'), 'w') as f:
        f.write(markdown(rr))


# ---------------------------------------------------------------- self-test
def gen_power(path, total_groups, dsp_w, dsp_n):
    """Write a minimal report_power-style file (same table shape as Vivado's) for the parser."""
    top = sum(total_groups[g] for g in GROUPS) + total_groups.get('rest', 0.0)
    L = ['1.1 On-Chip Components', '',
         f'| DSPs                     | {dsp_w:9.3f} | {int(dsp_n):8d} | {12288:9d} | {0.5:15.2f} |', '',
         '3.1 By Hierarchy', '', '+---+---+', '| Name                   | Power (W) |', '+---+---+',
         f'| BoomTile               | {top:.12f} |']
    for g in GROUPS:
        L.append(f'|   {g:<20} | {total_groups[g]:.12f} |')
        L.append(f'|     {"sub":<18} | {total_groups[g] / 2:.12f} |')
    L.append('+---+---+')
    open(path, 'w').write('\n'.join(L) + '\n')


def gen_util(path, util):
    hdr = ['Instance', 'Module', 'Total LUTs', 'Logic LUTs', 'LUTRAMs', 'SRLs', 'FFs', 'RAMB36', 'RAMB18', 'URAM',
           'DSP Blocks']
    L = ['| ' + ' | '.join(hdr) + ' |']

    def row(name, ind, r):
        return ('| ' + ' ' * ind + name + ' | m | ' + ' | '.join(str(int(x)) for x in
                [r['lut'], r['lut'], 0, 0, r['ff'], r['ramb36'], r['ramb18'], 0, r['dsp']]) + ' |')
    topr = {k: sum(util[g][k] for g in ALLG) for k in RES}
    L.append(row('BoomTile', 0, topr))
    for g in GROUPS:
        L.append(row(g, 2, util[g]))
        L.append(row('(' + g + ')', 4, util[g]))
    open(path, 'w').write('\n'.join(L) + '\n')


def selftest(scratch, pilot_act, pilot_vl, pilot_util):
    os.makedirs(scratch, exist_ok=True)
    ok = True

    def check(name, cond, extra=''):
        nonlocal ok
        print(('PASS ' if cond else 'FAIL ') + name + (' ' + extra if extra else ''))
        ok = ok and cond

    # --- Test 1: real pilot reports parse (W4)
    if pilot_act and os.path.exists(pilot_act):
        pa, pv, pu = parse_power(pilot_act), parse_power(pilot_vl), parse_util(pilot_util)
        check('pilot activity total 3.564', abs(pa['total'] - 3.564) < 1e-9, f'{pa["total"]}')
        check('pilot activity groups sum', abs(sum(pa['groups'][g] for g in GROUPS) - 3.564) < 0.0015,
              str({g: pa['groups'][g] for g in ALLG}))
        check('pilot vectorless total 3.100', abs(pv['total'] - 3.100) < 1e-9)
        check('pilot util core LUT 329664', pu['core']['lut'] == 329664 and pu['core']['dsp'] == 61)
        check('pilot DSP row parsed', pv['dsp_n'] == 61 and pa['dsp_n'] == 61, f'{pv["dsp_w"]}/{pv["dsp_n"]}')
    # --- Test 2: synthetic model-exact data; fit must recover the known a_G exactly
    rng = np.random.RandomState(7)
    ktrue = np.array([1.1e-5, 2.3e-6, 4.0e-3])
    kdsp = 0.017 / 61
    atrue = {'core': 0.8, 'lsu': 1.3, 'frontend': 0.6, 'dcache': 1.1, 'ptw': 0.9, 'rest': 1.0}
    base = parse_util(pilot_util) if pilot_util and os.path.exists(pilot_util) else {
        'core': dict(lut=3e5, ff=8e4, ramb36=0, ramb18=0, dsp=61), 'lsu': dict(lut=7e4, ff=1.6e4, ramb36=0, ramb18=0, dsp=0),
        'frontend': dict(lut=5e4, ff=3e4, ramb36=16, ramb18=96, dsp=0), 'dcache': dict(lut=1.6e4, ff=9e3, ramb36=38, ramb18=0, dsp=0),
        'ptw': dict(lut=2.7e3, ff=1.6e3, ramb36=0, ramb18=2, dsp=0), 'rest': dict(lut=300, ff=100, ramb36=0, ramb18=0, dsp=0)}
    ids = ['W4', 'W2', 'W1', 'MIN4', 'HELD2']
    mix = {'W4': [1, 1, 1, 1, 1], 'W2': [.5, .55, .8, .5, .6], 'W1': [.25, .3, .7, .5, .6],
           'MIN4': [.3, .25, .3, .3, .8], 'HELD2': [.4, .4, .5, .45, .7]}
    root = os.path.join(scratch, 't2')
    for i in ids:
        util = {g: {r: float(round(base[g][r] * (mix[i][GROUPS.index(g)] if g in GROUPS else 1.0)))
                    if r != 'dsp' or g != 'core' else 61.0 for r in RES} for g in ALLG}
        d = os.path.join(root, i)
        os.makedirs(d, exist_ok=True)
        gen_util(os.path.join(d, 'util_hier.rpt'), util)
        # 'rest' in util is top - groups = 0 here; give cg_inputs the same resources
        vlp = {g: float(ktrue @ np.array(feat(util[g])) + kdsp * util[g]['dsp']) for g in GROUPS}
        gen_power(os.path.join(d, 'vectorless.rpt'), vlp, 0.017, 61)
        actp = {g: atrue[g] * float(ktrue @ np.array(feat(util[g])) + kdsp * util[g]['dsp']) for g in GROUPS}
        gen_power(os.path.join(d, 'power_dhry.rpt'), actp, 0.011, 61)
        json.dump({'groups': {g: util[g] for g in GROUPS}}, open(os.path.join(d, 'cg_inputs.json'), 'w'))
    r = run(root, 'dhry', ids[:4], 'HELD2')
    err = max(abs(r['a_G'][g] - atrue[g]) for g in GROUPS)
    check('exact recovery of a_G (max abs err < 1e-9)', err < 1e-9, f'{err:.2e}')
    check('exact kappa recovery', abs(r['kappa']['kLUT'] - ktrue[0]) / ktrue[0] < 1e-6 and
          abs(r['kappa']['kFF'] - ktrue[1]) / ktrue[1] < 1e-6 and abs(r['kappa']['kBRAM'] - ktrue[2]) / ktrue[2] < 1e-6,
          f'{r["kappa"]["kLUT"]:.3e} {r["kappa"]["kFF"]:.3e} {r["kappa"]["kBRAM"]:.3e}')
    check('held-out error ~0 and ADOPT', abs(r['held_out_error_total']) < 1e-9 and r['verdict'] == 'ADOPT')
    check('area-only differs from per-group (area-only error nonzero)',
          abs(r['configs']['HELD2']['area_only_error']) > 1e-3, pct(r['configs']['HELD2']['area_only_error']))
    # --- Test 3: perturbed truth -> REJECT path (held-out group factor off by 30 percent on activity)
    d = os.path.join(root, 'HELD2')
    u = parse_util(os.path.join(d, 'util_hier.rpt')) if False else None
    p = parse_power(os.path.join(d, 'power_dhry.rpt'))
    gen_power(os.path.join(d, 'power_dhry.rpt'), {g: p['groups'][g] * 1.3 for g in GROUPS}, 0.011, 61)
    r2 = run(root, 'dhry', ids[:4], 'HELD2')
    check('+30% held-out activity -> REJECT', r2['verdict'] == 'REJECT', pct(r2['held_out_error_total']))
    gen_power(os.path.join(d, 'power_dhry.rpt'), p['groups'], 0.011, 61)
    # --- Test 4: real pilot files as W4 (activity+vectorless+util), others scaled copies (ratios constant -> exact)
    if pilot_act and os.path.exists(pilot_act):
        root4 = os.path.join(scratch, 't4')
        pa, pv, pu = parse_power(pilot_act), parse_power(pilot_vl), parse_util(pilot_util)
        sc = {'W4': 1.0, 'W2': 0.62, 'W1': 0.41, 'MIN4': 0.3, 'HELD2': 0.5}
        for i in ids:
            d = os.path.join(root4, i)
            os.makedirs(d, exist_ok=True)
            s = sc[i]
            ut = {g: {r: pu[g][r] * s for r in RES} for g in ALLG}
            if i == 'W4':
                import shutil
                shutil.copy(pilot_act, os.path.join(d, 'power_dhry.rpt'))
                shutil.copy(pilot_vl, os.path.join(d, 'vectorless.rpt'))
                shutil.copy(pilot_util, os.path.join(d, 'util_hier.rpt'))
            else:
                gen_power(os.path.join(d, 'power_dhry.rpt'), {g: pa['groups'][g] * s for g in GROUPS}, 0.011, 61)
                gen_power(os.path.join(d, 'vectorless.rpt'), {g: pv['groups'][g] * s for g in GROUPS}, 0.017, 61)
                gen_util(os.path.join(d, 'util_hier.rpt'), ut)
            json.dump({'groups': {g: ut[g] for g in ALLG}}, open(os.path.join(d, 'cg_inputs.json'), 'w'))
        r4 = run(root4, 'dhry', ids[:4], 'HELD2')
        sp = max(v for v in r4['a_G_spread_max_over_min'].values() if v)
        check('real W4 + scaled copies: spread 1 and held-out error ~0', sp < 1 + 1e-6 and
              abs(r4['held_out_error_total']) < 1e-4, f'spread {sp:.6f} err {pct(r4["held_out_error_total"])} '
              f'kappa {r4["kappa"]["kind"]}')
    # --- Test 5: determinism
    o1, o2 = os.path.join(scratch, 'o1'), os.path.join(scratch, 'o2')
    write_outputs(run(root, 'dhry', ids[:4], 'HELD2'), o1)
    write_outputs(run(root, 'dhry', ids[:4], 'HELD2'), o2)
    same = all(open(os.path.join(o1, f), 'rb').read() == open(os.path.join(o2, f), 'rb').read()
               for f in ('power-cal.json', 'power-cal.md'))
    check('rerun byte-identical', same)
    print('SELFTEST ' + ('OK' if ok else 'FAILED'))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--root')
    ap.add_argument('--out')
    ap.add_argument('--workload', default='dhry')
    ap.add_argument('--cal', default='W4,W2,W1,MIN4')
    ap.add_argument('--held', default='HELD2')
    ap.add_argument('--selftest')
    ap.add_argument('--pilot-activity')
    ap.add_argument('--pilot-vectorless')
    ap.add_argument('--pilot-util')
    a = ap.parse_args()
    if a.selftest:
        return selftest(a.selftest, a.pilot_activity, a.pilot_vectorless, a.pilot_util)
    if not a.root or not a.out:
        ap.error('--root and --out are required')
    try:
        r = run(a.root, a.workload, a.cal.split(','), a.held)
    except InputError as e:
        print(f'cal_fit: input error: {e}', file=sys.stderr)
        return 2
    write_outputs(r, a.out)
    print(f'held-out {a.held} total error {pct(r["held_out_error_total"])} (threshold +-{100 * THRESH:.0f}%)')
    print(r['verdict'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
