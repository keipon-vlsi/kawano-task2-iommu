"""Event-driven simulator core. All policies are received via injection (swappable).
For 3c/3d: run with unlimited resources and measure peak_walks / peak_buffer,
which equal the required walker count N and buffer size B (empirical Little's law)."""
import heapq
from dataclasses import dataclass, field

@dataclass
class Metrics:
    completed: int = 0
    peak_walks: int = 0
    peak_buffer: int = 0
    walks_started: int = 0          # true misses (number of memory-bound walks)
    mshr_coalesced: int = 0         # piggybacked on an in-flight line
    iotlb_hit: int = 0
    latencies: list = field(default_factory=list)
    first_arrival: float = None
    last_complete: float = 0.0
    def add_lat(self, x): self.latencies.append(x)
    @property
    def avg_lat(self): return sum(self.latencies)/len(self.latencies) if self.latencies else 0
    @property
    def p99_lat(self):
        if not self.latencies: return 0
        s = sorted(self.latencies); return s[min(len(s)-1, int(0.99*len(s)))]

class Simulator:
    def __init__(self, *, workload, iotlb, pwc, prefetcher, memory, cost_model,
                 num_walkers=None, buffer_size=None, hit_latency_ns=2.5):
        self.workload = workload
        self.iotlb = iotlb; self.pwc = pwc
        self.prefetcher = prefetcher; self.memory = memory; self.cost = cost_model
        self.num_walkers = num_walkers       # None = unlimited (for measuring required count)
        self.buffer_size = buffer_size       # None = unlimited
        self.hit_lat = hit_latency_ns
        self.m = Metrics()
        self._q = []; self._seq = 0
        self.active_walks = 0; self.buffer = 0
        self.mshr = {}                       # leaf_line -> completion time
        self.walk_wait = []; self.buf_wait = []

    def _push(self, t, kind, payload):
        heapq.heappush(self._q, (t, self._seq, kind, payload)); self._seq += 1

    def run(self):
        for at, vpn in self.workload:
            self._push(at, 'arrival', vpn)
        while self._q:
            t, _, kind, p = heapq.heappop(self._q)
            getattr(self, '_on_' + kind)(t, p)
        return self.m

    # --- demand arrival ---
    def _on_arrival(self, t, vpn):
        if self.m.first_arrival is None: self.m.first_arrival = t
        if self.buffer_size is not None and self.buffer >= self.buffer_size:
            self.buf_wait.append((t, vpn)); return      # back-pressure (stall)
        self._admit(t, vpn)

    def _admit(self, t, vpn):
        self.buffer += 1
        self.m.peak_buffer = max(self.m.peak_buffer, self.buffer)
        # fire prefetch (warms caches; does not consume the demand buffer)
        for pf in self.prefetcher.predict(vpn, t):
            self._translate(t, pf, is_prefetch=True)
        self._translate(t, vpn, is_prefetch=False)

    # --- translation request (shared by demand / prefetch) ---
    def _translate(self, t, vpn, is_prefetch):
        if self.iotlb.lookup(vpn):
            if not is_prefetch:
                self.m.iotlb_hit += 1
                self._push(t + self.hit_lat, 'complete', (t, vpn))
            return
        line = (vpn // self.cost.c) * self.cost.c
        if line in self.mshr:                 # piggyback on an in-flight line
            comp = max(self.mshr[line], t)
            if not is_prefetch:
                self.m.mshr_coalesced += 1
                self._push(comp, 'complete', (t, vpn))
            return
        self._start_walk(t, vpn, line, is_prefetch)

    def _start_walk(self, t, vpn, line, is_prefetch):
        if self.num_walkers is not None and self.active_walks >= self.num_walkers:
            self.walk_wait.append((t, vpn, line, is_prefetch)); return
        plan = self.cost.cost(vpn, self)      # compute cost against current PWC state
        self.memory.issue(plan.accesses)
        self.active_walks += 1
        self.m.walks_started += 1
        self.m.peak_walks = max(self.m.peak_walks, self.active_walks)
        comp = t + plan.accesses * self.memory.latency
        self.mshr[line] = comp
        self._push(comp, 'walk_done', (t, vpn, line, plan, is_prefetch))

    def _on_walk_done(self, t, p):
        t0, vpn, line, plan, is_prefetch = p
        self.active_walks -= 1
        self.memory.retire(plan.accesses)
        for k in plan.iotlb_keys: self.iotlb.insert(k)
        for k in plan.pwc_keys: self.pwc.insert(k)
        self.mshr.pop(line, None)
        if not is_prefetch:
            self._push(t, 'complete', (t0, vpn))
        if self.walk_wait:                    # start a queued walk
            wt, wv, wl, wpf = self.walk_wait.pop(0)
            self._start_walk(t, wv, wl, wpf)

    def _on_complete(self, t, p):
        t0, vpn = p
        self.m.completed += 1
        self.m.add_lat(t - t0)
        self.m.last_complete = max(self.m.last_complete, t)
        self.buffer -= 1
        if self.buf_wait:                     # buffer slot freed -> admit a waiting arrival
            at, wv = self.buf_wait.pop(0); self._admit(t, wv)