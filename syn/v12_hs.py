#!/usr/bin/env python3
"""v12: synthesize + place-optimize cfg5_notag with the HIGH-SPEED library
sky130_fd_sc_hs (vs the high-density hd used for v0..v11). RTL is UNCHANGED (same
sv2v -D SYNTHESIS sources as v11). Outputs go to cfg5_notag/results_hs/ so the hd
(v11) results are untouched. Reports synth-estimate + post-opt (P&R+resize) PPA.

Run: python3 syn/v12_hs.py
"""
import re, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "hpretl/iic-osic-tools:latest"
D = "/foss/designs"
CFG, TOP = "cfg5_notag", "cfg5_top"
ACT, PERIOD = 0.053, 2.5
# hs library from the repo open_pdks build (mounted at /foss/designs/open_pdks)
HSB = f"{D}/open_pdks/sky130/sky130A/libs.ref/sky130_fd_sc_hs"
LIB = f"{HSB}/lib/sky130_fd_sc_hs__tt_025C_1v80.lib"
TLEF = f"{HSB}/techlef/sky130_fd_sc_hs__nom.tlef"
CLEF = f"{HSB}/lef/sky130_fd_sc_hs.lef"
SITE = "unit"
CORE = ["rtl/iommu_pkg.sv", "rtl/fa_cache.sv", "rtl/line_iotlb.sv", "rtl/mem_master.sv",
        "rtl/prefetch_ctrl.sv", "rtl/iommu_top.sv"]
KNOBS = dict(PERIOD=2.5, SDC=1, RDES=1, RTIM=1, MAXTRANS=0.5, MAXFO=12, SLEWM=10, CAPM=10)

res = f"{CFG}/results_hs"
bld = "syn/build_hs"
srcs = " ".join(f"{D}/{f}" for f in CORE) + f" {D}/{CFG}/{TOP}.sv"


def drun(envs, cmd):
    eargs = []
    for k, v in envs.items():
        eargs += ["-e", f"{k}={v}"]
    return subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT}:{D}", *eargs,
                           IMAGE, "--skip", "bash", "-lc", cmd], capture_output=True, text=True)


def n(x):  # safe float
    return float(x) if x else None


# ---------------- 1) synthesis with hs ----------------
ys = f"""
read_verilog {D}/{bld}/{CFG}.v
hierarchy -top {TOP} -check
synth -top {TOP}
dfflibmap -liberty {LIB}
abc -liberty {LIB} -D {int(PERIOD*1000)}
clean -purge
tee -o {D}/{res}/synth_area.txt stat -liberty {LIB}
flatten
clean -purge
write_verilog -noattr {D}/{res}/netlist.v
"""
sta = f"""
read_liberty {LIB}
read_verilog {D}/{res}/netlist.v
link_design {TOP}
create_clock -name clk -period {PERIOD} [get_ports clk]
set_power_activity -global -activity {ACT} -duty 0.5
puts "=== WORST SLACK ==="
report_worst_slack -max
puts "=== POWER ==="
report_power -digits 6
exit
"""
synbash = (f"mkdir -p {D}/{bld} {D}/{res} && "
           f"sv2v -D SYNTHESIS {srcs} > {D}/{bld}/{CFG}.v 2>{D}/{bld}/sv2v.log && "
           f"cat > {D}/{bld}/synth.ys <<'YE'\n{ys}\nYE\n"
           f"cat > {D}/{bld}/sta.tcl <<'SE'\n{sta}\nSE\n"
           f"yosys -q {D}/{bld}/synth.ys 2>&1 | tee {D}/{res}/yosys.log ; "
           f"sta -no_init -exit {D}/{bld}/sta.tcl 2>&1 | tee {D}/{res}/sta.txt")
print("[v12 hs] synthesizing cfg5 with sky130_fd_sc_hs ...", flush=True)
r = drun({}, synbash)
(ROOT / res / "flow.log").write_text(r.stdout + "\n=ERR=\n" + r.stderr)
area_txt = (ROOT / res / "synth_area.txt").read_text()
sta_txt = (ROOT / res / "sta.txt").read_text()
ma = re.search(r"Chip area for top module[^:]*: *([\d.]+)", area_txt)
syn_area = n(ma.group(1)) if ma else None
ms = re.search(r"worst slack max\s+(-?[\d.]+)", sta_txt)
syn_sl = n(ms.group(1)) if ms else None
syn_fmax = (1000.0 / (PERIOD - syn_sl)) if (syn_sl is not None and PERIOD - syn_sl > 0) else None
mp = re.search(r"^Total\s+([0-9][0-9.eE+-]*)\s+([0-9][0-9.eE+-]*)\s+([0-9][0-9.eE+-]*)\s+([0-9][0-9.eE+-]*)", sta_txt, re.M)
syn_pwr = n(mp.group(4)) * 1000 if mp else None
print(f"[v12 hs] synth: area={syn_area} um2  Fmax~={syn_fmax and round(syn_fmax,1)} MHz  power~={syn_pwr and round(syn_pwr,1)} mW")

# ---------------- 2) post-opt (P&R + resize) with hs ----------------
env = dict(TOP=TOP, SITE=SITE, ACT=ACT, NET=f"{D}/{res}/netlist.v",
           LIB=LIB, TLEF=TLEF, CLEF=CLEF, DRVCELL="sky130_fd_sc_hs__buf_2", **KNOBS)
print("[v12 hs] constrained P&R + resize (hs) ...", flush=True)
r2 = drun(env, "openroad -no_init -exit /foss/designs/syn/fmax_opt/opt.tcl 2>&1")
(ROOT / res / "postopt.log").write_text(r2.stdout)
out = r2.stdout
post = out[out.find("##POST"):]
mp2 = re.search(r"##POST\s*\nworst slack max\s+(-?[\d.]+)", out)
po_sl = n(mp2.group(1)) if mp2 else None
po_fmax = (1000.0 / (PERIOD - po_sl)) if (po_sl is not None and PERIOD - po_sl > 0) else None
ar = re.search(r"Design area\s+([\d.]+)\s+um\^2", post)
po_area = n(ar.group(1)) if ar else None
mt = re.search(r"^Total\s+([0-9][0-9.eE+-]*)\s+([0-9][0-9.eE+-]*)\s+([0-9][0-9.eE+-]*)\s+([0-9][0-9.eE+-]*)", post, re.M)
po_pwr = n(mt.group(4)) * 1000 if mt else None

print("\n==================== v12 (sky130_fd_sc_hs) cfg5 ====================")
print(f"  synth   : area {syn_area} um2,  Fmax~ {syn_fmax and round(syn_fmax,1)} MHz")
print(f"  POST-OPT: Fmax {po_fmax and round(po_fmax,1)} MHz,  area {po_area} um2,  power {po_pwr and round(po_pwr,1)} mW @400")
print("  (compare v11 hd: post-opt 266.7 MHz, 94091 um2, 36.7 mW)")
