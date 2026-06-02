---
name: iommu-arch-sweep
description: Run a standardized IOMMU design-space exploration sweep with the project simulator (Python iommu_sim/ or PyMTL iommu_sim_pymtl/) and produce a comparison table, performance-sensitivity analysis, and an interpretation. Use whenever comparing or evaluating IOMMU design options — caches (PWC, direct/set-assoc/full), prefetchers (next-line, stride, RPT, DCPT, SMS), parallel-walker count, pipelining, or buffer sizing — or running sensitivity experiments (non-monotonic IOVA, device/process context switch, bounded memory outstanding). Also use to evaluate a newly added policy/component against the baseline.
---

# IOMMU architecture sweep

A repeatable procedure for exploring the IOMMU design space and reporting results
consistently. Read `CLAUDE.md` first for project context, fixed conditions, derived
numbers, and the validation trends.

## When to use
- Comparing cache structures, replacement policies, prefetchers, walker counts,
  pipelining, or buffer sizes.
- Evaluating a new component just added to the simulator.
- Running performance-sensitivity experiments.

## Procedure

1. **Confirm the baseline reproduces.** Run the simulator's demo (`iommu_sim/run.py`
   or the PyMTL demo) and check the validation trends in `CLAUDE.md` still hold
   (no-cache ⇒ peak walks ≈ 8, buffer ≈ 8, 300 ns; PWC+coalescing ⇒ ~0.13 mem/page,
   N ≈ 1). If not, fix before sweeping.

2. **Define the sweep matrix.** Vary one axis at a time around a named baseline.
   Typical axes:
   - cache: IOTLB assoc {0, direct, 4, 16, full}; PWC assoc {0, 4, 16, full}; coalesce {1, 8, 16}.
   - prefetch: {none, next-line, stride/RPT, DCPT, SMS} × distance.
   - walkers N: {1, 2, 4, 8, 16} (and None=infinite to read the required peak).
   - buffer: {1, 2, 4, 8, 16, None}.
   - pipeline: per-stage lookup latency in cycles.
   Record the exact config of each run.

3. **Always run BOTH resource modes:**
   - **Unlimited** (num_walkers=None, buffer_size=None) → read `peak_walks` (= required
     N, report item 3c) and `peak_buffer` (= required buffer, 3d).
   - **Finite** (the candidate sizing) → check whether wire rate (24.41 M/s) is sustained.

4. **Always run the sensitivity workloads**, not just sequential:
   - sequential IOVA (best case), random IOVA (worst case), multi_stream (interleaved).
   - If relevant: device/process context switching and bounded memory outstanding.
   Report each design point on BOTH best and worst case to expose cliffs.

5. **Produce a standardized comparison table** with columns:
   config | mem accesses/page | IOTLB hit / coalesced / true-walk | required N (peak
   walks) | required buffer (peak) | avg & p99 latency (ns) | achieved vs target
   throughput (M/s) | wire-rate met? Save as CSV and render as a table.

6. **Interpret using the project framework** (from CLAUDE.md):
   - Cross-check N against Little's law: N ≈ avg_latency / inter_arrival (40.96 ns).
     Note peak ≥ Little-average (size for peak).
   - Classify each optimization as a **free win** (worst case = no speedup, e.g.
     coalescing/PWC) or a **bet** (can cliff, e.g. prefetch/tiny caches/minimal N).
   - Identify **cliffs vs graceful degradation**: where does wire rate break, and does
     performance degrade smoothly under workload deviation?
   - Distinguish **steady-state vs cold-start** transients in peak buffer.

7. **Tie back to the report** (3a–3d) and, when synthesis is in play, flag the
   area/power/timing implications (bigger/more-associative caches and more walkers cost
   area & critical-path timing on sky130; apply the margin guidance in CLAUDE.md).

## Output
- A CSV of all runs + a rendered comparison table.
- A short written interpretation (free-win vs bet, cliffs, recommended sizing with
  margin, and which axis gives the most performance per area).
- Keep simulator code/comments in English; write the interpretation in Japanese.

## Guardrails
- Vary one axis at a time; keep a fixed named baseline for comparison.
- Never read a single workload only — always pair best (sequential) and worst (random).
- Required N / buffer come from the **unlimited-resource** peaks, not from a finite run.
- Do not edit `engine.py` to change a policy; swap the policy module / config.