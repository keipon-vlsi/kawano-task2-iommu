# Task: Build the IOMMU architecture-exploration simulator (RTL-aware, cycle-approximate)

You are building the simulator for the IOMMU Microarchitecture Exploration project.
**Read these first and treat them as the binding contract:**
- `simulator_design_doc.md` — the authoritative design spec (parameters, models, metrics, FoM).
- `simulator_usage_manual.md` — the exact CLI/config/output interface you must implement.
- `design_premises.md` — full design rationale (why each choice).
- `CLAUDE.md` — project context, fixed numbers, validation trends.
- `iommu_sim/*.py` — the existing reference event-driven simulator; extend/refactor it
  rather than starting from scratch where sensible.

Work autonomously. If blocked, make a reasonable assumption, record it in
`ASSUMPTIONS.md`, and continue. Verify by running. Do not stop to ask.

## Version control
- Work on the **main** branch. `git init` if needed. Commit incrementally with clear
  messages. Print `git log --oneline` and `git status` at the end.

## Goal
Implement a **cycle-approximate, event-driven** architecture-exploration simulator
that conforms exactly to the design doc and usage manual. It must answer 3a–3d, find
the **minimum hardware to sustain wire rate**, estimate **process-independent
(normalized) area & power with per-module breakdown**, and produce a **PPA Pareto +
table** and the project **Figure of Merit**. It must be built **RTL-aware** so chosen
architectures can later be implemented in **SystemVerilog** and synthesized on sky130.

## Conformance requirements (must match the contract exactly)
1. **Time in cycles.** 400 MHz, 1 cycle = 2.5 ns; memory = 40 cycles. Every operation
   (lookup, arbitration, walk step, pipeline stage) costs a **configurable number of
   cycles**. Event queue keyed by cycle. All reported latencies in cycles (and ns).
2. **Config = spec.** Implement the full config of design_doc §4 / usage_manual §3
   (YAML/JSON loadable + dataclass). Every field maps to a future SystemVerilog
   `parameter` (RTL-realizable items only).
3. **Cache set** (design_doc §5): combined IOTLB (IOVA→SPA, coalescing-filled),
   S1 PWC (L2/L1), S2 PWC, table-GPA→SPA, DDT$, PDT$, MSI$; **root as a register**
   (never misses); **`data_gpa_cache` as an on/off toggle** (valuable under
   invalidation — stage-separated S2 result retained across S1 invalidation).
   Tiny upper/context caches = fully-associative (CAM/FF), larger leaf/data = 4-way SRAM.
   Context-tagged entries (device_id/PASID/VMID) so context switch needs no flush.
   `lookup_mode` ∈ {parallel, sequential, hybrid}; parallel uses a fixed
   most-complete-hit priority. `walk_trigger` ∈ {demand, predictive}.
4. **Modes**: bare / s1_only / s2_only / nested; `superpage` off/2M/1G. Nested cost
   model per design_premises (cold 2D walk; steady ~2 accesses/8 pages with caches).
5. **MSHR coalescing** and **64B leaf coalescing** (`coalesce_factor`, default 8).
6. **On-demand miss penalty** reported as a **per-miss-type cycle distribution**
   (IOTLB hit / MSHR-coalesced / PWC full-hit / partial / full-cold) — design_doc §6.
7. **Workload & events**: iova_pattern (sequential/stride/random), data_gpa
   (sequential/random), and injected **invalidation (rate, target s1/s2/both,
   granularity), fault, context-switch** events with configurable rates and
   n_devices/n_pasids. Trace exportable to CSV for reuse as an RTL testbench stimulus.
8. **Metrics** (design_doc §8): throughput & wire_rate_met; peak_walks (3c);
   peak_buffer (3d); mem peak-outstanding & bandwidth (GB/s); io_bridge_buffer peak;
   per-cache hit/miss; accesses_per_translation; latency avg/max/p99 (cycles & ns);
   miss-penalty by type; per-module area/power/energy-per-translation.
9. **Minimum-HW search** (design_doc §9): wire-rate-met = steady-state stall-free +
   small margin (cold-start excluded). Sweep num_walkers / iommu_req_buffer /
   io_bridge_buffer / mem_max_outstanding to find the minimum that meets it. Provide a
   `--measure peaks` mode (infinite resources → peak_walks=3c, peak_buffer=3d).
10. **Process-independent P&A** (design_doc §10): area in **gate-equivalents (GE)**
    (SRAM bit 0.2, CAM bit 0.6, FF bit 6, periphery ×1.3; logic in GE); power in
    **normalized energy units** (dynamic = activity × access-energy + FF-clock; static
    = leakage × bits/gates); DRAM energy reported separately. **Per-module breakdown**
    for both area and power, plus totals and **energy_per_translation**. Optional
    `scale_factor` to absolute units. Weights are tunable constants in one place.
11. **PPA / FoM** (design_doc §11): gate = wire_rate_met; architectural efficiency =
    accesses/translation; Pareto axes = (area GE, energy/translation); HW-cost =
    required N/buffer/outstanding. Emit a **Pareto + CSV table** among wire-rate-meeting
    configs (auxiliary scalar area×energy/translation allowed).
12. **RTL/sky130-aware** (design_doc §12): module decomposition mirrors the planned
    SystemVerilog hierarchy (engine, caches, walker_pool, arbiter, transaction_buffer,
    memory_model, prefetcher, workload, estimator, sweep, metrics, config). Provide
    `--emit-candidates` to output the exact config of selected Pareto points as a
    **SystemVerilog parameter file** (`.svh`), and `--emit-trace` for CSV traces.
    Output a frozen-prediction JSON (config hash + normalized PPA) for later
    estimate-vs-synthesis calibration.

## Engineering constraints
- Python. **Code and code comments in English.** Keep the engine/policy separation;
  new policies = subclass an ABC + select via config (don't edit the engine).
- The CLI and outputs must match `simulator_usage_manual.md` (run.py, sweep.py, flags,
  field names) so the manual is accurate as written.
- Include unit/smoke tests (`pytest`) asserting the validation trends below.

## Validation (must reproduce; trends/orders, not exact numbers)
From CLAUDE.md / design_premises (8000 sequential reqs, 100 GB/s, 400 MHz, 100 ns mem):
- no-cache: ~3 mem/page, peak_walks ≈ 8, peak_buffer ≈ 8, ~300 ns latency, sustains.
- PWC + coalescing: ~0.13 mem/page, peak_walks ≈ 1, sustains.
- + prefetch: latency collapses toward hit latency.
- random IOVA: regresses toward the no-cache regime.
- finite (walkers=4, buffer=4): throughput < target (wire-rate cliff).
- nested steady-state ≈ ~2× single-stage (with G-stage caches + coalescing).
- Cross-check peak_walks ≈ avg_latency / inter_arrival (Little's law).
- Per-module area/power sum to totals.

## Deliverables (under `iommu_sim/` or a clearly-named package)
1. The simulator conforming to the contract, runnable per the usage manual.
2. `run.py` (single run), `sweep.py` (min-HW search + Pareto + emit candidates/trace),
   `configs/` (baseline + a sweep space), example `candidates/*.svh`, `results.csv`.
3. `pytest` tests for the validation trends.
4. Update `ASSUMPTIONS.md` with any decisions made. Print actual run output in README.

## Acceptance criteria
- `python3 run.py --config configs/baseline.yaml` prints all metrics in §8 incl.
  per-module normalized area/power and miss-penalty-by-type.
- `python3 sweep.py --pareto` produces the area–energy/translation Pareto + CSV among
  wire-rate-meeting configs; `--emit-candidates` writes SystemVerilog `.svh` params.
- Validation trends reproduced; tests pass; per-module PPA sums to totals.
- Interface matches `simulator_usage_manual.md` exactly; everything committed on main;
  `git log`/`git status` printed.