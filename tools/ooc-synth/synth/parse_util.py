#!/usr/bin/env python3
"""Parse a Vivado `report_utilization -hierarchical` table into result.json (spec schema).
Run as: python3 -I parse_util.py --rpt R --out result.json --point P [--meta k=v ...]"""
import argparse, json, re, sys

COLS = ["total_luts", "logic_luts", "lutram", "srl", "ffs", "ramb36", "ramb18", "uram", "dsp"]
HDR_NAMES = ["Total LUTs", "Logic LUTs", "LUTRAMs", "SRLs", "FFs", "RAMB36", "RAMB18", "URAM", "DSP Blocks"]
IFT_RE = re.compile(r"ift", re.I)


def _num(cell):
    m = re.match(r"\s*(\d+)", cell)
    if not m:
        raise ValueError("bad cell %r" % cell)
    return int(m.group(1))


def parse_rows(text):
    """Return list of (depth, inst, module, {col: int}) from the hierarchy table."""
    header = None
    rows = []
    for ln in text.splitlines():
        if not ln.startswith("|"):
            continue
        cells = ln.strip().strip("|").split("|")
        names = [c.strip() for c in cells]
        if header is None:
            if names and names[0] == "Instance":
                header = names
                idx = []
                for h in HDR_NAMES:
                    if h not in names:
                        raise ValueError("column %r missing in header %r" % (h, names))
                    idx.append(names.index(h))
                midx = names.index("Module")
            continue
        if len(cells) != len(header):
            continue
        raw = cells[0]
        depth = (len(raw) - len(raw.lstrip(" "))- 1) // 2
        inst = raw.strip()
        rows.append((depth, inst, names[midx], {c: _num(cells[i]) for c, i in zip(COLS, idx)}))
    if header is None:
        raise ValueError("no hierarchy table header found")
    if not rows:
        raise ValueError("no rows in hierarchy table")
    return rows


def build(rows):
    top_depth, top_inst, top_mod, top_vals = rows[0]
    if top_depth != 0:
        raise ValueError("first row not at depth 0")
    children = []
    for depth, inst, mod, vals in rows[1:]:
        if depth < 1 or depth > 2:
            continue
        d = {"inst": inst, "module": mod, "depth": depth}
        d.update(vals)
        d["ift"] = bool(IFT_RE.search(inst) or IFT_RE.search(mod))
        children.append(d)
    return top_inst, top_mod, top_vals, children


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--point", required=True)
    ap.add_argument("--meta", action="append", default=[], help="key=value, merged into result.json (ints auto-converted)")
    a = ap.parse_args()
    with open(a.rpt) as f:
        rows = parse_rows(f.read())
    top_inst, top_mod, total, children = build(rows)
    meta = {}
    for kv in a.meta:
        k, _, v = kv.partition("=")
        try:
            v = int(v)
        except ValueError:
            try:
                v = float(v)
            except ValueError:
                pass
        meta[k] = v
    parts = a.point.split("/")
    res = {"point": a.point,
           "module": parts[0] if len(parts) > 0 else "",
           "variant": parts[1] if len(parts) > 1 else "",
           "params": parts[2] if len(parts) > 2 else "",
           "top": meta.pop("top", top_mod),
           "status": "ok",
           "wall_s": meta.pop("wall_s", None),
           "peak_mb": meta.pop("peak_mb", None),
           "total": total,
           "children": children}
    res.update(meta)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1, sort_keys=False)
        f.write("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("parse_util: %s" % e, file=sys.stderr)
        sys.exit(1)
