"""Cache structures and replacement policies (swappable components).
To add a new replacement policy, subclass ReplacementPolicy.
To add a new cache structure, subclass CacheABC."""
from abc import ABC, abstractmethod
import random

# ---- Replacement policy (swap point) ----
class ReplacementPolicy(ABC):
    @abstractmethod
    def touch(self, s, key): ...
    @abstractmethod
    def victim(self, s, keys): ...
    def remove(self, s, key): ...

class LRU(ReplacementPolicy):
    def __init__(self): self.o = {}
    def touch(self, s, key):
        l = self.o.setdefault(s, [])
        if key in l: l.remove(key)
        l.append(key)
    def victim(self, s, keys):
        for k in self.o.get(s, []):
            if k in keys: return k
        return next(iter(keys))
    def remove(self, s, key):
        l = self.o.get(s, [])
        if key in l: l.remove(key)

class FIFO(ReplacementPolicy):
    def __init__(self): self.o = {}
    def touch(self, s, key):
        l = self.o.setdefault(s, [])
        if key not in l: l.append(key)
    def victim(self, s, keys):
        for k in self.o.get(s, []):
            if k in keys: return k
        return next(iter(keys))
    def remove(self, s, key):
        l = self.o.get(s, [])
        if key in l: l.remove(key)

class RandomRepl(ReplacementPolicy):
    def touch(self, s, key): pass
    def victim(self, s, keys): return random.choice(list(keys))
    def remove(self, s, key): pass

# ---- Cache structure (swap point) ----
class CacheABC(ABC):
    @abstractmethod
    def lookup(self, key): ...   # with statistics
    @abstractmethod
    def peek(self, key): ...      # without statistics
    @abstractmethod
    def insert(self, key): ...

class SetAssocCache(CacheABC):
    """num_sets x assoc. assoc=None means infinite; assoc=0 disables (always miss)."""
    def __init__(self, num_sets=1, assoc=None, policy=None):
        self.num_sets = max(1, num_sets)
        self.assoc = assoc                              # None=infinite, 0=disabled(always miss), num_sets=num of entries per set
        self.policy = policy or LRU()
        self.sets = [set() for _ in range(self.num_sets)]
        self.hits = 0; self.misses = 0
        self.inserts = 0    # new-key fills (estimator activity counter; does not affect policy)
    def _s(self, key): return hash(key) % self.num_sets
    def peek(self, key):
        return self.assoc != 0 and key in self.sets[self._s(key)]
    def lookup(self, key):
        if self.peek(key):
            self.hits += 1; self.policy.touch(self._s(key), key); return True
        self.misses += 1; return False
    def insert(self, key):
        if self.assoc == 0: return
        s = self._s(key); st = self.sets[s]
        if key in st: self.policy.touch(s, key); return
        if self.assoc is not None and len(st) >= self.assoc:
            v = self.policy.victim(s, st); st.discard(v); self.policy.remove(s, v)  # evict if needed
        st.add(key); self.policy.touch(s, key)
        self.inserts += 1   # count fills for the area/power estimator (activity only)
    @property
    def total(self): return self.hits + self.misses