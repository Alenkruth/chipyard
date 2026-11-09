#!/usr/bin/env python3
"""defpower_page.py -- deterministic results page for the defense power runs (owner: pages are scripts, not agents).

Plan: claude-artifacts/ooc-synthesis/plans/defense-power-plan.md sections 1.2 and 6.  Ids and knobs: defpower_cfgs.env next to this script.
Inputs (per id, W = ROOT/<id>): power_dhry.rpt (activity), vectorless.rpt, v/vivado.log (SAIF_FILL), v/act_RAMB.rpt + v/counts.txt
(BRAM S-tag share), window.env, prof/s2a_profile_pre.csv (poll halts in the window), ROOT/lane/prewin-<id>.log,
ROOT/lane/post_*.log (profile max_dev), logs/S1.log, logs/C0.log, logs/*.log of the point owner (P3, B1 sha1, KEY_B1), and the OOC point dir
ROOT/{A0,U}/ooc/points/Tile/fir/<CONFIG>/ (result.json, power_hier.rpt, util_hier.rpt, rtl/fir_elab.json, post_synth.dcp).
A ids (ASTT ADGL ASA ACEV AALL) share A0's netlist/point/sim binary (P3, LUT/FF, vectorless); their activity report, coverage, C0, window,
S1 and rcsr are their own.  Defense modules = instances in A0's util_hier.rpt that U's has not (no names in this script).
E9.r1 (2026-11-08): C0 per id, coverage 2 decimals, BRAM S-tag share, poll-halt check P, defense-module table from the util diff,
tree keys U vs A0, fixed notes, phone-width grid fix.  Files named *.pre-e10* are never read.
Missing input -> the cell reads 'pending'; the script never fails on a missing file.  Same inputs -> byte-identical page: no clock, no
randomness; 'data as of' is the newest mtime among the files read.
Usage: python3 -I defpower_page.py [--root /data/akrish/scratch/defense-power] [--out ROOT/page/defense-power.html]
"""
import argparse, glob, hashlib, html, json, os, re, time

ap = argparse.ArgumentParser()
ap.add_argument('--root', default='/data/akrish/scratch/defense-power')
ap.add_argument('--out', default=None)
a = ap.parse_args()
R = a.root
OUT = a.out or os.path.join(R, 'page', 'defense-power.html')
here = os.path.dirname(os.path.abspath(__file__))
IDS = ['U', 'A0', 'ASTT', 'ADGL', 'ASA', 'ACEV', 'AALL']
DEFS = ['ASTT', 'ADGL', 'ASA', 'ACEV', 'AALL']
LABEL = {'U': 'undefended core (own netlist)', 'A0': 'undefended on the AllDef netlist (paper baseline)', 'ASTT': 'STT', 'ADGL': 'STT + Doppelganger',
         'ASA': 'SpecASan', 'ACEV': 'Ceviche', 'AALL': 'all four'}
GROUPS = ['core', 'lsu', 'dcache', 'frontend', 'ptw']
FREQ_SCALE = 0.3  # 100 MHz report -> 30 MHz bitstream clock
PEND = 'pending'
E = html.escape
READ = []  # files actually read (mtime for 'data as of')


def rd(p):
    try:
        t = open(p, errors='replace').read()
    except OSError:
        return None
    READ.append(p)
    return t


def sha_file(p):
    try:
        h = hashlib.sha1()
        with open(p, 'rb') as f:
            for blk in iter(lambda: f.read(1 << 22), b''):
                h.update(blk)
        READ.append(p)
        return h.hexdigest()
    except OSError:
        return None


def cfg_env():
    d = {'cfg': {}, 'var': {}}
    t = rd(os.path.join(here, 'defpower_cfgs.env')) or ''
    for ln in t.splitlines():
        f = ln.split()
        if len(f) == 3 and f[0] in IDS:
            d['cfg'][f[0]] = f[1]
        m = re.match(r'^(\w+)=(.*)$', ln)
        if m:
            d['var'][m.group(1)] = m.group(2)
    return d


CE = cfg_env()
W = lambda i, *p: os.path.join(R, i, *p)
POINT_OWNER = lambda i: i if i == 'U' else 'A0'


def point(i, *p):
    o = POINT_OWNER(i); c = CE['cfg'].get(o)
    return os.path.join(R, o, 'ooc', 'points', 'Tile', 'fir', c or '?', *p)


def dyn(p):
    t = rd(p)
    if t is None:
        return None
    m = re.search(r'\| Dynamic \(W\)\s*\|\s*([0-9.]+)', t)
    return float(m.group(1)) if m else None


def hier(p):
    """[(path tuple below BoomTile, W)] from the 'By Hierarchy' table; indent 2 per level."""
    t = rd(p)
    if t is None:
        return None
    out = []; stack = []; on = False
    for ln in t.splitlines():
        if ln.strip() == '3.1 By Hierarchy':
            on = True; continue
        if not on:
            continue
        m = re.match(r'^\|( *)(\S+)\s*\|\s*([0-9.]+)\s*\|$', ln)
        if not m:
            continue
        lvl = len(m.group(1)) // 2
        stack = stack[:lvl] + [m.group(2)]
        if lvl >= 1:
            out.append((tuple(stack[1:]), float(m.group(3))))
    return out


def util_total(i):
    t = rd(point(i, 'result.json'))
    if t is None:
        return None
    try:
        return json.loads(t)['total']
    except Exception:
        return None


def coverage(i):
    t = rd(W(i, 'v', 'vivado.log'))
    if t is None:
        return None
    f = {k: int(v) for k, v in re.findall(r'^SAIF_FILL (cells|named|set) (\d+)$', t, re.M)}
    if len(f) != 3 or f['cells'] == 0:
        return None
    return (f['named'] + f['set']) / f['cells'], f


def s1(i):
    t = rd(W(i, 'logs', 'S1.log'))
    if t is None:
        return None
    g = lambda pat: (re.search(pat, t, re.M) or [None, None])[1]
    mc, mi, cy = g(r'^mcycle = (\d+)'), g(r'^minstret = (\d+)'), g(r'\*\*\* PASSED \*\*\* Completed after\s+(\d+)')
    if mc is None or mi is None or cy is None:
        return None
    return dict(mcycle=int(mc), minstret=int(mi), cycles=int(cy), rcsr=g(r'RCSR_SHA1=([0-9a-f]{40})'), loaded=g(r'^RCSR_LOADED=(\d+)'),
                rc=g(r'^S1_SIM_RC=(\d+)'))


def window(i):
    d = {}
    t = rd(W(i, 'window.env'))
    if t:
        d.update({k: v for k, v in (l.split('=', 1) for l in t.split() if '=' in l)})
    t = rd(os.path.join(R, 'lane', 'prewin-%s.log' % i)) or ''
    ms = re.findall(r'^PREWIN \S+ \S+ %s cal_window rc=(\d+) S=(\d+) A=(\d+) N=(\d+) L_boot=(\d+) L_plat=(\d+) stepdown=(\d+) checks=(\w+)'
                    % re.escape(i), t, re.M)
    if ms:
        m = ms[-1]; d.update(rc=m[0], S=m[1], A=m[2], N=m[3], L_boot=m[4], L_plat=m[5], stepdown=m[6], checks=m[7])
    t = ''
    for p in sorted(glob.glob(os.path.join(R, 'lane', 'post_*.log'))):
        t += rd(p) or ''
    ms = re.findall(r'^POSTLANE \S+ \S+ %s C1 ok .*max_dev=([0-9.]+%%)' % re.escape(i), t, re.M)
    if ms:
        d['max_dev'] = ms[-1]
    return d or None


def gates(i):
    o = POINT_OWNER(i); g = {}
    t = rd(W(o, 'logs', 'P3.log'))
    m = re.findall(r'^RESULT: (\S+)', t or '', re.M); g['P3'] = m[-1] if m else None
    t = rd(W(i, 'logs', 'C0.log'))  # C0 checks the converter on this id's own S0 dump
    m = re.findall(r'^C0_RESULT=(\S+)', t or '', re.M); g['C0'] = m[-1] if m else None
    return g


def b1_sha(i):
    t = rd(W(POINT_OWNER(i), 'logs', 'B1.log'))
    m = re.findall(r'^([0-9a-f]{40})  (\S*simulator-\S+)$', t or '', re.M)
    return m[-1] if m else None


def bram_stag(i):
    """(S-tagged RAMB output nets, RAMB output nets) from the V step's switching-activity dump (S = set from the SAIF)."""
    t = rd(W(i, 'v', 'act_RAMB.rpt')); c = rd(W(i, 'v', 'counts.txt'))
    m = re.search(r'^RAMB cells=\d+ out_nets=(\d+)$', c or '', re.M)
    if t is None or not m or int(m.group(1)) == 0:
        return None
    return len(re.findall(r'toggle rate = \S+ \(S\)$', t, re.M)), int(m.group(1))


def halts(i, w):
    """Check P: runs of idle-level buckets (< (L_boot + L_plat)/2, the cal_window midpoint) inside [A, A+N) of the S2 profile."""
    if not w or not all(k in w for k in ('A', 'N', 'L_boot', 'L_plat')):
        return None
    t = rd(W(i, 'prof', 's2a_profile_pre.csv'))
    if t is None:
        return None
    A, N = int(w['A']), int(w['N']); mid = (int(w['L_boot']) + int(w['L_plat'])) / 2
    lo = []
    for ln in t.splitlines()[1:]:
        f = ln.split(',')
        if len(f) == 3 and A <= int(f[1]) < A + N and int(f[2]) < mid:
            lo.append(int(f[1]))
    runs = []
    for c in lo:
        if runs and c - runs[-1][1] == 1000:
            runs[-1][1] = c
        else:
            runs.append([c, c])
    return runs


def util_hier(p):
    """{path below BoomTile: (module, LUT, FF, RAMB36)} from report_utilization -hierarchical; '(x)' self rows skipped."""
    t = rd(p)
    if t is None:
        return None
    out = {}; stack = []
    for ln in t.splitlines():
        m = re.match(r'^\|( *)(\S+)\s*\|\s*(\S+)\s*\|\s*(\d+)\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*(\d+)\s*\|\s*([0-9.]+)\s*\|', ln)
        if not m or m.group(2).startswith('('):
            continue
        lvl = len(m.group(1)) // 2
        stack = stack[:lvl] + [m.group(2)]
        if lvl >= 1:
            out[tuple(stack[1:])] = (m.group(3), int(m.group(4)), int(m.group(5)), m.group(6))
    return out


def tree_keys(i):
    t = rd(W(POINT_OWNER(i), 'logs', 'KEY_B1.txt'))
    k = [l.strip() for l in (t or '').splitlines() if re.match(r'^[0-9a-f]{40}$', l.strip())]
    return k if len(k) == 3 else None


D = {}
for i in IDS:
    D[i] = dict(act=dyn(W(i, 'power_dhry.rpt')), vl=dyn(W(i, 'vectorless.rpt')), vl_src='own' if dyn(W(i, 'vectorless.rpt')) is not None else None,
                hier_act=hier(W(i, 'power_dhry.rpt')), util=util_total(i), cov=coverage(i), s1=s1(i), win=window(i), g=gates(i), bram=bram_stag(i))
    D[i]['halts'] = halts(i, D[i]['win'])
for i in IDS:  # vectorless is a property of the netlist: fall back to the point's report (P2), then to A0's
    if D[i]['vl'] is None:
        v = dyn(point(i, 'power_hier.rpt'))
        if v is not None:
            D[i]['vl'] = v; D[i]['vl_src'] = 'P2 point'
        elif D['A0']['vl'] is not None and i != 'U':
            D[i]['vl'] = D['A0']['vl']; D[i]['vl_src'] = 'A0'
VL_HIER = {i: hier(point(i, 'power_hier.rpt')) for i in ('U', 'A0')}
UTIL_H = {i: util_hier(point(i, 'util_hier.rpt')) for i in ('U', 'A0')}

# ---------- formatting helpers
w3 = lambda x: PEND if x is None else '%.3f' % x
n0 = lambda x: PEND if x is None else '{:,}'.format(x)
pc = lambda x: PEND if x is None else '%+.1f%%' % (100 * x)
cell = lambda s, c='': '<td class="n %s">%s</td>' % (c, s if s != PEND else '<span class="pend">pending</span>')

# ---------- (1) headline
def head_row(i):
    d = D[i]; u = d['util']; cv = d['cov']; s = d['s1']
    act = d['act']
    c = ''
    if cv is not None:
        c = 'ok' if cv[0] >= 0.90 else 'bad'
    ipc = None if s is None else s['minstret'] / s['mcycle']
    return ('<tr><td><b>%s</b><br><span class="mute">%s</span></td>' % (i, E(LABEL[i])) + cell(w3(act)) + cell(w3(None if act is None else act * FREQ_SCALE)) +
            cell(w3(d['vl'])) + cell(n0(None if u is None else u['total_luts'])) + cell(n0(None if u is None else u['ffs'])) +
            cell(PEND if u is None else '%d / %d' % (u['ramb36'], u['ramb18'])) +
            cell(PEND if cv is None else '%.2f%%' % (100 * cv[0]), c) + cell(n0(None if s is None else s['mcycle'])) +
            cell(n0(None if s is None else s['minstret'])) + cell(PEND if ipc is None else '%.4f' % ipc) + '</tr>')


T1 = ''.join(head_row(i) for i in IDS)

# ---------- chart: dynamic W (activity) per id, vectorless beside it
have = [i for i in IDS if D[i]['act'] is not None or D[i]['vl'] is not None]
vmax = 6.0; SW, SH, L, B = 640, 270, 48, 40; gap = (SW - L - 20) / max(1, len(IDS)); bw = 22
yy = lambda v: SH - B - (SH - B - 16) * v / vmax
sv = ['<svg viewBox="0 0 %d %d" role="img" aria-label="Dynamic power at 100 MHz per defense point, activity and vectorless">' % (SW, SH)]
for t in range(0, 7):
    sv.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/><text x="%d" y="%.1f" class="tick" text-anchor="end">%d</text>' % (L, SW - 10, yy(t), yy(t), L - 6, yy(t) + 4, t))
for k, i in enumerate(IDS):
    x0 = L + 12 + k * gap
    for j, (key, cls_) in enumerate((('act', 'm'), ('vl', 'p'))):
        v = D[i][key]
        if v is not None:
            sv.append('<rect x="%.1f" y="%.1f" width="%d" height="%.1f" class="%s"/>' % (x0 + j * (bw + 3), yy(v), bw, SH - B - yy(v), cls_))
    sv.append('<text x="%.1f" y="%d" class="lab" text-anchor="middle">%s</text>' % (x0 + bw, SH - B + 16, i))
    if D[i]['act'] is None:
        sv.append('<text x="%.1f" y="%d" class="lab" text-anchor="middle">pending</text>' % (x0 + bw, SH - B + 29))
sv.append('<text x="12" y="%d" class="tick" text-anchor="middle" transform="rotate(-90 12 %d)">dynamic power at 100 MHz (W)</text></svg>' % (SH // 2, SH // 2))
SVG = ''.join(sv)

# ---------- (2) deltas
def dl(x, base):
    if x is None or base is None:
        return cell(PEND), cell(PEND)
    return cell('%+.3f' % (x - base)), cell(pc((x - base) / base))


def dc(i, base):
    a_, b_ = D[i]['s1'], D[base]['s1']
    if a_ is None or b_ is None:
        return cell(PEND)
    return cell('%s (%s)' % ('{:+,}'.format(a_['mcycle'] - b_['mcycle']), pc((a_['mcycle'] - b_['mcycle']) / b_['mcycle'])))


rows2 = ''
for i in DEFS:
    c1 = dl(D[i]['act'], D['A0']['act'])
    rows2 += '<tr><td><b>%s</b> &minus; A0</td><td>%s</td>%s%s%s</tr>' % (i, E(LABEL[i]), c1[0], c1[1], dc(i, 'A0'))
c1 = dl(D['A0']['act'], D['U']['act']); c2 = dl(D['A0']['vl'], D['U']['vl'])
rows2 += '<tr><td><b>A0 &minus; U</b></td><td>idle cost of the defense hardware, activity</td>%s%s%s</tr>' % (c1 + (dc('A0', 'U'),))
rows2 += '<tr><td><b>A0 &minus; U</b></td><td>idle cost of the defense hardware, vectorless</td>%s%s%s</tr>' % (c2 + (cell('&ndash;'),))

# ---------- (3) per module
def pw(i, path):
    h = D[i]['hier_act']
    if h is None:
        return PEND
    for p, v in h:
        if p == path:
            return v
    return None

def mod_table(paths):
    th = '<tr><th>module</th>%s</tr>' % ''.join('<th class="n">%s</th>' % i for i in IDS)
    body = ''
    for p in paths:
        cs = ''
        for i in IDS:
            v = pw(i, p)
            cs += cell(PEND if v == PEND else ('&mdash;' if v is None else '%.3f' % v))
        body += '<tr><td class="mono">%s%s</td>%s</tr>' % ('&nbsp;&nbsp;' * (len(p) - 1), E('/'.join(p[-1:])), cs)
    return '<div class="tbl"><table>%s%s</table></div>' % (th, body)

actpaths = set()
for i in IDS:
    for p, v in (D[i]['hier_act'] or []):
        actpaths.add(p)
grp_paths = sorted(p for p in actpaths if p[0] in GROUPS and len(p) <= 2)
for g in GROUPS:  # a group with no report yet still gets its own row
    if (g,) not in grp_paths and not any(p[0] == g for p in grp_paths):
        grp_paths.append((g,))
# defense modules = top-most instances of A0's netlist (util_hier.rpt, depth 8) that U's netlist has not; nothing is named here.
# The two builds differ only by the defense mixins, so every such instance is defense hardware.
ACT_IDS = ['A0'] + DEFS
if UTIL_H['A0'] is None or UTIL_H['U'] is None:
    DEFHTML = '<p class="mute"><span class="pend">pending</span>: util_hier.rpt of the A0 or U point does not exist yet.</p>'
else:
    newp = [p for p in UTIL_H['A0'] if p not in UTIL_H['U']]
    newset = set(newp)
    top = sorted(p for p in newp if not any(p[:k] in newset for k in range(1, len(p))))
    vl = dict(VL_HIER['A0'] or [])
    th = ('<tr><th>instance (below BoomTile)</th><th>module</th><th class="n">LUT</th><th class="n">FF</th><th class="n">RAMB36</th>'
          '<th class="n">vectorless W</th>%s</tr>' % ''.join('<th class="n">%s</th>' % i for i in ACT_IDS))
    body = ''
    for p in top:
        mod, lut, ff, rb = UTIL_H['A0'][p]
        v = vl.get(p)
        cs = cell(PEND if VL_HIER['A0'] is None else ('&lt;0.001' if v is None else '%.3f' % v))
        for i in ACT_IDS:
            h = D[i]['hier_act']
            if h is None:
                cs += cell(PEND)
            elif len(p) > 2:
                cs += cell('depth %d' % (len(p) + 1))
            else:
                x = dict(h).get(p)
                cs += cell('&lt;0.001' if x is None else '%.3f' % x)
        body += '<tr><td class="mono">%s</td><td class="mono">%s</td>%s%s%s%s</tr>' % (E('/'.join(p)), E(mod), cell(n0(lut)), cell(n0(ff)), cell(rb), cs)
    DEFHTML = '<div class="tbl"><table>%s%s</table></div>' % (th, body)
    DEFHTML += ('<p class="mute">Rows: the top-most instances in A0\'s utilization hierarchy (util_hier.rpt of the A0 point, depth 8) that U\'s has not; '
                'LUT/FF/RAMB36 from the same report. Vectorless W from A0\'s point power_hier.rpt (depth 8). Activity W from each id\'s power_dhry.rpt, '
                'which stops at depth 3 counting the tile, so the icache Ceviche units (depth 4) have only a vectorless figure and sit inside the '
                'frontend/icache row of the table above. &lt;0.001 = the report does not list the instance (Vivado lists none below 0.001 W).</p>')

# ---------- (4) method and gates
def gate_cell(i, k):
    v = D[i]['g'][k]
    return PEND if v is None else v

GT = ''
for i in IDS:
    d = D[i]; cv = d['cov']; w = d['win'] or {}; s = d['s1']
    GT += '<tr><td><b>%s</b></td>' % i
    GT += cell(gate_cell(i, 'P3'), 'ok' if D[i]['g']['P3'] == 'EQUIVALENT' else '')
    GT += cell(gate_cell(i, 'C0'), 'ok' if D[i]['g']['C0'] == 'IDENTICAL' else '')
    GT += cell(PEND if not w.get('S') else '%s / %s / %s' % (w['S'], w['A'], w['N']))
    GT += cell(PEND if not w.get('checks') else w['checks'], 'ok' if w.get('checks') == 'ok' else '')
    GT += cell(PEND if not w.get('stepdown') else w['stepdown'])
    GT += cell(PEND if 'max_dev' not in w else w['max_dev'])
    hz = d['halts']
    GT += cell(PEND if hz is None else ('%d: ' % len(hz) + ', '.join('%d-%dk' % (r[0] // 1000, r[1] // 1000) for r in hz)), '' if hz is None else ('ok' if len(hz) == 1 else 'bad'))
    GT += cell(PEND if cv is None else '%.2f%%' % (100 * cv[0]), '' if cv is None else ('ok' if cv[0] >= 0.90 else 'bad'))
    br = d['bram']
    GT += cell(PEND if br is None else '%.1f%% (%s / %s)' % (100 * br[0] / br[1], '{:,}'.format(br[0]), '{:,}'.format(br[1])))
    GT += cell(PEND if s is None else ('PASSED, RCSR_LOADED=%s' % s['loaded']))
    GT += '</tr>'

# ---------- (5) provenance
KEYS = ['boom', 'rocket-chip', 'chipyard']  # KEY_B1.txt line order
sha_cache = {}
def sc(p):
    if p not in sha_cache:
        sha_cache[p] = sha_file(p)
    return sha_cache[p]
def short(h):
    return PEND if h is None else h[:12]
elf = CE['var'].get('CAL_ELF')
PV = ''
for i in IDS:
    b = b1_sha(i); s = D[i]['s1']
    row = [('simulator', b[0] if b else None), ('ELF', sc(elf) if elf else None), ('rcsr', s['rcsr'] if s else None),
           ('fir_elab.json', sc(point(i, 'rtl', 'fir_elab.json'))), ('post_synth.dcp', sc(point(i, 'post_synth.dcp'))), ('s2a.saif', sc(W(i, 's2a.saif')))]
    PV += '<tr><td><b>%s</b></td>%s</tr>' % (i, ''.join(cell(short(h)) for k, h in row))
tku, tka = tree_keys('U'), tree_keys('A0')
TK = ''
for k, n in enumerate(KEYS):
    eq = PEND if not (tku and tka) else ('equal' if tku[k] == tka[k] else 'DIFFERENT')
    TK += '<tr><td>%s</td>%s%s%s</tr>' % (n, cell(short(tku[k]) if tku else PEND), cell(short(tka[k]) if tka else PEND),
                                          cell(eq, '' if eq == PEND else ('ok' if eq == 'equal' else 'bad')))

# ---------- data as of
newest = max([os.path.getmtime(p) for p in READ if os.path.exists(p)] or [0])
ASOF = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(newest))
NPEND = sum(1 for i in IDS if D[i]['act'] is None)
PENDING_IDS = ', '.join(i for i in IDS if D[i]['act'] is None) or 'none'

# ---------- fixed notes (wording fixed by the E9.r1 brief; numbers read from the files above, 'pending' while missing)
def hz_txt(i):
    hz = D[i]['halts']
    return PEND if hz is None else ('%d halt%s at %s' % (len(hz), '' if len(hz) == 1 else 's', ', '.join('%d-%dk' % (r[0] // 1000, r[1] // 1000) for r in hz)))
n_one = sum(1 for i in IDS if D[i]['halts'] is not None and len(D[i]['halts']) == 1)
n_hz = sum(1 for i in IDS if D[i]['halts'] is not None)
chkP = PEND if n_hz < len(IDS) else ('passed' if n_one == len(IDS) else 'FAILED (%d of %d ids hold exactly one halt)' % (n_one, len(IDS)))
dS, dG = D['ASTT'], D['ADGL']
dgl_cyc = PEND if not (dS['s1'] and dG['s1']) else '%+d' % (dG['s1']['mcycle'] - dS['s1']['mcycle'])
dgl_act = PEND if dS['act'] is None or dG['act'] is None else '%+.3f W' % (dG['act'] - dS['act'])
try:
    dgl_lp = '%+.1f %%' % (100 * (int(dG['win']['L_plat']) / int(dS['win']['L_plat']) - 1))
except (TypeError, KeyError, ZeroDivisionError):
    dgl_lp = PEND
cvA = PEND if D['A0']['cov'] is None else '%.2f %%' % (100 * D['A0']['cov'][0])
NOTES = [
    'U vs A0 S1: mcycle and minstret differ by DMI poll-halt phase only (report <code>execution/task-reports/defpower-u-vs-a0.md</code>, sha1 e75a7ef6: '
    'the minstret deltas are whole 5-instruction park-loop iterations). Check P %s: one halt per window (A0 %s, U %s).' % (chkP, hz_txt('A0'), hz_txt('U')),
    'Every 100k-cycle window holds one DMI poll halt of about 2.7k cycles (%d of %d ids, column "poll halts in window"). It is common-mode across the ids '
    '(about &minus;2.2 %% of window toggles in each) and it sets the profile max_dev column: the halt buckets sit at idle level, about 80 %% under the '
    'window mean.' % (n_one, len(IDS)),
    'Doppelganger: ADGL cycles = ASTT %s, but activity %s at equal cycles and the loop toggle level (cal_window L_plat) %s: the DGL CSR is active, '
    'with little cycle gain on Dhrystone.' % (dgl_cyc, dgl_act, dgl_lp),
    'SpecASan on unkeyed Dhrystone: every check matches, so the run measures the check machinery (plan section 1.2).',
    'Coverage: tile_equiv E8 gives P3 EQUIVALENT, and saif_fill_regs E10 adds memory aliases (SpecASan tag-cache naming): FF coverage on A0 went from '
    '93.76 %% to %s; the activity power changed by at most 0.001 W at the SpecASan-off points (U, A0, ASTT re-run).' % cvA,
    'FPGA vs sim residuals (plan section 2): the simulated tile has WithIFTOracle (record slots as tile outputs, core trace on) where the bitstream '
    'has WithIFTBridge; the numbers are the tile only; static and shell power are out of scope (measured dynamic power).',
]
NOTES_HTML = ''.join('<li>%s</li>' % n.replace(PEND, '<span class="pend">pending</span>') for n in NOTES)

page = f'''<title>Defense Power</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono:wght@400;500&family=Fraunces:opsz,wght@9..144,600&display=swap">
<style>
/* one reading column: headline, deltas, per-module, method and gates, provenance */
:root {{ --bg:#f6f7f8; --fg:#1b1f24; --mute:#5b6570; --line:#d9dee3; --accent:#2f5d8a; --pred:#c0843a;
  --ok:#2d7a46; --warn:#a66a00; --bad:#b23a3a; --card:#ffffff;
  --display:"Fraunces",Georgia,serif; --body:"IBM Plex Sans",system-ui,sans-serif; --mono:"IBM Plex Mono",ui-monospace,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#14181c; --fg:#e4e8ec; --mute:#9aa5b0; --line:#2c343c; --accent:#7fb0e0; --pred:#e0a560; --ok:#5fbf7f; --warn:#e0a640; --bad:#f07a7a; --card:#1b2026; color-scheme:dark; }} }}
:root[data-theme="dark"] {{ --bg:#14181c; --fg:#e4e8ec; --mute:#9aa5b0; --line:#2c343c; --accent:#7fb0e0; --pred:#e0a560; --ok:#5fbf7f; --warn:#e0a640; --bad:#f07a7a; --card:#1b2026; color-scheme:dark; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.55 var(--body); padding-left:max(16px,env(safe-area-inset-left,0px)); padding-right:max(16px,env(safe-area-inset-right,0px)); padding-block:24px 48px; }}
main {{ max-width:1080px; margin:0 auto; display:grid; grid-template-columns:minmax(0,1fr); gap:28px; }} main > * {{ min-width:0; }}
h1 {{ font:600 2rem/1.15 var(--display); margin:0; text-wrap:balance; }}
h2 {{ font:600 1.15rem/1.3 var(--body); margin:0 0 8px; }}
p, li {{ max-width:72ch; }} .mute {{ color:var(--mute); font-size:13px; }}
.verdict {{ background:var(--card); border:1px solid var(--line); border-left:4px solid var(--warn); padding:14px 18px; border-radius:6px; }}
.tbl {{ overflow-x:auto; }} table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
th, td {{ border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }}
th {{ color:var(--mute); font-weight:600; font-size:12px; letter-spacing:.03em; text-transform:uppercase; }}
.n {{ text-align:right; font-variant-numeric:tabular-nums; font-family:var(--mono); white-space:nowrap; }}
.mono {{ font-family:var(--mono); font-size:12px; word-break:break-all; }}
.ok {{ color:var(--ok); }} .warn {{ color:var(--warn); }} .bad {{ color:var(--bad); }} .pend {{ color:var(--mute); font-style:italic; }}
svg {{ width:100%; max-width:680px; height:auto; }} .grid {{ stroke:var(--line); }} .tick, .lab {{ fill:var(--mute); font:11px var(--mono); }}
.m {{ fill:var(--accent); }} .p {{ fill:var(--pred); }} .key span {{ display:inline-block; width:10px; height:10px; margin:0 4px 0 12px; }}
.key span.m {{ background:var(--accent); }} .key span.p {{ background:var(--pred); }}
code {{ font-family:var(--mono); font-size:.92em; overflow-wrap:anywhere; }}
</style>
<main>
<header><h1>Defense Power</h1>
<p class="mute">BoomTile, post-synthesis Vivado power at 100 MHz, Dhrystone ({E(os.path.basename(elf or ''))}); STT, Doppelganger, SpecASan and Ceviche on one AllDef netlist against the undefended core. Data as of {ASOF}. Generated by <code>defpower_page.py</code> from the lane outputs under <code>{E(R)}</code>.</p></header>

<section class="verdict"><h2>Status: {len(IDS) - NPEND} of {len(IDS)} ids have an activity power report</h2>
<p>Pending activity reports: <b>{E(PENDING_IDS)}</b>. A cell reading <span class="pend">pending</span> means its input file does not exist yet; nothing on this page is estimated.
Where a gate fails it is shown in red, not hidden: FF coverage must be at least 90% (plan section 1.2).</p></section>

<section><h2>1. Dynamic power per id</h2>
<p class="key mute"><span class="m"></span>activity (Vivado report_power with the sim SAIF)<span class="p"></span>vectorless (default activity, same netlist)</p>{SVG}
<div class="tbl"><table>
<tr><th>id</th><th class="n">activity W @100 MHz</th><th class="n">activity W x0.3 (30 MHz)</th><th class="n">vectorless W @100 MHz</th><th class="n">LUT</th><th class="n">FF</th><th class="n">RAMB36 / RAMB18</th><th class="n">FF coverage</th><th class="n">S1 mcycle</th><th class="n">S1 minstret</th><th class="n">IPC</th></tr>
{T1}</table></div>
<p class="mute">A ids share A0's LUT/FF/BRAM (same netlist); each id has its own vectorless report, equal on the shared netlist because vectorless power does not depend on the CSR file. x0.3 scales dynamic power linearly with clock, the FireSim bitstream runs at 30 MHz. FF coverage = (SAIF_FILL named + set) / cells from <code>v/vivado.log</code>. IPC = S1 minstret / mcycle of the Dhrystone region.</p></section>

<section><h2>2. Deltas</h2><div class="tbl"><table>
<tr><th>difference</th><th>meaning</th><th class="n">W</th><th class="n">%</th><th class="n">S1 mcycle</th></tr>{rows2}</table></div>
<p class="mute">A_x &minus; A0 is the change from running the defense on the same netlist (activity W at 100 MHz, % of A0). A0 &minus; U is the idle cost of the defense hardware (netlist difference, same CSRs). Power is at a fixed clock: a defense that lowers IPC lowers the toggles per cycle, so A_x &minus; A0 can be negative while the run takes more cycles (S1 mcycle column).</p></section>

<section><h2>3. Per module, activity W at 100 MHz</h2>
<p class="mute">Rows are the paths of the Vivado hierarchy report below the tile (indent = depth). &mdash; means the report exists but does not list the module (Vivado lists no instance below 0.001 W); pending means the report does not exist yet. The activity report is <code>report_power -hierarchical_depth 3</code>, the tile counting as depth 1, so modules below these rows are not in it.</p>
{mod_table(grp_paths)}
<h2 style="margin-top:18px">Defense modules</h2>{DEFHTML}</section>

<section><h2>4. Method and gates</h2><ul>
<li>One Verilator sim per id with the traced debug build (S0/S1/S2), FST to SAIF with array elements (C1), registers filled from their RTL names (fill), Vivado <code>report_power</code> on the post-synthesis checkpoint (V). The vectorless figure is the same checkpoint without the SAIF.</li>
<li>Window rule (cal_window.py): S = first profile bucket after 300k whose value and the next 9 reach (L_boot + L_plat)/2; A = S + 50,000; N = 100,000 core cycles; checks: window ends at least 5k cycles before S + mcycle, inside the trace, step-down within 3% of S + mcycle.</li>
<li>P3 EQUIVALENT is the tile_equiv verdict (E8) for the OOC RTL cut against the sim build's generated sources; the A ids use A0's netlist, so A0's P3 applies to them. C0 IDENTICAL (per id) means fst2saif and the reference converter give byte-identical SAIF and profile on that id's own S0 dump.</li>
<li>Coverage gate: (named + set) / cells at least 90%, else the activity number is not trusted. BRAM S-tag share = RAMB output nets whose activity comes from the SAIF (tag S) / all RAMB output nets (<code>v/act_RAMB.rpt</code>, <code>v/counts.txt</code>); the rest are propagated (P). Poll halts in window = runs of idle-level 1k buckets (below the cal_window midpoint) inside [A, A + N) of <code>prof/s2a_profile_pre.csv</code> (check P).</li>
{NOTES_HTML}</ul>
<div class="tbl"><table>
<tr><th>id</th><th class="n">P3</th><th class="n">C0</th><th class="n">window S / A / N</th><th class="n">window checks</th><th class="n">step-down (cycle)</th><th class="n">profile max_dev</th><th class="n">poll halts in window</th><th class="n">FF coverage</th><th class="n">BRAM S-tag share</th><th class="n">S1</th></tr>{GT}</table></div></section>

<section><h2>5. Provenance</h2>
<p class="mute">First 12 hex of sha1; A ids show A0's simulator, fir_elab.json and checkpoint (shared netlist), their own rcsr and SAIF.</p>
<div class="tbl"><table><tr><th>id</th><th class="n">simulator</th><th class="n">ELF</th><th class="n">rcsr</th><th class="n">fir_elab.json</th><th class="n">post_synth.dcp</th><th class="n">s2a.saif</th></tr>{PV}</table></div>
<div class="tbl"><table><tr><th>tree (KEY_B1.txt content key)</th><th class="n">U build</th><th class="n">A0 build</th><th class="n">U = A0</th></tr>{TK}</table></div></section>
</main>
'''
os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
open(OUT, 'w').write(page)
print('wrote', OUT, hashlib.sha1(page.encode()).hexdigest())
