"""cfg1 (no cache) cocotb runner. Usage: ../../.venv/bin/python run.py"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tb_coco"))
from runner_common import run_cfg

if __name__ == "__main__":
    run_cfg(
        cfg_name="cfg1_nocache",
        top_module="cfg1_top",
        wrapper_sv=HERE.parent / "cfg1_top.sv",
        env=dict(CO=1, NUM_WALKERS=37, BUFFER=37, HAS_PWC=0, HAS_IOTLB=0,
                 PREFETCH=0, TAG_CONTEXT=1, N_REQS=296, MEM_LATENCY=40),
    )
