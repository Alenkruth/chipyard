#!/usr/bin/env python3
"""fst2saif_ref.py -- independent slow reference for fst2saif (activity-power pilot, plan 1.3 / step C0).

Purpose : recompute T0/T1/TX/TC per bit for a clock-cycle window from the TEXT dump that /usr/bin/fst2vcd
          writes, and write the same SAIF (and optional profile csv) as fst2saif.c, so the two outputs can be
          compared bit for bit with `cmp`.  Shares no code with the C converter; the definitions are the ones
          in the header of fst2saif.c (rising edge 0->1, window [t(A), t(A+N)), TC = 0<->1 only, other
          characters = X, time scale round_half_up(x*out_ns*N/(t1-t0)), T0 = DURATION-T1-TX, arrays skipped unless
          --arrays, --mem-from-ports REL[:DEPTH] rebuilds REL's "Memory" from its W<k>_en/_addr/_data[/_mask] write ports).
Inputs  : --fst X.fst (read through `fst2vcd -f X.fst`, streamed, stopped as soon as the window end is read), or
          --vcd X.vcd (the output of fst2vcd, read from disk).  Python 3 stdlib only.
Usage   : fst2saif_ref.py --fst s0.fst --scope TOP/TestDriver/.../tile --clock clock --from-cycle A --cycles N \
              --out-period-ns 10 --out ref.saif [--skip-underscore] [--profile ref.csv --bucket 1000]
              [--arrays] [--mem-from-ports REL[:DEPTH] ...]
          (or --vcd s0.vcd after `fst2vcd s0.fst > s0.vcd`)
          cmp ref.saif fst.saif      # must be identical
Output  : SAIF at --out; "key=value" summary lines on stdout (variables, bits, aliases, arrays_skipped,
          others_skipped, total_tc, edges, window_start_time, window_end_time, period_time).
Exit    : 0 ok, 3 data problem.  Slow (pure Python): use it on slices, not on the 100k-cycle window.
"""
import argparse, bisect, re, subprocess, sys


def split_range(name):
    m = re.match(r"^(.*) \[(-?\d+):(-?\d+)\]$", name)
    if m:
        return m.group(1), int(m.group(2)), int(m.group(3)), True
    return name, 0, 0, False


def is_array_elem(base):
    return re.search(r"\[[0-9]+\]$", base) is not None


def esc(s):
    return "".join(c if (c.isalnum() and c.isascii()) or c == "_" else "\\" + c for c in s)


def norm(ch):
    return ch if ch in "01" else "x"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--vcd")
    src.add_argument("--fst")
    ap.add_argument("--scope", required=True)
    ap.add_argument("--clock", required=True)
    ap.add_argument("--from-cycle", type=int, required=True)
    ap.add_argument("--cycles", type=int, required=True)
    ap.add_argument("--out-period-ns", type=int, default=10)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-underscore", action="store_true")
    ap.add_argument("--profile")
    ap.add_argument("--bucket", type=int, default=1000)
    ap.add_argument("--arrays", action="store_true")
    ap.add_argument("--mem-from-ports", action="append", default=[])
    a = ap.parse_args()
    A, N = a.from_cycle, a.cycles

    def open_lines():
        """iterator over the VCD text lines (closed by the caller via .close())"""
        if a.fst:
            proc = subprocess.Popen(["fst2vcd", "-f", a.fst], stdout=subprocess.PIPE, text=True, bufsize=1 << 20)
            class It:
                def __iter__(self): return self
                def __next__(self):
                    ln = proc.stdout.readline()
                    if not ln: raise StopIteration
                    return ln.rstrip("\n")
                def close(self):
                    proc.stdout.close(); proc.terminate(); proc.wait()
            return It()
        fh = open(a.vcd)
        class Ft:
            def __iter__(self): return self
            def __next__(self): return next(fh).rstrip("\n")
            def close(self): fh.close()
        return Ft()

    # ---- header
    path = []
    stack = [None]
    tile = None
    ids = {}            # id -> bit count of kept handles
    kept = []           # (node, base, msb, lsb, bussed, nbits, id)
    arrays = others = aliases = arrays_emitted = 0
    mems = []                                     # ports: k -> {field: (id, width)}
    for r in a.mem_from_ports:
        rel, _, d = r.rpartition(":") if ":" in r else (r, "", "")
        if d and (not d.isdigit() or not 0 < int(d) <= 1 << 24):
            sys.exit("bad :DEPTH")
        mems.append({"rel": rel, "node": None, "ports": {}, "rdepth": int(d) if d else 0})
    clock_id = None
    scope_s = a.scope
    it = open_lines()
    for ln in it:
        t = ln.split()
        if not t:
            continue
        if t[0] == "$scope":
            path.append(t[2])
            full = "/".join(path)
            inside = full == scope_s or full.startswith(scope_s + "/")
            if full == scope_s:
                tile = {"name": t[2], "vars": [], "kids": []}
                stack.append(tile)
            elif inside:
                nd = next((k for k in stack[-1]["kids"] if k["name"] == t[2]), None)
                if nd is None:
                    nd = {"name": t[2], "vars": [], "kids": []}
                    stack[-1]["kids"].append(nd)
                stack.append(nd)
                for m in mems:
                    if full[len(scope_s) + 1:] == m["rel"]:
                        if m["node"] is not None:
                            sys.exit("memory scope appears twice")
                        m["node"] = nd; nd["mem"] = m
            else:
                stack.append(None)
        elif t[0] == "$upscope":
            path.pop(); stack.pop()
        elif t[0] == "$var":
            node = stack[-1]
            if node is None:
                continue
            typ, size, vid = t[1], int(t[2]), t[3]
            name = " ".join(t[4:t.index("$end")])
            if typ in ("real", "realtime", "event", "string"):
                others += 1; continue
            base, msb, lsb, bussed = split_range(name)
            rel = "/".join(path)[len(scope_s) + 1:]
            is_clock = (len(path) and "/".join(path) == scope_s and base == a.clock) or \
                       ("/" in a.clock and "/".join(path) != scope_s and (rel + "/" + base) == a.clock)
            if is_array_elem(base):
                if not a.arrays:
                    arrays += 1; continue
                arrays_emitted += 1
            if node.get("mem") is not None:
                mm = re.match(r"^W(\d+)_([a-z]+)$", base)
                if mm and int(mm.group(1)) < 16:
                    node["mem"]["ports"].setdefault(int(mm.group(1)), {})[mm.group(2)] = (vid, size)
            if a.skip_underscore and base.startswith("_") and not is_clock:
                continue
            if is_clock:
                if size != 1:
                    sys.exit("clock is not 1 bit")
                clock_id = vid
            if vid in ids:
                aliases += 1
            else:
                ids[vid] = size
            kept.append((node, base, msb, lsb, bussed, size, vid))
            node["vars"].append(len(kept) - 1)
        elif t[0] == "$enddefinitions":
            break
    it.close()
    if tile is None:
        sys.exit("scope not found")
    if clock_id is None:
        sys.exit("clock not found")
    for m in mems:
        if m["node"] is None:
            sys.exit("--mem-from-ports scope not found")
        P = m["ports"]
        if not P:
            sys.exit("no write port")
        np_ = max(P) + 1
        pl = []
        for k in range(np_):
            q = P.get(k, {})
            if not all(f in q for f in ("en", "addr", "data", "clk")):
                sys.exit("write port lacks en/addr/data/clk")
            if q["clk"][0] != clock_id:
                sys.exit("write-port clock is not the --clock variable")
            if q["en"][1] != 1:
                sys.exit("write enable is not 1 bit")
            pl.append(q)
        m["dw"] = pl[0]["data"][1]; aw = pl[0]["addr"][1]
        for q in pl:
            if q["data"][1] != m["dw"] or q["addr"][1] != aw or not 1 <= aw <= 24:
                sys.exit("write ports differ in width or address too wide")
            if "mask" in q and m["dw"] % q["mask"][1]:
                sys.exit("data width is not a multiple of the mask width")
        m["pl"] = pl; m["depth"] = 1 << aw
        if m["rdepth"]:
            if m["rdepth"] > m["depth"]:
                sys.exit("DEPTH exceeds 2^width(addr)")
            m["depth"] = m["rdepth"]
        m["cur"] = {}; m["lt"] = {}; m["T"] = {}; m["TC"] = {}   # per written word (unwritten words stay X, untouched)

    def events(only=None):
        """yield (time, id, value-string) from the body (re-reads the source, skipping the header)"""
        itb = open_lines()
        try:
            tm = 0
            inbody = False
            for ln in itb:
                if not inbody:
                    inbody = ln.startswith("$enddefinitions")
                    continue
                if not ln or ln[0] == "$":
                    continue
                c = ln[0]
                if c == "#":
                    tm = int(ln[1:]); continue
                if c in "bB":
                    v, vid = ln[1:].split()
                elif c in "rR":
                    continue
                else:
                    v, vid = ln[0], ln[1:]
                if vid not in ids:
                    continue
                if only is not None and vid != only:
                    continue
                yield tm, vid, v
        finally:
            itb.close()

    # ---- pass 1: clock edges
    edges, prev = [], "x"
    for tm, vid, v in events(clock_id):
        c = norm(v[-1] if len(v) else "x")
        if c == "1" and prev == "0":
            edges.append(tm)
        prev = c
        if len(edges) >= A + N + 1:
            break
    if len(edges) < A + N + 1:
        sys.exit("dump holds %d rising edges, window needs %d" % (len(edges), A + N + 1))
    t0, t1 = edges[A], edges[A + N]
    # ---- pass 2
    cur = {v: ["x"] * n for v, n in ids.items()}
    lt = {v: 0 for v in ids}
    T = {v: [[0, 0, 0] for _ in range(n)] for v, n in ids.items()}
    TC = {v: [0] * n for v, n in ids.items()}
    nprof = (A + N + a.bucket - 1) // a.bucket
    prof = [0] * nprof
    mem_tc = [0]
    ep = [0]

    def mem_edges(upto):
        while ep[0] < len(edges) and edges[ep[0]] <= upto and edges[ep[0]] < t1:
            e, j = edges[ep[0]], ep[0]
            for m in mems:
                dw = m["dw"]; pend = {}
                for q in m["pl"]:
                    if cur[q["en"][0]][0] != "1":
                        continue
                    ab = cur[q["addr"][0]]
                    if any(c not in "01" for c in ab):
                        continue
                    ad = int("".join(ab), 2)
                    if ad >= m["depth"]:
                        continue
                    if ad not in pend:
                        pend[ad] = list(m["cur"].get(ad, ["x"] * dw))
                    pv = pend[ad]; dv = cur[q["data"][0]]
                    if "mask" in q:
                        mv = cur[q["mask"][0]]; mw = len(mv); lw = dw // mw
                    for i in range(dw):
                        if "mask" in q:
                            mc = mv[mw - 1 - (dw - 1 - i) // lw]
                            if mc == "0":
                                continue
                            if mc != "1":
                                pv[i] = "x"; continue
                        pv[i] = dv[i]
                for ad in pend:
                    nvv = pend[ad]
                    old = m["cur"].get(ad, ["x"] * dw)
                    Tw = m["T"].setdefault(ad, [[0, 0, 0] for _ in range(dw)]); TCw = m["TC"].setdefault(ad, [0] * dw)
                    lo = max(m["lt"].get(ad, 0), t0); inwin = e >= t0
                    if inwin and e > lo:
                        for k in range(dw):
                            Tw[k]["01x".index(old[k])] += e - lo
                    tog = 0
                    for k in range(dw):
                        if (old[k], nvv[k]) in (("0", "1"), ("1", "0")):
                            tog += 1
                            if inwin:
                                TCw[k] += 1
                    m["cur"][ad] = nvv; m["lt"][ad] = e
                    if inwin:
                        mem_tc[0] += tog
                    if tog:
                        b = j // a.bucket
                        if b < nprof:
                            prof[b] += tog
            ep[0] += 1

    for tm, vid, v in events():
        if mems:
            mem_edges(tm)
        if tm >= t1:
            break
        n = ids[vid]
        if len(v) < n:
            v = (v[0] if v[0] in "xXzZ" else "0") * (n - len(v)) + v
        v = [norm(ch) if ch in "01" else "x" for ch in v[:n]]
        lo = max(lt[vid], t0)
        inwin = tm >= t0
        if inwin and tm > lo:
            for k in range(n):
                T[vid][k]["01x".index(cur[vid][k]) if cur[vid][k] in "01" else 2] += tm - lo
        tog = 0
        for k in range(n):
            o, nv = cur[vid][k], v[k]
            if (o == "0" and nv == "1") or (o == "1" and nv == "0"):
                tog += 1
                if inwin:
                    TC[vid][k] += 1
            cur[vid][k] = nv
        lt[vid] = tm
        if tog:
            cyc = max(bisect.bisect_right(edges, tm) - 1, 0)
            b = cyc // a.bucket
            if b < nprof:
                prof[b] += tog
    if mems:
        mem_edges(t1)
    for m in mems:
        for ad in range(m["depth"]):
            if ad not in m["cur"]:
                m["T"][ad] = [[0, 0, t1 - t0] for _ in range(m["dw"])]; m["TC"][ad] = [0] * m["dw"]
                continue
            lo = max(m["lt"][ad], t0)
            if t1 > lo:
                for k in range(m["dw"]):
                    m["T"][ad][k]["01x".index(m["cur"][ad][k])] += t1 - lo
    for vid, n in ids.items():
        lo = max(lt[vid], t0)
        if t1 > lo:
            for k in range(n):
                T[vid][k]["01x".index(cur[vid][k]) if cur[vid][k] in "01" else 2] += t1 - lo
    # ---- SAIF
    W = t1 - t0
    D = N * a.out_period_ns

    def sc(x):
        return (x * a.out_period_ns * N * 2 + W) // (2 * W)

    def netline(depth, base, idx, vid, k, src=None):
        Tk, tck = (T[vid][k], TC[vid][k]) if src is None else src
        s1 = sc(Tk[1]); sx = sc(Tk[2])
        if s1 > D: s1 = D
        if s1 + sx > D: sx = D - s1
        s0 = D - s1 - sx
        nm = esc(base) + ("\\[%s\\]" % idx if idx is not None else "")
        return "%s(%s (T0 %d) (T1 %d) (TX %d) (TC %d) (IG 0))\n" % (" " * depth, nm, s0, s1, sx, tck)

    def has_nets(n):
        return bool(n["vars"]) or any(has_nets(k) for k in n["kids"])

    out = []

    def wr(n, depth):
        if not has_nets(n):
            return
        out.append("%s(INSTANCE %s\n" % (" " * depth, esc(n["name"])))
        if n["vars"]:
            out.append("%s(NET\n" % (" " * (depth + 1)))
            for ix in n["vars"]:
                _, base, msb, lsb, bussed, nb, vid = kept[ix]
                if not bussed and nb == 1:
                    out.append(netline(depth + 2, base, None, vid, 0))
                else:
                    for p in range(nb):
                        idx = (msb - p if msb >= lsb else msb + p) if bussed else nb - 1 - p
                        out.append(netline(depth + 2, base, str(idx), vid, p))
            m = n.get("mem")
            if m is not None:
                for w in range(m["depth"]):
                    for p in range(m["dw"]):
                        out.append(netline(depth + 2, "Memory[%d]" % w, str(m["dw"] - 1 - p), None, None, (m["T"][w][p], m["TC"][w][p])))
            out.append("%s)\n" % (" " * (depth + 1)))
        for k in n["kids"]:
            wr(k, depth + 1)
        out.append("%s)\n" % (" " * depth))

    comp = scope_s.split("/")
    out.append('(SAIFILE\n(SAIFVERSION "2.0")\n(DIRECTION "backward")\n(DESIGN )\n(DATE "none")\n(VENDOR "fst2saif")\n(PROGRAM_NAME "fst2saif")\n(VERSION "1.0")\n(DIVIDER / )\n(TIMESCALE 1 ns)\n(DURATION %d)\n' % D)
    for i, c in enumerate(comp[:-1]):
        out.append("%s(INSTANCE %s\n" % (" " * i, esc(c)))
    wr(tile, len(comp) - 1)
    for i in range(len(comp) - 2, -1, -1):
        out.append("%s)\n" % (" " * i))
    out.append(")\n")
    open(a.out, "w").write("".join(out))
    if a.profile:
        with open(a.profile, "w") as pf:
            pf.write("bucket,first_cycle,toggles\n")
            for b, x in enumerate(prof):
                pf.write("%d,%d,%d\n" % (b, b * a.bucket, x))
    nbits = sum(ids.values())
    print("variables=%d\nbits=%d\naliases=%d\narrays_skipped=%d\nothers_skipped=%d\ntotal_tc=%d\nedges=%d\nwindow_start_time=%d\nwindow_end_time=%d\nperiod_time=%s"
          % (len(kept), nbits, aliases, arrays, others, sum(sum(x) for x in TC.values()), len(edges), t0, t1, "%.6f" % (W / N)))
    if a.arrays:
        print("arrays_emitted=%d" % arrays_emitted)
    if mems:
        print("mem_words=%d\nmem_words_written=%d\nmem_bits=%d\nmem_tc=%d" % (sum(m["depth"] for m in mems), sum(len(m["cur"]) for m in mems), sum(m["depth"] * m["dw"] for m in mems), mem_tc[0]))


if __name__ == "__main__":
    main()
