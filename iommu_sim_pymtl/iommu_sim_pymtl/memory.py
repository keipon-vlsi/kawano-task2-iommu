"""Memory model (swappable).

Default: fixed AXI-like latency (100 ns => 40 cycles @ 400 MHz) with optional
`max_outstanding` cap. Tracks total accesses and peak outstanding for metrics.
Replace with a bank / row-buffer / bandwidth model later by subclassing.
"""
from __future__ import annotations
from typing import Optional


class MemoryModel:
    def __init__(self, latency_cycles: int = 40,
                 max_outstanding: Optional[int] = None):
        self.latency_cycles = latency_cycles
        self.max_outstanding = max_outstanding
        self.outstanding = 0
        self.peak_outstanding = 0
        self.accesses = 0

    def can_issue(self, n: int = 1) -> bool:
        if self.max_outstanding is None:
            return True
        return self.outstanding + n <= self.max_outstanding

    def issue(self, n: int = 1) -> None:
        self.accesses += n
        self.outstanding += n
        if self.outstanding > self.peak_outstanding:
            self.peak_outstanding = self.outstanding

    def retire(self, n: int = 1) -> None:
        self.outstanding -= n
