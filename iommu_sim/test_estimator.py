"""Tests for the area & power estimator plus a guard that the additive activity
counters did NOT change the existing A-E performance trends.

Run:  cd iommu_sim && python3 -m pytest test_estimator.py -q
  or:  cd iommu_sim && python3 test_estimator.py
"""
import json
import os
import tempfile

from caches import SetAssocCache, LRU
from prefetch import NoPrefetch, NextLinePrefetch
from memory import MemoryModel
from walker import SingleStageCost
from workload import sequential, random_trace
from engine import Simulator
from estimator import (TechParams, StructParams, CalibParams, EstimatorConfig,
                       AreaModel, estimate)

N = 8000


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def run_scenario(*, trace, iotlb_assoc, pwc_assoc, coalesce, prefetcher,
                 num_walkers=None, buffer_size=None):
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
    return sim, m


def estimate_scenario(sim, m, *, iotlb_entries, pwc_entries, num_walkers=None,
                      buffer_size=None, **kw):
    walkers = num_walkers if num_walkers is not None else m.peak_walks
    depth = buffer_size if buffer_size is not None else m.peak_buffer
    cfg = EstimatorConfig(iotlb_entries=iotlb_entries, pwc_entries=pwc_entries,
                          num_walkers=walkers, buffer_depth=depth, **kw)
    return estimate(cfg, m, components={"iotlb": sim.iotlb, "pwc": sim.pwc,
                                        "memory": sim.memory})


# --------------------------------------------------------------------------
# 1. per-component areas sum to the total
# --------------------------------------------------------------------------
def test_component_areas_sum_to_total():
    sim, m = run_scenario(trace=sequential(N), iotlb_assoc=256, pwc_assoc=16,
                          coalesce=8, prefetcher=NoPrefetch())
    est = estimate_scenario(sim, m, iotlb_entries=256, pwc_entries=16)
    assert abs(sum(c.area_um2 for c in est.components) - est.area_um2) < 1e-6
    assert abs(est.area_mm2 - est.area_um2 / 1e6) < 1e-12
    # power totals also consistent
    assert abs(sum(c.dyn_mW for c in est.components) - est.dyn_mW) < 1e-9
    assert abs(sum(c.stat_mW for c in est.components) - est.stat_mW) < 1e-9


# --------------------------------------------------------------------------
# 2. larger / more-associative cache => larger area (monotonic sanity)
# --------------------------------------------------------------------------
def test_cache_area_monotonic_in_entries():
    am = AreaModel(TechParams(), StructParams())
    s = StructParams()
    small, *_ = am.cache(64, s.iotlb_tag_bits, s.iotlb_data_bits, fully_assoc=True)
    big, *_ = am.cache(256, s.iotlb_tag_bits, s.iotlb_data_bits, fully_assoc=True)
    assert big > small
    # fully-associative (CAM) tag store is more expensive than set/direct (SRAM)
    cam, *_ = am.cache(256, s.iotlb_tag_bits, s.iotlb_data_bits, fully_assoc=True)
    sram, *_ = am.cache(256, s.iotlb_tag_bits, s.iotlb_data_bits, fully_assoc=False)
    assert cam > sram


def test_total_area_grows_with_iotlb():
    sim, m = run_scenario(trace=sequential(N), iotlb_assoc=256, pwc_assoc=16,
                          coalesce=8, prefetcher=NoPrefetch())
    e_small = estimate_scenario(sim, m, iotlb_entries=64, pwc_entries=16)
    e_big = estimate_scenario(sim, m, iotlb_entries=512, pwc_entries=16)
    assert e_big.area_um2 > e_small.area_um2


# --------------------------------------------------------------------------
# 3. cached config (B) has lower energy/translation than no-cache (A)
# --------------------------------------------------------------------------
def test_cached_lower_energy_per_translation():
    simA, mA = run_scenario(trace=sequential(N), iotlb_assoc=0, pwc_assoc=0,
                            coalesce=1, prefetcher=NoPrefetch())
    estA = estimate_scenario(simA, mA, iotlb_entries=0, pwc_entries=0)

    simB, mB = run_scenario(trace=sequential(N), iotlb_assoc=256, pwc_assoc=16,
                            coalesce=8, prefetcher=NoPrefetch())
    estB = estimate_scenario(simB, mB, iotlb_entries=256, pwc_entries=16)

    assert estB.energy_per_translation_pj < estA.energy_per_translation_pj


# --------------------------------------------------------------------------
# 4. frozen JSON round-trips and contains config hash + tech/struct params
# --------------------------------------------------------------------------
def test_freeze_roundtrip():
    sim, m = run_scenario(trace=sequential(N), iotlb_assoc=256, pwc_assoc=16,
                          coalesce=8, prefetcher=NoPrefetch())
    est = estimate_scenario(sim, m, iotlb_entries=256, pwc_entries=16)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "rec.json")
        rec = est.freeze(path)
        assert os.path.exists(path)
        with open(path) as f:
            loaded = json.load(f)
    assert loaded == rec
    assert len(loaded["config_hash"]) == 64
    assert "tech_params" in loaded and "struct_params" in loaded
    assert loaded["tech_params"]["vdd_v"] == TechParams().vdd_v
    assert loaded["struct_params"]["buffer_ctrl_bits"] == StructParams().buffer_ctrl_bits
    # totals present and self-consistent
    comp_area = sum(c["area_um2"] for c in loaded["components"])
    assert abs(comp_area - loaded["totals"]["area_um2"]) < 1e-6
    # identical setup -> identical hash
    est2 = estimate_scenario(sim, m, iotlb_entries=256, pwc_entries=16)
    assert est2._record()["config_hash"] == loaded["config_hash"]


def test_calib_fit():
    predicted = {"IOTLB": 100.0, "PWC": 50.0}
    measured = {"IOTLB": 150.0, "PWC": 25.0}
    cp = CalibParams.fit(predicted, measured, key="area")
    assert abs(cp.a("IOTLB") - 1.5) < 1e-9
    assert abs(cp.a("PWC") - 0.5) < 1e-9
    assert cp.a("unknown") == 1.0   # default multiplier


# --------------------------------------------------------------------------
# 5. existing A-E perf trends still reproduce unchanged (additive counters only)
# --------------------------------------------------------------------------
def test_perf_trends_unchanged():
    # A: no cache, unlimited
    simA, A = run_scenario(trace=sequential(N), iotlb_assoc=0, pwc_assoc=0,
                           coalesce=1, prefetcher=NoPrefetch())
    assert A.completed == N
    assert simA.memory.accesses == 24000          # 3.0 / page
    assert A.walks_started == 8000
    assert A.peak_walks == 8 and A.peak_buffer == 8
    assert abs(A.avg_lat - 300.0) < 1e-6

    # B: PWC + coalescing -> residual collapses
    simB, B = run_scenario(trace=sequential(N), iotlb_assoc=256, pwc_assoc=16,
                           coalesce=8, prefetcher=NoPrefetch())
    assert simB.memory.accesses == 1017
    assert B.iotlb_hit == 4965 and B.mshr_coalesced == 2035 and B.walks_started == 1000
    assert B.peak_walks == 1
    assert simB.memory.accesses < simA.memory.accesses   # caching helps

    # C: B + prefetch -> latency collapses
    simC, C = run_scenario(trace=sequential(N), iotlb_assoc=256, pwc_assoc=16,
                           coalesce=8, prefetcher=NextLinePrefetch(distance=16, coalesce=8))
    assert simC.memory.accesses == 1023
    assert C.iotlb_hit == 7992 and C.peak_walks == 3
    assert C.avg_lat < B.avg_lat

    # D: random IOVA -> optimisation collapses
    simD, D = run_scenario(trace=random_trace(N), iotlb_assoc=256, pwc_assoc=16,
                           coalesce=8, prefetcher=NoPrefetch())
    assert simD.memory.accesses == 16131
    assert D.walks_started == 7997 and D.peak_walks == 8

    # E: finite resources -> stall (throughput halves)
    simE, E = run_scenario(trace=sequential(N), iotlb_assoc=0, pwc_assoc=0,
                           coalesce=1, prefetcher=NoPrefetch(),
                           num_walkers=4, buffer_size=4)
    assert simE.memory.accesses == 24000
    assert E.peak_walks == 4 and E.peak_buffer == 4
    span = (E.last_complete - E.first_arrival)
    thru_Mps = E.completed / span * 1e9 / 1e6
    assert 13.0 < thru_Mps < 13.6      # ~13.33 M/s, about half the 24.4 M/s target


# --------------------------------------------------------------------------
# 6. walker activity counter is populated and drives walker dynamic power
# --------------------------------------------------------------------------
def test_walker_busy_counter():
    sim, m = run_scenario(trace=sequential(N), iotlb_assoc=0, pwc_assoc=0,
                          coalesce=1, prefetcher=NoPrefetch())
    assert m.walker_busy_ns > 0
    # 8000 walks * 3 accesses * 100 ns
    assert abs(m.walker_busy_ns - 8000 * 3 * 100.0) < 1e-6


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
