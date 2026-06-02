"""Generate report artifacts: results CSV + figures from the iommu_sim simulator.

Run: cd report && python3 gen_results.py
Outputs (under report/):
  - results.csv            : scenarios A-E summary (3a/3b/3c/3d evidence)
  - walker_sweep.csv       : achieved throughput vs walker count (wire-rate cliff)
  - buffer_sweep.csv       : achieved throughput vs buffer depth (3d)
  - figures/*.png          : referenced by minimal_report.md

Engine/policy separation is preserved: this script only *configures* the
existing Simulator and reads metrics; it does not modify the engine.
"""
import os
import sys
import csv

# Make iommu_sim importable regardless of CWD.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "iommu_sim"))

from caches import SetAssocCache, LRU            # noqa: E402
from prefetch import NoPrefetch, NextLinePrefetch  # noqa: E402
from memory import MemoryModel                   # noqa: E402
from walker import SingleStageCost, ContextCachedCost  # noqa: E402
from workload import sequential, random_trace, wire_inter_arrival_ns  # noqa: E402
from engine import Simulator                     # noqa: E402

import matplotlib                                # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                  # noqa: E402

N = 8000
WIRE_IA = wire_inter_arrival_ns()                # 40.96 ns
WIRE_MPS = 1e9 / WIRE_IA / 1e6                   # 24.41 M/s
FIGDIR = os.path.join(HERE, "figures")
os.makedirs(FIGDIR, exist_ok=True)


def run(*, trace, iotlb_assoc, pwc_assoc, coalesce, prefetcher,
        num_walkers=None, buffer_size=None, cost_model=None):
    sim = Simulator(
        workload=trace,
        iotlb=SetAssocCache(num_sets=1, assoc=iotlb_assoc, policy=LRU()),
        pwc=SetAssocCache(num_sets=1, assoc=pwc_assoc, policy=LRU()),
        prefetcher=prefetcher,
        memory=MemoryModel(latency_ns=100.0),
        cost_model=cost_model or SingleStageCost(coalesce=coalesce),
        num_walkers=num_walkers, buffer_size=buffer_size,
    )
    m = sim.run()
    span = (m.last_complete - m.first_arrival) or 1
    thr = m.completed / span * 1e9 / 1e6   # M/s
    return {
        "completed": m.completed,
        "mem_per_page": sim.memory.accesses / m.completed,
        "iotlb_hit": m.iotlb_hit,
        "mshr_coalesced": m.mshr_coalesced,
        "walks": m.walks_started,
        "peak_walks": m.peak_walks,
        "peak_buffer": m.peak_buffer,
        "avg_lat": m.avg_lat,
        "p99_lat": m.p99_lat,
        "throughput": thr,
        "wire_met": thr >= 0.99 * WIRE_MPS,
    }


# ---- Scenarios A-E (reproduce CLAUDE.md validation trends) ----
SCENARIOS = [
    ("A", "no-cache (3-level, infinite res.)",
     dict(trace=sequential(N), iotlb_assoc=0, pwc_assoc=0, coalesce=1,
          prefetcher=NoPrefetch())),
    ("B", "PWC + 64B coalescing",
     dict(trace=sequential(N), iotlb_assoc=256, pwc_assoc=16, coalesce=8,
          prefetcher=NoPrefetch())),
    ("C", "B + next-line prefetch",
     dict(trace=sequential(N), iotlb_assoc=256, pwc_assoc=16, coalesce=8,
          prefetcher=NextLinePrefetch(distance=16, coalesce=8))),
    ("D", "random IOVA (B config)",
     dict(trace=random_trace(N), iotlb_assoc=256, pwc_assoc=16, coalesce=8,
          prefetcher=NoPrefetch())),
    ("E", "no-cache, walkers=4 buffer=4",
     dict(trace=sequential(N), iotlb_assoc=0, pwc_assoc=0, coalesce=1,
          prefetcher=NoPrefetch(), num_walkers=4, buffer_size=4)),
]


def main():
    rows = []
    print(f"wire target: {WIRE_MPS:.2f} M/s  inter-arrival {WIRE_IA:.2f} ns\n")
    for tag, desc, cfg in SCENARIOS:
        r = run(**cfg)
        r.update(scenario=tag, desc=desc)
        rows.append(r)
        print(f"{tag}: {desc}")
        print(f"    mem/page={r['mem_per_page']:.3f}  peakN={r['peak_walks']}  "
              f"peakBuf={r['peak_buffer']}  avg={r['avg_lat']:.1f}ns  "
              f"thr={r['throughput']:.2f}M/s  wire_met={r['wire_met']}")

    cols = ["scenario", "desc", "mem_per_page", "peak_walks", "peak_buffer",
            "avg_lat", "p99_lat", "throughput", "wire_met",
            "iotlb_hit", "mshr_coalesced", "walks", "completed"]
    with open(os.path.join(HERE, "results.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in cols})

    # ---- Walker-count sweep: 3c wire-rate cliff (no-cache, infinite buffer) ----
    walker_rows = []
    for nw in range(1, 13):
        r = run(trace=sequential(N), iotlb_assoc=0, pwc_assoc=0, coalesce=1,
                prefetcher=NoPrefetch(), num_walkers=nw, buffer_size=None)
        walker_rows.append((nw, r["throughput"], r["wire_met"]))
    with open(os.path.join(HERE, "walker_sweep.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["num_walkers", "throughput_Mps", "wire_met"])
        w.writerows(walker_rows)

    # ---- Buffer-depth sweep: 3d (no-cache, sufficient walkers) ----
    buf_rows = []
    for bs in range(1, 13):
        r = run(trace=sequential(N), iotlb_assoc=0, pwc_assoc=0, coalesce=1,
                prefetcher=NoPrefetch(), num_walkers=64, buffer_size=bs)
        buf_rows.append((bs, r["throughput"], r["wire_met"]))
    with open(os.path.join(HERE, "buffer_sweep.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["buffer_size", "throughput_Mps", "wire_met"])
        w.writerows(buf_rows)

    # ---- DDT$/PDT$/MSI$ amortization demo (3b context caches) ----
    # Single continuous stream -> one cold context walk, ~0 steady-state cost.
    ctx = run(trace=sequential(N), iotlb_assoc=256, pwc_assoc=16, coalesce=8,
              prefetcher=NoPrefetch(),
              cost_model=ContextCachedCost(coalesce=8, ddt_levels=3,
                                           pdt_levels=2))
    base = {r["scenario"]: r for r in rows}["B"]
    extra_total = (ctx["mem_per_page"] - base["mem_per_page"]) * N
    print(f"\nContext-cache (DDT$/PDT$) demo: +{extra_total:.0f} cold accesses "
          f"over {N} pages = +{extra_total / N * 100.0:.3f} ns/page amortized "
          f"(steady-state added latency ~ 0)")
    with open(os.path.join(HERE, "context_cache.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["config", "mem_per_page", "cold_context_accesses",
                    "amortized_ns_per_page"])
        w.writerow(["B (no context cost)", f"{base['mem_per_page']:.4f}", 0, 0.0])
        w.writerow(["B + DDT$/PDT$ (ctx cold once)",
                    f"{ctx['mem_per_page']:.4f}", round(extra_total),
                    round(extra_total / N * 100.0, 4)])

    make_figures(rows, walker_rows, buf_rows)
    print("\nWrote results.csv, walker_sweep.csv, buffer_sweep.csv, "
          "context_cache.csv, figures/*.png")


def make_figures(rows, walker_rows, buf_rows):
    by = {r["scenario"]: r for r in rows}
    abc = ["A", "B", "C"]
    labels = {"A": "A\nno-cache", "B": "B\nPWC+coalesce", "C": "C\n+prefetch",
              "D": "D\nrandom IOVA"}

    # Fig 1: avg latency + required N vs cache configuration (A->B->C)
    fig, ax1 = plt.subplots(figsize=(7, 4.2))
    x = range(len(abc))
    lat = [by[s]["avg_lat"] for s in abc]
    nN = [by[s]["peak_walks"] for s in abc]
    bars = ax1.bar(x, lat, color="#4878CF", width=0.55, label="avg latency (ns)")
    ax1.set_ylabel("avg translation latency (ns)", color="#4878CF")
    ax1.tick_params(axis="y", labelcolor="#4878CF")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels([labels[s] for s in abc])
    for b, v in zip(bars, lat):
        ax1.text(b.get_x() + b.get_width() / 2, v + 6, f"{v:.1f}",
                 ha="center", fontsize=9)
    ax2 = ax1.twinx()
    ax2.plot(x, nN, "o-", color="#E24A33", lw=2, label="required N (walks)")
    ax2.set_ylabel("required parallel walkers N", color="#E24A33")
    ax2.tick_params(axis="y", labelcolor="#E24A33")
    ax2.set_ylim(0, 10)
    for xi, v in zip(x, nN):
        ax2.text(xi + 0.05, v + 0.2, f"{v}", color="#E24A33", fontsize=9)
    ax1.set_title("Translation latency & required walkers vs cache config")
    fig.tight_layout()
    fig.savefig(os.path.join(FIGDIR, "fig1_latency_vs_config.png"), dpi=130)
    plt.close(fig)

    # Fig 2: throughput vs walker count (wire-rate cliff)
    fig, ax = plt.subplots(figsize=(7, 4.2))
    xs = [w[0] for w in walker_rows]
    ys = [w[1] for w in walker_rows]
    ax.plot(xs, ys, "o-", color="#4878CF", lw=2, label="achieved throughput")
    ax.axhline(WIRE_MPS, ls="--", color="#E24A33",
               label=f"wire rate {WIRE_MPS:.1f} M/s")
    ax.axvline(8, ls=":", color="gray", label="N=8 (Little's law)")
    ax.set_xlabel("number of parallel walkers N (no-cache, infinite buffer)")
    ax.set_ylabel("achieved throughput (M translations/s)")
    ax.set_title("Wire-rate cliff: throughput vs walker count")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(FIGDIR, "fig2_throughput_vs_walkers.png"), dpi=130)
    plt.close(fig)

    # Fig 3: mem accesses per page vs configuration (A/B/C/D)
    fig, ax = plt.subplots(figsize=(7, 4.2))
    order = ["A", "B", "C", "D"]
    mpp = [by[s]["mem_per_page"] for s in order]
    colors = ["#E24A33", "#4878CF", "#6ACC65", "#988ED5"]
    bars = ax.bar(range(len(order)), mpp, color=colors, width=0.6)
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels([labels[s] for s in order])
    ax.set_ylabel("memory accesses per page")
    ax.set_title("Memory traffic per page vs configuration")
    for b, v in zip(bars, mpp):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.05, f"{v:.2f}",
                ha="center", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(FIGDIR, "fig3_mem_per_page.png"), dpi=130)
    plt.close(fig)

    # Fig 4: throughput vs buffer depth (3d cliff)
    fig, ax = plt.subplots(figsize=(7, 4.2))
    xs = [b[0] for b in buf_rows]
    ys = [b[1] for b in buf_rows]
    ax.plot(xs, ys, "s-", color="#6ACC65", lw=2, label="achieved throughput")
    ax.axhline(WIRE_MPS, ls="--", color="#E24A33",
               label=f"wire rate {WIRE_MPS:.1f} M/s")
    ax.axvline(8, ls=":", color="gray", label="buffer=8 (Little's law)")
    ax.set_xlabel("transaction buffer depth (no-cache, sufficient walkers)")
    ax.set_ylabel("achieved throughput (M translations/s)")
    ax.set_title("Minimum buffer: throughput vs buffer depth")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(FIGDIR, "fig4_throughput_vs_buffer.png"), dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    main()
