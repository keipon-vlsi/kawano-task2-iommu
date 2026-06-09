# IOMMU flow report — `full` on `sky130_fd_sc_hd`

- clock target: **400.0 MHz** (2.5 ns)  ·  corner `tt_025C_1v80`  ·  git `8e437519f8`
- tools: yosys `Yosys 0.65 (git sha1 b85cad634, g++ 13.3.0-6ubuntu2~24.04.1 -fPIC -O3)` · openroad `26Q2-1164-g08f67ee5e` · magic `8.3.642` · klayout `KLayout 0.30.8`

## Stage pass/fail
| stage | status |
|---|---|
| synth | ✅ pass |
| ppa_stages | ✅ pass |

## PPA across stages
| stage | area | Fmax | power |
|---|---|---|---|
| post-synthesis | 616711 um² (cells) | 18.9 MHz | 0.336 W |

## Power: default vs VCD-annotated (gate-level)
| activity | internal | switching | leakage | total (W) |
|---|---|---|---|---|

> VCD annotation unavailable (RTL/gate net-name mismatch after flatten); annotated power falls back to default. See USAGE_flow.md.

## Signoff (signoff/*.rpt)
- `drc.rpt` · `hold.rpt` · `timing_worstN.rpt` · `clock.rpt` · `wirelength.rpt` · `congestion.rpt`
