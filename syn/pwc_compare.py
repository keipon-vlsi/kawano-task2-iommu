#!/usr/bin/env python3
"""Isolated PPA comparison of the VS-stage PWC caching scheme: COMBINED (VPN -> SPA,
G-resolved, one lookup) vs SPLIT (VPN -> GPA in VS PWC, then chained GPA -> SPA in a
separate G PWC). Same fa_cache primitive, same widths, same flow (hd, canonical knobs).
Isolates the structural + chained-depth delta. Run: python3 syn/pwc_compare.py
"""
import re, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "hpretl/iic-osic-tools:latest"
D = "/foss/designs"
HDB = "/foss/pdks/sky130A/libs.ref/sky130_fd_sc_hd"
LIB = f"{HDB}/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
TLEF = f"{HDB}/techlef/sky130_fd_sc_hd__nom.tlef"
CLEF = f"{HDB}/lef/sky130_fd_sc_hd.lef"
KNOBS = dict(PERIOD=2.5, SDC=1, RDES=1, RTIM=1, MAXTRANS=0.5, MAXFO=12, SLEWM=10, CAPM=10, UTIL=40)
ACT, PERIOD = 0.12, 2.5

# no-context tags (adopted cfg5 style): VML2 tag=9 (VPN[26:18]), VML1 tag=18 (VPN[26:9]).
# DATA_W=48 (PTE[47:0]); used PPN = data[10 +: 28].
COMBINED = """module pwc_dut (
  input logic clk, rst_n,
  input  logic [17:0] lk_vpn,
  output logic        hit_o,
  output logic [27:0] spa_o,
  input  logic        f2_en, input logic [8:0]  f2_tag, input logic [47:0] f2_data,
  input  logic        f1_en, input logic [17:0] f1_tag, input logic [47:0] f1_data,
  input  logic [27:0] root
);
  logic v2_hit, v1_hit; logic [47:0] v2_d, v1_d;
  fa_cache #(.ENTRIES(1),.TAG_W(9), .DATA_W(48)) u_vml2(.clk,.rst_n,
     .lk_tag(lk_vpn[17:9]),.lk_hit(v2_hit),.lk_data(v2_d),.fill_en(f2_en),.fill_tag(f2_tag),.fill_data(f2_data));
  fa_cache #(.ENTRIES(2),.TAG_W(18),.DATA_W(48)) u_vml1(.clk,.rst_n,
     .lk_tag(lk_vpn),     .lk_hit(v1_hit),.lk_data(v1_d),.fill_en(f1_en),.fill_tag(f1_tag),.fill_data(f1_data));
  // one lookup level: most-complete VS hit -> G-resolved SPA directly
  assign hit_o = v1_hit | v2_hit;
  assign spa_o = v1_hit ? v1_d[10+:28] : v2_hit ? v2_d[10+:28] : root;
endmodule"""

SPLIT = """module pwc_dut (
  input logic clk, rst_n,
  input  logic [17:0] lk_vpn,
  output logic        hit_o,
  output logic [27:0] spa_o,
  input  logic        f2_en, input logic [8:0]  f2_tag, input logic [47:0] f2_data,
  input  logic        f1_en, input logic [17:0] f1_tag, input logic [47:0] f1_data,
  input  logic        g2_en, input logic [8:0]  g2_tag, input logic [47:0] g2_data,
  input  logic        g1_en, input logic [17:0] g1_tag, input logic [47:0] g1_data,
  input  logic [27:0] groot
);
  logic v2_hit, v1_hit; logic [47:0] v2_d, v1_d;
  fa_cache #(.ENTRIES(1),.TAG_W(9), .DATA_W(48)) u_vml2(.clk,.rst_n,
     .lk_tag(lk_vpn[17:9]),.lk_hit(v2_hit),.lk_data(v2_d),.fill_en(f2_en),.fill_tag(f2_tag),.fill_data(f2_data));
  fa_cache #(.ENTRIES(2),.TAG_W(18),.DATA_W(48)) u_vml1(.clk,.rst_n,
     .lk_tag(lk_vpn),     .lk_hit(v1_hit),.lk_data(v1_d),.fill_en(f1_en),.fill_tag(f1_tag),.fill_data(f1_data));
  // VS lookup yields a GPA (the next VS table's guest-physical page number)
  logic        v_hit; logic [27:0] gpn;
  assign v_hit = v1_hit | v2_hit;
  assign gpn   = v1_hit ? v1_d[10+:28] : v2_hit ? v2_d[10+:28] : '0;
  // CHAINED second lookup: G PWC keyed by that GPA -> SPA
  logic g2_hit, g1_hit; logic [47:0] g2_d, g1_d;
  fa_cache #(.ENTRIES(1),.TAG_W(9), .DATA_W(48)) u_gl2(.clk,.rst_n,
     .lk_tag(gpn[8:0]), .lk_hit(g2_hit),.lk_data(g2_d),.fill_en(g2_en),.fill_tag(g2_tag),.fill_data(g2_data));
  fa_cache #(.ENTRIES(2),.TAG_W(18),.DATA_W(48)) u_gl1(.clk,.rst_n,
     .lk_tag(gpn[17:0]),.lk_hit(g1_hit),.lk_data(g1_d),.fill_en(g1_en),.fill_tag(g1_tag),.fill_data(g1_data));
  assign hit_o = v_hit & (g1_hit | g2_hit);
  assign spa_o = g1_hit ? g1_d[10+:28] : g2_hit ? g2_d[10+:28] : groot;
endmodule"""


def drun(envs, cmd):
    eargs = []
    for k, v in envs.items():
        eargs += ["-e", f"{k}={v}"]
    return subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT}:{D}", *eargs,
                           IMAGE, "--skip", "bash", "-lc", cmd], capture_output=True, text=True)


def num(x):
    return float(x) if x else None


res = {}
for name, src in (("combined", COMBINED), ("split", SPLIT)):
    wd = f"syn/pwc_cmp/{name}"
    wsdir = ROOT / "syn" / "pwc_cmp" / name
    wsdir.mkdir(parents=True, exist_ok=True)
    (wsdir / "pwc_dut.sv").write_text(src)
    srcs = f"{D}/rtl/fa_cache.sv {D}/{wd}/pwc_dut.sv"
    ys = (f"read_verilog {D}/{wd}/d.v\nhierarchy -top pwc_dut -check\nsynth -top pwc_dut\n"
          f"dfflibmap -liberty {LIB}\nabc -liberty {LIB} -D {int(PERIOD*1000)}\nclean -purge\n"
          f"tee -o {D}/{wd}/synth_area.txt stat -liberty {LIB}\nflatten\nclean -purge\n"
          f"write_verilog -noattr {D}/{wd}/netlist.v\n")
    synbash = (f"sv2v {srcs} > {D}/{wd}/d.v 2>{D}/{wd}/sv2v.log && "
               f"cat > {D}/{wd}/s.ys <<'YE'\n{ys}\nYE\n"
               f"yosys -q {D}/{wd}/s.ys 2>&1 | tee {D}/{wd}/yosys.log")
    print(f"[{name}] synth ...", flush=True)
    r = drun({}, synbash)
    (wsdir / "flow.log").write_text(r.stdout + "\n=ERR=\n" + r.stderr)
    ar = re.search(r"Chip area for top module[^:]*: *([\d.]+)", (wsdir / "synth_area.txt").read_text())
    syn_area = num(ar.group(1)) if ar else None
    env = dict(TOP="pwc_dut", SITE="unithd", ACT=ACT, NET=f"{D}/{wd}/netlist.v",
               LIB=LIB, TLEF=TLEF, CLEF=CLEF, DRVCELL="sky130_fd_sc_hd__buf_2", **KNOBS)
    print(f"[{name}] post-opt ...", flush=True)
    r2 = drun(env, "openroad -no_init -exit /foss/designs/syn/fmax_opt/opt.tcl 2>&1")
    (wsdir / "postopt.log").write_text(r2.stdout)
    post = r2.stdout[r2.stdout.find("##POST"):]
    mw = re.search(r"##POST\s*\nworst slack max\s+(-?[\d.]+)", r2.stdout)
    wns = num(mw.group(1)) if mw else None
    fmax = (1000.0 / (PERIOD - wns)) if wns is not None and PERIOD - wns > 0 else None
    ma = re.search(r"Design area\s+([\d.]+)\s+um\^2", post)
    area = num(ma.group(1)) if ma else None
    mp = re.search(r"^Total\s+\S+\s+\S+\s+\S+\s+(\S+)", post, re.M)
    pwr = num(mp.group(1)) * 1000 if mp else None
    res[name] = dict(syn_area=syn_area, fmax=fmax, area=area, pwr=pwr)
    print(f"  -> Fmax {fmax and round(fmax,1)} MHz, area {area}, synth_area {syn_area}, power {pwr and round(pwr,3)} mW")

print("\n==== VS-stage PWC: combined (VPN->SPA, 1 lookup) vs split (VPN->GPA ->chained G->SPA) ====")
print(f"{'scheme':<10}{'Fmax_MHz':>9}{'area_postopt':>13}{'synth_area':>11}{'power_mW':>10}")
for n in ("combined", "split"):
    r = res[n]
    g = lambda k, f="{:.1f}": (f.format(r[k]) if r.get(k) is not None else "n/a")
    print(f"{n:<10}{g('fmax'):>9}{g('area','{:.0f}'):>13}{g('syn_area','{:.0f}'):>11}{g('pwr','{:.3f}'):>10}")
c, s = res["combined"], res["split"]
if c['area'] and s['area']:
    print(f"  split vs combined: area {100*(s['area']-c['area'])/c['area']:+.1f}%, "
          f"synth_area {100*(s['syn_area']-c['syn_area'])/c['syn_area']:+.1f}%, "
          f"Fmax {100*(s['fmax']-c['fmax'])/c['fmax']:+.1f}%, "
          f"power {100*(s['pwr']-c['pwr'])/c['pwr']:+.1f}%")
