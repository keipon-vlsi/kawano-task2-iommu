"""Cycle-approximate, event-driven IOMMU simulator core (design_doc §2/§6).

Time is in cycles (1 cycle = ``cycle_ns``); the event queue is keyed by cycle.
Policies (caches, walk cost model, prefetcher, memory) are injected and never
edited here -- a new design option is a new policy + a config field.

Resource model:
  * IOMMU request buffer (``iommu_req_buffer``): all in-flight demand requests.
    None = unlimited -> ``peak_buffer`` is the required size (3d).
  * walkers (``num_walkers``): concurrent memory-bound walks. None = unlimited ->
    ``peak_walks`` is the required N (3c). A walk is a sequential pointer-chase
    holding ONE outstanding memory read at a time.
  * I/O-bridge buffer (``io_bridge_buffer``): in-flight *delayed-response* demand
    requests (missed the IOTLB, hold their 4 kB payload until the translation
    lands). Immediate IOTLB hits pass data through.
  * memory ``max_outstanding``: ceiling on concurrent read chains (caps N).

MSHR coalescing: a leaf line is registered on the FIRST miss (before any walker
is granted), so all requests to that line -- and the whole coalesced 64 B line --
share ONE walk. This is what makes a small walker count sufficient.

For 3c/3d run with the relevant resource = None and read the measured peak.
"""
from __future__ import annotations

import heapq

from caches import CacheSet
from memory import MemoryModel
from walker import make_cost_model, COLD_DEPTH
from prefetch import make_prefetcher
from metrics import Metrics


class _MSHR:
    """One in-flight / pending leaf line. Coalesces all requests for the line."""
    __slots__ = ("line", "lead_vpn", "lead_data", "ctx", "started", "waiters", "origin")

    def __init__(self, line, vpn, data, ctx, origin="demand"):
        self.line = line
        self.lead_vpn = vpn
        self.lead_data = data
        self.ctx = ctx
        self.started = False
        self.waiters = []          # list of (req, is_prefetch)
        self.origin = origin       # "demand" | "prefetch": which first-missed the line


class Simulator:
    def __init__(self, cfg, requests, events=None, warmup_frac=0.0):
        self.cfg = cfg
        self.requests = requests
        self.events = events or []
        last_arrival = requests[-1].arrival if requests else 0.0
        self.warmup_cutoff = warmup_frac * last_arrival   # cold-start excluded for clean 3c/3d

        self.caches = CacheSet(cfg)
        self.memory = MemoryModel(
            latency_cycles=cfg.memory.latency_cycles,
            max_outstanding=cfg.memory.max_outstanding,
            bank_parallel=cfg.memory.bank_parallel,
            coalescing_effective=cfg.memory.coalescing_effective,
        )
        self.cost = make_cost_model(cfg)

        base_c = cfg.caches.coalesce_factor if cfg.memory.coalescing_effective else 1
        if cfg.superpage == "2M":
            base_c = max(base_c, 512)
        elif cfg.superpage == "1G":
            base_c = max(base_c, 512 * 512)
        self.eff_coalesce = max(1, base_c)

        self.prefetcher = make_prefetcher(cfg, self.eff_coalesce)

        t = cfg.timing
        self.lookup_cycles = t.lookup_cycles
        self.arb_cycles = t.arbitration_cycles
        self.hit_latency = t.hit_latency_cycles
        self.pipeline_depth = cfg.walkers.pipeline_depth
        self.parallel_lookup = (cfg.caches.lookup_mode == "parallel")

        self.num_walkers = cfg.walkers.num_walkers
        self.buffer_size = cfg.buffers.iommu_req_buffer
        self.io_bridge_size = cfg.buffers.io_bridge_buffer
        self.context_walk = cfg.caches.context_walk

        self.m = Metrics()
        self._q = []
        self._seq = 0
        self.active_walks = 0
        self.buffer = 0
        self.io_bridge = 0
        self.mshr = {}                 # line -> _MSHR
        self._conc_last_t = 0.0        # last time the active-walk count changed
        self.walk_wait = []            # lines awaiting a walker / memory slot
        self.buf_wait = []             # arrivals awaiting a request-buffer slot
        self.iob_wait = []             # demand misses awaiting an I/O-bridge slot

    # --- event queue ---
    def _push(self, t, kind, payload):
        heapq.heappush(self._q, (t, self._seq, kind, payload))
        self._seq += 1

    def _account_concurrency(self, t):
        """Accumulate elapsed cycles at the current active-walk level (post-warmup),
        then advance the marker. Call just BEFORE active_walks changes."""
        dt = t - self._conc_last_t
        if dt > 0 and t >= self.warmup_cutoff:
            wc = self.m.walk_concurrency
            wc[self.active_walks] = wc.get(self.active_walks, 0.0) + dt
        self._conc_last_t = t

    def line_of(self, vpn):
        return (vpn // self.eff_coalesce) * self.eff_coalesce

    def _rec(self, t):
        return t >= self.warmup_cutoff

    def run(self):
        for r in self.requests:
            self._push(r.arrival, "arrival", r)
        for e in self.events:
            self._push(e.arrival, "event", e)
        while self._q:
            t, _, kind, p = heapq.heappop(self._q)
            getattr(self, "_on_" + kind)(t, p)
        self.m.invalidations = sum(c.invalidations for c in self.caches.named().values())
        return self.m

    # --- injected events ---
    def _on_event(self, t, e):
        if e.kind == "invalidation":
            g = e.payload.get("granularity", "context")
            target = e.payload.get("target", "s1")
            ctx = e.payload.get("ctx")
            if g == "context":
                self.caches.invalidate_stage(target, ctx=ctx)
            elif g == "page":
                line = self.line_of(e.payload.get("vpn"))
                self.caches.invalidate_stage(target, page=lambda k: k and k[0] == line)
            else:
                self.caches.invalidate_stage(target, ctx=None)
        elif e.kind == "fault":
            self.m.faults += 1
        elif e.kind == "context_switch":
            self.m.context_switches += 1

    # --- demand arrival ---
    def _on_arrival(self, t, req):
        if self.m.first_arrival is None:
            self.m.first_arrival = t
        if self.buffer_size is not None and self.buffer >= self.buffer_size:
            self.buf_wait.append(req)
            if self._rec(t):
                self.m.arrival_stalls += 1     # request-buffer back-pressure (stall)
            return
        self._admit(t, req)

    def _admit(self, t, req):
        self.buffer += 1
        if self._rec(t):
            self.m.peak_buffer = max(self.m.peak_buffer, self.buffer)
        for pf_vpn in self.prefetcher.predict(req.vpn, t):
            pf = type(req)(arrival=t, vpn=pf_vpn,
                           data_page=req.data_page + (pf_vpn - req.vpn),
                           ctx=req.ctx, idx=-1)
            self._translate(t, pf, is_prefetch=True)
        self._translate(t, req, is_prefetch=False)

    # --- translation (demand or prefetch) ---
    def _translate(self, t, req, is_prefetch):
        td = t + self.lookup_cycles
        ctx = req.ctx
        line = self.line_of(req.vpn)
        key = (req.vpn, ctx)                           # IOTLB is per-page (one IOVA->SPA)
        self.caches.origin = "prefetch" if is_prefetch else "demand"   # tag this request's lookups

        if self.parallel_lookup:                      # parallel mode probes VM PWC too (energy)
            self.caches.vm_l1.lookup(("vmL1", req.vpn >> 9, ctx))
            self.caches.vm_l2.lookup(("vmL2", req.vpn >> 18, ctx))

        if self.caches.iotlb.peek(key):               # IOTLB hit -> immediate response (no bridge)
            self.caches.iotlb.lookup(key)             # count the hit
            if not is_prefetch:
                self.m.iotlb_hit += 1
                self._push(td + self.hit_latency, "complete", (req, "iotlb_hit"))
            return

        # IOTLB miss -> delayed response, will hold an I/O-bridge (4 kB) slot.
        # Back-pressure NEW delayed responses when the bridge is full (NOT walk-start:
        # gating walk-start would deadlock, since only walk completion drains the bridge).
        if (not is_prefetch and self.io_bridge_size is not None
                and self.io_bridge >= self.io_bridge_size):
            self.iob_wait.append(req)
            if self._rec(t):
                self.m.io_bridge_stalls += 1
            return

        self.caches.iotlb.lookup(key)                 # count the IOTLB miss

        ent = self.mshr.get(line)
        if ent is not None:                           # concurrent within-line -> share the in-flight read
            self._add_waiter(t, ent, req, is_prefetch, coalesced=True)
            return

        # Line not in flight. Within-line REUSE: the leaf line (and data line) may be
        # cached from an earlier walk -> resolve from the PWC with NO memory access and
        # no walker (the leaf PTE was already fetched). This is where VM-L0 hits.
        if self.cost.warm_hit(req.vpn, req.data_page, ctx, self):
            if is_prefetch:
                return                                # already cached -> redundant prefetch
            self.cost.warm_lookup(req.vpn, req.data_page, ctx, self)   # count the PWC hits
            self.caches.iotlb.insert(key)
            self.io_bridge += 1
            if self._rec(t):
                self.m.io_bridge_peak = max(self.m.io_bridge_peak, self.io_bridge)
            self.m.pwc_hit += 1
            self._push(td + self.arb_cycles + self.hit_latency, "complete", (req, "pwc_hit"))
            return

        # first miss for this line: register MSHR, then try to start a walk
        ent = _MSHR(line, req.vpn, req.data_page, ctx,
                    origin="prefetch" if is_prefetch else "demand")
        self.mshr[line] = ent
        self._add_waiter(t, ent, req, is_prefetch, coalesced=False)
        self.walk_wait.append(line)
        self._dispatch_waiting(td)
        if not is_prefetch and not ent.started and self._rec(t):
            self.m.walk_stalls += 1            # walker/memory could not start it now

    def _add_waiter(self, t, ent, req, is_prefetch, coalesced):
        ent.waiters.append((req, is_prefetch))
        if not is_prefetch:
            if coalesced:
                self.m.mshr_coalesced += 1
            self.io_bridge += 1
            if self._rec(t):
                self.m.io_bridge_peak = max(self.m.io_bridge_peak, self.io_bridge)

    # --- start the walk for a pending line, if resources allow ---
    def _start_line(self, t, line):
        ent = self.mshr.get(line)
        if ent is None or ent.started:
            return False
        if self.num_walkers is not None and self.active_walks >= self.num_walkers:
            return False
        if not self.memory.can_issue():
            return False

        self.caches.origin = ent.origin               # tag the walk's PWC/G/context lookups
        plan = self.cost.cost(ent.lead_vpn, ent.lead_data, ent.ctx, self)
        plan_acc = plan.accesses
        total_acc = plan.total_accesses
        # context resolution (DDTW/PDTW), shared by the coalesced line (same context).
        if self.context_walk:
            ctx_acc = self.cost.context_accesses(ent.ctx, self)   # real directory walks
            plan_acc += ctx_acc                       # on the critical path (precede the PTW)
            total_acc += ctx_acc                      # and count as memory bandwidth
        else:
            # legacy: +1 latency per DDTC/PDTC cold miss (not counted as bandwidth).
            if not self.caches.ddtc.disabled and not self.caches.ddtc.lookup(("dev", ent.ctx[0])):
                plan_acc += 1
                self.caches.ddtc.insert(("dev", ent.ctx[0]))
            if not self.caches.pdtc.disabled and not self.caches.pdtc.lookup(("pas", ent.ctx[1])):
                plan_acc += 1
                self.caches.pdtc.insert(("pas", ent.ctx[1]))

        self.memory.enter()
        self.memory.account(total_acc)
        self._account_concurrency(t)
        self.active_walks += 1
        self.m.walks_started += 1
        self.m.walker_busy_cycles += plan_acc * self.memory.latency
        if self._rec(t):
            self.m.peak_walks = max(self.m.peak_walks, self.active_walks)

        walk_cycles = self.arb_cycles + self.pipeline_depth + plan_acc * self.memory.access_cycles()
        ent.started = True
        self._push(t + walk_cycles, "walk_done", (line, plan))
        return True

    def _dispatch_waiting(self, t):
        i = 0
        while i < len(self.walk_wait):
            line = self.walk_wait[i]
            if self.num_walkers is not None and self.active_walks >= self.num_walkers:
                break
            if not self.memory.can_issue():
                break
            if self._start_line(t, line):
                self.walk_wait.pop(i)
            else:
                i += 1

    def _on_walk_done(self, t, payload):
        line, plan = payload
        ent = self.mshr.pop(line, None)
        self._account_concurrency(t)
        self.active_walks -= 1
        self.memory.exit()
        for attr, keys in plan.fills.items():
            cache = getattr(self.caches, attr, None)
            if cache is not None:
                for k in keys:
                    cache.insert(k)
        if ent is not None:
            # Coalesced IOTLB fill: one walk resolves c per-page IOVA->SPA entries
            # (both leaf reads return c PTEs) -> the rest of the line hits the IOTLB.
            for p in plan.iotlb_pages:
                self.caches.iotlb.insert((p, ent.ctx))
            first_demand = True
            for req, is_pf in ent.waiters:
                if is_pf:
                    continue
                mt = plan.miss_type if first_demand else "mshr_coalesced"
                first_demand = False
                self._push(t, "complete", (req, mt))
        self._dispatch_waiting(t)

    def _on_complete(self, t, payload):
        req, miss_type = payload
        self.m.completed += 1
        self.m.add_latency(t - req.arrival, miss_type, complete_cycle=t)
        self.m.last_complete = max(self.m.last_complete, t)
        self.buffer -= 1
        if miss_type != "iotlb_hit":
            self.io_bridge -= 1
            # an I/O-bridge slot freed -> retry a back-pressured demand miss
            if self.iob_wait and (self.io_bridge_size is None or self.io_bridge < self.io_bridge_size):
                self._translate(t, self.iob_wait.pop(0), is_prefetch=False)
        if self.buf_wait:
            self._admit(t, self.buf_wait.pop(0))
        self._dispatch_waiting(t)

    def cold_depth(self):
        return COLD_DEPTH.get(self.cfg.mode, 3)
