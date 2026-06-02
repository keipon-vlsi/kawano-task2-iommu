"""Page-table-walk cost model (swappable component).
cost(vpn, sim) returns the number of required memory accesses plus the cache
entries to insert on completion. This is a single-stage (3-level) implementation.
Extend this class to add nesting / DDT / PDT."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class WalkPlan:
    accesses: int
    iotlb_keys: list = field(default_factory=list)   # inserted into IOTLB on completion (coalescing)
    pwc_keys: list = field(default_factory=list)      # inserted into PWC on completion

class WalkCostModel(ABC):
    @abstractmethod
    def cost(self, vpn, sim) -> WalkPlan: ...

class SingleStageCost(WalkCostModel):
    """Sv39-like 3 levels. PWC short-circuits upper levels; leaf uses 64B coalescing."""
    def __init__(self, coalesce=8, levels=3):
        self.c = coalesce; self.levels = levels # levels=3 means 3-level walk (Sv39); coalesce=8 means 64B coalescing (8x4KB pages)
    def cost(self, vpn, sim):
        acc = 0
        l2 = ('L2', vpn >> 18); l1 = ('L1', vpn >> 9)
        if not sim.pwc.lookup(l2): acc += 1     # read root PTE
        if not sim.pwc.lookup(l1): acc += 1     # read L1 PTE
        acc += 1                                 # read leaf line (64B)
        line = (vpn // self.c) * self.c
        iotlb_keys = list(range(line, line + self.c))   # warm 8 entries at once
        return WalkPlan(accesses=acc, iotlb_keys=iotlb_keys, pwc_keys=[l1, l2])

class NestedCost(SingleStageCost):
    """Example extension: add a simple S2 residual cost to each access.
    s2_residual = effective cost of the data-GPA S2 leaf fetch (after coalescing)."""
    def __init__(self, coalesce=8, s2_residual=1):
        super().__init__(coalesce); self.s2 = s2_residual
    def cost(self, vpn, sim):
        p = super().cost(vpn, sim)
        p.accesses += self.s2     # simplified: add the data-GPA translation (full impl needs an S2 cache)
        return p