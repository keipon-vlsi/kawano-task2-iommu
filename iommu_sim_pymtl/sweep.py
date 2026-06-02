"""Parameter sweep: locate the design points at which wire rate breaks.

Varies num_walkers, buffer_size, coalesce_factor, prefetch distance, and the
IOVA pattern. Writes one row per (param, value, pattern) to sweep.csv and
prints a short text summary describing the wire-rate cliff for each axis.
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from iommu_sim_pymtl import (
    SimConfig, IOTLBCfg, PWCCfg, PrefetchCfg, TraceCfg, run_simulation,
)


def base_cfg(label: str) -> SimConfig:
    """Baseline = PWC+coalescing, sequential, no prefetch."""
    return SimConfig(
        label=label,
        iotlb=IOTLBCfg(sets=1, assoc=256, policy="lru"),
        pwc=PWCCfg(sets=1, assoc=16, policy="lru"),
        coalesce_factor=8,
        prefetcher=PrefetchCfg(kind="none"),
        trace=TraceCfg(kind="sequential", n=4000),
    )


def measure(cfg: SimConfig) -> dict:
    m, _eng = run_simulation(cfg)
    n = max(1, m.completed)
    span_cycles = max(1, m.last_complete_cycle - (m.first_arrival_cycle or 0))
    span_ns = span_cycles * cfg.ns_per_cycle()
    tput_mps = n / span_ns * 1e9 / 1e6 if span_ns else 0.0
    target_mps = cfg.target_throughput_per_s() / 1e6
    return {
        "label": cfg.label,
        "throughput_Mps": round(tput_mps, 3),
        "target_Mps": round(target_mps, 3),
        "sustained": int(tput_mps >= 0.995 * target_mps),
        "mem_per_page": round(m.mem_accesses / n, 4),
        "peak_walks": m.peak_walks,
        "peak_buffer": m.peak_buffer,
        "avg_lat_ns": round(m.avg_lat_cycles * cfg.ns_per_cycle(), 2),
        "p99_lat_ns": round(m.p99_lat_cycles * cfg.ns_per_cycle(), 2),
    }


def sweep_walkers():
    """Vary walker pool size under the no-cache stress workload."""
    rows = []
    for nw in (1, 2, 3, 4, 5, 6, 7, 8, 12, 16, None):
        cfg = base_cfg(f"walkers={nw}")
        cfg.iotlb.assoc = 0
        cfg.pwc.assoc = 0
        cfg.coalesce_factor = 1
        cfg.num_walkers = nw
        cfg.buffer_size = 128
        r = measure(cfg)
        r["axis"] = "num_walkers"
        r["value"] = "inf" if nw is None else nw
        rows.append(r)
    return rows


def sweep_buffer():
    """Vary transaction buffer with the same no-cache stress workload."""
    rows = []
    for bs in (1, 2, 4, 6, 8, 12, 16, 32, None):
        cfg = base_cfg(f"buf={bs}")
        cfg.iotlb.assoc = 0
        cfg.pwc.assoc = 0
        cfg.coalesce_factor = 1
        cfg.num_walkers = 8
        cfg.buffer_size = bs
        r = measure(cfg)
        r["axis"] = "buffer_size"
        r["value"] = "inf" if bs is None else bs
        rows.append(r)
    return rows


def sweep_coalesce():
    """Vary leaf-coalescing factor (PTEs per cache line)."""
    rows = []
    for c in (1, 2, 4, 8, 16):
        cfg = base_cfg(f"coalesce={c}")
        cfg.coalesce_factor = c
        cfg.prefetcher = PrefetchCfg(kind="none", coalesce=c)
        r = measure(cfg)
        r["axis"] = "coalesce_factor"
        r["value"] = c
        rows.append(r)
    return rows


def sweep_prefetch():
    """Vary prefetch distance under sequential IOVA."""
    rows = []
    for d in (0, 4, 8, 16, 32, 64):
        cfg = base_cfg(f"pref_dist={d}")
        cfg.prefetcher = (PrefetchCfg(kind="none")
                          if d == 0 else
                          PrefetchCfg(kind="nextline", distance=d, coalesce=8))
        r = measure(cfg)
        r["axis"] = "prefetch_distance"
        r["value"] = d
        rows.append(r)
    return rows


def sweep_pattern():
    """Compare IOVA access patterns at the same PWC config."""
    rows = []
    for kind in ("sequential", "multi_stream", "random"):
        cfg = base_cfg(f"pat={kind}")
        cfg.trace = TraceCfg(kind=kind, n=4000, span_pages=1_000_000,
                             streams=4, stride_pages=1)
        r = measure(cfg)
        r["axis"] = "iova_pattern"
        r["value"] = kind
        rows.append(r)
    return rows


def summarise(rows, axis):
    cliff = None
    for r in rows:
        if not r["sustained"]:
            cliff = r["value"]
    sustained_vals = [r["value"] for r in rows if r["sustained"]]
    return (f"axis={axis:<18} sustained_at={sustained_vals}  "
            f"last_failed_at={cliff!r}")


def main():
    out_path = os.path.join(os.path.dirname(__file__), "sweep.csv")
    all_rows = []
    summaries = []
    for name, fn in [("num_walkers", sweep_walkers),
                     ("buffer_size", sweep_buffer),
                     ("coalesce_factor", sweep_coalesce),
                     ("prefetch_distance", sweep_prefetch),
                     ("iova_pattern", sweep_pattern)]:
        rows = fn()
        print(f"\n--- sweep: {name} ---")
        for r in rows:
            print(f"  {r['label']:<22} tput={r['throughput_Mps']:>7.3f} M/s  "
                  f"sustained={'YES' if r['sustained'] else 'no ':<3}  "
                  f"peak_walks={r['peak_walks']:>3}  "
                  f"peak_buf={r['peak_buffer']:>3}  "
                  f"avg_lat={r['avg_lat_ns']:>9.1f}ns  "
                  f"mem/pg={r['mem_per_page']:>6.3f}")
        all_rows.extend(rows)
        summaries.append(summarise(rows, name))

    with open(out_path, "w", newline="") as f:
        fieldnames = ["axis", "value", "label", "throughput_Mps", "target_Mps",
                      "sustained", "mem_per_page", "peak_walks", "peak_buffer",
                      "avg_lat_ns", "p99_lat_ns"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})

    print("\n==== Wire-rate cliff summary ====")
    for s in summaries:
        print("  " + s)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
