"""Single-run CLI (usage_manual §2/§4).

    python3 run.py --config configs/baseline.yaml
    python3 run.py --config configs/baseline.yaml --measure peaks   # 3c/3d (infinite res)
    python3 run.py --config configs/baseline.yaml --emit-trace out/trace.csv

Prints every metric in design_doc §8: throughput & wire_rate_met, peak_walks (3c),
peak_buffer (3d), memory peak-outstanding & bandwidth, I/O-bridge peak, per-cache
hit/miss, accesses/translation, latency avg/max/p99 (cycles & ns), miss-penalty by
type, and per-module normalized area/power + energy/translation. Also writes a
frozen-prediction JSON (config hash + normalized PPA)."""
from __future__ import annotations

import argparse
import os
import re

from config import Config
from runner import run_sim, summarize
from workload import generate, export_csv

FREEZE_DIR = os.path.join(os.path.dirname(__file__), "freeze")


def _slug(name):
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower()[:48]


def print_report(cfg, sim, m, s):
    cyc = cfg.cycle_ns
    print(f"\n=== {cfg.name}  (mode={cfg.mode}, superpage={cfg.superpage}, "
          f"lookup={cfg.caches.lookup_mode}, prefetch={cfg.prefetch.algo}) ===")
    print(f"  clock {cfg.timing.clock_mhz:.0f} MHz, 1 cycle = {cyc:.3g} ns, "
          f"mem = {cfg.memory.latency_cycles} cyc, inter-arrival = {cfg.inter_arrival_cycles:.2f} cyc")
    print("\n-- throughput / wire rate --")
    print(f"  completed              : {m.completed}")
    print(f"  throughput             : {s['throughput_mps']:.3f} M/s   (target {s['target_mps']:.3f} M/s)")
    print(f"  wire_rate_met          : {s['wire_rate_met']}")
    print("\n-- required hardware (3c / 3d) --")
    print(f"  peak_walks (3c, N)     : {m.peak_walks}"
          + ("   [num_walkers fixed]" if cfg.walkers.num_walkers is not None else "   [measured @ unlimited]"))
    print(f"  peak_buffer (3d)       : {m.peak_buffer}"
          + ("   [iommu_req_buffer fixed]" if cfg.buffers.iommu_req_buffer is not None else "   [measured @ unlimited]"))
    print("\n-- memory / I/O-bridge performance requirements --")
    print(f"  mem_outstanding_peak   : {s['mem_outstanding_peak']}")
    print(f"  mem_bandwidth          : {s['mem_bandwidth_gbs']:.2f} GB/s")
    print(f"  mem_accesses           : {s['mem_accesses']}  ({s['accesses_per_translation']:.4f} /translation)")
    print(f"  io_bridge_buffer_peak  : {m.io_bridge_peak}   (4 kB payload holders)")
    print("\n-- cache hit/miss --")
    print(f"  {'cache':<11}{'hits':>10}{'misses':>10}{'hit_rate':>10}")
    for name, (h, mi, hr) in s["hit"].items():
        if h + mi > 0:
            print(f"  {name:<11}{h:>10d}{mi:>10d}{hr:>10.3f}")
    print(f"  iotlb_hit={m.iotlb_hit}  mshr_coalesced={m.mshr_coalesced}  walks={m.walks_started}")
    print("\n-- latency --")
    print(f"  avg : {m.avg_lat:8.2f} cyc  ({m.avg_lat*cyc:8.2f} ns)")
    print(f"  p99 : {m.p99_lat:8.2f} cyc  ({m.p99_lat*cyc:8.2f} ns)")
    print(f"  max : {m.max_lat:8.2f} cyc  ({m.max_lat*cyc:8.2f} ns)")
    wu_cyc, wu_reqs, steady_lat = m.time_to_steady()
    print("\n-- cold-start / warm-up --")
    print(f"  time to steady-state   : {wu_cyc:.0f} cyc  ({wu_cyc*cyc:.0f} ns),  {wu_reqs} requests")
    print(f"  (steady-state latency ~ {steady_lat:.1f} cyc; warm-up = caches filling + initial backlog draining.")
    print(f"   peaks above are cold-start-INCLUSIVE when resources are unlimited -> the HW needed to")
    print(f"   sustain wire rate FROM cold start. `--measure peaks` reports the steady-state requirement.)")
    print("\n-- miss-penalty distribution by type (cycles) --")
    print(f"  {'type':<16}{'count':>8}{'avg_cyc':>10}{'avg_ns':>10}{'max_cyc':>10}")
    for t, cnt, avg, avgns, mx in m.miss_penalty_table(cyc):
        print(f"  {t:<16}{cnt:>8d}{avg:>10.2f}{avgns:>10.2f}{mx:>10.2f}")
    print(f"  (characteristic full-cold walk depth for mode {cfg.mode}: {sim.cold_depth()} x {cfg.memory.latency_cycles} cyc)")
    if m.faults or m.context_switches or m.invalidations:
        print(f"\n  events: faults={m.faults}  context_switches={m.context_switches}  invalidations={m.invalidations}")
    print("\n-- normalized area & power (per module) --")
    print(s["_result"].table())
    print(f"\n  FoM (area_GE x energy/translation): {s['fom_area_x_energy']:.2f}")


def main():
    ap = argparse.ArgumentParser(description="IOMMU exploration simulator -- single run")
    ap.add_argument("--config", required=True)
    ap.add_argument("--measure", choices=["peaks"], default=None,
                    help="peaks: force unlimited resources + cold-start warmup -> clean 3c/3d")
    ap.add_argument("--warmup", type=float, default=0.0, help="warmup fraction for peak measurement")
    ap.add_argument("--emit-trace", default=None, help="write the trace CSV (RTL testbench stimulus)")
    ap.add_argument("--freeze", default=None, help="path for the frozen-prediction JSON")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    warmup = args.warmup
    if args.measure == "peaks":
        cfg.walkers.num_walkers = None
        cfg.buffers.iommu_req_buffer = None
        cfg.buffers.io_bridge_buffer = None
        cfg.memory.max_outstanding = None
        warmup = max(warmup, 0.05)

    sim, m = run_sim(cfg, warmup_frac=warmup)
    s = summarize(cfg, sim, m)
    print_report(cfg, sim, m, s)

    if args.measure == "peaks":
        print(f"\n  >>> 3c required parallel walkers N (steady) = {m.peak_walks}")
        print(f"  >>> 3d required IOMMU request buffer (steady) = {m.peak_buffer}")
        # cold-start-inclusive requirement: same infinite-resource run, warmup=0
        cold_cfg = Config.load(args.config)
        cold_cfg.walkers.num_walkers = None
        cold_cfg.buffers.iommu_req_buffer = None
        cold_cfg.buffers.io_bridge_buffer = None
        cold_cfg.memory.max_outstanding = None
        csim, cm = run_sim(cold_cfg, warmup_frac=0.0)
        wu_cyc, wu_reqs, _ = cm.time_to_steady()
        print("\n  -- required HW: steady-state vs cold-start-inclusive --")
        print(f"  {'resource':<22}{'steady':>9}{'cold-incl':>11}")
        print(f"  {'num_walkers (N)':<22}{m.peak_walks:>9}{cm.peak_walks:>11}")
        print(f"  {'iommu_req_buffer':<22}{m.peak_buffer:>9}{cm.peak_buffer:>11}")
        print(f"  {'io_bridge_buffer':<22}{m.io_bridge_peak:>9}{cm.io_bridge_peak:>11}")
        print(f"  {'mem_outstanding':<22}{sim.memory.peak_outstanding:>9}{csim.memory.peak_outstanding:>11}")
        print(f"  warm-up to steady state: {wu_cyc:.0f} cyc ({wu_cyc*cfg.cycle_ns:.0f} ns), {wu_reqs} requests")
        print(f"  (provision the COLD-INCL column to meet wire rate from cold start with zero stalls;")
        print(f"   the steady column is the running requirement once warm.)")

    os.makedirs(FREEZE_DIR, exist_ok=True)
    fpath = args.freeze or os.path.join(FREEZE_DIR, f"{_slug(cfg.name)}.json")
    rec = s["_result"].freeze(fpath)
    print(f"\n  frozen prediction -> {os.path.relpath(fpath)}  (config_hash {rec['config_hash'][:12]}...)")

    if args.emit_trace:
        requests, events = generate(cfg)
        os.makedirs(os.path.dirname(os.path.abspath(args.emit_trace)), exist_ok=True)
        export_csv(requests, events, args.emit_trace, cfg.cycle_ns)
        print(f"  trace CSV -> {args.emit_trace}  ({len(requests)} requests, {len(events)} events)")


if __name__ == "__main__":
    main()
