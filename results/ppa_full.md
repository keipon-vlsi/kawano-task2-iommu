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

## Timing — Fmax / critical path (OpenSTA, IIC-OSIC-TOOLS)

From `python3 syn/synth_osic.py full` (native yosys flatten+ABC → OpenSTA, sky130
sc_hd tt, 400 MHz target). **Synthesis-only (no P&R/CTS/buffering)** — a worst
case, see caveat.

| metric | value |
|---|---|
| target | 400 MHz (2.5 ns) |
| **Fmax** | **≈19.5 MHz** (critical path 51.4 ns, WNS −48.9 ns) |
| meets 400 MHz | **no** (expected — single-cycle lookup, unpipelined) |
| critical path | reg `_58315_` → reg `_58322_` (within the lookup/arbiter/FSM cone) |

**Dominant cells on the path** (the actual bottleneck):

| cell | fanout | slew | delay |
|---|---:|---:|---:|
| `nor4_1` | **466** | 40.9 ns | **30.9 ns** |
| `nor4_1` | 74 | 6.6 ns | 5.1 ns |
| `a222oi_1` | 1 | 4.0 ns | 4.1 ns |

**Finding / next step:** one min-drive `nor4` drives **466 loads** (≈31 ns alone)
— a wide unbuffered reduction = the **fully-associative CAM "any-match" / priority
reduction** feeding the arbiter+FSM combinationally (matches the earlier `ltp`
path through `u_s1pwc` → arbiter → `lstate`). Two compounding causes: (1) the
single-cycle associative compare + cross-block combinational valid/ready is one
giant cone; (2) synth-only has no buffer tree/gate sizing, so high-fanout nets are
catastrophic. **Phase-2:** pipeline/register the CAM compare and the arbiter
(`PIPELINE_DEPTH`, `LOOKUP_MODE`), and a P&R run (CTS + sizing) will buffer the
fanout. Post-P&R Fmax will be far higher than this synth-only 19.5 MHz, but the
structural fix is pipelining.

## Power (OpenSTA, default activity)

| | internal | switching | leakage | **total** |
|---|---:|---:|---:|---:|
| W | 0.258 | 0.034 | ~1.5e-7 | **0.292 W** |

Estimated with OpenSTA default switching activity (no VCD). For an activity-true
number, feed the cocotb VCD (`read_power_activities -vcd`). Per-**module** power
needs a hierarchical-power flow (the flattened netlist loses module boundaries) —
a later refinement; the type split (internal/switching/leakage) is the Phase-1
breakdown. This is the calibration target vs the simulator's normalized power.

Raw logs: `results/full_area.txt` (per-module area), `results/full_area_flat.txt`
(total), `results/full_sta.txt` (STA path + power), `results/full.json` (all of it).
