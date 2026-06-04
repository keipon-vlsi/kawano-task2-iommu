"""Shared run helpers used by run.py and sweep.py: build a trace, run the engine,
and collect a flat summary dict. Keeps the CLIs thin."""
from __future__ import annotations

from workload import generate, inter_arrival_cycles
from engine import Simulator
from estimator import estimate


def run_sim(cfg, warmup_frac=0.0):
    requests, events = generate(cfg)
    sim = Simulator(cfg, requests, events, warmup_frac=warmup_frac)
    m = sim.run()
    return sim, m


def wire_rate_met(cfg, sim, m, margin=0.0, strict=True):
    """Sustained = steady-state stall-free + small margin (design_doc §9): achieved
    throughput reaches the target AND no post-warmup back-pressure/walk stalls.
    ``strict=False`` drops the stall requirement (throughput-only)."""
    target = cfg.target_throughput_mps * (1.0 + margin)
    if m.throughput_mps(cfg.cycle_ns) < target * 0.995:
        return False
    if strict and (m.arrival_stalls > 0 or m.walk_stalls > 0):
        return False
    return True


def summarize(cfg, sim, m):
    cyc = cfg.cycle_ns
    completed = m.completed or 1
    res = estimate(cfg, sim.caches, m, dram_accesses=sim.memory.accesses)
    caches = sim.caches
    hit = {}
    for name, c in [("iotlb", caches.iotlb), ("s1_pwc", caches.s1_l1), ("s2_pwc", caches.s2_pwc),
                    ("table_gpa", caches.table_gpa), ("data_gpa", caches.data_gpa),
                    ("ddtc", caches.ddtc), ("pdtc", caches.pdtc)]:
        hit[name] = (c.hits, c.misses, c.hit_rate)
    return {
        "name": cfg.name,
        "mode": cfg.mode,
        "completed": m.completed,
        "throughput_mps": m.throughput_mps(cyc),
        "target_mps": cfg.target_throughput_mps,
        "wire_rate_met": wire_rate_met(cfg, sim, m),
        "peak_walks": m.peak_walks,
        "peak_buffer": m.peak_buffer,
        "io_bridge_peak": m.io_bridge_peak,
        "mem_outstanding_peak": sim.memory.peak_outstanding,
        "mem_bandwidth_gbs": sim.memory.bandwidth_gbs(m.sim_cycles, cyc),
        "mem_accesses": sim.memory.accesses,
        "accesses_per_translation": sim.memory.accesses / completed,
        "iotlb_hit": m.iotlb_hit,
        "mshr_coalesced": m.mshr_coalesced,
        "walks_started": m.walks_started,
        "faults": m.faults,
        "context_switches": m.context_switches,
        "invalidations": m.invalidations,
        "avg_lat_cyc": m.avg_lat,
        "avg_lat_ns": m.avg_lat * cyc,
        "max_lat_cyc": m.max_lat,
        "max_lat_ns": m.max_lat * cyc,
        "p99_lat_cyc": m.p99_lat,
        "p99_lat_ns": m.p99_lat * cyc,
        "hit": hit,
        "miss_penalty": m.miss_penalty_table(cyc),
        "area_ge": res.area_ge,
        "dyn_power": res.dyn_power,
        "stat_power": res.stat_power,
        "energy_per_translation": res.energy_per_translation,
        "dram_energy_per_translation": (res.dram_energy / completed),
        "fom_area_x_energy": res.area_ge * res.energy_per_translation,
        "_result": res,
    }
