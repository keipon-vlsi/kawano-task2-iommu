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
    iotlb_pages: list = field(default_factory=list)  # IOVA pages whose full IOVA->SPA
    #   this walk resolves -> filled into the (per-page) IOTLB. Both leaf reads are
    #   64 B = c PTEs, so when the guest-leaf line AND the data-leaf line both cover
    #   the c-page line (contiguous IOVA + GPA=IOVA+const), all c are resolved at once.


# structural worst-case cold-walk depth per mode (design_doc §6, CLAUDE.md).
# Reported as the full_cold miss-penalty characteristic; the dynamic first walk
# is usually cheaper because the G-stage root is a register.
COLD_DEPTH = {"bare": 0, "s1_only": 3, "s2_only": 3, "nested": 15}


class WalkCostModel(ABC):
    ddt_levels = 3                                      # device directory depth (host)
    pdt_levels = 3                                      # process directory depth (guest)

    @abstractmethod
    def cost(self, vpn, data_page, ctx, sim) -> WalkPlan: ...

    def cold_depth(self):
        return 3

    def context_accesses(self, ctx, sim):
        """DDTW + PDTW host memory accesses to resolve the (device, process) context,
        gated by DDTC/PDTC. A DISABLED directory cache walks on EVERY request (not just
        cold). DDTW = ddt_levels host accesses; PDTW per ``_pdtw_accesses``."""
        cs = sim.caches
        acc = 0
        if cs.ddtc.disabled or not cs.ddtc.lookup(("dev", ctx[0])):
            acc += self.ddt_levels                     # device directory walk (host)
            if not cs.ddtc.disabled:
                cs.ddtc.insert(("dev", ctx[0]))
        if cs.pdtc.disabled or not cs.pdtc.lookup(("pas", ctx[1])):
            acc += self._pdtw_accesses(ctx, sim)
            if not cs.pdtc.disabled:
                cs.pdtc.insert(("pas", ctx[1]))
        return acc

    def _pdtw_accesses(self, ctx, sim):
        return self.pdt_levels                         # single stage: guest PDT reads only

    def warm_hit(self, vpn, data_page, ctx, sim):
        """True iff the translation needs NO memory access -- fully resolvable from
        the PWC (the guest-leaf line, and for nested the data line, are cached). This
        is the within-line *reuse* case: an IOTLB miss whose leaf line was already
        fetched. Side-effect-free (peek only)."""
        return False

    def warm_lookup(self, vpn, data_page, ctx, sim):
        """Count the PWC hits for a ``warm_hit`` page (called on the fast path)."""


# --------------------------------------------------------------------------
class PassthroughCost(WalkCostModel):
    """RISC-V *Bare* mode: no address translation (IOVA = SPA). No page-table walk
    and nothing to cache; the device/process context is still resolved (DDTW/PDTW
    via ``context_accesses``) to learn that the context is Bare. So a request's only
    possible memory cost is the context walk -- with DDTC/PDTC warm it is ~free."""

    def cold_depth(self):
        return 0

    def cost(self, vpn, data_page, ctx, sim):
        return WalkPlan(accesses=0, total_accesses=0, miss_type="passthrough")


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
        fills = {}
        line = (vpn // c) * c
        vm_l0_key = ("vmL0", line, ctx)
        l1key = ("vmL1", vpn >> 9, ctx)
        l2key = ("vmL2", vpn >> 18, ctx)

        # Deepest-first PWC probe. VM-L0 caches the leaf PTE line (1 read = c PTEs);
        # a hit -> the SPA is known with no memory access (within-line reuse).
        if cs.vm_l0.lookup(vm_l0_key):
            pass
        else:
            if self.levels >= 2 and cs.vm_l1.lookup(l1key):
                pass                               # leaf table located
            elif self.levels >= 3 and cs.vm_l2.lookup(l2key):
                acc += 1                           # read VM-L1 PTE
                miss_levels += 1
                fills.setdefault("vm_l1", []).append(l1key)
            else:                                  # cold from the root
                if self.levels >= 3:
                    acc += 1                       # read VM-L2 PTE
                    miss_levels += 1
                    fills.setdefault("vm_l2", []).append(l2key)
                if self.levels >= 2:
                    acc += 1                       # read VM-L1 PTE
                    miss_levels += 1
                    fills.setdefault("vm_l1", []).append(l1key)
            acc += 1                               # leaf line read (coalesced -> fills VM-L0)
            fills.setdefault("vm_l0", []).append(vm_l0_key)

        # single stage: the one leaf line read resolves the SPA for all c pages.
        iotlb_pages = [line + i for i in range(c)]
        miss_type = self._classify(miss_levels)
        return WalkPlan(accesses=acc, total_accesses=acc, miss_type=miss_type,
                        fills=fills, iotlb_pages=iotlb_pages)

    def warm_hit(self, vpn, data_page, ctx, sim):
        c = sim.eff_coalesce
        return sim.caches.vm_l0.peek(("vmL0", (vpn // c) * c, ctx))

    def warm_lookup(self, vpn, data_page, ctx, sim):
        c = sim.eff_coalesce
        sim.caches.vm_l0.lookup(("vmL0", (vpn // c) * c, ctx))

    @staticmethod
    def _classify(miss_levels):
        if miss_levels == 0:
            return "pwc_full_hit"
        if miss_levels >= 2:
            return "full_cold"
        return "pwc_partial"

    def cold_depth(self):
        return self.levels


# flat cache-attribute names for each G-stage hierarchy (l0, l1, l2 order)
_GA_VML2 = ("g_l0_vml2", "g_l1_vml2", "g_l2_vml2")   # translate VM-root pointer
_GA_VML1 = ("g_l0_vml1", "g_l1_vml1", "g_l2_vml1")   # translate VM-L2 PTE ppn
_GA_VML0 = ("g_l0_vml0", "g_l1_vml0", "g_l2_vml0")   # translate VM-L1 PTE ppn
_GA_FINAL = ("gf_l0", "gf_l1", "gf_l2")              # translate the final data GPA


# --------------------------------------------------------------------------
class NestedCost(WalkCostModel):
    """Sv39 (VS / VM-stage) + Sv39x4 (G-stage). Every guest PTE pointer is a GPA
    that the G-stage must translate (its own 3-level walk) before the guest PTE can
    be read. Caches, per the user taxonomy:
      * VM-L0/L1/L2 PWC + VM-root  -- guest-side, keyed by IOVA.
      * G-Lx@VM-Ly (g_pwc)         -- G-stage PWC for the guest-TABLE-page GPAs,
        physically separated per VM level. L0 = full GPA->SPA result.
      * G-final-L0/L1/L2           -- G-stage PWC for the final DATA GPA.
      * IOTLB                      -- G-final-L0 made directly VPN-hittable.
    With everything but DDT$/PDT$ disabled, a walk is the full (3+1)(3+1)-1 = 15
    -access 2D walk."""

    def __init__(self, data_contiguous=True):
        # GPA = IOVA + const: the c data GPAs of a line are contiguous, so the one
        # data-leaf read resolves the SPA for all c pages -> the IOTLB coalesces to c.
        # Random data GPA: only the requested page's SPA is resolved -> 1 entry.
        self.data_contiguous = data_contiguous
        self.g_levels = 3                              # G-stage (Sv39x4) depth
        self._pdt_gwarm = set()                        # ctxs whose PDT G-stage is warm

    def cold_depth(self):
        return 15

    def _pdtw_accesses(self, ctx, sim):
        # PDT lives in GUEST memory: each of pdt_levels guest reads needs a G-stage
        # translation. The G-stage tables warm only if a G-stage PWC exists to hold
        # them (and only after the first walk per context); with the G-stage PWC
        # disabled they are cold on every walk.
        g_on = not sim.caches.g_l0_vml2.disabled       # representative G-stage cache
        if g_on and ctx in self._pdt_gwarm:
            return self.pdt_levels                     # G-stage warm: guest PDT reads only
        if g_on:
            self._pdt_gwarm.add(ctx)
        # cold: pdt_levels guest reads, each preceded by a g_levels G-stage walk.
        # Unlike the PTW there is NO final data-GPA translation -- the PDT leaf's GPA
        # result is translated on the PTW side. So N(G+1), not (N+1)(G+1)-1.
        return self.pdt_levels * (self.g_levels + 1)

    @staticmethod
    def _gwalk(gcaches, keybase, ctx, fills, attrs):
        """Deepest-first G-stage (Sv39x4) walk translating ONE GPA. ``gcaches`` and
        ``attrs`` are (L0, L1, L2). L0 holds the full GPA->SPA result: hit -> 0 host
        accesses. Returns the host PTE reads on the critical path and schedules
        fills for every level walked (from the shared G-root register)."""
        g0, g1, g2 = gcaches
        a0, a1, a2 = attrs
        k0 = ("g0", keybase, ctx)
        if g0.lookup(k0):
            return 0
        k1 = ("g1", keybase >> 9, ctx)
        k2 = ("g2", keybase >> 18, ctx)
        if g1.lookup(k1):
            n = 1                                  # read G-L0 PTE
        elif g2.lookup(k2):
            n = 2                                  # read G-L1, G-L0 PTE
            fills.setdefault(a1, []).append(k1)
        else:
            n = 3                                  # read G-L2, G-L1, G-L0 PTE (from G-root)
            fills.setdefault(a2, []).append(k2)
            fills.setdefault(a1, []).append(k1)
        fills.setdefault(a0, []).append(k0)
        return n

    def cost(self, vpn, data_page, ctx, sim):
        cs = sim.caches
        c = sim.eff_coalesce
        acc = 0
        miss_levels = 0
        fills = {}
        line = (vpn // c) * c
        vm_l0_key = ("vmL0", line, ctx)
        vm_l1_key = ("vmL1", vpn >> 9, ctx)
        vm_l2_key = ("vmL2", vpn >> 18, ctx)
        gw = self._gwalk

        # Deepest-first VM probe. Each guest PTE we must READ first pays the G-stage
        # translation of its (GPA) address via the matching G-Lx@VM-Ly hierarchy.
        if cs.vm_l0.lookup(vm_l0_key):
            pass                                   # guest leaf PTE cached (rare for streaming)
        elif cs.vm_l1.lookup(vm_l1_key):
            acc += gw(cs.gstage("vml0"), vpn >> 9, ctx, fills, _GA_VML0)   # leaf-table GPA
            acc += 1                               # read guest leaf PTE (VM-L0)
            fills.setdefault("vm_l0", []).append(vm_l0_key)
        elif cs.vm_l2.lookup(vm_l2_key):
            acc += gw(cs.gstage("vml1"), vpn >> 18, ctx, fills, _GA_VML1)  # L1-table GPA
            acc += 1                               # read guest L1 PTE (VM-L1)
            fills.setdefault("vm_l1", []).append(vm_l1_key)
            acc += gw(cs.gstage("vml0"), vpn >> 9, ctx, fills, _GA_VML0)   # leaf-table GPA
            acc += 1                               # read guest leaf PTE (VM-L0)
            fills.setdefault("vm_l0", []).append(vm_l0_key)
            miss_levels += 1
        else:                                      # cold from VM-root (register)
            acc += gw(cs.gstage("vml2"), 0, ctx, fills, _GA_VML2)          # root-table GPA
            acc += 1                               # read guest L2 PTE (VM-L2)
            fills.setdefault("vm_l2", []).append(vm_l2_key)
            acc += gw(cs.gstage("vml1"), vpn >> 18, ctx, fills, _GA_VML1)
            acc += 1                               # read guest L1 PTE (VM-L1)
            fills.setdefault("vm_l1", []).append(vm_l1_key)
            acc += gw(cs.gstage("vml0"), vpn >> 9, ctx, fills, _GA_VML0)
            acc += 1                               # read guest leaf PTE (VM-L0)
            fills.setdefault("vm_l0", []).append(vm_l0_key)
            miss_levels += 2

        # final data-GPA G-stage walk (G-final-L0 = the coalesced data leaf line)
        acc += gw((cs.gf_l0, cs.gf_l1, cs.gf_l2), (data_page // c) * c, ctx, fills, _GA_FINAL)

        # Coalesced IOTLB fill: both leaf reads (guest-leaf line + data-leaf line)
        # resolve all c pages of the line when the data GPA is contiguous; otherwise
        # only the requested page's full IOVA->SPA is known.
        iotlb_pages = [line + i for i in range(c)] if self.data_contiguous else [vpn]
        miss_type = "full_cold" if miss_levels >= 2 else ("pwc_partial" if miss_levels == 1 else "pwc_full_hit")
        return WalkPlan(accesses=acc, total_accesses=acc, miss_type=miss_type,
                        fills=fills, iotlb_pages=iotlb_pages)

    def warm_hit(self, vpn, data_page, ctx, sim):
        c = sim.eff_coalesce
        cs = sim.caches
        # leaf guest PTE cached (-> data GPA known) AND data GPA->SPA cached -> no memory.
        return (cs.vm_l0.peek(("vmL0", (vpn // c) * c, ctx))
                and cs.gf_l0.peek(("g0", (data_page // c) * c, ctx)))

    def warm_lookup(self, vpn, data_page, ctx, sim):
        c = sim.eff_coalesce
        cs = sim.caches
        cs.vm_l0.lookup(("vmL0", (vpn // c) * c, ctx))
        cs.gf_l0.lookup(("g0", (data_page // c) * c, ctx))


def make_cost_model(cfg):
    mode = cfg.mode
    data_contiguous = (cfg.workload.data_gpa == "sequential")
    if mode == "bare":
        m = PassthroughCost()                          # RISC-V Bare: no translation
    elif mode == "nested":
        m = NestedCost(data_contiguous=data_contiguous)
    else:                                              # s1_only / s2_only: single-stage walk
        # superpage reduces walk depth: 2M -> 2 levels, 1G -> 1 level
        levels = 3
        if cfg.superpage == "2M":
            levels = 2
        elif cfg.superpage == "1G":
            levels = 1
        m = SingleStageCost(levels=levels)
    m.ddt_levels = cfg.caches.ddt_levels
    m.pdt_levels = cfg.caches.pdt_levels
    return m
