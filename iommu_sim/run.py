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


def print_report(cfg, sim, m, s, m_steady=None):
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
    if m_steady is not None:
        # both columns: steady-state (warm) requirement vs cold-start-inclusive
        # (provision the cold-incl column to sustain wire rate from cold start).
        print(f"  {'resource':<24}{'steady':>9}{'cold-incl':>11}")
        print(f"  {'peak_walks (3c, N)':<24}{m_steady.peak_walks:>9}{m.peak_walks:>11}")
        print(f"  {'peak_buffer (3d)':<24}{m_steady.peak_buffer:>9}{m.peak_buffer:>11}")
        print(f"  {'io_bridge_buffer':<24}{m_steady.io_bridge_peak:>9}{m.io_bridge_peak:>11}")
        print(f"  (steady = post-warm-up running requirement; cold-incl = from cold start.")
        print(f"   measured @ unlimited resources.)")
        dist = m_steady.concurrency_distribution()
        if dist:
            print(f"\n  parallel-walk distribution (steady, time-weighted):")
            print(f"  {'active walks':>12}{'cycles':>12}{'fraction':>10}")
            for lvl, lvl_cyc, frac in dist:
                bar = "#" * int(round(frac * 40))
                print(f"  {lvl:>12}{lvl_cyc:>12.0f}{frac:>9.1%}  {bar}")
            print(f"  (zero-stall peak provisions the tail; typical concurrency is the mode above.)")
    else:
        print(f"  peak_walks (3c, N)     : {m.peak_walks}   [num_walkers fixed]")
        print(f"  peak_buffer (3d)       : {m.peak_buffer}   [iommu_req_buffer fixed]")
    print("\n-- memory / I/O-bridge performance requirements --")
    print(f"  mem_outstanding_peak   : {s['mem_outstanding_peak']}")
    print(f"  mem_bandwidth          : {s['mem_bandwidth_gbs']:.2f} GB/s")
    print(f"  mem_accesses           : {s['mem_accesses']}  ({s['accesses_per_translation']:.4f} /translation)")
    print(f"  io_bridge_buffer_peak  : {m.io_bridge_peak}   (4 kB payload holders)")
    print("\n-- cache hit/miss (demand vs prefetch) --")
    print(f"  {'cache':<11}{'d_hit':>9}{'d_miss':>8}{'d_rate':>8}{'pf_hit':>8}{'pf_miss':>8}")
    for name, (dh, dm, dr, ph, pm) in s["hit"].items():
        if dh + dm + ph + pm > 0:
            print(f"  {name:<11}{dh:>9d}{dm:>8d}{dr:>8.3f}{ph:>8d}{pm:>8d}")
    print(f"  iotlb_hit(demand)={m.iotlb_hit}  pwc_hit={m.pwc_hit}  mshr_coalesced={m.mshr_coalesced}  walks={m.walks_started}")
    print(f"  (d_=demand, pf_=prefetch. prefetch misses are intended first-touches that warm the cache;")
    print(f"   d_rate is the demand-facing hit rate -- the number that reflects how well the cache serves traffic.)")

    # G-stage PWC is a 2D structure (G-Lx @ VM-Ly); the flat rows above hide which
    # G-level serves which VM-level. Total (demand+prefetch) so it is prefetch-agnostic.
    def _gcell(name):
        x = sim.caches.get(name)
        if x is None:
            return "-"
        h, mi = x.hits, x.misses
        return f"{h}/{mi} ({h/(h+mi):.0%})" if (h + mi) else "-"
    _g_names = ("g_l0_vml0", "g_l1_vml0", "g_l2_vml0", "gf_l0", "gf_l1", "gf_l2")
    if any((sim.caches.get(n) and (sim.caches.get(n).hits + sim.caches.get(n).misses)) for n in _g_names):
        print("\n-- G-stage PWC hit/miss [VM-level x G-level] (total) --")
        print(f"  {'':<9}{'G-L0(result)':>17}{'G-L1':>17}{'G-L2':>17}")
        for label, tag in (("@VM-L2", "vml2"), ("@VM-L1", "vml1"), ("@VM-L0", "vml0")):
            print(f"  {label:<9}{_gcell('g_l0_'+tag):>17}{_gcell('g_l1_'+tag):>17}{_gcell('g_l2_'+tag):>17}")
        print(f"  {'G-final':<9}{_gcell('gf_l0'):>17}{_gcell('gf_l1'):>17}{_gcell('gf_l2'):>17}")
        print(f"  (row = which VM-level pointer GPA is G-translated; col = G-stage level probed.")
        print(f"   deepest-first: G-L0 = full GPA->SPA result -> a hit means 0 host accesses.)")
    print("\n-- latency --")
    print(f"  avg : {m.avg_lat:8.2f} cyc  ({m.avg_lat*cyc:8.2f} ns)")
    print(f"  p99 : {m.p99_lat:8.2f} cyc  ({m.p99_lat*cyc:8.2f} ns)")
    print(f"  max : {m.max_lat:8.2f} cyc  ({m.max_lat*cyc:8.2f} ns)")
    wu_cyc, wu_reqs, steady_lat = m.time_to_steady()
    print("\n-- cold-start / warm-up --")
    print(f"  time to steady-state   : {wu_cyc:.0f} cyc  ({wu_cyc*cyc:.0f} ns),  {wu_reqs} requests")
    print(f"  (steady-state latency ~ {steady_lat:.1f} cyc; warm-up = caches filling + initial backlog draining.")
    print(f"   the required-hardware table above gives both the steady (warm) and the cold-start-")
    print(f"   inclusive peaks; provision the cold-incl column to sustain wire rate from cold start.)")
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
        # force unlimited resources so the peaks are the *requirement*; the steady
        # vs cold-incl split is reported automatically (no warm-up bump needed).
        cfg.walkers.num_walkers = None
        cfg.buffers.iommu_req_buffer = None
        cfg.buffers.io_bridge_buffer = None
        cfg.memory.max_outstanding = None

    # The primary run reports cache stats etc. from cold start (warmup=warmup).
    sim, m = run_sim(cfg, warmup_frac=warmup)
    s = summarize(cfg, sim, m)

    # When walkers/buffer are unlimited (null) we are MEASURING the requirement, so
    # report both the steady-state (warm) and the cold-start-inclusive peaks. A
    # second pass with a warm-up cutoff gives the steady column.
    m_steady = None
    measuring = (cfg.walkers.num_walkers is None and cfg.buffers.iommu_req_buffer is None)
    if measuring:
        _, m_steady = run_sim(cfg, warmup_frac=max(warmup, 0.1))
    print_report(cfg, sim, m, s, m_steady=m_steady)

    if measuring:
        wu_cyc, wu_reqs, _ = m.time_to_steady()
        print(f"\n  >>> 3c required parallel walkers N : steady {m_steady.peak_walks}, cold-incl {m.peak_walks}")
        print(f"  >>> 3d required IOMMU request buffer: steady {m_steady.peak_buffer}, cold-incl {m.peak_buffer}")
        print(f"      warm-up to steady state: {wu_cyc:.0f} cyc ({wu_cyc*cfg.cycle_ns:.0f} ns), {wu_reqs} requests")

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
