"""Shared helpers for the OOC analysis scripts (stdlib only)."""
import csv, json, os, re

COLS = ["total_luts", "logic_luts", "lutram", "srl", "ffs", "ramb36", "ramb18", "uram", "dsp"]
VARIANTS = ["base", "ift", "ift_nobridge", "reconf_base", "reconf_ift"]
SWEEP_VARIANTS = ["base", "ift"]
# Modules whose IFT bridge/state taps (require() at icache.scala:283, btb.scala:331, tage.scala:489, dcache.scala:1775,
# frontend.scala:1383 for BoomRAS) pin the max geometry: they get ift_nobridge (taps off, in-core IFT on) next to ift.
NOBRIDGE_MODULES = ["BTB", "Tage", "ICache", "DCache", "BoomRAS"]
# Static non-max sizes of these modules are mis-elaborated without the staged clamp
# (staged/boom-ooc-static-fixes.diff, sha1 6035b65b): base/ift_nobridge non-max points get status needs_clamp_fix.
# FetchTargetQueue is affected only at non power-of-2 sizes (agent A2 section 6).
CLAMP_MODULES = ["IssueInt", "IssueMem", "IssueFp", "Rob", "Lsu", "DCache", "FetchBuffer"]
CLAMP_POW2_OK = ["FetchTargetQueue"]
# Points that need the staged rob.scala:474 lsuWbPorts fix: ift_nobridge at full-mode width 1 (Core, Rob).
NEEDS_FIX_MODULES = ["Core", "Rob"]
# statuses whose results are not trustworthy / not yet runnable: excluded from derived tables
HOLD_STATUSES = ["infeasible", "needs_clamp_fix", "needs_fix"]
# (module, param, value) -> reason; such points are listed with status "infeasible" and are never run.
INFEASIBLE = {("FetchBuffer", "entries", "8"): "cannot elaborate statically (fetch-buffer.scala:87)"}
WIDTH_MODES = ["full", "dispatch_only"]


def repo_root():
    r = os.environ.get("CHIPYARD_ROOT")
    if r:
        return r
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", ".."))


def default_corefuzzing_scala():
    return os.path.join(repo_root(), "generators", "rocket-chip", "src", "main", "scala", "util", "CoreFuzzing.scala")


def parse_options_scala(path):
    """Return {name: [ints]} for every `def xOptions = Seq(a, b, ...)` in CoreFuzzing.scala."""
    txt = []
    with open(path) as f:
        for line in f:
            txt.append(re.sub(r"//.*", "", line))
    txt = "\n".join(txt)
    out = {}
    for m in re.finditer(r"def\s+(\w+Options)\s*=\s*Seq\(([^)]*)\)", txt):
        vals = [int(x) for x in re.findall(r"-?\d+", m.group(2))]
        out[m.group(1)] = vals
    return out


def load_options(json_path=None, scala_path=None):
    if json_path:
        with open(json_path) as f:
            return json.load(f), json_path
    p = scala_path or default_corefuzzing_scala()
    return parse_options_scala(p), p


def parse_params(s):
    d = {}
    for kv in s.split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            d[k] = v
    return d


def split_point(point):
    module, variant, params = point.split("/", 2)
    return module, variant, params


def width_of(params):
    return parse_params(params).get("width", "")


def key_wo_width(params):
    return ",".join(kv for kv in params.split(",") if not kv.startswith("width="))


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def load_results(work):
    """Scan $work/points/<module>/<variant>/<params>/result.json; sorted by point."""
    res = []
    base = os.path.join(work, "points")
    for dp, dn, fn in os.walk(base):
        dn.sort()
        if "result.json" in fn:
            try:
                with open(os.path.join(dp, "result.json")) as f:
                    r = json.load(f)
            except (OSError, ValueError):
                continue
            if "point" not in r:
                rel = os.path.relpath(dp, base)
                r["point"] = rel.replace(os.sep, "/")
            res.append(r)
    res.sort(key=lambda r: r["point"])
    return res


def col(d, c):
    v = d.get(c, 0) if d else 0
    return v if isinstance(v, (int, float)) else 0
