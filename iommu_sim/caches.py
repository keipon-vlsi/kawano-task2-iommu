"""Cache structures, replacement, generation-based invalidation, and the
named RISC-V IOMMU cache set (design_doc §5, design_premises §15).

Swap points:
  * subclass ``ReplacementPolicy`` for a new replacement order,
  * subclass ``CacheABC`` for a new cache structure,
  * ``CacheSet`` assembles all named caches from a ``CachesCfg``.

Associativity convention (config -> structure):
  * ``"full"`` -> fully associative (1 set, ``entries`` ways) == CAM in RTL.
  * ``1``      -> direct mapped (``entries`` sets, 1 way).
  * ``N``      -> N-way set associative (``entries/N`` sets).
  * ``entries <= 0`` -> disabled (always miss); used to turn a cache off.

Context tags: every translation-cache key is a tuple whose LAST element is the
context tag ``(device_id, pasid, vmid)``. This lets a context switch be handled
without a flush, and lets selective invalidation target a context in O(1) via a
per-context generation counter (RTL intent; modelled here with generation
stamps so a hit is only valid while its stamp matches the live generation).
"""
from __future__ import annotations

import random
from abc import ABC, abstractmethod
from collections import OrderedDict, defaultdict


# ---- Replacement policy (swap point) --------------------------------------
class ReplacementPolicy(ABC):
    @abstractmethod
    def order(self, items):
        """Given the current per-set OrderedDict, return the victim key."""

    name = "abstract"


class LRU(ReplacementPolicy):
    name = "lru"

    def order(self, items):           # OrderedDict, oldest first
        return next(iter(items))


class FIFO(ReplacementPolicy):
    name = "fifo"

    def order(self, items):
        return next(iter(items))


class RandomRepl(ReplacementPolicy):
    name = "random"

    def __init__(self, seed=0):
        self._r = random.Random(seed)

    def order(self, items):
        return self._r.choice(list(items.keys()))


_POLICIES = {"lru": LRU, "fifo": FIFO, "random": RandomRepl}


def make_policy(name):
    return _POLICIES.get(name, LRU)()


def ctx_of(key):
    """Context tag = last element of the key tuple (or None for untagged keys)."""
    if isinstance(key, tuple) and key and isinstance(key[-1], tuple):
        return key[-1]
    return None


# ---- Cache structure (swap point) -----------------------------------------
class CacheABC(ABC):
    @abstractmethod
    def lookup(self, key): ...     # with statistics

    @abstractmethod
    def peek(self, key): ...       # without statistics

    @abstractmethod
    def insert(self, key): ...


class SetAssocCache(CacheABC):
    """Set-associative / fully-associative / direct cache with generation-based
    invalidation. ``policy`` only matters for assoc > 1 (a fully-associative
    streaming cache has no useful replacement order; structural separation is
    what protects hot upper entries -- design_premises §10)."""

    def __init__(self, entries, assoc="full", policy=None, name=""):
        self.name = name
        self.entries = int(entries)
        self.assoc = assoc
        self.disabled = self.entries <= 0
        if assoc == "full":
            self.num_sets, self.ways = 1, max(1, self.entries)
        elif assoc in (1, "1", "direct"):
            self.num_sets, self.ways = max(1, self.entries), 1
        else:
            self.ways = max(1, int(assoc))
            self.num_sets = max(1, self.entries // self.ways)
        self.is_cam = (assoc == "full")
        self.policy = policy or LRU()
        # each set: OrderedDict key -> generation stamp (insertion order = LRU/FIFO)
        self.sets = [OrderedDict() for _ in range(self.num_sets)]
        # generation counters (invalidation): flush bumps global; selective bumps a ctx
        self.flush_gen = 0
        self.ctx_gen = defaultdict(int)
        # statistics
        self.hits = 0
        self.misses = 0
        self.inserts = 0
        self.invalidations = 0
        # demand vs prefetch split (origin read from the owning CacheSet at lookup)
        self.dem_hits = 0
        self.dem_misses = 0
        self.pf_hits = 0
        self.pf_misses = 0
        self.owner = None

    # --- internal ---
    def _set(self, key):
        return self.sets[hash(key) % self.num_sets]

    def _stamp(self, key):
        return (self.flush_gen, self.ctx_gen[ctx_of(key)])

    # --- API ---
    def peek(self, key):
        if self.disabled:
            return False
        s = self._set(key)
        return key in s and s[key] == self._stamp(key)

    def lookup(self, key):
        pf = self.owner is not None and self.owner.origin == "prefetch"
        if self.peek(key):
            self.hits += 1
            if pf:
                self.pf_hits += 1
            else:
                self.dem_hits += 1
            if not isinstance(self.policy, FIFO):    # FIFO keeps insertion order
                s = self._set(key)
                s.move_to_end(key)
            return True
        self.misses += 1
        if pf:
            self.pf_misses += 1
        else:
            self.dem_misses += 1
        return False

    def insert(self, key):
        if self.disabled:
            return
        s = self._set(key)
        stamp = self._stamp(key)
        if key in s:
            s[key] = stamp
            if not isinstance(self.policy, FIFO):
                s.move_to_end(key)
            return
        if len(s) >= self.ways:
            victim = self.policy.order(s)
            s.pop(victim, None)
        s[key] = stamp
        self.inserts += 1

    # --- invalidation (design_doc §5, design_premises §16) ---
    def invalidate(self, ctx=None, page=None):
        """flush-all (ctx is None and page is None), per-context (ctx given), or
        per-page/range (page predicate given). Generation-based: O(1) for
        flush/context; page granularity filters matching entries."""
        self.invalidations += 1
        if page is not None:
            for s in self.sets:
                for k in [k for k in s if (isinstance(k, tuple) and page(k))]:
                    s.pop(k, None)
            return
        if ctx is None:
            self.flush_gen += 1                # all stamps now stale
        else:
            self.ctx_gen[ctx] += 1             # only this context stale

    @property
    def total(self):
        return self.hits + self.misses

    @property
    def hit_rate(self):
        return self.hits / self.total if self.total else 0.0

    # --- area/power inputs ---
    @property
    def cam_bits_frac(self):
        return 1.0 if self.is_cam else 0.0


class AlwaysHit(CacheABC):
    """Register-backed structure that never misses (e.g. the page-table ROOT in a
    single-context system: invariant -> miss rate zero). Counted as a few FFs."""

    def __init__(self, name="root_reg"):
        self.name = name
        self.hits = 0
        self.misses = 0
        self.inserts = 0
        self.invalidations = 0
        self.disabled = False

    def peek(self, key):
        return True

    def lookup(self, key):
        self.hits += 1
        return True

    def insert(self, key):
        pass


def make_cache(cfg, name):
    """Build a cache from a CacheCfg-like object. enabled=False -> disabled."""
    if cfg is None or not getattr(cfg, "enabled", True):
        return SetAssocCache(0, name=name)     # disabled (always miss)
    pol = LRU() if cfg.assoc != "full" else LRU()
    return SetAssocCache(cfg.entries, cfg.assoc, policy=pol, name=name)


def make_level_cache(lvl, name):
    """Build a per-level PWC cache (PWCLevelCfg). enabled=False -> disabled,
    consistent with make_cache (so `enabled: false` works on s1_pwc levels too)."""
    if lvl is None or not getattr(lvl, "enabled", True):
        return SetAssocCache(0, name=name)
    return SetAssocCache(lvl.entries, lvl.assoc, name=name)


# ---- Named IOMMU cache set ------------------------------------------------
# VM levels whose pointer GPA each per-VM-level G-stage hierarchy translates.
_VM_TAGS = ("vml2", "vml1", "vml0")


class CacheSet:
    """All translation/context caches for one IOMMU configuration (user taxonomy:
    VM-Lx PWC / G-Lx@VM-Ly / G-final-Lx / IOTLB). The walk cost models query these;
    the engine applies fills/invalidations to them by flat attribute name; the
    estimator reads their sizes and activity counters."""

    def __init__(self, cfg):
        c = cfg.caches
        self._all = {}
        self.origin = "demand"        # "demand" | "prefetch": tags each lookup's source

        def reg(name, obj):
            cache = make_level_cache(obj, name)   # honours enabled / entries / assoc
            cache.owner = self                    # so lookup() can read self.origin
            self._all[name] = cache
            setattr(self, name, cache)
            return cache

        # IOTLB: fully-resolved IOVA->SPA (== G-final-L0 made VPN-hittable).
        reg("iotlb", c.iotlb)
        # VM-stage (guest, Sv39) PWC: L0(leaf)/L1/L2 + root register.
        reg("vm_l2", c.vm_pwc.l2)
        reg("vm_l1", c.vm_pwc.l1)
        reg("vm_l0", c.vm_pwc.l0)
        reg("vm_root", c.vm_pwc.root)
        # G-stage PWC for guest-table-page GPAs, separated per VM level (G-Lx@VM-Ly).
        for tag, gs in zip(_VM_TAGS, (c.g_pwc.vm_l2, c.g_pwc.vm_l1, c.g_pwc.vm_l0)):
            reg(f"g_l2_{tag}", gs.l2)
            reg(f"g_l1_{tag}", gs.l1)
            reg(f"g_l0_{tag}", gs.l0)
        reg("g_root", c.g_pwc.root)               # shared G-stage root register
        # G-stage PWC for the final DATA GPA.
        reg("gf_l2", c.g_final.l2)
        reg("gf_l1", c.g_final.l1)
        reg("gf_l0", c.g_final.l0)
        # context / interrupt caches.
        reg("ddtc", c.ddtc)
        reg("pdtc", c.pdtc)
        reg("msi", c.msi)
        self.cfg = cfg

    def get(self, name):
        return self._all.get(name)

    def gstage(self, vm_tag):
        """(L0, L1, L2) G-stage caches translating VM-`vm_tag`'s pointer GPA."""
        return (self._all[f"g_l0_{vm_tag}"], self._all[f"g_l1_{vm_tag}"],
                self._all[f"g_l2_{vm_tag}"])

    def named(self):
        """component-name -> cache (for invalidation targeting & estimator)."""
        return dict(self._all)

    # cache groups for stage-targeted invalidation
    _S1_NAMES = ("iotlb", "vm_l2", "vm_l1", "vm_l0", "vm_root")
    _S2_NAMES = ("iotlb", "gf_l2", "gf_l1", "gf_l0", "g_root",
                 "g_l2_vml2", "g_l1_vml2", "g_l0_vml2",
                 "g_l2_vml1", "g_l1_vml1", "g_l0_vml1",
                 "g_l2_vml0", "g_l1_vml0", "g_l0_vml0")

    def invalidate_stage(self, stage, ctx=None, page=None):
        """stage in {s1, s2, both}. S1 -> guest-side caches (VM PWC + IOTLB).
        S2 -> G-stage caches (G / G-final + IOTLB). The combined IOTLB holds the
        final IOVA->SPA so either stage invalidates it; the separate G-final (data)
        caches survive an S1-only invalidation (stage separation benefit)."""
        names = []
        if stage in ("s1", "both"):
            names += self._S1_NAMES
        if stage in ("s2", "both"):
            names += self._S2_NAMES
        for n in dict.fromkeys(names):            # dedup, keep order
            self._all[n].invalidate(ctx=ctx, page=page)
