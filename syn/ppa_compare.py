#!/usr/bin/env python3
"""Cross-stage PPA comparison for one config:

  architectural estimate (iommu_sim, normalized)  ->  post-synthesis (yosys+OpenSTA)
  ->  post-placement+repair  ->  post-CTS  ->  post-global-route   (OpenROAD)

Pulls: the simulator's normalized PPA (computed for the matching config), the
synth JSON (results/<name>.json), and the staged P&R JSON (results/<name>_pnr.json).
Writes results/ppa_compare.md (+ .json). Units differ per stage and are labelled:
architectural area is gate-equivalents (GE) / normalized energy; synth/P&R are
sky130 um^2 / MHz / Watts -- the GE row is the relative architectural reference,
the EDA rows are the absolute physical PPA.

Usage:  python3 syn/ppa_compare.py [name]
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"


def architectural(name):
    """Run the simulator's estimator for the config matching the RTL Full point."""
    sys.path.insert(0, str(ROOT / "iommu_sim"))
    try:
        from config import Config
        from runner import run_sim, summarize
    except Exception as e:
        return {"error": str(e)}
    cfg = Config.from_dict({
        "mode": "bare",                       # = MODE s1_only single-stage
        "caches": {"iotlb": {"entries": 64, "assoc": 4},
                   "s1_pwc": {"l2": {"entries": 8}, "l1": {"entries": 16}},
                   "s2_pwc": {"enabled": False}, "table_gpa": {"enabled": False},
                   "data_gpa": {"enabled": False}, "ddtc": {"entries": 4},
                   "pdtc": {"enabled": False}, "msi": {"enabled": False},
                   "coalesce_factor": 8},
        "walkers": {"num_walkers": 4}, "buffers": {"iommu_req_buffer": 16, "io_bridge_buffer": 16},
        "workload": {"iova_pattern": "sequential", "n_requests": 8000},
    })
    sim, m = run_sim(cfg, warmup_frac=0.05)
    s = summarize(cfg, sim, m)
    return {"area_GE": round(s["area_ge"], 1),
            "energy_per_xlate_norm": round(s["energy_per_translation"], 2),
            "fmax_mhz": "n/a (behavioral)"}


def jload(p):
    return json.loads((RESULTS / p).read_text()) if (RESULTS / p).exists() else {}


def main(name="full"):
    arch = architectural(name)
    syn = jload(f"{name}.json")
    pnr = jload(f"{name}_pnr.json")
    stages = pnr.get("stages", {})

    rows = []
    rows.append(("architectural (sim, normalized)",
                 f"{arch.get('area_GE','?')} GE", arch.get("fmax_mhz", "?"),
                 f"{arch.get('energy_per_xlate_norm','?')} /xlate (norm)"))
    if syn:
        p = syn.get("power_W", {})
        rows.append(("post-synthesis (yosys+OpenSTA)",
                     f"{syn.get('area_um2_total',0):.0f} um^2 (cells)",
                     f"{(syn.get('fmax_mhz') or 0):.1f} MHz",
                     f"{p.get('total_W',0):.3f} W"))
    label = {"PLACE": "post-place+repair", "CTS": "post-CTS",
             "GROUTE": "post-global-route", "DROUTE": "post-detailed-route"}
    for k in ["PLACE", "CTS", "GROUTE", "DROUTE"]:
        if k in stages:
            st = stages[k]
            rows.append((label[k] + " (OpenROAD)",
                         f"{st.get('die_area_um2',0)} um^2 (die@{st.get('utilization_pct','?')}%)",
                         f"{(st.get('fmax_mhz') or 0):.1f} MHz",
                         f"{(st.get('power_W_total') or 0):.3f} W"))

    md = ["# PPA across stages — config `%s`" % name, "",
          "| stage | area | Fmax | power |", "|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} |")
    md += ["",
           "Notes:",
           "- *architectural* = simulator estimate, **normalized** units (GE / norm-energy) —",
           "  the relative reference, not directly comparable to um^2 (calibrate via fit factor).",
           "- *post-synthesis* area = standard-cell area (no die/whitespace); Fmax/power from OpenSTA.",
           "- *post-place..route* area = **die** area at the placement utilization; Fmax from worst",
           "  slack at the 2.5 ns (400 MHz) target; power includes wire RC (more realistic each stage).",
           "- Fmax improves synth→place (buffering fixes the fanout-466 net) then dips slightly with",
           "  CTS/route parasitics; the design still needs pipelining to reach 400 MHz.",
           ]
    (RESULTS / f"{name}_ppa.md").write_text("\n".join(md) + "\n")
    (RESULTS / f"{name}_ppa.json").write_text(json.dumps(
        {"config": name,
         "architectural_rtl": arch,        # simulator estimate (normalized)
         "post_synth": syn,                # results/<name>.json
         "pnr_stages": stages},            # results/<name>_pnr.json stages
        indent=2))
    print("\n".join(md))
    append_history(name, syn, pnr, stages)


MODE_NAME = {0: "bare", 1: "s1_only", 2: "s2_only", 3: "nested"}


def arch_summary(params):
    p = params or {}
    sto = {0: "ff", 1: "sram"}
    return (f"{MODE_NAME.get(p.get('MODE'), '?')} coal={p.get('COALESCE_FACTOR','?')} "
            f"N={p.get('NUM_WALKERS','?')} buf={p.get('BUFFER_DEPTH','?')} "
            f"pf={p.get('PREFETCH_EN','?')} "
            f"IOTLB={p.get('IOTLB_ENTRIES','?')}/{p.get('IOTLB_ASSOC','?')}/{sto.get(p.get('IOTLB_STORAGE'),'?')} "
            f"PWC={p.get('S1PWC_ENTRIES','?')}/{sto.get(p.get('S1PWC_STORAGE'),'?')}")


def append_history(name, syn, pnr, stages):
    """Append one row to results/ppa_compare.md -- an accumulating log of how PPA
    moves with architecture + library changes (kept across runs)."""
    hist = RESULTS / "ppa_compare.md"
    header = (
        "# IOMMU PPA history (append-only)\n\n"
        "Each P&R run appends a row. Columns: synth = yosys+OpenSTA (cell area); "
        "route = last P&R stage (die area, Fmax @2.5ns target, power incl. wire RC).\n\n"
        "| # | config | library | architecture | synth Fmax | synth area(um^2) | synth P(W) "
        "| route stage | route Fmax | die(um^2) | route P(W) | GDS |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    if not hist.exists():
        hist.write_text(header)
    n = sum(1 for ln in hist.read_text().splitlines() if ln.startswith("| ") and ln[2:3].isdigit())
    variant = pnr.get("variant", "hd")
    arch = arch_summary(syn.get("params"))
    sfmax = f"{(syn.get('fmax_mhz') or 0):.1f}" if syn else "-"
    sarea = f"{syn.get('area_um2_total',0):.0f}" if syn else "-"
    spow = f"{syn.get('power_W',{}).get('total_W',0):.3f}" if syn else "-"
    last = next((k for k in ["DROUTE", "GROUTE", "CTS", "PLACE"] if k in stages), None)
    if last:
        st = stages[last]
        rfmax = f"{(st.get('fmax_mhz') or 0):.1f}"; rdie = f"{st.get('die_area_um2','-')}"
        rpow = f"{(st.get('power_W_total') or 0):.3f}"
    else:
        last = rfmax = rdie = rpow = "-"
    gds = pnr.get("gds") or "-"
    row = (f"| {n+1} | {name} | sky130_fd_sc_{variant} | {arch} | {sfmax} | {sarea} | {spow} "
           f"| {last} | {rfmax} | {rdie} | {rpow} | {gds} |\n")
    with open(hist, "a") as f:
        f.write(row)
    print(f"  appended to results/ppa_compare.md (row {n+1}: {name}/{variant})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "full")
