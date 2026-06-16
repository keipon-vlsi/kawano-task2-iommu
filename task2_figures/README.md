# TASK2 — Nested-IOMMU microarchitecture validation figures

Reproducible figure/data generator for the nested-IOMMU design-validation study.
Data is **representative/mock** except where a formula is given (Fig 1, 3, 5, 6 are
computed; Fig 2 and 4 use the spec's representative vectors). All hardcoded vectors
live in clearly-named variables near the top of `generate_figures.py` so they can be
swapped for real simulator output later.

## Run

```bash
python generate_figures.py        # (re)produces every CSV + PNG with fixed seed (0)
```

Requires Python 3 + `numpy`, `pandas`, `matplotlib` only. Outputs:
- data → `./data/*.csv` (the exact numeric series plotted)
- figures → `./figures/*.png` (dpi=150)

## Global model constants (top of script)

`T_arr_ns=40`, `T_mem_ns=100`, `T_mem_req=2.5`, `mem_bw_8=3`, `wire_rate=1.0`,
`PAGES={'4KB':1,'2MB':512,'1GB':262144}`.

## Figures

| Figure | PNG | CSV(s) | Description |
|---|---|---|---|
| **Fig 1** Sizing (2 panels) | `figures/fig1_sizing.png` | `data/fig1a_walkers_throughput.csv`, `data/fig1b_buffer_throughput.csv` | Throughput vs #walkers (knee at N_req per cache level) and vs buffer depth — coalescing cuts walkers ~8x but not buffer; prefetch cuts buffer. |
| **Fig 2** Access/latency reduction (2 panels) | `figures/fig2_reduction.png` | `data/fig2a_ablation.csv`, `data/fig2b_combine_split.csv` | (A) mem-accesses/translation across the cache ablation (~8x drop at coalescing); (B) combine vs split vs no-combine latency for symmetric/asymmetric Pv,Pg (winner highlighted). |
| **Fig 3** Warm-up transient | `figures/fig3_warmup.png` | `data/fig3_warmup.csv` | Throughput vs pages since cold start, prefetch on vs off; warm-up gap to 99% wire rate (~9 vs ~115 pages). |
| **Fig 4** PPA (2 panels) | `figures/fig4_ppa.png` | `data/fig4a_pareto.csv`, `data/fig4b_breakdown.csv` | (A) area–energy Pareto with frontier and the chosen 1proc/1dev point (tag-reduction arrow); (B) per-arch GE breakdown — buffer dominates, tags→~0 for the chosen arch. |
| **Fig 5** Sensitivity / robustness (2 panels) | `figures/fig5_sensitivity.png` | `data/fig5a_degradation.csv`, `data/fig5b_resource_stress.csv` | (A) throughput degradation under contexts (graceful), invalidation (moderate), IOVA-random (cliff); (B) which resource each perturbation stresses. |
| **Fig 6** Prefetch lead D (grounded model) | `figures/fig6_prefetch_lead.png` | `data/fig6_prefetch_lead.csv` | 9-cell (Pv×Pg) heatmap of the just-in-time prefetch lead D (requests before the 1GB boundary), computed from the steady-rate / spare-bandwidth model; budget-bound for 4KB/4KB. |

## Simulator structure diagrams

`python generate_diagrams.py` (matplotlib only) produces two explanatory diagrams:

| Diagram | PNG | Description |
|---|---|---|
| Module architecture | `figures/sim_architecture.png` | engine/policy split — `config.py` → `engine.py` event-driven core ← swappable policies (`workload/memory/caches/walker/prefetch`) → outputs (`run/estimator/sweep`). |
| Request lifecycle | `figures/sim_request_flow.png` | event-driven path of one request: arrival → buffer/prefetch → `_translate` (IOTLB→MSHR→warm fast-path→walk) → cost model (deepest-first, n accesses) → memory/walker → fill caches → complete; with per-path latency (IOTLB 7.5 ns, PWC-hit 10 ns, walk 12.5+100·n ns) and the resource caps (walkers, MSHR, buffers, outstanding). |

## Notes

- **Fig 6** is fully computed from the model in the task spec (steady-rate, spare
  bandwidth, boundary access counts → `lead = max(latency, budget)`). The CSV also
  carries `D_2MB`, `steady_per_8`, `spare_per_page`, `A_2MB`, `A_1GB`. `D_2MB` is
  blank where the 2MB boundary is absent (Pv=Pg=1GB). Sanity checks asserted in
  code: `4KB/4KB → A_2MB=7, A_1GB=12, D_2MB=56, D_1GB=96`; `1GB/1GB → steady≈0,
  D_1GB=6`.
- Fig 4(B) is drawn as a stacked **bar** (the spec's "stacked area breakdown" over 3
  discrete architectures); GE numbers are plausible/representative and sum to the
  Fig 4(A) areas.
- Swap the named vectors (`acc`, `lat`, `ge`, `pts`, `VM_*`, `G_*`, `N_req`,
  `B_req`, …) for real simulator output to re-ground any figure.
