"""Glue: SimConfig -> wired engine -> tick loop -> Metrics.

The harness is small on purpose. It exists to:
  1. Build the policy objects (caches, prefetcher, memory, cost model).
  2. Build the workload (a list of (cycle, vpn) tuples).
  3. Instantiate IOMMUEngine, attach those objects, elaborate, tick to done.
  4. Fold cache / memory counters back into the Metrics object.
"""
from __future__ import annotations
from typing import Optional

from pymtl3.passes.PassGroups import DefaultPassGroup

from .config import SimConfig
from .caches import SetAssocCache, make_policy
from .prefetch import make_prefetcher
from .memory import MemoryModel
from .walker_cost import SingleStageCost, NestedCost, DirectoryWalkCost
from .workload import make_trace
from .metrics import Metrics
from .engine import IOMMUEngine


def build_engine_from_config(cfg: SimConfig) -> IOMMUEngine:
    """Construct and wire up an IOMMUEngine according to `cfg`.

    The engine is returned ALREADY elaborated and reset, with internal state
    initialised. The caller just needs to tick it until done."""
    iotlb = SetAssocCache(num_sets=cfg.iotlb.sets, assoc=cfg.iotlb.assoc,
                          policy=make_policy(cfg.iotlb.policy))
    pwc = SetAssocCache(num_sets=cfg.pwc.sets, assoc=cfg.pwc.assoc,
                        policy=make_policy(cfg.pwc.policy))
    prefetcher = make_prefetcher(cfg.prefetcher)
    memory = MemoryModel(latency_cycles=cfg.mem_latency_cycles(),
                         max_outstanding=cfg.mem_max_outstanding)
    if cfg.nested:
        cost_model = NestedCost(coalesce=cfg.coalesce_factor,
                                levels=cfg.levels,
                                s2_residual=cfg.nested_s2_residual)
    else:
        cost_model = SingleStageCost(coalesce=cfg.coalesce_factor,
                                     levels=cfg.levels)

    # Metrics created here so the directory-walk cost model can write into it.
    m = Metrics()

    # Optional RISC-V directory-table walks (DDTW / PDTW). When either is on we
    # wrap the base page-table cost model; DDT$ / PDT$ are real caches.
    ddt_cache = pdt_cache = None
    if cfg.ddtw_enabled or cfg.pdtw_enabled:
        ddt_cache = SetAssocCache(num_sets=cfg.ddt.sets, assoc=cfg.ddt.assoc,
                                  policy=make_policy(cfg.ddt.policy))
        pdt_cache = SetAssocCache(num_sets=cfg.pdt.sets, assoc=cfg.pdt.assoc,
                                  policy=make_policy(cfg.pdt.policy))
        cost_model = DirectoryWalkCost(
            cost_model,
            ddtw_enabled=cfg.ddtw_enabled, pdtw_enabled=cfg.pdtw_enabled,
            ddt_cache=ddt_cache, pdt_cache=pdt_cache,
            ddt_miss=cfg.ddtw_miss_accesses(), pdt_miss=cfg.pdtw_miss_accesses(),
            num_devices=cfg.trace.num_devices,
            num_processes=cfg.trace.num_processes,
            ctx_switch_every=cfg.trace.ctx_switch_every,
            metrics=m)

    workload = make_trace(cfg.trace, clock_mhz=cfg.clock_mhz,
                          wire_gbs=cfg.wire_gbs, page_kb=cfg.page_kb)

    eng = IOMMUEngine()
    eng.iotlb = iotlb
    eng.pwc = pwc
    eng.prefetcher = prefetcher
    eng.memory = memory
    eng.cost_model = cost_model
    eng.workload = workload
    eng.num_walkers = cfg.num_walkers
    eng.buffer_size = cfg.buffer_size
    eng.hit_latency_cycles = cfg.hit_latency_cycles
    eng.mem_latency_cycles = cfg.mem_latency_cycles()
    eng.max_cycles = cfg.max_cycles
    eng.m = m
    # Stash the directory caches so run_simulation can fold their stats.
    eng._ddt_cache = ddt_cache
    eng._pdt_cache = pdt_cache

    eng.elaborate()
    eng.apply(DefaultPassGroup())
    eng.sim_reset()
    eng.reset_state()
    return eng


def run_simulation(cfg: SimConfig) -> tuple[Metrics, IOMMUEngine]:
    """Build, tick, fold counters; return (metrics, engine)."""
    eng = build_engine_from_config(cfg)

    # Tick until the engine asserts done_out, or until max_cycles.
    # The done_reg flips inside _tick_cycle, so we read it AFTER the tick.
    while int(eng.done_out) == 0 and int(eng.cycle_out) <= cfg.max_cycles:
        eng.sim_tick()

    # Roll up cache / memory counters into the metrics object.
    m = eng.m
    m.iotlb_hits = eng.iotlb.hits
    m.iotlb_misses = eng.iotlb.misses
    m.pwc_hits = eng.pwc.hits
    m.pwc_misses = eng.pwc.misses
    m.mem_accesses = eng.memory.accesses
    m.mem_peak_outstanding = eng.memory.peak_outstanding
    if m.sim_cycles == 0:
        m.sim_cycles = int(eng.cycle_out)
    return m, eng


# -------------------- reporting helpers (CLI-shaped) --------------------

def fmt_report(name: str, cfg: SimConfig, m: Metrics) -> str:
    """Pretty per-scenario printout that mirrors the reference simulator."""
    ns_per_cycle = cfg.ns_per_cycle()
    n = max(1, m.completed)
    span_cycles = max(1,
                      m.last_complete_cycle
                      - (m.first_arrival_cycle if m.first_arrival_cycle else 0))
    span_ns = span_cycles * ns_per_cycle
    throughput_mps = n / span_ns * 1e9 / 1e6 if span_ns > 0 else 0.0
    target_mps = cfg.target_throughput_per_s() / 1e6
    sustained = "YES" if throughput_mps >= 0.995 * target_mps else "no"
    lines = [
        f"\n=== {name} ===",
        f"  completed         : {m.completed}",
        f"  total mem accesses: {m.mem_accesses}  "
        f"({m.mem_accesses / n:.3f} /page)",
        f"  IOTLB hit         : {m.iotlb_hit}  / coalesced(MSHR): "
        f"{m.mshr_coalesced}  / true miss(walk): {m.walks_started}",
        f"  required N (peak walks): {m.peak_walks}",
        f"  required buffer (peak) : {m.peak_buffer}",
        f"  avg latency       : {m.avg_lat_cycles * ns_per_cycle:.1f} ns "
        f"(p99 {m.p99_lat_cycles * ns_per_cycle:.1f} ns)",
        f"  achieved throughput: {throughput_mps:.2f} M/s  "
        f"(target {target_mps:.2f} M/s)  sustained={sustained}",
    ]
    if cfg.ddtw_enabled or cfg.pdtw_enabled:
        parts = []
        if cfg.ddtw_enabled:
            parts.append(f"DDTW walks(DDT$ miss): {m.ddtw_walks} "
                         f"(+{cfg.ddtw_miss_accesses()} acc each, DDT$ hit {m.ddt_hits})")
        if cfg.pdtw_enabled:
            parts.append(f"PDTW walks(PDT$ miss): {m.pdtw_walks} "
                         f"(+{cfg.pdtw_miss_accesses()} acc each, PDT$ hit {m.pdt_hits})")
        lines.append("  " + "  /  ".join(parts))
    return "\n".join(lines)
