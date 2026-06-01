"""Assembly example. Change the arguments here, or swap in different classes,
to reconfigure the simulation."""
from caches import SetAssocCache, LRU
from prefetch import NoPrefetch, NextLinePrefetch
from memory import MemoryModel
from walker import SingleStageCost
from workload import sequential, random_trace, wire_inter_arrival_ns
from engine import Simulator

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

N = 8000

# A: no cache (IOTLB/PWC disabled, no coalescing) -> reproduces required N ~= 8
build_and_run("A: no cache (full 3-level, unlimited resources)",
              trace=sequential(N), iotlb_assoc=0, pwc_assoc=0, coalesce=1,
              prefetcher=NoPrefetch())

# B: PWC + 64B coalescing -> residual drops sharply
build_and_run("B: PWC + coalescing",
              trace=sequential(N), iotlb_assoc=256, pwc_assoc=16, coalesce=8,
              prefetcher=NoPrefetch())

# C: B + prefetch
build_and_run("C: B + prefetch",
              trace=sequential(N), iotlb_assoc=256, pwc_assoc=16, coalesce=8,
              prefetcher=NextLinePrefetch(distance=16, coalesce=8))

# D: random IOVA (sensitivity) -> best-case optimization collapses
build_and_run("D: random IOVA (same PWC+coalesce config)",
              trace=random_trace(N), iotlb_assoc=256, pwc_assoc=16, coalesce=8,
              prefetcher=NoPrefetch())

# E: finite resources to observe stall (walker=4, buffer=4)
build_and_run("E: no-cache + finite (walker=4, buffer=4)",
              trace=sequential(N), iotlb_assoc=0, pwc_assoc=0, coalesce=1,
              prefetcher=NoPrefetch(), num_walkers=4, buffer_size=4)