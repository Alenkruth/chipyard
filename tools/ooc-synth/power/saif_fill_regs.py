#!/usr/bin/env python3
"""saif_fill_regs.py -- SAIF -> Tcl activity table for registers whose Q net synthesis renamed (power pilot).

Purpose : read_saif matches SAIF entries to synthesized nets by NAME.  Vivado renames the Q net of many registers
          (X_reg_n_0_[3], X_reg[15]_0[3], p_0_in[3], replicas X_reg[3]_rep__1, memory words Memory_reg[5][63]_0[2]),
          so those flip-flops keep the 12.5 % default activity although the RTL register is in the SAIF.  This
          script writes a Tcl data file with the SAIF activity of every SAIF net, keyed by its full name below the
          strip path; saif_fill_regs.tcl (sourced by synth/power_saif.tcl with PWR_FILL=<file>, or by
          saif_match_diag.tcl with DIAG_FILL=<file>) then gives every flip-flop whose Q net was not matched by
          name the activity of the RTL register named by its CELL (rule below), with set_switching_activity.
Rule (shared with saif_fill_regs.tcl, implemented by rtl_name() here and saif_fill_rtl_name there):
          register cell name C (path below the strip instance) -> RTL bit name:
            strip a trailing duplicate suffix "__<n>" and a replica suffix "_rep" / "_rep__<n>" (after the indices
            or between "_reg" and the indices); then C must read  <base>_reg<idx>  with <idx> zero or more [<n>];
            the RTL name is <base><idx>.  Examples:
              core/rob/rob_val_reg[3]            -> core/rob/rob_val[3]
              tlMasterXbar/readys_mask_reg[1]    -> tlMasterXbar/readys_mask[1]   (Q net readys_mask_reg_n_0_[1])
              core/x/r_uops_0_pdst_reg[2]_rep__0 -> core/x/r_uops_0_pdst[2]       (replica of the same register)
              core/x/_R0_addr_d0_reg_rep[5]      -> core/x/_R0_addr_d0[5]
              core/iregfile/regfile_ext/Memory_reg[17][63] -> core/iregfile/regfile_ext/Memory[17][63]
                (the SAIF word comes from fst2saif --mem-from-ports or --arrays)
            no match (retimed *_bret*, cells without "_reg") -> no RTL name.  Fallbacks when the name is not in
            the SAIF: <base> for <base>[0] and <base>[0] for <base> (1-bit buses).
Values  : signal rate (Mtr/s) = TC / (DURATION * timescale_ns) * 1000 (= TC/1000 for DURATION 1e6 ns);
          static probability = T1 / (T0 + T1), or 0.5 (Vivado's default) when the bit is X for the whole window
          (T0 + T1 = 0, e.g. a register-file word never written: rate 0 is exact, its level is unknown).
          With TX = 0 (every dumped bit of the pilot SAIF) this is T1 / DURATION, the value Vivado itself reads
          ((S) rates equal TC/DURATION, power-pilot-name-match.md).
Inputs  : --saif S.saif (fst2saif output) --strip PATH (as read_saif -strip_path) --out fill.tcl
Usage   : python3 -I saif_fill_regs.py --saif $W/s2m.saif --strip TOP/.../element_reset_domain_boom_tile --out $W/s2m_fill.tcl
          then  PWR_FILL=$W/s2m_fill.tcl  (power_saif.tcl)  or  DIAG_FILL=$W/s2m_fill.tcl  (saif_match_diag.tcl)
Output  : fill.tcl: "set ::saif_fill_meta {...}" and chunks of "array set ::saif_act {<name> {<rate> <sp>} ...}";
          summary "key=value" lines on stdout (names, skipped_names, duration, timescale_ns).
"""
import argparse, re, sys

_DUP = re.compile(r"__\d+$")
_REP_TAIL = re.compile(r"_rep(?:__\d+)?$")
_REG = re.compile(r"^(.*)_reg(?:_rep(?:__\d+)?)?((?:\[\d+\])*)$")


def rtl_name(cell):
    """register cell name -> RTL bit name, or None (rule in the module docstring)"""
    n = _DUP.sub("", cell)
    n = _REP_TAIL.sub("", n)
    n = _DUP.sub("", n)
    m = _REG.match(n)
    return (m.group(1) + m.group(2)) if m else None


def lookup(name, keys):
    """RTL bit name -> the SAIF key used (with the 1-bit bus fallbacks), or None"""
    if name in keys:
        return name
    if name.endswith("[0]") and name[:-3] in keys and not name[:-3].endswith("]"):
        return name[:-3]
    if not name.endswith("]") and name + "[0]" in keys:
        return name + "[0]"
    return None


def read_saif(path, strip):
    """yield (full name below strip, T0, T1, TX, TC); also returns duration/timescale through the meta dict"""
    comps = strip.split("/") if strip else []
    unesc = lambda s: re.sub(r"\\(.)", r"\1", s)
    stack, in_net, meta = [], False, {}
    netre = re.compile(r"^\(((?:\\.|[^\s()\\])+) \(T0 (\d+)\) \(T1 (\d+)\) \(TX (\d+)\) \(TC (\d+)\)")
    out = []
    with open(path) as f:
        for ln in f:
            s = ln.strip()
            if s.startswith("(INSTANCE "):
                stack.append(unesc(s[len("(INSTANCE "):].split()[0])); continue
            if s == "(NET":
                in_net = True; continue
            if in_net and s.startswith("(") and "(T0" in s:
                m = netre.match(s)
                if m and stack[:len(comps)] == comps:
                    out.append(("/".join(stack[len(comps):] + [unesc(m.group(1))]),) + tuple(int(m.group(i)) for i in range(2, 6)))
                continue
            if s == ")":
                if in_net: in_net = False
                elif stack: stack.pop()
                continue
            if s.startswith("(DURATION "):
                meta["duration"] = int(s.split()[1].rstrip(")"))
            elif s.startswith("(TIMESCALE "):
                v, u = s[len("(TIMESCALE "):].rstrip(")").split()
                meta["timescale_ns"] = float(v) * {"s": 1e9, "ms": 1e6, "us": 1e3, "ns": 1.0, "ps": 1e-3, "fs": 1e-6}[u]
    return out, meta


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--saif", required=True); ap.add_argument("--strip", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    nets, meta = read_saif(a.saif, a.strip)
    if "duration" not in meta or meta["duration"] <= 0:
        sys.exit("saif_fill_regs: no DURATION in the SAIF")
    D = meta["duration"]; ts = meta.get("timescale_ns", 1.0)
    bad = re.compile(r'[\s{}\\"$;]')
    skipped = 0; rows = []
    for name, t0, t1, tx, tc in nets:
        if bad.search(name):
            skipped += 1; continue
        rate = tc / (D * ts) * 1e3          # transitions per ns -> Mtr/s
        rows.append("%s {%.9g %.9g}" % (name, rate, t1 / (t0 + t1) if t0 + t1 else 0.5))
    with open(a.out, "w") as f:
        f.write("# generated by saif_fill_regs.py from %s (strip %s); data for saif_fill_regs.tcl\n" % (a.saif, a.strip))
        f.write("set ::saif_fill_meta {saif %s duration %d timescale_ns %g names %d}\n" % (a.saif, D, ts, len(rows)))
        for i in range(0, len(rows), 20000):
            f.write("array set ::saif_act {\n" + "\n".join(rows[i:i + 20000]) + "\n}\n")
    print("names=%d\nskipped_names=%d\nduration=%d\ntimescale_ns=%g" % (len(rows), skipped, D, ts))


if __name__ == "__main__":
    main()
