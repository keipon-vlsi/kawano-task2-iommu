"""Parametric cocotb runner: run a cfg top with a chosen MEM_LATENCY (cycles).
Usage: run_param.py <cfg_name> <top_module> <wrapper.sv> <mem_latency_cycles>
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from runner_common import run_cfg

cfg, top, wrapper, memlat = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
run_cfg(cfg_name=cfg, top_module=top, wrapper_sv=Path(wrapper).resolve(),
        env=dict(CO=8, NUM_WALKERS=1, BUFFER=1, HAS_PWC=1, HAS_IOTLB=1,
                 PREFETCH=1, TAG_CONTEXT=0, N_REQS=296, MEM_LATENCY=memlat))
