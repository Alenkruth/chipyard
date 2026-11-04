#!/usr/bin/env python3
"""cal_fit_v2.py - power calibration model v2 (ONE reusable script; no per-run copies, no variants).

Binding text: claude-artifacts/ooc-synthesis/plans/power-cal-revision-plan.md (sha1 65b42143) sections 1.1-1.3.
cal_fit.py (v1) is kept unchanged; this file only reuses its parsers (parse_power, nnls3).

MODEL (fixed; the grid, groups, inputs and parsers below are the only v2 model)
  Groups G = core, lsu, frontend, dcache, ptw (rest is predicted 0, as in v1).
  C_G(c)   = K_G(c) + F_hat_G(c)             K_G = knob-module LUT sum from boom_ift.descriptor.area_breakdown
  F_hat_G  = F_G(w) * (1 - beta_G + beta_G * rho_G)   for core and frontend;  F_G(w) for the other groups
  rho_G    = K_G(c) / K_G(full size, width w)
  F_G(w)   = in-tile fixed LUT of G in the full-size Baseline tile of width w (build_cg_inputs.fixed_part of
             W4/W2/W1 util_hier.rpt); stored in params.json
  beta_G   = clip( sum_c (phi-1)(rho-1) / sum_c (rho-1)^2 , 0, 1 ) over the beta tiles {MIN4, HELD2}, with
             phi = F_G,meas(c) / F_G(w_c) from the tile's own util_hier.rpt (area only, no power used)
  C_dcache = LUT_dc + lambda_B * (RAMB36 + RAMB18/2)_dc,  lambda_B = k_B / k_L from NNLS of the VECTORLESS
             dcache power: vl_dc(c) = k_L LUT_dc(c) + k_B B36_dc(c)   (5 tiles; k_B = 0 gives lambda_B = 0)
  u(c)     = IPC / coreWidth, IPC = minstret / mcycle of the LAST counter lines of <ID>/logs/S1.log, read by
             boom_ift.bench_ipc.parse_log
  P_hat(c) = sum_G a_G * C_G(c) * [(1 - theta) + theta * u(c)]
  theta    = argmin over the grid {0, 0.001, ..., 1} of J = sum_c ((P_hat - P_act)/P_act)^2 over the totals
             (smallest theta on a tie); a_G(theta) = mean over the calibration tiles of P_act_G / (C_G g_theta)

MODES (all deterministic; same inputs -> byte-identical outputs; sorted keys, 12 significant digits, no clock)
  --calibrate        fit on W4 W2 W1 MIN4 HELD2 (all pinned inputs checked by sha1), write OUT/params.json
                     (params + in-sample errors + 5-fold LOO + diagnostics).  Its sha1 goes in the ledger.
  --predict ID       sealed prediction for config ID from its knob string (cal_cfgs.env row ID, or --pt STRING),
                     area_breakdown, u from ID/logs/S1.log and OUT/params.json only.  Writes OUT/predict-ID.json.
  --score ID         reads ID's measured power_dhry.rpt, the sealed OUT/predict-ID.json (its params sha1 and
                     S1.log sha1 must still match), writes OUT/power-cal-v2.{json,md}; verdict ADOPT iff
                     |e| <= 10 % on the BoomTile dynamic total.
  Do not run --calibrate or --score on anything but the real tree; the unit test
  (python3 -I tools/ooc-synth/power/test_cal_fit_v2.py) uses synthetic data only.
USAGE
  python3 -I cal_fit_v2.py --calibrate     [--root /data/akrish/scratch/power-cal] [--out ROOT/fit-v2] [--pins FILE]
  python3 -I cal_fit_v2.py --write-pins --pins FILE [--root ..]   (FILE must not exist; pins the CURRENT files of the 5 ids)
  python3 -I cal_fit_v2.py --predict HELD3 [--root ..] [--out ..] [--pt knob,string]
  python3 -I cal_fit_v2.py --score   HELD3 [--root ..] [--out ..]
PINS  --pins FILE = JSON {id: {relpath: sha1}} for the 5 calibration ids (same relpaths as PINNED); default = the built-in
      PINNED (old root).  --write-pins writes FILE from the current files under --root and refuses to overwrite.
      params.json records pins_file_sha1 (null for the built-in pins) and inputs_sha1 (the pins used); --predict/--score
      re-check the W4/W2/W1 inputs against that inputs_sha1 and, when --pins is given, its sha1 against pins_file_sha1.
ROOT layout per id: util_hier.rpt vectorless.rpt power_dhry.rpt cg_inputs.json logs/S1.log
Exit 0 ok, 2 missing / malformed / sha1-mismatching input.
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
FUZZER = os.path.join(REPO, 'sims', 'verilator', 'base-attacks', 'fuzzer')
for _p in (FUZZER, HERE):                      # python -I does not put the script dir on sys.path
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_cg_inputs as BG          # fixed_part, KNOB, read_cfgs, pt_dict, fuzzer_cfg
import cal_fit as V1                  # parse_power, nnls3 (v1 parsers; v1 itself is not changed)
import boom_ift.descriptor as DESC    # area_breakdown
import boom_ift.bench_ipc as BIPC     # parse_log

GROUPS = ['core', 'lsu', 'frontend', 'dcache', 'ptw']
CAL_IDS = ['W4', 'W2', 'W1', 'MIN4', 'HELD2']
BETA_IDS = ['MIN4', 'HELD2']                    # non-full-size synthesized tiles (the W tiles are identities)
BETA_GROUPS = ['core', 'frontend']
W_SOURCE = {4: 'W4', 2: 'W2', 1: 'W1'}
THRESH = 0.10
GRID = [i / 1000.0 for i in range(1001)]
U_SENS = 0.035
MODEL = 'v2: P=sum_G a_G*C_G*((1-theta)+theta*u); plan power-cal-revision-plan.md sha1 65b42143 sections 1.1-1.3'

# sha1 of every calibration input (checked before --calibrate; the pinned tree of plan 65b42143)
PINNED = {
    'W4': {
        'util_hier.rpt': '4c3da20c096d347651efda2454b8026072318ae4',
        'vectorless.rpt': 'e470aadebca7e82676b22ecdb8a624dfd9531c0a',
        'power_dhry.rpt': 'fa44ef7718b4e10fc054e3b93a8cda131f2a47a8',
        'cg_inputs.json': '6b719716d5935b62b730bc68095e234d1d1c6085',
        'logs/S1.log': 'b4c44b5384a86915167977f2cdad1cd24da359b4',
    },
    'W2': {
        'util_hier.rpt': '357e0e76985497b148d1d476693fe7fd5adbc492',
        'vectorless.rpt': '838fa5b31e03276f8e3dbb2621141c1ec57a8656',
        'power_dhry.rpt': '1e2f0743e52f0ae67442e9ce71aa67e66cc8de15',
        'cg_inputs.json': '76b96324d096be73cf004088199aa531d36f4426',
        'logs/S1.log': '2e88d4d44bea560524134c1d716af8d20176b87a',
    },
    'W1': {
        'util_hier.rpt': '5cd0765f15bb4ce42ad712fc9ad3760f0c12d3a3',
        'vectorless.rpt': '0182e672e14b314697ec14dfe496c40c9619e7d2',
        'power_dhry.rpt': '48a9494a9905428f5d50aeacf375d0fd2cc764bf',
        'cg_inputs.json': '88039038c4b4684b00e941acabc7d2627a2b5853',
        'logs/S1.log': '744fb9fd5f8741bb3c05f754c9ebba06f98f3f7a',
    },
    'MIN4': {
        'util_hier.rpt': 'f7ad02df698c9e1b51d1b5a3694e91e8f75650ea',
        'vectorless.rpt': 'fbf361b5167ed1aac29ceb146c9bfb872fab40a6',
        'power_dhry.rpt': 'e25ccee209e55f220bdfb584d11c2501a89b3260',
        'cg_inputs.json': 'f65cf79f8ddd70e22af7aad33ac17348af19a01b',
        'logs/S1.log': '4e734ccb36e52b51673c19c7d9d174e86ea0efd1',
    },
    'HELD2': {
        'util_hier.rpt': 'fde836c1d4ba372bef5e61300acb19920c05e953',
        'vectorless.rpt': '47f382ecd51c4f53363e694ce57f933ab9c43175',
        'power_dhry.rpt': '75810cd8e5977b7109787e2e42acb4435e6df02d',
        'cg_inputs.json': 'd556cff1437aeabbab4603fe8a2e89cd8d0528e0',
        'logs/S1.log': '98945b902fd7daabdeedbb628bd411ffb6171734',
    },
}


class InputError(Exception):
    pass


# ---------------------------------------------------------------- helpers
def sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()


def r12(o):
    """12 significant digits (a_G ~ 1e-5 W/LUT needs more than fixed decimals); deterministic."""
    if isinstance(o, float):
        return float('%.12g' % o)
    if isinstance(o, dict):
        return {k: r12(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [r12(v) for v in o]
    return o


def dump(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(r12(obj), f, indent=1, sort_keys=True)
        f.write('\n')


def rel(p, m):
    return None if not m else (p - m) / m


def pct(x):
    return 'n/a' if x is None else '%+.2f%%' % (100 * x)


# ---------------------------------------------------------------- model core (pure, no files)
def gfac(theta, u):
    return (1.0 - theta) + theta * u


def cap(c, prm):
    """C_G for a tile dict c = {width, K{G}, dc_b36} under params prm = {tab, beta, lambda_B}."""
    w = str(c['width'])
    F, Kf = prm['tab']['F'][w], prm['tab']['Kfull'][w]
    out = {}
    for g in GROUPS:
        k = c['K'].get(g, 0.0)
        if g in BETA_GROUPS:
            rho = k / Kf[g]
            b = prm['beta'][g]
            out[g] = k + F[g] * (1.0 - b + b * rho)
        else:
            out[g] = k + F[g]
    out['dcache'] += prm['lambda_B'] * c['dc_b36']
    return out


def fit_beta(tiles, tab):
    """beta_G from the beta tiles' in-tile area (c['F_meas']); returns (beta, detail)."""
    beta, det = {}, {}
    pts = [c for c in tiles if c.get('beta_pt')]
    for g in BETA_GROUPS:
        num = den = 0.0
        rows = []
        for c in pts:
            w = str(c['width'])
            phi = c['F_meas'][g] / tab['F'][w][g]
            rho = c['K'][g] / tab['Kfull'][w][g]
            num += (phi - 1.0) * (rho - 1.0)
            den += (rho - 1.0) ** 2
            rows.append({'id': c['id'], 'phi': phi, 'rho': rho})
        b = num / den if den > 0 else 0.0
        beta[g] = min(1.0, max(0.0, b))
        for r in rows:
            r['F_hat_over_F_meas_minus1'] = (1.0 - beta[g] + beta[g] * r['rho']) / r['phi'] - 1.0
        det[g] = {'raw': num / den if den > 0 else None, 'rows': rows}
    return beta, det


def fit_lambda(tiles):
    """lambda_B = k_B/k_L from NNLS of vectorless dcache power on (LUT_dc, B36_dc)."""
    A = [[c['K']['dcache'], c['dc_b36']] for c in tiles]
    b = [c['vl_dc'] for c in tiles]
    k, cond = V1.nnls3(A, b)
    kL, kB = float(k[0]), float(k[1])
    if kB <= 0.0:
        lam = 0.0
    elif kL <= 0.0:
        raise InputError('lambda_B NNLS gave k_L = 0 with k_B > 0 (lambda_B undefined)')
    else:
        lam = kB / kL
    return {'k_L': kL, 'k_B': kB, 'lambda_B': lam, 'condition_number': float(cond)}


def fit_power(tiles, prm):
    """theta, a_G on the grid; returns (theta, a, J_grid)."""
    Cs = [cap(c, prm) for c in tiles]
    best, Js = None, []
    for th in GRID:
        a = {}
        for g in GROUPS:
            rs = []
            for c, C in zip(tiles, Cs):
                d = C[g] * gfac(th, c['u'])
                if d > 0:
                    rs.append(c['act_groups'][g] / d)
            a[g] = sum(rs) / len(rs) if rs else 0.0
        J = 0.0
        for c, C in zip(tiles, Cs):
            p = sum(a[g] * C[g] for g in GROUPS) * gfac(th, c['u'])
            J += ((p - c['act_total']) / c['act_total']) ** 2
        Js.append(J)
        if best is None or J < best[0]:
            best = (J, th, a)
    return best[1], best[2], Js


def fit_all(tiles, tab):
    """Full v2 fit on tiles (each with act, vl_dc, u, K, dc_b36, F_meas for beta tiles)."""
    beta, bdet = fit_beta(tiles, tab)
    lam = fit_lambda(tiles)
    prm = {'tab': tab, 'beta': beta, 'lambda_B': lam['lambda_B']}
    theta, a, Js = fit_power(tiles, prm)
    prm = dict(prm, theta=theta, a_G=a)
    return prm, {'beta_detail': bdet, 'lambda_fit': lam, 'J_grid': Js}


def predict_tile(prm, c):
    C = cap(c, prm)
    g = gfac(prm['theta'], c['u'])
    pg = {k: prm['a_G'][k] * C[k] * g for k in GROUPS}
    return pg, sum(pg.values()), C


def flat_interval(Js):
    jm = min(Js)
    lim = max(1.5 * jm, jm + 1e-6)
    ok = [GRID[i] for i, j in enumerate(Js) if j <= lim]
    return [min(ok), max(ok)]


def params_block(prm):
    return {'theta': prm['theta'], 'a_G': prm['a_G'], 'beta': prm['beta'], 'lambda_B': prm['lambda_B'],
            'tab': prm['tab']}


def load_prm(block):
    return {'theta': block['theta'], 'a_G': block['a_G'], 'beta': block['beta'], 'lambda_B': block['lambda_B'],
            'tab': block['tab']}


def calibrate_core(tiles, tab):
    """Pure calibration + diagnostics.  tiles: list of dicts (see fit_all).  Returns the params.json content
    (without provenance)."""
    prm, det = fit_all(tiles, tab)
    prm = load_prm(r12(params_block(prm)))          # predict from the rounded numbers actually stored
    atot_r = [c['act_total'] / sum(cap(c, prm).values()) for c in tiles]
    a_tot = sum(atot_r) / len(atot_r)
    prm['a_tot'] = a_tot
    ins = {}
    for c in tiles:
        pg, tot, _ = predict_tile(prm, c)
        ap = a_tot * sum(cap(c, prm).values())
        ins[c['id']] = {'u': c['u'], 'act_total': c['act_total'], 'pred_total': tot, 'error': rel(tot, c['act_total']),
                        'area_only_total': ap, 'area_only_error': rel(ap, c['act_total']),
                        'groups': {g: {'act': c['act_groups'][g], 'pred': pg[g], 'error': rel(pg[g], c['act_groups'][g])}
                                   for g in GROUPS}}
    loo = {}
    for c in tiles:
        rest = [x for x in tiles if x['id'] != c['id']]
        p2, d2 = fit_all(rest, tab)
        pg, tot, _ = predict_tile(p2, c)
        ap = (sum(x['act_total'] / sum(cap(x, p2).values()) for x in rest) / len(rest)) * sum(cap(c, p2).values())
        loo[c['id']] = {'theta': p2['theta'], 'beta': p2['beta'], 'lambda_B': p2['lambda_B'],
                        'pred_total': tot, 'act_total': c['act_total'], 'error': rel(tot, c['act_total']),
                        'area_only_error': rel(ap, c['act_total'])}
    Js = det['J_grid']
    diag = {'J_min': min(Js), 'J_at_grid': {('%.1f' % t): Js[int(round(t * 1000))] for t in [i / 10 for i in range(11)]},
            'J_flat_interval_1p5x': flat_interval(Js), 'beta_detail': det['beta_detail'],
            'lambda_fit': det['lambda_fit'], 'a_tot': a_tot, 'in_sample': ins, 'loo': loo,
            'a_G_spread_max_over_min': spread(prm, tiles)}
    return {'params': params_block(prm), 'a_tot': a_tot, 'diagnostics': diag}


def spread(prm, tiles):
    out = {}
    for g in GROUPS:
        rs = []
        for c in tiles:
            d = cap(c, prm)[g] * gfac(prm['theta'], c['u'])
            if d > 0 and c['act_groups'][g] > 0:
                rs.append(c['act_groups'][g] / d)
        out[g] = (max(rs) / min(rs)) if rs else None
    return out


# ---------------------------------------------------------------- input loading (real tree)
def load_pins(path):
    """Pins file -> {id: {relpath: sha1}}; must cover CAL_IDS with exactly the PINNED relpaths."""
    try:
        pins = json.load(open(path))
    except (OSError, ValueError) as e:
        raise InputError('cannot read pins file %s: %s' % (path, e))
    for i in CAL_IDS:
        if not isinstance(pins, dict) or not isinstance(pins.get(i), dict) or set(pins[i]) != set(PINNED[i]):
            raise InputError('pins file %s: id %s missing or its relpaths differ from %s' % (path, i, sorted(PINNED[i])))
    return pins


def cmd_write_pins(root, path):
    if path is None:
        raise InputError('--write-pins needs --pins FILE')
    if os.path.exists(path):
        raise InputError('%s exists; --write-pins never overwrites' % path)
    pins = {}
    for i in CAL_IDS:
        pins[i] = {}
        for f in PINNED[i]:
            p = os.path.join(root, i, f)
            if not os.path.isfile(p):
                raise InputError('missing input %s' % p)
            pins[i][f] = sha1(p)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, 'w') as fh:
        json.dump(pins, fh, indent=1, sort_keys=True)
        fh.write('\n')
    print('wrote %s sha1 %s' % (path, sha1(path)))
    return 0


def check_pinned(root, cid, pins=None):
    for f, want in (pins or PINNED)[cid].items():
        p = os.path.join(root, cid, f)
        if not os.path.isfile(p):
            raise InputError('missing pinned input %s' % p)
        got = sha1(p)
        if got != want:
            raise InputError('pinned sha1 mismatch %s: %s != %s' % (p, got, want))


def area_tile(cid, pt):
    """Knob-LUT sums per group and D$ B36 from area_breakdown for a knob string."""
    p = BG.pt_dict(pt)
    cfg = BG.fuzzer_cfg(p)
    ab = DESC.area_breakdown(cfg)
    if ab['missing']:
        raise InputError('%s: area_breakdown has no value for %s' % (cid, ab['missing']))
    if set(ab['modules']) != set(BG.KNOB):
        raise InputError('%s: area_breakdown modules differ from build_cg_inputs.KNOB' % cid)
    K = {g: 0.0 for g in GROUPS}
    for m, (g, _p) in sorted(BG.KNOB.items()):
        K[g] += float(ab['modules'][m]['value'][0])
    dv = ab['modules']['DCache']['value']
    return {'width': p['width'], 'K': K, 'dc_b36': float(dv[2]) + float(dv[3]) / 2.0,
            'area_total': {k: ab[k] for k in ('lut', 'ff', 'ramb36', 'ramb18')},
            'substituted': list(ab['substituted']), 'cfg': cfg}


def read_u(root, cid, width):
    p = os.path.join(root, cid, 'logs', 'S1.log')
    if not os.path.isfile(p):
        raise InputError('missing %s' % p)
    r = BIPC.parse_log(open(p).read())
    if not r['passed'] or not r['mcycle'] or not r['minstret']:
        raise InputError('%s: S1 not PASSED or counters missing (%s)' % (p, r))
    ipc = r['minstret'] / r['mcycle']
    return {'mcycle': r['mcycle'], 'minstret': r['minstret'], 'ipc': ipc, 'u': ipc / width}


def build_tab(root, rows):
    F, Kf = {}, {}
    for w, sid in sorted(W_SOURCE.items()):
        up = os.path.join(root, sid, 'util_hier.rpt')
        grp, _intile, _top = BG.fixed_part(up)
        F[str(w)] = {g: float(grp[g]['lut']) for g in GROUPS}
        Kf[str(w)] = area_tile(sid, rows[sid]['pt'])['K']
    return {'F': F, 'Kfull': Kf}


def load_cal_tiles(root, rows, tab, pins=None):
    tiles = []
    for cid in CAL_IDS:
        check_pinned(root, cid, pins)
        a = area_tile(cid, rows[cid]['pt'])
        act = V1.parse_power(os.path.join(root, cid, 'power_dhry.rpt'))
        vl = V1.parse_power(os.path.join(root, cid, 'vectorless.rpt'))
        u = read_u(root, cid, a['width'])
        c = {'id': cid, 'width': a['width'], 'K': a['K'], 'dc_b36': a['dc_b36'], 'u': u['u'], 'ipc': u['ipc'],
             'act_total': act['total'], 'act_groups': {g: act['groups'][g] for g in GROUPS},
             'vl_dc': vl['groups']['dcache'], 'beta_pt': cid in BETA_IDS}
        # cross-check the live area lookup against the pinned cg_inputs.json (knob LUT per group)
        cg = json.load(open(os.path.join(root, cid, 'cg_inputs.json')))['per_group']
        for g in GROUPS:
            if abs(cg[g]['knob']['lut'] - c['K'][g]) > 0.5:
                raise InputError('%s: area_breakdown knob LUT %s=%s differs from cg_inputs.json %s'
                                 % (cid, g, c['K'][g], cg[g]['knob']['lut']))
        if cid in BETA_IDS:
            grp, _i, _t = BG.fixed_part(os.path.join(root, cid, 'util_hier.rpt'))
            c['F_meas'] = {g: float(grp[g]['lut']) for g in GROUPS}
        tiles.append(c)
    return tiles


def cmd_calibrate(root, out, pins_file=None):
    pins = load_pins(pins_file) if pins_file else PINNED
    env, rows = BG.read_cfgs()
    for i in CAL_IDS:
        if i not in rows:
            raise InputError('cal_cfgs.env has no row %s' % i)
    for i in W_SOURCE.values():
        check_pinned(root, i, pins)
    tab = build_tab(root, rows)
    tiles = load_cal_tiles(root, rows, tab, pins)
    res = calibrate_core(tiles, tab)
    res['model'] = MODEL
    res['calibration'] = CAL_IDS
    res['beta_tiles'] = BETA_IDS
    res['area_model'] = DESC.AREA_MODEL_TAG
    res['inputs_sha1'] = {i: dict(pins[i]) for i in CAL_IDS}
    res['pins_file_sha1'] = sha1(pins_file) if pins_file else None
    res['script_sha1'] = sha1(os.path.abspath(__file__))
    res['pt_strings'] = {i: rows[i]['pt'] for i in CAL_IDS}
    path = os.path.join(out, 'params.json')
    dump(res, path)
    print('wrote %s sha1 %s' % (path, sha1(path)))
    print('theta %.3f  beta %s  lambda_B %.4f' % (res['params']['theta'], res['params']['beta'],
                                                   res['params']['lambda_B']))
    for i, v in res['diagnostics']['in_sample'].items():
        print('  in-sample %-6s %s   LOO %s' % (i, pct(v['error']), pct(res['diagnostics']['loo'][i]['error'])))
    return 0


# ---------------------------------------------------------------- predict / score
def load_params(out):
    p = os.path.join(out, 'params.json')
    if not os.path.isfile(p):
        raise InputError('missing %s (run --calibrate first)' % p)
    d = json.load(open(p))
    return d, load_prm(d['params']), sha1(p)


def check_pins_used(root, d, pins_file):
    """predict/score: the pins params.json was calibrated with still hold for the W tiles; --pins must be that file."""
    if pins_file is not None and sha1(pins_file) != d.get('pins_file_sha1'):
        raise InputError('--pins %s sha1 differs from the pins file params.json used (%s)' % (pins_file, d.get('pins_file_sha1')))
    used = d.get('inputs_sha1')
    if used:
        for i in W_SOURCE.values():
            check_pinned(root, i, used)


def cmd_predict(root, out, cid, pt, pins_file=None):
    d, prm, psha = load_params(out)
    check_pins_used(root, d, pins_file)
    env, rows = BG.read_cfgs()
    if pt is None:
        if cid not in rows:
            raise InputError('no cal_cfgs.env row %s (E7 not applied?) and no --pt' % cid)
        pt = rows[cid]['pt']
    if d.get('area_model') != DESC.AREA_MODEL_TAG:
        raise InputError('area model %s differs from the calibration %s' % (DESC.AREA_MODEL_TAG, d.get('area_model')))
    for f in ('power_dhry.rpt', 'vectorless.rpt', os.path.join('v', 'power_saif.rpt')):
        if os.path.exists(os.path.join(root, cid, f)):
            raise InputError('%s exists: the sealed prediction must be made before %s power exists' % (f, cid))
    if os.path.exists(os.path.join(out, 'predict-%s.json' % cid)):
        raise InputError('sealed %s exists; never overwritten' % os.path.join(out, 'predict-%s.json' % cid))
    a = area_tile(cid, pt)
    s1 = os.path.join(root, cid, 'logs', 'S1.log')
    u = read_u(root, cid, a['width'])
    c = {'id': cid, 'width': a['width'], 'K': a['K'], 'dc_b36': a['dc_b36'], 'u': u['u']}
    pg, tot, C = predict_tile(prm, c)
    res = {'id': cid, 'model': MODEL, 'pt': pt, 'width': a['width'], 'params_sha1': psha, 'area_model': DESC.AREA_MODEL_TAG,
           's1_log_sha1': sha1(s1), 'mcycle': u['mcycle'], 'minstret': u['minstret'], 'ipc': u['ipc'], 'u': u['u'],
           'K': a['K'], 'dc_b36': a['dc_b36'], 'C': C, 'pred_groups': pg, 'pred_total_W': tot,
           'area_substituted': a['substituted'], 'script_sha1': sha1(os.path.abspath(__file__))}
    path = os.path.join(out, 'predict-%s.json' % cid)
    dump(res, path)
    print('wrote %s sha1 %s  predicted total %.4f W (u %.4f)' % (path, sha1(path), tot, u['u']))
    return 0


def md(r):
    L = ['# Power calibration v2 (%s)' % r['id'], '',
         'Verdict: **%s** - %s total dynamic error %s (threshold +-%d%%).' % (r['verdict'], r['id'], pct(r['error_total']),
                                                                           100 * THRESH), '',
         'Sealed prediction sha1 %s (params sha1 %s). theta %.3f, u %.4f.' % (r['predict_sha1'][:8], r['params_sha1'][:8],
                                                                             r['params']['theta'], r['u']), '',
         '| group | measured W | predicted W | error |', '|---|---|---|---|']
    for g in GROUPS:
        x = r['groups'][g]
        L.append('| %s | %.4f | %.4f | %s |' % (g, x['measured'], x['predicted'], pct(x['error'])))
    L.append('| **total** | %.4f | %.4f | %s |' % (r['measured_total_W'], r['pred_total_W'], pct(r['error_total'])))
    L += ['', 'v1 (adopted model, W4 W2 W1 MIN4) prediction: %s' % ('n/a' if r['v1_pred_total_W'] is None else
          '%.4f W (%s)' % (r['v1_pred_total_W'], pct(r['v1_error_total']))),
          'Area-only proxy (one factor x sum C_G): %.4f W (%s)' % (r['area_only_total_W'], pct(r['area_only_error'])),
          'Sensitivity to u +-%.1f%%: error %s / %s' % (100 * U_SENS, pct(r['sens_u_minus_error']), pct(r['sens_u_plus_error'])),
          'F_hat core / frontend vs measured in-tile F: %s' % r['F_check'], '',
          'Order of the totals (low to high) measured: %s; predicted: %s; match: %s.' % (
              ' < '.join(r['order_measured']), ' < '.join(r['order_predicted']), r['order_match']), '',
          '| config | role | measured W | predicted W | error |', '|---|---|---|---|---|']
    for i, v in r['all_totals'].items():
        L.append('| %s | %s | %.4f | %.4f | %s |' % (i, v['role'], v['measured'], v['predicted'], pct(v['error'])))
    L.append('')
    return '\n'.join(L)


def cmd_score(root, out, cid, pins_file=None):
    d, prm, psha = load_params(out)
    check_pins_used(root, d, pins_file)
    pp = os.path.join(out, 'predict-%s.json' % cid)
    if not os.path.isfile(pp):
        raise InputError('missing sealed %s' % pp)
    pr = json.load(open(pp))
    if pr['params_sha1'] != psha:
        raise InputError('sealed prediction was made with params sha1 %s, current params.json is %s'
                         % (pr['params_sha1'], psha))
    s1 = os.path.join(root, cid, 'logs', 'S1.log')
    if sha1(s1) != pr['s1_log_sha1']:
        raise InputError('S1.log changed since the prediction was sealed')
    act = V1.parse_power(os.path.join(root, cid, 'power_dhry.rpt'))
    meas_total = act['total']
    e = rel(pr['pred_total_W'], meas_total)
    groups = {g: {'measured': act['groups'][g], 'predicted': pr['pred_groups'][g],
                  'error': rel(pr['pred_groups'][g], act['groups'][g]) if abs(act['groups'][g]) > 1e-9 else None}
              for g in GROUPS}
    c = {'id': cid, 'width': pr['width'], 'K': pr['K'], 'dc_b36': pr['dc_b36']}
    sens = {}
    for tag, f in (('minus', 1.0 - U_SENS), ('plus', 1.0 + U_SENS)):
        c['u'] = pr['u'] * f
        sens[tag] = rel(predict_tile(prm, c)[1], meas_total)
    a_tot = d['a_tot']
    ap = a_tot * sum(pr['C'].values())
    # v1 (the adopted model: W4 W2 W1 MIN4 calibration) prediction of this config, for comparison
    try:
        v1 = V1.run(root, 'dhry', ['W4', 'W2', 'W1', 'MIN4'], cid)['configs'][cid]
        v1p, v1e = v1['predicted_total_W'], v1['error_total']
    except Exception:                       # v1 inputs (e.g. cg_inputs.json of cid) not there yet
        v1p = v1e = None
    # F_hat vs the measured in-tile F (after P2)
    fcheck = 'n/a (no util_hier.rpt)'
    up = os.path.join(root, cid, 'util_hier.rpt')
    if os.path.isfile(up):
        grp, _i, _t = BG.fixed_part(up)
        w = str(pr['width'])
        parts = []
        for g in BETA_GROUPS:
            rho = pr['K'][g] / prm['tab']['Kfull'][w][g]
            b = prm['beta'][g]
            fh = prm['tab']['F'][w][g] * (1 - b + b * rho)
            parts.append('%s %.0f vs %.0f (%s)' % (g, fh, grp[g]['lut'], pct(rel(fh, grp[g]['lut']))))
        fcheck = '; '.join(parts)
    allt = {}
    for i, v in d['diagnostics']['in_sample'].items():
        allt[i] = {'role': 'calibration', 'measured': v['act_total'], 'predicted': v['pred_total'], 'error': v['error']}
    allt[cid] = {'role': 'held-out', 'measured': meas_total, 'predicted': pr['pred_total_W'], 'error': e}
    mo = sorted(allt, key=lambda i: (allt[i]['measured'], i))
    po = sorted(allt, key=lambda i: (allt[i]['predicted'], i))
    res = {'id': cid, 'verdict': 'ADOPT' if (e is not None and abs(e) <= THRESH) else 'NOT ADOPTED',
           'threshold': THRESH, 'error_total': e, 'measured_total_W': meas_total, 'pred_total_W': pr['pred_total_W'],
           'groups': groups, 'v1_pred_total_W': v1p, 'v1_error_total': v1e, 'area_only_total_W': ap,
           'area_only_error': rel(ap, meas_total), 'sens_u_minus_error': sens['minus'], 'sens_u_plus_error': sens['plus'],
           'F_check': fcheck, 'all_totals': allt, 'order_measured': mo, 'order_predicted': po, 'order_match': mo == po,
           'u': pr['u'], 'params': d['params'], 'params_sha1': psha, 'predict_sha1': sha1(pp),
           'loo': d['diagnostics']['loo'], 'in_sample': d['diagnostics']['in_sample'],
           'theta_per_loo_fold': {k: v['theta'] for k, v in d['diagnostics']['loo'].items()},
           'J_flat_interval_1p5x': d['diagnostics']['J_flat_interval_1p5x'],
           'lambda_condition_number': d['diagnostics']['lambda_fit']['condition_number'],
           'power_dhry_sha1': sha1(os.path.join(root, cid, 'power_dhry.rpt'))}
    dump(res, os.path.join(out, 'power-cal-v2.json'))
    with open(os.path.join(out, 'power-cal-v2.md'), 'w') as f:
        f.write(md(r12(res)))
    print('%s: e = %s -> %s' % (cid, pct(e), res['verdict']))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--calibrate', action='store_true')
    g.add_argument('--predict', metavar='ID')
    g.add_argument('--score', metavar='ID')
    g.add_argument('--write-pins', action='store_true')
    ap.add_argument('--pins', default=None, help='pins JSON {id: {relpath: sha1}}; default = built-in PINNED')
    ap.add_argument('--root', default='/data/akrish/scratch/power-cal')
    ap.add_argument('--out', default=None)
    ap.add_argument('--pt', default=None, help='knob string override for --predict')
    a = ap.parse_args(argv)
    out = a.out or os.path.join(a.root, 'fit-v2')
    try:
        if a.write_pins:
            return cmd_write_pins(a.root, a.pins)
        if a.calibrate:
            return cmd_calibrate(a.root, out, a.pins)
        if a.predict:
            return cmd_predict(a.root, out, a.predict, a.pt, a.pins)
        return cmd_score(a.root, out, a.score, a.pins)
    except (InputError, V1.InputError, BG.InputError, OSError, ValueError, KeyError) as e:
        print('ERROR: %s' % e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
