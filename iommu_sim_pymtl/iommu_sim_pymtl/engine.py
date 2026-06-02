"""IOMMUEngine — the PyMTL3 cycle-level core.

Structure
=========
A single PyMTL3 Component with one @update_ff block. Each tick advances the
cycle counter by 1 and runs `_tick_cycle(c)`, which executes the per-cycle
datapath in the fixed order

    arrivals[c] -> completions(walks)[c] -> completions(hits)[c]
                -> launch queued walks   -> admit waiting demand

This is the "datapath" — fixed, cycle-driven, with explicit back-pressure.
Every *policy* used inside the datapath (IOTLB lookup, PWC lookup, walk-cost
model, prefetcher, replacement) is plugged in via a small ABC and held as a
plain Python attribute on the Component. To change behaviour you swap the
policy object; you do not edit the engine.

Why CL and not RTL? At 800 GbE the queueing & cache interactions dominate
sizing decisions. RTL would not change the architectural answers but would
slow exploration ~100x. Each component is structured so it can later be
refined toward RTL (replace `WalkerPool` Python list with PyMTL3
queues/delay pipes; replace cache dict lookups with priority-encoder logic;
etc.) without disturbing the engine's per-cycle scheduling.

State machines / queues
=======================
* `pending_arrivals`        : sorted list of (cycle, vpn) demand requests.
* `buffer`                  : in-flight DMA requests, sized by cfg.buffer_size.
* `buf_wait`                : queued arrivals that hit a full buffer.
* `mshr`                    : leaf-line -> {comp_cycle, waiters_demand,
                                            waiters_prefetch, vpn-list...}.
                              Coalesces in-flight fetches to the same line.
* `walker_pool`             : list of currently active walks; len() bounded
                              by cfg.num_walkers (None => unbounded).
* `walk_wait`               : walks delayed because all walker slots busy.
* `completions[c]`          : list of completion callbacks for cycle c
                              (demand-translation completes or hit-paths).

Metrics
=======
The engine writes Metrics live. `peak_walks` and `peak_buffer` are the
two architectural sizing answers (required walker count N, required
transaction-buffer depth B); we measure them by running with unlimited
resources, exactly as the reference does.
"""
from __future__ import annotations
from collections import defaultdict
from typing import List, Tuple, Optional, Dict

from pymtl3 import Component, OutPort, Wire, mk_bits, update_ff, update

from .metrics import Metrics


# 64-bit cycle counter is plenty for any realistic exploration run.
Bits64 = mk_bits(64)
Bits1 = mk_bits(1)


class IOMMUEngine(Component):
    """Top-level cycle-level engine.

    Wiring is done by the harness: construct() is parameter-free (PyMTL3
    requires construct args be hashable, and we want to pass live Python
    objects), so the harness fills these fields after instantiation and
    before elaborate(). All fields below MUST be set before elaborate()."""

    # -- harness-injected (set BEFORE elaborate()) --
    iotlb = None              # SetAssocCache
    pwc = None                # SetAssocCache
    prefetcher = None         # Prefetcher
    memory = None             # MemoryModel
    cost_model = None         # WalkCostModel
    workload: List[Tuple[int, int]] = None
    num_walkers: Optional[int] = None
    buffer_size: Optional[int] = None
    hit_latency_cycles: int = 1
    mem_latency_cycles: int = 40
    max_cycles: int = 10_000_000
    m: Metrics = None

    # ----------------------------- PyMTL3 -----------------------------
    def construct(s):
        # Observable signals for any external monitor / waveform dump.
        s.cycle_out = OutPort(Bits64)
        s.done_out = OutPort(Bits1)

        # Internal "registers" (one per cycle):
        s.cycle = Wire(Bits64)
        s.done_reg = Wire(Bits1)

        # Empty defaults so sim_reset()'s implicit tick does not crash before
        # the harness calls reset_state(). The `_running` flag gates the tick
        # body until the workload has been installed.
        s._running = False
        s._arrivals = []
        s._arr_idx = 0
        s._buffer_count = 0
        s._buf_wait = []
        s._mshr = {}
        s._walker_pool = []
        s._walk_wait = []
        s._completions = defaultdict(list)
        s._target_completed = 0

        @update_ff
        def tick():
            # Hold during reset / before harness initialisation.
            if not s._running:
                return
            # Step state by one cycle. We use the Python-side `_tick_cycle`
            # for the actual work because the bookkeeping is naturally
            # represented as Python data structures (sorted queues, dicts,
            # variable-length lists). The PyMTL3 update_ff guarantees this
            # body runs exactly once per simulator tick after reset.
            c = int(s.cycle) + 1
            s.cycle <<= Bits64(c)
            if not int(s.done_reg):
                if s._tick_cycle(c):
                    s.done_reg <<= Bits1(1)

        @update
        def expose():
            s.cycle_out @= s.cycle
            s.done_out @= s.done_reg

    # ------------------------- Python state ---------------------------
    def reset_state(s):
        """Re-initialise the pure-Python simulator state. Call this once,
        AFTER apply()+sim_reset() and BEFORE the first tick. Cannot live
        inside construct() because it relies on the harness-injected
        objects (workload, caches, ...)."""
        s._arrivals = list(s.workload)                          # (cycle, vpn)
        s._arrivals.sort(key=lambda x: x[0])
        s._arr_idx = 0                                          # consumed pointer
        s._buffer_count = 0
        s._buf_wait: List[Tuple[int, int]] = []                 # queued demand
        s._mshr: Dict[int, dict] = {}                           # line -> info
        s._walker_pool: List[dict] = []
        s._walk_wait: List[dict] = []
        s._completions: Dict[int, list] = defaultdict(list)     # cycle -> events
        s.m.first_arrival_cycle = (s._arrivals[0][0]
                                   if s._arrivals else None)
        s._target_completed = len(s._arrivals)
        s._running = True

    # -------------------------- per-cycle -----------------------------
    def _tick_cycle(s, c: int) -> bool:
        """Run the datapath for cycle `c`. Returns True when the simulation
        has nothing more to do (all demand completed AND no in-flight work)."""
        # safety stop
        if c > s.max_cycles:
            return True

        # 1) Fire any completions scheduled for this cycle.
        if c in s._completions:
            for ev in s._completions.pop(c):
                ev(c)

        # 2) Admit arrivals whose arrival_cycle <= c (do not allow look-ahead).
        while (s._arr_idx < len(s._arrivals)
               and s._arrivals[s._arr_idx][0] <= c):
            at, vpn = s._arrivals[s._arr_idx]
            s._arr_idx += 1
            s._on_arrival(c, at, vpn)

        # 3) Drain queued walks if walker slots are free.
        s._drain_walk_wait(c)

        # 4) Drain buffer-waiters if buffer slots are free.
        s._drain_buf_wait(c)

        # Done when every workload entry has been delivered and all in-flight
        # transactions have completed.
        if (s._arr_idx >= len(s._arrivals)
                and s.m.completed >= s._target_completed
                and not s._walker_pool
                and not s._mshr
                and not s._completions
                and not s._walk_wait
                and not s._buf_wait):
            s.m.sim_cycles = c
            return True
        return False

    # --------------------------- arrival ------------------------------
    def _on_arrival(s, c: int, at: int, vpn: int) -> None:
        if s.buffer_size is not None and s._buffer_count >= s.buffer_size:
            # back-pressure: hold the request until a buffer slot opens.
            s._buf_wait.append((at, vpn))
            return
        s._admit(c, at, vpn)

    def _admit(s, c: int, at: int, vpn: int) -> None:
        s._buffer_count += 1
        if s._buffer_count > s.m.peak_buffer:
            s.m.peak_buffer = s._buffer_count

        # Prefetcher fires alongside the demand request. Prefetches share the
        # walker pool & memory but do NOT take buffer slots and do NOT log
        # latency.
        for pf_vpn in s.prefetcher.predict(vpn, c):
            s._translate(c, at, pf_vpn, is_prefetch=True)
        s._translate(c, at, vpn, is_prefetch=False)

    # ----------------------- translation path -------------------------
    def _translate(s, c: int, at: int, vpn: int, is_prefetch: bool) -> None:
        if s.iotlb.lookup(vpn):
            if not is_prefetch:
                s.m.iotlb_hit += 1
                comp_cycle = c + s.hit_latency_cycles
                s._completions[comp_cycle].append(
                    lambda t, _at=at, _vpn=vpn: s._on_complete(t, _at, _vpn))
            return

        line = (vpn // s.cost_model.c) * s.cost_model.c
        if line in s._mshr:
            # Piggy-back on an in-flight leaf-line fetch (MSHR coalescing).
            info = s._mshr[line]
            comp = max(info["comp_cycle"], c)
            if not is_prefetch:
                s.m.mshr_coalesced += 1
                s._completions[comp].append(
                    lambda t, _at=at, _vpn=vpn: s._on_complete(t, _at, _vpn))
            # else: prefetch already-in-flight is harmless / free.
            return

        # No coalescing — start a new walk (subject to walker availability).
        s._start_walk(c, at, vpn, line, is_prefetch)

    # --------------------------- walker -------------------------------
    def _start_walk(s, c: int, at: int, vpn: int,
                    line: int, is_prefetch: bool) -> None:
        if (s.num_walkers is not None
                and len(s._walker_pool) >= s.num_walkers):
            # No walker context free — park the walk.
            s._walk_wait.append({"at": at, "vpn": vpn, "line": line,
                                 "is_prefetch": is_prefetch})
            return

        # Memory back-pressure (optional cap on outstanding memory accesses).
        plan = s.cost_model.cost(vpn, s.pwc)
        if not s.memory.can_issue(plan.accesses):
            s._walk_wait.append({"at": at, "vpn": vpn, "line": line,
                                 "is_prefetch": is_prefetch})
            return

        s.memory.issue(plan.accesses)
        ctx = {"at": at, "vpn": vpn, "line": line, "is_prefetch": is_prefetch,
               "plan": plan}
        s._walker_pool.append(ctx)
        s.m.walks_started += 1
        if len(s._walker_pool) > s.m.peak_walks:
            s.m.peak_walks = len(s._walker_pool)

        comp_cycle = c + plan.accesses * s.mem_latency_cycles
        s._mshr[line] = {"comp_cycle": comp_cycle}

        def _on_walk_done(t, _ctx=ctx, _line=line):
            s._finish_walk(t, _ctx, _line)
        s._completions[comp_cycle].append(_on_walk_done)

    def _finish_walk(s, c: int, ctx: dict, line: int) -> None:
        s._walker_pool.remove(ctx)
        s.memory.retire(ctx["plan"].accesses)
        # Install cache entries earned by this walk.
        for k in ctx["plan"].iotlb_keys:
            s.iotlb.insert(k)
        for k in ctx["plan"].pwc_keys:
            s.pwc.insert(k)
        s._mshr.pop(line, None)
        # The demand request paired with this walk completes the same cycle
        # the leaf fetch comes back (no extra hit cycle on the walk path).
        if not ctx["is_prefetch"]:
            s._on_complete(c, ctx["at"], ctx["vpn"])

    def _drain_walk_wait(s, c: int) -> None:
        # Repeatedly attempt to launch queued walks until we either run out of
        # candidates or hit a resource limit (walker pool full / memory full).
        progressed = True
        while progressed and s._walk_wait:
            progressed = False
            if (s.num_walkers is not None
                    and len(s._walker_pool) >= s.num_walkers):
                break
            w = s._walk_wait[0]
            # Re-check MSHR — the line may have been started by another vpn
            # while this walk was waiting (rare but possible under finite N).
            if w["line"] in s._mshr:
                s._walk_wait.pop(0)
                info = s._mshr[w["line"]]
                comp = max(info["comp_cycle"], c)
                if not w["is_prefetch"]:
                    s.m.mshr_coalesced += 1
                    s._completions[comp].append(
                        lambda t, _at=w["at"], _vpn=w["vpn"]:
                            s._on_complete(t, _at, _vpn))
                progressed = True
                continue
            # Try to start the walk now; _start_walk re-queues if memory full.
            saved_len = len(s._walk_wait)
            s._walk_wait.pop(0)
            s._start_walk(c, w["at"], w["vpn"], w["line"], w["is_prefetch"])
            # If _start_walk re-queued the same walk (memory full), bail.
            if len(s._walk_wait) >= saved_len:
                break
            progressed = True

    # ------------------------- completion -----------------------------
    def _on_complete(s, c: int, at: int, vpn: int) -> None:
        s.m.completed += 1
        s.m.add_lat(c - at)
        if c > s.m.last_complete_cycle:
            s.m.last_complete_cycle = c
        s._buffer_count -= 1
        # Buffer slot freed -> we will admit a waiter in this same tick's
        # drain step (see _drain_buf_wait, called at the end of _tick_cycle).

    def _drain_buf_wait(s, c: int) -> None:
        while s._buf_wait and (
                s.buffer_size is None
                or s._buffer_count < s.buffer_size):
            at, vpn = s._buf_wait.pop(0)
            s._admit(c, at, vpn)
