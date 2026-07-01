#!/usr/bin/env python3
"""One-figure overview: simulator flow + how each metric is measured.

figures/sim_overview.png  — request flow (top) with a measurement 'tap' under each
stage showing how throughput/latency, walker count (3c), buffer count (3d), and
cache hit/miss are collected.
Run:  python generate_overview.py
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

BASE = os.path.dirname(os.path.abspath(__file__))
FIGS = os.path.join(BASE, "figures")
os.makedirs(FIGS, exist_ok=True)

FLOW = "#cfe2f3"      # flow stages
M_CACHE = "#d9ead3"   # cache hit/miss
M_BUF = "#fce5cd"     # buffer
M_WALK = "#fff2cc"    # walkers
M_LAT = "#ead1dc"     # latency / throughput


def box(ax, x, y, w, h, fc, lw=1.4):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                 boxstyle="round,pad=0.02,rounding_size=0.08",
                 fc=fc, ec="#333", lw=lw, zorder=2))


def flow_arrow(ax, x1, x2, y):
    ax.annotate("", xy=(x2, y), xytext=(x1, y),
                arrowprops=dict(arrowstyle="-|>", lw=2.2, color="#333"), zorder=1)


def tap(ax, x, y1, y2):
    ax.annotate("", xy=(x, y2), xytext=(x, y1),
                arrowprops=dict(arrowstyle="-|>", lw=1.3, color="#888",
                                ls=(0, (4, 3))), zorder=1)


def overview():
    fig, ax = plt.subplots(figsize=(14.2, 8.0))
    ax.set_xlim(0, 14.4); ax.set_ylim(0, 9); ax.axis("off")

    ax.text(7.2, 8.55, "IOMMU simulator at a glance — flow & how each metric is measured",
            ha="center", fontsize=17, fontweight="bold")
    ax.text(7.2, 8.10, "event-driven, cycle-keyed: each step schedules a future-cycle "
            "event (delay is added to the event time, not waited)",
            ha="center", fontsize=10, color="#555", style="italic")

    # ---------- flow row ----------
    XS = [1.6, 4.4, 7.2, 10.0, 12.8]
    FY, FW, FH = 6.6, 2.5, 1.45
    flow = [
        ("Workload", ["arrival events", "spaced at wire rate", "(1 page / 40 ns)"]),
        ("Admit → buffer", ["buffer += 1", "issue prefetch", "(if enabled)"]),
        ("Translate", ["IOTLB lookup", "hit → done", "miss → walk"]),
        ("Page-walk", ["MSHR coalesce", "PWC / G-stage walk", "walkers + memory"]),
        ("Complete", ["fill caches", "finish request", "free buffer / walker"]),
    ]
    for x, (title, lines) in zip(XS, flow):
        box(ax, x, FY, FW, FH, FLOW)
        ax.text(x, FY + 0.45, title, ha="center", fontsize=11, fontweight="bold")
        for i, ln in enumerate(lines):
            ax.text(x, FY + 0.05 - i * 0.32, ln, ha="center", fontsize=8.2)
    for a, b in zip(XS, XS[1:]):
        flow_arrow(ax, a + FW / 2, b - FW / 2, FY)
    # feedback (complete -> admit)
    ax.annotate("", xy=(XS[1], FY + FH / 2 + 0.05), xytext=(XS[4], FY + FH / 2 + 0.05),
                arrowprops=dict(arrowstyle="-|>", lw=1.0, color="#aaa",
                                ls=(0, (3, 3)), connectionstyle="arc3,rad=-0.25"))
    ax.text(7.2, 7.95 - 0.0, "", ha="center")
    ax.text((XS[1] + XS[4]) / 2, 7.62, "completion frees a slot → admit next / dispatch waiter",
            ha="center", fontsize=8, color="#999")

    # ---------- measurement row (tap under stages 2..5) ----------
    MY, MW, MH = 2.35, 2.75, 3.25
    meas = [
        (XS[1], M_BUF, "#a60", "Buffer count  (3d)", [
            "buffer: +1 admit / -1 complete", "io_bridge: IOTLB-miss holders",
            "peak = max(occupancy)", "time-weighted distribution", "→ mode / peak(0-stall)"]),
        (XS[2], M_CACHE, "#1a7", "Cache hit / miss", [
            "lookup() counts hits/misses", "IOTLB tag = VPN; PWC = VPN-prefix;",
            "G-stage = GPA  (+ context)", "hit_rate = hits/(hits+misses)",
            "demand vs prefetch split"]),
        (XS[3], M_WALK, "#960", "Walker count  (3c, N)", [
            "active_walks ++ at walk start,", "-- at walk_done",
            "(1 outstanding read each)", "peak_walks = max(active_walks)",
            "time-weighted distribution"]),
        (XS[4], M_LAT, "#a06", "Latency & Throughput", [
            "latency = complete - arrival", "  per request → avg / p99 / max",
            "throughput = completed /", "  (last_complete - first_arrival)",
            "→ M/s vs wire-rate target"]),
    ]
    for x, fc, tc, title, lines in meas:
        tap(ax, x, FY - FH / 2 - 0.05, MY + MH / 2 + 0.05)
        box(ax, x, MY, MW, MH, fc)
        ax.text(x, MY + MH / 2 - 0.32, title, ha="center", fontsize=10.5,
                fontweight="bold", color=tc)
        for i, ln in enumerate(lines):
            ax.text(x - MW / 2 + 0.18, MY + MH / 2 - 0.78 - i * 0.40, ln,
                    ha="left", fontsize=8.0)

    ax.text(7.2, 0.30, "Resources are unlimited when measuring 3c/3d → the peaks are the "
            "requirement; mode = typical steady level.",
            ha="center", fontsize=8.6, style="italic", color="#555")

    fig.savefig(os.path.join(FIGS, "sim_overview.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("figures/sim_overview.png")


if __name__ == "__main__":
    overview()
