# PPA — Full config (Phase 1, sky130 `sc_hd` tt_025C_1v80)

Config `full` = single-stage (MODE=s1_only), COALESCE_FACTOR=8, NUM_WALKERS=4,
BUFFER_DEPTH=16, MEM_MAX_OUTSTANDING=8, IOTLB 64e/4-way, S1 PWC 16e fully-assoc.
Synthesized with Yosys (`abc -liberty`, sky130 `sc_hd` typical corner). All cache
storage maps to flip-flops (no SRAM macro mapping yet — that is the later
storage-pattern sub-experiment).

## Area (per module)

| module (RTL block) | area µm² | share | note |
|---|---:|---:|---|
| `cache_store` — IOTLB (64e / 4-way) | 294,847 | 56 % | combined IOVA→SPA, line-keyed; FF-mapped |
| `txn_buffer` — buffer + MSHR + lookup FSM | 133,780 | 25 % | control state (no 4 kB payload) |
| `cache_store` — S1 PWC (16e / fully-assoc CAM) | 84,522 | 16 % | (S2 PWC same params, largely opt-away: preload-only) |
| `walker` × 4 | 10,772 | 2 % | 2,693 each (state box + addr-gen) |
| `walk_engine` — arbiter / mux / demux | 1,907 | <1 % | |
| `mem_if` — AXI read master | 231 | <1 % | |
| **total (`cfg_full`)** | **526,057** | 100 % | ~0.53 mm² (pre-P&R) |

**Finding:** the translation **caches dominate area (~72 %)**, and ~67 % of all
cells are sequential — because every cache array maps to flip-flops/CAM (no SRAM
macro). This is exactly the simulator's prediction (buffer/cache storage is the
cost driver) and motivates the **SRAM-macro storage pattern** and **smaller leaf
IOTLB** (design_premises: spend area on PWC + coalescing, keep the leaf IOTLB
small) in the next iteration.

## Timing — critical path

No OpenSTA/OpenROAD offline, so a calibrated Fmax is pending the OpenLane/OpenSTA
flow (`syn/openlane/`). Yosys `ltp` on the flattened generic netlist gives the
**critical-path location and depth**, which is what guides the next step:

- **depth:** 713 generic logic levels (pre-tech-map; not a calibrated delay).
- **path:** `mem_if.can_issue` → `walk_engine` arbiter (`any_free`, walker
  `state`/`busy`) → `txn_buffer` lookup FSM (`lstate`) → **S1 PWC associative
  (CAM) lookup** (`u_s1pwc.lookup_en` → `hit`).

**Finding / next step:** the bottleneck is the **single-cycle fully-associative
CAM lookup combined with cross-block combinational valid/ready + priority
encoders** (arbiter `any_free`, `coalesce_hit` scan, IOTLB/PWC compare). To close
400 MHz this must be **pipelined / registered**: register the CAM compare output
(already 1-cycle in the wrapper, but the compare feeds combinationally into the
arbiter+FSM), deepen `PIPELINE_DEPTH`, and break the cross-module combinational
path. This is the Phase-2 lookup-mode / pipelining work the spec calls for.

## Power
Dynamic power needs activity annotation (VCD from the cocotb run) + OpenSTA;
deferred to the estimate↔synth calibration phase, where it is cross-checked
against the simulator's per-module normalized power. Area + critical path are the
Phase-1 synthesis deliverables.

Raw logs: `results/full_area.txt`, `results/full_ltp.txt`, `results/full.json`.
