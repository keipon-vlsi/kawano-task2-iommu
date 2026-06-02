# IOMMU PyMTL3 cycle-level simulator

A PyMTL3-based, cycle-level architecture-exploration simulator for an IOMMU
that has to keep up with **800 GbE / 100 GB/s wire DMA** at 400 MHz, with
swappable IOTLB / PWC policies, MSHR coalescing, walker pool, transaction
buffer, and a prefetcher. Reproduces the reference Python event-driven
simulator's trends and adds parameter sweeps that pinpoint where wire rate
breaks.

> 🇯🇵 詳しい使い方は **[`USAGE_ja.md`](./USAGE_ja.md)**、
> モジュール解説は **[`EXPLAIN_ja.md`](./EXPLAIN_ja.md)** を参照。

---

## Quick start

```bash
# from the repo root
python3 -m venv venv
source venv/bin/activate
pip install pymtl3 'pytest<8'        # see notes below

cd iommu_sim_pymtl
python3 run_demo.py                  # prints A–E table, writes results.csv
python3 sweep.py                     # writes sweep.csv + cliff summary
python3 -m pytest tests/ -v          # runs all 21 tests
```

`pytest<8` is required because the PyMTL3 wheel ships a pytest plugin that
uses the (now-removed) `pytest_cmdline_preparse` hook. The simulator itself
runs on any modern Python; only the test harness needs the older pytest.

---

## Demo output (verified on this machine)

```
=== A: no cache (full 3-level, unlimited resources) ===
  completed         : 8000
  total mem accesses: 24000  (3.000 /page)
  IOTLB hit         : 0  / coalesced(MSHR): 0  / true miss(walk): 8000
  required N (peak walks): 8
  required buffer (peak) : 8
  avg latency       : 300.0 ns (p99 300.0 ns)
  achieved throughput: 24.39 M/s  (target 24.41 M/s)  sustained=YES

=== B: PWC + coalescing ===
  completed         : 8000
  total mem accesses: 1017  (0.127 /page)
  IOTLB hit         : 4965  / coalesced(MSHR): 2035  / true miss(walk): 1000
  required N (peak walks): 1
  required buffer (peak) : 8
  avg latency       : 24.6 ns (p99 100.0 ns)
  achieved throughput: 24.42 M/s  (target 24.41 M/s)  sustained=YES

=== C: B + prefetch ===
  completed         : 8000
  total mem accesses: 1023  (0.128 /page)
  IOTLB hit         : 7992  / coalesced(MSHR): 8  / true miss(walk): 1002
  required N (peak walks): 3
  required buffer (peak) : 8
  avg latency       : 2.7 ns (p99 2.5 ns)
  achieved throughput: 24.42 M/s  (target 24.41 M/s)  sustained=YES

=== D: random IOVA (same PWC+coalesce config) ===
  completed         : 8000
  total mem accesses: 16131  (2.016 /page)
  IOTLB hit         : 3  / coalesced(MSHR): 0  / true miss(walk): 7997
  required N (peak walks): 8
  required buffer (peak) : 8
  avg latency       : 201.6 ns (p99 300.0 ns)
  achieved throughput: 24.40 M/s  (target 24.41 M/s)  sustained=YES

=== E: no-cache + finite (walker=4, buffer=4) ===
  completed         : 8000
  total mem accesses: 24000  (3.000 /page)
  IOTLB hit         : 0  / coalesced(MSHR): 0  / true miss(walk): 8000
  required N (peak walks): 4
  required buffer (peak) : 4
  avg latency       : 136393.0 ns (p99 269895.0 ns)
  achieved throughput: 13.33 M/s  (target 24.41 M/s)  sustained=no
```

The trends the prompt asks for are reproduced:

| Trend                                                             | Result                                  |
|-------------------------------------------------------------------|-----------------------------------------|
| No-cache baseline needs ~8 concurrent walks                       | A: **peak_walks = 8** ✓                 |
| PWC + 64 B coalescing collapses memory traffic ~20-25x            | B: **3.0 → 0.13 mem/page** ✓            |
| Prefetch collapses observed latency to ~hit latency               | C: **2.7 ns avg, hit-lat ~2.5 ns** ✓    |
| Random IOVA regresses toward no-cache regime                      | D: **peak_walks = 8, mem/pg = 2.0** ✓   |
| Under-provisioned finite resources fail wire rate                 | E: **13.3 M/s < 24.41 M/s** ✓           |

`sweep.py` shows the cliffs explicitly: walker=8 (last failed at 7),
buffer=8 (last failed at 6) under the no-cache stress workload. Both are
saved in `sweep.csv`.

---

## Layout

```
iommu_sim_pymtl/
├── iommu_sim_pymtl/
│   ├── __init__.py        — package exports
│   ├── config.py          — SimConfig + sub-cfgs (every knob lives here)
│   ├── caches.py          — SetAssocCache + LRU/FIFO/Random
│   ├── prefetch.py        — NoPrefetch / NextLineStride / ConfidenceStride
│   ├── memory.py          — fixed-latency memory with outstanding cap
│   ├── walker_cost.py     — SingleStageCost / NestedCost
│   ├── workload.py        — sequential / random / multi_stream
│   ├── metrics.py         — per-run counters
│   ├── engine.py          — IOMMUEngine — the PyMTL3 cycle-level Component
│   └── harness.py         — SimConfig → wired engine → tick loop → Metrics
├── run_demo.py            — A–E scenarios + results.csv
├── sweep.py               — walkers/buffer/coalesce/prefetch/pattern sweep
├── pytest.ini             — disables broken PyMTL3 pytest plugin
├── README.md              — this file
├── USAGE_ja.md            — Japanese: end-to-end usage + parameter recipes
├── EXPLAIN_ja.md          — Japanese: code walkthrough, per module
└── tests/
    ├── test_smoke.py        — every component constructs / runs
    └── test_validation.py   — assertions on A–E trends + swappability
```

See `../ASSUMPTIONS.md` for decisions made during the build.

---

## Engine / policy separation

The engine in `iommu_sim_pymtl/engine.py` is a single PyMTL3 Component
with one `@update_ff` block that advances the simulation by one cycle.
Per-cycle it executes the fixed datapath order:

```
arrivals[c] -> completions[c] -> launch queued walks -> admit waiting demand
```

Every *policy* used inside that datapath — IOTLB lookup, PWC lookup,
replacement, walk cost (single-stage / nested), prefetcher, workload — is
plugged in through a small ABC and held as a plain Python attribute on the
Component. To swap one of them you edit a single `SimConfig` field; you
do not touch the engine.

This is the "structured so each component can later be refined toward RTL"
constraint from the prompt: the engine reads as fixed hardware, the
policies read as configuration.

---

## License / status

Build for internal architecture-exploration. Not a production IOMMU model.
See `../ASSUMPTIONS.md` for the explicit list of simplifications.
