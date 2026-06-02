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
