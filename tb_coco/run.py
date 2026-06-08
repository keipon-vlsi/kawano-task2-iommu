"""cocotb runner (Verilator) for the IOMMU core happy-path test.

Usage:  ../.venv/bin/python run.py
Build parameters here define the "Full" config under test; keep COALESCE_FACTOR
in sync with the COALESCE_FACTOR env var the test reads.
"""
import os
from pathlib import Path
from cocotb_tools.runner import get_runner

RTL = Path(__file__).resolve().parent.parent / "rtl"
SOURCES = [RTL / f for f in [
    "iommu_pkg.sv", "cache_store.sv", "mem_if.sv", "walker.sv",
    "walk_engine.sv", "txn_buffer.sv", "iommu_core.sv",
]]

# Full config (single-stage, PWC + coalescing, modest N/buffer)
PARAMS = dict(
    MODE=1,                 # s1_only
    COALESCE_FACTOR=8,
    PREFETCH_EN=0,
    NUM_WALKERS=4,
    BUFFER_DEPTH=16,
    MEM_MAX_OUTSTANDING=8,
    IOTLB_ENTRIES=64, IOTLB_ASSOC=4, IOTLB_STORAGE=1,
    S1PWC_ENTRIES=16, S1PWC_ASSOC=16, S1PWC_STORAGE=0,
)


def main():
    os.environ["COALESCE_FACTOR"] = str(PARAMS["COALESCE_FACTOR"])
    os.environ.setdefault("N_REQS", "256")
    os.environ.setdefault("MEM_LATENCY", "40")
    runner = get_runner("verilator")
    runner.build(
        sources=[str(s) for s in SOURCES],
        hdl_toplevel="iommu_core",
        parameters=PARAMS,
        build_args=["--timing", "-Wno-WIDTHEXPAND", "-Wno-WIDTHTRUNC",
                    "-Wno-UNUSEDPARAM", "-Wno-UNUSEDSIGNAL", "-Wno-DECLFILENAME"],
        always=True,
    )
    runner.test(hdl_toplevel="iommu_core", test_module="test_iommu")


if __name__ == "__main__":
    main()
