"""Trace generators.

Each generator returns a list of (arrival_cycle, vpn) tuples. arrival_cycle
is an integer cycle count; we use a fractional accumulator to quantise the
true inter-arrival (40.96 ns ≈ 16.384 cycles @ 400 MHz) onto integer cycles
without drifting away from the target wire rate.
"""
from __future__ import annotations
from typing import List, Tuple
import random


def wire_inter_arrival_ns(wire_gbs: float = 100.0, page_kb: int = 4) -> float:
    page_bytes = page_kb * 1024
    return page_bytes / (wire_gbs * 1e9) * 1e9


def wire_inter_arrival_cycles(wire_gbs: float = 100.0, page_kb: int = 4,
                              clock_mhz: float = 400.0) -> float:
    return wire_inter_arrival_ns(wire_gbs, page_kb) * (clock_mhz / 1000.0)


def _quantise(n: int, ia_cycles: float) -> List[int]:
    """Convert n equispaced arrivals at fractional rate `ia_cycles` to
    integer cycles via a running accumulator. Eliminates drift."""
    out: List[int] = []
    acc = 0.0
    for _ in range(n):
        out.append(int(acc))
        acc += ia_cycles
    return out


def sequential(n: int, wire_gbs: float = 100.0, page_kb: int = 4,
               clock_mhz: float = 400.0, base_vpn: int = 0
               ) -> List[Tuple[int, int]]:
    ia = wire_inter_arrival_cycles(wire_gbs, page_kb, clock_mhz)
    ts = _quantise(n, ia)
    return [(t, base_vpn + i) for i, t in enumerate(ts)]


def random_trace(n: int, span_pages: int = 1_000_000, wire_gbs: float = 100.0,
                 page_kb: int = 4, clock_mhz: float = 400.0, seed: int = 0
                 ) -> List[Tuple[int, int]]:
    ia = wire_inter_arrival_cycles(wire_gbs, page_kb, clock_mhz)
    ts = _quantise(n, ia)
    r = random.Random(seed)
    return [(t, r.randrange(span_pages)) for t in ts]


def multi_stream(n: int, streams: int = 4, stride_pages: int = 1,
                 wire_gbs: float = 100.0, page_kb: int = 4,
                 clock_mhz: float = 400.0, base_offset: int = 0x40000
                 ) -> List[Tuple[int, int]]:
    """Several sequential streams interleaved round-robin. Locality exists
    but the IOVA stream is no longer monotonic, so per-line coalescing and
    a naive next-line prefetcher both lose some of their value."""
    ia = wire_inter_arrival_cycles(wire_gbs, page_kb, clock_mhz)
    ts = _quantise(n, ia)
    bases = [s * base_offset for s in range(streams)]
    cnt = [0] * streams
    out: List[Tuple[int, int]] = []
    for i, t in enumerate(ts):
        s = i % streams
        out.append((t, bases[s] + cnt[s] * stride_pages))
        cnt[s] += 1
    return out


def make_trace(cfg, clock_mhz: float, wire_gbs: float, page_kb: int):
    kind = cfg.kind.lower()
    if kind == "sequential":
        return sequential(cfg.n, wire_gbs=wire_gbs, page_kb=page_kb,
                          clock_mhz=clock_mhz, base_vpn=cfg.base_vpn)
    if kind == "random":
        return random_trace(cfg.n, span_pages=cfg.span_pages, wire_gbs=wire_gbs,
                            page_kb=page_kb, clock_mhz=clock_mhz, seed=cfg.seed)
    if kind == "multi_stream":
        return multi_stream(cfg.n, streams=cfg.streams,
                            stride_pages=cfg.stride_pages, wire_gbs=wire_gbs,
                            page_kb=page_kb, clock_mhz=clock_mhz)
    raise ValueError(f"unknown trace kind: {cfg.kind}")
