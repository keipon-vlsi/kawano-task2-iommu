"""Trace generation + event injection + CSV export (swappable).

A trace is a list of ``Request(arrival_cycle, vpn, data_page, ctx, idx)`` plus a
list of injected ``Event(arrival_cycle, kind, payload)`` for invalidation / fault
/ context-switch (design_doc §7). Arrival times are spaced at the wire-rate
inter-arrival in cycles. The trace is exportable to CSV so the exact same
stimulus can drive an RTL testbench (design_doc §12).

  * iova_pattern : sequential / stride(k) / random  -> the IOVA (VPN) stream.
  * data_gpa     : sequential / random              -> the guest data-GPA stream
                   (nested S2 leaf-coalescing sensitivity).
  * context tags : device_id / pasid rotate when context_switch_rate > 0,
                   over n_devices / n_pasids. vmid fixed (single guest) here.
"""
from __future__ import annotations

import csv
import random
from dataclasses import dataclass


@dataclass
class Request:
    arrival: float          # arrival time in cycles
    vpn: int                # IOVA virtual page number
    data_page: int          # guest data-GPA page (final S2 input for nested)
    ctx: tuple              # (device_id, pasid, vmid)
    idx: int


@dataclass
class Event:
    arrival: float
    kind: str               # 'invalidation' | 'fault' | 'context_switch'
    payload: dict


def inter_arrival_cycles(cfg):
    ia_ns = cfg.workload.page_bytes / (cfg.workload.wire_gbs * 1e9) * 1e9
    return ia_ns / cfg.cycle_ns


def _interval(rate):
    """events-per-translation rate -> integer request interval (0 -> never)."""
    if rate is None or rate <= 0:
        return None
    return max(1, round(1.0 / rate))


def generate(cfg):
    """Return (requests, events) for the given Config."""
    w = cfg.workload
    n = w.n_requests
    ia = inter_arrival_cycles(cfg)
    rnd = random.Random(w.seed)

    inval_iv = _interval(w.invalidation.rate)
    fault_iv = _interval(w.fault_rate)
    switch_iv = _interval(w.context_switch_rate)

    dev = pas = 0
    vmid = 0
    requests = []
    events = []
    iova_base = 0
    data_base = 0

    for i in range(n):
        at = i * ia

        # --- context switch ---
        if switch_iv and i > 0 and i % switch_iv == 0:
            dev = (dev + 1) % max(1, w.n_devices)
            pas = (pas + 1) % max(1, w.n_pasids)
            events.append(Event(at, "context_switch", {"device_id": dev, "pasid": pas}))
        ctx = (dev, pas, vmid)

        # --- IOVA pattern ---
        if w.iova_pattern == "random":
            vpn = rnd.randrange(w.span_pages)
        elif w.iova_pattern == "stride":
            vpn = iova_base + i * max(1, w.stride)
        else:                                       # sequential
            vpn = iova_base + i

        # --- data GPA pattern ---
        if w.data_gpa == "random":
            data_page = rnd.randrange(w.span_pages)
        else:                                       # sequential (GPA = IOVA + const)
            data_page = data_base + (vpn - iova_base if w.iova_pattern != "random" else i)

        requests.append(Request(at, vpn, data_page, ctx, i))

        # --- injected invalidation ---
        if inval_iv and i > 0 and i % inval_iv == 0:
            events.append(Event(at, "invalidation",
                                {"target": w.invalidation.target,
                                 "granularity": w.invalidation.granularity,
                                 "ctx": ctx, "vpn": vpn}))
        # --- injected fault ---
        if fault_iv and i > 0 and i % fault_iv == 0:
            events.append(Event(at, "fault", {"ctx": ctx, "vpn": vpn}))

    return requests, events


def export_csv(requests, events, path, cycle_ns=2.5):
    """Write the trace as CSV (RTL-testbench stimulus). One row per request and
    per event, time-ordered, with both cycle and ns timestamps."""
    rows = []
    for r in requests:
        rows.append((r.arrival, "dma", r.vpn, r.data_page, r.ctx[0], r.ctx[1], r.ctx[2], ""))
    for e in events:
        rows.append((e.arrival, e.kind, e.payload.get("vpn", ""), "",
                     e.payload.get("ctx", (e.payload.get("device_id", ""),))[0]
                     if e.payload.get("ctx") else e.payload.get("device_id", ""),
                     "", "", e.payload.get("target", e.payload.get("granularity", ""))))
    rows.sort(key=lambda x: x[0])
    with open(path, "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["arrival_cycle", "arrival_ns", "kind", "vpn", "data_page",
                      "device_id", "pasid", "vmid", "info"])
        for row in rows:
            at = row[0]
            wtr.writerow([f"{at:.3f}", f"{at * cycle_ns:.3f}", row[1], row[2],
                          row[3], row[4], row[5], row[6], row[7]])
    return path
