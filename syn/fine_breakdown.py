#!/usr/bin/env python3
"""Fine-grained per-config area breakdown.

Caches (fa_cache) are split tag / data+valid / lookup-logic analytically:
  tag FF        = ENTRIES * TAG_W                 (x A_FF)
  data+valid FF = ENTRIES * (DATA_W + 1) + PTRW   (x A_FF)
  lookup logic  = measured_instance_area - those  (CAM compare + priority enc + mux)
Control (iommu_top) is split by DFF register-group bit width (sequential) + remainder
(combinational arbiter / pte_addr adders / MSHR compare). FF counts/areas are read from
the Yosys stat reports; widths/params from the RTL.
"""
import math, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
A_FF, DATA_W = 25.02, 28
def clog2(n): return 1 if n < 2 else math.ceil(math.log2(n))

# name, dir, NCTX, BUFFER, COALESCE, has_iotlb, has_pf, TAG_CTX
CFGS = [("cfg1","cfg1_nocache",37,37,1,0,0,1),
        ("cfg2","cfg2_pwc",     5, 5,1,0,0,1),
        ("cfg3","cfg3_iotlb",   1, 5,8,1,0,1),
        ("cfg4","cfg4_prefetch",1,1,8,1,1,1),
        ("cfg5","cfg5_notag",   1,1,8,1,1,0)]

def areas(cfg):
    txt=(ROOT/cfg/"results"/"synth_area.txt").read_text()
    fa=[]; mods={}
    for n,a in re.findall(r"Chip area for module '([^']+)': *([\d.]+)", txt):
        a=float(a)
        if "fa_cache" in n: fa.append(a)
        elif "iommu_top" in n: mods["ctrl"]=a
        elif "mem_master" in n: mods["mem"]=a
        elif "prefetch_ctrl" in n: mods["pf"]=a
    return sorted(fa,reverse=True), mods

def dff(cfg):
    on=[]; f=False
    for ln in (ROOT/cfg/"results"/"synth_area.txt").read_text().splitlines():
        if "iommu_top ===" in ln: f=True
        if f and "Chip area for module" in ln and "iommu_top" in ln: break
        if f: on.append(ln)
    return sum(int(l.split()[0]) for l in on if re.search(r"__df(rtp|stp)",l))

def cache_split(meas, entries, tagw):
    ptr=clog2(entries)
    tag=entries*tagw*A_FF
    dv=(entries*(DATA_W+1)+ptr)*A_FF
    look=max(0.0, meas-tag-dv)
    return tag, dv, look

ROWS=["Walker RF (FF)","Transaction buffer (FF)",
      "Misc ctrl FF (roots/region/ctr)","Arbiter + pte_addr adders + MSHR (comb)",
      "PWC tag (FF)","PWC data+valid (FF)","PWC lookup logic (CAM+enc+mux)",
      "IOTLB tag (FF)","IOTLB data+valid (FF)","IOTLB lookup logic (CAM+enc+mux)",
      "prefetch_ctrl","mem_master"]
table={r:{} for r in ROWS}

for disp,cfg,nctx,buf,co,iot,pf,tc in CFGS:
    fa,mods=areas(cfg); nd=dff(cfg); tcw=36 if tc else 0
    seq=nd*A_FF; ctrl=mods["ctrl"]; comb_ctrl=max(0.0,ctrl-seq)
    vpnline=27 if co==1 else 24
    wl=(2+4+27+36+28+27+27+vpnline+4)*nctx           # +4 = wbeat (leaf-burst beat ctr)
    bf=(2+27+36+40)*buf
    ms=(28+18+1 if pf else 0)+56+64+clog2(nctx)+clog2(buf)
    tot=wl+bf+ms
    table["Walker RF (FF)"][disp]=seq*wl/tot
    table["Transaction buffer (FF)"][disp]=seq*bf/tot
    table["Misc ctrl FF (roots/region/ctr)"][disp]=seq*ms/tot
    table["Arbiter + pte_addr adders + MSHR (comb)"][disp]=comb_ctrl
    # caches
    pwc_t=pwc_d=pwc_l=iot_t=iot_d=iot_l=0.0
    if iot and len(fa)>=3:           # fa[0]=IOTLB, fa[1]=L1 x2, fa[2]=L2 x2
        iot_t,iot_d,iot_l=cache_split(fa[0],2*co,tcw+27)
        t,d,l=cache_split(fa[1],2,tcw+18); pwc_t+=2*t;pwc_d+=2*d;pwc_l+=2*l
        t,d,l=cache_split(fa[2],1,tcw+9);  pwc_t+=2*t;pwc_d+=2*d;pwc_l+=2*l
    elif (not iot) and len(fa)>=2:   # cfg2: fa[0]=L1 x2, fa[1]=L2 x2
        t,d,l=cache_split(fa[0],2,tcw+18); pwc_t+=2*t;pwc_d+=2*d;pwc_l+=2*l
        t,d,l=cache_split(fa[1],1,tcw+9);  pwc_t+=2*t;pwc_d+=2*d;pwc_l+=2*l
    table["PWC tag (FF)"][disp]=pwc_t
    table["PWC data+valid (FF)"][disp]=pwc_d
    table["PWC lookup logic (CAM+enc+mux)"][disp]=pwc_l
    table["IOTLB tag (FF)"][disp]=iot_t
    table["IOTLB data+valid (FF)"][disp]=iot_d
    table["IOTLB lookup logic (CAM+enc+mux)"][disp]=iot_l
    table["prefetch_ctrl"][disp]=mods.get("pf",0.0)
    table["mem_master"][disp]=mods.get("mem",0.0)

cols=[d for d,*_ in CFGS]
def cell(v): return f"{v:,.0f}" if v>=1 else "—"
print("| component | "+" | ".join(cols)+" |")
print("|"+"---|"*(len(cols)+1))
for r in ROWS:
    print(f"| {r} | "+" | ".join(cell(table[r].get(c,0)) for c in cols)+" |")
tot={c:sum(table[r].get(c,0) for r in ROWS) for c in cols}
print("| **total** | "+" | ".join(f"**{tot[c]:,.0f}**" for c in cols)+" |")
