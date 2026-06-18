#!/usr/bin/env python3
"""PPA of cfg2/cfg3/cfg4 WITH vs WITHOUT context tags (device_id + PASID) in the cache
tags. For each cfg, generate a top with TAG_CONTEXT_EN in {1,0}, synthesize on
sky130_fd_sc_hd (current RTL), post-opt with the canonical fmax_opt knobs, report
area/Fmax/power. Writes syn/notag_build/. Run: python3 syn/notag_ppa.py
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
CORE = ["rtl/iommu_pkg.sv", "rtl/fa_cache.sv", "rtl/line_iotlb.sv", "rtl/mem_master.sv",
        "rtl/prefetch_ctrl.sv", "rtl/iommu_top.sv"]
KNOBS = dict(PERIOD=2.5, SDC=1, RDES=1, RTIM=1, MAXTRANS=0.5, MAXFO=12, SLEWM=10, CAPM=10, UTIL=35)
ACT = 0.12
PERIOD = 2.5
# cfg: (HAS_PWC, HAS_IOTLB, NCTX, BUF, CO, PF)
CFGS = {
    "cfg2": (1, 0, 5, 5, 1, 0),
    "cfg3": (1, 1, 1, 5, 8, 0),
    "cfg4": (1, 1, 1, 1, 8, 1),
}
PORTS = """  input  logic clk, rst_n,
  input  logic pl_valid, input logic [1:0] pl_sel, input logic [PPN_W-1:0] pl_data,
  input  logic req_valid, output logic req_ready,
  input  logic [VPN_W-1:0] req_vpn, input logic [DEVICE_W-1:0] req_device_id,
  input  logic [PASID_W-1:0] req_pasid, input logic req_is_write,
  output logic rsp_valid, input logic rsp_ready,
  output logic [VPN_W-1:0] rsp_vpn, output logic [SPA_W-1:0] rsp_spa,
  output logic arvalid, input logic arready, output logic [PA_W-1:0] araddr,
  output logic [TAG_W_TOP-1:0] arid, output logic [2:0] arlen,
  input  logic rvalid, output logic rready, input logic [PTE_W-1:0] rdata,
  input  logic [TAG_W_TOP-1:0] rid, input logic rlast,
  output logic [31:0] walks_o, resp_o, outstanding_o"""


def gen_top(mod, p, ctx):
    pwc, iot, nctx, buf, co, pf = p
    return f"""import iommu_pkg::*;
module {mod} (
{PORTS}
);
  iommu_top #(
    .HAS_PWC({pwc}), .HAS_IOTLB({iot}), .NUM_WALKERS({nctx}), .BUFFER_DEPTH({buf}),
    .COALESCE_FACTOR({co}), .PREFETCH_EN({pf}), .PREFETCH_LEAD(1), .TAG_CONTEXT_EN({ctx}),
    .MEM_LATENCY_CYCLES(40), .MEM_MAX_OUTSTANDING(8), .PIPELINE_DEPTH(1)
  ) u_core (.*);
endmodule
"""


def drun(envs, cmd):
    eargs = []
    for k, v in envs.items():
        eargs += ["-e", f"{k}={v}"]
    return subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT}:{D}", *eargs,
                           IMAGE, "--skip", "bash", "-lc", cmd], capture_output=True, text=True)


def num(x):
    return float(x) if x else None


res = {}
for cfg, p in CFGS.items():
    for ctx in (1, 0):
        tag = "ctx" if ctx else "noctx"
        mod = f"{cfg}_{tag}_top"
        wsdir = ROOT / "syn" / "notag_build" / f"{cfg}_{tag}"
        wsdir.mkdir(parents=True, exist_ok=True)
        (wsdir / f"{mod}.sv").write_text(gen_top(mod, p, ctx))
        srcs = " ".join(f"{D}/{f}" for f in CORE) + f" {D}/syn/notag_build/{cfg}_{tag}/{mod}.sv"
        wd = f"syn/notag_build/{cfg}_{tag}"
        ys = (f"read_verilog {D}/{wd}/d.v\nhierarchy -top {mod} -check\nsynth -top {mod}\n"
              f"dfflibmap -liberty {LIB}\nabc -liberty {LIB} -D {int(PERIOD*1000)}\nclean -purge\n"
              f"tee -o {D}/{wd}/synth_area.txt stat -liberty {LIB}\nflatten\nclean -purge\n"
              f"write_verilog -noattr {D}/{wd}/netlist.v\n")
        synbash = (f"sv2v -D SYNTHESIS {srcs} > {D}/{wd}/d.v 2>{D}/{wd}/sv2v.log && "
                   f"cat > {D}/{wd}/s.ys <<'YE'\n{ys}\nYE\n"
                   f"yosys -q {D}/{wd}/s.ys 2>&1 | tee {D}/{wd}/yosys.log")
        print(f"[{cfg}/{tag}] synth ...", flush=True)
        r = drun({}, synbash)
        (wsdir / "flow.log").write_text(r.stdout + "\n=ERR=\n" + r.stderr)
        ar = re.search(r"Chip area for top module[^:]*: *([\d.]+)", (wsdir / "synth_area.txt").read_text())
        syn_area = num(ar.group(1)) if ar else None
        env = dict(TOP=mod, SITE="unithd", ACT=ACT, NET=f"{D}/{wd}/netlist.v",
                   LIB=LIB, TLEF=TLEF, CLEF=CLEF, DRVCELL="sky130_fd_sc_hd__buf_2", **KNOBS)
        print(f"[{cfg}/{tag}] post-opt ...", flush=True)
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
        res[(cfg, tag)] = dict(syn_area=syn_area, fmax=fmax, area=area, pwr=pwr)
        print(f"  -> Fmax {fmax and round(fmax,1)} MHz, area {area}, power {pwr and round(pwr,1)} mW")

print("\n==== cfg2/3/4: context tag (device_id 24b + PASID 20b = 44b) in cache tags ====")
print(f"{'cfg':<6}{'ctx?':<7}{'Fmax_MHz':>9}{'area_um2':>10}{'power_mW':>9}{'synth_area':>11}")
for cfg in CFGS:
    for tag in ("ctx", "noctx"):
        r = res[(cfg, tag)]
        g = lambda k, f="{:.1f}": (f.format(r[k]) if r.get(k) is not None else "n/a")
        print(f"{cfg:<6}{tag:<7}{g('fmax'):>9}{g('area','{:.0f}'):>10}{g('pwr'):>9}{g('syn_area','{:.0f}'):>11}")
    rc, rn = res[(cfg, 'ctx')], res[(cfg, 'noctx')]
    if rc['area'] and rn['area']:
        da = 100 * (rn['area'] - rc['area']) / rc['area']
        df = 100 * (rn['fmax'] - rc['fmax']) / rc['fmax'] if rc['fmax'] and rn['fmax'] else None
        dp = 100 * (rn['pwr'] - rc['pwr']) / rc['pwr'] if rc['pwr'] and rn['pwr'] else None
        print(f"  delta noctx vs ctx: area {da:+.1f}%, Fmax {df:+.1f}%, power {dp:+.1f}%")
