#!/usr/bin/env python3
"""tile_equiv.py -- step P3 of the activity-power pilot: is the simulated BoomTile the synthesized BoomTile?

Purpose : compare, per instance path under the tile top, the module bodies of the OOC point's rtl/ (behavioural
          memories, one BoomTile.sv + BoomTile_deps.sv) with the Verilator build's gen-collateral/ (one module per
          file; SRAMs split into *_ext macros by --repl-seq-mem and described in <config>.top.mems.conf).
Method  : both trees are parsed with scala/extract_rtl.py (parse(): module texts), the instance tree is rebuilt
          across files, and every instance path is compared.  A module body is normalised to a list of
          statements: `// ...` and /* */ comments dropped, every string literal reduced to "" (assertion and
          printf text never reaches synthesis), whitespace collapsed, the whole body joined and split on ';'
          and on begin/end (line wrapping differs between the trees), the module header split per port, and
          each instance statement written as one item per connection.  A module name becomes @M only in the
          module-type position of an instance statement (and in the `module NAME` header); numeric suffixes
          such as ALUExeUnit_2 can differ between elaborations, child bodies are compared at their own path.
          Memories are compared by shape: OOC module with `reg [W-1:0] Memory[0:D-1]` against the sim wrapper
          whose only child is a *_ext macro listed in top.mems.conf (sim memories that stay inline as a Memory
          array are read like the OOC ones); shape = (depth, width, port kind rw|wr, mask granularity).
          Memory wrappers are leaves.  The memory children of a parent are named by order of appearance
          (@mem0, @mem1, ...) in both trees, so OOC `table`/`table_ext` pairs with sim `table_0`; the instance
          name and its port wires (`_<inst>_R0_data`) are renamed to that canonical name inside the parent
          body, and a constant `.W0_en (1'h1)` connection of a memory instance is dropped (the sim wrapper
          has no W0_en port).  Dead sim-only logic: a sim memory instance whose read data port (R*_data,
          RW*_rdata) is absent or unconnected is a dead sim-only memory: it is counted and listed separately
          and removed with everything that only feeds it (a port/wire whose live sinks are all dead
          statements or dead connections is dead; its declaration, its drivers and the connections to a dead
          input port of a child are dropped; fixpoint over all sim modules).  Dropping is done on the sim
          side only; whatever is not provably dead still differs and fails the run.
Inputs  : --ooc DIR    the point's rtl/ directory (*.sv)
          --sim DIR    generated-src/<package>.<config> directory (needs gen-collateral/*.sv and *.top.mems.conf)
          --top NAME   tile top module in both trees (default BoomTile)
          --mems-conf FILE   override the conf file
          --max-report N     detail lines per category (default 15)
Usage   : python3 tile_equiv.py --ooc $W/ooc/points/Tile/base/<K>,width=4/rtl --sim $CY/sims/verilator/generated-src/chipyard.harness.TestHarness.BaselineBoomPowerDMIConfig
Output  : counts and the first differences on stdout (incl. "dead sim-only memories" with their paths and the
          number of dead sim-only ports/wires/statements dropped).  Exit 0 only when there is no non-memory
          difference, no memory-shape difference and no path present on one side only (dead sim-only memories
          do not count); exit 1 otherwise, 2 usage/parse error.
"""
import argparse, difflib, glob, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scala"))
import extract_rtl as X  # noqa: E402

IDENT = re.compile(r"(?<![\w$'`])[A-Za-z_]\w*")
INST_STMT = re.compile(r"^([A-Za-z_]\w*)\s*(?:#\s*\(.*?\)\s*)?([A-Za-z_]\w*)\s*\((.*)\)$", re.S)
ASSIGN = re.compile(r"^(?:assign\s+)?([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?:<=|=)(?!=)\s*(.*)$", re.S)
DECL = re.compile(r"^(?:wire|reg|logic|integer)\b")
CONN = re.compile(r"\.\s*(\w+)\s*\(")
RDATA = re.compile(r"^(?:R\d+_data|RW\d+_rdata)$")
WEN1 = re.compile(r"^W\d+_en$")
MEMW = re.compile(r"^_(.+?)_((?:RW|R|W)\d+_\w+)$")


def load_mods(files, label):
    mods, order, skipped = {}, [], []
    for f in files:
        try:
            _, m, o, _ = X.parse(f)
        except SystemExit as e:
            skipped.append((f, str(e)[:80])); continue
        for k in o:
            if k in mods:
                sys.exit("%s: duplicate module %s (%s)" % (label, k, f))
            mods[k] = m[k]; order.append(k)
    return mods, order, skipped


def strip_text(text):
    """drop // and /* */ comments, reduce every string literal to "" ; newlines kept"""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1; out.append('""')
        elif text.startswith("//", i):
            j = text.find("\n", i); i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2); i = n if j < 0 else j + 2
            out.append(" ")
        else:
            out.append(c); i += 1
    return "".join(out)


def split_stmts(text):
    """module text -> (header items, statements); header = ['module @M(', 'input clock', ...]"""
    lines = []
    for ln in strip_text(text).split("\n"):
        t = ln.strip()
        if t:
            lines.append(t + " ;" if t.startswith("`") else t)   # directives end their own statement
    j = re.sub(r"\s+", " ", " ".join(lines))
    j = re.sub(r"\b(begin|end)\b", r" ; \1 ; ", j)
    st = [s.strip() for s in j.split(";")]
    st = [s for s in st if s]
    hdr = []
    if st and st[0].startswith("module "):
        m = re.match(r"module\s+\w+\s*\((.*)\)$", st[0], re.S)
        if m:
            hdr = ["module @M("] + [x.strip() for x in m.group(1).split(",")]
        else:
            hdr = [re.sub(r"^module\s+\w+", "module @M", st[0])]
        st = st[1:]
    return hdr, st


def parse_conns(s):
    out, i, n = [], 0, len(s)
    while True:
        m = CONN.search(s, i)
        if not m:
            break
        j, d = m.end(), 1
        while j < n and d:
            d += (s[j] == "(") - (s[j] == ")"); j += 1
        out.append((m.group(1), s[m.end():j - 1].strip()))
        i = j
    return out


def hdr_ports(hdr):
    """-> {name: dir} from header items"""
    res, cur = {}, None
    for it in hdr[1:]:
        t = it.split()
        if t and t[0] in ("input", "output", "inout"):
            cur = t[0]
        ids = IDENT.findall(re.sub(r"\[[^\]]*\]", "", it))
        if ids and cur:
            res[ids[-1]] = cur
    return res


def decl_names(st):
    s = re.sub(r"\[[^\]]*\]", "", st)
    s = re.sub(r"^(?:wire|reg|logic|integer)\b", "", s).strip()
    s = re.sub(r"^signed\b", "", s).strip()
    names = []
    for piece in s.split(","):
        piece = piece.split("=")[0]
        ids = IDENT.findall(piece)
        if ids:
            names.append(ids[0] if not names else ids[-1])
    return names


class Tree:
    """one side: module texts -> statements, instances, memory shapes, dead sim-only logic, normalised bodies"""

    def __init__(self, mods, known, conf=None, find_dead=False):
        self.mods, self.known, self.conf = mods, known, conf or {}
        self.hdr, self.stm, self.inst = {}, {}, {}
        for name, text in mods.items():
            h, st = split_stmts(text)
            self.hdr[name], self.stm[name] = h, st
            lst = []
            for k, s in enumerate(st):
                m = INST_STMT.match(s)
                if m and m.group(1) in known:
                    lst.append((k, m.group(1), m.group(2), parse_conns(m.group(3))))
            self.inst[name] = lst
        self.shape = {m: ooc_mem_shape(t) for m, t in mods.items()}
        self._memcache, self._normc = {}, {}
        self.dead = {}          # mod -> dict(stmt, conn, ids, ports, mems)
        if find_dead:
            self.analyse_dead()

    def mem_of(self, m):
        """memory shape of module m (None = logic).  Sim wrapper: its only child is a conf macro."""
        if m in self._memcache:
            return self._memcache[m]
        r = None
        if m in self.mods:
            ch = self.inst.get(m, [])
            if self.conf and len(ch) == 1 and ch[0][1] in self.conf:
                r = conf_shape(self.conf[ch[0][1]])
            else:
                r = self.shape.get(m)
        self._memcache[m] = r
        return r

    def is_dead_mem(self, typ, conns):
        if self.mem_of(typ) is None:
            return False
        return not any(RDATA.match(p) and e.strip() for p, e in conns)

    # ---- dead sim-only logic -------------------------------------------------------------------
    def analyse_dead(self):
        dead_in = {m: set() for m in self.mods}
        changed = True
        while changed:
            changed = False
            for m in self.mods:
                if self.mem_of(m) is not None:
                    continue
                d = self.dead_mod(m, dead_in)
                if d is None:
                    continue
                self.dead[m] = d
                if d["ports"] != dead_in[m]:
                    dead_in[m] = set(d["ports"]); changed = True

    def dead_mod(self, m, dead_in):
        stm, inst = self.stm[m], self.inst[m]
        dstmt, dconn = set(), set()
        mems = []
        for (k, typ, iname, conns) in inst:
            if self.is_dead_mem(typ, conns):
                dstmt.add(k); mems.append(iname)
            else:
                for p, _ in conns:
                    if p in dead_in.get(typ, ()):
                        dconn.add((k, p))
        if not dstmt and not dconn:
            return None
        ports = hdr_ports(self.hdr[m])
        inports = {p for p, d in ports.items() if d == "input"}
        declared, declk = {}, set()
        for k, s in enumerate(stm):
            if DECL.match(s):
                declk.add(k)
                for nm in decl_names(s):
                    declared[nm] = k
        cand = inports | set(declared)
        instat = {k: (typ, conns) for (k, typ, iname, conns) in inst}
        dids = set()
        while True:
            uses, refs = {}, set()
            for k, s in enumerate(stm):
                if k in declk:
                    # `wire x = expr`: a driver of x; a plain declaration has no uses
                    if "=" in s:
                        nm = decl_names(s)[0]
                        rhs = s.split("=", 1)[1]
                        if nm in dids:
                            dstmt.add(k); refs.update(IDENT.findall(rhs))
                        else:
                            for i in IDENT.findall(rhs): uses[i] = uses.get(i, 0) + 1
                    continue
                if k in instat:
                    typ, conns = instat[k]
                    for p, e in conns:
                        ids = IDENT.findall(e)
                        if k in dstmt or (k, p) in dconn:
                            refs.update(ids)
                        else:
                            for i in ids: uses[i] = uses.get(i, 0) + 1
                    continue
                a = ASSIGN.match(s)
                if a and a.group(1) in dids:
                    dstmt.add(k)
                if k in dstmt:
                    refs.update(IDENT.findall(s)); continue
                if a:
                    # the assigned name itself is a driver, not a use; index and right-hand side idents are uses
                    for i in IDENT.findall(re.sub(r"^assign\s+", "", s))[1:]:
                        uses[i] = uses.get(i, 0) + 1
                else:
                    for i in IDENT.findall(s): uses[i] = uses.get(i, 0) + 1
            new = {x for x in cand if x in refs and not uses.get(x) and x not in dids}
            if not new:
                break
            dids |= new
        # ports that are dead inputs; wires/regs dropped with their declarations
        dports = {x for x in dids if x in inports}
        return {"stmt": dstmt, "conn": dconn, "ids": dids, "ports": dports, "mems": mems}

    # ---- normalised body ------------------------------------------------------------------------
    def children(self, m):
        """live children: [(type, canonical name, display name, is_mem)]; dead memories are left out"""
        res, k = [], 0
        dm = set(self.dead.get(m, {}).get("mems", ()))
        for (_, typ, iname, conns) in self.inst.get(m, []):
            if iname in dm and self.is_dead_mem(typ, conns):
                continue
            if self.mem_of(typ) is not None:
                res.append((typ, "@mem%d" % k, iname, True)); k += 1
            else:
                res.append((typ, iname, iname, False))
        return res

    def dead_children(self, m):
        return list(self.dead.get(m, {}).get("mems", ()))

    def norm(self, m):
        if m in self._normc:
            return self._normc[m]
        self._normc[m] = out = self._norm(m)
        return out

    def _norm(self, m):
        stm, inst = self.stm[m], self.inst[m]
        d = self.dead.get(m)
        dstmt = d["stmt"] if d else set()
        dconn = d["conn"] if d else set()
        dids = d["ids"] if d else set()
        # canonical memory names (order of appearance of the live memory children)
        exact, stems, k = {}, {}, 0
        dm = set(d["mems"]) if d else set()
        for (_, typ, iname, conns) in inst:
            if iname in dm and self.is_dead_mem(typ, conns):
                continue
            if self.mem_of(typ) is not None:
                c = "@mem%d" % k; k += 1
                exact[iname] = c
                b = iname[:-4] if iname.endswith("_ext") else iname
                for s in {iname, b, re.sub(r"_\d+$", "", iname), re.sub(r"_\d+$", "", b)}:
                    stems.setdefault(s, c)
        def ren(tok):
            if tok in exact:
                return exact[tok]
            mm = MEMW.match(tok)
            if mm and mm.group(1) in stems:
                return "_%s_%s" % (stems[mm.group(1)], mm.group(2))
            return tok
        def rn(s):
            return IDENT.sub(lambda mo: ren(mo.group(0)), s) if (exact or stems) else s
        out = []
        ports = hdr_ports(self.hdr[m])
        for it in self.hdr[m]:
            if it == "module @M(" or it.startswith("module @M"):
                out.append(it); continue
            ids = IDENT.findall(re.sub(r"\[[^\]]*\]", "", it))
            if ids and ids[-1] in dids and ports.get(ids[-1]) == "input":
                continue
            out.append(it)
        instat = {k2: (typ, iname, conns) for (k2, typ, iname, conns) in inst}
        for k2, s in enumerate(stm):
            if k2 in dstmt:
                continue
            if k2 in instat:
                typ, iname, conns = instat[k2]
                ism = self.mem_of(typ) is not None
                out.append("@M %s (" % rn(iname))
                for p, e in conns:
                    if (k2, p) in dconn:
                        continue
                    if ism and WEN1.match(p) and e.strip() == "1'h1":
                        continue
                    out.append(".%s (%s)" % (p, rn(e)))
                out.append(")")
                continue
            if DECL.match(s) and dids and any(nm in dids for nm in decl_names(s)):
                continue   # (a `wire x = expr` of a dead x is already in dstmt)
            out.append(rn(s))
        return out


def parse_conf(path):
    conf = {}
    for ln in open(path):
        t = ln.split()
        if len(t) >= 8 and t[0] == "name":
            d = {"depth": int(t[3]), "width": int(t[5]), "ports": t[7]}
            if "mask_gran" in t:
                d["gran"] = int(t[t.index("mask_gran") + 1])
            conf[t[1]] = d
    return conf


def conf_shape(c):
    p = c["ports"]
    kind = "rw" if p.endswith("rw") else "wr"
    return (c["depth"], c["width"], kind, c.get("gran"))


def ooc_mem_shape(text):
    m = re.search(r"reg\s*(?:\[(\d+):0\])?\s*Memory\s*\[0:(\d+)\]", text)
    if not m:
        return None
    width = int(m.group(1)) + 1 if m.group(1) else 1
    depth = int(m.group(2)) + 1
    kind = "rw" if re.search(r"\bRW0_addr\b", text.split(");", 1)[0]) else "wr"
    mk = re.search(r"\[(\d+):0\]\s+(?:W|RW)0_w?mask\b", text.split(");", 1)[0])
    gran = None
    if mk:
        bits = int(mk.group(1)) + 1
        gran = width // bits if width % bits == 0 else -1
    elif re.search(r"\b(?:W|RW)0_w?mask\b", text.split(");", 1)[0]):
        gran = width
    return (depth, width, kind, gran)


def walk(tree, root):
    """-> (path -> module, path -> display name, [(path, dead sim-only memory name)]); memory wrappers are leaves"""
    res, disp, dead, stack = {}, {}, [], [("", root, "")]
    while stack:
        path, mod, dn = stack.pop()
        res[path] = mod; disp[path] = dn
        if tree.mem_of(mod) is not None:
            continue
        for nm in tree.dead_children(mod):
            dead.append((path or "<top>", nm))
        for child, canon, dname, _ in reversed(tree.children(mod)):
            stack.append((path + "." + canon if path else canon, child, dname))
    return res, disp, dead


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ooc", required=True)
    ap.add_argument("--sim", required=True)
    ap.add_argument("--top", default="BoomTile")
    ap.add_argument("--mems-conf")
    ap.add_argument("--max-report", type=int, default=15)
    a = ap.parse_args()

    ofiles = sorted(glob.glob(os.path.join(a.ooc, "*.sv")))
    sfiles = sorted(glob.glob(os.path.join(a.sim, "gen-collateral", "*.sv")))
    if not ofiles or not sfiles:
        print("no *.sv found (ooc: %d, sim gen-collateral: %d)" % (len(ofiles), len(sfiles)), file=sys.stderr); return 2
    conf_path = a.mems_conf or (sorted(glob.glob(os.path.join(a.sim, "*.top.mems.conf")) or [None])[0])
    if not conf_path:
        print("no *.top.mems.conf in --sim", file=sys.stderr); return 2
    conf = parse_conf(conf_path)
    omods, _, oskip = load_mods(ofiles, "ooc")
    smods, _, sskip = load_mods(sfiles, "sim")
    if a.top not in omods: print("top %s not in ooc" % a.top, file=sys.stderr); return 2
    if a.top not in smods: print("top %s not in sim" % a.top, file=sys.stderr); return 2
    ot = Tree(omods, set(omods))
    st = Tree(smods, set(smods) | set(conf), conf=conf, find_dead=True)

    opaths, odisp, _ = walk(ot, a.top)
    spaths, sdisp, sdead = walk(st, a.top)
    only_o = sorted(set(opaths) - set(spaths))
    only_s = sorted(set(spaths) - set(opaths))
    mem_eq = mem_diff = logic_eq = 0
    mem_report, logic_report, kind_report = [], [], []
    pair = {}
    for p in sorted(set(opaths) & set(spaths)):
        om, sm = opaths[p], spaths[p]
        os_, ss_ = ot.mem_of(om), st.mem_of(sm)
        if (os_ is None) != (ss_ is None):
            kind_report.append("%s: ooc %s (%s) / sim %s (%s)" % (p, om, "memory" if os_ else "logic", sm, "memory" if ss_ else "logic")); continue
        if os_ is not None:
            if os_ == ss_: mem_eq += 1
            else:
                mem_diff += 1; mem_report.append("%s (ooc inst %s / sim inst %s): ooc %s %s  sim %s %s" % (p, odisp[p], sdisp[p], om, os_, sm, ss_))
            continue
        key = (om, sm)
        if key not in pair:
            pair[key] = ot.norm(om) == st.norm(sm)
        if pair[key]: logic_eq += 1
        else: logic_report.append((p, om, sm))
    pair_diff = sorted({(o, s) for (_, o, s) in logic_report})
    nd_ports = sum(len(d["ports"]) for d in st.dead.values())
    nd_ids = sum(len(d["ids"] - d["ports"]) for d in st.dead.values())
    nd_stm = sum(len(d["stmt"]) + len(d["conn"]) for d in st.dead.values())
    print("tile top            : %s" % a.top)
    print("ooc modules / paths : %d / %d   (skipped files: %d)" % (len(omods), len(opaths), len(oskip)))
    print("sim modules / paths : %d / %d   (skipped files: %d)   mems.conf: %s (%d macros)" % (len(smods), len(spaths), len(sskip), os.path.basename(conf_path), len(conf)))
    print("logic paths equal   : %d" % logic_eq)
    print("logic paths DIFFER  : %d   (distinct module pairs: %d)" % (len(logic_report), len(pair_diff)))
    print("memory paths equal  : %d" % mem_eq)
    print("memory paths DIFFER : %d" % mem_diff)
    print("memory/logic kind mismatch: %d" % len(kind_report))
    print("paths only in ooc   : %d" % len(only_o))
    print("paths only in sim   : %d" % len(only_s))
    print("dead sim-only memories      : %d   (read data unconnected; not a failure)" % len(sdead))
    print("dead sim-only ports/wires   : %d ports, %d wires/regs, %d statements/connections dropped in %d modules" % (nd_ports, nd_ids, nd_stm, len(st.dead)))
    n = a.max_report
    for (p, nm) in sdead[:n]:
        print("DEAD sim-only memory: %s.%s" % (p, nm))
    for m_, d in sorted(st.dead.items())[:n]:
        print("DEAD in module %s: ports %s, wires/regs %s" % (m_, sorted(d["ports"]) or "-", sorted(d["ids"] - d["ports"]) or "-"))
    seen = set()
    for (p, om, sm) in logic_report:
        if (om, sm) in seen: continue
        seen.add((om, sm))
        if len(seen) > n: break
        print("\nDIFF logic %s : ooc %s vs sim %s" % (p, om, sm))
        d = list(difflib.unified_diff(ot.norm(om), st.norm(sm), "ooc", "sim", lineterm="", n=0))
        for ln in d[:8]: print("   " + ln[:200])
    for title, lst in (("memory shape", mem_report), ("memory/logic kind", kind_report), ("only in ooc", only_o), ("only in sim", only_s)):
        for ln in lst[:n]: print("DIFF %s: %s" % (title, ln))
    bad = len(logic_report) + mem_diff + len(kind_report) + len(only_o) + len(only_s)
    print("\nRESULT: %s" % ("EQUIVALENT" if bad == 0 else "DIFFERENT (%d)" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
