# IOMMU Microarchitecture Exploration

Pre-training Program 2 (Tenstorrent / ADIP). This project explores, simulates, and
will synthesize an IOMMU that sustains wire-rate DMA address translation for an
800GbE NIC. Task spec: `doc/tt_pretraining_task2.md`.

## Goal & deliverables
- **Report**: IOMMU performance requirements + a proposed microarchitecture to meet them.
- **Simulator + results**: simulate the IOMMU caching mechanisms; report hit/miss
  rates and timing for a sample request trace.
- Report MUST contain:
  - (3a) Address-translation latency **without** caching.
  - (3b) Latency **with** IOATCs (IOTLB, DDT cache, PDT cache, MSI translation cache).
  - (3c) **Parallel page-table-walk count** to sustain wire rate, assuming an infinite
    transaction buffer at the I/O bridge.
  - (3d) **Minimum buffer** to sustain wire rate, assuming sufficient parallel walkers.

## Fixed conditions (the spec)
- 800GbE NIC over PCIe Gen6 x16 (128 GB/s). Continuous 4 kB page DMA to a contiguous address.
- IOMMU clock **400 MHz** (2.5 ns/cycle). Main memory via AXI, **100 ns** latency.

## Derived key numbers (keep these handy)
- Effective wire rate = 800 Gbps = **100 GB/s** (Ethernet is the bottleneck; PCIe 128 GB/s is not).
- Translations/s = 100e9 / 4096 = **24.41 M/s**. Inter-arrival = **40.96 ns ≈ 16.4 cycles**.
- No-cache 3-level walk = 3 × 100 ns = **300 ns**.
- Little's law: required walkers N ≈ 300 / 40.96 ≈ **8**; required buffer ≈ **8**.
- 64 B cache line = **8 PTEs** (PTE = 8 B) ⇒ leaf coalescing factor = 8.
- Nested (Sv39 + Sv39x4) cold 2D walk = (3+1)(3+1)−1 = **15** memory accesses.

## Roadmap (work top-down: finish phase 1, then iterate)
1. **Minimal deliverables**: 3a–3d numbers + a working simulator + the report.
2. **Architecture exploration**:
   - Caches: PWC; direct / set-associative / fully-associative; per-level structures (IOTLB/PWC/DDT$/PDT$/MSI$).
   - Prefetch: next-line, stride, RPT, DCPT, SMS.
   - Parallel walk; pipelining; buffer sizing.
3. **Performance sensitivity**: non-monotonic IOVA; device/process context switch;
   bounded memory accept (outstanding) count.
4. **SkyWater (sky130) synthesis**: quantitative power/area + relative per-arch
   comparison; meet clock & wire rate via pipeline optimization and cache-structure
   optimization.

## Established findings / design principles (reuse these)
- **Latency is hidden, not reduced.** Concurrency required = latency / inter-arrival
  (Little's law). Implement that concurrency as N parallel walkers OR one pipelined
  engine with N contexts (= N outstanding tagged memory reads). A "walker" is mostly
  a small state box + the right to hold one outstanding memory read; not an ALU.
- **PTE coalescing is the biggest cheap win.** DRAM is read in 64 B bursts; latency
  is per-row-activation, not per-64 B. One 64 B read returns 8 leaf PTEs ⇒ ~1 access
  per 8 pages with PWC warm. It depends only on **PTE location contiguity** (= IOVA
  sequential), independent of where data sits.
- **Location vs content locality (do not conflate):** contiguous IOVA ⇒ leaf PTEs
  contiguous ⇒ coalescing works. Contiguous physical **data** ⇒ superpages possible.
  These are independent.
- **Leaf IOTLB has ~no reuse for streaming**; spend area on PWC + coalescing, not on
  a big leaf IOTLB. Upper-level PTEs/PWC are reused for long stretches (leaf table
  2 MB, L1 1 GB, root 512 GB).
- **Nested:** each translation stage caches by its **input** (VS by IOVA, G-stage by
  GPA). SPA scatter does not hurt translation caching; it only affects superpages and
  DRAM row locality. GPA locality is two-layer: data GPA = spatial (sequential), table
  GPA = temporal (few, heavily reused). Both come from the S1 PTE contents.
- **In steady state** DDTW/PDTW/S2-table accesses are ~all hits (single context); the
  only moving residual is the data-leaf fetch stream. So the real design levers are
  **coalescing → covering residual misses (parallel walk / prefetch) → buffer sizing**.
- **Replacement policy is near-moot** for monotonic IOVA (no reuse). The real lever is
  **structural per-level cache separation** (IOTLB/PWC/DDT$/PDT$) so streaming leaf
  traffic cannot evict hot upper entries.
- **Robustness:** prefer "free wins" (coalescing, PWC) over "bets" (prefetch, tiny
  context caches, minimal walker count). Make bets self-disable (confidence-throttled
  prefetch). Keep a correct general fallback path so deviations slow down, not break.
- **Sim ≠ timing.** The performance sim proves the architecture has enough concurrency;
  it does NOT prove 400 MHz closes after synthesis/P&R. Margins: aim synth Fmax ≈
  1.3–1.5× spec; treat usable cycle budget as ~70%; provision N/buffer/MSHR +50–100%
  over idealized peak; keep walk memory BW ≤ ~50%. Prefer modeling extra per-stage
  cycles + memory-latency tail over a blanket fudge factor.
- **sky130 note:** 400 MHz on the IOMMU logic is achievable but aggressive for a
  standard-cell/open flow (comfortable target ~100–250 MHz); needs deep pipelining and
  modest single-cycle caches. PCIe Gen6 / 800GbE PHYs cannot be built in 130 nm, but
  they are out of scope (this task is the IOMMU digital block only). Good for
  prototyping the flow; a product would target ≤16 nm.

## Repository layout
- `iommu_sim/` — reference **Python event-driven** simulator (swappable policies).
- `iommu_sim_pymtl/` — **PyMTL3 cycle-level** version (build per `pymtl_simulator_prompt.md`).
- `IOMMU_sizing_model.xlsx` — Little's-law sizing calculator (change inputs → N, buffer).
- `pymtl_simulator_prompt.md` — self-contained spec/prompt for the PyMTL build.
- `.claude/skills/iommu-arch-sweep/` — skill for standardized design-space sweeps.

## Simulator (`iommu_sim/`) — run & extend
- Run: `cd iommu_sim && python3 run.py` (prints scenarios A–E).
- Architecture: `engine.py` (event loop; transaction buffer, walker pool, MSHR
  coalescing, metrics) + swappable policies: `caches.py` (SetAssocCache + LRU/FIFO/
  Random), `prefetch.py`, `walker.py` (SingleStageCost / NestedCost), `memory.py`,
  `workload.py` (sequential/random/multi_stream). Wire it up in `run.py`.
- To change a parameter: edit `run.py` args (num_walkers, buffer_size, iotlb/pwc assoc,
  coalesce, prefetcher, trace) — or subclass the relevant ABC to add a new policy.
  Do not edit `engine.py` for policy changes.
- **Measure peaks under infinite resources** (num_walkers=None, buffer_size=None):
  `peak_walks` = required N (3c), `peak_buffer` = required buffer (3d).

### Validation trends the simulator must reproduce (8000 sequential reqs, 100 GB/s)
| scenario | mem/page | peak walks (N) | peak buffer | avg lat | wire rate |
|---|---|---|---|---|---|
| A no-cache | 3.0 | 8 | 8 | 300 ns | met |
| B PWC + coalescing | ~0.13 | 1 | 8 (cold-start) | ~25 ns | met |
| C B + prefetch | ~0.13 | ~3 | 8 | ~2.7 ns | met |
| D random IOVA (B cfg) | ~2.0 | 8 | 8 | ~200 ns | met |
| E no-cache, walkers=4 buffer=4 | 3.0 | 4 | 4 | 300 ns | **NOT met (13.3 M/s)** |
Exact numbers may vary (cycle-level vs event-driven); orders/trends must hold.

## Conventions
- Simulator in **Python and/or PyMTL3**. **Code and code comments: English.**
  Explanatory docs (USAGE/EXPLAIN): **Japanese**.
- After architecture exploration: RTL implementation, then synthesis on **sky130**.
- Keep the engine/policy separation. A new design option = a new module behind an ABC
  + a config field, so it can be swapped without touching the engine.
- Always include a verification step (reproduce validation trends; cross-check against
  Little's law: N ≈ avg_latency / inter_arrival).

## Glossary
IOVA (device DMA address) → GPA (guest physical, intermediate) → SPA (host physical).
IOATC = umbrella for IOMMU translation caches. IOTLB (final leaf), PWC (page-walk /
upper-level), DDT$/PDT$ (device/process context, RISC-V IOMMU), MSI$ (interrupt).
Coalescing = fetch a 64 B line = 8 PTEs in one access. MSHR = tracks in-flight line
fetches so duplicate misses share one fetch. Sv39 = 39-bit, 3-level; Sv39x4 = G-stage
(2nd stage) with 11-bit root (16 KiB root table). 2D walk = nested two-stage walk.