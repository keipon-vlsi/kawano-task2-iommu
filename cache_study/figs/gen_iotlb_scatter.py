#!/usr/bin/env python3
"""Scatter plots of the IOTLB variants (data from cache_study/results/iotlb_*.json):
  figures/iotlb_area_fmax.png   -- area vs Fmax
  figures/iotlb_area_depth.png  -- area vs critical-path logic depth (avoids the
                                   wire-rate confusion that Fmax invites)
Color-coded by family. Run: .venv/bin/python3 cache_study/figs/gen_iotlb_scatter.py
"""
import json, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(BASE, "..", "results")
FIGS = os.path.join(BASE, "figures"); os.makedirs(FIGS, exist_ok=True)

# variant -> (label, family). families: line / cam / base+offset
VARS = {
    "iotlb_t0":   ("T0 line 2×8 (current)", "line"),
    "iotlb_t0x3": ("T0×3 line 3×8 (24)",    "line"),
    "iotlb_t1":   ("T1 aligned window",      "line"),
    "iotlb_t2":   ("T2 seq-pointer",         "line"),
    "iotlb_t3":   ("T3 speculative (=T1)",   "line"),
    "iotlb_t4":   ("T4 base+offset",         "base"),
    "iotlb_t5":   ("T5 CAM+priority",        "cam"),
    "iotlb_t6":   ("T6 CAM one-hot",         "cam"),
    "iotlb_t7":   ("T7 line-predictor",      "line"),
    "iotlb_t8":   ("T8 CAM mux-cascade",     "cam"),
}
FAM = {"line": ("#2a7fb8", "o", "line-tag + offset index"),
       "cam":  ("#c0392b", "s", "flat 16-way CAM"),
       "base": ("#27ae60", "^", "base + offset (adder)")}

rows = []
for v, (lab, fam) in VARS.items():
    d = json.load(open(os.path.join(RES, f"{v}.json")))
    rows.append(dict(lab=lab, fam=fam, area=d["area_um2"], fmax=d["fmax_mhz"],
                     depth=d["logic_depth"], delay=1000.0 / d["fmax_mhz"]))  # delay[ns]=1000/Fmax


def label_offsets(rows, key):
    # small manual nudges to avoid overlap; default up-right
    off = {}
    for r in rows:
        off[r["lab"]] = (6, 4)
    # tweaks for crowded points
    if key == "fmax":
        tweak = {
            "T3 speculative (=T1)": (6, -13), "T1 aligned window": (6, 6),
            "T0 line 2×8 (current)": (-6, -15), "T7 line-predictor": (8, 5),
            "T2 seq-pointer": (8, -3),
            "T6 CAM one-hot": (6, -13), "T8 CAM mux-cascade": (8, 3),
            "T5 CAM+priority": (8, -3), "T0×3 line 3×8 (24)": (-8, 9),
            "T4 base+offset": (8, 2),
        }
    elif key == "delay":
        tweak = {
            "T3 speculative (=T1)": (6, 6), "T1 aligned window": (6, -13),
            "T0 line 2×8 (current)": (-6, 9), "T7 line-predictor": (8, -10),
            "T2 seq-pointer": (8, 4), "T6 CAM one-hot": (8, 5),
            "T8 CAM mux-cascade": (8, -3), "T5 CAM+priority": (8, 3),
            "T6 CAM one-hot": (8, -13), "T0×3 line 3×8 (24)": (-10, 10), "T4 base+offset": (8, 2),
        }
    else:  # depth
        tweak = {
            "T3 speculative (=T1)": (6, -13), "T1 aligned window": (8, 5),
            "T0 line 2×8 (current)": (-6, -15), "T7 line-predictor": (8, 6),
            "T2 seq-pointer": (8, -10),
            "T6 CAM one-hot": (8, -12), "T8 CAM mux-cascade": (8, 4),
            "T5 CAM+priority": (8, -2), "T0×3 line 3×8 (24)": (-8, 9),
            "T4 base+offset": (8, 2),
        }
    for k, val in tweak.items():
        off[k] = val
    return off


def scatter(key, ylabel, title, fname, ylim=None):
    fig, ax = plt.subplots(figsize=(10, 7))
    seen = set()
    off = label_offsets(rows, key)
    for r in rows:
        c, m, leg = FAM[r["fam"]]
        ax.scatter(r["area"] / 1000.0, r[key], c=c, marker=m, s=130, edgecolor="black",
                   linewidth=0.8, zorder=3, label=(leg if r["fam"] not in seen else None))
        seen.add(r["fam"])
        dx, dy = off[r["lab"]]
        ax.annotate(r["lab"], (r["area"] / 1000.0, r[key]), textcoords="offset points",
                    xytext=(dx, dy), fontsize=8.5, zorder=4)
    ax.set_xlabel("area (post-opt) [×1000 µm²]", fontsize=11)
    ax.set_ylabel(ylabel, fontsize=11)
    ax.set_title(title, fontsize=12.5, fontweight="bold")
    ax.grid(True, ls="--", alpha=0.4, zorder=0)
    ax.legend(title="family", fontsize=9.5, loc="best")
    if ylim: ax.set_ylim(*ylim)
    fig.tight_layout()
    p = os.path.join(FIGS, fname)
    fig.savefig(p, dpi=140, bbox_inches="tight"); plt.close(fig)
    print("wrote", p)


# (1) PRIMARY: area vs critical-path DELAY [ns] = 1000/Fmax -- the physically accurate
#     metric (real cell-delay sum, not just levels). "ns" (not "MHz") also avoids the
#     wire-rate confusion. NOTE: ideal-clock/no-CTS-route -> absolute ns optimistic, use
#     for RELATIVE comparison.
scatter("delay", "critical-path delay (post-opt, sky130 hd) [ns]  (lower = faster)",
        "IOTLB variants: area vs critical-path delay  (lower-left = smaller & faster)",
        "iotlb_area_delay.png")
# (2) area vs Fmax (= 1000/delay; same info, frequency view)
scatter("fmax", "Fmax (post-opt, sky130 hd) [MHz]",
        "IOTLB variants: area vs Fmax  (lower-left→upper-left = smaller & faster)",
        "iotlb_area_fmax.png")
# (3) area vs logic depth -- STRUCTURE proxy (P&R-noise-robust). Beware: same depth !=
#     same delay (T8 depth 11 is FASTER than T5 depth 10 due to fanout).
scatter("depth", "critical-path logic depth [levels]  (structure proxy, not delay)",
        "IOTLB variants: area vs logic depth  (depth misranks delay when fanout differs)",
        "iotlb_area_depth.png")
