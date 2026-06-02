"""Reproduce scenarios A–E from the prompt using the PyMTL3 simulator.

Runs each scenario, prints a per-scenario report (mirrors the reference
event-driven sim's output format), then writes a row to results.csv so the
trends can be cross-checked at a glance:

    A. No-cache baseline                    : measures required N (~8).
    B. PWC + 64 B leaf coalescing           : memory traffic collapses ~24x.
    C. B + prefetch                         : latency collapses to ~hit-lat.
    D. Random IOVA (same B config)          : caching collapses, N back to ~8.
    E. No-cache + finite walkers=4, buf=4   : wire rate fails (cliff).
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from iommu_sim_pymtl import (
    SimConfig, IOTLBCfg, PWCCfg, PrefetchCfg, TraceCfg, run_simulation,
)
from iommu_sim_pymtl.harness import fmt_report


N = 8000


def scenario_A():
    return SimConfig(
        label="A_no_cache",
        iotlb=IOTLBCfg(sets=1, assoc=0, policy="lru"),
        pwc=PWCCfg(sets=1, assoc=0, policy="lru"),
        coalesce_factor=1,
        prefetcher=PrefetchCfg(kind="none"),
        trace=TraceCfg(kind="sequential", n=N),
    )


def scenario_B():
    return SimConfig(
        label="B_pwc_coalesce",
        iotlb=IOTLBCfg(sets=1, assoc=256, policy="lru"),
        pwc=PWCCfg(sets=1, assoc=16, policy="lru"),
        coalesce_factor=8,
        prefetcher=PrefetchCfg(kind="none"),
        trace=TraceCfg(kind="sequential", n=N),
    )


def scenario_C():
    return SimConfig(
        label="C_pwc_coalesce_prefetch",
        iotlb=IOTLBCfg(sets=1, assoc=256, policy="lru"),
        pwc=PWCCfg(sets=1, assoc=16, policy="lru"),
        coalesce_factor=8,
        prefetcher=PrefetchCfg(kind="nextline", distance=16, coalesce=8),
        trace=TraceCfg(kind="sequential", n=N),
    )


def scenario_D():
    return SimConfig(
        label="D_random_iova",
        iotlb=IOTLBCfg(sets=1, assoc=256, policy="lru"),
        pwc=PWCCfg(sets=1, assoc=16, policy="lru"),
        coalesce_factor=8,
        prefetcher=PrefetchCfg(kind="none"),
        trace=TraceCfg(kind="random", n=N, span_pages=1_000_000, seed=0),
    )


def scenario_E():
    return SimConfig(
        label="E_no_cache_finite",
        iotlb=IOTLBCfg(sets=1, assoc=0, policy="lru"),
        pwc=PWCCfg(sets=1, assoc=0, policy="lru"),
        coalesce_factor=1,
        prefetcher=PrefetchCfg(kind="none"),
        trace=TraceCfg(kind="sequential", n=N),
        num_walkers=4,
        buffer_size=4,
    )


def main():
    csv_path = os.path.join(os.path.dirname(__file__), "results.csv")
    rows = []
    for name, mk in [
        ("A: no cache (full 3-level, unlimited resources)", scenario_A),
        ("B: PWC + coalescing", scenario_B),
        ("C: B + prefetch", scenario_C),
        ("D: random IOVA (same PWC+coalesce config)", scenario_D),
        ("E: no-cache + finite (walker=4, buffer=4)", scenario_E),
    ]:
        cfg = mk()
        m, _eng = run_simulation(cfg)
        print(fmt_report(name, cfg, m))
        n = max(1, m.completed)
        span_cycles = max(1, m.last_complete_cycle
                          - (m.first_arrival_cycle or 0))
        span_ns = span_cycles * cfg.ns_per_cycle()
        rows.append({
            "scenario": cfg.label,
            "completed": m.completed,
            "mem_accesses": m.mem_accesses,
            "mem_per_page": round(m.mem_accesses / n, 4),
            "iotlb_hit": m.iotlb_hit,
            "mshr_coalesced": m.mshr_coalesced,
            "walks_started": m.walks_started,
            "peak_walks": m.peak_walks,
            "peak_buffer": m.peak_buffer,
            "avg_lat_ns": round(m.avg_lat_cycles * cfg.ns_per_cycle(), 2),
            "p99_lat_ns": round(m.p99_lat_cycles * cfg.ns_per_cycle(), 2),
            "throughput_Mps": round(n / span_ns * 1e9 / 1e6, 3) if span_ns else 0,
            "target_Mps": round(cfg.target_throughput_per_s() / 1e6, 3),
            "sim_cycles": m.sim_cycles,
        })

    fieldnames = list(rows[0].keys())
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {csv_path}")

    # Quick comparison table.
    print("\n==== A-E comparison ====")
    hdr = ("scenario", "mem/pg", "peak_N", "peak_buf",
           "avg_ns", "p99_ns", "Mps", "tgt")
    print("  ".join(f"{h:>20}" for h in hdr))
    for r in rows:
        print("  ".join(f"{v:>20}" for v in (
            r["scenario"], r["mem_per_page"], r["peak_walks"],
            r["peak_buffer"], r["avg_lat_ns"], r["p99_lat_ns"],
            r["throughput_Mps"], r["target_Mps"])))


if __name__ == "__main__":
    main()
