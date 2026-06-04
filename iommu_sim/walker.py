"""Page-table-walk cost models (swappable). ``cost()`` probes the live cache set
and returns a ``WalkPlan``: the number of *sequential* memory accesses on the
critical path (latency driver), the total accesses issued (bandwidth), a
miss-type classification (design_doc §6), and the cache entries to fill on
completion (coalescing fills the whole 64 B line into the combined IOTLB).

Modes:
  * ``BareCost`` / single-stage (bare, s1_only, s2_only): Sv39-like 3-level walk.
  * ``NestedCost``: Sv39 + Sv39x4 two-stage walk. Cold ~= a full 2D walk; steady
    state ~= 2 accesses per 8 pages (~2x single-stage) -- design_premises §4/§15.

Miss-type labels (per-translation, for the miss-penalty distribution):
  iotlb_hit | mshr_coalesced | pwc_full_hit | pwc_partial | full_cold
(iotlb_hit / mshr_coalesced are decided by the engine before a walk starts.)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class WalkPlan:
    accesses: int                                  # sequential reads on the critical path
    total_accesses: int                            # total reads issued (bandwidth)
    miss_type: str
    fills: dict = field(default_factory=dict)      # cache attr name -> list[key]


# structural worst-case cold-walk depth per mode (design_doc §6, CLAUDE.md).
# Reported as the full_cold miss-penalty characteristic; the dynamic first walk
# is usually cheaper because the G-stage root is a register.
COLD_DEPTH = {"bare": 3, "s1_only": 3, "s2_only": 3, "nested": 15}


class WalkCostModel(ABC):
    @abstractmethod
    def cost(self, vpn, data_page, ctx, sim) -> WalkPlan: ...

    def cold_depth(self):
        return 3


# --------------------------------------------------------------------------
class SingleStageCost(WalkCostModel):
    """Sv39-like 3-level walk. PWC short-circuits the upper levels; the leaf line
    read is 64 B = ``coalesce`` PTEs, filling the combined IOTLB for the whole
    line at once. Used for bare / s1_only / s2_only modes."""

    def __init__(self, levels=3):
        self.levels = levels

    def cost(self, vpn, data_page, ctx, sim):
        cs = sim.caches
        c = sim.eff_coalesce
        acc = 0
        miss_levels = 0
        fills = {"iotlb": [], "s1_l2": [], "s1_l1": []}

        if self.levels >= 3:
            l2key = ("L2", vpn >> 18, ctx)
            if not cs.s1_l2.lookup(l2key):
                acc += 1
                miss_levels += 1
                fills["s1_l2"].append(l2key)
        if self.levels >= 2:
            l1key = ("L1", vpn >> 9, ctx)
            if not cs.s1_l1.lookup(l1key):
                acc += 1
                miss_levels += 1
                fills["s1_l1"].append(l1key)

        acc += 1                                   # leaf line read (coalesced)
        line = (vpn // c) * c
        fills["iotlb"] = [(line, ctx)]             # one combined IOTLB line entry covers c pages

        miss_type = self._classify(miss_levels)
        return WalkPlan(accesses=acc, total_accesses=acc, miss_type=miss_type, fills=fills)

    @staticmethod
    def _classify(miss_levels):
        if miss_levels == 0:
            return "pwc_full_hit"
        if miss_levels >= 2:
            return "full_cold"
        return "pwc_partial"

    def cold_depth(self):
        return self.levels


# --------------------------------------------------------------------------
class NestedCost(WalkCostModel):
    """Sv39 (VS-stage) + Sv39x4 (G-stage). Every guest PTE pointer is a GPA that
    the G-stage must translate before the guest PTE can be read. Cold = full 2D
    walk; steady state collapses to (guest leaf line) + (data-GPA S2 leaf line)
    = ~2 accesses per coalesced line. The G-stage root is a register (loaded
    once)."""

    # distinct GPA-table-page id namespaces (root / L1-table / leaf-table)
    ROOT_TBL = 1 << 40
    L1_TBL = 2 << 40
    LEAF_TBL = 3 << 40

    def cold_depth(self):
        return 15

    # --- G-stage walk for a guest *table* page GPA (table_gpa cache result) ---
    def _s2_table(self, table_id, ctx, cs, fills):
        tkey = ("tbl", table_id, ctx)
        if cs.table_gpa.lookup(tkey):
            return 0
        n = 0
        s2u = ("s2u", table_id >> 9, ctx)          # G-stage upper (root is a register)
        if not cs.s2_pwc.lookup(s2u):
            n += 1
            fills.setdefault("s2_pwc", []).append(s2u)
        n += 1                                     # S2 leaf for the table page
        fills.setdefault("table_gpa", []).append(tkey)
        return n

    # --- G-stage walk for the final *data* GPA (folded into IOTLB; coalesced) ---
    def _s2_data(self, data_page, ctx, cs, fills, c):
        dline = (data_page // c) * c
        if not cs.data_gpa.disabled:
            dkey = ("dat", dline, ctx)
            if cs.data_gpa.lookup(dkey):
                return 0
        n = 0
        s2u = ("s2du", data_page >> 9, ctx)
        if not cs.s2_pwc.lookup(s2u):
            n += 1
            fills.setdefault("s2_pwc", []).append(s2u)
        n += 1                                     # S2 data-leaf line (coalesced)
        if not cs.data_gpa.disabled:
            fills.setdefault("data_gpa", []).append(("dat", dline, ctx))
        return n

    def cost(self, vpn, data_page, ctx, sim):
        cs = sim.caches
        c = sim.eff_coalesce
        acc = 0
        miss_levels = 0
        fills = {"iotlb": [], "s1_l2": [], "s1_l1": []}

        # one-time G-stage root register load (part of the very first cold 2D walk)
        if not cs._s2_root_loaded:
            acc += 1
            cs._s2_root_loaded = True

        # guest L2 (root table is invariant within a context)
        l2key = ("L2", vpn >> 18, ctx)
        if not cs.s1_l2.lookup(l2key):
            acc += self._s2_table(self.ROOT_TBL, ctx, cs, fills)
            acc += 1                               # read guest L2 PTE
            miss_levels += 1
            fills["s1_l2"].append(l2key)

        # guest L1 (1 GB region)
        l1key = ("L1", vpn >> 9, ctx)
        if not cs.s1_l1.lookup(l1key):
            acc += self._s2_table(self.L1_TBL | (vpn >> 18), ctx, cs, fills)
            acc += 1                               # read guest L1 PTE
            miss_levels += 1
            fills["s1_l1"].append(l1key)

        # guest leaf line (2 MB region table); always read on an IOTLB miss
        acc += self._s2_table(self.LEAF_TBL | (vpn >> 9), ctx, cs, fills)
        acc += 1                                   # read guest leaf line (coalesced)

        # final data-GPA G-stage translation (coalesced)
        acc += self._s2_data(data_page, ctx, cs, fills, c)

        line = (vpn // c) * c
        fills["iotlb"] = [(line, ctx)]             # combined IOTLB line entry (covers c pages)

        miss_type = "full_cold" if miss_levels >= 2 else ("pwc_partial" if miss_levels == 1 else "pwc_full_hit")
        return WalkPlan(accesses=acc, total_accesses=acc, miss_type=miss_type, fills=fills)


def make_cost_model(cfg):
    mode = cfg.mode
    if mode == "nested":
        return NestedCost()
    # superpage reduces walk depth: 2M -> 2 levels, 1G -> 1 level
    levels = 3
    if cfg.superpage == "2M":
        levels = 2
    elif cfg.superpage == "1G":
        levels = 1
    return SingleStageCost(levels=levels)
