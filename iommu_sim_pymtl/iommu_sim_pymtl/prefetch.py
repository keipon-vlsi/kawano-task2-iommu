"""Prefetcher policies (swappable).

`predict(vpn, cycle)` returns a list of vpns to prefetch. The engine treats
each returned vpn as a free-floating translation request that:
  * goes through the same IOTLB / PWC / walker / memory path,
  * does NOT occupy a transaction-buffer slot,
  * does NOT contribute to demand-latency statistics on completion.

Prefetches warm the caches ahead of demand. They do, however, count toward
peak walker occupancy and toward total memory traffic.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List


class Prefetcher(ABC):
    @abstractmethod
    def predict(self, vpn: int, cycle: int) -> List[int]: ...


class NoPrefetch(Prefetcher):
    def predict(self, vpn, cycle):
        return []


class NextLineStride(Prefetcher):
    """Next-line + N-page-ahead prefetcher tuned for sequential IOVA.

    Only fires at leaf-line boundaries (every `coalesce` pages) so we don't
    issue redundant requests inside a single 64 B PTE line. The frontier
    counter prevents the same page from being prefetched twice."""
    def __init__(self, distance: int = 16, coalesce: int = 8):
        self.d = distance
        self.c = coalesce
        self.frontier = -1

    def predict(self, vpn, cycle):
        target = vpn + self.d
        out: List[int] = []
        start = max(self.frontier + 1, vpn)
        for p in range(start, target + 1):
            if p % self.c == 0:           # only at line starts
                out.append(p)
        if target > self.frontier:
            self.frontier = target
        return out


class ConfidenceStride(Prefetcher):
    """Stride prefetcher with a saturating confidence counter.

    Tracks the delta between consecutive demand vpns. When `threshold`
    consecutive deltas agree the prefetcher is confident; it then issues
    `distance` lines ahead at the observed stride. Any deviation slams
    confidence to zero, so non-sequential / random streams quickly stop
    generating useless traffic (graceful degradation)."""
    def __init__(self, distance: int = 16, threshold: int = 4, coalesce: int = 8):
        self.d = distance
        self.t = threshold
        self.c = coalesce
        self.last_vpn: int | None = None
        self.last_stride: int | None = None
        self.confidence = 0
        self.frontier = -1

    def predict(self, vpn, cycle):
        out: List[int] = []
        if self.last_vpn is not None:
            stride = vpn - self.last_vpn
            if stride == self.last_stride and stride > 0:
                if self.confidence < 1_000_000:
                    self.confidence += 1
            else:
                self.confidence = 0
                self.last_stride = stride
        self.last_vpn = vpn

        if self.confidence >= self.t and self.last_stride and self.last_stride > 0:
            target = vpn + self.last_stride * self.d
            start = max(self.frontier + 1, vpn + self.last_stride)
            p = start
            while p <= target:
                if p % self.c == 0:
                    out.append(p)
                p += self.last_stride
            if target > self.frontier:
                self.frontier = target
        return out


def make_prefetcher(cfg) -> Prefetcher:
    kind = cfg.kind.lower()
    if kind == "none":
        return NoPrefetch()
    if kind == "nextline":
        return NextLineStride(distance=cfg.distance, coalesce=cfg.coalesce)
    if kind == "stride":
        return ConfidenceStride(distance=cfg.distance, threshold=cfg.threshold,
                                coalesce=cfg.coalesce)
    raise ValueError(f"unknown prefetcher: {cfg.kind}")
