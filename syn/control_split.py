#!/usr/bin/env python3
"""Split the 'Control' (iommu_top local) area into Walker vs Buffer vs the rest.

iommu_top is a single module, so yosys gives only its total local area. We split it:
  - sequential area = (#dfrtp flip-flops in iommu_top) x A_FF   (dfrtp_1 = 25.02 um2)
  - that sequential area is attributed to register groups by their nominal bit width
    (walker context RF, transaction buffer, coalesce/IOTLB-fill line reg, misc)
  - the remainder of Control is combinational: the memory-issue arbiter, the
    pte_addr() adders and the MSHR/broadcast compare.
Flip-flop counts are read from each config's Yosys stat; bit widths from the RTL.
"""
import math, re
from pathlib import Path
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
A_FF = 25.02   # sky130_fd_sc_hd__dfrtp_1 cell area (um2)

# (name, NCTX, BUFFER, COALESCE, has_iotlb, has_pf)
CFGS = [("cfg1 nocache","cfg1_nocache",37,37,1,0,0),
        ("cfg2 pwc",    "cfg2_pwc",     5, 5,1,0,0),
        ("cfg3 iotlb",  "cfg3_iotlb",   1, 5,8,1,0),
        ("cfg4 prefetch","cfg4_prefetch",1,1,8,1,1),
        ("cfg5 notag",  "cfg5_notag",   1, 1,8,1,1)]

def clog2(n): return 1 if n < 2 else math.ceil(math.log2(n))

def ctrl_area(cfg):
    txt = (ROOT/cfg/"results"/"synth_area.txt").read_text()
    m = re.search(r"iommu_top': *([\d.]+)", txt)
    return float(m.group(1))

def dff_count(cfg):
    sec, on = [], False
    for ln in (ROOT/cfg/"results"/"synth_area.txt").read_text().splitlines():
        if "iommu_top ===" in ln: on = True
        if on and "Chip area for module" in ln and "iommu_top" in ln: break
        if on: sec.append(ln)
    return sum(int(l.split()[0]) for l in sec if re.search(r"__df(rtp|stp)", l))

def nominal_bits(nctx, buf, co, iotlb, pf):
    vpnline = 27 if co == 1 else 24
    walker = (2+4+27+44+28+27+27+vpnline+4) * nctx         # +wbeat; IOTLB filled per-beat
    buffer = (2+27+44+40) * buf                            # bs,bvpn,bctx,bspa
    misc   = (28+18+1 if pf else 0) + 56 + 64 + clog2(nctx) + clog2(buf)
    return {"Walker RF": walker, "Buffer": buffer, "Misc seq": misc}

rows, fig = [], None
labels = ["Walker RF", "Buffer", "Misc seq", "Arbiter+adders+MSHR (comb)"]
colors = ["#4C78A8", "#E45756", "#9D755D", "#BAB0AC"]
data = []
for disp, cfg, nctx, buf, co, iotlb, pf in CFGS:
    total = ctrl_area(cfg)
    ndff = dff_count(cfg)
    seq = ndff * A_FF
    comb = max(0.0, total - seq)
    nb = nominal_bits(nctx, buf, co, iotlb, pf)
    tot_bits = sum(nb.values())
    parts = {k: seq * v / tot_bits for k, v in nb.items()}   # attribute seq by bit width
    parts["Arbiter+adders+MSHR (comb)"] = comb
    rows.append((disp, total, parts))
    data.append([parts[l] for l in labels])

# table
print(f"\nControl breakdown (µm²) — sequential split by register-group bit width, "
      f"combinational = remainder\n")
print("| Control part | " + " | ".join(d for d,_,_ in rows) + " |")
print("|" + "---|"*(len(rows)+1))
for i,l in enumerate(labels):
    print(f"| {l} | " + " | ".join(f"{data[j][i]:,.0f}" for j in range(len(rows))) + " |")
print("| **Control total** | " + " | ".join(f"**{t:,.0f}**" for _,t,_ in rows) + " |")

# stacked bar chart
fig, ax = plt.subplots(figsize=(11,6))
import numpy as np
x = np.arange(len(rows)); bottom = np.zeros(len(rows))
for i,l in enumerate(labels):
    vals = np.array([data[j][i]/1000 for j in range(len(rows))])
    ax.bar(x, vals, bottom=bottom, label=l, color=colors[i])
    bottom += vals
ax.set_xticks(x); ax.set_xticklabels([d for d,_,_ in rows])
ax.set_ylabel("Control-block area (k µm²)")
ax.set_title("iommu_top 'Control' area split: Walker vs Buffer vs arbiter/fill\n"
             "(sequential by FF register-group width; combinational = remainder)")
ax.legend(loc="upper right", fontsize=9)
for j,(d,t,_) in enumerate(rows):
    ax.text(j, bottom[j]+3, f"{t/1000:.0f}k", ha="center", fontsize=9)
fig.tight_layout()
out = ROOT/"results"/"control_split.png"
fig.savefig(out, dpi=130); print("\nwrote", out)
