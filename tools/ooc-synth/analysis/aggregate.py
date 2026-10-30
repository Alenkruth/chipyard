#!/usr/bin/env python3
"""Aggregate result.json files into the raw-column tables. Run as: python3 -I aggregate.py --work DIR --out DIR
Outputs: areas_raw.csv ift_overhead.csv core_vs_sum.csv reconf_overhead.csv fuzzer_area.json
Raw columns only; no weighted score. Deterministic (sorted, no timestamps)."""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *

# Modules that make up one core instance (one each); Core itself is the whole-core measurement.
CORE_PARTS = ["FetchBuffer", "Rob", "FetchTargetQueue", "BoomRAS", "RegfileInt", "RegfileFp", "FreelistInt",
              "FreelistFp", "IssueInt", "IssueMem", "IssueFp", "Lsu", "BTB", "Tage", "ICache", "DCache"]


def vec(d):
    return [col(d, c) for c in COLS]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest", help="optional manifest.csv; used for max-size flags")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    results = load_results(a.work)
    # manifest: max-size flags, and points on hold (needs_clamp_fix / needs_fix / infeasible): their results, if any,
    # come from an unfixed tree and are kept in areas_raw.csv only, never in the derived tables.
    maxpts, hold = set(), set()
    if a.manifest:
        for r in read_csv(a.manifest):
            if r["opt_max"] == "true":
                maxpts.add(r["point"])
            if r["status"] in HOLD_STATUSES:
                hold.add(r["point"])
    ok = [r for r in results if r.get("status") == "ok" and r["point"] not in hold]
    by = {}
    for r in ok:
        m, v, p = split_point(r["point"])
        by[(m, v, p)] = r

    # ---- areas_raw.csv
    rows = []
    for r in results:
        m, v, p = split_point(r["point"])
        rows.append([r["point"], m, v, p, width_of(p), r.get("status", ""), r.get("wall_s", ""), r.get("peak_mb", "")]
                    + vec(r.get("total")))
    write_csv(os.path.join(a.out, "areas_raw.csv"),
              ["point", "module", "variant", "params", "width", "status", "wall_s", "peak_mb"] + COLS, rows)

    # ---- ift_overhead.csv  (scope B: ift/ift_nobridge only at the max size per module/width, plus the smallest-size spot check)
    # delta         : IFT overhead at the max-size point = (ift_nobridge - base) for bridge-pinned modules (BTB, Tage, ICache, DCache, BoomRAS),
    #                 else (ift - base) where ift exists, else (ift_nobridge - base) (full-mode width 1/2: ift does not elaborate).
    # scaling_delta : (ift_nobridge - base) at a non-max size (the one smallest-size spot check per module); shows how the overhead scales.
    # tap_bridge    : (ift - ift_nobridge) at the max-size point (IFT state taps / bridge), only where both exist.
    # ift_child     : children flagged ift:true; variant_pair = "children(<variant>)".
    # last column d_total_luts_pct_of_base = 100 * delta total_luts / base total_luts (delta and scaling_delta rows).
    hdr = ["row_type", "module", "params", "variant_pair", "inst", "child_module"] + COLS + ["d_total_luts_pct_of_base"]
    rows = []
    mods = sorted({k[0] for k in by})
    for m in mods:
        pinned = m in NOBRIDGE_MODULES
        for p in sorted({k[2] for k in by if k[0] == m and k[1] in ("ift", "ift_nobridge")}):
            nb, ft, b = by.get((m, "ift_nobridge", p)), by.get((m, "ift", p)), by.get((m, "base", p))
            iv = "ift_nobridge" if (pinned or ft is None) else "ift"
            i = by.get((m, iv, p))
            if i is not None and b is not None:
                ismax = (m + "/base/" + p in maxpts) if maxpts else True
                bt = col(b["total"], "total_luts")
                d = [x - y for x, y in zip(vec(i["total"]), vec(b["total"]))]
                rows.append(["delta" if ismax else "scaling_delta", m, p, iv + "-base", "", ""] + d
                            + [round(100.0 * d[0] / bt, 3) if bt else ""])
            if nb is not None and ft is not None:
                rows.append(["tap_bridge", m, p, "ift-ift_nobridge", "", ""] + [x - y for x, y in zip(vec(ft["total"]), vec(nb["total"]))] + [""])
            for v in ("ift", "ift_nobridge"):
                r = by.get((m, v, p))
                if r is None:
                    continue
                for ch in sorted(r.get("children", []), key=lambda c: (c.get("inst", ""), c.get("module", ""))):
                    if ch.get("ift"):
                        rows.append(["ift_child", m, p, "children(%s)" % v, ch.get("inst", ""), ch.get("module", "")] + vec(ch) + [""])
    write_csv(os.path.join(a.out, "ift_overhead.csv"), hdr, rows)

    # ---- reconf_overhead.csv : reconf_X - X at max-size point
    rows = []
    for (m, v, p) in sorted(by):
        if v not in ("reconf_base", "reconf_ift"):
            continue
        ref = by.get((m, v[len("reconf_"):], p))
        if ref is None:
            continue
        rows.append([m, v[len("reconf_"):], p] + [x - y for x, y in zip(vec(by[(m, v, p)]["total"]), vec(ref["total"]))]
                    + [col(ref["total"], "total_luts") and round(100.0 * (col(by[(m, v, p)]["total"], "total_luts") - col(ref["total"], "total_luts")) / col(ref["total"], "total_luts"), 3)])
    write_csv(os.path.join(a.out, "reconf_overhead.csv"),
              ["module", "reference_variant", "params"] + ["d_" + c for c in COLS] + ["d_total_luts_pct"], rows)

    # ---- core_vs_sum.csv : one block per (variant, width, width_mode).
    # full: every module taken at the core width. dispatch_only: issue/regfile modules taken at the max width
    # (issue ports/FUs stay at max), all others at the core width. sum_basis records this.
    ISSUE_PARTS = ("IssueInt", "IssueMem", "IssueFp", "RegfileInt", "RegfileFp")
    allw = sorted({int(width_of(k[2])) for k in by if width_of(k[2]).isdigit()})
    wmax = str(allw[-1]) if allw else ""
    rows = []
    cores = sorted({(v, int(width_of(p)), parse_params(p).get("width_mode", "full")) for (m, v, p) in by if m == "Core" and width_of(p).isdigit()},
                   key=lambda t: (VARIANTS.index(t[0]) if t[0] in VARIANTS else 99, t[0], -t[1], t[2]))
    for variant, wi, mode in cores:
        w = str(wi)
        cp = "width=%s" % w + (",width_mode=dispatch_only" if mode == "dispatch_only" else "")
        core = by[("Core", variant, cp)]["total"]
        tot = [0] * len(COLS)
        missing = []
        for part in CORE_PARTS:
            pw = wmax if (mode == "dispatch_only" and part in ISSUE_PARTS) else w
            # variant preference: the core's own variant, then the other IFT flavour (ift and ift_nobridge differ only by the
            # taps; at width<4 modules exist only as ift_nobridge)
            prefs = [variant] + {"ift": ["ift_nobridge"], "ift_nobridge": ["ift"]}.get(variant, [])
            cands = []
            for pv in prefs:
                cands = [r for k, r in by.items() if k[0] == part and k[1] == pv and
                         (k[0] + "/" + k[1] + "/" + k[2] in maxpts if maxpts else True) and width_of(k[2]) in (pw, "")]
                if not maxpts:  # no manifest: choose the point with largest total_luts at this width
                    cands = sorted(cands, key=lambda r: -col(r["total"], "total_luts"))[:1]
                if cands:
                    break
            if len(cands) != 1:
                missing.append(part); continue
            tot = [x + y for x, y in zip(tot, vec(cands[0]["total"]))]
        basis = "all modules at width %s" % w if mode == "full" else "issue/regfile at width %s, others at width %s" % (wmax, w)
        cv = vec(core)
        pre = [variant, w, mode]
        rows.append(["core_measured"] + pre + ["", basis] + cv)
        rows.append(["sum_of_modules"] + pre + [("INCOMPLETE:" + ";".join(missing) if missing else ""), basis] + tot)
        rows.append(["residual"] + pre + ["", basis] + [x - y for x, y in zip(cv, tot)])
        rows.append(["residual_pct_of_core"] + pre + ["", basis] + [round(100.0 * (x - y) / x, 3) if x else "" for x, y in zip(cv, tot)])
    write_csv(os.path.join(a.out, "core_vs_sum.csv"), ["row", "variant", "width", "width_mode", "missing_parts", "sum_basis"] + COLS, rows)

    # ---- fuzzer_area.json : base variant only
    fa = {}
    for (m, v, p) in sorted(by):
        if v == "base" and m != "Core":
            fa.setdefault(m, {})[p] = {c: col(by[(m, v, p)]["total"], c) for c in COLS}
    with open(os.path.join(a.out, "fuzzer_area.json"), "w") as f:
        json.dump(fa, f, indent=1, sort_keys=True); f.write("\n")
    print("ok points: %d of %d result.json" % (len(ok), len(results)))


if __name__ == "__main__":
    main()
