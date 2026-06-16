#!/usr/bin/env python3
"""Per-config area breakdown (by function) + pie charts, from the Yosys stat reports.

Yosys reports area per *module definition* (one instance). Instance counts come from
the RTL: IOTLB x1; the two ENTRIES=2 PWCs (VM-L1, G-L1) share a paramod -> x2; the two
ENTRIES=1 PWCs (VM-L2, G-L2) share a paramod -> x2; mem_master x1; prefetch_ctrl x1;
iommu_top local cells x1 (= walker RF + arbiter + MSHR + address adders = "control").
"""
import re
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
CFGS = [("cfg1_nocache", "cfg1 nocache", False, False, False),
        ("cfg2_pwc",      "cfg2 pwc",     True,  False, False),
        ("cfg3_iotlb",    "cfg3 iotlb",   True,  True,  False),
        ("cfg4_prefetch", "cfg4 prefetch",True,  True,  True),
        ("cfg5_notag",    "cfg5 notag",   True,  True,  True)]
CATS = ["Control (walker RF/arbiter/MSHR/adders)", "IOTLB CAM", "PWC CAMs (4)",
        "prefetch_ctrl", "mem_master"]
COLORS = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#B279A2"]


def module_areas(cfg):
    txt = (ROOT / cfg / "results" / "synth_area.txt").read_text()
    fa, ctrl, mem, pf = [], 0.0, 0.0, 0.0
    for name, area in re.findall(r"Chip area for module '([^']+)': *([\d.]+)", txt):
        a = float(area)
        if "fa_cache" in name:   fa.append(a)
        elif "iommu_top" in name: ctrl = a
        elif "mem_master" in name: mem = a
        elif "prefetch_ctrl" in name: pf = a
    return sorted(fa, reverse=True), ctrl, mem, pf


def breakdown(cfg, has_pwc, has_iotlb, has_pf):
    fa, ctrl, mem, pf = module_areas(cfg)
    iotlb = pwc = 0.0
    if has_iotlb:
        iotlb = fa[0]
        if has_pwc and len(fa) >= 3:   # fa[1]=L1 (x2), fa[2]=L2 (x2)
            pwc = 2*fa[1] + 2*fa[2]
    elif has_pwc:                      # cfg2: no IOTLB, fa[0]=L1 (x2), fa[1]=L2 (x2)
        pwc = 2*fa[0] + 2*fa[1]
    return [ctrl, iotlb, pwc, (pf if has_pf else 0.0), mem]


rows = []
fig, axes = plt.subplots(1, 5, figsize=(22, 5.2))
for ax, (cfg, label, hp, hi, hf) in zip(axes, CFGS):
    vals = breakdown(cfg, hp, hi, hf)
    total = sum(vals)
    rows.append((label, vals, total))
    nz = [(c, v, col) for c, v, col in zip(CATS, vals, COLORS) if v > 0]
    ax.pie([v for _, v, _ in nz], colors=[c for *_, c in nz],
           autopct=lambda p: f"{p:.0f}%" if p >= 4 else "",
           startangle=90, counterclock=False, pctdistance=0.75,
           wedgeprops=dict(width=0.42, edgecolor="white"))
    ax.set_title(f"{label}\n{total/1000:.0f}k µm²", fontsize=12)
fig.legend(CATS, loc="lower center", ncol=5, fontsize=10, frameon=False)
fig.suptitle("IOMMU per-config standard-cell area breakdown (sky130_fd_sc_hd)", fontsize=14)
fig.tight_layout(rect=[0, 0.06, 1, 0.95])
out = ROOT / "results" / "area_breakdown.png"
fig.savefig(out, dpi=130)
print("wrote", out)

# markdown table (µm² and %)
print("\n| component | " + " | ".join(l for l, *_ in rows) + " |")
print("|" + "---|" * (len(rows)+1))
for i, cat in enumerate(CATS):
    cells = []
    for _, vals, total in rows:
        v = vals[i]
        cells.append(f"{v:,.0f} ({100*v/total:.0f}%)" if v > 0 else "—")
    print(f"| {cat} | " + " | ".join(cells) + " |")
print("| **total** | " + " | ".join(f"**{t:,.0f}**" for *_, t in rows) + " |")
