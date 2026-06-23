#!/usr/bin/env python3
"""ROUTING-aware sweep: shared line-IOTLB serving NP subjects, (A) multiport vs
(B) 1-port+MUX, NP in {1,2,4,6,8,10}. Per (cfg,NP): synth -> netlist, then OpenROAD
floorplan + global_place + global_route at FIXED utilization. Report post-place die area
and global-route congestion (overflow). This captures the wiring cost that the cell-area
sweep (ports_area_sweep.py) misses -- the MUX version concentrates NP*27 tag wires into
one point, so congestion should grow with NP even though cells barely do.
Run: .venv/bin/python3 cache_study/syn/ports_route_sweep.py
"""
import re, subprocess, csv, os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; CS="cache_study"
IMG="hpretl/iic-osic-tools:latest"; D="/foss/designs"
B="/foss/pdks/sky130A/libs.ref/sky130_fd_sc_hd"
LIB=f"{B}/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"; LIBF=f"{D}/{CS}/results/sky130_hd_nolp.lib"
NPS=[1,2,4,6,8,10]; CFGS=[("mport","iotlb_mport"),("muxport","iotlb_muxport")]
UTIL=45
res=ROOT/CS/"results"; res.mkdir(exist_ok=True)

def one(cfg,top,n):
    wd=f"{CS}/results/portsrt/{cfg}_{n}"; (ROOT/wd).mkdir(parents=True,exist_ok=True)
    ys=(f"read_verilog /tmp/p.v\nhierarchy -top {top}\nsynth -top {top} -flatten\n"
        f"dfflibmap -liberty {LIBF}\nabc -liberty {LIBF} -D 800\nclean -purge\n"
        f"write_verilog -noattr {D}/{wd}/net.v\n")
    bash=(f"[ -f {LIBF} ] || python3 {D}/{CS}/syn/filter_lib.py {LIB} {LIBF}; "
          f"sv2v -DNP={n} {D}/{CS}/iotlb/iotlb_ports.sv > /tmp/p.v 2>/dev/null; "
          f"cat > /tmp/y.ys <<'YE'\n{ys}\nYE\n"
          f"yosys -q /tmp/y.ys 2>/dev/null; "
          f"export TOP={top} SITE=unithd NET={D}/{wd}/net.v LIB={LIBF} "
          f"TLEF={B}/techlef/sky130_fd_sc_hd__nom.tlef CLEF={B}/lef/sky130_fd_sc_hd.lef UTIL={UTIL}; "
          f"openroad -no_init -exit {D}/{CS}/syn/ports_route.tcl 2>&1")
    r=subprocess.run(["docker","run","--rm","-v",f"{ROOT}:{D}",IMG,"--skip","bash","-lc",bash],
                     capture_output=True,text=True)
    out=r.stdout; (ROOT/wd/"route.log").write_text(out)
    area=None; util=None; m=re.search(r"##AREA\s*\nDesign area\s+([\d.]+)\s+um\^2\s+(\d+)%",out)
    if m: area=float(m.group(1)); util=int(m.group(2))
    cong=out[out.find("##CONG"):] if "##CONG" in out else out
    # total routed wirelength = sum of per-net global-route wire length (GRT-0237)
    wl=sum(float(x) for x in re.findall(r"global route wire length:\s*([\d.]+)um",cong))
    # final placement HPWL (placer wirelength estimate) -- last HPWL value in the gpl table
    hp=re.findall(r"\|\s*([\d.]+e\+\d+)\s*\|\s*[-+][\d.]+%",out)
    hpwl=float(hp[-1]) if hp else None
    # congestion overflow (if any)
    ov=None; mo=re.search(r"[Tt]otal\s+overflow[:=\s]+(\d+)",cong)
    if mo: ov=int(mo.group(1))
    failed = bool(re.search(r"congestion.*(cannot|fail)|Routing congestion too high",cong,re.I))
    return dict(area=area,util=util,wl=round(wl,1),hpwl=hpwl,overflow=ov,failed=failed)

rows={}
for cfg,top in CFGS:
    for n in NPS:
        print(f"[{cfg} NP={n}] floorplan+place+route ...",flush=True)
        rows[(cfg,n)]=one(cfg,top,n)
        d=rows[(cfg,n)]
        print(f"   die={d['area']} util%={d['util']} routed_WL={d['wl']}um HPWL={d['hpwl']} overflow={d['overflow']}")

with open(res/"ports_route.csv","w",newline="") as f:
    w=csv.writer(f); w.writerow(["config","NP","die_area_um2","util_pct","routed_wirelength_um","placer_hpwl","route_overflow"])
    for cfg,_ in CFGS:
        for n in NPS:
            d=rows[(cfg,n)]; w.writerow([cfg,n,d['area'],d['util'],d['wl'],d['hpwl'],d['overflow']])

print("\n{:<8}{:>4}{:>11}{:>7}{:>14}{:>11}".format("cfg","NP","die_area","util%","routed_WL_um","overflow"))
for cfg,_ in CFGS:
    for n in NPS:
        d=rows[(cfg,n)]
        print("{:<8}{:>4}{:>11}{:>7}{:>14}{:>11}".format(cfg,n,str(d['area']),str(d['util']),str(d['wl']),str(d['overflow'])))

# plot: routed wirelength (the wiring cost) vs NP, both configs
import matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
plt.rcParams['font.family']=['Noto Sans CJK JP','DejaVu Sans']; plt.rcParams['axes.unicode_minus']=False
fig,(a1,a2)=plt.subplots(1,2,figsize=(14,6))
for cfg,lab,col in [("mport","(A) N ポート並列",'#c0392b'),("muxport","(B) 1 ポート+N:1 MUX",'#2a7fb8')]:
    ns=[n for n in NPS if rows[(cfg,n)]['wl'] is not None]
    wl=[rows[(cfg,n)]['wl']/1000 for n in ns]; ar=[rows[(cfg,n)]['area']/1000 for n in ns]
    a1.plot(ns,wl,'o-',color=col,label=lab); a2.plot(ns,ar,'o-',color=col,label=lab)
a1.set_title("総ルーティング配線長 vs 主体数 (global route)",fontweight="bold"); a1.set_ylabel("routed wirelength [×1000 µm]")
a2.set_title("die 面積 vs 主体数 (post-place, util≈45%固定)",fontweight="bold"); a2.set_ylabel("die area [×1000 µm²]")
for a in (a1,a2): a.set_xlabel("ルックアップ主体数 NP"); a.grid(True,ls='--',alpha=0.4); a.legend()
fig.suptitle("配線込み：共有 line-IOTLB を NP 主体で共有 — N ポート vs 1 ポート+MUX (sky130 hd, global route)",
             fontsize=12,fontweight="bold")
fig.tight_layout(rect=[0,0,1,0.95])
p=ROOT/CS/"figs/figures/ports_route.png"; fig.savefig(p,dpi=140,bbox_inches="tight")
print("\nwrote",p,"\nCSV: cache_study/results/ports_route.csv ; logs: results/portsrt/*/route.log")
