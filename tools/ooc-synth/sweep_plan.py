#!/usr/bin/env python3
"""Plan for sweep.sh.  usage: sweep_plan.py MANIFEST WORK [--clamp-verified]
Prints TSV lines: action<TAB>point<TAB>module<TAB>variant<TAB>params<TAB>mode<TAB>detail
action: RUN | SKIP_DONE | HOLD_CLAMP | SKIP_STATUS.   mode: all (private clamp jar) | auto (elaborate.sh decides: plain jar for max points).
Rows are read with csv (point ids contain commas).  A point is SKIP_DONE when its result.json is complete (status ok, has total)
and the rtl_sha1 recorded in <point>/.synth_rtl_sha1 equals rtl/elab.json's rtl_sha1.  Read-only; no process is started."""
import csv, json, os, sys

man, work = sys.argv[1], sys.argv[2]
verified = "--clamp-verified" in sys.argv[3:]


def complete(pt):
    try:
        r = json.load(open(os.path.join(pt, "result.json")))
        if r.get("status") != "ok" or "total" not in r:
            return False
        want = json.load(open(os.path.join(pt, "rtl", "elab.json")))["rtl_sha1"]
        return open(os.path.join(pt, ".synth_rtl_sha1")).read().strip() == want
    except Exception:
        return False


for r in csv.DictReader(open(man, newline="")):
    st = r["status"]
    pt = os.path.join(work, "points", r["point"])
    f = [r["point"], r["module"], r["variant"], r["params"]]
    if st not in ("pending", "needs_clamp_fix"):
        print("\t".join(["SKIP_STATUS"] + f + ["-", st])); continue
    if st == "needs_clamp_fix" and not verified:
        print("\t".join(["HOLD_CLAMP"] + f + ["-", "needs --clamp-verified"])); continue
    if complete(pt):
        print("\t".join(["SKIP_DONE"] + f + ["-", "result.json complete, rtl_sha1 matches"])); continue
    print("\t".join(["RUN"] + f + ["all" if st == "needs_clamp_fix" else "auto", st]))
