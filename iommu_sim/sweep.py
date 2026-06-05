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


def run_pareto(cfg_path, emit_candidates=False, plot=True):
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
    fields = ["name", "mode", "wire_rate_met", "on_pareto", "area_ge", "energy_per_translation",
              "fom_area_x_energy", "accesses_per_translation", "peak_walks", "peak_buffer",
              "io_bridge_peak", "mem_outstanding_peak", "throughput_mps", "avg_lat_ns"]
    with open(out_csv, "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(fields + ["labels"])
        for r in sorted(rows, key=lambda x: (not x["wire_rate_met"], x["area_ge"])):
            wtr.writerow([r["name"], r["mode"], r["wire_rate_met"], r["name"] in front_names,
                          f"{r['area_ge']:.1f}", f"{r['energy_per_translation']:.3f}",
                          f"{r['fom_area_x_energy']:.1f}", f"{r['accesses_per_translation']:.4f}",
                          r["peak_walks"], r["peak_buffer"], r["io_bridge_peak"],
                          r["mem_outstanding_peak"], f"{r['throughput_mps']:.2f}",
                          f"{r['avg_lat_ns']:.1f}"]
                         + ["; ".join(f"{k}={v}" for k, v in r["labels"].items())])
    print(f"  results table -> {out_csv}")

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

    return rows, front


def _knobs(r):
    """Extract the visualised design knobs for one config row."""
    c = r["_cfg"]
    nw = c.walkers.num_walkers if c.walkers.num_walkers is not None else r["peak_walks"]
    return {
        "coalesce": c.caches.coalesce_factor,
        "iotlb": c.caches.iotlb.entries,
        "s1_pwc": c.caches.s1_pwc.l1.entries + c.caches.s1_pwc.l2.entries,
        "walkers": nw,
        "buffer": c.buffers.iommu_req_buffer if c.buffers.iommu_req_buffer is not None else r["peak_buffer"],
        "area": r["area_ge"],
        "energy": r["energy_per_translation"],
    }


def _try_plot(met, front, outdir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"  (matplotlib unavailable: {e}; skipping plot, CSV still written)")
        return
    if not met:
        print("  (no wire-rate-meeting config; skipping plot)")
        return
    front_names = {r["name"] for r in front}
    K = [(_knobs(r), r["name"] in front_names, r["name"]) for r in met]
    cache_sz = [k["iotlb"] + k["s1_pwc"] for k, _, _ in K]
    smin, smax = min(cache_sz), max(cache_sz)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

    # ---- panel 1: area vs energy; colour = #walkers, size = total cache entries ----
    xs = [k["area"] for k, _, _ in K]
    ys = [k["energy"] for k, _, _ in K]
    cs = [k["walkers"] for k, _, _ in K]
    sizes = [40 + 260 * (sz - smin) / (smax - smin + 1e-9) for sz in cache_sz]
    sc = ax1.scatter(xs, ys, c=cs, s=sizes, cmap="viridis", alpha=0.8, edgecolors="k", linewidths=0.3)
    fx = [r["area_ge"] for r in front]
    fy = [r["energy_per_translation"] for r in front]
    ax1.plot(fx, fy, "-o", color="crimson", label="Pareto front", zorder=5)
    for r in front:
        ax1.annotate(r["name"], (r["area_ge"], r["energy_per_translation"]), fontsize=8, color="crimson")
    cb = fig.colorbar(sc, ax=ax1)
    cb.set_label("num_walkers (colour)")
    ax1.set_xlabel("area (GE)")
    ax1.set_ylabel("energy / translation (norm units)")
    ax1.set_title("PPA Pareto — colour=walkers, size=cache entries (IOTLB+PWC)")
    ax1.legend(loc="upper right")
    ax1.text(0.02, 0.02, f"marker size: cache entries {smin}..{smax}\n(closer to origin = better)",
             transform=ax1.transAxes, fontsize=8, va="bottom")

    # ---- panel 2: parallel coordinates over all knobs ----
    axes = ["coalesce", "iotlb", "s1_pwc", "walkers", "buffer", "area", "energy"]
    cols = {a: [k[a] for k, _, _ in K] for a in axes}
    lo = {a: min(cols[a]) for a in axes}
    hi = {a: max(cols[a]) for a in axes}
    xpos = list(range(len(axes)))

    def norm(a, v):
        return (v - lo[a]) / (hi[a] - lo[a] + 1e-9)
    for k, on_front, name in K:
        ys2 = [norm(a, k[a]) for a in axes]
        ax2.plot(xpos, ys2, color=("crimson" if on_front else "lightsteelblue"),
                 lw=(2.2 if on_front else 0.7), alpha=(0.95 if on_front else 0.5),
                 zorder=(5 if on_front else 1))
    for x in xpos:
        ax2.axvline(x, color="gray", lw=0.6)
    ax2.set_xticks(xpos)
    ax2.set_xticklabels([f"{a}\n[{lo[a]:g}..{hi[a]:g}]" for a in axes], fontsize=8)
    ax2.set_yticks([])
    ax2.set_ylim(-0.05, 1.05)
    ax2.set_title("Design knobs per config (red = Pareto front)")
    ax2.plot([], [], color="crimson", lw=2.2, label="Pareto front")
    ax2.plot([], [], color="lightsteelblue", lw=1, label="meets wire rate")
    ax2.legend(loc="upper right", fontsize=8)

    fig.suptitle("IOMMU design-space exploration (wire-rate-meeting configs)", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    path = os.path.join(outdir, "pareto.png")
    fig.savefig(path, dpi=120)
    print(f"  Pareto plot -> {path}  (area-energy + per-knob parallel coordinates)")


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
        f"localparam int S1_PWC_L2_ENTRIES    = {c.s1_pwc.l2.entries};",
        f"localparam int S1_PWC_L1_ENTRIES    = {c.s1_pwc.l1.entries};",
        f"localparam int S2_PWC_ENTRIES       = {c.s2_pwc.entries};",
        f"localparam int TABLE_GPA_ENTRIES    = {c.table_gpa.entries};",
        f"localparam bit DATA_GPA_ENABLED     = {1 if c.data_gpa.enabled else 0};",
        f"localparam int DATA_GPA_ENTRIES     = {c.data_gpa.entries};",
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
    ap.add_argument("--emit-trace", default=None)
    ap.add_argument("--no-plot", action="store_true")
    args = ap.parse_args()

    if args.pareto:
        run_pareto(args.config, emit_candidates=args.emit_candidates, plot=not args.no_plot)
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
