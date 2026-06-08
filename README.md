# IOMMU Microarchitecture Exploration

End-to-end flow for the 800 GbE IOMMU study: a cycle-approximate **architecture
explorer** (Python) → a **parameterized synthesizable RTL** (SystemVerilog) →
**sky130 synthesis**, with a cocotb sim↔RTL cross-check tying them together.

```
 iommu_sim/ (Python)            rtl/ (SystemVerilog)            syn/ (sky130)
 explore architectures,   -->   one parameterized design,  -->  per-config PPA
 answer 3a-3d, PPA Pareto       config = parameter set           area / Fmax / crit-path
        |                              ^                                ^
        +----- trace + frozen PPA -----+----- cocotb cross-check -------+
                                   tb_coco/
```

## Layout
| dir | what |
|---|---|
| `iommu_sim/` | cycle-approximate event-driven explorer (3a–3d, min-HW, PPA Pareto, FoM). See `iommu_sim/README.md`. |
| `rtl/` | parameterized synthesizable SV: the 5 core blocks + top (`iommu_core`). |
| `tb_coco/` | cocotb (Verilator) happy-path testbench + stub memory + sim↔RTL cross-check. |
| `syn/` | sky130 synthesis flow (`synth.py`, Yosys + sv2v) + OpenLane scaffold. |
| `results/` | synthesis PPA (area / critical path) per config. |
| `simulator_*.md`, `design_premises.md`, `CLAUDE.md` | the design contract / rationale. |
| `ASSUMPTIONS.md` | decisions & simplifications (simulator phase **and** RTL phase). |

## Quick start

### 1. Architecture exploration (Python; stdlib + PyYAML)
```bash
cd iommu_sim
python3 run.py   --config configs/baseline.yaml                 # 3a-3d + PPA for one config
python3 run.py   --config configs/baseline.yaml --measure peaks # 3c/3d + cold-start HW
python3 sweep.py --config configs/space.yaml    --pareto        # area-energy Pareto + CSV
../.venv/bin/python -m pytest -q                                # validation tests
```

### 2. RTL + cocotb cross-check (Verilator)
```bash
cd tb_coco
../.venv/bin/python run.py        # builds iommu_core, runs happy path, sim<->RTL walk-count check
```
Passes: all 256 translations complete with correct per-page SPAs; RTL walk count
(32 = 256/8 lines) matches the Python reference sim run in-process.

### 3. sky130 synthesis (Yosys + sv2v, sky130 PDK via volare)
```bash
export PDK_ROOT=$PWD/pdk
../.venv/bin/python syn/synth.py full        # -> results/full.json, full_area.txt, ppa_full.md
```
Full config: **~526 k µm²** (sky130 sc_hd tt), caches dominate (~72 %, all FF-mapped);
critical path = the single-cycle CAM lookup + arbiter + FSM (→ pipeline next).
See `results/ppa_full.md`. A full P&R run uses `syn/openlane/config.json`.

## Toolchain (project `.venv`, no system pip)
Verilator 5.046, cocotb 2.0.1, sv2v 0.0.13, yowasp-yosys 0.66, sky130 PDK (volare),
PyYAML / pytest / matplotlib. See `ASSUMPTIONS.md` for install notes (cocotb is
force-installed for Python 3.14; OpenSTA/OpenROAD are not available offline, so
synth Fmax is pending the OpenLane/OpenSTA calibration phase).

## Status
- **Simulator**: complete — answers 3a–3d, min-HW, per-module normalized PPA, Pareto/FoM, sensitivity; tests pass.
- **RTL Phase 1**: complete — clean parameterized SV (5 blocks + top), cocotb happy
  path + sim↔RTL cross-check passing, Full config synthesizes on sky130 with
  per-module area + critical-path identified.
- **Next**: 4-config sweep, sub-experiments (PIPELINE_DEPTH, ff/sram/mixed storage,
  clock-gating, assoc), pipeline the lookup to close 400 MHz, estimate↔synth calibration.
