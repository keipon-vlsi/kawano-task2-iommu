#!/usr/bin/env python3
"""QoR driver for the cache_study variants. Per variant: (1) functional check (iverilog,
shared tb), (2) synthesis (Yosys + sky130_fd_sc_hd + abc speed target) -> area / cell /
DFF breakdown, (3) STA (OpenSTA) -> Fmax, logic depth, critical-path location.
Each variant is wrapped with input/output registers so the measured path is a clean
reg->reg lookup path. Writes cache_study/results/{<v>.json, <v>.{func,synth,sta}.log}
and cache_study/results/summary.json.  Usage: python3 cache_study/syn/run_qor.py [names...]
"""
import json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CS = "cache_study"
IMAGE = "hpretl/iic-osic-tools:latest"
D = "/foss/designs"
LIB = "/foss/pdks/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
LIBF = f"{D}/{CS}/results/sky130_hd_nolp.lib"   # filtered: drop lpflow* iso/clamp cells
B = "/foss/pdks/sky130A/libs.ref/sky130_fd_sc_hd"
P = 2.0           # unbuffered-STA clock period [ns] (for logic depth / structure)
POPT = 1.0        # OpenROAD repair target period [ns] (for realistic Fmax)
ABC_D = 400       # abc delay target [ps]


# (name, kind, file, module). kind: pwc (tag 18b) | iotlb (tag 27b)
VARIANTS = [
    ("pwc_p0", "pwc", "pwc/pwc_p0.sv", "pwc_p0"),
    ("pwc_p1", "pwc", "pwc/pwc_p1.sv", "pwc_p1"),
    ("pwc_p2", "pwc", "pwc/pwc_p2.sv", "pwc_p2"),
    ("pwc_p3", "pwc", "pwc/pwc_p3.sv", "pwc_p3"),
    ("pwc_p4", "pwc", "pwc/pwc_p4.sv", "pwc_p4"),
    ("iotlb_t0", "iotlb", "iotlb/iotlb_t0.sv", "iotlb_t0"),
    ("iotlb_t1", "iotlb", "iotlb/iotlb_t1.sv", "iotlb_t1"),
    ("iotlb_t2", "iotlb", "iotlb/iotlb_t2.sv", "iotlb_t2"),
    ("iotlb_t3", "iotlb", "iotlb/iotlb_t3.sv", "iotlb_t3"),
    ("iotlb_t4", "iotlb", "iotlb/iotlb_t4.sv", "iotlb_t4"),
    ("iotlb_t5", "iotlb", "iotlb/iotlb_t5.sv", "iotlb_t5"),
    ("iotlb_t6", "iotlb", "iotlb/iotlb_t6.sv", "iotlb_t6"),
    ("iotlb_t7", "iotlb", "iotlb/iotlb_t7.sv", "iotlb_t7"),
    ("iotlb_t8", "iotlb", "iotlb/iotlb_t8.sv", "iotlb_t8"),
    ("iotlb_t0x3", "iotlb", "iotlb/iotlb_t0x3.sv", "iotlb_t0x3"),
    ("iotlb_t8k", "iotlb", "iotlb/iotlb_t8k.sv", "iotlb_t8k"),
]


def tw(kind):
    return 18 if kind == "pwc" else 27


def wrapper(kind, mod):
    w = tw(kind)
    return f"""module qor_top (
  input logic clk, rst_n,
  input  logic [{w-1}:0] lk_tag_i,
  output logic        lk_hit_o,
  output logic [43:0] lk_spa_o,
  input  logic        fill_en,
  input  logic [{w-1}:0] fill_tag,
  input  logic [43:0] fill_spa
);
  logic [{w-1}:0] lkt_q; logic hc; logic [43:0] sc; logic hq; logic [43:0] sq;
  {mod} u (.clk, .rst_n, .lk_tag(lkt_q), .lk_hit(hc), .lk_spa(sc),
           .fill_en, .fill_tag, .fill_spa);
  always_ff @(posedge clk) begin lkt_q <= lk_tag_i; hq <= hc; sq <= sc; end
  assign lk_hit_o = hq; assign lk_spa_o = sq;
endmodule
"""


def run(name, kind, f, mod):
    res = ROOT / CS / "results"
    res.mkdir(exist_ok=True)
    (res / f"{name}.wrap.sv").write_text(wrapper(kind, mod))
    tb = f"{D}/{CS}/tb/{kind}_tb.sv"
    src = f"{D}/{CS}/{f}"
    wrp = f"{D}/{CS}/results/{name}.wrap.sv"
    ys = f"""
read_verilog -sv {src} {wrp}
hierarchy -top qor_top -check
synth -top qor_top -flatten
dfflibmap -liberty {LIBF}
abc -liberty {LIBF} -D {ABC_D}
clean -purge
tee -o {D}/{CS}/results/{name}.stat.txt stat -liberty {LIBF}
write_verilog -noattr {D}/{CS}/results/{name}.netlist.v
"""
    sta = f"""
read_liberty {LIBF}
read_verilog {D}/{CS}/results/{name}.netlist.v
link_design qor_top
create_clock -name clk -period {P} [get_ports clk]
puts "=== WNS ==="
report_worst_slack -max
puts "=== PATH ==="
report_checks -path_delay max -fields {{slew cap}} -digits 4 -group_count 1
exit
"""
    bash = (
        f"set -e; cd {D}/{CS}/results; "
        f"[ -f {LIBF} ] || python3 {D}/{CS}/syn/filter_lib.py {LIB} {LIBF}; "
        # functional (iverilog)
        f"(iverilog -g2012 -DDUT={mod} -s {kind}_tb -o {name}.vvp {tb} {src} "
        f" && vvp {name}.vvp) > {name}.func.log 2>&1 || true; "
        # synth + sta
        f"cat > {name}.ys <<'YE'\n{ys}\nYE\n"
        f"cat > {name}.sta <<'SE'\n{sta}\nSE\n"
        f"yosys -q {name}.ys > {name}.synth.log 2>&1; "
        f"sta -no_init -exit {name}.sta > {name}.sta.log 2>&1; "
        # realistic Fmax: OpenROAD repair_design+repair_timing (buffers the high-fanout SPA mux)
        f"export TOP=qor_top SITE=unithd ACT=0.1 NET={D}/{CS}/results/{name}.netlist.v "
        f"LIB={LIBF} TLEF={B}/techlef/sky130_fd_sc_hd__nom.tlef CLEF={B}/lef/sky130_fd_sc_hd.lef "
        f"DRVCELL=sky130_fd_sc_hd__buf_2 PERIOD=1.0 SDC=1 RDES=1 RTIM=1 MAXTRANS=0.4 MAXFO=8 "
        f"SLEWM=0 CAPM=0 UTIL=40; "
        f"openroad -no_init -exit {D}/syn/fmax_opt/opt.tcl > {name}.opt.log 2>&1 || true; "
        f"echo DONE"
    )
    r = subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT}:{D}", IMAGE, "--skip",
                        "bash", "-lc", bash], capture_output=True, text=True)
    return parse(name, res)


def parse(name, res):
    func = (res / f"{name}.func.log").read_text() if (res / f"{name}.func.log").exists() else ""
    stat = (res / f"{name}.stat.txt").read_text() if (res / f"{name}.stat.txt").exists() else ""
    sta = (res / f"{name}.sta.log").read_text() if (res / f"{name}.sta.log").exists() else ""
    func_pass = "PASS" in func and "FAIL" not in func
    # area + cells + DFF from yosys stat
    area = None; m = re.search(r"Chip area for (?:top )?module[^:]*: *([\d.]+)", stat)
    if m: area = float(m.group(1))
    m = re.search(r"Number of cells:\s+(\d+)", stat)
    cells = int(m.group(1)) if m else sum(
        int(x) for x in re.findall(r"^\s*(\d+)\s+[\d.eE+]+\s+sky130_fd_sc_hd__", stat, re.M))
    # DFF count + DFF area (sum dfxtp/dfrtp/dfstp rows: "<count> <area> sky130..df..")
    dff_n = 0; dff_a = 0.0
    for mm in re.finditer(r"^\s*(\d+)\s+([\d.eE+]+)\s+sky130_fd_sc_hd__(df\w+)", stat, re.M):
        dff_n += int(mm.group(1)); dff_a += float(mm.group(2))
    comb_a = (area - dff_a) if area is not None else None
    # unbuffered Fmax (structure-only, no buffering)
    wns = None; m = re.search(r"worst slack max\s+(-?[\d.]+)", sta)
    if m: wns = float(m.group(1))
    fmax_unbuf = (1000.0 / (P - wns)) if wns is not None and (P - wns) > 0 else None
    # realistic Fmax + true logic depth + critical-path structure from the OpenROAD
    # post-repair reg->reg worst path (##PATH section). Buffers/clk/delay cells excluded
    # from the logic-depth count.
    opt = (res / f"{name}.opt.log").read_text() if (res / f"{name}.opt.log").exists() else ""
    pa = opt[opt.find("##PATH"):] if "##PATH" in opt else opt
    # the reg->reg path block: Startpoint FF ... Endpoint FF ... slack
    blk = re.search(r"Startpoint: (\S+) \(rising edge.*?Endpoint: (\S+) \(rising edge.*?"
                    r"slack \((?:VIOLATED|MET)\)", pa, re.S)
    fmax = None; depth = 0; start = end = None; chain = []
    if blk:
        body = blk.group(0); start, end = blk.group(1), blk.group(2)
        ms = re.search(r"(-?[\d.]+)\s+slack \((?:VIOLATED|MET)\)", body)
        if ms:
            s = float(ms.group(1)); fmax = (1000.0 / (POPT - s)) if (POPT - s) > 0 else None
        for cm in re.finditer(r"\(sky130_fd_sc_hd__(\w+)\)", body):
            ct = cm.group(1)
            if ct.startswith("df") or re.match(r"(buf|clkbuf|dlymetal|dlygate|conb)", ct):
                continue
            chain.append(ct)
        depth = len(chain)
    out = dict(name=name, func_pass=func_pass, area_um2=area, cells=cells,
               dff_cells=dff_n, dff_area_um2=round(dff_a, 1) if dff_a else dff_a,
               comb_area_um2=round(comb_a, 1) if comb_a is not None else None,
               fmax_mhz=round(fmax, 1) if fmax else None,
               fmax_unbuf_mhz=round(fmax_unbuf, 1) if fmax_unbuf else None,
               logic_depth=depth, crit_path=" -> ".join(chain),
               crit_start=start, crit_end=end)
    (res / f"{name}.json").write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    sel = sys.argv[1:]
    rows = [run(n, k, f, m) for (n, k, f, m) in VARIANTS if not sel or n in sel]
    (ROOT / CS / "results" / "summary.json").write_text(json.dumps(rows, indent=2))
    print(f"{'variant':<16}{'func':>5}{'Fmax_MHz':>10}{'area':>9}{'cells':>7}{'DFF':>5}{'depth':>6}")
    for r in rows:
        print(f"{r['name']:<16}{('OK' if r['func_pass'] else 'FAIL'):>5}"
              f"{str(r['fmax_mhz']):>10}{str(round(r['area_um2']) if r['area_um2'] else None):>9}"
              f"{str(r['cells']):>7}{str(r['dff_cells']):>5}{str(r['logic_depth']):>6}")
