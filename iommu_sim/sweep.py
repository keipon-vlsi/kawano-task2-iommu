"""Sweep / search CLI (usage_manual §5/§6/§10).

    python3 sweep.py --config configs/baseline.yaml --measure peaks    # 3c/3d
    python3 sweep.py --config configs/baseline.yaml --search min_hw     # min HW to sustain
    python3 sweep.py --config configs/space.yaml --pareto               # area-energy Pareto + CSV
    python3 sweep.py --config configs/space.yaml --pareto --emit-candidates   # .svh params
    python3 sweep.py --config configs/baseline.yaml --emit-trace out/trace.csv

Wire-rate-met = steady-state stall-free with a small margin (cold-start excluded
via a warmup fraction). The Pareto front minimizes (area_GE, energy/translation)
among configs that meet wire rate; an auxiliary scalar area x energy/translation
is also tabulated. Candidate Pareto points are emitted as SystemVerilog parameter
files for the next (RTL) phase.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import os

from config import Config
from runner import run_sim, summarize, wire_rate_met
from workload import generate, export_csv

WARMUP = 0.05


# --------------------------------------------------------------------------
# dotted-path config editing
# --------------------------------------------------------------------------
def set_path(d, path, value):
    keys = path.split(".")
    cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def expand_grid(base_dict, grid):
    """Cartesian product over grid axes -> list of config dicts."""
    if not grid:
        return [base_dict]
    axes = list(grid.items())
    names = [a[0] for a in axes]
    out = []
    for combo in itertools.product(*[a[1] for a in axes]):
        d = _deepcopy(base_dict)
        for name, val in zip(names, combo):
            set_path(d, name, val)
        out.append((dict(zip(names, combo)), d))
    return out


def _deepcopy(d):
    import copy
    return copy.deepcopy(d)


# --------------------------------------------------------------------------
# min-HW search (§9)
# --------------------------------------------------------------------------
def _run(cfg):
    sim, m = run_sim(cfg, warmup_frac=WARMUP)
    return sim, m, wire_rate_met(cfg, sim, m)


def min_resource(base_cfg, setter, candidates):
    """Return the smallest candidate (and its metrics) that sustains wire rate,
    with all other resources left generous."""
    for v in candidates:
        cfg = base_cfg.copy()
        setter(cfg, v)
        sim, m, met = _run(cfg)
        if met:
            return v, m
    return None, None


def search_min_hw(cfg0):
    print(f"\n=== min-HW search (sustain wire rate, steady-state, mode={cfg0.mode}) ===")
    # measure peaks first (infinite resources) as the search ceiling
    peak_cfg = cfg0.copy()
    peak_cfg.walkers.num_walkers = None
    peak_cfg.buffers.iommu_req_buffer = None
    peak_cfg.buffers.io_bridge_buffer = None
    peak_cfg.memory.max_outstanding = None
    psim, pm = run_sim(peak_cfg, warmup_frac=WARMUP)
    print(f"  peaks @ infinite resources: peak_walks={pm.peak_walks} (3c), "
          f"peak_buffer={pm.peak_buffer} (3d), io_bridge_peak={pm.io_bridge_peak}, "
          f"mem_outstanding_peak={psim.memory.peak_outstanding}")
    hi = max(4, pm.peak_walks * 3, pm.peak_buffer * 2)

    def gen(cfg):
        cfg.buffers.iommu_req_buffer = None
        cfg.buffers.io_bridge_buffer = None
        cfg.memory.max_outstanding = None

    results = {}

    def set_walkers(cfg, v):
        gen(cfg); cfg.walkers.num_walkers = v
    results["num_walkers"], _ = min_resource(cfg0, set_walkers, range(1, hi + 1))

    def set_buffer(cfg, v):
        cfg.walkers.num_walkers = None; cfg.buffers.io_bridge_buffer = None
        cfg.memory.max_outstanding = None; cfg.buffers.iommu_req_buffer = v
    results["iommu_req_buffer"], _ = min_resource(cfg0, set_buffer, range(1, hi + 1))

    def set_iob(cfg, v):
        cfg.walkers.num_walkers = None; cfg.buffers.iommu_req_buffer = None
        cfg.memory.max_outstanding = None; cfg.buffers.io_bridge_buffer = v
    results["io_bridge_buffer"], _ = min_resource(cfg0, set_iob, range(1, hi + 1))

    def set_out(cfg, v):
        gen(cfg); cfg.memory.max_outstanding = v
    results["mem_max_outstanding"], _ = min_resource(cfg0, set_out, range(1, hi + 1))

    print("\n  minimum resource (each found with the others generous):")
    for k, v in results.items():
        print(f"    {k:<22}: {v if v is not None else 'NOT FOUND <= %d' % hi}")
    print("  (provision +50-100% over these per design_premises §12.)")
    return results


# --------------------------------------------------------------------------
# Pareto sweep (§11)
# --------------------------------------------------------------------------
def pareto_front(rows):
    """rows with 'area_ge' and 'energy_per_translation' -> subset on the front
    (both minimized); none dominates a front point."""
    front = []
    for r in rows:
        dominated = False
        for o in rows:
            if o is r:
                continue
            if (o["area_ge"] <= r["area_ge"] and o["energy_per_translation"] <= r["energy_per_translation"]
                    and (o["area_ge"] < r["area_ge"] or o["energy_per_translation"] < r["energy_per_translation"])):
                dominated = True
                break
        if not dominated:
            front.append(r)
    return sorted(front, key=lambda r: r["area_ge"])


def run_pareto(cfg_path, emit_candidates=False, plot=True, emit_breakdown=False):
    with open(cfg_path) as f:
        import yaml
        spec = yaml.safe_load(f)
    base = spec.get("base", {})
    grid = spec.get("grid", {})
    combos = expand_grid(base, grid)
    print(f"\n=== Pareto sweep: {len(combos)} configurations ===")

    rows = []
    for i, (labels, d) in enumerate(combos):
        d = dict(d)
        d["name"] = "cfg%03d" % i
        cfg = Config.from_dict(d)
        sim, m = run_sim(cfg, warmup_frac=WARMUP)
        s = summarize(cfg, sim, m)
        s["labels"] = labels
        s["_cfg"] = cfg
        rows.append(s)

    met = [r for r in rows if r["wire_rate_met"]]
    print(f"  {len(met)}/{len(rows)} configurations meet wire rate")
    front = pareto_front(met) if met else []
    front_names = {r["name"] for r in front}

    # ---- CSV table ----
    out_csv = os.path.join(os.path.dirname(os.path.abspath(cfg_path)), "..", "results.csv")
    out_csv = os.path.normpath(out_csv)
    # one column per swept grid axis (split out of the old packed "labels"), so the
    # table pastes straight into a spreadsheet for architecture comparison.
    label_keys = list(rows[0]["labels"].keys()) if rows else []
    id_fields = ["name", "mode", "wire_rate_met", "on_pareto"]
    metric_fields = ["area_ge", "energy_per_translation", "dram_energy_per_translation",
                     "fom_area_x_energy", "accesses_per_translation", "peak_walks", "peak_buffer",
                     "io_bridge_peak", "mem_outstanding_peak", "throughput_mps", "avg_lat_ns"]
    with open(out_csv, "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(id_fields + label_keys + metric_fields)
        for r in sorted(rows, key=lambda x: (not x["wire_rate_met"], x["area_ge"])):
            ids = [r["name"], r["mode"], r["wire_rate_met"], r["name"] in front_names]
            knobs = [r["labels"].get(k, "") for k in label_keys]
            metrics = [f"{r['area_ge']:.1f}", f"{r['energy_per_translation']:.3f}",
                       f"{r['dram_energy_per_translation']:.3f}",
                       f"{r['fom_area_x_energy']:.1f}", f"{r['accesses_per_translation']:.4f}",
                       r["peak_walks"], r["peak_buffer"], r["io_bridge_peak"],
                       r["mem_outstanding_peak"], f"{r['throughput_mps']:.2f}", f"{r['avg_lat_ns']:.1f}"]
            wtr.writerow(ids + knobs + metrics)
    print(f"  results table -> {out_csv}  ({len(label_keys)} swept-axis columns + metrics)")

    # ---- print Pareto front ----
    print("\n  -- area-energy Pareto front (wire-rate-meeting) --")
    print(f"  {'name':<8}{'area_GE':>10}{'E/xlate':>10}{'area*E':>14}{'N':>4}{'buf':>5}  labels")
    for r in front:
        print(f"  {r['name']:<8}{r['area_ge']:>10.1f}{r['energy_per_translation']:>10.3f}"
              f"{r['fom_area_x_energy']:>14.1f}{r['peak_walks']:>4}{r['peak_buffer']:>5}  "
              + "; ".join(f"{k}={v}" for k, v in r["labels"].items()))
    if len(front) == 1:
        print("  (single-point front: throughput is a hard gate, so among wire-rate-meeting\n"
              "   configs area & energy co-minimize -> PPA reduces to minimization, design_doc §11.\n"
              "   See results.csv / pareto.png for the full scatter across architectural regimes.)")

    if plot:
        _try_plot(met, front, os.path.dirname(out_csv))

    if emit_candidates and front:
        emit_candidate_svhs(front)

    if emit_breakdown and met:
        emit_pa_breakdown(met)

    return rows, front


def emit_pa_breakdown(met):
    """One PNG per wire-rate-meeting config: area-by-module + power-by-module pie
    charts. Written to iommu_sim/plot/ (can be many files)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"  (matplotlib unavailable: {e}; skipping per-config breakdown pies)")
        return
    outdir = os.path.join(os.path.dirname(__file__), "plot")
    os.makedirs(outdir, exist_ok=True)
    def _pie(ax, mods, value_of, title):
        items = [(m.name, value_of(m)) for m in mods if value_of(m) > 0]
        total = sum(v for _, v in items) or 1.0
        labels = [n for n, _ in items]
        vals = [v for _, v in items]
        wedges, _ = ax.pie(vals, startangle=90, counterclock=False)
        ax.legend(wedges, [f"{n}  {v/total:5.1%}" for n, v in items],
                  loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=8, frameon=False)
        ax.set_title(title, fontsize=10)

    n = 0
    for r in met:
        res = r["_result"]
        fig, (axa, axp) = plt.subplots(1, 2, figsize=(14, 6))
        _pie(axa, res.modules, lambda m: m.area_ge, f"area breakdown (total {res.area_ge:,.0f} GE)")
        _pie(axp, res.modules, lambda m: m.total_power,
             f"power breakdown (total {res.total_power:.3f} norm units/cyc)")
        nw = r["labels"].get("walkers.num_walkers", r["peak_walks"])
        fig.suptitle(f"{r['name']}  |  {r['mode']}  |  "
                     f"E/xlate {res.energy_per_translation:.1f} (IOMMU) + "
                     f"{r['dram_energy_per_translation']:.1f} (DRAM)  |  "
                     f"N={nw}  |  area {res.area_ge:,.0f} GE", fontsize=11)
        fig.tight_layout(rect=[0, 0, 1, 0.95])
        path = os.path.join(outdir, f"{r['name']}_pa.png")
        fig.savefig(path, dpi=110)
        plt.close(fig)
        n += 1
    print(f"\n  -- per-config area/power breakdown pies -> {os.path.relpath(outdir)}/  ({n} figures, wire-rate-meeting only)")


def _try_plot(met, front, outdir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"  (matplotlib unavailable: {e}; skipping plot, CSV still written)")
        return
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.scatter([r["area_ge"] for r in met], [r["energy_per_translation"] for r in met],
               c="lightgray", label="meets wire rate")
    fx = [r["area_ge"] for r in front]
    fy = [r["energy_per_translation"] for r in front]
    ax.plot(fx, fy, "-o", color="crimson", label="Pareto front")
    for r in front:
        ax.annotate(r["name"], (r["area_ge"], r["energy_per_translation"]), fontsize=7)
    ax.set_xlabel("area (GE)")
    ax.set_ylabel("energy / translation (norm units)")
    ax.set_title("IOMMU PPA Pareto (wire-rate-meeting configs)")
    ax.legend()
    fig.tight_layout()
    path = os.path.join(outdir, "pareto.png")
    fig.savefig(path, dpi=120)
    print(f"  Pareto plot -> {path}")


# --------------------------------------------------------------------------
# SystemVerilog candidate emission (§12)
# --------------------------------------------------------------------------
def _svh(cfg, label):
    c = cfg.caches

    def assoc_num(a):
        return 0 if a == "full" else int(a)   # 0 == fully associative (CAM)
    lines = [
        f"// Auto-generated IOMMU parameters from simulator config '{cfg.name}'",
        f"// selection: {label}",
        f"// mode={cfg.mode} superpage={cfg.superpage} lookup={c.lookup_mode} prefetch={cfg.prefetch.algo}",
        "`ifndef IOMMU_PARAMS_SVH",
        "`define IOMMU_PARAMS_SVH",
        "",
        f"localparam int CLOCK_MHZ            = {int(cfg.timing.clock_mhz)};",
        f"localparam int MEM_LATENCY_CYCLES   = {cfg.memory.latency_cycles};",
        f"localparam int COALESCE_FACTOR      = {c.coalesce_factor};",
        "",
        "// 0 == fully associative (CAM)",
        f"localparam int IOTLB_ENTRIES        = {c.iotlb.entries};",
        f"localparam int IOTLB_ASSOC          = {assoc_num(c.iotlb.assoc)};",
        f"localparam int VM_PWC_L2_ENTRIES    = {c.vm_pwc.l2.entries};",
        f"localparam int VM_PWC_L1_ENTRIES    = {c.vm_pwc.l1.entries};",
        f"localparam int VM_PWC_L0_ENTRIES    = {c.vm_pwc.l0.entries};",
        f"localparam int G_PWC_VML0_L0_ENTRIES = {c.g_pwc.vm_l0.l0.entries};",
        f"localparam int G_FINAL_L1_ENTRIES   = {c.g_final.l1.entries};",
        f"localparam bit G_FINAL_L0_ENABLED   = {1 if c.g_final.l0.enabled else 0};",
        f"localparam int G_FINAL_L0_ENTRIES   = {c.g_final.l0.entries};",
        f"localparam int DDTC_ENTRIES         = {c.ddtc.entries};",
        f"localparam bit PDTC_ENABLED         = {1 if c.pdtc.enabled else 0};",
        f"localparam int PDTC_ENTRIES         = {c.pdtc.entries};",
        f"localparam int MSI_ENTRIES          = {c.msi.entries};",
        "",
        f"localparam int NUM_WALKERS          = {cfg.walkers.num_walkers if cfg.walkers.num_walkers is not None else 8};",
        f"localparam int WALK_PIPELINE_DEPTH  = {cfg.walkers.pipeline_depth};",
        f"localparam int IOMMU_REQ_BUFFER     = {cfg.buffers.iommu_req_buffer if cfg.buffers.iommu_req_buffer is not None else 16};",
        f"localparam int IO_BRIDGE_BUFFER     = {cfg.buffers.io_bridge_buffer if cfg.buffers.io_bridge_buffer is not None else 16};",
        f"localparam int LOOKUP_CYCLES        = {cfg.timing.lookup_cycles};",
        f"localparam int ARBITRATION_CYCLES   = {cfg.timing.arbitration_cycles};",
        f"localparam int HIT_LATENCY_CYCLES   = {cfg.timing.hit_latency_cycles};",
        "",
        "`endif // IOMMU_PARAMS_SVH",
        "",
    ]
    return "\n".join(lines)


def emit_candidate_svhs(front):
    outdir = os.path.join(os.path.dirname(__file__), "candidates")
    os.makedirs(outdir, exist_ok=True)
    picks = {}
    picks["min_area"] = min(front, key=lambda r: r["area_ge"])
    picks["min_energy"] = min(front, key=lambda r: r["energy_per_translation"])
    picks["knee"] = min(front, key=lambda r: r["fom_area_x_energy"])
    written = set()
    print("\n  -- emitting SystemVerilog candidate params --")
    for tag, r in picks.items():
        fname = os.path.join(outdir, f"{tag}_{r['name']}.svh")
        if fname in written:
            continue
        with open(fname, "w") as f:
            f.write(_svh(r["_cfg"], f"{tag} ({r['name']}): area={r['area_ge']:.0f} GE, "
                                    f"E/xlate={r['energy_per_translation']:.2f}"))
        written.add(fname)
        print(f"    {tag:<10} -> {os.path.relpath(fname)}")


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="IOMMU exploration simulator -- sweep / search")
    ap.add_argument("--config", required=True)
    ap.add_argument("--search", choices=["min_hw"], default=None)
    ap.add_argument("--measure", choices=["peaks"], default=None)
    ap.add_argument("--pareto", action="store_true")
    ap.add_argument("--emit-candidates", action="store_true")
    ap.add_argument("--emit-breakdown", action="store_true",
                    help="per-config area/power pie charts (wire-rate-meeting only) -> plot/")
    ap.add_argument("--emit-trace", default=None)
    ap.add_argument("--no-plot", action="store_true")
    args = ap.parse_args()

    if args.pareto:
        run_pareto(args.config, emit_candidates=args.emit_candidates, plot=not args.no_plot,
                   emit_breakdown=args.emit_breakdown)
        return

    cfg = Config.load(args.config)

    if args.measure == "peaks":
        for fld in ("num_walkers",):
            cfg.walkers.num_walkers = None
        cfg.buffers.iommu_req_buffer = None
        cfg.buffers.io_bridge_buffer = None
        cfg.memory.max_outstanding = None
        sim, m = run_sim(cfg, warmup_frac=WARMUP)
        print(f"\n=== measure peaks (infinite resources, mode={cfg.mode}) ===")
        print(f"  3c  peak_walks (required N)        = {m.peak_walks}")
        print(f"  3d  peak_buffer (required buffer)  = {m.peak_buffer}")
        print(f"      io_bridge_peak                 = {m.io_bridge_peak}")
        print(f"      mem_outstanding_peak           = {sim.memory.peak_outstanding}")
        return

    if args.search == "min_hw":
        search_min_hw(cfg)
        return

    if args.emit_trace:
        requests, events = generate(cfg)
        os.makedirs(os.path.dirname(os.path.abspath(args.emit_trace)), exist_ok=True)
        export_csv(requests, events, args.emit_trace, cfg.cycle_ns)
        print(f"  trace CSV -> {args.emit_trace}  ({len(requests)} requests, {len(events)} events)")
        return

    ap.error("nothing to do: pass --pareto, --search min_hw, --measure peaks, or --emit-trace")


if __name__ == "__main__":
    main()
