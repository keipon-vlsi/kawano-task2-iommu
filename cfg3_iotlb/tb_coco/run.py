"""cfg3 (PWC + IOTLB + coalesce) cocotb runner."""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tb_coco"))
from runner_common import run_cfg
if __name__ == "__main__":
    run_cfg(cfg_name="cfg3_iotlb", top_module="cfg3_top",
            wrapper_sv=HERE.parent / "cfg3_top.sv",
            env=dict(CO=8, NUM_WALKERS=1, BUFFER=5, HAS_PWC=1, HAS_IOTLB=1,
                     PREFETCH=0, TAG_CONTEXT=1, N_REQS=296, MEM_LATENCY=40))
