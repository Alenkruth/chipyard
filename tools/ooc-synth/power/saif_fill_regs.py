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
Memory aliases (--mem-alias all, the default; defpower-a0-coverage.md): the Verilator build models every SyncReadMem with
          the MacroCompiler lane split, <P>/<inst>/<mod>_ext/mem_0_<k>/ram[w][b] (lane k holds bits [o_k, o_k + width_k),
          lanes in k order, mems.v: mem_0_1_W0_data = W0_data[15:8] for an 8-bit mask granule), while the OOC netlist
          synthesizes the firtool memory <P>/<inst>_ext with reg Memory[w][o_k + b] and the read pipeline registers
          _R<n>_addr_d0 <= R<n>_addr, _R<n>_en_d0 <= R<n>_en, _RW<n>_raddr_d0 <= RW<n>_addr, _RW<n>_ren_d0 <= RW<n>_en,
          _RW<n>_rmode_d0 <= RW<n>_wmode (unconditional, so they carry the activity of the sim ext's input port).
          So the table also gets, for every such sim memory, the alias names
            <P>/<inst>_ext/Memory[w][o_k + b]   (Memory[w] when the memory is 1 bit wide)  from mem_0_<k>/ram[w][b] or ram[w]
            <P>/<inst>_ext/_R<n>_addr_d0[i] ...  from <P>/<inst>/<mod>_ext/R<n>_addr[i] ... (port names above)
          Only memories that synthesis dissolved into flip-flops use the Memory aliases (e.g. dcache/sa_tags/tc_data_1_ext,
          16,384 FFs); BRAM/LUTRAM memories ignore them.  Depth-split lanes (mem_<d>_<k>, d > 0) get no Memory alias.
          --mem-alias ports: only the read pipeline aliases; none: no aliases (the table before this change).
Values  : signal rate (Mtr/s) = TC / (DURATION * timescale_ns) * 1000 (= TC/1000 for DURATION 1e6 ns);
          static probability = T1 / (T0 + T1), or 0.5 (Vivado's default) when the bit is X for the whole window
          (T0 + T1 = 0, e.g. a register-file word never written: rate 0 is exact, its level is unknown).
          With TX = 0 (every dumped bit of the pilot SAIF) this is T1 / DURATION, the value Vivado itself reads
          ((S) rates equal TC/DURATION, power-pilot-name-match.md).
Inputs  : --saif S.saif (fst2saif output) --strip PATH (as read_saif -strip_path) --out fill.tcl [--mem-alias all|ports|none]
Usage  : python3 -I saif_fill_regs.py --saif $W/s2m.saif --strip TOP/.../element_reset_domain_boom_tile --out $W/s2m_fill.tcl
          then  PWR_FILL=$W/s2m_fill.tcl  (power_saif.tcl)  or  DIAG_FILL=$W/s2m_fill.tcl  (saif_match_diag.tcl)
Output  : fill.tcl: "set ::saif_fill_meta {...}" and chunks of "array set ::saif_act {<name> {<rate> <sp>} ...}";
          summary "key=value" lines on stdout (names, skipped_names, duration, timescale_ns, mem_alias_memory,
          mem_alias_ports, mem_alias_mems, mem_alias_skipped).
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


_LANE = re.compile(r"^(.*)/([^/]+)/[^/]+_ext/mem_(\d+)_(\d+)/ram\[(\d+)\](?:\[(\d+)\])?$")
_EXTPORT = re.compile(r"^(.*)/([^/]+)/[^/]+_ext/(R\d+_addr|R\d+_en|RW\d+_addr|RW\d+_en|RW\d+_wmode)((?:\[\d+\])?)$")
_PORT2REG = [(re.compile(r"^R(\d+)_addr$"), r"_R\1_addr_d0"), (re.compile(r"^R(\d+)_en$"), r"_R\1_en_d0"),
             (re.compile(r"^RW(\d+)_addr$"), r"_RW\1_raddr_d0"), (re.compile(r"^RW(\d+)_en$"), r"_RW\1_ren_d0"),
             (re.compile(r"^RW(\d+)_wmode$"), r"_RW\1_rmode_d0")]


def mem_aliases(nets, mode):
    """sim MacroCompiler lane names -> OOC firtool memory names (rule 'Memory aliases' in the module docstring).
    Returns (alias rows shaped like nets, stats dict)."""
    st = {"mem_alias_memory": 0, "mem_alias_ports": 0, "mem_alias_mems": 0, "mem_alias_skipped": 0}
    if mode == "none":
        return [], st
    out, width = [], {}
    if mode == "all":
        for row in nets:                                   # pass 1: lane widths (bits per mem_0_<k>)
            m = _LANE.match(row[0])
            if m and m.group(3) == "0":
                k = (m.group(1), m.group(2), int(m.group(4)))
                width[k] = max(width.get(k, 0), int(m.group(6) or 0) + 1)
    lanes = {}
    for (p, i, k), w in width.items():
        lanes.setdefault((p, i), {})[k] = w
    off, total = {}, {}
    for pi, ks in lanes.items():
        if sorted(ks) != list(range(len(ks))):              # a lane missing from the dump: no Memory alias for this memory
            continue
        o = 0
        for k in sorted(ks):
            off[pi + (k,)] = o; o += ks[k]
        total[pi] = o
    st["mem_alias_mems"] = len(total)
    for row in nets:                                       # pass 2: alias rows
        name = row[0]
        if "/mem_" in name:
            m = _LANE.match(name) if mode == "all" else None
            if not m:
                continue
            p, i, d, k = m.group(1), m.group(2), m.group(3), int(m.group(4))
            o = off.get((p, i, k))
            if d != "0" or o is None:
                st["mem_alias_skipped"] += 1; continue
            b = int(m.group(6) or 0)
            a = "%s/%s_ext/Memory[%s]" % (p, i, m.group(5)) + ("[%d]" % (o + b) if total[(p, i)] > 1 else "")
            out.append((a,) + tuple(row[1:])); st["mem_alias_memory"] += 1
            continue
        m = _EXTPORT.match(name)
        if m:
            for rx, rep in _PORT2REG:
                if rx.match(m.group(3)):
                    out.append(("%s/%s_ext/%s%s" % (m.group(1), m.group(2), rx.sub(rep, m.group(3)), m.group(4)),) + tuple(row[1:]))
                    st["mem_alias_ports"] += 1
                    break
    return out, st


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
    ap.add_argument("--mem-alias", choices=("all", "ports", "none"), default="all",
                    help="add OOC firtool-memory alias names for the sim's lane-split memories (docstring 'Memory aliases')")
    a = ap.parse_args()
    nets, meta = read_saif(a.saif, a.strip)
    alias, ast = mem_aliases(nets, a.mem_alias)
    nets = alias + nets                     # aliases first: a real SAIF name with the same key (array set) overrides them
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
    print("\n".join("%s=%d" % kv for kv in ast.items()))


if __name__ == "__main__":
    main()
