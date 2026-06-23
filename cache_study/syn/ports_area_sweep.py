#!/usr/bin/env python3
"""Area sweep: shared line-IOTLB serving NP subjects, NP=1..10, two ways:
(A) iotlb_mport  = NP parallel lookup ports (lookup logic x NP),
(B) iotlb_muxport = 1 port + N:1 input mux.
Synthesize each on sky130 hd; report total area, storage DFF area (~const), and lookup
combinational area (= total - DFF) which is the part that scales with NP.
Writes results/ports_area.csv + figs/figures/ports_area.png.
Run: .venv/bin/python3 cache_study/syn/ports_area_sweep.py
"""
import re, subprocess, csv, os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; CS="cache_study"
IMG="hpretl/iic-osic-tools:latest"; D="/foss/designs"
LIB="/foss/pdks/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
LIBF=f"{D}/{CS}/results/sky130_hd_nolp.lib"
NPS=list(range(1,11))
CFGS=[("mport","iotlb_mport"),("muxport","iotlb_muxport")]

# one container run: loop cfg x NP, sv2v -DNP=n, yosys stat, print "RES <cfg> <n> <area> <dffarea>"
lines=[]
for cfg,top in CFGS:
    for n in NPS:
        lines.append(
          f'sv2v -DNP={n} {D}/{CS}/iotlb/iotlb_ports.sv > /tmp/p.v 2>/dev/null; '
          f'yosys -q -p "read_verilog /tmp/p.v; hierarchy -top {top}; synth -top {top} -flatten; '
          f'dfflibmap -liberty {LIBF}; abc -liberty {LIBF} -D 800; clean -purge; '
          f'tee -o /tmp/s.txt stat -liberty {LIBF}" 2>/dev/null; '
          f'A=$(grep -m1 "Chip area for" /tmp/s.txt | grep -oE "[0-9.]+" | tail -1); '
          f'Dn=$(grep -E "sky130_fd_sc_hd__df" /tmp/s.txt | awk "{{s+=\\$2}} END{{print s}}"); '
          f'echo "RES {cfg} {n} $A $Dn"')
bash = (f'[ -f {LIBF} ] || python3 {D}/{CS}/syn/filter_lib.py {LIB} {LIBF}; ' + " ; ".join(lines))
print("synthesizing 2 cfgs x 10 NP ...", flush=True)
r=subprocess.run(["docker","run","--rm","-v",f"{ROOT}:{D}",IMG,"--skip","bash","-lc",bash],
                 capture_output=True,text=True)
rows={}
for m in re.finditer(r"RES (\w+) (\d+) ([\d.]+) ([\d.]+)", r.stdout):
    cfg,n,area,dff=m.group(1),int(m.group(2)),float(m.group(3)),float(m.group(4))
    rows[(cfg,n)]=(area,dff,area-dff)   # total, storage(DFF), lookup-comb
if not rows:
    print("NO RESULTS\n",r.stdout[-2000:],"\nSTDERR\n",r.stderr[-1000:]); raise SystemExit(1)

# CSV
res=ROOT/CS/"results"; res.mkdir(exist_ok=True)
with open(res/"ports_area.csv","w",newline="") as f:
    w=csv.writer(f); w.writerow(["config","NP","total_um2","storage_dff_um2","lookup_comb_um2"])
    for cfg,_ in CFGS:
        for n in NPS:
            if (cfg,n) in rows:
                t,d,c=rows[(cfg,n)]; w.writerow([cfg,n,round(t,1),round(d,1),round(c,1)])
print("\n{:<8}{:>4}{:>11}{:>13}{:>14}".format("cfg","NP","total","storage_DFF","lookup_comb"))
for cfg,_ in CFGS:
    for n in NPS:
        if (cfg,n) in rows:
            t,d,c=rows[(cfg,n)]
            print("{:<8}{:>4}{:>11.0f}{:>13.0f}{:>14.0f}".format(cfg,n,t,d,c))

# plot
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
plt.rcParams['font.family']=['Noto Sans CJK JP','DejaVu Sans']; plt.rcParams['axes.unicode_minus']=False
fig,(a1,a2)=plt.subplots(1,2,figsize=(14,6))
for cfg,lab,col in [("mport","(A) N ポート並列",'#c0392b'),("muxport","(B) 1 ポート+N:1 MUX",'#2a7fb8')]:
    ns=[n for n in NPS if (cfg,n) in rows]
    tot=[rows[(cfg,n)][0]/1000 for n in ns]; lk=[rows[(cfg,n)][2]/1000 for n in ns]
    a1.plot(ns,tot,'o-',color=col,label=lab)
    a2.plot(ns,lk,'o-',color=col,label=lab)
# storage line (const)
st=[rows[("mport",n)][1]/1000 for n in NPS if ("mport",n) in rows]
a1.axhline(st[0],ls='--',color='#888',label='ストレージDFF (一定)')
for a,t in [(a1,"総面積 vs 主体数"),(a2,"ルックアップロジック面積 (= 総 − ストレージDFF) vs 主体数")]:
    a.set_xlabel("ルックアップ主体数 NP"); a.set_ylabel("面積 [×1000 µm²]"); a.set_title(t,fontweight="bold")
    a.grid(True,ls='--',alpha=0.4); a.legend()
fig.suptitle("共有 line-IOTLB（2×8）を NP 主体で共有：N ポート並列 vs 1 ポート+MUX の面積スケーリング (sky130 hd)",
             fontsize=12,fontweight="bold")
fig.tight_layout(rect=[0,0,1,0.95])
p=ROOT/CS/"figs/figures/ports_area.png"; fig.savefig(p,dpi=140,bbox_inches="tight")
print("\nwrote",p); print("wrote",res/"ports_area.csv")
