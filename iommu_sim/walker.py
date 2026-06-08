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
    the G-stage must translate before the guest PTE can be read. Each G-stage walk
    is a full 3-host-access (S2 root/L1/leaf) walk when uncached. S2-side caches:
      * ``root_gpa``  -- GPA->SPA of the guest root page table (the GPA held in the
        PDT$ entry); hit -> the root G-stage walk is skipped.
      * ``table_gpa`` -- GPA->SPA result for the guest L1/leaf table pages.
      * ``s2_pwc``    -- the G-stage PWC for the DATA-GPA translation (caches its
        S2 root + S2 L1 PTE reads); ``data_gpa`` caches its leaf.
    With ONLY DDT$/PDT$ (all of the above disabled), the PTW after a PDT$ hit is
    the full (3+1)(3+1)-1 = 15-access 2D walk, starting from the root-GPA G-stage
    translation."""

    # distinct GPA-table-page id namespaces (root / L1-table / leaf-table)
    ROOT_TBL = 1 << 40
    L1_TBL = 2 << 40
    LEAF_TBL = 3 << 40

    def cold_depth(self):
        return 15

    # --- G-stage translation cached as a FULL GPA->SPA result (root_gpa/table_gpa) ---
    @staticmethod
    def _result_cached(cache, key, fills, attr):
        """A full-result cache: hit -> 0 accesses; miss -> a full uncached G-stage
        walk (3 host PTE reads) and fill. Disabled cache -> always 3."""
        if not cache.disabled and cache.lookup(key):
            return 0
        if not cache.disabled:
            fills.setdefault(attr, []).append(key)
        return 3

    def _s2_root(self, ctx, cs, fills):
        return self._result_cached(cs.root_gpa, ("root", ctx), fills, "root_gpa")

    def _s2_table(self, table_id, ctx, cs, fills):
        return self._result_cached(cs.table_gpa, ("tbl", table_id, ctx), fills, "table_gpa")

    # --- DATA-GPA G-stage walk: upper (S2 root + L1) in s2_pwc, leaf in data_gpa ---
    def _s2_data(self, data_page, ctx, cs, fills, c):
        dline = (data_page // c) * c
        if not cs.data_gpa.disabled and cs.data_gpa.lookup(("dat", dline, ctx)):
            return 0
        n = 0
        if not cs.s2_pwc.lookup(("s2root", ctx)):                 # S2 root PTE (data PWC)
            n += 1
            fills.setdefault("s2_pwc", []).append(("s2root", ctx))
        if not cs.s2_pwc.lookup(("s2L1", data_page >> 9, ctx)):   # S2 L1 PTE (data PWC)
            n += 1
            fills.setdefault("s2_pwc", []).append(("s2L1", data_page >> 9, ctx))
        n += 1                                                    # S2 data-leaf PTE read
        if not cs.data_gpa.disabled:
            fills.setdefault("data_gpa", []).append(("dat", dline, ctx))
        return n

    def cost(self, vpn, data_page, ctx, sim):
        cs = sim.caches
        c = sim.eff_coalesce
        acc = 0
        miss_levels = 0
        fills = {"iotlb": [], "s1_l2": [], "s1_l1": []}

        # guest L2: translate the root-table GPA (from the PDT$ context) via the
        # G-stage; cached in root_gpa. Then read the guest L2 PTE.
        l2key = ("L2", vpn >> 18, ctx)
        if not cs.s1_l2.lookup(l2key):
            acc += self._s2_root(ctx, cs, fills)
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
