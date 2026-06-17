#!/usr/bin/env python3
"""Synthesize + place-optimize the NO-PIPELINE cfg5 (cfg5_nopipe_top, PIPELINE_DEPTH=1)
on BOTH sky130_fd_sc_hd (high-density) and sky130_fd_sc_hs (high-speed). Logic-structure
optimizations (line IOTLB, counter exclusion) are kept; no pipeline stages. Uses the
v14-tuned P&R knobs (relaxed slew, UTIL 65). Writes cfg5_nopipe/results_<lib>/.
Run: python3 syn/nopipe_both.py
"""
import re, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "hpretl/iic-osic-tools:latest"
D = "/foss/designs"
CFG, TOP = "cfg5_nopipe", "cfg5_nopipe_top"
ACT, PERIOD = 0.053, 2.5
PDK = f"{D}/open_pdks/sky130/sky130A/libs.ref"
CORE = ["rtl/iommu_pkg.sv", "rtl/fa_cache.sv", "rtl/line_iotlb.sv", "rtl/mem_master.sv",
        "rtl/prefetch_ctrl.sv", "rtl/iommu_top.sv"]
# v14-tuned knobs (best found): relaxed slew + dense floorplan
KNOBS = dict(PERIOD=2.5, SDC=1, RDES=1, RTIM=1, MAXTRANS=0.75, MAXFO=16,
             SLEWM=0, CAPM=0, SETUPM=0.15, UTIL=65)
LIBS = {  # lib: (base, site, driving cell)
    "hd": (f"{PDK}/sky130_fd_sc_hd", "unithd", "sky130_fd_sc_hd__buf_2"),
    "hs": (f"{PDK}/sky130_fd_sc_hs", "unit",   "sky130_fd_sc_hs__buf_2"),
}
srcs = " ".join(f"{D}/{f}" for f in CORE) + f" {D}/{CFG}/{TOP}.sv"


def drun(envs, cmd):
    eargs = []
    for k, v in envs.items():
        eargs += ["-e", f"{k}={v}"]
    return subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT}:{D}", *eargs,
                           IMAGE, "--skip", "bash", "-lc", cmd], capture_output=True, text=True)


def num(x):
    return float(x) if x else None


results = {}
for lib, (base, site, drv) in LIBS.items():
    LIB = f"{base}/lib/sky130_fd_sc_{lib}__tt_025C_1v80.lib"
    TLEF = f"{base}/techlef/sky130_fd_sc_{lib}__nom.tlef"
    CLEF = f"{base}/lef/sky130_fd_sc_{lib}.lef"
    res = f"{CFG}/results_{lib}"
    bld = f"syn/build_nopipe_{lib}"
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
report_power -digits 6
exit
"""
    synbash = (f"mkdir -p {D}/{bld} {D}/{res} && "
               f"sv2v -D SYNTHESIS {srcs} > {D}/{bld}/{CFG}.v 2>{D}/{bld}/sv2v.log && "
               f"cat > {D}/{bld}/synth.ys <<'YE'\n{ys}\nYE\n"
               f"cat > {D}/{bld}/sta.tcl <<'SE'\n{sta}\nSE\n"
               f"yosys -q {D}/{bld}/synth.ys 2>&1 | tee {D}/{res}/yosys.log ; "
               f"sta -no_init -exit {D}/{bld}/sta.tcl 2>&1 | tee {D}/{res}/sta.txt")
    print(f"[nopipe {lib}] synthesizing ...", flush=True)
    r = drun({}, synbash)
    (ROOT / res / "flow.log").write_text(r.stdout + "\n=ERR=\n" + r.stderr)
    sta_txt = (ROOT / res / "sta.txt").read_text()
    area_txt = (ROOT / res / "synth_area.txt").read_text()
    ma = re.search(r"Chip area for top module[^:]*: *([\d.]+)", area_txt)
    syn_area = num(ma.group(1)) if ma else None
    ms = re.search(r"worst slack max\s+(-?[\d.]+)", sta_txt)
    syn_fmax = (1000.0 / (PERIOD - num(ms.group(1)))) if ms else None

    env = dict(TOP=TOP, SITE=site, ACT=ACT, NET=f"{D}/{res}/netlist.v",
               LIB=LIB, TLEF=TLEF, CLEF=CLEF, DRVCELL=drv, **KNOBS)
    print(f"[nopipe {lib}] P&R + resize ...", flush=True)
    r2 = drun(env, "openroad -no_init -exit /foss/designs/syn/fmax_opt/opt.tcl 2>&1")
    (ROOT / res / "postopt.log").write_text(r2.stdout)
    post = r2.stdout[r2.stdout.find("##POST"):]
    mw = re.search(r"##POST\s*\nworst slack max\s+(-?[\d.]+)", r2.stdout)
    wns = num(mw.group(1)) if mw else None
    mt = re.search(r"tns max\s+(-?[\d.]+)", post)
    tns = num(mt.group(1)) if mt else None
    fmax = (1000.0 / (PERIOD - wns)) if wns is not None else None
    ar = re.search(r"Design area\s+([\d.]+)\s+um\^2", post)
    area = num(ar.group(1)) if ar else None
    mp = re.search(r"^Total\s+\S+\s+\S+\s+\S+\s+(\S+)", post, re.M)
    pwr = num(mp.group(1)) * 1000 if mp else None
    results[lib] = dict(syn_area=syn_area, syn_fmax=syn_fmax, wns=wns, tns=tns,
                        fmax=fmax, area=area, pwr=pwr)

print("\n==================== NO-PIPELINE cfg5 (PIPELINE_DEPTH=1) ====================")
print("logic-structure opt only (line IOTLB + counter excluded); v14-tuned P&R knobs\n")
print(f"{'lib':<5}{'synthFmax':>11}{'postFmax':>10}{'WNS':>8}{'TNS':>10}{'area':>10}{'power':>8}")
for lib in ("hd", "hs"):
    r = results[lib]
    g = lambda k, f="{:.1f}": (f.format(r[k]) if r.get(k) is not None else "n/a")
    print(f"{lib:<5}{g('syn_fmax'):>11}{g('fmax'):>10}{g('wns','{:.2f}'):>8}"
          f"{g('tns','{:.1f}'):>10}{g('area','{:.0f}'):>10}{g('pwr'):>8}")
print("\ncompare: pipelined hd v11=266.7MHz/94091um2, pipelined hs v14=395.3MHz/123510um2")
