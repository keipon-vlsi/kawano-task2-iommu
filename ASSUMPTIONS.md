# ASSUMPTIONS

Recorded while adding the area & power estimator (Task 2). Each item is a place
where the repo state differed from the task brief; I made a reasonable choice and
continued.

## Repo state
- **No `CLAUDE.md`** exists in the repo. The brief said to read it for the fixed
  conditions and the "A–E validation trends". I took the A–E scenarios and their
  expected numbers from the simulator's own `run.py` (they are literally scenarios
  A–E there) and captured a baseline run before touching anything. Those numbers are
  now asserted in `test_estimator.py::test_perf_trends_unchanged`.
- **The Python simulator source lived in `simulator/`, not `iommu_sim/`.** At start,
  `iommu_sim/` contained only a stale `__pycache__/` (compiled `caches/engine/memory/
  prefetch/walker/workload` — the exact module set in `simulator/`). The committed
  source (`FIRST COMMIT`) is under `simulator/`. I reconstituted `iommu_sim/` by
  copying the committed `simulator/*.py` into it, so the acceptance command
  `cd iommu_sim && python3 run.py` works. `simulator/` is left untouched as the
  original; all estimator work is in `iommu_sim/`.
- The PyMTL tree (`iommu_sim_pymtl/`) also has only `__pycache__/` (no `.py`); it was
  not in scope and not used.

## Estimator design choices
- **No formal `Config` class** in the simulator (it is assembled with kwargs in
  `run.py`). I introduced `EstimatorConfig` (structural sizing only) as the "static
  config" the estimator consumes, and build it in `run.py` from the same kwargs.
- **DDT$, PDT$, MSI$ are not modelled by the current single-stage simulator.** The
  brief requires them in the component breakdown, so they are included with sensible
  default sizes (area + leakage counted) but **zero dynamic activity** (no access
  counter exists for them yet). This is flagged in the output and in `ESTIMATOR_ja.md`.
  When those structures are simulated, wire their hit/miss/insert counters into the
  `components` dict and dynamic power appears automatically.
- **No transaction-buffer object** exists (the engine tracks a `buffer` integer). I
  size the buffer FF area from the provisioned depth: explicit `buffer_size` if set,
  else the measured `peak_buffer`. Same for `num_walkers` (explicit, else `peak_walks`).
- **Activity counters added are additive only** (`SetAssocCache.inserts`,
  `Metrics.walker_busy_ns`); they do not change any policy or the A–E perf numbers
  (verified — baseline run is byte-identical, and the perf-trend test passes).
- Tech constants are the **sky130 SEED placeholders** from the brief, marked `REFINE`
  in `TechParams`. This estimator is first-order: for relative comparison + later
  calibration vs. synthesis, not absolute sign-off.
