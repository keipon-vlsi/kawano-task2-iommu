#!/usr/bin/env python3
"""One-slide overview of the iommu_sim structure (16:9, abstract).

Produces figures/sim_slide.png covering:
  - cycle-approximate event-driven flow
  - delay model (ns -> cycles)
  - scope (performance + relative PPA; NOT physical timing)
Run:  python generate_slide.py
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

BASE = os.path.dirname(os.path.abspath(__file__))
FIGS = os.path.join(BASE, "figures")
os.makedirs(FIGS, exist_ok=True)

BLUE = "#cfe2f3"; GREEN = "#d9ead3"; GRAY = "#f3f3f3"; YEL = "#fff2cc"


def box(ax, x, y, w, h, fc, lw=1.4):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                 boxstyle="round,pad=0.02,rounding_size=0.10",
                 fc=fc, ec="#333", lw=lw, zorder=2))


def arrow(ax, p1, p2):
    ax.annotate("", xy=p2, xytext=p1,
                arrowprops=dict(arrowstyle="-|>", lw=2.2, color="#333"), zorder=1)


def slide():
    fig, ax = plt.subplots(figsize=(13.33, 7.5))
    ax.set_xlim(0, 16); ax.set_ylim(0, 9); ax.axis("off")

    ax.text(8, 8.55, "Cycle-approximate IOMMU simulator — how it works",
            ha="center", fontsize=19, fontweight="bold")
    ax.text(8, 8.05, "event-driven, swappable-policy model: proves the architecture has the "
            "concurrency to sustain wire rate", ha="center", fontsize=11, color="#555",
            style="italic")

    # ---- (1) flow band ----
    ax.text(0.4, 7.35, "1  Cycle-approximate flow", fontsize=13, fontweight="bold",
            color="#1b4")
    box(ax, 2.2, 6.3, 3.0, 1.5, GREEN)
    ax.text(2.2, 6.55, "DMA requests", ha="center", fontsize=12, fontweight="bold")
    ax.text(2.2, 6.05, "wire-rate stream\n(1 page / 40 ns)", ha="center", fontsize=9)

    box(ax, 8.0, 6.3, 5.6, 1.7, BLUE, lw=2.0)
    ax.text(8.0, 6.95, "IOMMU engine  (event-driven, cycle-keyed queue)",
            ha="center", fontsize=11.5, fontweight="bold")
    ax.text(8.0, 6.35, "IOTLB  →  PWC / page-walk  →  fill caches", ha="center", fontsize=10.5)
    ax.text(8.0, 5.9, "+ coalescing · walkers · MSHR · buffers", ha="center", fontsize=9,
            color="#555")

    box(ax, 13.7, 6.3, 3.0, 1.5, GRAY)
    ax.text(13.7, 6.55, "translation done", ha="center", fontsize=12, fontweight="bold")
    ax.text(13.7, 6.05, "latency recorded\n(complete − arrival)", ha="center", fontsize=9)

    arrow(ax, (3.7, 6.3), (5.2, 6.3))
    arrow(ax, (10.8, 6.3), (12.2, 6.3))
    ax.text(8.0, 5.15, "each step schedules a future-cycle event — the engine never "
            "waits in real time; it advances virtual cycles", ha="center", fontsize=9.5,
            color="#444", style="italic")

    # ---- (2) delay model ----
    ax.text(0.4, 4.45, "2  Delay model  (ns → cycles)", fontsize=13, fontweight="bold",
            color="#a60")
    box(ax, 4.05, 2.1, 7.2, 4.0, YEL)
    L = [
        ("400 MHz   →   1 cycle = 2.5 ns", True),
        ("memory access   =   100 ns   =   40 cycles", True),
        ("", False),
        ("latency  =  lookup + arbitration + pipeline + n × mem", True),
        ("   • IOTLB / cache hit  →  a few cycles", False),
        ("   • page-walk  →  n = serial memory reads (0–15)", False),
        ("", False),
        ("delay is ACCUMULATED into the event time,", False),
        ("not spent — so 1000s of requests run instantly", False),
    ]
    y = 3.7
    for txt, big in L:
        if txt:
            ax.text(0.85, y, txt, fontsize=11 if big else 9.8,
                    fontweight="bold" if big else "normal",
                    color="#000" if big else "#444")
        y -= 0.42

    # ---- (3) scope ----
    ax.text(8.7, 4.45, "3  Scope", fontsize=13, fontweight="bold", color="#36a")
    box(ax, 12.0, 2.1, 7.0, 4.0, GRAY)
    ax.text(8.85, 3.75, "Models  (performance + relative PPA):", fontsize=10.5,
            fontweight="bold", color="#1a7")
    for i, t in enumerate(["concurrency (parallel walkers, N)",
                            "throughput vs wire rate",
                            "per-request latency & buffer sizing",
                            "relative area / power (GE, normalized)"]):
        ax.text(8.95, 3.35 - i * 0.40, "✓  " + t, fontsize=9.8, color="#0a6")
    ax.text(8.85, 1.55, "NOT modeled  (no physical timing):", fontsize=10.5,
            fontweight="bold", color="#c30")
    for i, t in enumerate(["Fmax / clock closure",
                           "place & route · wire delay · voltage"]):
        ax.text(8.95, 1.15 - i * 0.40, "✗  " + t, fontsize=9.8, color="#c30")
    ax.text(8.85, 0.30, "→ validates the design, not a 400 MHz sign-off",
            fontsize=9.2, style="italic", color="#555")

    fig.savefig(os.path.join(FIGS, "sim_slide.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("figures/sim_slide.png")


if __name__ == "__main__":
    slide()
