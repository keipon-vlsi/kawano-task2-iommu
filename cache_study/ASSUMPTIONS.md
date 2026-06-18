# ASSUMPTIONS — cache_study (PWC / IOTLB lookup microarchitecture QoR study)

Isolated cache **lookup + fill datapath** only (no IOMMU/walker/memory IF). QoR comparison.

## Fixed across all variants (fairness)
- **Storage = DFF** (flip-flops), all in logic. No SRAM, no behavioral assoc arrays.
- Entry counts: **PWC = 2 entries**; **IOTLB = 16 entries = 2 lines × 8 pages**.
- Sv39 / 4 KB pages: VPN[2],[1],[0] = 9 bits each.
  - **PWC tag = VPN[2:1] = 18 bit**; value = base SPA, PPN = **44 bit**.
  - **IOTLB tag = VPN[2:0] = 27 bit**; value = data SPA, PPN = **44 bit**.
- **Context tags (device_id/PASID) OFF by default**; optional 2nd sweep parameter (CTX).
- Implement **both lookup and fill**; both paths synthesized.
- Real combinational logic (comparators / muxes / priority / adders); no SystemVerilog
  associative arrays, no behavioral lookup.

## Common I/O (so one testbench + one timing wrapper is fair across variants)
Every PWC variant = module with ports:
`clk, rst_n, lk_tag[17:0], lk_hit, lk_spa[43:0], fill_en, fill_tag[17:0], fill_spa[43:0]`.
Every IOTLB variant: same but `lk_tag[26:0]`, `fill_tag[26:0]`.
`lk_hit`/`lk_spa` are **combinational**; the synth harness wraps the DUT with **input and
output registers** so the measured path is a clean reg→reg "lookup" path
(`req-reg / storage-FF → lookup logic → resp-reg`). This isolates lookup logic depth.

## Synthesis / measurement
- sky130_fd_sc_hd, tt 025C 1v80. Yosys (synth + abc -D speed target) → stat (area, cell
  count, DFF count). OpenSTA (create_clock, report_checks/worst_slack) → Fmax & critical path.
- **Fmax** = 1000 / (P − worst_slack) [MHz] at clock period P (ideal wireload / no place).
- **論理段数 (logic depth)** = number of combinational cell stages on the worst reg→reg path
  (parsed from `report_checks`; buffers/inverters counted, noted separately where relevant).
- **Area breakdown**: storage DFF (exact, from stat) vs combinational (rest); compare/mux/
  control attributed qualitatively from the datapath structure.
- Functional check: iverilog, sequential (contiguous) IOVA trace for all; + random trace for
  the fully-associative variants (P0, T5). Checks hit/SPA and a few fills.

## Bets vs robust
- Variants that **bet on contiguous IOVA** (sequential/pointer P3,T2; base+offset T4; FIFO X6;
  even-window P2,T1) are marked. Their **fallback** = a full tag-compare path that still
  produces correct hit/miss on non-sequential input (slower, never wrong).
