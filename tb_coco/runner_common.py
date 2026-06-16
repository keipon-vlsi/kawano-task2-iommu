"""Shared Verilator+cocotb runner for the per-config testbenches.

Each cfgN/tb_coco/run.py imports run_cfg() and passes its config name, the toplevel
wrapper file, and the environment knobs the shared test (iommu_tb.py) reads.
"""
import os
import sys
from pathlib import Path
from cocotb_tools.runner import get_runner

ROOT = Path(__file__).resolve().parent.parent
RTL = ROOT / "rtl"
CORE = [RTL / f for f in ["iommu_pkg.sv", "fa_cache.sv", "mem_master.sv",
                          "prefetch_ctrl.sv", "iommu_top.sv"]]
WAIVERS = ["-Wno-PINCONNECTEMPTY", "-Wno-IMPORTSTAR", "-Wno-UNUSEDSIGNAL",
           "-Wno-DECLFILENAME", "-Wno-UNUSEDPARAM", "-Wno-WIDTHEXPAND",
           "-Wno-WIDTHTRUNC", "-Wno-GENUNNAMED", "-Wno-CASEINCOMPLETE"]


def run_cfg(cfg_name, top_module, wrapper_sv, env):
    for k, v in env.items():
        os.environ[k] = str(v)
    os.environ["CFG_NAME"] = cfg_name
    sources = [str(s) for s in CORE if s.exists()] + [str(wrapper_sv)]
    runner = get_runner("verilator")
    runner.build(sources=sources, hdl_toplevel=top_module,
                 build_args=["--timing"] + WAIVERS, always=True)
    # point cocotb at the shared test module
    sys.path.insert(0, str(ROOT / "tb_coco"))
    os.environ.setdefault("COCOTB_TEST_MODULES", "iommu_tb")
    runner.test(hdl_toplevel=top_module, test_module="iommu_tb",
                test_dir=str(Path(wrapper_sv).resolve().parent.parent / "tb_coco"))
