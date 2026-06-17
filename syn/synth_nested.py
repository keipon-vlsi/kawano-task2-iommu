#!/usr/bin/env python3
"""sky130 synthesis + STA for the parameterized nested IOMMU core (one cfgN_top).

Runs entirely inside the iic-osic-tools container (sv2v + Yosys 0.65 + OpenSTA),
with the repo mounted at /foss/designs. Per config it reports:
  - per-module standard-cell area (Yosys stat, hierarchy retained)
  - Fmax and the critical path (OpenSTA, sky130_fd_sc_hd tt 1v80)
Outputs land in results/<cfg>/ : synth_area.txt, sta.txt, netlist.v, report.md.

Usage:  python3 syn/synth_nested.py cfg4_prefetch        (default: all five)
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "hpretl/iic-osic-tools:latest"
D = "/foss/designs"
LIB = "/foss/pdks/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
CORE = ["rtl/iommu_pkg.sv", "rtl/fa_cache.sv", "rtl/line_iotlb.sv", "rtl/mem_master.sv",
        "rtl/prefetch_ctrl.sv", "rtl/iommu_top.sv"]
PERIOD_NS = 2.5      # 400 MHz spec target

# Per-config switching activity, CALIBRATED once against the VCD-annotated gate-level
# power (syn/power_vcd.py). Always-walking configs (cfg1/2/3) toggle ~0.16; prefetch
# configs (cfg4/5: idle walker, IOTLB hits) ~0.05. Using these lets the flat STA
# estimate match the VCD power without re-running the (slow) gate-level sim each time.
ACTIVITY = {"cfg1_nocache": 0.18, "cfg2_pwc": 0.16, "cfg3_iotlb": 0.165,
            "cfg4_prefetch": 0.045, "cfg5_notag": 0.053}

CONFIGS = {
    "cfg1_nocache":  "cfg1_top",
    "cfg2_pwc":      "cfg2_top",
    "cfg3_iotlb":    "cfg3_top",
    "cfg4_prefetch": "cfg4_top",
    "cfg5_notag":    "cfg5_top",
}


def docker_bash(cmd):
    full = ["docker", "run", "--rm", "-v", f"{ROOT}:{D}", IMAGE, "--skip", "bash", "-lc", cmd]
    return subprocess.run(full, capture_output=True, text=True)


def synth(cfg, top):
    cfgdir = next(p for p in (cfg,) )
    res = f"{D}/{cfg}/results"
    bld = f"{D}/syn/build_nested/{cfg}"
    srcs = " ".join(f"{D}/{f}" for f in CORE) + f" {D}/{cfg}/{top}.sv"
    ys = f"""
read_verilog {bld}/{cfg}.v
hierarchy -top {top} -check
synth -top {top}
dfflibmap -liberty {LIB}
abc -liberty {LIB} -D {int(PERIOD_NS*1000)}
clean -purge
tee -o {res}/synth_area.txt stat -liberty {LIB}
flatten
clean -purge
write_verilog -noattr {res}/netlist.v
"""
    sta = f"""
read_liberty {LIB}
read_verilog {res}/netlist.v
link_design {top}
create_clock -name clk -period {PERIOD_NS} [get_ports clk]
set_power_activity -global -activity {ACTIVITY.get(cfg, 0.1)} -duty 0.5
puts "=== CRITICAL PATH (reg2reg max) @ {PERIOD_NS}ns ==="
report_checks -path_delay max -fields {{slew cap fanout}} -digits 4 -group_count 2
puts "=== WORST SLACK ==="
report_worst_slack -max
puts "=== POWER (activity 0.2) ==="
report_power -digits 6
exit
"""
    bash = (
        f"mkdir -p {bld} {res} && "
        f"sv2v {srcs} > {bld}/{cfg}.v 2>{bld}/sv2v.log && "
        f"cat > {bld}/synth.ys <<'YSEOF'\n{ys}\nYSEOF\n"
        f"cat > {bld}/sta.tcl <<'STAEOF'\n{sta}\nSTAEOF\n"
        f"yosys -q {bld}/synth.ys 2>&1 | tee {res}/yosys.log ; "
        f"sta -no_init -exit {bld}/sta.tcl 2>&1 | tee {res}/sta.txt"
    )
    print(f"[{cfg}] synthesizing ({top}) ...", flush=True)
    r = docker_bash(bash)
    (ROOT / cfg / "results" / "flow.log").write_text(r.stdout + "\n=STDERR=\n" + r.stderr)
    return parse(cfg)


def parse(cfg):
    res = ROOT / cfg / "results"
    area_txt = (res / "synth_area.txt").read_text() if (res / "synth_area.txt").exists() else ""
    sta_txt = (res / "sta.txt").read_text() if (res / "sta.txt").exists() else ""
    # total chip area (top module)
    m = re.search(r"Chip area for top module.*?: *([\d.]+)", area_txt)
    total_area = float(m.group(1)) if m else None
    # worst slack @ PERIOD -> achieved period -> Fmax
    mw = re.search(r"worst slack max\s+(-?[\d.]+)", sta_txt) or \
         re.search(r"slack \((?:MET|VIOLATED)\)\s+(-?[\d.]+)", sta_txt)
    wns = float(mw.group(1)) if mw else None
    fmax = None
    if wns is not None:
        achieved = PERIOD_NS - wns          # ns of the worst path
        if achieved > 0:
            fmax = 1000.0 / achieved        # MHz
    # total power: last "Total" row of report_power, 4th col = total W
    power_w = None
    mp = re.search(r"^\s*Total\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)",
                   sta_txt, re.M)
    if mp:
        power_w = float(mp.group(4))
    return {"cfg": cfg, "total_area_um2": total_area, "wns_ns": wns, "fmax_mhz": fmax,
            "power_mw": (power_w*1000.0 if power_w is not None else None), "period_ns": PERIOD_NS}


if __name__ == "__main__":
    sel = sys.argv[1:] or list(CONFIGS)
    out = []
    for cfg in sel:
        out.append(synth(cfg, CONFIGS[cfg]))
    print(json.dumps(out, indent=2))
    (ROOT / "results" / "nested_ppa.json").write_text(json.dumps(out, indent=2))
