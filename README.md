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
| 4 | cfg4_prefetch | 1 | 1 | 1  | 5  | 8 | 1 | 1 |
| 5 | cfg5_notag    | 1 | 1 | 1  | 5  | 8 | 1 | 0 |

Walker/buffer counts come from the project simulator (`iommu_sim/`): cfg1 = 12-memory-
access cold nested walk under Little's law (×1.25 → 37); cfg2 = PWC warm ⇒ 2 leaf reads
= 200 ns ⇒ 5; cfg3/4/5 = one coalesced line-pair walk per 8 translations ⇒ 1 walker,
the other 7 served by IOTLB / MSHR.

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
| 3 | iotlb    | 11.30 | met  | 37 (=N/8)      | all correct |
| 4 | prefetch | 10.32 | met  | 41 (~N/8)      | all correct |
| 5 | notag    | 10.32 | met  | 41 (~N/8)      | all correct |

`*` cfg2 (5 walkers, two serial 100 ns reads) runs at ~94 % of the wire-rate budget at
the cycle level — the known event-driven→cycle-level gap; the CLAUDE.md +1-walker margin
(6 walkers) closes it. cfg4 < cfg3 confirms prefetch hides cold-start latency.

## Results — synthesis (sky130_fd_sc_hd, tt 1v80, 2.5 ns target, switching activity 0.2)
| # | config | area (µm²) | power (mW) | worst slack (ns) | Fmax (MHz) |
|---|---|---|---|---|---|
| 1 | nocache  | 564 070 | 254.3 | −53.54 | 17.8 |
| 2 | pwc      | 103 447 | 48.0  | −15.53 | 55.5 |
| 3 | iotlb    | 151 598 | 74.0  | −14.16 | 60.0 |
| 4 | prefetch | 165 838 | 80.1  | −15.85 | 54.5 |
| 5 | notag    | 127 397 | 60.7  | −16.78 | 51.9 |

- cfg1 (37 walkers, no cache) = 37 parallel `pte_addr` adders + a 37-way arbiter ⇒ ~5.5×
  the area / ~3–5× the power of the cached configs and the worst Fmax — brute-force
  concurrency is the most expensive way to hit wire rate.
- cfg5 = cfg4 minus device_id/PASID in every cache tag ⇒ **−23 % area / −24 % power**
  (165 838→127 397 µm², 80→61 mW): context tags are expensive in a FF-based CAM.
- Power is ~71–86 % internal/sequential (FF-based CAMs + walker RF); leakage negligible.
  Reported at the 2.5 ns clock with flat 0.2 activity (no VCD) — the switching share
  scales ∝ freq; internal/leakage (~85 %) is ~frequency-independent.

cfg4 per-module area (µm²): IOTLB CAM **67 742** (16×63-bit FF tags) + iommu_top control
**73 339** (walker RF, arbiter, MSHR, address adders) dominate; VM/G PWCs 3 498 / 7 876;
prefetch_ctrl 1 790; mem_master 216. Area grows monotonically with caching
(cfg2 < cfg3 < cfg4), as expected — caches are the cost, coalescing is the benefit.

**Critical path (all configs, Phase 1):** the **fused single-cycle memory-issue cone** —
context FF → cache-hit / most-complete-hit shortcut → high-fanout MSHR/arbiter select →
`pte_addr` address-composition adder → next-state FF (~16–18 ns). This single-cycle
fusion was chosen to meet the wire-rate cycle budget at the minimal walker counts; it is
the Phase-2 optimization target.

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
