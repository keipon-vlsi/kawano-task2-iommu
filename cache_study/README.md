# cache_study — PWC / IOTLB lookup microarchitecture QoR study

Isolated cache **lookup + fill** datapaths (no IOMMU). Each variant is one synthesizable
SystemVerilog module with a **common I/O** (so one testbench + one timing wrapper are fair).
DFF storage; sky130_fd_sc_hd. See `ASSUMPTIONS.md`. Report: `REPORT.md` (日本語).

## Layout
- `pwc/` — PWC variants (2 entries, tag = VPN[2:1] 18b). `iotlb/` — IOTLB variants (16 = 2×8, tag 27b).
- `tb/` — shared testbenches (`pwc_tb.sv`, `iotlb_tb.sv`; iverilog `-DDUT=<module>`).
- `syn/` — `run_qor.py` (functional + Yosys synth + OpenSTA + OpenROAD repair), `filter_lib.py`.
- `results/` — per-variant `.json` + raw logs; `summary.json`.

## Run
    python3 cache_study/syn/run_qor.py            # all variants
    python3 cache_study/syn/run_qor.py pwc_p0 ... # selected
Per variant it reports: functional pass, area (µm², DFF/comb split), cell count, **Fmax**
(post-repair, sky130), **logic depth** (logic levels on the post-repair reg→reg critical
path), and the critical-path cell chain.
