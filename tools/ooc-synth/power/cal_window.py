#!/usr/bin/env python3
"""cal_window.py -- dhrystone power window from the S2 toggle profile (power calibration).

Why: the S1-only formula A = T_pass/R - mcycle + 50k lands 330-390k core cycles too late, because T_pass also counts the
result printfs and the exit after the Proc loop (reports/power-cal-window-check.md 7d46ad8f).  The loop start S is read
from the converter profile (toggles per 1k core cycles, cycles 0..end of the trace) instead.

Rule (report section 4):
  L_boot = mean of buckets with first_cycle in [100k, 400k);  L_plat = max mean over consecutive 20k blocks (20 buckets);
  S = first bucket after 300k whose value and the next 9 are all >= (L_boot + L_plat)/2;  A = S + 50,000;  N = 100,000.
  Checks: A + N <= S + mcycle - 5,000 (window ends inside the loop); A + N <= last profiled cycle (inside the trace);
          if a step-down below the midpoint exists after S, |stepdown - S - mcycle| <= 3% of mcycle.
Usage : cal_window.py --profile W/s2a_profile.csv --mcycle M [--n 100000] [--write W/window.env --r 2 --maxc X]
Output: one line 'S=.. A=.. N=.. stepdown=.. checks=ok|FAIL(..)'; with --write the env file (R A N MAXC); exit 1 on a failed check.
"""
import argparse, csv, sys

ap = argparse.ArgumentParser()
ap.add_argument('--profile', required=True); ap.add_argument('--mcycle', type=int, required=True)
ap.add_argument('--n', type=int, default=100000); ap.add_argument('--write'); ap.add_argument('--r', type=int); ap.add_argument('--maxc', type=int)
a = ap.parse_args()
rows = sorted((int(r['first_cycle']), int(r['toggles'])) for r in csv.DictReader(open(a.profile)))
cyc = [c for c, _ in rows]; val = [t for _, t in rows]
boot = [t for c, t in rows if 100000 <= c < 400000]
if not boot: sys.exit('no buckets in [100k, 400k)')
l_boot = sum(boot) / len(boot)
l_plat = max(sum(val[i:i + 20]) / 20 for i in range(0, len(val) - 19))
mid = (l_boot + l_plat) / 2
S = next((cyc[i] for i in range(len(val) - 9) if cyc[i] > 300000 and all(v >= mid for v in val[i:i + 10])), None)
if S is None: sys.exit('no step-up found')
A = S + 50000; end = A + a.n
i0 = cyc.index(S)
down = next((cyc[i] for i in range(i0, len(val) - 9) if all(v < mid for v in val[i:i + 10])), None)
fails = []
if end > S + a.mcycle - 5000: fails.append('window ends after the loop')
if end > cyc[-1] + 1000: fails.append('window ends after the trace')
if down is not None and abs(down - S - a.mcycle) > 0.03 * a.mcycle: fails.append('step-down %d vs S+mcycle %d' % (down, S + a.mcycle))
print('S=%d A=%d N=%d L_boot=%.0f L_plat=%.0f stepdown=%s checks=%s' % (S, A, a.n, l_boot, l_plat, down, 'ok' if not fails else 'FAIL(' + '; '.join(fails) + ')'))
if a.write and not fails:
    open(a.write, 'w').write('R=%d\nA=%d\nN=%d\nMAXC=%d\n' % (a.r, A, a.n, a.maxc))
sys.exit(1 if fails else 0)
