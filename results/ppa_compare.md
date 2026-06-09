# IOMMU PPA history (append-only)

Each P&R run appends a row. Columns: synth = yosys+OpenSTA (cell area); route = last P&R stage (die area, Fmax @2.5ns target, power incl. wire RC).

| # | config | library | architecture | synth Fmax | synth area(um^2) | synth P(W) | route stage | route Fmax | die(um^2) | route P(W) | GDS |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | full | sky130_fd_sc_hd | s1_only coal=8 N=4 buf=16 pf=0 IOTLB=64/4/sram PWC=16/ff | 19.5 | 497944 | 0.292 | GROUTE | 59.4 | 561624 | 0.597 | results/full.gds |
| 2 | full | sky130_fd_sc_hs | s1_only coal=8 N=4 buf=16 pf=0 IOTLB=64/4/sram PWC=16/ff | 48.0 | 705033 | 0.517 | - | - | - | - | results/full.gds |
| 3 | full | sky130_fd_sc_hd | s1_only coal=8 N=4 buf=16 pf=0 IOTLB=64/4/sram PWC=16/ff | 19.5 | 497944 | 0.292 | GROUTE | 59.4 | 561624 | 0.597 | results/full.gds |
| 4 | full | sky130_fd_sc_hd | s1_only coal=8 N=4 buf=16 pf=0 IOTLB=64/4/sram PWC=16/ff | 19.5 | 497944 | 0.292 | GROUTE | 59.4 | 561624 | 0.597 | results/full.gds |
| 5 | full | sky130_fd_sc_hd | s1_only coal=8 N=4 buf=16 pf=0 IOTLB=64/4/sram PWC=16/ff | 19.5 | 498300 | 0.292 | GROUTE | 64.5 | 562338 | 0.592 | - |
| 6 | full | sky130_fd_sc_hd | s1_only coal=8 N=4 buf=16 pf=0 IOTLB=64/4/sram PWC=16/ff | 18.9 | 616711 | 0.336 | GROUTE | 54.5 | 696028 | 0.666 | results/full.gds |
| 7 | full | sky130_fd_sc_hs | s1_only coal=8 N=4 buf=16 pf=0 IOTLB=64/4/sram PWC=16/ff | 26.6 | 871991 | 0.563 | GROUTE | 81.2 | 974564 | 0.928 | results/full.gds |
| 8 | full | sky130_fd_sc_hd | s1_only coal=8 N=4 buf=16 pf=0 IOTLB=64/4/sram PWC=16/ff | 18.9 | 616711 | 0.336 | GROUTE | 54.5 | 696028 | 0.666 | results/full.gds |