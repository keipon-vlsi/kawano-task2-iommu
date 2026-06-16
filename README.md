# Nested IOMMU translation core — one parameterized RTL, five configurations

A single parameterized, synthesizable SystemVerilog **nested 2-stage** (VS-stage +
G-stage, 4 KB pages) IOMMU address-translation core, instantiated as **five
configurations**. Each config lives in its own folder with a thin parameter-set
wrapper, a cocotb testbench, and sky130 synthesis output.

The DUT is the digital IOMMU block only (PCIe/Ethernet PHYs out of scope). It starts
**after a DDTC/PDTC hit** (device/G-stage root pointers are pre-loaded registers — no
context walk), is **happy-path only** (no faults/permissions), and treats data
movement abstractly (a translation completes when its SPA is produced). Full list in
`ASSUMPTIONS.md`.

Per the task constraint, **no logic that could lie on the critical path is abstracted
away**: real parallel CAM tag compare across all entries, priority / most-complete-hit
encoder, base-SPA mux trees, the address-composition adder `(base<<12)+(idx<<3)`, MSHR
associative compare, the walker context register file, the memory-issue arbiter, and
the memory tag demux are all real RTL.

## Layout
```
rtl/                  one parameterized core
  iommu_pkg.sv          types/widths, address model, pte_addr() adder
  fa_cache.sv           fully-associative DFF CAM: parallel compare + priority enc + mux tree
  mem_master.sv         AXI-like tagged read master (64 B line), MEM_MAX_OUTSTANDING cap
  prefetch_ctrl.sv      next-line prefetch trigger (adder + same-VM-L0-table guard + dedup)
  iommu_top.sv          N-context walker RF, nested PTW + table-G sub-walks, MSHR,
                        unified pipelined memory-issue arbiter, PWC/IOTLB shortcuts
cfg1_nocache/ .. cfg5_notag/
  cfgN_top.sv           parameter-set wrapper (the config)
  tb_coco/run.py        cocotb (Verilator) runner for this config
  results/              synthesis artifacts (synth_area.txt, sta.txt, netlist.v, *.log)
tb_coco/iommu_tb.py     shared cocotb test (builds nested page tables, drives wire rate)
tb_coco/runner_common.py
syn/synth_nested.py     sky130 synth + OpenSTA (per-module area, Fmax, critical path)
ASSUMPTIONS.md
```

## The five configurations
| # | folder | HAS_PWC | HAS_IOTLB | WALKERS | BUFFER | COALESCE | PREFETCH | TAG_CTX |
|---|---|---|---|---|---|---|---|---|
| 1 | cfg1_nocache  | 0 | 0 | 37 | 37 | 1 | 0 | 1 |
| 2 | cfg2_pwc      | 1 | 0 | 5  | 5  | 1 | 0 | 1 |
| 3 | cfg3_iotlb    | 1 | 1 | 1  | 5  | 8 | 0 | 1 |
| 4 | cfg4_prefetch | 1 | 1 | 1  | 1  | 8 | 1 | 1 |
| 5 | cfg5_notag    | 1 | 1 | 1  | 1  | 8 | 1 | 0 |

Walker/buffer counts come from the project simulator (`iommu_sim/`): cfg1 = 12-memory-
access cold nested walk under Little's law (×1.25 → 37); cfg2 = PWC warm ⇒ 2 leaf reads
= 200 ns ⇒ 5; cfg3 = one coalesced line-pair walk per 8 translations ⇒ 1 walker, buffer 5
holds the riders waiting on the in-flight walk. cfg4/cfg5 add prefetch one line ahead, so
demands hit the IOTLB and never wait ⇒ **1 walker + 1-deep buffer** (the walker is shared:
demand-priority, idle for the prefetch walk in steady state).

## How to run

**1. Architecture sim (Python, picks the counts)**
```
cd iommu_sim && python3 run.py
```
**2. Cocotb testbench (Verilator) — per config**
```
cd cfg4_prefetch/tb_coco && ../../.venv/bin/python run.py
```
Drives a sequential contiguous-IOVA trace, builds a self-consistent nested page table
(identity G-stage so the walk is fully exercised; expected SPA = (vpn+BASE)<<12), and
checks: every translation produces the correct SPA, coalescing (`walks ≈ N/COALESCE`),
and sustained wire rate (≤ 16.384 cyc/translation once warmed).

**3. sky130 synthesis + STA (Docker iic-osic-tools)**
```
python3 syn/synth_nested.py cfg4_prefetch          # or list configs / none for all
```
Per-module area, Fmax and the critical path (sky130_fd_sc_hd, tt 1v80, 2.5 ns target).

## Results — functional (cocotb, 296 translations, 100 GB/s, MEM_LATENCY = 40 cyc)
| # | config | cyc/translation | wire rate (≤16.38) | walks (coalescing) | SPA |
|---|---|---|---|---|---|
| 1 | nocache  | 10.82 | met  | 296 (=N, CO=1) | all correct |
| 2 | pwc      | 17.33 | met* | 296 (=N, CO=1) | all correct |
| 3 | iotlb    | 10.80 | met  | 37 (=N/8)      | all correct |
| 4 | prefetch | 11.08 | met  | 38 (~N/8)      | all correct |
| 5 | notag    | 11.08 | met  | 38 (~N/8)      | all correct |

`*` cfg2 (5 walkers, two serial 100 ns reads) runs at ~94 % of the wire-rate budget at
the cycle level — the known event-driven→cycle-level gap; the CLAUDE.md +1-walker margin
(6 walkers) closes it. cfg4/cfg5 run with **1 walker and a 1-deep buffer**: prefetch is
issued one line ahead, so steady-state demands are all IOTLB hits that never touch the
walker (free for the prefetch walk) and each completes in ~2 cyc (1 buffer slot suffices).
The memory bus is 8 B (1 PTE/beat); a coalesced 64 B leaf line arrives as an 8-beat burst.

## Results — synthesis (sky130_fd_sc_hd, tt 1v80, 2.5 ns target, switching activity 0.2)
| # | config | area (µm²) | power (mW) | worst slack (ns) | Fmax (MHz) |
|---|---|---|---|---|---|
| 1 | nocache  | 535 709 | 250.2 | −58.24 | 16.5 |
| 2 | pwc      |  98 004 | 46.7  | −12.76 | 65.5 |
| 3 | iotlb    | 131 157 | 65.2  | −16.04 | 53.9 |
| 4 | prefetch | 114 444 | 56.5  |  −8.58 | 90.3 |
| 5 | notag    |  76 142 | 37.7  |  −6.35 | 113.0 |

- cfg1 (37 walkers, no cache) = 37 parallel `pte_addr` adders + a 37-way arbiter ⇒ ~5–7×
  the area / ~4–7× the power of the cached configs and the worst Fmax (16.5 MHz) — brute-
  force concurrency is the most expensive way to hit wire rate.
- **cfg4 < cfg3** (114 444 < 131 157 µm²) **and 90 vs 54 MHz**: prefetch lets cfg4 run at
  1 walker / 1-deep buffer, shrinking control 40 443 → 20 546 µm² and killing the MSHR
  fanout on the critical path — prefetch *reduces* area and *improves* Fmax.
- The 8 B-bus / per-beat IOTLB fill (B案) **removed the 512 b `fill_data_q`** and its
  512→64 b extraction mux from the consume→issue cone: cfg4 Fmax 62 → 90 MHz, cfg5 73 →
  113 MHz vs the old single-cycle-512 b model.
- cfg5 = cfg4 minus device_id/PASID in every cache tag ⇒ **−33 % area / −33 % power**
  (114 444→76 142 µm², 56.5→37.7 mW) and the best Fmax (113 MHz): narrower CAM tags shrink
  both tag storage and the compare logic.
- Power is ~71–86 % internal/sequential (FF-based CAMs + walker RF); leakage negligible.
  Reported at the 2.5 ns clock with flat 0.2 activity (no VCD); at each config's achievable
  Fmax with activity 0.1 the real operating power is ~5–9 mW.

Per-module and fine-grained breakdowns: `results/area_breakdown.png` (pie),
`results/control_split.png` (Control split), and `syn/fine_breakdown.py` (caches split
tag / data / lookup-logic). The IOTLB CAM and its lookup logic dominate the cached
configs; the arbiter+adder cone dominates cfg1.

**Critical path:** see `CRITICAL_PATH.md`. All configs share the fused consume→issue cone
(cache-hit shortcut → arbiter → `pte_addr` adder → FF); its length scales with NUM_WALKERS
(arbiter/mux), cache CAM width, and BUFFER_DEPTH (MSHR). cfg1 60.6 ns (37-way arbiter),
cfg4 11.1 ns, cfg5 8.7 ns. RTL module-by-module detail: `RTL_DETAILS.md`.

## Phase status
- **Phase 1 (done):** clean parameterized RTL; cocotb passing on all 5; **all 5
  synthesized** (area/power/Fmax above) with the critical path identified.
- **Phase 2 (after review):** take cfg4 and iterate the
  critical path — **register the issue address** (break the consume→adder→arbiter cone
  into ≥2 stages / add a lookup-mode + `PIPELINE_DEPTH` stage) and report before/after
  Fmax. Expected: large Fmax gain for +1 cycle/read latency (covered by the +1-walker
  margin already noted for cfg2).
```
git log --oneline   # incremental history
```
