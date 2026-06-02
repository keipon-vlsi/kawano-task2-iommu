"""Metrics container shared between engine and reporters.

Both arrival_cycle and last_complete_cycle are integer cycle stamps; the
harness converts them to wall-clock ns / throughput at report time, using
the SimConfig clock frequency.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Metrics:
    completed: int = 0
    peak_walks: int = 0
    peak_buffer: int = 0
    walks_started: int = 0
    mshr_coalesced: int = 0
    iotlb_hit: int = 0
    latencies_cycles: List[int] = field(default_factory=list)
    first_arrival_cycle: Optional[int] = None
    last_complete_cycle: int = 0
    sim_cycles: int = 0

    # --- cache stats, filled in by harness from cache objects ---
    iotlb_hits: int = 0
    iotlb_misses: int = 0
    pwc_hits: int = 0
    pwc_misses: int = 0

    # --- memory stats ---
    mem_accesses: int = 0
    mem_peak_outstanding: int = 0

    # --- directory-table walks (DDTW / PDTW); filled by DirectoryWalkCost ---
    ddtw_walks: int = 0       # DDT$ misses that triggered a device-directory walk
    pdtw_walks: int = 0       # PDT$ misses that triggered a process-directory walk
    ddt_hits: int = 0
    pdt_hits: int = 0

    def add_lat(self, x: int) -> None:
        self.latencies_cycles.append(x)

    @property
    def avg_lat_cycles(self) -> float:
        if not self.latencies_cycles:
            return 0.0
        return sum(self.latencies_cycles) / len(self.latencies_cycles)

    @property
    def p99_lat_cycles(self) -> float:
        if not self.latencies_cycles:
            return 0.0
        s = sorted(self.latencies_cycles)
        return s[min(len(s) - 1, int(0.99 * len(s)))]
