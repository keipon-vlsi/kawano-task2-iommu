"""Trace generation (swappable component). Each item is (arrival_time_ns, vpn).
Switch between sequential / random / multi-stream for sensitivity analysis."""
import random

# default inter-arrival assumes 100 GB/s wire and 4 KB pages(4096 / 100e9 = 40.96ns).
def wire_inter_arrival_ns(wire_gbs=100.0, page_kb=4):
    page_bytes = page_kb * 1024
    return page_bytes / (wire_gbs * 1e9) * 1e9   # ns

# e.g., [(0, 0), (40.96, 1), (81.92, 2), ...] for sequential with 100 GB/s wire and 4 KB pages.
def sequential(n, wire_gbs=100.0, page_kb=4, base_vpn=0):
    ia = wire_inter_arrival_ns(wire_gbs, page_kb)
    return [(i * ia, base_vpn + i) for i in range(n)]

def random_trace(n, span_pages=1_000_000, wire_gbs=100.0, page_kb=4, seed=0):
    ia = wire_inter_arrival_ns(wire_gbs, page_kb); r = random.Random(seed)
    return [(i * ia, r.randrange(span_pages)) for i in range(n)]

def multi_stream(n, streams=4, stride_pages=1, wire_gbs=100.0, page_kb=4):
    """Interleave several sequential streams (locality exists but not monotonic)."""
    ia = wire_inter_arrival_ns(wire_gbs, page_kb)
    bases = [s * 0x40000 for s in range(streams)]; cnt = [0]*streams
    out = []
    for i in range(n):
        s = i % streams
        out.append((i * ia, bases[s] + cnt[s]*stride_pages)); cnt[s] += 1
    return out