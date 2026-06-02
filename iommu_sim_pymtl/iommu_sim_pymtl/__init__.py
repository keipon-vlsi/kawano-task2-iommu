"""PyMTL3 cycle-level IOMMU architecture-exploration simulator.

Modules:
  - config      : SimConfig dataclass (one place to set every knob).
  - caches      : Set-associative cache with pluggable LRU / FIFO / Random.
  - prefetch    : NoPrefetch / NextLineStride / ConfidenceStride.
  - memory      : Fixed-latency memory model with outstanding tracking.
  - walker_cost : SingleStageCost (Sv39-like) / NestedCost (two-stage).
  - workload    : Trace generators (sequential / random / multi_stream).
  - metrics     : Metrics container.
  - engine      : IOMMUEngine — the PyMTL3 cycle-level Component.
  - harness     : run_simulation(cfg) — elaborates the engine and ticks it.

Engine/policy separation: the engine clocks transactions through fixed
datapath stages (admit -> translate -> walk -> complete), while every policy
(replacement, prefetch, walk-cost, memory) is plugged in via a small ABC.
Swap a policy by editing the config — never the engine.
"""
from .config import SimConfig, IOTLBCfg, PWCCfg, PrefetchCfg, TraceCfg
from .caches import SetAssocCache, LRU, FIFO, RandomRepl
from .prefetch import NoPrefetch, NextLineStride, ConfidenceStride
from .memory import MemoryModel
from .walker_cost import WalkPlan, SingleStageCost, NestedCost
from .workload import sequential, random_trace, multi_stream, wire_inter_arrival_ns
from .metrics import Metrics
from .engine import IOMMUEngine
from .harness import build_engine_from_config, run_simulation

__all__ = [
    "SimConfig", "IOTLBCfg", "PWCCfg", "PrefetchCfg", "TraceCfg",
    "SetAssocCache", "LRU", "FIFO", "RandomRepl",
    "NoPrefetch", "NextLineStride", "ConfidenceStride",
    "MemoryModel",
    "WalkPlan", "SingleStageCost", "NestedCost",
    "sequential", "random_trace", "multi_stream", "wire_inter_arrival_ns",
    "Metrics", "IOMMUEngine",
    "build_engine_from_config", "run_simulation",
]
