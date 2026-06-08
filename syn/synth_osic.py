#!/usr/bin/env python3
"""Full sky130 synth + STA + power using the IIC-OSIC-TOOLS docker (native yosys +
OpenSTA + sky130 PDK). Unlike syn/synth.py (offline WASM yosys, area only), this
gets a real critical path, Fmax and power.

Flow (inside the container, project mounted at /foss/designs):
  sv2v wrapper+rtl -> Yosys (synth -flatten -> sky130 sc_hd map -> netlist + area)
  -> OpenSTA (create_clock 400 MHz -> report_checks/wns -> Fmax; report_power).
Writes results/<cfg>.json (params, area+per-module, Fmax, critical path, power+split)
plus raw logs results/<cfg>_{area,sta}.txt and the gate netlist syn/build/<cfg>_netlist.v.

Usage:  python3 syn/synth_osic.py full [no_coalesce ...]
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# reuse the config set + wrapper generator from the offline driver
sys.path.insert(0, str(Path(__file__).resolve().parent))
from synth import CONFIGS, wrapper_sv, RTL_FILES, clog2   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "syn" / "build"
RESULTS = ROOT / "results"
IMAGE = "hpretl/iic-osic-tools:latest"
# cell library variant: hd=high-density (default), hs=high-speed, hdll=hd-low-leak,
# ms/ls=medium/low speed. Override with STD_VARIANT=hs etc.
VARIANT = os.environ.get("STD_VARIANT", "hd")
CORNER = os.environ.get("STD_CORNER", "tt_025C_1v80")
LIB = f"/foss/pdks/sky130A/libs.ref/sky130_fd_sc_{VARIANT}/lib/sky130_fd_sc_{VARIANT}__{CORNER}.lib"
PERIOD_NS = float(os.environ.get("PERIOD_NS", "2.5"))   # 400 MHz target
DROOT = "/foss/designs"  # mount point of the repo inside the container
# NOTE: max-fanout limiting + buffer insertion + gate sizing happen in P&R
# (OpenLane SYNTH_MAX_FANOUT / OpenROAD repair_design); this synth-only flow
# reports the *unbuffered* worst case. Cell library variant = STD_VARIANT (hd/hs/...).


def gen_scripts(name):
    BUILD.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (BUILD / f"cfg_{name}.sv").write_text(wrapper_sv(name, CONFIGS[name]))
    top = f"cfg_{name}"
    srcs = " ".join(f"{DROOT}/rtl/{f}" for f in RTL_FILES) + f" {DROOT}/syn/build/cfg_{name}.sv"
    vfile = f"{DROOT}/syn/build/{name}.v"
    netlist = f"{DROOT}/syn/build/{name}_netlist.v"
    period_ps = int(PERIOD_NS * 1000)

    # hierarchical pass: per-module area (keep block boundaries)
    (BUILD / f"{name}_hier.ys").write_text(f"""\
read_verilog {vfile}
hierarchy -top {top}
synth -top {top}
dfflibmap -liberty {LIB}
abc -liberty {LIB}
opt_clean
tee -o {DROOT}/results/{name}_area.txt stat -liberty {LIB}
""")
    # flattened pass: gate netlist for STA + total area
    (BUILD / f"{name}.ys").write_text(f"""\
read_verilog {vfile}
hierarchy -top {top}
synth -top {top} -flatten
dfflibmap -liberty {LIB}
abc -liberty {LIB} -D {period_ps}
setundef -zero
opt_clean -purge
tee -o {DROOT}/results/{name}_area_flat.txt stat -liberty {LIB}
write_verilog -noattr {netlist}
""")

    (BUILD / f"{name}.sta.tcl").write_text(f"""\
read_liberty {LIB}
read_verilog {netlist}
link_design {top}
create_clock -name clk -period {PERIOD_NS} [get_ports clk]
set_propagated_clock [all_clocks]
puts "=== CRITICAL PATH (max) ==="
report_checks -path_delay max -group_count 1 -fields {{slew cap fanout}} -digits 4
puts "=== WNS/TNS ==="
report_wns
report_tns
puts "=== POWER ==="
report_power -digits 6
""")
    return top, srcs, vfile


def run_in_docker(name, srcs, vfile):
    cmd = (
        f"cd {DROOT} && "
        f"sv2v --top=cfg_{name} --write={vfile} {srcs} && "
        f"yosys -q {DROOT}/syn/build/{name}_hier.ys && "
        f"yosys -q {DROOT}/syn/build/{name}.ys && "
        f"sta -no_init -exit {DROOT}/syn/build/{name}.sta.tcl"
    )
    docker = ["docker", "run", "--rm", "-v", f"{ROOT}:/foss/designs",
              IMAGE, "--skip", "bash", "-lc", cmd]
    r = subprocess.run(docker, capture_output=True, text=True)
    (RESULTS / f"{name}_sta.txt").write_text(r.stdout + "\n===STDERR===\n" + r.stderr)
    return r.stdout + r.stderr


def _rd(p):
    return (RESULTS / p).read_text() if (RESULTS / p).exists() else ""


def parse(name, log):
    # per-module area (hierarchical pass), total area (flattened pass)
    modules, total_area = {}, None
    for ln in _rd(f"{name}_area.txt").splitlines():
        m = re.search(r"Chip area for module '([^']+)':\s*([0-9.]+)", ln)
        if m and float(m.group(2)) > 0:
            k = re.sub(r"^\$paramod\$[0-9a-f]+\\", "", m.group(1)).lstrip("\\")
            if k in (f"cfg_{name}", "iommu_core"):     # wrappers -> skip in breakdown
                continue
            while k in modules:
                k += "'"
            modules[k] = float(m.group(2))
    for ln in _rd(f"{name}_area_flat.txt").splitlines():
        m = re.search(r"Chip area for (?:top )?module '([^']+)':\s*([0-9.]+)", ln)
        if m:
            total_area = float(m.group(2))
    # dominant cells on the critical path (big fanout / slew = the bottleneck)
    worst = []
    for ln in log.splitlines():
        m = re.match(r"\s*(\d+)\s+[0-9.]+\s+([0-9.]+)\s+([0-9.]+)\s+[0-9.]+\s+[\^v]\s+\S+\s+\((\S+)\)", ln)
        if m:
            worst.append({"fanout": int(m.group(1)), "slew_ns": float(m.group(2)),
                          "delay_ns": float(m.group(3)), "cell": m.group(4)})
    worst = sorted(worst, key=lambda x: x["delay_ns"], reverse=True)[:3]
    # critical path: startpoint / endpoint / arrival / slack
    start = re.search(r"Startpoint:\s*(\S+)", log)
    end = re.search(r"Endpoint:\s*(\S+)", log)
    slack = re.search(r"(-?\d+\.\d+)\s+slack", log)
    arrival = re.findall(r"(-?\d+\.\d+)\s+data arrival time", log)
    slack_ns = float(slack.group(1)) if slack else None
    crit_ns = (PERIOD_NS - slack_ns) if slack_ns is not None else (
        float(arrival[-1]) if arrival else None)
    fmax_mhz = (1000.0 / crit_ns) if crit_ns and crit_ns > 0 else None
    # power: parse the Total row (Internal Switching Leakage Total, Watts)
    power = {}
    for ln in log.splitlines():
        m = re.match(r"\s*Total\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)", ln)
        if m:
            power = {"internal_W": float(m.group(1)), "switching_W": float(m.group(2)),
                     "leakage_W": float(m.group(3)), "total_W": float(m.group(4))}
    res = {
        "config": name, "params": CONFIGS[name],
        "clock_target_mhz": 1000.0 / PERIOD_NS,
        "area_um2_total": total_area, "area_um2_per_module": modules,
        "fmax_mhz": fmax_mhz, "critical_path_ns": crit_ns,
        "wns_ns": slack_ns,
        "critical_startpoint": start.group(1) if start else None,
        "critical_endpoint": end.group(1) if end else None,
        "critical_dominant_cells": worst,
        "power_W": power,
        "meets_target": (slack_ns is not None and slack_ns >= 0),
    }
    (RESULTS / f"{name}.json").write_text(json.dumps(res, indent=2))
    return res


if __name__ == "__main__":
    for name in (sys.argv[1:] or ["full"]):
        print(f"=== OSIC synth+STA: {name} ===")
        top, srcs, vfile = gen_scripts(name)
        log = run_in_docker(name, srcs, vfile)
        res = parse(name, log)
        a = res["area_um2_total"]; f = res["fmax_mhz"]
        print(f"  area {a:.0f} um^2" if a else "  area: parse-failed (see results/%s_area.txt)" % name)
        print(f"  Fmax {f:.1f} MHz  crit {res['critical_path_ns']} ns  WNS {res['wns_ns']} ns"
              if f else "  Fmax: parse-failed (see results/%s_sta.txt)" % name)
        print(f"  power {res['power_W']}")
