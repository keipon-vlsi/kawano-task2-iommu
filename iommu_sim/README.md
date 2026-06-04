# IOMMU architecture-exploration simulator

Cycle-approximate, event-driven simulator for the IOMMU microarchitecture
exploration (Task 2). It answers 3a–3d, finds the **minimum hardware to sustain
800 GbE wire rate**, estimates **process-independent (GE / normalized) area &
power per module**, and produces a **PPA Pareto + table** and the project Figure
of Merit. It is built **RTL-aware** so chosen points can later be implemented in
SystemVerilog and synthesized on sky130.

Contract: `../simulator_design_doc.md`, `../simulator_usage_manual.md`,
`../design_premises.md`. Decisions: `../ASSUMPTIONS.md`.

## Layout (mirrors the planned SystemVerilog hierarchy)
| module | role |
|---|---|
| `config.py`    | full config dataclasses + YAML/JSON loader (= the SV parameter table) |
| `engine.py`    | cycle-keyed event loop: buffer, walker pool, MSHR coalescing, metrics |
| `caches.py`    | CacheABC, SetAssoc/CAM, replacement, generation invalidation, `CacheSet` |
| `walker.py`    | walk cost models (bare/s1/s2/nested), coalescing, miss-type classify |
| `memory.py`    | latency / outstanding / bank / coalescing |
| `prefetch.py`  | off/next_line/stride/rpt/dcpt/sms (+confidence throttle) |
| `workload.py`  | trace generation + invalidation/fault/context-switch events + CSV export |
| `metrics.py`   | all §8 metrics incl. miss-penalty-by-type |
| `estimator.py` | per-module area (GE) + power (normalized energy) + frozen JSON |
| `runner.py`    | shared run + summarize helpers |
| `run.py`       | single-run CLI |
| `sweep.py`     | min-HW search, peaks, Pareto + CSV, candidate `.svh` / trace emission |

## Install / run
`run.py` and `sweep.py` use only the standard library + **PyYAML** (so plain
`python3` works). **pytest** and **matplotlib** ship in the repo venv `../.venv`.

```bash
cd iommu_sim
python3 run.py   --config configs/baseline.yaml                 # single run (all §8 metrics)
python3 run.py   --config configs/baseline.yaml --measure peaks # clean 3c/3d (infinite res)
python3 sweep.py --config configs/baseline.yaml --search min_hw # minimum HW to sustain wire rate
python3 sweep.py --config configs/space.yaml    --pareto --emit-candidates
python3 run.py   --config configs/baseline.yaml --emit-trace out/trace.csv
../.venv/bin/python -m pytest -q                                # validation tests
```

## Answers (baseline = nested, contiguous IOVA+GPA, single context)
| question | result |
|---|---|
| 3a no-cache latency | 3 × 40 cyc = **120 cyc = 300 ns** (3-level walk) |
| 3b with caches (nested) | avg **39 cyc ≈ 98 ns** (IOTLB hit 3 cyc; ~0.27 mem accesses/translation) |
| 3c required walkers N | **2** (`--measure peaks`); no-cache needs **8** |
| 3d required buffer | **13** (`--measure peaks`); no-cache needs **8** |
| min-HW (nested) | num_walkers **2**, iommu_req_buffer **13**, io_bridge **~20**, mem_outstanding **2** |

`--measure peaks` excludes cold start (steady-state requirement). A plain `run.py`
reports true peaks **including** cold start (N=5, buffer=33 here) — conservative
provisioning per design_premises §12.

## Baseline single-run output
```
=== baseline  (mode=nested, superpage=off, lookup=hybrid, prefetch=off) ===
  clock 400 MHz, 1 cycle = 2.5 ns, mem = 40 cyc, inter-arrival = 16.38 cyc

-- throughput / wire rate --
  completed              : 8000
  throughput             : 24.417 M/s   (target 24.414 M/s)
  wire_rate_met          : True

-- required hardware (3c / 3d) --
  peak_walks (3c, N)     : 5   [measured @ unlimited]
  peak_buffer (3d)       : 33   [measured @ unlimited]

-- memory / I/O-bridge performance requirements --
  mem_outstanding_peak   : 5
  mem_bandwidth          : 0.42 GB/s
  mem_accesses           : 2136  (0.2670 /translation)
  io_bridge_buffer_peak  : 33   (4 kB payload holders)

-- cache hit/miss --
  cache            hits    misses  hit_rate
  iotlb            1930      6070     0.241
  s1_pwc            965        35     0.965
  s2_pwc            995        50     0.952
  table_gpa         995        45     0.957
  ddtc              999         1     0.999
  iotlb_hit=1930  mshr_coalesced=5070  walks=1000

-- latency --
  avg :    39.05 cyc  (   97.61 ns)
  p99 :   188.62 cyc  (  471.54 ns)
  max :   525.00 cyc  ( 1312.50 ns)

-- miss-penalty distribution by type (cycles) --
  type               count   avg_cyc    avg_ns   max_cyc
  iotlb_hit           1930      3.00      7.50      3.00
  mshr_coalesced      5070     42.62    106.56    508.62
  pwc_full_hit         965     85.00    212.50     85.00
  pwc_partial           30    205.00    512.50    205.00
  full_cold              5    461.00   1152.50    525.00
  (characteristic full-cold walk depth for mode nested: 15 x 40 cyc)

-- normalized area & power (per module) --
module           area_GE   sram_b   cam_b    ff_b  gates    access        dyn       stat
----------------------------------------------------------------------------------------
iotlb             1661.4     6144       0       0     64      9000     0.1318    0.06272
s1_pwc            1219.4      336     636       0    636      2017     0.4980    0.02244
s2_pwc             941.1      224     496       0    496      1064     0.2059    0.01712
table_gpa         1882.2      448     992       0    992      1058     0.4049    0.03424
data_gpa             0.0        0       0       0      0         0     0.0000    0.00000
ddtc               667.2      704     272       0    272      1001     0.1106    0.01520
pdtc                 0.0        0       0       0      0         0     0.0000    0.00000
msi                747.8      576     336       0    336         0     0.0000    0.01584
walkers           5360.0        0       0     560   2000      1000     0.7476    0.04560
buffer           19008.0        0       0    3168      0      8000    31.7972    0.03168
arbiter            800.0        0       0       0    800      8000     0.0488    0.01600
control           2000.0        0       0       0   2000      8000     0.0610    0.04000
----------------------------------------------------------------------------------------
TOTAL            34287.3                                              34.0059    0.30084
  area: 34,287.3 GE
  power(norm): dyn 34.0059 + stat 0.30084 = 34.3068 units/cycle
  energy/translation: 562.025 norm-units
  DRAM (separate): 2136 accesses, 42,720 norm-units (5.34/translation)

  FoM (area_GE x energy/translation): 19270304.89
```

## Validation trends (reproduced; `pytest`)
8000 sequential requests, 100 GB/s, 400 MHz, 100 ns memory:

| scenario | mem/page | peak walks | avg lat | wire rate |
|---|---|---|---|---|
| A no-cache              | 3.00  | 8 | 312 ns | met |
| B PWC + coalescing      | 0.127 | 1–2 | 33 ns | met |
| C B + prefetch          | 0.128 | 2–3 | 8 ns  | met |
| D random IOVA           | 2.00  | 9 | 212 ns | met (degraded) |
| E no-cache, 4 walk/4 buf| 3.00  | 4 | — | **NOT met (12.8 M/s)** |
| nested (steady)         | 0.267 | 1–2 | 98 ns | met (≈2× single) |

Little's law cross-check (no-cache): N ≈ avg_latency / inter-arrival = 312/40.96 ≈ 8.

## Sweep / Pareto outputs
- `results.csv` — every config: area_GE, energy/translation, FoM, peaks, wire_rate_met.
- `pareto.png` — area–energy scatter + front (needs matplotlib / the venv).
- `candidates/*.svh` — selected Pareto points as SystemVerilog parameter files.
- `freeze/*.json` — frozen normalized-PPA prediction (config hash) for synthesis calibration.
- `out/*.csv` — exported trace (RTL testbench stimulus).
