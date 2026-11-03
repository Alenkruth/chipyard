#!/usr/bin/env python3
"""saif_match_offline.py -- per driver class, how many synthesized nets have a same-named SAIF entry (power pilot V2).

Purpose : companion of saif_match_diag.tcl.  Rebuilds the full path (below the --strip instance) of every net in a
          fst2saif SAIF, unescaping \\[ \\] and other backslash escapes, and compares it with the Vivado net-name lists
          nets_<class>.txt written by saif_match_diag.tcl (FD, RAMB, DSP, LUT, CARRY, MUXF, SRL, PORT, all).  Also
          summarises Vivado's own saif_unmatched.txt (SAIF objects read_saif could not place) by name pattern.
          The offline match is exact string equality, so it is a lower bound of Vivado's own matching.
          --fill adds an ESTIMATE of what saif_fill_regs.tcl reaches: offline there are no cell names, so the
          register cell is guessed from the net name (fill_cells() below; Vivado's Q-net naming, an assumption),
          then saif_fill_regs.rtl_name()/lookup() give the RTL bit; the real count comes from the Vivado run
          (SAIF_FILL lines, saif_fill.txt).  --act DIR counts the source tags of act_<class>.rpt (DIAG_ACTIVITY=1):
          S = from the SAIF, D = default, other = user-set (saif_fill_regs.tcl) or propagated.
Inputs  : --saif S.saif --strip PATH (same as read_saif -strip_path) --diag DIR (saif_match_diag.tcl output)
          [--fill] [--act DIR] [--json PATH (default DIR/offline_match.json of --diag)]
Usage   : python3 -I saif_match_offline.py --saif $W/s2.saif --strip TOP/.../element_reset_domain_boom_tile --diag $W/v2diag
Output  : a table on stdout and the json file.
"""
import argparse, collections, importlib.util, json, os, re

def _fill_module():
    sp = importlib.util.spec_from_file_location("saif_fill_regs", os.path.join(os.path.dirname(os.path.abspath(__file__)), "saif_fill_regs.py"))
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m); return m

_NNI = re.compile(r"^(.*)_n_\d+_(\[\d+\])$")                                   # X_reg_n_0_[3]      -> X_reg[3]
_NN = re.compile(r"^(.*)_n_\d+$")                                                # X_reg[3]_rep__0_n_0 -> X_reg[3]_rep__0
_BUS = re.compile(r"^(.*_reg(?:_rep(?:__\d+)?)?(?:\[\d+\])*)\[\d+\]_\d+\[(\d+)\]$")  # X_reg[2][63]_0[5] -> X_reg[2][5]
_SFX = re.compile(r"^(.*_reg(?:_rep(?:__\d+)?)?(?:\[\d+\])*(?:_rep(?:__\d+)?)?)_\d+$")  # X_reg[5]_0 -> X_reg[5]

def fill_cells(net):
    """net name -> guessed register cell name (offline estimate only), or None"""
    d, _, leaf = net.rpartition("/")
    for rx, f in ((_NNI, lambda m: m.group(1) + m.group(2)), (_NN, lambda m: m.group(1)),
                  (_BUS, lambda m: m.group(1) + "[" + m.group(2) + "]"), (_SFX, lambda m: m.group(1))):
        m = rx.match(leaf)
        if m:
            return (d + "/" if d else "") + f(m)
    return (d + "/" if d else "") + leaf if "_reg" in leaf else None

def count_act(path):
    """source tags of a report_switching_activity file -> Counter of S / D / other (by the static-probability tag)"""
    c = collections.Counter()
    for ln in open(path):
        m = re.search(r"static probability = \S+ \((\w)\)", ln)
        if m: c[m.group(1) if m.group(1) in "SD" else "other"] += 1
    return c

def saif_names(path, strip):
    comps = strip.split("/")
    stack, names, in_net = [], set(), False
    tok = re.compile(r"\(INSTANCE\s+((?:\\.|[^\s()])+)|\(NET\b|\(((?:\\.|[^\s()])+)\s+\(T0|\)")
    unesc = lambda s: re.sub(r"\\(.)", r"\1", s)
    depth_net = None
    with open(path) as f:
        for ln in f:
            s = ln.strip()
            if s.startswith("(INSTANCE "):
                stack.append(unesc(s[len("(INSTANCE "):].split()[0])); continue
            if s == "(NET":
                in_net = True; continue
            if in_net and s.startswith("(") and "(T0" in s:
                nm = unesc(s[1:s.index(" (T0")])
                p = stack[len(comps):] + [nm]
                if stack[:len(comps)] == comps: names.add("/".join(p))
                continue
            if s == ")":
                if in_net: in_net = False
                elif stack: stack.pop()
    return names

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--saif", required=True); ap.add_argument("--strip", required=True); ap.add_argument("--diag", required=True)
    ap.add_argument("--fill", action="store_true"); ap.add_argument("--act"); ap.add_argument("--json")
    a = ap.parse_args()
    F = _fill_module() if a.fill else None
    S = saif_names(a.saif, a.strip)
    res = {"saif_names": len(S), "classes": {}}
    print("SAIF net names below strip path: %d" % len(S))
    print(("%-6s %10s %10s %7s" % ("class", "nets", "in SAIF", "%")) + ("  %10s %10s %7s" % ("+fill est", "total", "%") if F else ""))
    for cls in ["FD", "RAMB", "DSP", "LUT", "CARRY", "MUXF", "SRL", "PORT", "all"]:
        f = os.path.join(a.diag, "nets_%s.txt" % cls)
        if not os.path.exists(f): continue
        nets = [x.strip() for x in open(f) if x.strip()]
        hit = [n for n in nets if n in S]
        miss = [n for n in nets if n not in S]
        res["classes"][cls] = {"nets": len(nets), "matched": len(hit), "miss_examples": miss[:15]}
        line = "%-6s %10d %10d %6.1f%%" % (cls, len(nets), len(hit), 100.0 * len(hit) / max(1, len(nets)))
        if F and cls in ("FD",):
            filled, left = [], []
            for n in miss:
                c = fill_cells(n); r = F.rtl_name(c) if c else None
                (filled if r and F.lookup(r, S) else left).append(n)
            tot = len(hit) + len(filled)
            res["classes"][cls].update({"fill_estimate": len(filled), "total_estimate": tot,
                                        "left_patterns": collections.Counter(re.sub(r"\d+", "N", x.split("/")[-1]) for x in left).most_common(30)})
            line += "  %10d %10d %6.1f%%" % (len(filled), tot, 100.0 * tot / max(1, len(nets)))
        print(line)
    um = os.path.join(a.diag, "saif_unmatched.txt")
    if os.path.exists(um):
        L = [x.rstrip("\n") for x in open(um) if x.strip()]
        pat = collections.Counter(re.sub(r"\d+", "N", x.split("/")[-1])[:40] for x in L)
        res["vivado_unmatched_lines"] = len(L); res["vivado_unmatched_top_patterns"] = pat.most_common(25)
        print("\nVivado saif_unmatched.txt: %d lines; top leaf patterns:" % len(L))
        for p, c in pat.most_common(25): print("  %7d  %s" % (c, p))
    if F and "FD" in res["classes"]:
        print("\nFD left after the fill estimate, top leaf patterns:")
        for p, c in res["classes"]["FD"]["left_patterns"][:20]: print("  %7d  %s" % (c, p))
    if a.act:
        print("\nactivity source tags (act_<class>.rpt in %s): S = SAIF, D = default, other = user-set/propagated" % a.act)
        res["act"] = {}
        for cls in ["FD", "RAMB", "DSP", "LUT", "CARRY", "MUXF"]:
            f = os.path.join(a.act, "act_%s.rpt" % cls)
            if not os.path.exists(f): continue
            c = count_act(f); n = sum(c.values()); res["act"][cls] = dict(c)
            print("  %-6s %9d  S %9d (%5.1f%%)  D %9d  other %9d" % (cls, n, c["S"], 100.0 * c["S"] / max(1, n), c["D"], c["other"]))
    json.dump(res, open(a.json or os.path.join(a.diag, "offline_match.json"), "w"), indent=1)

if __name__ == "__main__":
    main()
