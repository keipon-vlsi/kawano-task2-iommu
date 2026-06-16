#!/usr/bin/env python3
"""Diagrams of the iommu_sim structure (matplotlib only, no external tools).

Produces:
  figures/sim_architecture.png   -- engine/policy module map + data products
  figures/sim_request_flow.png   -- event-driven request lifecycle + latency

Run:  python generate_diagrams.py
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

BASE = os.path.dirname(os.path.abspath(__file__))
FIGS = os.path.join(BASE, "figures")
os.makedirs(FIGS, exist_ok=True)

# palette
C_ENGINE = "#cfe2f3"   # blue   - engine core
C_POLICY = "#d9ead3"   # green  - swappable policy
C_DEC    = "#fff2cc"   # yellow - decision / lookup
C_TERM   = "#ead1dc"   # pink   - terminal / complete
C_OUT    = "#f3f3f3"   # gray   - outputs
C_RES    = "#fce5cd"   # orange - resources


def box(ax, x, y, w, h, text, fc, fs=9, ec="black", bold=False, lw=1.3):
    p = FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                       boxstyle="round,pad=0.015,rounding_size=0.08",
                       fc=fc, ec=ec, lw=lw, zorder=2)
    ax.add_patch(p)
    ax.text(x, y, text, ha="center", va="center", fontsize=fs, zorder=3,
            fontweight="bold" if bold else "normal")
    return (x, y, w, h)


def arrow(ax, p_from, p_to, color="0.25", lw=1.5, ls="-", text=None,
          tcolor="black", tfs=8, rad=0.0, tdx=0.0, tdy=0.18):
    ax.annotate("", xy=p_to, xytext=p_from,
                arrowprops=dict(arrowstyle="-|>", lw=lw, color=color, ls=ls,
                                connectionstyle=f"arc3,rad={rad}"), zorder=1)
    if text:
        mx = (p_from[0] + p_to[0]) / 2 + tdx
        my = (p_from[1] + p_to[1]) / 2 + tdy
        ax.text(mx, my, text, ha="center", va="center", fontsize=tfs,
                color=tcolor, zorder=4)


def bottom(b):  # bottom-center anchor of a box (x,y,w,h)
    return (b[0], b[1] - b[3] / 2)
def top(b):
    return (b[0], b[1] + b[3] / 2)
def left(b):
    return (b[0] - b[2] / 2, b[1])
def right(b):
    return (b[0] + b[2] / 2, b[1])


# ==========================================================================
# Diagram 1 — Architecture: engine core + swappable policies
# ==========================================================================
def architecture():
    fig, ax = plt.subplots(figsize=(13, 8))
    ax.set_xlim(0, 13); ax.set_ylim(0, 10); ax.axis("off")
    XC = 6.5

    # config (top)
    cfg = box(ax, XC, 9.3, 4.6, 0.7,
              "config.py  —  YAML/JSON  (mode, caches, walkers, buffers,\n"
              "prefetch, memory, timing, workload)", C_OUT, fs=9, bold=True)

    # engine core (center)  -> spans x 4.4..8.6
    eng = box(ax, XC, 5.6, 4.2, 3.0, "", C_ENGINE, lw=2.0)
    ax.text(XC, 6.75, "engine.py  —  EVENT-DRIVEN CORE",
            ha="center", va="center", fontsize=10, fontweight="bold")
    ax.text(XC, 6.42, "(not edited for policy changes)", ha="center",
            va="center", fontsize=8, style="italic", color="0.35")
    for i, t in enumerate([
            "event queue (cycle-keyed heapq)",
            "transaction buffer + I/O-bridge buffer",
            "walker pool + MSHR coalescing",
            "metrics (latency, peaks, hit/miss, conc.)"]):
        ax.text(XC, 6.0 - i * 0.43, "• " + t, ha="center", va="center", fontsize=8)

    eng_l = eng[0] - eng[2] / 2
    eng_r = eng[0] + eng[2] / 2

    # swappable policies (left + right), each plugs into the engine via an ABC
    left_pol = [
        ("workload.py", "trace: sequential / stride / random\n(IOVA + data-GPA streams)"),
        ("memory.py", "AXI model: latency, outstanding,\nbank-parallel, coalescing"),
    ]
    right_pol = [
        ("caches.py", "CacheSet: IOTLB / VM-PWC /\nG-PWC / G-final / DDT$,PDT$,MSI$"),
        ("walker.py", "walk cost model (deepest-first)\n+ context walk (DDTW/PDTW)"),
        ("prefetch.py", "off / next_line / stride /\nrpt / dcpt / sms"),
    ]
    for i, (name, desc) in enumerate(left_pol):
        b = box(ax, 2.1, 6.4 - i * 2.0, 3.0, 1.25, name + "\n" + desc, C_POLICY, fs=8)
        arrow(ax, right(b), (eng_l, b[1]))
    for i, (name, desc) in enumerate(right_pol):
        b = box(ax, 10.9, 7.0 - i * 1.55, 3.0, 1.2, name + "\n" + desc, C_POLICY, fs=8)
        arrow(ax, left(b), (eng_r, b[1]))

    arrow(ax, bottom(cfg), top(eng), text="configures", tdy=0.0, tdx=0.6)

    # outputs (bottom) — fan out from a hub below the engine
    hub = (XC, 2.9)
    arrow(ax, bottom(eng), hub, text="metrics / CacheSet", tdy=0.0, tdx=1.5)
    outs = [
        ("run.py", "single run → baseline.log\n(latency, 3c/3d, hit/miss)"),
        ("estimator.py", "area (GE) + power\n+ freeze/*.json"),
        ("sweep.py", "min-HW search, Pareto\n(results.csv, *.svh)"),
    ]
    for i, (name, desc) in enumerate(outs):
        x = 2.7 + i * 3.8
        b = box(ax, x, 1.4, 3.1, 1.1, name + "\n" + desc, C_OUT, fs=8)
        arrow(ax, hub, top(b), color="0.45")

    ax.text(XC, 0.35, "Engine ↔ policy split: a new design option = a new policy "
            "behind an ABC + a config field (engine unchanged).",
            ha="center", fontsize=8.5, style="italic", color="0.3")
    ax.set_title("iommu_sim — module architecture", fontsize=13, fontweight="bold")
    fig.savefig(os.path.join(FIGS, "sim_architecture.png"), dpi=150,
                bbox_inches="tight")
    plt.close(fig)
    print("[diagram] figures/sim_architecture.png")


# ==========================================================================
# Diagram 2 — Request lifecycle (event-driven flow) + latency
# ==========================================================================
def request_flow():
    fig, ax = plt.subplots(figsize=(12.5, 13.5))
    ax.set_xlim(0, 12); ax.set_ylim(0, 15); ax.axis("off")

    XS = 3.3          # spine x
    XB = 8.6          # branch/complete x
    W, H = 3.4, 0.92

    # --- spine boxes (top -> bottom) ---
    n_acc = "n"
    spine = [
        ("wl",   13.9, "workload.py → arrival events\n(spaced at wire-rate inter-arrival, 40 ns)", C_POLICY),
        ("arr",  12.5, "_on_arrival\nrequest buffer full?  → back-pressure", C_DEC),
        ("adm",  11.1, "_admit  (buffer +1, track peak_buffer)\nprefetch.predict → issue prefetch reqs", C_ENGINE),
        ("tr",    9.7, "_translate → IOTLB.peek( vpn,ctx )", C_DEC),
        ("mshr",  8.3, "IOTLB miss → line already in MSHR?", C_DEC),
        ("warm",  6.9, "warm_hit?  VM-L0 & G-final-L0 cached", C_DEC),
        ("reg",   5.5, "register MSHR + _start_line\n(gate: free walker?  memory.can_issue?)", C_ENGINE),
        ("cost",  4.1, "walk cost model (deepest-first probe)\n→ accesses n  (+ DDTW/PDTW if context_walk)", C_ENGINE),
        ("walk",  2.7, "memory.enter (1 outstanding); active_walks++\nwalk_done after  A+P + n·M cyc", C_ENGINE),
        ("done",  1.3, "_on_walk_done: fill IOTLB / VM-PWC / G-PWC / G-final\n→ complete each waiter", C_ENGINE),
    ]
    B = {}
    for key, y, txt, fc in spine:
        B[key] = box(ax, XS, y, W, H, txt, fc, fs=8.2)

    # spine arrows
    chain = ["wl", "arr", "adm", "tr", "mshr", "warm", "reg", "cost", "walk", "done"]
    for a, b in zip(chain, chain[1:]):
        arrow(ax, bottom(B[a]), top(B[b]))

    # --- complete box (right) ---
    comp = box(ax, XB, 2.7, 3.6, 1.5,
               "_on_complete\nlatency = complete − arrival\n"
               "buffer −1, io_bridge −1\nretry back-pressured waiters", C_TERM, fs=8.5)

    # --- exit branches to complete, with latency labels ---
    # IOTLB hit
    arrow(ax, right(B["tr"]), (XB, B["tr"][1]), color="#2a7", lw=1.6)
    ax.text(XB, B["tr"][1] + 0.25, "IOTLB HIT  →  7.5 ns  (L+H)", color="#1a7",
            fontsize=8.5, ha="center", fontweight="bold")
    arrow(ax, (XB, B["tr"][1]), top(comp), color="#2a7", lw=1.4, rad=-0.15)
    # MSHR coalesce
    arrow(ax, right(B["mshr"]), (XB - 0.0, B["mshr"][1]), color="#27a", lw=1.6)
    ax.text(XB, B["mshr"][1] + 0.25, "in MSHR → coalesce\n(mshr_coalesced, share walk)",
            color="#16a", fontsize=8, ha="center")
    arrow(ax, (XB, B["mshr"][1]), (XB, 3.45), color="#27a", lw=1.4)
    # warm fast-path
    arrow(ax, right(B["warm"]), (XB, B["warm"][1]), color="#a06", lw=1.6)
    ax.text(XB, B["warm"][1] + 0.25, "warm → PWC hit  →  10 ns  (L+A+H)",
            color="#a06", fontsize=8.5, ha="center", fontweight="bold")
    arrow(ax, (XB, B["warm"][1]), (XB, 3.45), color="#a06", lw=1.4)
    # walk_done -> complete
    arrow(ax, right(B["done"]), left(comp), color="#444", lw=1.6)
    ax.text((XS + XB) / 2 + 0.3, 1.05,
            "walk  →  (12.5 + 100·n) ns", color="#444", fontsize=8.5,
            ha="center", fontweight="bold")
    # complete -> retry (buffer frees, dashed up)
    arrow(ax, top(comp), (XB, 11.1), color="0.6", lw=1.0, ls="--", rad=0.25,
          text="free slot → admit / retry waiter", tcolor="0.4", tfs=7.5, tdx=1.6, tdy=0.0)

    # --- back-pressure side notes (left) ---
    box(ax, 1.0, 12.5, 1.7, 0.8, "buf_wait\narrival_stall", C_OUT, fs=7.5)
    arrow(ax, left(B["arr"]), (1.85, 12.5), color="0.6", lw=1.0, ls="--")
    box(ax, 1.0, 9.7, 1.7, 0.8, "io_bridge full\n→ iob_wait", C_OUT, fs=7.5)
    arrow(ax, left(B["tr"]), (1.85, 9.7), color="0.6", lw=1.0, ls="--")

    # --- resources legend (top-right) ---
    rx, ry = 9.6, 13.6
    box(ax, rx, ry, 4.4, 2.2, "", C_RES, lw=1.5)
    ax.text(rx, ry + 0.85, "RESOURCE POOLS (caps)", ha="center", fontsize=9,
            fontweight="bold")
    for i, t in enumerate([
            "walkers:  active_walks ≤ num_walkers   → peak_walks (3c)",
            "MSHR file:  one entry per in-flight leaf line",
            "request buffer:  in-flight demands  → peak_buffer (3d)",
            "I/O-bridge:  4 kB payload holders (IOTLB misses)",
            "memory:  outstanding ≤ max_outstanding"]):
        ax.text(rx - 2.05, ry + 0.5 - i * 0.38, "• " + t, ha="left", fontsize=7.6)

    # latency legend (bottom-left)
    lx, ly = 2.0, 0.55
    ax.text(lx, ly, "M = 40 cyc (100 ns) per memory access · L=2 A=1 P=2 H=1 cyc · 1 cyc = 2.5 ns\n"
            "n = serial memory reads on the critical path (the 0–15 'state' depth)",
            ha="left", fontsize=7.8, style="italic", color="0.3")

    ax.set_title("iommu_sim — request lifecycle (event-driven flow & latency)",
                 fontsize=13, fontweight="bold")
    fig.savefig(os.path.join(FIGS, "sim_request_flow.png"), dpi=150,
                bbox_inches="tight")
    plt.close(fig)
    print("[diagram] figures/sim_request_flow.png")


if __name__ == "__main__":
    architecture()
    request_flow()
    print("diagrams done.")
