#!/usr/bin/env python3
"""Generate the three deliverable tables (PPA / area breakdown / power breakdown) as
markdown AND CSV. Sources: results/nested_ppa.json (area, Fmax, slack), the per-config
Yosys stat (fine area breakdown), and results/power_vcd.json (VCD-annotated power).

Run after syn/synth_nested.py and syn/power_vcd.py. Writes:
  results/ppa_compare.csv, results/area_breakdown.csv, results/power_breakdown.csv
"""
import csv, json, math, re
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
A_FF, DATA_W, CTXW = 25.02, 48, 44   # cache data = PTE[47:0]; CTX = device_id(24)+PASID(20)
def clog2(n): return 1 if n < 2 else math.ceil(math.log2(n))

# cfg, dir, NCTX, BUF, CO, has_iotlb, has_pf, tag_ctx
CFGS = [("cfg1","cfg1_nocache",37,37,1,0,0,1),("cfg2","cfg2_pwc",5,5,1,0,0,1),
        ("cfg3","cfg3_iotlb",1,5,8,1,0,1),("cfg4","cfg4_prefetch",1,1,8,1,1,1),
        ("cfg5","cfg5_notag",1,1,8,1,1,0)]
DIRS = {c[0]: c[1] for c in CFGS}
NAMES = [c[0] for c in CFGS]

ppa = {r["cfg"].split("_")[0]: r for r in json.loads((ROOT/"results/nested_ppa.json").read_text())}
pv = {}
pvf = ROOT/"results/power_vcd.json"
if pvf.exists():
    pv = {r["cfg"].split("_")[0]: r for r in json.loads(pvf.read_text())}

def fa_and_dff(d):
    txt=(ROOT/d/"results/synth_area.txt").read_text(); fa=[]; mods={}
    for n,a in re.findall(r"Chip area for module '([^']+)': *([\d.]+)", txt):
        a=float(a)
        if "fa_cache" in n: fa.append(a)
        elif "iommu_top" in n: mods["ctrl"]=a
        elif "mem_master" in n: mods["mem"]=a
        elif "prefetch_ctrl" in n: mods["pf"]=a
    on=[]; f=False
    for ln in txt.splitlines():
        if "iommu_top ===" in ln: f=True
        if f and "Chip area for module" in ln and "iommu_top" in ln: break
        if f: on.append(ln)
    nd=sum(int(l.split()[0]) for l in on if re.search(r"__df(rtp|stp)",l))
    return sorted(fa,reverse=True), mods, nd

def csplit(meas,entries,tagw):
    tag=entries*tagw*A_FF; dv=(entries*(DATA_W+1)+clog2(entries))*A_FF
    return tag,dv,max(0.0,meas-tag-dv)

# ---------------- area breakdown ----------------
AREA_ROWS=["Walker RF","Transaction buffer","Misc ctrl FF","Arbiter+adders+MSHR (comb)",
           "PWC tag","PWC data+valid","PWC lookup logic","IOTLB tag","IOTLB data+valid",
           "IOTLB lookup logic","prefetch_ctrl","mem_master"]
area={r:{} for r in AREA_ROWS}; area_total={}
for cn,d,nctx,buf,co,iot,pf,tc in CFGS:
    fa,mods,nd=fa_and_dff(d); tcw=CTXW if tc else 0
    seq=nd*A_FF; comb=max(0.0,mods["ctrl"]-seq); vpnline=27 if co==1 else 24
    wl=(2+4+27+CTXW+28+27+27+vpnline+4)*nctx; bf=(2+27+CTXW+40)*buf
    ms=(28+18+1 if pf else 0)+56+64+clog2(nctx)+clog2(buf); tot=wl+bf+ms
    area["Walker RF"][cn]=seq*wl/tot; area["Transaction buffer"][cn]=seq*bf/tot
    area["Misc ctrl FF"][cn]=seq*ms/tot; area["Arbiter+adders+MSHR (comb)"][cn]=comb
    pt=pd=pl=it=idv=il=0.0
    if iot and len(fa)>=3:
        it,idv,il=csplit(fa[0],2*co,tcw+27)
        a,b,c=csplit(fa[1],2,tcw+18); pt+=2*a;pd+=2*b;pl+=2*c
        a,b,c=csplit(fa[2],1,tcw+9);  pt+=2*a;pd+=2*b;pl+=2*c
    elif (not iot) and len(fa)>=2:
        a,b,c=csplit(fa[0],2,tcw+18); pt+=2*a;pd+=2*b;pl+=2*c
        a,b,c=csplit(fa[1],1,tcw+9);  pt+=2*a;pd+=2*b;pl+=2*c
    area["PWC tag"][cn]=pt; area["PWC data+valid"][cn]=pd; area["PWC lookup logic"][cn]=pl
    area["IOTLB tag"][cn]=it; area["IOTLB data+valid"][cn]=idv; area["IOTLB lookup logic"][cn]=il
    area["prefetch_ctrl"][cn]=mods.get("pf",0.0); area["mem_master"][cn]=mods.get("mem",0.0)
    area_total[cn]=ppa[cn]["total_area_um2"]

# ---------------- tables + CSV ----------------
def md_table(title, header, rows):
    out=[f"\n## {title}\n", "| "+" | ".join(header)+" |", "|"+"---|"*len(header)]
    for r in rows: out.append("| "+" | ".join(r)+" |")
    return "\n".join(out)

def write_csv(path, header, rows):
    with open(ROOT/"results"/path,"w",newline="") as f:
        w=csv.writer(f); w.writerow(header); w.writerows(rows)

# post-opt (constraints+sizing P&R) results, keyed by short cfg name
po = {}
pof = ROOT/"results/fmax_opt/postopt.json"
if pof.exists():
    po = {r["cfg"].split("_")[0]: r for r in json.loads(pof.read_text())}
def poget(cn,k):
    v = po.get(cn,{}).get(k); return v

# 1) PPA compare
ppa_hdr=["metric"]+NAMES
ppa_rows=[]
def pget(cn,k,d=0.0): return ppa.get(cn,{}).get(k,d)
def pvget(cn,k,d=None):
    r=pv.get(cn,{}); return r.get(k,d)
def fmt(v,f="{:.1f}"): return f.format(v) if v is not None else "n/a"
ppa_rows.append(["area_synth_um2"]+[f"{area_total[c]:,.0f}" for c in NAMES])
ppa_rows.append(["area_postopt_um2"]+[fmt(poget(c,'area_um2'),"{:,.0f}") for c in NAMES])
ppa_rows.append(["Fmax_synth_MHz"]+[f"{pget(c,'fmax_mhz'):.1f}" for c in NAMES])
ppa_rows.append(["Fmax_postopt_MHz"]+[fmt(poget(c,'fmax_mhz')) for c in NAMES])
ppa_rows.append(["power_synth_mW@400"]+[f"{pget(c,'power_mw'):.1f}" for c in NAMES])
ppa_rows.append(["power_postopt_mW@400"]+[fmt(poget(c,'power_mW')) for c in NAMES])
print(md_table("PPA 比較 (sky130_fd_sc_hd tt 1v80, 2.5ns target; synth=ideal-wireload "
               "estimate, postopt=P&R+resize 制約サイジング後)", ppa_hdr, ppa_rows))
write_csv("ppa_compare.csv", ppa_hdr, ppa_rows)

# 2) area breakdown
area_hdr=["component_um2"]+NAMES
area_rows=[[r]+[f"{area[r][c]:,.0f}" if area[r].get(c,0)>0.5 else "0" for c in NAMES] for r in AREA_ROWS]
area_rows.append(["TOTAL"]+[f"{area_total[c]:,.0f}" for c in NAMES])
print(md_table("詳細 面積内訳 (µm²; cache=tag/data/lookup)", area_hdr, area_rows))
write_csv("area_breakdown.csv", area_hdr, area_rows)

# 3) power breakdown -- from the VCD-CALIBRATED flat STA (group table) + per-module
#    apportionment by FF(sequential)/comb area. No per-run VCD needed.
def read_grouppwr(d):
    t=(ROOT/d/"results/sta.txt").read_text(); n=r"([0-9][0-9.eE+-]*)"
    out={}
    for grp,key in [("Sequential","seq"),("Combinational","comb")]:
        m=re.search(rf"^{grp}\s+{n}\s+{n}\s+{n}\s+{n}",t,re.M)
        if m: out[key]=float(m.group(4))*1000
    mt=re.search(rf"^Total\s+{n}\s+{n}\s+{n}\s+{n}",t,re.M)
    if mt:
        out["dyn"]=(float(mt.group(1))+float(mt.group(2)))*1000
        out["leak_uW"]=float(mt.group(3))*1e6; out["total"]=float(mt.group(4))*1000
    return out
# per-module seq/comb area groups (reuse the area breakdown)
SEQ={"IOTLB":["IOTLB tag","IOTLB data+valid"],"PWC":["PWC tag","PWC data+valid"],
     "Control":["Walker RF","Transaction buffer","Misc ctrl FF"]}
COMB={"IOTLB":["IOTLB lookup logic"],"PWC":["PWC lookup logic"],
      "Control":["Arbiter+adders+MSHR (comb)"],"prefetch_ctrl":["prefetch_ctrl"],
      "mem_master":["mem_master"]}
gp={c:read_grouppwr(DIRS[c]) for c in NAMES}
pmod={c:{} for c in NAMES}
for c in NAMES:
    sa=sum(area[r][c] for g in SEQ.values() for r in g)
    ca=sum(area[r][c] for g in COMB.values() for r in g)
    sp=gp[c].get("seq",0); cp=gp[c].get("comb",0)
    for mod in ["IOTLB","PWC","Control","prefetch_ctrl","mem_master"]:
        s=sum(area[r][c] for r in SEQ.get(mod,[])); k=sum(area[r][c] for r in COMB.get(mod,[]))
        pmod[c][mod]=(sp*s/sa if sa else 0)+(cp*k/ca if ca else 0)
pw_hdr=["power_mW@400(calib)"]+NAMES
pw_rows=[]
for key,lab in [("dyn","dynamic"),("leak_uW","leakage_uW"),("seq","sequential"),
                ("comb","combinational"),("total","TOTAL")]:
    pw_rows.append([lab]+[f"{gp[c].get(key,0):.3f}" for c in NAMES])
for mod in ["IOTLB","PWC","Control","prefetch_ctrl","mem_master"]:
    pw_rows.append(["  "+mod]+[f"{pmod[c][mod]:.2f}" for c in NAMES])
print(md_table("詳細 電力内訳 (VCD較正済み flat 推定 + モジュール按分)", pw_hdr, pw_rows))
write_csv("power_breakdown.csv", pw_hdr, pw_rows)
print("\nNote: per-config switching activity was calibrated once against the VCD-annotated"
      "\n      gate-level power (28b design); going forward the flat STA estimate is used"
      "\n      (no per-run VCD). The 48b/24b design is larger, so absolute power > the 28b VCD.")
print("CSV: results/ppa_compare.csv, area_breakdown.csv, power_breakdown.csv")
