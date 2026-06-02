"""Set-associative caches and replacement policies.

Add a new replacement policy = subclass ReplacementPolicy and register it in
`make_policy`. Add a new cache structure = subclass CacheABC.
Semantics match the reference simulator exactly:
  * assoc=None  -> infinite (every insert succeeds, no eviction)
  * assoc=0     -> disabled (lookup is always a miss, insert is a no-op)
  * assoc>0     -> set-associative
"""
from __future__ import annotations
from abc import ABC, abstractmethod
import random
from typing import Optional, Set, Any


# ------------------------- replacement policies -------------------------

class ReplacementPolicy(ABC):
    @abstractmethod
    def touch(self, s: int, key: Any) -> None: ...
    @abstractmethod
    def victim(self, s: int, keys: Set[Any]) -> Any: ...
    def remove(self, s: int, key: Any) -> None: ...


class LRU(ReplacementPolicy):
    """Most-recently-used at the tail; victim is the oldest in the set."""
    def __init__(self):
        self.o: dict[int, list] = {}
    def touch(self, s, key):
        l = self.o.setdefault(s, [])
        if key in l:
            l.remove(key)
        l.append(key)
    def victim(self, s, keys):
        for k in self.o.get(s, []):
            if k in keys:
                return k
        return next(iter(keys))
    def remove(self, s, key):
        l = self.o.get(s, [])
        if key in l:
            l.remove(key)


class FIFO(ReplacementPolicy):
    """First inserted is first evicted; access does not update order."""
    def __init__(self):
        self.o: dict[int, list] = {}
    def touch(self, s, key):
        l = self.o.setdefault(s, [])
        if key not in l:
            l.append(key)
    def victim(self, s, keys):
        for k in self.o.get(s, []):
            if k in keys:
                return k
        return next(iter(keys))
    def remove(self, s, key):
        l = self.o.get(s, [])
        if key in l:
            l.remove(key)


class RandomRepl(ReplacementPolicy):
    def __init__(self, seed: int = 0):
        self.r = random.Random(seed)
    def touch(self, s, key):
        pass
    def victim(self, s, keys):
        return self.r.choice(list(keys))
    def remove(self, s, key):
        pass


def make_policy(name: str) -> ReplacementPolicy:
    name = name.lower()
    if name == "lru":
        return LRU()
    if name == "fifo":
        return FIFO()
    if name == "random":
        return RandomRepl()
    raise ValueError(f"unknown replacement policy: {name}")


# --------------------------- cache structures ---------------------------

class CacheABC(ABC):
    @abstractmethod
    def lookup(self, key) -> bool: ...   # counts hits/misses
    @abstractmethod
    def peek(self, key) -> bool: ...     # silent (no statistics)
    @abstractmethod
    def insert(self, key) -> None: ...


class SetAssocCache(CacheABC):
    """num_sets x assoc with pluggable replacement.

    Cache statistics are kept here (hits / misses); the engine reads them
    directly when reporting metrics. Sets are stored as Python sets and the
    replacement policy holds an ordering dict, exactly as in the reference."""
    def __init__(self, num_sets: int = 1, assoc: Optional[int] = None,
                 policy: Optional[ReplacementPolicy] = None):
        self.num_sets = max(1, num_sets)
        self.assoc = assoc
        self.policy = policy or LRU()
        self.sets: list[set] = [set() for _ in range(self.num_sets)]
        self.hits = 0
        self.misses = 0

    def _s(self, key) -> int:
        return hash(key) % self.num_sets

    def peek(self, key) -> bool:
        if self.assoc == 0:
            return False
        return key in self.sets[self._s(key)]

    def lookup(self, key) -> bool:
        if self.peek(key):
            self.hits += 1
            self.policy.touch(self._s(key), key)
            return True
        self.misses += 1
        return False

    def insert(self, key) -> None:
        if self.assoc == 0:
            return
        s = self._s(key)
        st = self.sets[s]
        if key in st:
            self.policy.touch(s, key)
            return
        if self.assoc is not None and len(st) >= self.assoc:
            v = self.policy.victim(s, st)
            st.discard(v)
            self.policy.remove(s, v)
        st.add(key)
        self.policy.touch(s, key)

    @property
    def total(self) -> int:
        return self.hits + self.misses
