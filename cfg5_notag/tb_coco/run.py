"""cfg5_notag cocotb runner."""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tb_coco"))
from runner_common import run_cfg
if __name__ == "__main__":
    run_cfg(cfg_name="cfg5_notag", top_module="cfg5_top",
            wrapper_sv=HERE.parent / "cfg5_top.sv",
            env=dict(CO=8, NUM_WALKERS=1, BUFFER=1, HAS_PWC=1, HAS_IOTLB=1,
                     PREFETCH=1, TAG_CONTEXT=0, N_REQS=296, MEM_LATENCY=40))
