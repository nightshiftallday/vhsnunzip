#!/usr/bin/env python3
"""Summarizes harness results as Markdown tables.

  summarize.py perf RESULT.json|DIR ...       throughput (sim unit tests, hw benchmark)
  summarize.py impl REPORT_DIR ...            utilization and timing (ooc.tcl, coyote_build.sh)
  summarize.py compare BASE CAND              per-corpus / per-build deltas, CAND vs BASE
                                              (each a result dir, JSON file or report dir)
"""

import argparse
import csv
import glob
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

from vhsnunzip_harness import regs  # noqa: E402


# -- Loading ---------------------------------------------------------------------

def config_label(cfg: dict) -> str:
    build = cfg.get("build", {})
    label = "B%s %s %s%s" % (cfg.get("c_bytes", "?"), "dual" if cfg.get("dual_issue") else "single",
                             cfg.get("ram_style", "?"), "" if cfg.get("long_chunks", 1) else " short")
    git = cfg.get("git") or build.get("git")
    return label + (" @" + git if git else "")


def perf_row(path: str) -> dict:
    with open(path) as f:
        res = json.load(f)
    cfg, corpus, counters = res["config"], res["corpus"], res["counters"]
    reps = res.get("run", {}).get("reps", 1)
    d = regs.derive(counters, cfg["c_bytes"])
    cycles = max(counters["cycles"], 1)
    elements = corpus.get("stats", {}).get("elements", 0)
    row = {
        "path": path,
        "kind": res["kind"],
        "config": config_label(cfg),
        "corpus": corpus["name"],
        "ratio": corpus["raw_bytes"] / max(corpus["comp_bytes"], 1),
        "bytes_per_element": corpus["raw_bytes"] / max(elements, 1),
        "out_bpc": d["out_bytes_per_cycle"],
        "peak": d["peak_fraction"],
        "elem_per_cycle": elements * reps / cycles,
        "lat": d.get("lat_avg"),
        "check": d["check_class_sum"],
    }
    for k in regs.CYCLE_CLASSES[1:]:
        row[k] = d["frac_" + k]
    model = corpus.get("model")
    if model and model.get("wi") == cfg["c_bytes"]:
        key = "fold_cycles" if cfg.get("dual_issue") else "single_cycles"
        if model.get(key):
            row["model_bpc"] = corpus["raw_bytes"] / model[key]
    clk = cfg.get("clk_mhz_measured") or cfg.get("clk_mhz") or cfg.get("build", {}).get("clk_mhz")
    if clk:
        row["clk_mhz"] = clk
        row["core_gbps"] = d["out_bytes_per_cycle"] * clk / 1e3
    if "run" in res:
        row["host_gbps"] = res["run"].get("host_gbps")
        row["verify"] = res["run"].get("verify")
    return row


def number(text: str):
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return text


def parse_util_hier(path: str) -> list:
    """Rows of a Vivado hierarchical utilization table with their depth."""
    rows, header = [], None
    with open(path) as f:
        for line in f:
            if not line.startswith("|"):
                continue
            cells = line.rstrip("\n").split("|")[1:-1]
            if header is None:
                if cells and cells[0].strip() == "Instance":
                    header = [c.strip().lower() for c in cells]
                continue
            raw = cells[0]
            row = {"instance": raw.strip(), "module": cells[1].strip(),
                   "depth": (len(raw) - len(raw.lstrip()) - 1) // 2}
            for key, value in zip(header[2:], cells[2:]):
                row[key] = number(value.strip())
            rows.append(row)
    return rows


def module_of(pin: str) -> str:
    """Core-relative instance path of a timing path pin, without generate labels."""
    if "core_inst/" not in pin:
        return "(outside core)"
    parts = pin.split("core_inst/", 1)[1].split("/")[:-2]  # drop cell and pin
    return "/".join(p.split(".")[-1] for p in parts) or "(core top)"


def impl_row(path: str) -> dict:
    row = {"path": path, "config": {}}
    cfg_path = os.path.join(path, "harness_config.json")
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            row["config"] = json.load(f)
    util = parse_util_hier(os.path.join(path, "core_utilization_hier.rpt"))
    row["util"] = util
    row["total"] = util[0] if util else {}

    paths = []
    with open(os.path.join(path, "core_paths.csv")) as f:
        for p in csv.DictReader(f):
            if not p["slack"] or p["slack"] in ("inf", "{}"):
                continue
            p = {k: number(v) for k, v in p.items()}
            p["internal"] = "core_inst/" in str(p["startpoint"]) and "core_inst/" in str(p["endpoint"])
            paths.append(p)
    row["paths"] = paths
    internal = [p for p in paths if p["internal"]]
    if internal:
        worst = internal[0]
        row["wns_internal"] = worst["slack"]
        row["period"] = worst["requirement"]
        row["fmax_est"] = 1000.0 / (worst["requirement"] - worst["slack"])
        row["levels_hist"] = Counter(int(p["logic_levels"]) for p in internal)
    boundary = [p for p in paths if not p["internal"]]
    if boundary:
        row["wns_io"] = boundary[0]["slack"]
    design = {}
    if os.path.exists(os.path.join(path, "design.csv")):
        with open(os.path.join(path, "design.csv")) as f:
            design = {r["metric"]: number(r["value"]) for r in csv.DictReader(f)}
    row["design"] = design
    return row


def collect(paths: list):
    perf, impl = [], []
    for path in paths:
        if os.path.isdir(path) and os.path.exists(os.path.join(path, "core_utilization_hier.rpt")):
            impl.append(impl_row(path))
        elif os.path.isdir(path):
            perf += [perf_row(p) for p in sorted(glob.glob(os.path.join(path, "**", "*.json"), recursive=True))
                     if os.path.basename(p) != "harness_config.json"]
        else:
            perf.append(perf_row(path))
    return perf, impl


# -- Output ----------------------------------------------------------------------

def fmt(value, spec="%.3f"):
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return spec % value
    return str(value)


def table(header: list, rows: list) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def print_perf(rows: list) -> None:
    header = ["kind", "config", "corpus", "ratio", "B/elem", "B/cycle", "% peak", "elem/cycle",
              "model B/cycle", "core stall", "in empty", "in only", "out blocked", "latency",
              "clk MHz", "core GB/s", "host GB/s"]
    body = []
    for r in rows:
        body.append([r["kind"], r["config"], r["corpus"], fmt(r["ratio"], "%.2f"),
                     fmt(r["bytes_per_element"], "%.1f"), fmt(r["out_bpc"]), fmt(100 * r["peak"], "%.0f"),
                     fmt(r["elem_per_cycle"]), fmt(r.get("model_bpc")),
                     fmt(100 * r["core_stall"], "%.1f%%"), fmt(100 * r["in_empty"], "%.1f%%"),
                     fmt(100 * r["in_only"], "%.1f%%"), fmt(100 * r["de_blocked"], "%.1f%%"),
                     fmt(r["lat"], "%.0f"), fmt(r.get("clk_mhz"), "%.1f"), fmt(r.get("core_gbps"), "%.2f"),
                     fmt(r.get("host_gbps"), "%.2f")])
        if not r["check"]:
            print("warning: %s: cycle classes do not add up to cycles" % r["path"], file=sys.stderr)
        if r.get("verify", "ok") not in ("ok", "skipped"):
            print("warning: %s: verification %s" % (r["path"], r["verify"]), file=sys.stderr)
    print(table(header, body))


UTIL_COLUMNS = ["total luts", "logic luts", "lutrams", "srls", "ffs", "ramb36", "ramb18", "uram", "dsp blocks"]


def impl_label(r: dict) -> str:
    cfg = r["config"]
    if not cfg:
        return r["path"]
    clk = "%s MHz" % fmt(cfg.get("clk_mhz"), "%.0f")
    return "%s, %s %s" % (config_label(cfg), cfg.get("flow", "?"), clk)


def print_impl(rows: list, depth: int = 2, paths: int = 8) -> None:
    header = ["build"] + UTIL_COLUMNS + ["period ns", "WNS internal", "f_max est MHz", "WNS core I/O",
                                         "design WNS", "max levels"]
    body = []
    for r in rows:
        t = r["total"]
        levels = max(r["levels_hist"]) if r.get("levels_hist") else None
        body.append([impl_label(r)] + [fmt(t.get(c)) for c in UTIL_COLUMNS] +
                    [fmt(r.get("period")), fmt(r.get("wns_internal")), fmt(r.get("fmax_est"), "%.0f"),
                     fmt(r.get("wns_io")), fmt(r["design"].get("design_wns")), fmt(levels)])
    print(table(header, body))

    for r in rows:
        print("\n### %s\n" % impl_label(r))
        sub = [u for u in r["util"] if 0 < u["depth"] <= depth and not u["instance"].startswith("(")]
        print(table(["instance", "module"] + UTIL_COLUMNS,
                    [["&nbsp;" * 2 * (u["depth"] - 1) + u["instance"], u["module"]] +
                     [fmt(u.get(c)) for c in UTIL_COLUMNS] for u in sub]))
        if r.get("levels_hist"):
            print("\nLogic levels of internal paths: " +
                  ", ".join("%d: %d" % kv for kv in sorted(r["levels_hist"].items())))
        print()
        print(table(["slack", "levels", "logic/net ns", "from", "to"],
                    [[fmt(p["slack"]), fmt(p["logic_levels"]),
                      "%s/%s" % (fmt(p["logic_delay"]), fmt(p["net_delay"])),
                      module_of(str(p["startpoint"])), module_of(str(p["endpoint"]))]
                     for p in r["paths"][:paths]]))


def print_compare(base: list, cand: list, kind: str) -> None:
    if kind == "perf":
        index = {(r["kind"], r["corpus"]): r for r in base}
        body = []
        for r in cand:
            b = index.get((r["kind"], r["corpus"]))
            if not b:
                continue
            gain = r["out_bpc"] / b["out_bpc"] - 1 if b["out_bpc"] else 0
            gbps = (r.get("core_gbps") or 0) / b["core_gbps"] - 1 if b.get("core_gbps") else None
            body.append([r["kind"], r["corpus"], fmt(b["out_bpc"]), fmt(r["out_bpc"]),
                         fmt(100 * gain, "%+.1f%%"), fmt(None if gbps is None else 100 * gbps, "%+.1f%%"),
                         fmt(100 * (r["core_stall"] - b["core_stall"]), "%+.1f pp")])
        print(table(["kind", "corpus", "base B/cycle", "cand B/cycle", "B/cycle", "GB/s", "core stall"], body))
    else:
        body = []
        for b, r in zip(base, cand):
            row = [impl_label(b) + " -> " + impl_label(r)]
            for c in UTIL_COLUMNS:
                bv, rv = b["total"].get(c), r["total"].get(c)
                row.append("%s (%+d)" % (fmt(rv), rv - bv) if isinstance(bv, int) and isinstance(rv, int) else fmt(rv))
            row += [fmt(r.get("fmax_est"), "%.0f") + (" (%+.0f)" % (r["fmax_est"] - b["fmax_est"])
                                                      if b.get("fmax_est") and r.get("fmax_est") else "")]
            body.append(row)
        print(table(["builds"] + UTIL_COLUMNS + ["f_max est MHz"], body))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("perf")
    sp.add_argument("paths", nargs="+")
    sp = sub.add_parser("impl")
    sp.add_argument("paths", nargs="+")
    sp.add_argument("--depth", type=int, default=2, help="hierarchy depth of the per-module table")
    sp.add_argument("--paths-shown", type=int, default=8)
    sp = sub.add_parser("compare")
    sp.add_argument("base")
    sp.add_argument("cand")
    args = p.parse_args()

    if args.cmd == "perf":
        print_perf(collect(args.paths)[0])
    elif args.cmd == "impl":
        print_impl(collect(args.paths)[1], args.depth, args.paths_shown)
    else:
        base_perf, base_impl = collect([args.base])
        cand_perf, cand_impl = collect([args.cand])
        if base_perf and cand_perf:
            print_compare(base_perf, cand_perf, "perf")
        if base_impl and cand_impl:
            print_compare(base_impl, cand_impl, "impl")


if __name__ == "__main__":
    main()
