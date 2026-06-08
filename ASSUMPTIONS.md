# ASSUMPTIONS — IOMMU exploration simulator

Decisions made while building the simulator (`iommu_sim/`) to the contract
(`simulator_design_doc.md`, `simulator_usage_manual.md`, `design_premises.md`).
Where the contract was silent or under-specified, a reasonable choice was made,
recorded here, and the build continued. (This supersedes the earlier
estimator-only ASSUMPTIONS.)

## Environment / tooling
- **Python 3.14**, no system `pip`. `run.py` / `sweep.py` need only the standard
  library + **PyYAML** (present in the system interpreter), so they run under
  plain `python3`.
- **pytest** and **matplotlib** are not installed system-wide and there is no
  system pip. A project virtualenv **`.venv`** (repo root) was created with
  `pytest`, `matplotlib`, `pyyaml`. Run tests with `../.venv/bin/python -m pytest`
  and the Pareto plot with the venv interpreter. The Pareto CSV is always written;
  the PNG is skipped gracefully if matplotlib is unavailable.
- The whole `iommu_sim/` Python tree was rewritten to the cycle-based, config-driven
  contract (the previous event-driven reference in ns was extended/replaced module
  by module, keeping the engine/policy separation).

## Time / cycle model
- All time is in **cycles** (float; arrivals land on fractional cycles because the
  wire inter-arrival 40.96 ns = 16.384 cycles is not integer). ns is derived with
  `cycle_ns` at report time. The event queue is keyed by cycle.
- A page-table walk is a **sequential pointer-chase**: it occupies the memory
  channel for `accesses × mem_latency` cycles but holds **one** outstanding read at
  a time. Hence `mem_outstanding_peak ≈ peak_walks`, and the AXI outstanding cap
  bounds concurrent walks (design_premises §6).
- Per-walk latency = `arbitration_cycles + walk_pipeline_depth + accesses ×
  mem_latency`. A lookup adds `lookup_cycles`; a hit completes after
  `hit_latency_cycles`.

## Cache / IOTLB modelling
- **Combined IOTLB is keyed by the coalesced 64 B line** `(line, ctx)`, not by
  individual page. One leaf fetch fills one line entry covering `coalesce_factor`
  pages (the other pages of the line then hit). This is functionally equivalent to
  filling 8 per-page entries for hit/miss behaviour, avoids materialising large page
  ranges (important for superpages), and lets MSHR and IOTLB share one key. The
  **area model still counts the configured per-page entry count** (`iotlb.entries`).
- **MSHR registers a line on the FIRST miss, before any walker is granted.** All
  requests to that line — and the whole coalesced line — share ONE walk. Without
  this, capping walkers would (wrongly) make every request spawn its own walk and
  collapse coalescing; with it, a small walker count suffices (the intended result).
- **Associativity**: `"full"` → fully associative (CAM, 1 set); `1` → direct; `N` →
  N-way. Replacement (`LRU`/`FIFO`/`Random`) only matters for assoc > 1 — for
  monotonic streaming, structural per-level separation is the real lever
  (design_premises §10), so the default `LRU` is near-moot.
- **Generation-based invalidation**: flush bumps a global generation; per-context
  invalidation bumps that context's generation; page/range filters matching entries.
  A hit is valid only while its stamp matches the live generation → O(1)
  flush/context invalidation (RTL intent).
- **Root tables are registers** (never miss in a single context). The G-stage root
  is an `AlwaysHit` structure loaded once.

## Walk cost model
- **Single-stage (bare/s1_only/s2_only)**: Sv39-like 3-level. PWC short-circuits
  upper levels; the leaf line is coalesced. Cold = 3 accesses, steady = 1 per line.
- **Nested**: Sv39 + Sv39x4. Each guest PTE pointer is a GPA translated by the
  G-stage before the guest PTE is read. **Steady state collapses to 2 accesses per
  coalesced line** (guest-leaf line + data-GPA S2-leaf line) ≈ 2× single-stage,
  matching design_premises §4/§15. The dynamic **cold** first walk is ~12 accesses
  in this cached model (the G-stage root is a register that loads once); the
  canonical structural worst case **15** ((3+1)(3+1)−1, no caches at all) is reported
  as the `full_cold` miss-penalty *characteristic* for nested mode (design_doc §6).
  These two numbers measure different things and both are shown.
- **Superpage** reduces walk depth (2M → 2 levels, 1G → 1 level) and broadens the
  effective coalescing width (2M → 512 pages/leaf, 1G → 512²), so translation traffic
  drops sharply. Coarse but directionally correct.

## Resources / wire-rate definition
- **`wire_rate_met` = steady-state stall-free + margin** (design_doc §9): achieved
  throughput ≥ target AND no post-warmup back-pressure (`arrival_stalls`) and no
  walk-start stalls (`walk_stalls`). A throughput-only definition would call a
  degenerate buffer=1 "sufficient"; the stall-free definition yields meaningful
  minima (min buffer = peak in-flight, min walkers = peak concurrent walks).
- **Warmup**: peaks (`--measure peaks`) and the min-HW search exclude cold start via
  `warmup_frac` (default 0.05) so 3c/3d reflect the steady-state requirement that
  governs wire rate. `run.py` without `--measure` reports **true peaks including cold
  start** (slightly higher; conservative provisioning, design_premises §12).
- The min-HW search finds each resource's minimum with the **others left generous**
  (independent lower bounds); provision +50–100 % per design_premises §12.

## Area / power (process-independent)
- Area in **gate-equivalents (GE)**: `cell = SRAMbit×0.2 + CAMbit×0.6 + FFbit×6`,
  array periphery ×1.3, logic added as gate count. Power in **normalized NAND-switch
  energy units**; dynamic = activity×access-energy + FF-clock, static =
  (bits+gates)×leak. **DRAM access energy is reported separately.** All weights live
  in `estimator.PAWeights` (one place); `pa.scale_factor` absolutizes.
- The transaction buffer holds **control bits only** (the 4 kB DMA payload lives in
  the I/O bridge, design_premises §13). Buffer FF-clock power is intentionally
  visible per-module — it flags over-provisioned buffers as a real RTL cost.
- Module decomposition mirrors the planned SystemVerilog hierarchy: iotlb / s1_pwc /
  s2_pwc / table_gpa / data_gpa / ddtc / pdtc / msi / walkers / buffer / arbiter /
  control.

## PPA / FoM
- Pareto axes = **(area GE, energy/translation)** among wire-rate-meeting configs;
  auxiliary scalar = area × energy/translation. Because throughput is a **hard gate**,
  area and energy co-minimize and the front often reduces to a **single dominant
  point** — the expected reduction stated in design_doc §11, not a bug. The full
  scatter (`results.csv`, `pareto.png`) shows the architectural regimes;
  `--emit-candidates` writes the selected points as `.svh` parameter files.

## Workload / events
- `iova_pattern` ∈ sequential/stride/random; `data_gpa` ∈ sequential/random (the
  nested S2-leaf coalescing lever). Invalidation/fault/context-switch are injected as
  rated events (events per translation → integer request interval). Context tags
  (`device_id`, `pasid`) rotate over `n_devices`/`n_pasids` when
  `context_switch_rate > 0`; `vmid` fixed (single guest).
- `--emit-trace` writes the request+event stream as CSV (cycle and ns timestamps)
  for reuse as an RTL testbench stimulus.

## Known simplifications (first-order; calibrate against synthesis later)
- Interconnect / clock-tree area & power are not modelled (design_premises §13).
- `bank_parallel=false` applies a coarse outstanding-proportional penalty rather than
  a full bank/row-buffer model.
- Prefetchers rpt/dcpt/sms are confidence-throttled stride variants (distinct
  behaviour, self-disable on random) rather than full literature implementations;
  enough to show "+prefetch collapses latency" and "self-disables on random".
- The cycle costs (lookup/arbitration/pipeline) are seeds to be refreshed from early
  trial synthesis on sky130 (design_doc §6/§12).

---

# ASSUMPTIONS — RTL phase (`rtl/`, `tb_coco/`, `syn/`)

Phase-1 = a clean, reviewable, synthesizable first version of the 5 core blocks +
top, a working cocotb happy-path testbench, and the Full config synthesized on
sky130. The 4-config sweep, sub-experiments and estimate↔synth calibration are
later iterations.

## Toolchain (no system pip; built in the project `.venv`)
- **Verilator 5.046** (cocotb sim), **cocotb 2.0.1** (force-installed:
  `COCOTB_IGNORE_PYTHON_REQUIRES=1`, since Python is 3.14 > cocotb's 3.13 cap;
  the happy-path TB works), **sv2v v0.0.13** (`/tmp/sv2v`; SV→Verilog so yosys
  parses), **yowasp-yosys 0.66** (WASM yosys), **sky130 PDK** via `volare`
  (`$PDK_ROOT`, `sky130_fd_sc_hd` tt corner). No OpenSTA/OpenROAD available
  offline → timing from ABC/`ltp` (see Synthesis).

## RTL scope / simplifications (steady-state happy path)
- Only the 5 blocks are synthesized: walk engine (PTW), txn buffer + MSHR,
  caches, arbiter, memory IF. Workload driver, memory model and the I/O-bridge
  4 kB data path are **testbench stubs**, not DUT.
- **No faults/permissions/PRI**; every PTE is valid, every access permitted.
- **Context/root pre-loaded**: the modeled walk starts at the (non-leaf) PTE
  fetch; DDT$/PDT$/root resolution is not walked (the TB pre-loads caches/regs).
- **No 4 kB data movement**: the buffer holds control/descriptor state only; a
  request completes abstractly when its SPA is produced.
- **MSHR = the buffer**: same-line in-flight entries coalesce onto the one
  dispatched walk (no separate MSHR table) — matches the sim's line-keyed MSHR.
- **Walk model**: a walker executes a fetch *plan* = `nreads` chained tagged PTE
  reads (the residual after PWC/IOTLB short-circuit), then composes the SPA. The
  front-end computes `nreads` from MODE + PWC hit (steady single=1, nested=2,
  cold larger) — faithful to the sim's accesses/translation, not a full per-level
  2D-walk address generator. The leaf read returns the coalesced **line base**
  PPN; the front-end adds the page offset-within-line so each of COALESCE_FACTOR
  pages gets a distinct, correct SPA.
- **Cache lookup is registered (1-cycle)** for both ff and sram storage, so the
  pipeline is uniform. `STORAGE=ff|sram` is a recorded parameter; the ff/sram/
  mixed *synthesis* mapping (RAM macro vs flops) is applied in the synth flow and
  is a later sub-experiment. A conditional `ram_style` RTL attribute was removed
  (non-constant after elaboration); yosys currently maps all cache arrays to flops
  (→ caches dominate area, the expected all-DFF result).
- `LOOKUP_MODE`, `PIPELINE_DEPTH`, `PREFETCH_EN`, `CLOCK_GATING_EN` are real
  parameters/hooks; Phase-1 behaviour is the hybrid/1-cycle/no-prefetch point.
  Per-entry write-enables are already coded (clock-gating friendly).
- DDT$/PDT$ are sized by parameter but, being pre-loaded constant context, do not
  add residual reads on the happy path; S2 PWC is instantiated (area) and
  exercised via preload. Full per-level S2 PWC + DDT$/PDT$ traffic is a later
  iteration.

## Testbench
- cocotb + Verilator. Stub AXI memory returns a PTE after `MEM_LATENCY` cycles;
  the S1 PWC is pre-loaded for steady state; a sequential trace is driven at
  ~wire-rate pacing. Checks every translation completes with the correct per-page
  SPA and **cross-checks the RTL walk count against the Python reference sim**
  (imported in-process): 32 walks = 256/8 lines, matches.

## Synthesis (sky130, yosys)
- `syn/synth.py`: per config → SV wrapper (fixes the parameter set) → sv2v →
  yosys generic synth → `dfflibmap` + `abc -liberty` (sky130 sc_hd tt) → `stat`
  (per-module area) and a flattened ABC pass for the critical path.
- **Area** is from yosys + the sky130 liberty (authoritative total; per-module
  from the per-module `Chip area` lines — they sum approximately, cross-module
  opt shifts a few %).
- **Timing**: no OpenSTA/OpenROAD offline, so Fmax is **estimated** from the ABC
  critical-path / `ltp` logic-depth × a typical sky130-HD per-stage delay; it is
  a pre-P&R estimate (synth-only, no wire load), to be replaced by an OpenLane/
  OpenSTA number in the calibration phase. The *location* of the critical path
  (the fully-associative CAM compare in the IOTLB/PWC lookup) is the actionable
  Phase-1 finding for lookup-mode/pipelining work.
- **Power**: dynamic power needs activity annotation (VCD) + OpenSTA, deferred to
  the estimate↔synth calibration phase; Phase-1 reports area + timing. This ties
  directly into the simulator's per-module normalized PPA (the calibration target).
- An OpenLane `config.json` is provided in `syn/openlane/` for a full P&R PPA run
  where docker + OpenLane are available (out of scope for this offline run).
