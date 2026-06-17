#!/usr/bin/env python3
"""Per-module power ESTIMATE (no hierarchical VCD needed).

Power here is ~100% dynamic, ~80% sequential. So we apportion the STA-measured
sequential power by each module's flip-flop (sequential) area, and the combinational
power by each module's combinational area. Inputs: the fine-grained area breakdown
(syn/fine_breakdown.py logic) + the per-cfg sequential/combinational power from STA.
This is an apportionment, not a per-instance VCD measurement, but is accurate to the
extent FF power is uniform per FF and comb power is uniform per area.
"""
import math, re
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
A_FF, DATA_W = 25.02, 28
def clog2(n): return 1 if n < 2 else math.ceil(math.log2(n))

CFGS = [("cfg1","cfg1_nocache",37,37,1,0,0,1),("cfg2","cfg2_pwc",5,5,1,0,0,1),
        ("cfg3","cfg3_iotlb",1,5,8,1,0,1),("cfg4","cfg4_prefetch",1,1,8,1,1,1),
        ("cfg5","cfg5_notag",1,1,8,1,1,0)]
# measured (sequential_mW, combinational_mW) @400MHz act0.2 from sta.txt
PWR = {"cfg1":(177.18,73.00),"cfg2":(34.95,11.73),"cfg3":(52.53,12.71),
       "cfg4":(46.10,10.43),"cfg5":(30.70,6.99)}

def fa_areas(cfg):
    txt=(ROOT/cfg/"results"/"synth_area.txt").read_text()
    fa=[]; mods={}
    for n,a in re.findall(r"Chip area for module '([^']+)': *([\d.]+)", txt):
        a=float(a)
        if "fa_cache" in n: fa.append(a)
        elif "iommu_top" in n: mods["ctrl"]=a
        elif "mem_master" in n: mods["mem"]=a
        elif "prefetch_ctrl" in n: mods["pf"]=a
    return sorted(fa,reverse=True),mods
def dff(cfg):
    on=[];f=False
    for ln in (ROOT/cfg/"results"/"synth_area.txt").read_text().splitlines():
        if "iommu_top ===" in ln:f=True
        if f and "Chip area for module" in ln and "iommu_top" in ln:break
        if f:on.append(ln)
    return sum(int(l.split()[0]) for l in on if re.search(r"__df(rtp|stp)",l))
def csplit(meas,entries,tagw):
    ptr=clog2(entries); tag=entries*tagw*A_FF; dv=(entries*(DATA_W+1)+ptr)*A_FF
    return tag,dv,max(0.0,meas-tag-dv)

print(f"{'module':<16}"+"".join(f"{c:>9}" for c,*_ in CFGS)+"   (mW @400MHz act0.2)")
rows={m:{} for m in ["IOTLB","PWC(x4)","Control(wlk/buf/arb)","prefetch_ctrl","mem_master"]}
for disp,cfg,nctx,buf,co,iot,pf,tc in CFGS:
    fa,mods=fa_areas(cfg); nd=dff(cfg); tcw=36 if tc else 0
    seqA=nd*A_FF; ctrl=mods["ctrl"]; combA_ctrl=max(0.0,ctrl-seqA)
    # cache seq/comb
    it_seq=it_comb=pwc_seq=pwc_comb=0.0
    if iot and len(fa)>=3:
        t,d,l=csplit(fa[0],2*co,tcw+27); it_seq+=t+d; it_comb+=l
        t,d,l=csplit(fa[1],2,tcw+18); pwc_seq+=2*(t+d); pwc_comb+=2*l
        t,d,l=csplit(fa[2],1,tcw+9);  pwc_seq+=2*(t+d); pwc_comb+=2*l
    elif (not iot) and len(fa)>=2:
        t,d,l=csplit(fa[0],2,tcw+18); pwc_seq+=2*(t+d); pwc_comb+=2*l
        t,d,l=csplit(fa[1],1,tcw+9);  pwc_seq+=2*(t+d); pwc_comb+=2*l
    pf_a=mods.get("pf",0.0); mem_a=mods.get("mem",0.0)
    tot_seqA = seqA + it_seq + pwc_seq
    tot_combA= combA_ctrl + it_comb + pwc_comb + pf_a + mem_a
    sP,cP=PWR[disp]
    def p(seqa,comba): return sP*(seqa/tot_seqA if tot_seqA else 0)+cP*(comba/tot_combA if tot_combA else 0)
    rows["IOTLB"][disp]=p(it_seq,it_comb)
    rows["PWC(x4)"][disp]=p(pwc_seq,pwc_comb)
    rows["Control(wlk/buf/arb)"][disp]=p(seqA,combA_ctrl)
    rows["prefetch_ctrl"][disp]=p(0,pf_a)
    rows["mem_master"][disp]=p(0,mem_a)
for m,d in rows.items():
    print(f"{m:<16}"+"".join(f"{d.get(c,0):>9.2f}" for c,*_ in CFGS))
print(f"{'TOTAL':<16}"+"".join(f"{sum(PWR[c]):>9.2f}" for c,*_ in CFGS))
