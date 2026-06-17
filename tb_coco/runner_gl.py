"""Gate-level (post-synthesis netlist) cocotb runner — drives the SAME iommu_tb test
on the synthesized sky130 netlist and dumps a VCD, for VCD-annotated power analysis.

Sources = sky130_fd_sc_hd functional models + the hierarchical gate netlist. The TB only
touches top-level ports (clk/req/rsp/AR-R/pl), which the netlist preserves, so the
identical happy-path test runs unchanged. waves=True makes Verilator dump dump.vcd with
real per-net toggle activity (hierarchy preserved -> per-module power downstream).
"""
import os
import sys
from pathlib import Path
from cocotb_tools.runner import get_runner

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "pdk/sky130A/libs.ref/sky130_fd_sc_hd/verilog"
WAIVERS = ["-Wno-fatal", "-Wno-WIDTHEXPAND", "-Wno-WIDTHTRUNC", "-Wno-UNUSEDSIGNAL",
           "-Wno-UNUSEDPARAM", "-Wno-DECLFILENAME", "-Wno-PINMISSING", "-Wno-IMPLICIT",
           "-Wno-TIMESCALEMOD", "-Wno-MULTIDRIVEN", "-Wno-CASEINCOMPLETE",
           "-Wno-SELRANGE", "-Wno-WIDTHCONCAT", "-Wno-UNOPTFLAT"]


def run_gl(cfg_name, top_module, netlist_sv, env, sim_dir, test_dir):
    for k, v in env.items():
        os.environ[k] = str(v)
    os.environ["CFG_NAME"] = cfg_name
    Path(test_dir).mkdir(parents=True, exist_ok=True)
    sources = [str(MODELS / "primitives.v"), str(MODELS / "sky130_fd_sc_hd.v"),
               str(netlist_sv)]
    runner = get_runner("verilator")
    runner.build(sources=sources, hdl_toplevel=top_module,
                 build_args=["--trace", "--trace-depth", "99",
                             "+define+FUNCTIONAL", "+define+UNIT_DELAY=", *WAIVERS],
                 always=True, waves=True, build_dir=str(sim_dir))
    sys.path.insert(0, str(ROOT / "tb_coco"))
    runner.test(hdl_toplevel=top_module, test_module="iommu_tb",
                test_dir=str(test_dir), waves=True)
