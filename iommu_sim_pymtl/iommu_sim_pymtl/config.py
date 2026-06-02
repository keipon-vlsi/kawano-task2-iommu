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
class DirCacheCfg:
    """Device/Process directory cache (DDT$ / PDT$).

    Caches the result of a device-directory (DDTW) or process-directory (PDTW)
    table walk so the walk is paid once per context and then served from cache.
    Same assoc semantics as the other caches:
      None = infinite, 0 = disabled (every context walk re-walks), >0 = entries.
    """
    sets: int = 1
    assoc: Optional[int] = 32
    policy: Literal["lru", "fifo", "random"] = "lru"


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
    # --- device / process context (drives DDT$ / PDT$ activity) ---
    num_devices: int = 1          # distinct device_ids in the stream
    num_processes: int = 1        # distinct process_ids in the stream
    ctx_switch_every: int = 0     # 0 = static context; >0 = rotate context every N walks


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
    levels: int = 3                    # S1 walk levels (Sv39 => 3)
    nested: bool = True                # if True, use NestedCost (exact two-stage)
    s2_levels: Optional[int] = None    # G-stage (second-stage) walk depth; None => same as `levels`

    # --- RISC-V IOMMU directory-table walks (off by default) ---
    ddtw_enabled: bool = True         # model Device-Directory-Table walks + DDT$
    pdtw_enabled: bool = True         # model Process-Directory-Table walks + PDT$
    ddt_levels: int = 3                # DDTW depth -> accesses charged on a DDT$ miss
    pdt_levels: int = 3                # PDTW base depth (see pdtw_miss_accesses())

    iotlb: IOTLBCfg = field(default_factory=IOTLBCfg)
    pwc: PWCCfg = field(default_factory=PWCCfg)
    ddt: DirCacheCfg = field(default_factory=DirCacheCfg)
    pdt: DirCacheCfg = field(default_factory=DirCacheCfg)
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

    def s2_depth(self) -> int:
        """G-stage (second-stage) walk depth. Defaults to the S1 `levels`."""
        return self.levels if self.s2_levels is None else self.s2_levels

    def ddtw_miss_accesses(self) -> int:
        """Memory accesses for one Device-Directory-Table walk (on a DDT$ miss).

        The DDT lives in supervisor/hypervisor physical memory, so it is a
        single-stage walk regardless of nesting: `ddt_levels` accesses (=3 for
        a 3-level DDT)."""
        return self.ddt_levels

    def pdtw_miss_accesses(self) -> int:
        """Memory accesses for one Process-Directory-Table walk (on a PDT$ miss).

        Without nesting the PDT is a plain `pdt_levels` walk (=3). Under
        two-stage translation the PDT base is a guest-physical address, so each
        of the `pdt_levels` levels is itself translated by a full G-stage
        (`s2_depth()`-deep) walk. The classic two-stage worst case is
            (pdt_levels + 1) * (s2_levels + 1) - 1
        = (3+1)*(3+1)-1 = 15 for a 3-level PDT under a 3-level G-stage."""
        if not self.nested:
            return self.pdt_levels
        return (self.pdt_levels + 1) * (self.s2_depth() + 1) - 1
