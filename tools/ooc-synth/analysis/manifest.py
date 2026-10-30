#!/usr/bin/env python3
"""Manifest generator: enumerates every OOC point from the CoreFuzzing.scala option lists.

  manifest.py gen [--options-json J | --scala S] [--out manifest.csv] [--widths 4,2,1]
  manifest.py set-status MANIFEST POINT STATUS
  manifest.py release MANIFEST OLD_STATUS [NEW_STATUS=pending]
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *

# module -> (dims: [(param, option-list name, dedupe)], width_dep, elab_kind, evidence)
# Option lists are NEVER copied here; only the names of the lists in CoreFuzzing.scala.
MODULES = {
 "FetchBuffer": ([("entries", "fetchBufferEntryOptions")], True, "chisel",
   "fetch-buffer.scala: 56 coreWidth/fetchWidth/decodeWidth uses; dequeue lanes = coreWidth"),
 "Rob": ([("entries", "robEntryOptions")], True, "chisel",
   "rob.scala: 123 uses; rows = entries/coreWidth, retireWidth=decodeWidth (parameters.scala:181)"),
 "FetchTargetQueue": ([("entries", "ftQueueEntryOptions")], True, "chisel",
   "fetch-target-queue.scala:134,151 debug/secret ports are Vec(coreWidth)"),
 "BoomRAS": ([("entries", "rasEntryCountOptions")], True, "chisel",
   "bpd/ras.scala:49-50,134 late_write ports Vec(coreWidth + memWidth)"),
 "RegfileInt": ([("pregs", "pregFileSizeOptions")], True, "chisel",
   "regfile.scala:132-141 read/write port count from issue widths (IQ dispatch/issue width scales with coreWidth)"),
 "RegfileFp": ([("pregs", "fpPregFileSizeOptions")], True, "chisel",
   "same as RegfileInt; FP list has duplicate 40 (rung 4), deduplicated"),
 "FreelistInt": ([("pregs", "pregFileSizeOptions")], True, "chisel",
   "rename-freelist.scala:22,38-45 plWidth ports (= coreWidth)"),
 "FreelistFp": ([("pregs", "fpPregFileSizeOptions")], True, "chisel",
   "same as FreelistInt; duplicate 40 deduplicated"),
 "IssueInt": ([("entries", "issueQueueEntryOptions")], True, "chisel",
   "issue-unit.scala: 31 width uses; dispatchWidth <= coreWidth (parameters.scala:630)"),
 "IssueMem": ([("entries", "issueQueueEntryOptions")], True, "chisel", "same as IssueInt"),
 "IssueFp": ([("entries", "issueQueueEntryOptions")], True, "chisel", "same as IssueInt"),
 "Lsu": ([("ldq", "ldQueueEntryOptions"), ("stq", "stQueueEntryOptions")], True, "fullconfig",
   "lsu.scala: 127 coreWidth/memWidth uses (dispatch lanes, commit lanes, ports)"),
 "BTB": ([("sets", "btbSetOptions"), ("ways", "btbWayOptions")], False, "chisel",
   "bpd/btb.scala:29 only fetchWidth (fixed 8 in WithFuzzingBoom config-mixins.scala:549), no coreWidth"),
 "Tage": ([("tables", "tagetableCountOptions")], False, "chisel",
   "bpd/tage.scala: 0 coreWidth/decodeWidth/memWidth uses"),
 "ICache": ([("sets", "icacheSetOptions"), ("ways", "cacheWayOptions")], False, "fullconfig",
   "icache.scala: 0 coreWidth uses; fetchWidth fixed"),
 "DCache": ([("sets", "dcacheSetOptions"), ("ways", "cacheWayOptions")], True, "fullconfig",
   "dcache.scala/mshrs.scala: ports are Vec(memWidth); memWidth follows the issue width in full width_mode (agent A2 section 7), so DCache is width-dependent"),
}
# Extra DCache replacement policy points (plan section 2). Not in CoreFuzzing.scala, so the names live here.
# Width-dependent modules whose non-max sizes are run at the max width only (DCache: memWidth follows width, but the
# fuzzer table needs size scaling at the built width; owner scope B keeps the count near 330).
WIDTH_AT_MAX_ONLY = ["DCache"]
DCACHE_REPL_EXTRA = ["trulru", "random"]
# LSU grid corners checked for additivity (plan section 3); (max,max) already in the one-factor sweep.
HEADER = ["point", "module", "variant", "params", "width", "width_mode", "width_dep", "elab_kind", "kind", "is_max", "opt_max", "status", "note"]


def uniq(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x); out.append(x)
    return out


def module_options(mod, opts):
    dims, _, _, _ = MODULES[mod]
    out = []
    for pname, lname, in dims:
        if lname not in opts:
            raise SystemExit("option list %s (needed by %s) not found" % (lname, mod))
        out.append((pname, uniq(opts[lname])))
    return out


def fmt(pairs, width):
    s = ",".join("%s=%s" % kv for kv in pairs)
    if width:
        s += ",width=%s" % width
    return s


def sweep_points(mod, opts, widths, wdep):
    """One-factor-at-a-time over multi-dim modules (others at max), except caches/BTB: full grid."""
    dims = module_options(mod, opts)
    maxes = {p: max(v) for p, v in dims}
    full_grid = mod in ("BTB", "ICache", "DCache")
    combos = []
    if full_grid:
        import itertools
        for vals in itertools.product(*[v for _, v in dims]):
            combos.append((tuple(zip([p for p, _ in dims], vals)), "sweep"))
    else:
        for p, v in dims:
            for x in v:
                d = dict(maxes); d[p] = x
                combos.append((tuple((q, d[q]) for q, _ in dims), "sweep"))
        if mod == "Lsu":
            (lp, lv), (sp, sv) = dims
            for a in (min(lv), max(lv)):
                for b in (min(sv), max(sv)):
                    combos.append((((lp, a), (sp, b)), "lsu_corner"))
    seen, res = set(), []
    for c, kind in combos:
        if c in seen:
            continue
        seen.add(c)
        res.append((c, kind))
    out = []
    for c, kind in res:
        ws_ = widths if wdep else [None]
        if mod in WIDTH_AT_MAX_ONLY and c != tuple((p, maxes[p]) for p, _ in dims):
            ws_ = [max(widths)]  # width scaling only matters (and is only run) at the max geometry
        for w in ws_:
            out.append((c, w, kind))
    if mod == "DCache":
        mx = tuple((p, maxes[p]) for p, _ in dims)
        for r in DCACHE_REPL_EXTRA:
            out.append((mx + (("repl", r),), max(widths) if wdep else None, "repl_policy"))
    return out


def infeasible_note(mod, params):
    d = parse_params(params)
    for (m, k, v), why in sorted(INFEASIBLE.items()):
        if m == mod and d.get(k) == v:
            return why
    return ""


def is_pow2(n):
    return n > 0 and n & (n - 1) == 0


def smallest_combo(mod, dm):
    """Smallest feasible size combination (infeasible values skipped, e.g. FetchBuffer entries=8)."""
    out = []
    for p, v in dm:
        ok = [x for x in v if (mod, p, str(x)) not in INFEASIBLE]
        out.append((p, min(ok)))
    return tuple(out)


def needs_clamp(mod, c, maxes):
    geo = {p: v for p, v in c if p != "repl"}
    if geo == dict(maxes):
        return False
    if mod in CLAMP_MODULES:
        return True
    if mod in CLAMP_POW2_OK:
        return any(not is_pow2(int(v)) for v in geo.values())
    return False


def build(opts, widths, wdep_override):
    """Scope B (owner 2026-10-28): base at ALL sizes/widths; ift, ift_nobridge, reconf_* only at max size per module/width,
    plus one ift_nobridge spot check at the smallest feasible size of each module (max width)."""
    rows = []
    wmax = str(max(widths))
    for mod in sorted(MODULES):
        dims, wdep, ek, _ = MODULES[mod]
        wdep = wdep or mod in wdep_override
        dm = module_options(mod, opts)
        maxes = tuple((p, max(v)) for p, v in dm)
        mins = smallest_combo(mod, dm)
        pinned = mod in NOBRIDGE_MODULES
        for c, w, kind in sweep_points(mod, opts, widths, wdep):
            ws = str(w) if w is not None else ""
            optmax = (c == maxes)
            is_max = (optmax and (not wdep or ws == wmax))
            at_wmax = (not wdep) or ws == wmax
            vs = ["base"]
            if optmax:
                if at_wmax:
                    vs += ["ift", "ift_nobridge"] if pinned else ["ift"]
                else:
                    vs += ["ift_nobridge"]  # ift (taps on) does not elaborate at full-mode width 1/2 (dcache.scala:1775)
            elif c == mins and at_wmax and kind != "repl_policy":
                vs += ["ift_nobridge"]  # scaling spot check at the smallest size
            for variant in vs:
                rows.append([mod, variant, fmt(c, ws), ws, "", wdep, ek, kind, is_max, optmax, needs_clamp(mod, c, maxes)])
        for variant in ("reconf_base", "reconf_ift"):
            ws = wmax if wdep else ""
            rows.append([mod, variant, fmt(maxes, ws), ws, "", wdep, ek, "reconf_max", True, True, False])
    # whole core (all option dims at max): width_mode full (everything scales) and dispatch_only (decode/rename/dispatch scale,
    # issue ports/FUs stay at max); dispatch_only at max width equals full, so omitted.
    # width 4: base, ift, ift_nobridge (tap row), reconf_*; width<4 full: base, ift_nobridge; dispatch_only: base, ift.
    for w in widths:
        full = ["base", "ift", "ift_nobridge"] if w == max(widths) else ["base", "ift_nobridge"]
        for variant in full:
            rows.append(["Core", variant, "width=%s" % w, str(w), "full", True, "fullconfig", "core", w == max(widths), True, False])
        if w != max(widths):
            for variant in ("base", "ift"):
                rows.append(["Core", variant, "width=%s,width_mode=dispatch_only" % w, str(w), "dispatch_only", True, "fullconfig",
                             "core_dispatch_only", False, True, False])
    for variant in ("reconf_base", "reconf_ift"):
        rows.append(["Core", variant, "width=%s" % max(widths), str(max(widths)), "full", True, "fullconfig", "reconf_max", True, True, False])
    out = []
    for mod, variant, params, ws, wm, wd, ek, kind, ismax, optmax, clamp in rows:
        why = infeasible_note(mod, params)
        note = why
        status = "infeasible" if why else "pending"
        if not why and variant == "ift_nobridge" and mod in NEEDS_FIX_MODULES and ws == "1" and wm != "dispatch_only":
            status = "needs_fix"
            note = "needs staged rob.scala:474 lsuWbPorts fix (boom-ooc-static-fixes.diff); not a failure"
        elif not why and clamp and variant in ("base", "ift_nobridge"):
            status = "needs_clamp_fix"
            note = "non-max static size mis-elaborates without staged clamp (boom-ooc-static-fixes.diff)"
        if variant == "ift_nobridge" and not optmax and not why:
            note = (note + "; " if note else "") + "taps off, in-core IFT on; smallest-size spot check"
        elif not note:
            if variant == "ift_nobridge":
                note = "taps off, in-core IFT on"
            elif variant == "ift" and mod in NOBRIDGE_MODULES:
                note = "taps on, max geometry only"
        out.append(["%s/%s/%s" % (mod, variant, params), mod, variant, params, ws, wm, str(bool(wd)).lower(), ek, kind,
                    str(bool(ismax)).lower(), str(bool(optmax)).lower(), status, note])
    out.sort(key=lambda r: r[0])
    return out


def counts(rows):
    tab = {}
    for r in rows:
        tab.setdefault(r[1], {}).setdefault(r[2], 0)
        tab[r[1]][r[2]] += 1
    return tab


def cmd_gen(a):
    opts, src = load_options(a.options_json, a.scala)
    widths = [int(x) for x in a.widths.split(",")] if a.widths else opts.get("coreWidthOptions", [4, 2, 1])
    rows = build(opts, widths, set(a.width_dep or []))
    write_csv(a.out, HEADER, rows)
    print("options source:", src)
    print("widths:", widths)
    fmtrow = "%-18s" + " %12s" * (len(VARIANTS) + 1)
    print(fmtrow % tuple(["module"] + VARIANTS + ["total"]))
    tab = counts(rows)
    tot = dict.fromkeys(VARIANTS, 0)
    for m in sorted(tab):
        t = tab[m]
        print(fmtrow % tuple([m] + [t.get(v, 0) for v in VARIANTS] + [sum(t.values())]))
        for v in VARIANTS:
            tot[v] += t.get(v, 0)
    print(fmtrow % tuple(["ALL"] + [tot[v] for v in VARIANTS] + [len(rows)]))
    mod_rows = [r for r in rows if r[1] != "Core"]
    print("module rows (excluding Core): %d ; Core rows: %d" % (len(mod_rows), len(rows) - len(mod_rows)))
    inf = [r for r in rows if r[11] == "infeasible"]
    print("infeasible (listed, not run): %d ; to run: %d" % (len(inf), len(rows) - len(inf)))
    for r in inf:
        print("  infeasible:", r[0], "-", r[12])
    sts = sorted({r[11] for r in rows})
    print("\nper variant x status:")
    for v in VARIANTS:
        print("  %-13s %s" % (v, " ".join("%s=%d" % (s, sum(1 for r in rows if r[2] == v and r[11] == s)) for s in sts)))
    print("per module x variant x status:")
    for mname in sorted({r[1] for r in rows}):
        for v in VARIANTS:
            sub = [r for r in rows if r[1] == mname and r[2] == v]
            if sub:
                print("  %-18s %-13s %s" % (mname, v, " ".join("%s=%d" % (s, sum(1 for r in sub if r[11] == s)) for s in sts if any(r[11] == s for r in sub))))
    print("TOTAL points: %d ; %s" % (len(rows), " ".join("%s=%d" % (s, sum(1 for r in rows if r[11] == s)) for s in sts)))
    wm = {}
    for r in rows:
        if r[1] == "Core":
            wm[(r[5], r[2])] = wm.get((r[5], r[2]), 0) + 1
    for k in sorted(wm):
        print("Core width_mode=%s variant=%s: %d" % (k[0], k[1], wm[k]))
    print("wrote", a.out)


def cmd_set(a):
    rows = read_csv(a.manifest)
    hit = False
    for r in rows:
        if r["point"] == a.point:
            r["status"] = a.status; hit = True
    if not hit:
        raise SystemExit("point not found: " + a.point)
    tmp = a.manifest + ".tmp"
    write_csv(tmp, HEADER, [[r[h] for h in HEADER] for r in rows])
    os.replace(tmp, a.manifest)


def cmd_release(a):
    """Move every row with status OLD to NEW (e.g. needs_clamp_fix -> pending once the staged clamp is applied and the jar rebuilt)."""
    rows = read_csv(a.manifest)
    n = 0
    for r in rows:
        if r["status"] == a.old:
            r["status"] = a.new; n += 1
    tmp = a.manifest + ".tmp"
    write_csv(tmp, HEADER, [[r[h] for h in HEADER] for r in rows])
    os.replace(tmp, a.manifest)
    print("moved %d rows %s -> %s" % (n, a.old, a.new))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    g = sp.add_parser("gen")
    g.add_argument("--options-json"); g.add_argument("--scala"); g.add_argument("--out", default="manifest.csv")
    g.add_argument("--widths"); g.add_argument("--width-dep", action="append")
    s = sp.add_parser("set-status")
    s.add_argument("manifest"); s.add_argument("point"); s.add_argument("status")
    r = sp.add_parser("release")
    r.add_argument("manifest"); r.add_argument("old"); r.add_argument("new", nargs="?", default="pending")
    a = ap.parse_args()
    {"gen": cmd_gen, "set-status": cmd_set, "release": cmd_release}[a.cmd](a)
