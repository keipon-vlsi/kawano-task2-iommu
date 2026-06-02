"""Assembly example. Change the arguments here, or swap in different classes,
to reconfigure the simulation.

In addition to the performance metrics, each scenario now also prints a
first-order area & power estimate (see estimator.py / ESTIMATOR_ja.md) and
writes a frozen JSON prediction record under ./freeze/."""
import os
import re

from caches import SetAssocCache, LRU
from prefetch import NoPrefetch, NextLinePrefetch
from memory import MemoryModel
from walker import SingleStageCost
from workload import sequential, random_trace, wire_inter_arrival_ns
from engine import Simulator
from estimator import EstimatorConfig, estimate

FREEZE_DIR = os.path.join(os.path.dirname(__file__), "freeze")


def build_and_run(name, *, trace, iotlb_assoc, pwc_assoc, coalesce,
                  prefetcher, num_walkers=None, buffer_size=None):
    sim = Simulator(
        workload=trace,
        iotlb=SetAssocCache(num_sets=1, assoc=iotlb_assoc, policy=LRU()),
        pwc=SetAssocCache(num_sets=1, assoc=pwc_assoc, policy=LRU()),
        prefetcher=prefetcher,
        memory=MemoryModel(latency_ns=100.0),
        cost_model=SingleStageCost(coalesce=coalesce),
        num_walkers=num_walkers, buffer_size=buffer_size,
    )
    m = sim.run()
    n = m.completed
    span = (m.last_complete - m.first_arrival) or 1
    print(f"\n=== {name} ===")
    print(f"  completed         : {n}")
    print(f"  total mem accesses: {sim.memory.accesses}  ({sim.memory.accesses/n:.3f} /page)")
    print(f"  IOTLB hit         : {m.iotlb_hit}  / coalesced(MSHR): {m.mshr_coalesced}  / true miss(walk): {m.walks_started}")
    print(f"  required N (peak walks): {m.peak_walks}")
    print(f"  required buffer (peak) : {m.peak_buffer}")
    print(f"  avg latency       : {m.avg_lat:.1f} ns  (p99 {m.p99_lat:.1f} ns)")
    print(f"  achieved throughput: {n/span*1e9/1e6:.2f} M/s  (target {1e9/wire_inter_arrival_ns()/1e6:.2f} M/s)")

    # ---- first-order area & power estimate (non-invasive: reads config + metrics) ----
    # Resolve provisioned sizes: explicit limits if given, else the measured peaks.
    walkers = num_walkers if num_walkers is not None else m.peak_walks
    depth = buffer_size if buffer_size is not None else m.peak_buffer
    # IOTLB/PWC entries: assoc is entries-per-(single-)set here. 0 (disabled) -> 0 entries.
    cfg = EstimatorConfig(
        name=name,
        iotlb_entries=iotlb_assoc or 0, iotlb_fully_assoc=True,
        pwc_entries=pwc_assoc or 0,     pwc_fully_assoc=True,
        buffer_depth=depth, num_walkers=walkers,
    )
    est = estimate(cfg, m, components={"iotlb": sim.iotlb, "pwc": sim.pwc, "memory": sim.memory})
    print(est.table())

    os.makedirs(FREEZE_DIR, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()[:40]
    rec = est.freeze(os.path.join(FREEZE_DIR, f"{slug}.json"))
    print(f"  frozen prediction -> freeze/{slug}.json  (config_hash {rec['config_hash'][:12]}...)")

    return {"name": name, "completed": n, "mem_per_page": sim.memory.accesses / n,
            "peak_walks": m.peak_walks, "avg_lat": m.avg_lat,
            "area_mm2": est.area_mm2, "total_mW": est.total_mW,
            "epp_pj": est.energy_per_translation_pj}


N = 8000
rows = []

# A: no cache (IOTLB/PWC disabled, no coalescing) -> reproduces required N ~= 8
rows.append(build_and_run("A: no cache (full 3-level, unlimited resources)",
              trace=sequential(N), iotlb_assoc=0, pwc_assoc=0, coalesce=1,
              prefetcher=NoPrefetch()))

# B: PWC + 64B coalescing -> residual drops sharply
rows.append(build_and_run("B: PWC + coalescing",
              trace=sequential(N), iotlb_assoc=256, pwc_assoc=16, coalesce=8,
              prefetcher=NoPrefetch()))

# C: B + prefetch
rows.append(build_and_run("C: B + prefetch",
              trace=sequential(N), iotlb_assoc=256, pwc_assoc=16, coalesce=8,
              prefetcher=NextLinePrefetch(distance=16, coalesce=8)))

# D: random IOVA (sensitivity) -> best-case optimization collapses
rows.append(build_and_run("D: random IOVA (same PWC+coalesce config)",
              trace=random_trace(N), iotlb_assoc=256, pwc_assoc=16, coalesce=8,
              prefetcher=NoPrefetch()))

# E: finite resources to observe stall (walker=4, buffer=4)
rows.append(build_and_run("E: no-cache + finite (walker=4, buffer=4)",
              trace=sequential(N), iotlb_assoc=0, pwc_assoc=0, coalesce=1,
              prefetcher=NoPrefetch(), num_walkers=4, buffer_size=4))

# ---- combined perf + area/power sweep summary ----
print("\n\n=== sweep summary (perf + area/power) ===")
hdr = f"{'scenario':<10}{'mem/pg':>9}{'peakN':>7}{'avg_ns':>9}{'area_mm2':>11}{'power_mW':>11}{'pJ/xlate':>11}"
print(hdr); print("-" * len(hdr))
for r in rows:
    tag = r["name"].split(":")[0]
    print(f"{tag:<10}{r['mem_per_page']:>9.3f}{r['peak_walks']:>7d}{r['avg_lat']:>9.1f}"
          f"{r['area_mm2']:>11.6f}{r['total_mW']:>11.4f}{r['epp_pj']:>11.3f}")
