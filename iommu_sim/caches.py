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
        if self.peek(key):
            self.hits += 1
            if not isinstance(self.policy, FIFO):    # FIFO keeps insertion order
                s = self._set(key)
                s.move_to_end(key)
            return True
        self.misses += 1
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
class CacheSet:
    """All translation/context caches for one IOMMU configuration. The walk cost
    models query these; the engine applies fills/invalidations to them; the
    estimator reads their sizes and activity counters."""

    def __init__(self, cfg):
        c = cfg.caches
        self.iotlb = make_cache(c.iotlb, "iotlb")
        # S1 PWC has per-level structures (L2 upper, L1 deeper-and-hotter).
        # enabled=False (or entries<=0) -> disabled (always miss), honoured per level.
        self.s1_l2 = make_level_cache(c.s1_pwc.l2, "s1_pwc")
        self.s1_l1 = make_level_cache(c.s1_pwc.l1, "s1_pwc")
        # G-stage upper PWC holds the S2 root + S2 L1 PTE results (register-like
        # once warm). Disabling it makes every G-stage walk pay the full 3 host
        # accesses -> a DDT$/PDT$-only config costs the full 15-access 2D walk.
        self.s2_pwc = make_cache(c.s2_pwc, "s2_pwc")
        self.table_gpa = make_cache(c.table_gpa, "table_gpa")
        self.data_gpa = make_cache(c.data_gpa, "data_gpa")     # disabled unless enabled=True
        self.ddtc = make_cache(c.ddtc, "ddtc")
        self.pdtc = make_cache(c.pdtc, "pdtc")
        self.msi = make_cache(c.msi, "msi")
        self.cfg = cfg

    def named(self):
        """component-name -> cache (for invalidation targeting & estimator)."""
        return {
            "iotlb": self.iotlb,
            "s1_pwc": self.s1_l1,        # representative; both levels share the name
            "s1_l2": self.s1_l2,
            "s1_l1": self.s1_l1,
            "s2_pwc": self.s2_pwc,
            "table_gpa": self.table_gpa,
            "data_gpa": self.data_gpa,
            "ddtc": self.ddtc,
            "pdtc": self.pdtc,
            "msi": self.msi,
        }

    def invalidate_stage(self, stage, ctx=None, page=None):
        """stage in {s1, s2, both}. S1 -> guest-side caches (IOTLB combined result,
        S1 PWC). S2 -> G-stage caches (S2 PWC, table_gpa, data_gpa). The combined
        IOTLB holds the final IOVA->SPA so it is invalidated by either stage."""
        if stage in ("s1", "both"):
            self.iotlb.invalidate(ctx=ctx, page=page)
            self.s1_l2.invalidate(ctx=ctx, page=page)
            self.s1_l1.invalidate(ctx=ctx, page=page)
            if stage == "s1":
                # S1-only invalidation: combined IOTLB also loses the entry, but a
                # separate data_gpa cache (if enabled) retains the S2 result.
                if not self.data_gpa.disabled:
                    pass                 # data_gpa survives (stage separation benefit)
        if stage in ("s2", "both"):
            self.iotlb.invalidate(ctx=ctx, page=page)
            self.s2_pwc.invalidate(ctx=ctx, page=page)
            self.table_gpa.invalidate(ctx=ctx, page=page)
            self.data_gpa.invalidate(ctx=ctx, page=page)
