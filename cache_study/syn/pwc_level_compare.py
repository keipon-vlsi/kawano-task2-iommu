#!/usr/bin/env python3
"""Multi-level PWC lookup: PARALLEL+priority (1 cycle, all levels) vs SEQUENTIAL (probe
leaf-nearest first, advance on miss, 1..3 cycles). Same storage (near L1 18b, mid L2 9b,
root reg) via fa_cache. Functional check (iverilog) + synth (Yosys, lpflow-filtered hd) +
OpenROAD repair for Fmax. Reports area / cells / DFF / Fmax / depth + latency note.
Run: .venv/bin/python3 cache_study/syn/pwc_level_compare.py
"""
import os, re, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; CS="cache_study"
IMG="hpretl/iic-osic-tools:latest"; D="/foss/designs"
B="/foss/pdks/sky130A/libs.ref/sky130_fd_sc_hd"
LIB=f"{B}/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"; LIBF=f"{D}/{CS}/results/sky130_hd_nolp.lib"
P=2.0; POPT=1.0; ABC=400

# wrappers: register IO so the measured path is clean reg->reg.
WRAP_PAR="""module qor_top(input logic clk,rst_n,
  input logic [26:0] lk_vpn_i, output logic [27:0] base_o, output logic [1:0] lvl_o,
  input logic fn_en,input logic [17:0] fn_tag,input logic [27:0] fn_d,
  input logic fm_en,input logic [8:0] fm_tag,input logic [27:0] fm_d,
  input logic fr_en,input logic [27:0] fr_d);
  logic [26:0] vq; logic [27:0] bc; logic [1:0] lc; logic [27:0] bq; logic [1:0] lq;
  pwc_lvl_par u(.clk,.rst_n,.lk_vpn(vq),.lk_base(bc),.lk_lvl(lc),
    .fn_en,.fn_tag,.fn_d,.fm_en,.fm_tag,.fm_d,.fr_en,.fr_d);
  always_ff @(posedge clk) begin vq<=lk_vpn_i; bq<=bc; lq<=lc; end
  assign base_o=bq; assign lvl_o=lq; endmodule"""
WRAP_SEQ="""module qor_top(input logic clk,rst_n,
  input logic req_i, input logic [26:0] lk_vpn_i,
  output logic rv_o, output logic [27:0] base_o, output logic [1:0] lvl_o,
  input logic fn_en,input logic [17:0] fn_tag,input logic [27:0] fn_d,
  input logic fm_en,input logic [8:0] fm_tag,input logic [27:0] fm_d,
  input logic fr_en,input logic [27:0] fr_d);
  logic reqq; logic [26:0] vq; logic rv; logic [27:0] bc; logic [1:0] lc; logic rvq; logic [27:0] bq; logic [1:0] lq;
  pwc_lvl_seq u(.clk,.rst_n,.req(reqq),.lk_vpn(vq),.resp_valid(rv),.lk_base(bc),.lk_lvl(lc),
    .fn_en,.fn_tag,.fn_d,.fm_en,.fm_tag,.fm_d,.fr_en,.fr_d);
  always_ff @(posedge clk) begin reqq<=req_i; vq<=lk_vpn_i; rvq<=rv; bq<=bc; lq<=lc; end
  assign rv_o=rvq; assign base_o=bq; assign lvl_o=lq; endmodule"""

# functional TBs: fill L1/L2/root, then check most-complete-hit selection.
TB_PAR="""`timescale 1ns/1ps
module tb; logic clk=0,rst_n=0; logic [26:0] v; logic [27:0] b; logic [1:0] l;
 logic fn,fm,fr; logic [17:0] fnt; logic [8:0] fmt; logic [27:0] fnd,fmd,frd; int e=0;
 qor_top d(.clk,.rst_n,.lk_vpn_i(v),.base_o(b),.lvl_o(l),.fn_en(fn),.fn_tag(fnt),.fn_d(fnd),
   .fm_en(fm),.fm_tag(fmt),.fm_d(fmd),.fr_en(fr),.fr_d(frd));
 always #5 clk=~clk;
 task fill(input int w,input [17:0] t,input [27:0] dd); begin @(negedge clk);
   fn=0;fm=0;fr=0; if(w==2)begin fn=1;fnt=t;fnd=dd;end if(w==1)begin fm=1;fmt=t[8:0];fmd=dd;end
   if(w==0)begin fr=1;frd=dd;end @(negedge clk); fn=0;fm=0;fr=0; end endtask
 task chk(input [26:0] vv,input [1:0] el,input [27:0] eb); begin @(negedge clk); v=vv;
   @(posedge clk); @(posedge clk); #1;   // 2-stage wrapper: vq then bq
   if(l!==el||b!==eb)begin e++; $display("  MISS v=%h l=%0d/%0d b=%h/%h",vv,l,el,b,eb);end end endtask
 initial begin fn=0;fm=0;fr=0; @(negedge clk); rst_n=1;
   fill(0,0,28'h7000);                 // root
   fill(1,18'h0AA,28'h1100);           // L2 tag VPN[26:18]=0xAA  -> for vpn[26:18]=0x0AA
   fill(2,18'h2AAAA,28'h2200);         // L1 tag VPN[26:9]=0x2AAAA
   // vpn whose [26:9]=0x2AAAA AND [26:18]=0x155 ... build a vpn that hits L1
   chk({18'h2AAAA,9'h0},2'd2,28'h2200);          // L1 hit (most complete) -> near
   chk({9'h0AA,18'h001},2'd1,28'h1100);          // [26:18]=0x0AA hits L2, [26:9]!=2AAAA -> mid
   chk(27'h7FFFFFF,2'd0,28'h7000);               // neither -> root
   if(e==0)$display("PWC_LVL_PAR PASS"); else $display("PWC_LVL_PAR FAIL %0d",e); $finish; end
endmodule"""
TB_SEQ="""`timescale 1ns/1ps
module tb; logic clk=0,rst_n=0; logic rq; logic [26:0] v; logic rv; logic [27:0] b; logic [1:0] l;
 logic fn,fm,fr; logic [17:0] fnt; logic [8:0] fmt; logic [27:0] fnd,fmd,frd; int e=0;
 qor_top d(.clk,.rst_n,.req_i(rq),.lk_vpn_i(v),.rv_o(rv),.base_o(b),.lvl_o(l),.fn_en(fn),.fn_tag(fnt),
   .fn_d(fnd),.fm_en(fm),.fm_tag(fmt),.fm_d(fmd),.fr_en(fr),.fr_d(frd));
 always #5 clk=~clk;
 task fill(input int w,input [17:0] t,input [27:0] dd); begin @(negedge clk);
   fn=0;fm=0;fr=0; if(w==2)begin fn=1;fnt=t;fnd=dd;end if(w==1)begin fm=1;fmt=t[8:0];fmd=dd;end
   if(w==0)begin fr=1;frd=dd;end @(negedge clk); fn=0;fm=0;fr=0; end endtask
 task probe(input [26:0] vv,input [1:0] el,input [27:0] eb); int g; begin @(negedge clk);
   v=vv; rq=1; @(negedge clk); rq=0; g=0;
   while(!rv && g<8) begin @(negedge clk); g++; end
   if(l!==el||b!==eb)begin e++; $display("  MISS v=%h l=%0d/%0d b=%h/%h",vv,l,el,b,eb);end end endtask
 initial begin rq=0;fn=0;fm=0;fr=0; @(negedge clk); rst_n=1;
   fill(0,0,28'h7000); fill(1,18'h0AA,28'h1100); fill(2,18'h2AAAA,28'h2200);
   probe({18'h2AAAA,9'h0},2'd2,28'h2200); probe({9'h0AA,18'h001},2'd1,28'h1100); probe(27'h7FFFFFF,2'd0,28'h7000);
   if(e==0)$display("PWC_LVL_SEQ PASS"); else $display("PWC_LVL_SEQ FAIL %0d",e); $finish; end
endmodule"""

VARS=[("par","pwc_lvl_par",WRAP_PAR,TB_PAR),("seq","pwc_lvl_seq",WRAP_SEQ,TB_SEQ)]
res=ROOT/CS/"results"; res.mkdir(exist_ok=True)
def num(x): return float(x) if x else None
out={}
for nm,mod,wrap,tb in VARS:
    (res/f"lvl_{nm}.wrap.sv").write_text(wrap); (res/f"lvl_{nm}.tb.sv").write_text(tb)
    src=f"{D}/rtl/fa_cache.sv {D}/{CS}/pwc/{mod}.sv {D}/{CS}/results/lvl_{nm}.wrap.sv"
    ys=(f"read_verilog -sv {src}\nhierarchy -top qor_top -check\nsynth -top qor_top -flatten\n"
        f"dfflibmap -liberty {LIBF}\nabc -liberty {LIBF} -D {ABC}\nclean -purge\n"
        f"tee -o {D}/{CS}/results/lvl_{nm}.stat.txt stat -liberty {LIBF}\n"
        f"write_verilog -noattr {D}/{CS}/results/lvl_{nm}.netlist.v\n")
    sta=(f"read_liberty {LIBF}\nread_verilog {D}/{CS}/results/lvl_{nm}.netlist.v\nlink_design qor_top\n"
         f"create_clock -name clk -period {P} [get_ports clk]\nreport_worst_slack -max\nexit\n")
    bash=(f"set -e;cd {D}/{CS}/results;[ -f {LIBF} ]||python3 {D}/{CS}/syn/filter_lib.py {LIB} {LIBF};"
          f"(iverilog -g2012 -s tb -o lvl_{nm}.vvp {D}/{CS}/results/lvl_{nm}.tb.sv "
          f"  {D}/rtl/fa_cache.sv {D}/{CS}/pwc/{mod}.sv {D}/{CS}/results/lvl_{nm}.wrap.sv && vvp lvl_{nm}.vvp)"
          f"  > lvl_{nm}.func.log 2>&1 || true;"
          f"cat>lvl_{nm}.ys<<'YE'\n{ys}\nYE\ncat>lvl_{nm}.sta<<'SE'\n{sta}\nSE\n"
          f"yosys -q lvl_{nm}.ys>lvl_{nm}.synth.log 2>&1;sta -no_init -exit lvl_{nm}.sta>lvl_{nm}.sta.log 2>&1;"
          f"export TOP=qor_top SITE=unithd ACT=0.1 NET={D}/{CS}/results/lvl_{nm}.netlist.v LIB={LIBF} "
          f"TLEF={B}/techlef/sky130_fd_sc_hd__nom.tlef CLEF={B}/lef/sky130_fd_sc_hd.lef "
          f"DRVCELL=sky130_fd_sc_hd__buf_2 PERIOD={POPT} SDC=1 RDES=1 RTIM=1 MAXTRANS=0.4 MAXFO=8 SLEWM=0 CAPM=0 UTIL=40;"
          f"openroad -no_init -exit {D}/syn/fmax_opt/opt.tcl>lvl_{nm}.opt.log 2>&1||true;echo DONE")
    print(f"[lvl_{nm}] run ...",flush=True)
    subprocess.run(["docker","run","--rm","-v",f"{ROOT}:{D}",IMG,"--skip","bash","-lc",bash],
                   capture_output=True,text=True)
    func=(res/f"lvl_{nm}.func.log").read_text(); stat=(res/f"lvl_{nm}.stat.txt").read_text()
    opt=(res/f"lvl_{nm}.opt.log").read_text() if (res/f"lvl_{nm}.opt.log").exists() else ""
    fp="PASS" in func and "FAIL" not in func
    ar=re.search(r"Chip area for (?:top )?module[^:]*: *([\d.]+)",stat); area=num(ar.group(1)) if ar else None
    dff=sum(int(m.group(1)) for m in re.finditer(r"^\s*(\d+)\s+[\d.eE+]+\s+sky130_fd_sc_hd__df",stat,re.M))
    cells=sum(int(x) for x in re.findall(r"^\s*(\d+)\s+[\d.eE+]+\s+sky130_fd_sc_hd__",stat,re.M))
    pa=opt[opt.find("##PATH"):] if "##PATH" in opt else opt
    blk=re.search(r"Startpoint: (\S+) \(rising edge.*?Endpoint: (\S+) \(rising edge.*?slack \((?:VIOLATED|MET)\)",pa,re.S)
    fmax=None;depth=0
    if blk:
        body=blk.group(0); ms=re.search(r"(-?[\d.]+)\s+slack",body)
        if ms: s=float(ms.group(1)); fmax=1000.0/(POPT-s) if POPT-s>0 else None
        depth=sum(1 for c in re.finditer(r"\(sky130_fd_sc_hd__(\w+)\)",body)
                  if not c.group(1).startswith("df") and not re.match(r"(buf|clkbuf|dlymetal|dlygate|conb)",c.group(1)))
    out[nm]=dict(func=fp,area=area,cells=cells,dff=dff,fmax=round(fmax,1) if fmax else None,depth=depth)
    print(f"  func={fp} Fmax={out[nm]['fmax']} area={round(area) if area else None} cells={cells} DFF={dff} depth={depth}")

print("\n==== multi-level PWC lookup: PARALLEL+priority vs SEQUENTIAL(probe leaf-first) ====")
print(f"{'scheme':<10}{'func':>5}{'Fmax':>8}{'area':>8}{'cells':>7}{'DFF':>5}{'depth':>6}{'latency':>22}")
print(f"{'parallel':<10}{('OK' if out['par']['func'] else 'X'):>5}{str(out['par']['fmax']):>8}"
      f"{str(round(out['par']['area']) if out['par']['area'] else None):>8}{out['par']['cells']:>7}"
      f"{out['par']['dff']:>5}{out['par']['depth']:>6}{'1 cycle (always)':>22}")
print(f"{'sequential':<10}{('OK' if out['seq']['func'] else 'X'):>5}{str(out['seq']['fmax']):>8}"
      f"{str(round(out['seq']['area']) if out['seq']['area'] else None):>8}{out['seq']['cells']:>7}"
      f"{out['seq']['dff']:>5}{out['seq']['depth']:>6}{'1-3 cyc (~1 steady)':>22}")
