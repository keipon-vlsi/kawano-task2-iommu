"""Prefetch policies (swappable). ``predict(vpn, now)`` returns a list of page
numbers to prefetch ahead (line-aligned). All learned prefetchers are
confidence-throttled so they *self-disable* on random/non-monotonic streams
(design_premises §10/§17): a bet that costs nothing when wrong.

Implemented: off / next_line / stride / rpt / dcpt / sms.
  * next_line : assume +1-page stride, prefetch line starts up to `distance`.
  * stride    : detect the dominant stride; prefetch only once confident.
  * rpt       : reference-prediction-table style, per-context stride + state.
  * dcpt      : distance-prefetching style (delta between consecutive lines).
  * sms       : spatial-memory-streaming style, prefetch the rest of the region.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Prefetcher(ABC):
    name = "abstract"

    @abstractmethod
    def predict(self, vpn, now): ...


class NoPrefetch(Prefetcher):
    name = "off"

    def predict(self, vpn, now):
        return []


class _FrontierMixin:
    """Emit line-aligned pages between the frontier and the target, never twice."""

    def _emit(self, vpn, target, coalesce, frontier):
        out = []
        start = max(frontier + 1, vpn)
        for p in range(start, target + 1):
            if coalesce <= 1 or p % coalesce == 0:
                out.append(p)
        return out


class NextLinePrefetch(Prefetcher, _FrontierMixin):
    name = "next_line"

    def __init__(self, distance=16, coalesce=8):
        self.d = distance
        self.c = coalesce
        self.frontier = -1

    def predict(self, vpn, now):
        target = vpn + self.d
        out = self._emit(vpn, target, self.c, self.frontier)
        if target > self.frontier:
            self.frontier = target
        return out


class StridePrefetch(Prefetcher, _FrontierMixin):
    """Detect a stable stride; prefetch ahead only once confidence >= threshold."""
    name = "stride"

    def __init__(self, distance=16, coalesce=8, confidence=2):
        self.d = distance
        self.c = coalesce
        self.thr = confidence
        self.last = None
        self.stride = None
        self.conf = 0
        self.frontier = -1

    def _update(self, vpn):
        if self.last is not None:
            s = vpn - self.last
            if s == self.stride:
                self.conf = min(self.conf + 1, self.thr + 4)
            else:
                self.stride = s
                self.conf = 0
        self.last = vpn

    def predict(self, vpn, now):
        self._update(vpn)
        if self.conf < self.thr or not self.stride or self.stride <= 0:
            return []                              # not confident -> self-disabled
        target = vpn + self.stride * self.d
        out = self._emit(vpn, target, self.c, self.frontier)
        if target > self.frontier:
            self.frontier = target
        return out


class RPTPrefetch(StridePrefetch):
    """Reference-prediction-table style: same stride machine, slightly stickier
    confidence (state machine: init/transient/steady)."""
    name = "rpt"

    def _update(self, vpn):
        if self.last is not None:
            s = vpn - self.last
            if s == self.stride:
                self.conf = min(self.conf + 1, self.thr + 8)
            elif self.conf > 0:
                self.conf -= 1                     # transient: decay, don't reset
            else:
                self.stride = s
        self.last = vpn


class DCPTPrefetch(StridePrefetch):
    """Distance-prefetching style: keys on the delta between successive lines and
    replays the learned delta. Modelled as a line-granular stride machine."""
    name = "dcpt"

    def predict(self, vpn, now):
        line = (vpn // self.c) * self.c
        self._update(line)
        if self.conf < self.thr or not self.stride or self.stride <= 0:
            return []
        target = line + self.stride * self.d
        out = self._emit(line, target, self.c, self.frontier)
        if target > self.frontier:
            self.frontier = target
        return out


class SMSPrefetch(StridePrefetch):
    """Spatial-memory-streaming style: once confident, prefetch the remaining
    lines of the current spatial region (here: the next `distance` lines)."""
    name = "sms"

    def predict(self, vpn, now):
        self._update(vpn)
        if self.conf < self.thr:
            return []
        region = ((vpn // self.c) + 1) * self.c
        target = region + self.d
        out = self._emit(vpn, target, self.c, self.frontier)
        if target > self.frontier:
            self.frontier = target
        return out


_PREFETCHERS = {
    "off": NoPrefetch,
    "next_line": NextLinePrefetch,
    "stride": StridePrefetch,
    "rpt": RPTPrefetch,
    "dcpt": DCPTPrefetch,
    "sms": SMSPrefetch,
}


def make_prefetcher(cfg, coalesce):
    algo = cfg.prefetch.algo
    if algo == "off":
        return NoPrefetch()
    cls = _PREFETCHERS.get(algo, NoPrefetch)
    if cls is NoPrefetch:
        return NoPrefetch()
    if cls is NextLinePrefetch:
        return cls(distance=cfg.prefetch.distance, coalesce=coalesce)
    return cls(distance=cfg.prefetch.distance, coalesce=coalesce,
               confidence=cfg.prefetch.confidence)
