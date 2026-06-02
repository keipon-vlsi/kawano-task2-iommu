"""Page-table-walk cost model.

`cost(vpn, pwc)` returns a WalkPlan describing the number of memory accesses
the walker must perform AND the cache entries to install on completion
(leaf-line coalescing of 8 vpns, PWC inserts for intermediate prefixes).

Single-stage (SingleStageCost) is the default. NestedCost adds an S2
residual cost per access, modelling two-stage (host + guest) translation.
Add a new cost model = subclass WalkCostModel.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List


@dataclass
class WalkPlan:
    accesses: int                                        # memory accesses the walker will issue
    iotlb_keys: List[int] = field(default_factory=list)  # vpns to install on completion
    pwc_keys: List[tuple] = field(default_factory=list)  # PWC prefixes to install on completion


class WalkCostModel(ABC):
    @abstractmethod
    def cost(self, vpn: int, pwc) -> WalkPlan: ...


class SingleStageCost(WalkCostModel):
    """Sv39-like single-stage walk.

    On each translation:
      1. If L2 prefix not in PWC, 1 memory access for the root PTE.
      2. If L1 prefix not in PWC, 1 memory access for the intermediate PTE.
      3. Always 1 memory access for the leaf line (64 B = 8 PTEs => coalesces
         8 sequential vpns into a single warm of the IOTLB).

    `levels` is kept for completeness; the model assumes a 3-level walk."""
    def __init__(self, coalesce: int = 8, levels: int = 3):
        self.c = coalesce
        self.levels = levels

    def cost(self, vpn, pwc) -> WalkPlan:
        acc = 0
        l2 = ("L2", vpn >> 18)
        l1 = ("L1", vpn >> 9)
        if not pwc.lookup(l2):
            acc += 1                                   # root PTE
        if not pwc.lookup(l1):
            acc += 1                                   # L1 PTE
        acc += 1                                       # leaf line
        line = (vpn // self.c) * self.c
        iotlb_keys = list(range(line, line + self.c))  # warm whole 64 B line
        return WalkPlan(accesses=acc, iotlb_keys=iotlb_keys, pwc_keys=[l1, l2])


class DirectoryWalkCost(WalkCostModel):
    """Prepend RISC-V IOMMU directory-table walks to a base page-table walk.

    Before the address translation walk, an IOMMU resolves the request's
    *device context* (via the Device Directory Table) and *process context*
    (via the Process Directory Table). Each is cached (DDT$ / PDT$); on a cache
    miss the directory walk costs extra memory accesses, on a hit it is free.

    This class wraps any base WalkCostModel (SingleStageCost / NestedCost) and
    adds, per page-table walk:
      - DDTW: `ddt_miss` accesses on a DDT$ miss (keyed by device_id).
              DDT lives in supervisor PA -> single-stage -> 3 (independent of nesting).
      - PDTW: `pdt_miss` accesses on a PDT$ miss (keyed by (device_id, process_id)).
              PDT base may be guest-physical -> under nesting each level is
              S2-translated -> 15; without nesting -> 3.

    Each stage is independently toggled (`ddtw_enabled` / `pdtw_enabled`).

    device_id / process_id are produced by an internal context schedule so the
    engine does not need to carry them. With num_devices=num_processes=1 (the
    default) there is exactly one cold DDTW and one cold PDTW for the whole run
    (steady-state ~0), matching the directory caches' amortisation. Set
    `ctx_switch_every>0` (and num_devices/num_processes>1) to rotate contexts
    and exercise repeated directory walks / cache pressure.

    NOTE: the context schedule advances once per *page-table walk* (i.e. per
    cost() call: true misses + prefetch walks), not per demand request -- a
    first-order approximation that is exact for the single-context case.
    """
    def __init__(self, base, *, ddtw_enabled=False, pdtw_enabled=False,
                 ddt_cache=None, pdt_cache=None,
                 ddt_miss=3, pdt_miss=3,
                 num_devices=1, num_processes=1, ctx_switch_every=0,
                 metrics=None):
        self.base = base
        self.c = base.c                  # engine reads cost_model.c for line calc
        self.levels = getattr(base, "levels", 3)
        self.ddtw_enabled = ddtw_enabled
        self.pdtw_enabled = pdtw_enabled
        self.ddt = ddt_cache
        self.pdt = pdt_cache
        self.ddt_miss = ddt_miss
        self.pdt_miss = pdt_miss
        self.num_devices = max(1, num_devices)
        self.num_processes = max(1, num_processes)
        self.ctx_switch_every = max(0, ctx_switch_every)
        self.m = metrics
        self._calls = 0

    def _context(self):
        """Return (device_id, process_id) for the current walk and advance."""
        step = (self._calls // self.ctx_switch_every) if self.ctx_switch_every else 0
        self._calls += 1
        dev = step % self.num_devices
        proc = (step // self.num_devices) % self.num_processes
        return dev, proc

    def cost(self, vpn, pwc) -> WalkPlan:
        plan = self.base.cost(vpn, pwc)
        dev, proc = self._context()
        if self.ddtw_enabled and self.ddt is not None:
            if not self.ddt.lookup(dev):
                plan.accesses += self.ddt_miss
                self.ddt.insert(dev)
                if self.m is not None:
                    self.m.ddtw_walks += 1
            elif self.m is not None:
                self.m.ddt_hits += 1
        if self.pdtw_enabled and self.pdt is not None:
            key = (dev, proc)
            if not self.pdt.lookup(key):
                plan.accesses += self.pdt_miss
                self.pdt.insert(key)
                if self.m is not None:
                    self.m.pdtw_walks += 1
            elif self.m is not None:
                self.m.pdt_hits += 1
        return plan


class NestedCost(SingleStageCost):
    """Two-stage: each S1 access additionally costs `s2_residual` memory
    accesses for the S2 walk that translates the S1 page-table address.

    This is a coarse model — for full fidelity you would carry a dedicated
    S2 PWC. It is enough to expose the order-of-magnitude trend (nested
    translation roughly doubles or triples memory traffic per walk)."""
    def __init__(self, coalesce: int = 8, levels: int = 3, s2_residual: int = 1):
        super().__init__(coalesce=coalesce, levels=levels)
        self.s2 = s2_residual

    def cost(self, vpn, pwc) -> WalkPlan:
        p = super().cost(vpn, pwc)
        # Each of the {root, L1, leaf} accesses needs an S2 translation.
        p.accesses += self.s2 * p.accesses
        return p
