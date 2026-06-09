"""All simulator metrics (design_doc §8). Times are in cycles; ns is derived with
``cycle_ns`` at report time."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Metrics:
    completed: int = 0
    peak_walks: int = 0                 # 3c: required parallel walkers N
    peak_buffer: int = 0                # 3d: required IOMMU request buffer
    io_bridge_peak: int = 0             # peak in-flight delayed-response (4 kB) holders
    walks_started: int = 0              # true misses (memory-bound walks)
    mshr_coalesced: int = 0             # piggybacked on an in-flight line
    iotlb_hit: int = 0
    pwc_hit: int = 0                    # within-line reuse served by the PWC (no memory)
    faults: int = 0
    context_switches: int = 0
    invalidations: int = 0
    walker_busy_cycles: float = 0.0     # sum(accesses*latency) over walks (estimator)
    arrival_stalls: int = 0             # demand back-pressured by a full request buffer (post-warmup)
    walk_stalls: int = 0                # a line could not start a walk immediately (walker/mem)
    io_bridge_stalls: int = 0           # a demand miss back-pressured by a full I/O-bridge buffer

    latencies: list = field(default_factory=list)          # cycles, per translation
    completion_log: list = field(default_factory=list)     # (complete_cycle, latency_cycles)
    # miss-penalty distribution by type: type -> [count, sum_cycles, max_cycles]
    miss_penalty: dict = field(default_factory=dict)
    # parallelism (active-walk count) distribution: level -> cycles spent at it
    walk_concurrency: dict = field(default_factory=dict)

    first_arrival: float = None
    last_complete: float = 0.0

    # --- recording ---
    def add_latency(self, cycles, miss_type, complete_cycle=None):
        self.latencies.append(cycles)
        if complete_cycle is not None:
            self.completion_log.append((complete_cycle, cycles))
        b = self.miss_penalty.setdefault(miss_type, [0, 0.0, 0.0])
        b[0] += 1
        b[1] += cycles
        b[2] = max(b[2], cycles)

    # --- cold-start / warm-up duration ---
    def time_to_steady(self, k=1.5, tail_frac=0.25):
        """Cycles from first arrival until the cold-start transient settles.

        The latency distribution is bimodal in steady state (most requests hit the
        IOTLB; ~1/8 trigger a walk), so the reference is the steady-state *tail*
        (p99 of the last ``tail_frac`` by time), NOT the median: a request is a
        cold-start (back-logged) request only if its latency exceeds ``k`` x that
        steady tail. Warm-up ends at the last such completion.
        Returns (warmup_end_cycle [rel. first arrival], warmup_requests,
        steady_typical_latency_cyc [median])."""
        log = self.completion_log
        if not log:
            return 0.0, 0, 0.0
        t0 = self.first_arrival or 0.0
        last = self.last_complete or max(t for t, _ in log)
        cutoff = last - tail_frac * (last - t0)
        tail = sorted(lat for t, lat in log if t >= cutoff)
        if not tail:
            tail = sorted(lat for _, lat in log)
        steady_med = tail[len(tail) // 2]
        steady_p99 = tail[min(len(tail) - 1, int(0.99 * len(tail)))]
        thr = k * steady_p99
        elevated_t = [t for t, lat in log if lat > thr]
        if not elevated_t:
            return 0.0, 0, steady_med
        end = max(elevated_t)
        warmup_reqs = sum(1 for t, _ in log if t <= end)
        return end - t0, warmup_reqs, steady_med

    # --- latency stats (cycles) ---
    @property
    def avg_lat(self):
        return sum(self.latencies) / len(self.latencies) if self.latencies else 0.0

    @property
    def max_lat(self):
        return max(self.latencies) if self.latencies else 0.0

    @property
    def p99_lat(self):
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        return s[min(len(s) - 1, int(0.99 * len(s)))]

    @property
    def sim_cycles(self):
        span = self.last_complete - (self.first_arrival or 0.0)
        return span if span > 0 else (self.last_complete or 1.0)

    def throughput_mps(self, cycle_ns):
        """Completed translations per second (M/s)."""
        if self.sim_cycles <= 0:
            return 0.0
        sim_s = self.sim_cycles * cycle_ns * 1e-9
        return self.completed / sim_s / 1e6

    def concurrency_distribution(self):
        """Time-weighted active-walk distribution: list of (level, cycles, frac),
        ascending by level. Level 0 (idle) is included."""
        total = sum(self.walk_concurrency.values())
        if total <= 0:
            return []
        return [(lvl, cyc, cyc / total) for lvl, cyc in sorted(self.walk_concurrency.items())]

    def miss_penalty_table(self, cycle_ns):
        """Return rows (type, count, avg_cycles, avg_ns, max_cycles)."""
        order = ["iotlb_hit", "mshr_coalesced", "pwc_full_hit", "pwc_partial", "full_cold"]
        rows = []
        for t in order:
            if t in self.miss_penalty:
                cnt, tot, mx = self.miss_penalty[t]
                avg = tot / cnt if cnt else 0.0
                rows.append((t, cnt, avg, avg * cycle_ns, mx))
        # any custom types not in the canonical order
        for t, (cnt, tot, mx) in self.miss_penalty.items():
            if t not in order:
                avg = tot / cnt if cnt else 0.0
                rows.append((t, cnt, avg, avg * cycle_ns, mx))
        return rows
