#!/usr/bin/env python3
# IOTLB variants -- area vs Fmax / area vs critical-path depth scatter plots.
# Self-contained (data inlined) for Colab: just `pip install matplotlib` and run.
# Measured on sky130_fd_sc_hd, post-place+resize (ideal clock). Edit DATA freely.
import matplotlib.pyplot as plt

# name, label, family, area[um2], Fmax[MHz], logic_depth[levels]
# family: "line" = line-tag + offset index | "cam" = flat 16-way CAM | "base" = base+offset(adder)
DATA = [
    ("T0",   "T0 line 2x8 (current)", "line", 34556, 343.7,  7),
    ("T0x3", "T0x3 line 3x8 (24)",    "line", 51227, 334.7,  8),
    ("T1",   "T1 aligned window",     "line", 32572, 464.2,  5),
    ("T2",   "T2 seq-pointer",        "line", 37868, 293.8,  7),
    ("T3",   "T3 speculative (=T1)",  "line", 32572, 464.2,  5),
    ("T4",   "T4 base+offset",        "base", 12090, 127.0, 28),
    ("T5",   "T5 CAM+priority",       "cam",  53466, 245.6, 10),
    ("T6",   "T6 CAM one-hot",        "cam",  52874, 367.8,  7),
    ("T7",   "T7 line-predictor",     "line", 35515, 335.4,  7),
    ("T8",   "T8 CAM mux-cascade",    "cam",  53787, 284.3, 11),
]

# family -> (color, marker, legend label)
FAM = {
    "line": ("#2a7fb8", "o", "line-tag + offset index"),
    "cam":  ("#c0392b", "s", "flat 16-way CAM"),
    "base": ("#27ae60", "^", "base + offset (adder)"),
}

# per-plot label nudges (points; (dx, dy)) to avoid overlap. key = short name.
OFFSETS = {
    "fmax": {"T1": (6, 6), "T3": (6, -13), "T0": (-6, -15), "T7": (8, 5),
             "T2": (8, -3), "T6": (6, -13), "T8": (8, 3), "T5": (8, -3),
             "T0x3": (-8, 9), "T4": (8, 2)},
    "delay": {"T1": (6, -13), "T3": (6, 6), "T0": (-6, 9), "T7": (8, -10),
              "T2": (8, 4), "T6": (8, 5), "T8": (8, -3), "T5": (8, 3),
              "T0x3": (-10, 10), "T6": (8, -13), "T4": (8, 2)},
    "depth": {"T1": (8, 5), "T3": (6, -13), "T0": (-6, -15), "T7": (8, 6),
              "T2": (8, -10), "T6": (8, -12), "T8": (8, 4), "T5": (8, -2),
              "T0x3": (-8, 9), "T4": (8, 2)},
}


def scatter(metric_idx, key, ylabel, title, fname, save=True, show=False):
    fig, ax = plt.subplots(figsize=(10, 7))
    seen = set()
    for name, label, fam, area, fmax, depth in DATA:
        # 0=fmax [MHz], 1=depth [levels], 2=critical-path delay [ns] = 1000/Fmax (physical)
        y = (fmax, depth, 1000.0 / fmax)[metric_idx]
        c, m, leg = FAM[fam]
        ax.scatter(area / 1000.0, y, c=c, marker=m, s=130, edgecolor="black",
                   linewidth=0.8, zorder=3, label=(leg if fam not in seen else None))
        seen.add(fam)
        dx, dy = OFFSETS[key].get(name, (6, 4))
        ax.annotate(label, (area / 1000.0, y), textcoords="offset points",
                    xytext=(dx, dy), fontsize=8.5, zorder=4)
    ax.set_xlabel("area (post-opt) [x1000 um^2]", fontsize=11)
    ax.set_ylabel(ylabel, fontsize=11)
    ax.set_title(title, fontsize=12.5, fontweight="bold")
    ax.grid(True, ls="--", alpha=0.4, zorder=0)
    ax.legend(title="family", fontsize=9.5, loc="best")
    fig.tight_layout()
    if save:
        fig.savefig(fname, dpi=140, bbox_inches="tight")
        print("wrote", fname)
    if show:
        plt.show()
    plt.close(fig)


# (1) PRIMARY: area vs critical-path DELAY [ns] -- the physically accurate metric (real
#     cell-delay sum, not just levels). Saying "ns" (not "MHz") also avoids the wire-rate
#     confusion. NOTE: ideal-clock / no-CTS-route post-place -> absolute ns is optimistic;
#     use for RELATIVE comparison.
scatter(2, "delay", "critical-path delay (post-opt, sky130 hd) [ns]  (lower = faster)",
        "IOTLB variants: area vs critical-path delay  (lower-left = smaller & faster)",
        "iotlb_area_delay.png", show=True)
# (2) area vs Fmax (= 1000/delay; same info, frequency view)
scatter(0, "fmax", "Fmax (post-opt, sky130 hd) [MHz]",
        "IOTLB variants: area vs Fmax  (lower-left->upper-left = smaller & faster)",
        "iotlb_area_fmax.png", show=True)
# (3) area vs critical-path logic depth -- STRUCTURE proxy (P&R-noise-robust). Beware:
#     same depth != same delay; e.g. T8 depth 11 is FASTER than T5 depth 10 (fanout).
scatter(1, "depth", "critical-path logic depth [levels]  (structure proxy, not delay)",
        "IOTLB variants: area vs logic depth  (depth misranks delay when fanout differs)",
        "iotlb_area_depth.png", show=True)
