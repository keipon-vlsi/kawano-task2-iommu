"""Prefetch algorithms (swappable component).
predict(vpn, now) returns a list of page numbers to prefetch ahead."""
from abc import ABC, abstractmethod

class Prefetcher(ABC):
    @abstractmethod
    def predict(self, vpn, now): ...

class NoPrefetch(Prefetcher):
    def predict(self, vpn, now): return []

class NextLinePrefetch(Prefetcher):
    """Assumes sequential IOVA. Prefetches line starts up to `distance` pages ahead."""
    def __init__(self, distance=8, coalesce=8):
        self.d = distance; self.c = coalesce; self.frontier = -1
    def predict(self, vpn, now):
        target = vpn + self.d               # prefetch up to distance ahead
        out = []
        start = max(self.frontier + 1, vpn) # only prefetch beyond the current frontier (avoid duplicates)
        for p in range(start, target + 1):
            if p % self.c == 0:             # trigger only at line boundaries
                out.append(p)
        if target > self.frontier: self.frontier = target
        return out