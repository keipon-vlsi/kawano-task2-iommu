"""Memory model (swappable). AXI split-transaction abstraction: latency is fixed
per access (a DRAM row activation), outstanding reads are tracked, and the
outstanding cap bounds parallel walks (design_premises §6).

  * ``latency_cycles``    : cycles per access (default 40 = 100 ns at 400 MHz).
  * ``max_outstanding``   : ceiling on concurrent in-flight reads (None = inf).
  * ``bank_parallel``     : if False, an extra serialization penalty is applied
                            to model bank conflicts under heavy outstanding load.
  * ``coalescing_effective``: if False, a 64 B line read returns only one PTE
                            (row-buffer locality lost) -> coalesce factor forced to 1.
"""
from __future__ import annotations


class MemoryModel:
    def __init__(self, latency_cycles=40, max_outstanding=None,
                 bank_parallel=True, coalescing_effective=True):
        self.latency = int(latency_cycles)
        self.max_outstanding = max_outstanding
        self.bank_parallel = bank_parallel
        self.coalescing_effective = coalescing_effective
        self.outstanding = 0            # concurrent in-flight read chains (~= active walks)
        self.peak_outstanding = 0
        self.accesses = 0               # total reads issued (bandwidth / energy)
        self.bytes_read = 0

    def can_issue(self):
        """A walk is a sequential pointer-chase: it holds ONE outstanding read at
        a time. The AXI outstanding cap therefore bounds concurrent walks
        (design_premises §6)."""
        if self.max_outstanding is None:
            return True
        return self.outstanding + 1 <= self.max_outstanding

    def enter(self):
        self.outstanding += 1
        self.peak_outstanding = max(self.peak_outstanding, self.outstanding)

    def exit(self):
        self.outstanding -= 1

    def account(self, n_accesses, bytes_per_access=64):
        """Count total reads for bandwidth/energy (not the outstanding slot)."""
        self.accesses += n_accesses
        self.bytes_read += n_accesses * bytes_per_access

    def access_cycles(self):
        """Latency of one access; bank conflicts add a small penalty when banks
        are not parallel and the channel is busy."""
        if self.bank_parallel:
            return self.latency
        # serialized banks: a queued access waits behind others (coarse model)
        return self.latency + max(0, self.outstanding) * 2

    def bandwidth_gbs(self, sim_time_cycles, cycle_ns):
        if sim_time_cycles <= 0:
            return 0.0
        sim_time_s = sim_time_cycles * cycle_ns * 1e-9
        return self.bytes_read / sim_time_s / 1e9
