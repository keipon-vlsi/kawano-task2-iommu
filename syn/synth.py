#!/usr/bin/env python3
"""sky130 synthesis driver for the parameterized IOMMU core.

Flow per config (= a parameter set):
  1. emit a thin SV wrapper that instantiates iommu_core with the config params,
  2. sv2v (SystemVerilog -> Verilog-2005) so Yosys can parse it,
  3. Yosys: generic synth -> map to sky130_fd_sc_hd -> ABC tech map,
     - hierarchical pass -> per-module cell area (stat),
     - flattened pass + ABC delay target -> Fmax / critical path.
Outputs: results/<cfg>_area.txt, <cfg>_timing.txt, <cfg>.json.

Tools: sv2v (/tmp/sv2v or $SV2V), yowasp-yosys (via the project venv). PDK from
$PDK_ROOT (sky130A sc_hd tt corner).
"""
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RTL = ROOT / "rtl"
BUILD = ROOT / "syn" / "build"
RESULTS = ROOT / "results"
PDK_ROOT = os.environ.get("PDK_ROOT", str(ROOT / "pdk"))
LIB = (Path(PDK_ROOT) / "sky130A/libs.ref/sky130_fd_sc_hd/lib/"
       "sky130_fd_sc_hd__tt_025C_1v80.lib")
SV2V = os.environ.get("SV2V", "/tmp/sv2v")
YOSYS = [sys.executable.replace("python", "yowasp-yosys")] if False else \
        [str(Path(sys.executable).parent / "yowasp-yosys")]
TARGET_PERIOD_PS = 2500   # 400 MHz

RTL_FILES = ["iommu_pkg.sv", "cache_store.sv", "mem_if.sv", "walker.sv",
             "walk_engine.sv", "txn_buffer.sv", "iommu_core.sv"]

# fixed widths (mirror iommu_pkg)
VPN_W, DEVICE_W, PASID_W, VMID_W = 27, 16, 20, 14
SPA_W, GPA_W, PPN_W, CTX_W = 40, 41, 28, 50
IOTLB_KEY_W = CTX_W + VPN_W


def clog2(n):
    return 1 if n < 2 else math.ceil(math.log2(n))


def wrapper_sv(name, p):
    """Generate a wrapper module that fixes iommu_core's parameters for this config."""
    tag_w = clog2(p["NUM_WALKERS"])
    mshr_w = clog2(p["BUFFER_DEPTH"])
    pmap = ", ".join(f".{k}({v})" for k, v in p.items())
    return f"""// auto-generated config wrapper: {name}
module cfg_{name} (
  input  logic clk, rst_n,
  input  logic req_valid, output logic req_ready,
  input  logic [{VPN_W-1}:0] req_vpn, input logic [{DEVICE_W-1}:0] req_device_id,
  input  logic [{PASID_W-1}:0] req_pasid, input logic [{VMID_W-1}:0] req_vmid, input logic req_is_write,
  output logic rsp_valid, input logic rsp_ready,
  output logic [{SPA_W-1}:0] rsp_spa, output logic [{mshr_w-1}:0] rsp_tag,
  output logic arvalid, input logic arready, output logic [{GPA_W-1}:0] araddr, output logic [{tag_w-1}:0] arid,
  input  logic rvalid, output logic rready, input logic [{PPN_W-1}:0] rdata, input logic [{tag_w-1}:0] rid,
  input  logic pl_valid, input logic [2:0] pl_sel,
  input  logic [{IOTLB_KEY_W-1}:0] pl_key, input logic [{SPA_W-1}:0] pl_data,
  output logic [31:0] cnt_iotlb_hit, cnt_coalesced, cnt_walks, buf_occupancy, active_walks, mem_outstanding
);
  iommu_core #({pmap}) u (.*);
endmodule
"""


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def synth(name, params):
    BUILD.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    wrap = BUILD / f"cfg_{name}.sv"
    wrap.write_text(wrapper_sv(name, params))
    vfile = BUILD / f"{name}.v"

    # 1) sv2v
    srcs = [str(RTL / f) for f in RTL_FILES] + [str(wrap)]
    r = run([SV2V, f"--top=cfg_{name}", f"--write={vfile}"] + srcs)
    if r.returncode != 0:
        print(r.stdout, r.stderr); sys.exit("sv2v failed")

    top = f"cfg_{name}"
    area_txt = RESULTS / f"{name}_area.txt"
    time_txt = RESULTS / f"{name}_timing.txt"

    # 2) Yosys: hierarchical area pass
    ys_area = f"""
read_verilog {vfile}
hierarchy -top {top}
synth -top {top}
dfflibmap -liberty {LIB}
abc -liberty {LIB}
opt_clean
tee -o {area_txt} stat -liberty {LIB}
"""
    r = run(YOSYS + ["-q", "-p", ys_area])
    if r.returncode != 0:
        print(r.stdout[-3000:], r.stderr[-3000:]); sys.exit("yosys area pass failed")

    # 3) Yosys: critical-path depth via ltp on the flattened generic netlist.
    # (WASM abc cannot complete a -liberty STA on the full flattened design offline;
    # ltp gives the logic depth + path location, which is what guides pipelining.
    # A calibrated Fmax comes from the OpenLane/OpenSTA flow in syn/openlane/.)
    ys_time = f"""
read_verilog {vfile}
hierarchy -top {top}
flatten
proc; opt; memory_collect; opt; techmap; opt
ltp
"""
    r = run(YOSYS + ["-p", ys_time])
    time_log = r.stdout + r.stderr
    time_txt.write_text(time_log)

    return parse_results(name, params, area_txt.read_text(), time_log)


def _clean(mod):
    mod = re.sub(r"^\$paramod\$[0-9a-f]+\\", "", mod)   # strip paramod hash
    return mod.lstrip("\\")


def parse_results(name, params, area_log, time_log):
    # per-module chip area: "Chip area for (top )?module '<m>': <area>"
    modules = {}
    total_area = None
    for ln in area_log.splitlines():
        m = re.search(r"Chip area for top module '([^']+)':\s*([0-9.]+)", ln)
        if m:
            total_area = float(m.group(2))
            continue
        m = re.search(r"Chip area for module '([^']+)':\s*([0-9.]+)", ln)
        if m and float(m.group(2)) > 0:
            key = _clean(m.group(1))
            # disambiguate repeated module names (e.g. the cache_store variants)
            while key in modules:
                key += "'"
            modules[key] = float(m.group(2))
    # critical path: ltp logic depth + the modules it traverses (timing-risk blocks)
    m = re.search(r"Longest topological path in \S+ \(length=(\d+)\)", time_log)
    depth = int(m.group(1)) if m else None
    path_mods = []
    for ln in time_log.splitlines():
        for mm in re.findall(r"\\u\.(u_\w+)", ln):
            if mm not in path_mods:
                path_mods.append(mm)
    res = {
        "config": name, "params": params,
        "area_um2_per_module": modules,
        "total_area_um2": total_area,
        "critical_path_depth_generic_levels": depth,
        "critical_path_modules": path_mods,
        "fmax_mhz": None,   # needs OpenLane/OpenSTA (offline WASM abc STA unavailable)
        "target_mhz": 1e6 / TARGET_PERIOD_PS,
        "note": "area from sky130 sc_hd tt; Fmax pending OpenSTA; critical path is the "
                "single-cycle CAM lookup + arbiter + FSM -> pipeline the lookup next.",
    }
    (RESULTS / f"{name}.json").write_text(json.dumps(res, indent=2))
    return res


CONFIGS = {
    # Full: single-stage, PWC + coalescing, modest N/buffer (prefetch hook off in P1)
    "full": dict(MODE=1, COALESCE_FACTOR=8, PREFETCH_EN=0, NUM_WALKERS=4, BUFFER_DEPTH=16,
                 MEM_MAX_OUTSTANDING=8, IOTLB_ENTRIES=64, IOTLB_ASSOC=4, IOTLB_STORAGE=1,
                 S1PWC_ENTRIES=16, S1PWC_ASSOC=16, S1PWC_STORAGE=0),
    # (later phase) no-coalescing
    "no_coalesce": dict(MODE=1, COALESCE_FACTOR=1, PREFETCH_EN=0, NUM_WALKERS=4, BUFFER_DEPTH=16,
                        MEM_MAX_OUTSTANDING=8, IOTLB_ENTRIES=64, IOTLB_ASSOC=4, IOTLB_STORAGE=1,
                        S1PWC_ENTRIES=16, S1PWC_ASSOC=16, S1PWC_STORAGE=0),
    # (later phase) no-cache: DDT$/PDT$ only, large N/buffer
    "no_cache": dict(MODE=1, COALESCE_FACTOR=1, PREFETCH_EN=0, NUM_WALKERS=8, BUFFER_DEPTH=16,
                     MEM_MAX_OUTSTANDING=8, IOTLB_ENTRIES=0, IOTLB_ASSOC=1, IOTLB_STORAGE=0,
                     S1PWC_ENTRIES=0, S1PWC_ASSOC=1, S1PWC_STORAGE=0),
    # Full nested
    "full_nested": dict(MODE=3, COALESCE_FACTOR=8, PREFETCH_EN=0, NUM_WALKERS=4, BUFFER_DEPTH=16,
                        MEM_MAX_OUTSTANDING=8, IOTLB_ENTRIES=64, IOTLB_ASSOC=4, IOTLB_STORAGE=1,
                        S1PWC_ENTRIES=16, S1PWC_ASSOC=16, S1PWC_STORAGE=0),
}


if __name__ == "__main__":
    names = sys.argv[1:] or ["full"]
    rows = []
    for n in names:
        print(f"=== synthesizing config '{n}' ===")
        res = synth(n, CONFIGS[n])
        rows.append(res)
        print(f"  total area : {res['total_area_um2']} um^2")
        print(f"  Fmax       : {res['fmax_mhz']:.1f} MHz  (crit {res['critical_path_ns']:.3f} ns)"
              if res['fmax_mhz'] else "  Fmax: n/a")
    (RESULTS / "ppa.json").write_text(json.dumps(rows, indent=2))
