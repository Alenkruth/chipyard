#!/usr/bin/env python3
"""Extract one module (by instance path) and its full submodule closure from a single-file
firtool output.

  extract_rtl.py tree  <ooc.sv> [--root MOD] [--depth N]        print the instance tree
  extract_rtl.py find  <ooc.sv> <path-pattern>                  print matching instance paths
  extract_rtl.py emit  <ooc.sv> <path-pattern> <outdir> [<count-pattern>]
                                                                write <top>.sv, <top>_deps.sv,
                                                                filelist.f, top.txt, extract.json;
                                                                count-pattern (default path-pattern)
                                                                counts the copies in the design

A path pattern is a dot-separated list of instance names matched as a SUFFIX of the full instance
path from the top module; '*' matches any one instance name.  The match must be unique.
Deterministic: same input -> byte-identical output (modules in first-definition order).
"""
import hashlib, json, os, re, sys

MOD_RE = re.compile(r"^module\s+([A-Za-z_][\w$]*)")
INST_RE = re.compile(r"^\s+([A-Za-z_][\w$]*)\s+([A-Za-z_][\w$]*)\s*\($")
PINST_RE = re.compile(r"^\s+([A-Za-z_][\w$]*)\s+#\($")
PEND_RE = re.compile(r"^\s+\)\s+([A-Za-z_][\w$]*)\s*\($")


FILE_MARK = "// ----- 8< ----- FILE"
# Preprocessor directives firtool emits between modules (the header macro guards: STOP_COND_,
# ASSERT_VERBOSE_COND_, RANDOMIZE, INIT_RANDOM_PROLOG_, ENABLE_INITIAL_*_).  `timescale is not kept.
PP_RE = re.compile(r"^\s*`(ifndef|ifdef|elsif|else|endif|define|undef)\b")


def parse(path):
    """-> (preamble_lines, {mod: text}, order[mod], {mod: [(child_mod, inst)]})

    Text outside module bodies is kept ONLY if it is a preprocessor directive (or a comment / blank
    line) that appears before firtool's first inlined black-box resource file ("// ----- 8< ----- FILE").
    Everything from that marker on that is not a `module ... endmodule` body is dropped: it holds the
    TestHarness simulation collateral (`timescale, import "DPI-C", the C++ sources with #include
    <vpi_user.h>), which is not hardware and made Vivado fail with [Synth 8-2716].  Modules defined
    inside those resource files (e.g. EICG_wrapper.v, plusarg_reader.v) are still parsed and emitted
    when they are in the extracted closure."""
    pre, mods, order = [], {}, []
    cur, buf = None, []
    in_files = False
    with open(path) as f:
        for line in f:
            if cur is None:
                m = MOD_RE.match(line)
                if m:
                    cur, buf = m.group(1), [line]
                elif line.startswith(FILE_MARK):
                    in_files = True
                elif in_files or line.startswith("// external module"):
                    continue
                elif PP_RE.match(line) or line.lstrip().startswith("//") or not line.strip():
                    pre.append(line)
                else:
                    raise SystemExit("unexpected non-module text before the first resource file: %r" % line[:120])
            else:
                buf.append(line)
                if line.startswith("endmodule"):
                    if cur in mods:
                        raise SystemExit("duplicate module definition: %s" % cur)
                    mods[cur] = "".join(buf)
                    order.append(cur)
                    cur, buf = None, []
    if cur is not None:
        raise SystemExit("unterminated module %s" % cur)
    insts = {}
    for name, text in mods.items():
        lst, pending = [], None
        for line in text.split("\n")[1:]:
            line = line.split("//", 1)[0].rstrip()
            if pending is not None:
                m = PEND_RE.match(line)
                if m:
                    lst.append((pending, m.group(1)))
                    pending = None
                continue
            m = INST_RE.match(line)
            if m and m.group(1) in mods:
                lst.append((m.group(1), m.group(2)))
                continue
            m = PINST_RE.match(line)
            if m and m.group(1) in mods:
                pending = m.group(1)
        insts[name] = lst
    # keep only preamble macro blocks (ifndef/define guards), drop comments-only noise
    return pre, mods, order, insts


def top_of(mods, insts):
    children = {c for l in insts.values() for c, _ in l}
    tops = [m for m in mods if m not in children]
    for pref in ("TestHarness", "ChipTop"):
        if pref in tops:
            return pref
    if len(tops) != 1:
        raise SystemExit("ambiguous top modules: %s" % tops[:10])
    return tops[0]


def walk(insts, root):
    """yield (path_tuple_of_inst_names, module) depth-first"""
    stack = [((), root)]
    while stack:
        path, mod = stack.pop()
        yield path, mod
        for child, inst in reversed(insts[mod]):
            stack.append((path + (inst,), child))


def find(insts, root, pattern):
    pat = pattern.split(".")
    hits = []
    for path, mod in walk(insts, root):
        if len(path) >= len(pat) and all(p == "*" or p == q for p, q in zip(pat, path[-len(pat):])):
            hits.append((path, mod))
    return hits


def closure(insts, mod, order):
    seen, todo = set(), [mod]
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        todo.extend(c for c, _ in insts[m])
    return [m for m in order if m in seen]


def main(argv):
    if len(argv) < 3:
        print(__doc__); return 2
    cmd, sv = argv[1], argv[2]
    pre, mods, order, insts = parse(sv)
    root = top_of(mods, insts)
    if cmd == "tree":
        rmod, depth = root, 99
        if "--root" in argv:
            rmod = argv[argv.index("--root") + 1]
        if "--depth" in argv:
            depth = int(argv[argv.index("--depth") + 1])
        for path, mod in walk(insts, rmod):
            if len(path) <= depth:
                print("  " * len(path) + (path[-1] if path else "") + " : " + mod)
        return 0
    if cmd == "find":
        for path, mod in find(insts, root, argv[3]):
            print(".".join(path), mod)
        return 0
    if cmd == "emit":
        pattern, out = argv[3], argv[4]
        hits = find(insts, root, pattern)
        if len(hits) != 1:
            print("pattern %r matched %d instances: %s" % (pattern, len(hits),
                  [".".join(p) + ":" + m for p, m in hits[:8]]), file=sys.stderr)
            return 3
        path, top = hits[0]
        deps = [m for m in closure(insts, top, order) if m != top]
        os.makedirs(out, exist_ok=True)
        # collapse runs of blank lines (the dropped text leaves them); content-only, deterministic
        preamble = re.sub(r"\n{3,}", "\n\n", "".join(pre)).rstrip("\n") + "\n\n"
        files = [top + ".sv"]
        with open(os.path.join(out, top + ".sv"), "w") as f:
            f.write(preamble + mods[top])
        if deps:
            files.append(top + "_deps.sv")
            with open(os.path.join(out, top + "_deps.sv"), "w") as f:
                f.write(preamble + "".join(mods[m] for m in deps))
        with open(os.path.join(out, "filelist.f"), "w") as f:
            f.write("".join(x + "\n" for x in files))
        with open(os.path.join(out, "top.txt"), "w") as f:
            f.write(top + "\n")
        lines = sum(mods[m].count("\n") for m in [top] + deps)
        cpat = argv[5] if len(argv) > 5 else pattern
        copies = find(insts, root, cpat)
        info = {"top": top, "instance_path": ".".join(path), "n_modules": 1 + len(deps),
                "count_pattern": cpat, "copies": [".".join(p) + ":" + m for p, m in copies],
                "rtl_lines": lines, "files": files,
                "rtl_sha1": hashlib.sha1("".join(mods[m] for m in [top] + deps).encode()).hexdigest()}
        with open(os.path.join(out, "extract.json"), "w") as f:
            json.dump(info, f, indent=1, sort_keys=True); f.write("\n")
        print(json.dumps(info, sort_keys=True))
        return 0
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
