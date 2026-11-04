#!/usr/bin/env python3
"""Unit test of cal_fit_v2.py: synthetic data with a known theta, a_G, beta, lambda_B, plus the v1 reproduction.

Run: python3 -I tools/ooc-synth/power/test_cal_fit_v2.py     (1 thread; reads only rpt/json under
/data/akrish/scratch/power-cal for the v1 reproduction; writes only to a temp dir)
"""
import json, math, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cal_fit_v2 as M
import cal_fit as V1

ROOT = '/data/akrish/scratch/power-cal'
TH, AG, BETA, LAM = 0.57, {'core': 2.0e-5, 'lsu': 1.5e-5, 'frontend': 3.0e-5, 'dcache': 2.5e-5, 'ptw': 4.0e-5}, \
    {'core': 0.8, 'frontend': 0.25}, 300.0
KL = 1.0e-5
TAB = {'F': {'2': {'core': 80000., 'lsu': 0., 'frontend': 20000., 'dcache': 0., 'ptw': 2500.},
             '4': {'core': 150000., 'lsu': 0., 'frontend': 22000., 'dcache': 0., 'ptw': 2800.}},
       'Kfull': {'2': {'core': 200000., 'lsu': 75000., 'frontend': 46000., 'dcache': 15800., 'ptw': 0.},
                 '4': {'core': 350000., 'lsu': 130000., 'frontend': 50000., 'dcache': 15800., 'ptw': 0.}}}


def synth(i, w, fr, b36, u, beta_pt):
    """A tile whose power obeys the model exactly (fr = knob fraction of full size per group)."""
    K = {g: TAB['Kfull'][str(w)][g] * fr for g in M.GROUPS}
    K['dcache'] = 13000. + 200. * b36
    K['ptw'] = 0.
    c = {'id': i, 'width': w, 'K': K, 'dc_b36': float(b36), 'u': u, 'beta_pt': beta_pt}
    prm = {'tab': TAB, 'beta': BETA, 'lambda_B': LAM, 'theta': TH, 'a_G': AG}
    C = M.cap(c, prm)
    c['act_groups'] = {g: AG[g] * C[g] * M.gfac(TH, u) for g in M.GROUPS}
    c['act_total'] = sum(c['act_groups'].values())
    c['vl_dc'] = KL * K['dcache'] + KL * LAM * b36
    if beta_pt:
        c['F_meas'] = {g: TAB['F'][str(w)][g] * (1 - BETA[g] + BETA[g] * K[g] / TAB['Kfull'][str(w)][g])
                       for g in M.BETA_GROUPS}
    return c


def tiles():
    return [synth('A', 4, 1.0, 38, 0.68, False), synth('B', 2, 1.0, 38, 0.82, False),
            synth('C', 4, 0.22, 4, 0.19, True), synth('D', 2, 0.48, 19, 0.74, True),
            synth('E', 4, 0.6, 12, 0.45, False)]


class T(unittest.TestCase):
    def test_recovery(self):
        r = M.calibrate_core(tiles(), TAB)
        p = r['params']
        self.assertAlmostEqual(p['theta'], TH, places=9)
        for g in M.GROUPS:
            self.assertAlmostEqual(p['a_G'][g] / AG[g], 1.0, places=8)
        for g in M.BETA_GROUPS:
            self.assertAlmostEqual(p['beta'][g], BETA[g], places=8)
        self.assertAlmostEqual(p['lambda_B'] / LAM, 1.0, places=8)
        for v in r['diagnostics']['in_sample'].values():
            self.assertLess(abs(v['error']), 1e-8)

    def test_predict_exact_and_deterministic(self):
        r1 = M.calibrate_core(tiles(), TAB)
        r2 = M.calibrate_core(tiles(), TAB)
        self.assertEqual(json.dumps(M.r12(r1), sort_keys=True), json.dumps(M.r12(r2), sort_keys=True))
        h = synth('H', 2, 0.3, 10, 0.5, False)
        prm = M.load_prm(r1['params'])
        _, tot, _ = M.predict_tile(prm, h)
        self.assertLess(abs(tot / h['act_total'] - 1), 1e-8)

    def test_loo_runs(self):
        r = M.calibrate_core(tiles(), TAB)
        self.assertEqual(set(r['diagnostics']['loo']), {'A', 'B', 'C', 'D', 'E'})

    def test_beta_clip_and_bram_zero(self):
        ts = tiles()
        for c in ts:                       # vectorless independent of B36 -> k_B = 0 -> lambda_B = 0
            c['vl_dc'] = KL * c['K']['dcache']
        self.assertEqual(M.fit_lambda(ts)['lambda_B'], 0.0)
        hi = [dict(c, F_meas={g: 0.1 * TAB['F'][str(c['width'])][g] for g in M.BETA_GROUPS}) if c.get('beta_pt') else c
              for c in tiles()]
        b, det = M.fit_beta(hi, TAB)
        for g in M.BETA_GROUPS:
            self.assertEqual(b[g], 1.0)
            self.assertGreater(det[g]['raw'], 1.0)
        lo = [dict(c, F_meas={g: 2.0 * TAB['F'][str(c['width'])][g] for g in M.BETA_GROUPS}) if c.get('beta_pt') else c
              for c in tiles()]
        b, det = M.fit_beta(lo, TAB)
        for g in M.BETA_GROUPS:
            self.assertEqual(b[g], 0.0)
            self.assertLess(det[g]['raw'], 0.0)

    @unittest.skipUnless(os.path.isdir(ROOT + '/fit'), 'power-cal tree not present')
    def test_v1_reproduction(self):
        ref = json.load(open(ROOT + '/fit/power-cal.json'))
        out = M.r12(V1.rnd(V1.run(ROOT, 'dhry', ['W4', 'W2', 'W1', 'MIN4'], 'HELD2')))
        self.assertAlmostEqual(out['configs']['HELD2']['error_total'], -0.0544, places=4)
        self.assertAlmostEqual(out['configs']['MIN4']['error_total'], 0.7441, places=4)
        self.assertEqual(out['verdict'], ref['verdict'])
        for i in ref['configs']:
            self.assertAlmostEqual(out['configs'][i]['error_total'], ref['configs'][i]['error_total'], places=8)
        print('v1 path: HELD2 %+.2f %%  MIN4 %+.2f %%' % (100 * out['configs']['HELD2']['error_total'],
                                                           100 * out['configs']['MIN4']['error_total']))

    @unittest.skipUnless(os.path.isdir(ROOT + '/fit'), 'power-cal tree not present')
    def test_inputs_pinned_and_tab(self):
        for i in M.CAL_IDS:
            M.check_pinned(ROOT, i)
        import build_cg_inputs as BG
        _, rows = BG.read_cfgs()
        tab = M.build_tab(ROOT, rows)
        F = {'core': (148333., 86324., 65693.), 'frontend': (22893., 22515., 29538.), 'ptw': (2793., 2572., 2589.),
             'lsu': (0., 0., 0.), 'dcache': (0., 0., 0.)}
        for g, v in F.items():
            self.assertEqual(tuple(tab['F'][w][g] for w in ('4', '2', '1')), v)
        S1 = {'W4': (4, 205932, 558522), 'W2': (2, 341763, 558755), 'W1': (1, 572593, 559261),
              'MIN4': (4, 723789, 559762), 'HELD2': (2, 376437, 558790)}
        for i, (w, mc, mi) in S1.items():
            u = M.read_u(ROOT, i, w)
            self.assertEqual((u['mcycle'], u['minstret']), (mc, mi))

    def _predict_setup(self, tag):
        tmp = tempfile.mkdtemp(prefix='calfit_t_', dir=os.environ.get('CLAUDE_JOB_DIR') and
                               os.path.join(os.environ['CLAUDE_JOB_DIR'], 'tmp') if os.path.isdir(
                                   os.path.join(os.environ.get('CLAUDE_JOB_DIR', ''), 'tmp')) else None)
        root, out = os.path.join(tmp, 'root'), os.path.join(tmp, 'out')
        os.makedirs(os.path.join(root, 'H', 'v'))
        os.makedirs(out)
        d = {'params': M.calibrate_core(tiles(), TAB)['params'], 'area_model': tag}
        json.dump(d, open(os.path.join(out, 'params.json'), 'w'))
        return root, out

    def test_predict_seal_refusals(self):
        root, out = self._predict_setup(M.DESC.AREA_MODEL_TAG)
        for f in ('power_dhry.rpt', 'vectorless.rpt', os.path.join('v', 'power_saif.rpt')):
            p = os.path.join(root, 'H', f)
            open(p, 'w').close()
            with self.assertRaisesRegex(M.InputError, 'must be made before'):
                M.cmd_predict(root, out, 'H', 'x')
            os.remove(p)
        open(os.path.join(out, 'predict-H.json'), 'w').close()
        with self.assertRaisesRegex(M.InputError, 'never overwritten'):
            M.cmd_predict(root, out, 'H', 'x')

    def test_predict_area_model_mismatch(self):
        root, out = self._predict_setup('some-other-tag')
        with self.assertRaisesRegex(M.InputError, 'area model'):
            M.cmd_predict(root, out, 'H', 'x')

    def _fake_root(self):
        tmp = tempfile.mkdtemp(prefix='calfit_pins_')
        root = os.path.join(tmp, 'root')
        for i in M.CAL_IDS:
            for f in M.PINNED[i]:
                p = os.path.join(root, i, f)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, 'w').write('%s/%s\n' % (i, f))
        return tmp, root

    def test_write_pins_and_load(self):
        tmp, root = self._fake_root()
        pf = os.path.join(tmp, 'pins.json')
        self.assertEqual(M.main(['--write-pins', '--pins', pf, '--root', root]), 0)
        pins = M.load_pins(pf)
        self.assertEqual(set(pins), set(M.CAL_IDS))
        for i in M.CAL_IDS:
            self.assertEqual(set(pins[i]), set(M.PINNED[i]))
            for f, h in pins[i].items():
                self.assertEqual(h, M.sha1(os.path.join(root, i, f)))
            M.check_pinned(root, i, pins)
            with self.assertRaisesRegex(M.InputError, 'pinned sha1 mismatch'):
                M.check_pinned(root, i)              # built-in PINNED does not match the synthetic files
        before = M.sha1(pf)
        self.assertEqual(M.main(['--write-pins', '--pins', pf, '--root', root]), 2)   # refuses to overwrite
        self.assertEqual(M.sha1(pf), before)
        self.assertEqual(M.main(['--write-pins', '--root', root]), 2)                 # needs --pins
        open(os.path.join(root, 'W4', 'logs', 'S1.log'), 'a').write('x')
        with self.assertRaisesRegex(M.InputError, 'pinned sha1 mismatch'):
            M.check_pinned(root, 'W4', pins)

    def test_write_pins_missing_and_bad_file(self):
        tmp, root = self._fake_root()
        os.remove(os.path.join(root, 'HELD2', 'power_dhry.rpt'))
        pf = os.path.join(tmp, 'p.json')
        self.assertEqual(M.main(['--write-pins', '--pins', pf, '--root', root]), 2)
        self.assertFalse(os.path.exists(pf))
        json.dump({'W4': {'util_hier.rpt': 'x'}}, open(pf, 'w'))
        with self.assertRaisesRegex(M.InputError, 'relpaths differ'):
            M.load_pins(pf)

    def test_params_pins_sha1_and_predict_check(self):
        tmp, root = self._fake_root()
        pf = os.path.join(tmp, 'pins.json')
        M.main(['--write-pins', '--pins', pf, '--root', root])
        pins = M.load_pins(pf)
        d = {'params': M.calibrate_core(tiles(), TAB)['params'], 'inputs_sha1': pins, 'pins_file_sha1': M.sha1(pf)}
        M.check_pins_used(root, d, pf)
        M.check_pins_used(root, d, None)
        other = os.path.join(tmp, 'other.json')
        json.dump(pins, open(other, 'w'), indent=2)
        with self.assertRaisesRegex(M.InputError, 'differs from the pins file'):
            M.check_pins_used(root, d, other)
        open(os.path.join(root, 'W2', 'vectorless.rpt'), 'a').write('x')
        with self.assertRaisesRegex(M.InputError, 'pinned sha1 mismatch'):
            M.check_pins_used(root, d, pf)
        d.pop('inputs_sha1')                          # old params.json without pins record: no extra check
        M.check_pins_used(root, d, None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
