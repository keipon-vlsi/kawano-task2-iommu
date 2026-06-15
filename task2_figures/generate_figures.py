#!/usr/bin/env python3
"""Generate IOMMU microarchitecture validation figures (PNG) + source data (CSV).

Nested-IOMMU design-validation study (TASK2). Data is representative/mock unless a
formula is given, in which case it is computed (Fig 1, 3, 5, 6). Vectors given in the
spec (Fig 2, 4) are used as representative data. Run:  python generate_figures.py
(re)produces every data/*.csv and figures/*.png deterministically (fixed seed).

Deps: numpy, pandas, matplotlib only.
"""
from __future__ import annotations

import math
import os

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# --------------------------------------------------------------------------
# Global constants (swap these for real simulator output later)
# --------------------------------------------------------------------------
T_arr_ns  = 40                       # inter-arrival per 4KB request at wire rate
T_mem_ns  = 100                      # memory access latency
T_mem_req = T_mem_ns / T_arr_ns      # = 2.5 requests
mem_bw_8  = 3                        # memory accesses available per 8-request window
wire_rate = 1.0                      # normalized (100% = sustains wire rate)
PAGES = {"4KB": 1, "2MB": 512, "1GB": 262144}

SEED = 0
np.random.seed(SEED)

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
FIGS = os.path.join(BASE, "figures")
os.makedirs(DATA, exist_ok=True)
os.makedirs(FIGS, exist_ok=True)

DPI = 150
TAB = plt.cm.tab10.colors


def _grid(ax):
    ax.grid(True, ls=":", lw=0.6, alpha=0.5)


def _save(fig, name):
    path = os.path.join(FIGS, name)
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return path


def _csv(df, name):
    path = os.path.join(DATA, name)
    df.to_csv(path, index=False)
    return path


# ==========================================================================
# Fig 1 — Sizing (2 panels)
# ==========================================================================
def fig1():
    # (A) throughput vs walkers
    N_req = {"no-cache": 8, "+PWC": 4, "+coalescing": 2}
    walkers = np.arange(0, 17)
    dfA = pd.DataFrame({"walkers": walkers})
    for s, n in N_req.items():
        dfA[s] = np.minimum(100.0, 100.0 * walkers / n)
    a_csv = _csv(dfA, "fig1a_walkers_throughput.csv")

    # (B) throughput vs buffer depth
    B_req = {"+coalescing": 42, "+prefetch": 8}
    buffer = np.arange(0, 65)
    dfB = pd.DataFrame({"buffer": buffer})
    for s, b in B_req.items():
        dfB[s] = np.minimum(100.0, 100.0 * buffer / b)
    b_csv = _csv(dfB, "fig1b_buffer_throughput.csv")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    for i, (s, n) in enumerate(N_req.items()):
        ax1.plot(walkers, dfA[s], color=TAB[i], lw=2, label=s)
        ax1.plot(n, 100, "o", color=TAB[i], ms=9, zorder=5)          # knee
    ax1.axhline(100, ls="--", color="gray", lw=1)
    ax1.set_xlabel("# parallel walkers")
    ax1.set_ylabel("throughput (% of wire rate)")
    ax1.set_title("(A) Throughput vs walkers")
    ax1.set_ylim(0, 110); ax1.set_xlim(0, 16); ax1.legend(loc="lower right")
    ax1.text(8.3, 50, "knee = N_req", fontsize=8, color="gray")
    _grid(ax1)

    for i, (s, b) in enumerate(B_req.items()):
        ax2.plot(buffer, dfB[s], color=TAB[i], lw=2, label=s)
        ax2.plot(b, 100, "o", color=TAB[i], ms=9, zorder=5)
    ax2.axhline(100, ls="--", color="gray", lw=1)
    ax2.set_xlabel("buffer depth (entries)")
    ax2.set_ylabel("throughput (% of wire rate)")
    ax2.set_title("(B) Throughput vs buffer depth")
    ax2.set_ylim(0, 110); ax2.set_xlim(0, 64); ax2.legend(loc="lower right")
    _grid(ax2)

    fig.suptitle("Fig 1 — Sizing: coalescing cuts walkers ~8x but NOT buffer; "
                 "prefetch cuts buffer", fontsize=11)
    p = _save(fig, "fig1_sizing.png")
    print(f"[fig1] {a_csv}  {b_csv}  ->  {p}")


# ==========================================================================
# Fig 2 — Access / latency reduction (2 panels)
# ==========================================================================
def fig2():
    # (A) ablation
    cats = ["no-cache", "+PWC", "+coalescing", "+combined", "+superpage", "+prefetch"]
    acc  = [3.0, 1.2, 0.15, 0.13, 0.02, 0.02]
    dfA = pd.DataFrame({"config": cats, "mem_accesses_per_translation": acc})
    a_csv = _csv(dfA, "fig2a_ablation.csv")

    # (B) combine vs split, symmetric vs asymmetric Pv/Pg
    groups = ["symmetric (Pv=Pg)", "asymmetric (Pv!=Pg)"]
    bars   = ["combined", "split", "no-combine"]
    lat = {"symmetric (Pv=Pg)":  {"combined": 1.0, "split": 1.6, "no-combine": 2.2},
           "asymmetric (Pv!=Pg)": {"combined": 3.0, "split": 1.2, "no-combine": 2.0}}
    dfB = pd.DataFrame([{"group": g, **lat[g]} for g in groups])
    b_csv = _csv(dfB, "fig2b_combine_split.csv")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    ax1.bar(cats, acc, color=TAB[0])
    for x, v in enumerate(acc):
        ax1.text(x, v + 0.05, f"{v:g}", ha="center", va="bottom", fontsize=8)
    # biggest drop: +PWC (1.2) -> +coalescing (0.15) ~= 8x
    ax1.annotate("~8x drop\n(coalescing)", xy=(2, 0.15), xytext=(2.6, 1.8),
                 fontsize=9, ha="left",
                 arrowprops=dict(arrowstyle="->", color="crimson", lw=1.5),
                 color="crimson")
    ax1.set_ylabel("memory accesses / translation")
    ax1.set_title("(A) Access reduction (cache ablation)")
    ax1.set_ylim(0, 3.4)
    ax1.tick_params(axis="x", rotation=25)
    ax1.grid(True, axis="y", ls=":", lw=0.6, alpha=0.5)

    x = np.arange(len(groups)); w = 0.25
    for i, b in enumerate(bars):
        vals = [lat[g][b] for g in groups]
        ax2.bar(x + (i - 1) * w, vals, w, color=TAB[i], label=b)
    # highlight winner (min) per group
    for gi, g in enumerate(groups):
        win = min(bars, key=lambda b: lat[g][b])
        bi = bars.index(win)
        v = lat[g][win]
        ax2.bar(x[gi] + (bi - 1) * w, v, w, facecolor="none",
                edgecolor="black", lw=2.2, zorder=5)
        ax2.text(x[gi] + (bi - 1) * w, v + 0.06, "win", ha="center",
                 fontsize=8, fontweight="bold")
    ax2.set_xticks(x); ax2.set_xticklabels(groups)
    ax2.set_ylabel("latency / translation (relative)")
    ax2.set_title("(B) combine vs split (Pv/Pg symmetry)")
    ax2.set_ylim(0, 3.4); ax2.legend(title="page-table layout")
    ax2.grid(True, axis="y", ls=":", lw=0.6, alpha=0.5)

    fig.suptitle("Fig 2 — Access & latency reduction", fontsize=11)
    p = _save(fig, "fig2_reduction.png")
    print(f"[fig2] {a_csv}  {b_csv}  ->  {p}")


# ==========================================================================
# Fig 3 — Warmup transient
# ==========================================================================
def fig3():
    pages = np.arange(0, 121)
    cold = 100.0 * (1.0 - np.exp(-pages / 25.0))
    pref = 100.0 * (1.0 - np.exp(-pages / 2.0))
    df = pd.DataFrame({"pages": pages,
                       "prefetch_off_cold": cold,
                       "prefetch_on": pref})
    c_csv = _csv(df, "fig3_warmup.csv")

    # warmup time = page where throughput crosses 99%  (tau * ln(100))
    p99_cold = 25.0 * math.log(100.0)
    p99_pref = 2.0 * math.log(100.0)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(pages, cold, color=TAB[3], lw=2, label="prefetch off (cold)")
    ax.plot(pages, pref, color=TAB[2], lw=2, label="prefetch on")
    ax.axhline(100, ls="--", color="gray", lw=1)
    ax.axhline(99, ls=":", color="gray", lw=0.8)
    for px, col in [(p99_cold, TAB[3]), (p99_pref, TAB[2])]:
        ax.axvline(px, ls=":", color=col, lw=1)
    ax.annotate("", xy=(p99_cold, 50), xytext=(p99_pref, 50),
                arrowprops=dict(arrowstyle="<->", color="black", lw=1.3))
    ax.text((p99_cold + p99_pref) / 2, 53,
            f"warm-up gap\n~{p99_cold - p99_pref:.0f} pages", ha="center", fontsize=9)
    ax.text(p99_pref + 1, 20, f"on:\n{p99_pref:.0f}p", fontsize=8, color=TAB[2])
    ax.text(p99_cold - 1, 20, f"off:\n{p99_cold:.0f}p", fontsize=8,
            color=TAB[3], ha="right")
    ax.set_xlabel("pages since cold start")
    ax.set_ylabel("throughput (% of wire rate)")
    ax.set_title("Fig 3 — Warm-up transient (time to reach 99% wire rate)")
    ax.set_xlim(0, 120); ax.set_ylim(0, 108); ax.legend(loc="center right")
    _grid(ax)
    p = _save(fig, "fig3_warmup.png")
    print(f"[fig3] {c_csv}  ->  {p}")


# ==========================================================================
# Fig 4 — PPA (2 panels)
# ==========================================================================
def fig4():
    # (A) Pareto scatter
    pts = [("no-cache", 120, 3.00),
           ("+PWC", 180, 1.10),
           ("+coalescing", 220, 0.50),
           ("+combined+tags", 300, 0.40),
           ("chosen 1proc1dev", 270, 0.35)]
    dfA = pd.DataFrame(pts, columns=["arch", "area_GE", "energy_per_translation"])
    a_csv = _csv(dfA, "fig4a_pareto.csv")

    # lower-left (minimize both) Pareto frontier
    def dominated(p, others):
        return any(o[1] <= p[1] and o[2] <= p[2] and (o[1] < p[1] or o[2] < p[2])
                   for o in others)
    front = [p for p in pts if not dominated(p, [q for q in pts if q is not p])]
    front = sorted(front, key=lambda p: p[1])

    # (B) GE breakdown (representative; buffer dominates, tags->~0 for chosen)
    comps = ["buffer", "IOTLB", "PWC", "tags", "walker", "misc"]
    archs = ["no-cache", "combined+tags", "chosen(1proc1dev)"]
    ge = {"no-cache":          [80,   0,  0,  0, 30, 10],   # = 120
          "combined+tags":     [140, 30, 60, 40, 20, 10],   # = 300
          "chosen(1proc1dev)": [140, 30, 60,  2, 20, 18]}   # = 270
    dfB = pd.DataFrame(ge, index=comps).T.reset_index().rename(columns={"index": "arch"})
    b_csv = _csv(dfB, "fig4b_breakdown.csv")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))

    # frontier line
    fx = [p[1] for p in front]; fy = [p[2] for p in front]
    ax1.plot(fx, fy, ls="--", color="gray", lw=1.5, zorder=1, label="Pareto frontier")
    # per-point label offsets (dx_pts, dy_pts, ha) to avoid collisions
    off = {"no-cache": (8, 4, "left"), "+PWC": (8, 4, "left"),
           "+coalescing": (8, 4, "left"), "+combined+tags": (6, 12, "left"),
           "chosen 1proc1dev": (-10, -14, "right")}
    for label, ax_, ay in pts:
        chosen = label.startswith("chosen")
        ax1.scatter(ax_, ay, s=160 if chosen else 80,
                    color="crimson" if chosen else TAB[0],
                    edgecolor="black", zorder=3, marker="*" if chosen else "o")
        dx, dy, ha = off[label]
        ax1.annotate(label, (ax_, ay), textcoords="offset points",
                     xytext=(dx, dy), ha=ha, fontsize=8)
    # arrow combined+tags -> chosen (label placed above the arrow, no overlap)
    src = next(p for p in pts if p[0] == "+combined+tags")
    dst = next(p for p in pts if p[0].startswith("chosen"))
    ax1.annotate("", xy=(dst[1], dst[2]), xytext=(src[1], src[2]),
                 arrowprops=dict(arrowstyle="->", color="crimson", lw=1.6))
    ax1.text((src[1] + dst[1]) / 2, (src[2] + dst[2]) / 2 + 0.12, "tag reduction",
             ha="center", color="crimson", fontsize=9)
    ax1.set_xlabel("area (gate-equivalents, GE)")
    ax1.set_ylabel("energy / translation (norm)")
    ax1.set_title("(A) PPA Pareto (lower-left is better)")
    ax1.set_xlim(105, 335); ax1.set_ylim(0.1, 3.3)
    ax1.legend(loc="upper right"); _grid(ax1)

    bottom = np.zeros(len(archs))
    for i, comp in enumerate(comps):
        vals = np.array([ge[a][i] for a in archs])
        ax2.bar(archs, vals, bottom=bottom, color=TAB[i], label=comp)
        bottom += vals
    ax2.set_ylabel("area (GE)")
    ax2.set_title("(B) Area breakdown by component")
    ax2.legend(ncol=2, fontsize=8); ax2.tick_params(axis="x", rotation=10)
    ax2.grid(True, axis="y", ls=":", lw=0.6, alpha=0.5)

    fig.suptitle("Fig 4 — Power/Performance/Area", fontsize=11)
    p = _save(fig, "fig4_ppa.png")
    print(f"[fig4] {a_csv}  {b_csv}  ->  {p}")


# ==========================================================================
# Fig 5 — Sensitivity / robustness (2 panels)
# ==========================================================================
def fig5():
    x = np.linspace(0, 1, 101)
    contexts = 100.0 - 12.0 * x
    inval    = 100.0 - 45.0 * x
    iova     = 100.0 / (1.0 + np.exp(15.0 * (x - 0.6)))
    dfA = pd.DataFrame({"perturbation": x, "contexts": contexts,
                        "invalidation": inval, "IOVA_random": iova})
    a_csv = _csv(dfA, "fig5a_degradation.csv")

    stress = [("contexts -> tags/area", 0.80),
              ("invalidation -> refill/re-walk", 0.55),
              ("IOVA_random -> buffer/working-set", 1.00)]
    dfB = pd.DataFrame(stress, columns=["perturbation_to_resource", "relative_stress"])
    b_csv = _csv(dfB, "fig5b_resource_stress.csv")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
    ax1.plot(x, contexts, color=TAB[0], lw=2, label="contexts (device/process)")
    ax1.plot(x, inval, color=TAB[1], lw=2, label="invalidation")
    ax1.plot(x, iova, color=TAB[3], lw=2, label="IOVA random")
    ax1.annotate("cliff", xy=(0.6, 50), xytext=(0.66, 72), color=TAB[3], fontsize=10,
                 arrowprops=dict(arrowstyle="->", color=TAB[3], lw=1.4))
    ax1.annotate("graceful", xy=(0.85, contexts[85]), xytext=(0.5, 84),
                 color=TAB[0], fontsize=10,
                 arrowprops=dict(arrowstyle="->", color=TAB[0], lw=1.4))
    ax1.set_xlabel("perturbation (normalized 0..1)")
    ax1.set_ylabel("throughput (% of wire rate)")
    ax1.set_title("(A) Degradation under perturbation")
    ax1.set_xlim(0, 1); ax1.set_ylim(0, 105); ax1.legend(loc="lower left"); _grid(ax1)

    pert = ["contexts", "invalidation", "IOVA_random"]
    res_lbl = ["tags / area", "refill / re-walk", "buffer / working-set"]
    mags = [s[1] for s in stress]
    cols = [TAB[0], TAB[1], TAB[3]]
    ax2.barh(pert, mags, color=cols)
    for i, (m, rl) in enumerate(zip(mags, res_lbl)):
        ax2.text(0.02, i, f"-> {rl}", va="center", ha="left",
                 color="white", fontsize=9, fontweight="bold")
        ax2.text(m + 0.02, i, f"{m:.2f}", va="center", fontsize=9)
    ax2.set_xlabel("relative stress on the bottlenecked resource (0..1)")
    ax2.set_title("(B) Which resource each perturbation stresses")
    ax2.set_xlim(0, 1.2); ax2.invert_yaxis()
    ax2.grid(True, axis="x", ls=":", lw=0.6, alpha=0.5)

    fig.suptitle("Fig 5 — Sensitivity / robustness", fontsize=11)
    p = _save(fig, "fig5_sensitivity.png")
    print(f"[fig5] {a_csv}  {b_csv}  ->  {p}")


# ==========================================================================
# Fig 6 — Prefetch lead D (grounded model)
# ==========================================================================
# memory accesses generated AT each boundary (incl. leaf re-reads; table-G = 4KB,
# no table-G PWC). vm/g contributions per page size:
VM_2MB = {"4KB": 5, "2MB": 1, "1GB": 0}
G_2MB  = {"4KB": 2, "2MB": 1, "1GB": 0}
VM_1GB = {"4KB": 9, "2MB": 5, "1GB": 1}
G_1GB  = {"4KB": 3, "2MB": 2, "1GB": 1}


def _compute_lead(Pv, Pg):
    vm_leaf_span = 8 if Pv == "4KB" else PAGES[Pv]
    g_leaf_span  = 8 if Pg == "4KB" else PAGES[Pg]
    steady_per_page = 1.0 / vm_leaf_span + 1.0 / g_leaf_span
    spare_per_page  = mem_bw_8 / 8.0 - steady_per_page
    A_2MB = VM_2MB[Pv] + G_2MB[Pg]
    A_1GB = VM_1GB[Pv] + G_1GB[Pg]

    def lead(A):
        if A == 0:
            return None                               # boundary not present
        latency = math.ceil(A * T_mem_req)            # dependent serial chain
        budget = math.ceil(A / spare_per_page) if spare_per_page > 0 else math.inf
        return int(max(latency, budget))

    return dict(Pv=Pv, Pg=Pg,
                steady_per_8=steady_per_page * 8.0,
                spare_per_page=spare_per_page,
                A_2MB=A_2MB, A_1GB=A_1GB,
                D_2MB=lead(A_2MB), D_1GB=lead(A_1GB))


def fig6():
    order = ["4KB", "2MB", "1GB"]
    rows = [_compute_lead(Pv, Pg) for Pv in order for Pg in order]
    df = pd.DataFrame(rows, columns=["Pv", "Pg", "steady_per_8", "spare_per_page",
                                     "A_2MB", "A_1GB", "D_2MB", "D_1GB"])
    c_csv = _csv(df, "fig6_prefetch_lead.csv")

    # --- sanity asserts ---
    r44 = _compute_lead("4KB", "4KB")
    assert r44["A_2MB"] == 7 and r44["A_1GB"] == 12, r44
    assert r44["D_2MB"] == 56 and r44["D_1GB"] == 96, r44
    r11 = _compute_lead("1GB", "1GB")
    assert r11["steady_per_8"] < 1e-2, r11               # steady ~ 0
    assert r11["D_1GB"] is not None and r11["D_1GB"] <= 10, r11   # small
    print("[fig6] sanity asserts passed "
          f"(4KB/4KB: A_2MB={r44['A_2MB']}, A_1GB={r44['A_1GB']}, "
          f"D_2MB={r44['D_2MB']}, D_1GB={r44['D_1GB']}; 1GB/1GB D_1GB={r11['D_1GB']})")

    # heatmap of D_1GB over (Pv rows x Pg cols)
    M = np.full((3, 3), np.nan)
    txt = [["" for _ in order] for _ in order]
    for r in rows:
        i, j = order.index(r["Pv"]), order.index(r["Pg"])
        d = r["D_1GB"]
        M[i, j] = np.nan if d is None else d
        txt[i][j] = "—" if d is None else str(d)

    fig, ax = plt.subplots(figsize=(7, 6))
    im = ax.imshow(M, cmap="YlOrRd", aspect="equal")
    ax.set_xticks(range(3)); ax.set_xticklabels(order)
    ax.set_yticks(range(3)); ax.set_yticklabels(order)
    ax.set_xlabel("Pg  (data-GPA / G-stage page size)")
    ax.set_ylabel("Pv  (IOVA / VM-stage page size)")
    vmax = np.nanmax(M)
    for i in range(3):
        for j in range(3):
            val = M[i, j]
            color = "white" if (not np.isnan(val) and val > 0.6 * vmax) else "black"
            ax.text(j, i, txt[i][j], ha="center", va="center",
                    color=color, fontsize=13, fontweight="bold")
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("prefetch lead D at 1GB boundary (requests)")
    ax.set_title("Fig 6 — Prefetch lead D (1GB boundary)\n"
                 "lead = requests before boundary (budget-bound for 4KB/4KB)",
                 fontsize=10)
    p = _save(fig, "fig6_prefetch_lead.png")
    print(f"[fig6] {c_csv}  ->  {p}")


# ==========================================================================
def main():
    fig1(); fig2(); fig3(); fig4(); fig5(); fig6()
    print("All figures generated (seed=%d)." % SEED)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
