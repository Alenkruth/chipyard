#!/usr/bin/env python3
"""Synthesize fake result.json files (schema: harness-spec.md) for tests. Usage: make_fixtures.py OUTDIR"""
import hashlib, json, os, sys


def h(s, n):
    return int(hashlib.sha1(s.encode()).hexdigest()[:8], 16) % n


def mk(point, scale, ift):
    cols = lambda tag, k: {"total_luts": int(scale * k) + h(point + tag, 50), "logic_luts": int(scale * k) - 5, "lutram": h(point + tag + "a", 9),
                           "srl": 0, "ffs": int(scale * k * 2), "ramb36": h(point + tag + "b", 3), "ramb18": 0, "uram": 0, "dsp": 0}
    k = 1 + (0.15 if ift else 0)
    children = [{"inst": "core", "module": "Body", **cols("c0", 0.9)}]
    if ift:
        children.append({"inst": "ift_bank", "module": "RobIftBank", "ift": True, **cols("c1", 0.1)})
    return {"point": point, "module": point.split("/")[0], "variant": point.split("/")[1], "params": point.split("/", 2)[2],
            "top": point.split("/")[0], "status": "ok", "wall_s": 60 + h(point, 100), "peak_mb": 1000 + h(point, 500),
            "total": cols("t", k), "children": children, "vivado_version": "fake", "part": "xcu250", "clock_ns": 10.0, "tcl_sha1": "0" * 40}


def main(out):
    pts = []
    # scope B: base at several sizes/widths; ift only at max size and width 4; ift_nobridge at max size for width<4 (ift does not
    # elaborate there) and at the smallest-size spot check; reconf at max.
    for mod, mx, small in (("FetchBuffer", 64, 16), ("Rob", 256, 32)):
        for e in (mx, small, 128 if mod == "Rob" else 32):
            for w in (4, 2):
                pts.append("%s/base/entries=%d,width=%d" % (mod, e, w))
        pts += ["%s/ift/entries=%d,width=4" % (mod, mx), "%s/ift_nobridge/entries=%d,width=2" % (mod, mx),
                "%s/ift_nobridge/entries=%d,width=4" % (mod, small)]
    pts += ["Rob/reconf_base/entries=256,width=4", "Rob/reconf_ift/entries=256,width=4"]
    for w in (4, 2):
        pts.append("Core/base/width=%d" % w)
    pts += ["Core/ift/width=4", "Core/ift_nobridge/width=2",
            "Core/base/width=2,width_mode=dispatch_only", "Core/ift/width=2,width_mode=dispatch_only"]
    # bridge-pinned module: ift_nobridge at the smallest size, ift (taps on) and ift_nobridge at max
    for sets, ways in ((64, 8), (32, 4)):
        pts.append("ICache/base/sets=%d,ways=%d" % (sets, ways))
    pts += ["ICache/ift_nobridge/sets=64,ways=8", "ICache/ift_nobridge/sets=32,ways=4", "ICache/ift/sets=64,ways=8"]
    for part in ("IssueInt", "RegfileInt"):
        for w in (4, 2):
            pts.append("%s/base/entries=32,width=%d" % (part, w))
        pts += ["%s/ift/entries=32,width=4" % part, "%s/ift_nobridge/entries=32,width=2" % part]
    for p in sorted(set(pts)):
        m, v, params = p.split("/", 2)
        scale = {"FetchBuffer": 300, "Rob": 4000, "Core": 20000, "ICache": 1500, "IssueInt": 600, "RegfileInt": 900}[m] // (1 if m == "Core" else 2)
        scale = scale * (int(params.split("entries=")[1].split(",")[0]) // 32 if "entries=" in params else 1)
        scale = scale * (1 + int(params.split("width=")[1].split(",")[0]) // 2) // 3 if "width=" in params else scale
        if v == "ift" and m == "ICache":
            scale = int(scale * 1.02)
        if v.startswith("reconf"):
            scale = int(scale * 1.03)
        d = os.path.join(out, "points", m, v, params)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "result.json"), "w") as f:
            json.dump(mk(p, scale, v.startswith("ift") or v == "reconf_ift"), f, indent=1, sort_keys=True)
    # one failed point
    d = os.path.join(out, "points", "Rob", "base", "entries=32,width=1")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "result.json"), "w") as f:
        json.dump({"point": "Rob/base/entries=32,width=1", "status": "fail", "total": {}}, f)


if __name__ == "__main__":
    main(sys.argv[1])
