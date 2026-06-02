"""Central configuration for the simulator.

One dataclass per swappable subcomponent so that "I want to change X" maps to
"edit one field on SimConfig and re-run". `harness.build_engine_from_config`
turns a SimConfig into a fully-wired IOMMUEngine.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal, Any


@dataclass
class IOTLBCfg:
    """IOTLB: caches final vpn -> PA (the result of a completed page walk)."""
    sets: int = 1
    # None  = infinite (used to measure required IOTLB size)
    # 0     = disabled (always miss, IOTLB removed from the path)
    # >0    = num entries per set, real set-associative behaviour
    assoc: Optional[int] = 256
    policy: Literal["lru", "fifo", "random"] = "lru"


@dataclass
class PWCCfg:
    """Page-Walk Cache: caches intermediate page-table entries (L1, L2 prefixes)
    so the walker can short-circuit upper levels of a multi-level walk."""
    sets: int = 1
    assoc: Optional[int] = 16
    policy: Literal["lru", "fifo", "random"] = "lru"


@dataclass
class PrefetchCfg:
    """Prefetcher selection.
      - none     : NoPrefetch
      - nextline : NextLineStride(distance)
      - stride   : ConfidenceStride(distance, threshold) — self-disables on
                   non-sequential streams (graceful degradation)."""
    kind: Literal["none", "nextline", "stride"] = "none"
    distance: int = 16          # number of pages of look-ahead
    threshold: int = 4          # confidence threshold for stride prefetcher
    coalesce: int = 8           # leaf-line coalescing factor (matches cost model)


@dataclass
class TraceCfg:
    """Workload pattern. `n` translations into the model."""
    kind: Literal["sequential", "random", "multi_stream"] = "sequential"
    n: int = 8000
    span_pages: int = 1_000_000   # only for random
    streams: int = 4              # only for multi_stream
    stride_pages: int = 1         # only for multi_stream
    base_vpn: int = 0
    seed: int = 0


@dataclass
class SimConfig:
    """Whole-system knobs. Every architectural parameter the prompt lists
    lives here; the engine does not embed any of them as constants."""
    # --- physical / workload ---
    wire_gbs: float = 100.0            # Ethernet effective payload rate (GB/s)
    page_kb: int = 4                   # page size (KB)
    clock_mhz: float = 400.0           # IOMMU clock
    mem_latency_ns: float = 100.0      # main-memory access latency

    # --- microarchitecture ---
    coalesce_factor: int = 8           # PTEs per 64B cache line (leaf coalescing)
    levels: int = 3                    # walk levels (Sv39 => 3)
    nested: bool = False               # if True, use NestedCost (two-stage)
    nested_s2_residual: int = 1        # extra memory accesses per walk under nesting

    iotlb: IOTLBCfg = field(default_factory=IOTLBCfg)
    pwc: PWCCfg = field(default_factory=PWCCfg)
    prefetcher: PrefetchCfg = field(default_factory=PrefetchCfg)

    num_walkers: Optional[int] = None  # None = infinite (measure required N)
    buffer_size: Optional[int] = None  # None = infinite (measure required B)

    mem_max_outstanding: Optional[int] = None
    hit_latency_cycles: int = 1        # IOTLB-hit completion latency

    trace: TraceCfg = field(default_factory=TraceCfg)

    # --- run control ---
    max_cycles: int = 10_000_000       # safety cap to prevent runaway sims

    # Free-form notes (label written to results.csv)
    label: str = ""

    def cycles_per_ns(self) -> float:
        return self.clock_mhz / 1000.0

    def ns_per_cycle(self) -> float:
        return 1000.0 / self.clock_mhz

    def mem_latency_cycles(self) -> int:
        # round to integer cycles. 100ns @ 400MHz => 40 cycles exactly.
        return int(round(self.mem_latency_ns * self.cycles_per_ns()))

    def wire_inter_arrival_cycles(self) -> float:
        """Inter-arrival in (fractional) cycles. 4096B/100GB/s = 40.96ns;
        @400MHz that is 16.384 cycles. We keep the fractional value and the
        workload generator quantises arrivals using a running accumulator."""
        page_bytes = self.page_kb * 1024
        ia_ns = page_bytes / (self.wire_gbs * 1e9) * 1e9
        return ia_ns * self.cycles_per_ns()

    def target_throughput_per_s(self) -> float:
        page_bytes = self.page_kb * 1024
        return self.wire_gbs * 1e9 / page_bytes
