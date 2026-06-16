"""cfg2 (PWC, no coalesce) cocotb runner."""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tb_coco"))
from runner_common import run_cfg
if __name__ == "__main__":
    run_cfg(cfg_name="cfg2_pwc", top_module="cfg2_top",
            wrapper_sv=HERE.parent / "cfg2_top.sv",
            env=dict(CO=1, NUM_WALKERS=5, BUFFER=5, HAS_PWC=1, HAS_IOTLB=0,
                     PREFETCH=0, TAG_CONTEXT=1, N_REQS=296, MEM_LATENCY=40))
