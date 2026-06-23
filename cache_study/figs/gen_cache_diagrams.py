#!/usr/bin/env python3
"""Block diagrams for the cache_study report.
  figures/cache_structures.png  -- lookup datapath of representative PWC & IOTLB variants
  figures/pipeline_split.png    -- how the fused lookup/issue cone was carved into stages
matplotlib only. Run:  .venv/bin/python3 cache_study/figs/gen_cache_diagrams.py
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams['font.family']=['Noto Sans CJK JP','DejaVu Sans']
plt.rcParams['axes.unicode_minus']=False
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

BASE = os.path.dirname(os.path.abspath(__file__))
FIGS = os.path.join(BASE, "figures")
os.makedirs(FIGS, exist_ok=True)

C_STORE = "#cfe2f3"   # blue   - storage (DFF)
C_CMP   = "#fff2cc"   # yellow - comparator / tag match
C_MUX   = "#d9ead3"   # green  - mux / select
C_ARITH = "#f4cccc"   # red    - arithmetic (slow!)
C_REG   = "#ead1dc"   # pink   - pipeline register
C_LOGIC = "#fce5cd"   # orange - misc logic / priority
C_IO    = "#f3f3f3"   # gray   - I/O


def box(ax, x, y, w, h, text, fc, fs=8.5, bold=False, ec="black", lw=1.2):
    ax.add_patch(FancyBboxPatch((x - w/2, y - h/2), w, h,
        boxstyle="round,pad=0.02,rounding_size=0.06", fc=fc, ec=ec, lw=lw))
    ax.text(x, y, text, ha="center", va="center", fontsize=fs,
            fontweight="bold" if bold else "normal", zorder=5)


def arrow(ax, x1, y1, x2, y2, lw=1.4, color="black", style="-|>"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
        mutation_scale=11, lw=lw, color=color, shrinkA=2, shrinkB=2, zorder=4))


def panel(ax, title, sub):
    ax.set_xlim(0, 10); ax.set_ylim(0, 10); ax.axis("off")
    ax.text(5, 9.6, title, ha="center", va="center", fontsize=11, fontweight="bold")
    ax.text(5, 9.05, sub, ha="center", va="center", fontsize=8, style="italic", color="#444")


# ============================================================ Fig 1: cache structures
def fig_structures():
    fig, axs = plt.subplots(2, 4, figsize=(20, 9.5))
    fig.suptitle("Cache lookup datapaths — representative PWC & IOTLB variants "
                 "(storage=DFF; sky130 post-synth)", fontsize=14, fontweight="bold")

    # ---- PWC P0: 2-way FA (baseline) ----
    ax = axs[0][0]; panel(ax, "PWC P0  2-way FA (baseline)", "2 tag compares + priority 2:1 mux  ·  460 MHz / depth5")
    box(ax, 2.0, 7.4, 2.4, 0.8, "tag_q[0]\nspa_q[0] (DFF)", C_STORE, 7.5)
    box(ax, 2.0, 5.6, 2.4, 0.8, "tag_q[1]\nspa_q[1] (DFF)", C_STORE, 7.5)
    box(ax, 5.0, 7.4, 1.7, 0.7, "== (18b)", C_CMP)
    box(ax, 5.0, 5.6, 1.7, 0.7, "== (18b)", C_CMP)
    box(ax, 7.2, 6.5, 1.7, 0.9, "priority\n2:1 mux", C_MUX)
    box(ax, 5.0, 3.7, 1.7, 0.7, "OR", C_LOGIC)
    arrow(ax, 0.6, 6.5, 0.8, 6.5); ax.text(0.5, 6.9, "lk_tag", fontsize=8)
    arrow(ax, 0.8, 6.5, 3.2, 7.4); arrow(ax, 0.8, 6.5, 3.2, 5.6)
    arrow(ax, 3.2, 7.4, 4.15, 7.4); arrow(ax, 3.2, 5.6, 4.15, 5.6)
    arrow(ax, 5.85, 7.4, 6.35, 6.8); arrow(ax, 5.85, 5.6, 6.35, 6.2)
    arrow(ax, 5.85, 7.4, 4.3, 4.0, color="#888"); arrow(ax, 5.85, 5.6, 4.3, 3.9, color="#888")
    arrow(ax, 8.05, 6.5, 9.2, 6.5); ax.text(9.4, 6.5, "spa", fontsize=8)
    arrow(ax, 5.85, 3.7, 9.2, 3.7); ax.text(9.4, 3.7, "hit", fontsize=8)
    ax.text(5, 1.7, "robust: any tag. compare→mux SERIAL.", ha="center", fontsize=8, color="#333")

    # ---- PWC P2: even-aligned window (smallest) ----
    ax = axs[0][1]; panel(ax, "PWC P2  even-aligned window", "LSB selects (no compare) + 17b compare  ·  480 MHz / SMALLEST")
    box(ax, 2.2, 7.0, 2.6, 0.9, "baseHi_q[16:0]\nspa_q[2] (DFF)", C_STORE, 7.5)
    box(ax, 5.3, 7.4, 1.8, 0.7, "== (17b)\nhi bits", C_CMP)
    box(ax, 5.3, 5.4, 1.9, 0.9, "2:1 mux\nsel = tag[0]", C_MUX, 8)
    box(ax, 7.6, 6.4, 1.5, 0.8, "AND", C_LOGIC)
    arrow(ax, 0.7, 6.4, 1.0, 6.4); ax.text(0.6, 6.85, "lk_tag", fontsize=8)
    arrow(ax, 1.0, 6.4, 3.5, 7.0)
    arrow(ax, 3.5, 7.2, 4.4, 7.4)
    arrow(ax, 3.5, 6.8, 4.35, 5.6, color="#888")
    arrow(ax, 1.2, 6.0, 4.35, 5.2, color="#c00"); ax.text(2.2, 4.7, "tag[0] (LSB)\nDIRECT select", fontsize=7, color="#c00")
    arrow(ax, 6.2, 7.4, 7.0, 6.7); arrow(ax, 6.25, 5.4, 7.0, 6.1, color="#888")
    arrow(ax, 6.25, 5.4, 9.2, 5.4); ax.text(9.4, 5.4, "spa", fontsize=8)
    arrow(ax, 8.35, 6.4, 9.2, 6.4); ax.text(9.4, 6.7, "hit", fontsize=8)
    ax.text(5, 1.7, "no subtractor; LSB = entry index. shallow & small.", ha="center", fontsize=8, color="#333")

    # ---- PWC P1: base+delta (WORST) ----
    ax = axs[0][2]; panel(ax, "PWC P1  base + delta  (anti-pattern)", "18b SUBTRACTOR on path  ·  222 MHz / depth15 WORST")
    box(ax, 2.2, 7.0, 2.4, 0.9, "base_q[17:0]\nspa_q[2] (DFF)", C_STORE, 7.5)
    box(ax, 5.2, 7.0, 2.3, 1.1, "lk_tag − base\n18b SUBTRACTOR\n(carry chain)", C_ARITH, 8, bold=True)
    box(ax, 8.0, 7.4, 1.6, 0.7, "d[17:1]==0\n(in-window)", C_CMP, 7.5)
    box(ax, 8.0, 5.7, 1.6, 0.8, "2:1 mux\nsel=d[0]", C_MUX, 7.5)
    arrow(ax, 0.7, 6.7, 1.0, 6.7); ax.text(0.6, 7.15, "lk_tag", fontsize=8)
    arrow(ax, 1.0, 6.7, 3.4, 7.0); arrow(ax, 3.4, 7.0, 4.05, 7.0)
    arrow(ax, 6.35, 7.2, 7.2, 7.4); arrow(ax, 6.35, 6.7, 7.2, 5.9)
    ax.text(5, 3.2, "the (tag−base) carry chain (maj3×4 …)\nis the critical path → 15 logic levels.",
            ha="center", fontsize=8.5, color="#c00")
    ax.text(5, 1.5, "LESSON: arithmetic in the window test kills depth.", ha="center", fontsize=8, color="#333", fontweight="bold")

    # ---- PWC P4: speculative read (FASTEST) ----
    ax = axs[0][3]; panel(ax, "PWC P4  speculative read", "predicted-index read ∥ late validate  ·  615 MHz FASTEST")
    box(ax, 2.2, 7.0, 2.4, 0.9, "tag_q[2]\nspa_q[2] (DFF)", C_STORE, 7.5)
    box(ax, 5.4, 7.7, 2.0, 0.8, "2:1 mux\nsel=tag[0]", C_MUX, 8, bold=True)
    box(ax, 5.4, 5.6, 1.8, 0.8, "== (18b)\nvalidate", C_CMP)
    arrow(ax, 0.7, 6.6, 1.0, 6.6); ax.text(0.55, 7.05, "lk_tag", fontsize=8)
    arrow(ax, 1.0, 6.6, 3.4, 7.0)
    arrow(ax, 3.4, 7.2, 4.4, 7.7); arrow(ax, 3.4, 6.8, 4.5, 5.7, color="#888")
    arrow(ax, 1.2, 6.2, 4.4, 7.6, color="#070"); ax.text(2.0, 8.4, "tag[0]→read NOW", fontsize=7, color="#070")
    arrow(ax, 6.4, 7.7, 9.2, 7.7); ax.text(9.4, 7.7, "spa\n(fast)", fontsize=8)
    arrow(ax, 6.3, 5.6, 9.2, 5.6); ax.text(9.4, 5.6, "hit\n(parallel)", fontsize=8)
    ax.text(5, 2.8, "SPA read does NOT wait for compare;\ncompare only builds 'hit' in parallel.",
            ha="center", fontsize=8.5, color="#070")
    ax.text(5, 1.3, "shortest data path → highest Fmax.", ha="center", fontsize=8, color="#333")

    # ---- IOTLB T0: line baseline (current) ----
    ax = axs[1][0]; panel(ax, "IOTLB T0  line baseline (current)", "2 line-tag compares + offset 8:1 mux  ·  344 MHz")
    box(ax, 2.0, 7.3, 2.4, 0.9, "line0: ltag24b\n+ 8×spa (DFF)", C_STORE, 7.5)
    box(ax, 2.0, 5.4, 2.4, 0.9, "line1: ltag24b\n+ 8×spa (DFF)", C_STORE, 7.5)
    box(ax, 5.0, 7.3, 1.6, 0.7, "==(24b)", C_CMP)
    box(ax, 5.0, 5.4, 1.6, 0.7, "==(24b)", C_CMP)
    box(ax, 7.3, 6.3, 1.7, 1.0, "8:1 mux\noff=VPN[2:0]\n→ 2:1", C_MUX, 7.5)
    arrow(ax, 0.6, 6.3, 0.8, 6.3); ax.text(0.45, 6.75, "VPN", fontsize=8)
    arrow(ax, 0.8, 6.3, 3.2, 7.3); arrow(ax, 0.8, 6.3, 3.2, 5.4)
    arrow(ax, 3.2, 7.3, 4.2, 7.3); arrow(ax, 3.2, 5.4, 4.2, 5.4)
    arrow(ax, 5.8, 7.3, 6.45, 6.7); arrow(ax, 5.8, 5.4, 6.45, 5.9)
    arrow(ax, 8.15, 6.3, 9.2, 6.3); ax.text(9.35, 6.3, "spa/hit", fontsize=8)
    ax.text(5, 1.7, "offset never compared (X1/X5). line tag = 2 compares.", ha="center", fontsize=8, color="#333")

    # ---- IOTLB T1: aligned single window (fastest) ----
    ax = axs[1][1]; panel(ax, "IOTLB T1  aligned single window", "1 compare (23b) + 16:1 index mux  ·  464 MHz FASTEST")
    box(ax, 2.2, 6.8, 2.6, 1.0, "base23b + 16×spa\n(DFF, one window)", C_STORE, 7.5)
    box(ax, 5.4, 7.5, 1.8, 0.7, "== (23b)\nbase", C_CMP, bold=True)
    box(ax, 5.4, 5.5, 1.9, 0.9, "16:1 mux\nidx=VPN[3:0]", C_MUX, 8, bold=True)
    box(ax, 7.7, 6.5, 1.4, 0.8, "AND", C_LOGIC)
    arrow(ax, 0.7, 6.3, 1.0, 6.3); ax.text(0.55, 6.75, "VPN", fontsize=8)
    arrow(ax, 1.0, 6.3, 3.5, 6.8)
    arrow(ax, 3.5, 7.0, 4.5, 7.5)
    arrow(ax, 1.2, 5.9, 4.45, 5.6, color="#c00"); ax.text(2.2, 5.0, "VPN[3:0]\nDIRECT index", fontsize=7, color="#c00")
    arrow(ax, 6.3, 7.5, 7.1, 6.8); arrow(ax, 6.35, 5.5, 7.1, 6.2, color="#888")
    arrow(ax, 6.35, 5.5, 9.2, 5.5); ax.text(9.35, 5.5, "spa", fontsize=8)
    arrow(ax, 8.4, 6.5, 9.2, 6.5); ax.text(9.35, 6.8, "hit", fontsize=8)
    ax.text(5, 1.7, "ONE window tag → 1 compare. flat 16:1 by index.", ha="center", fontsize=8, color="#333")

    # ---- IOTLB T4: base+offset (smallest, contiguous bet) ----
    ax = axs[1][2]; panel(ax, "IOTLB T4  base + offset (contiguous)", "store base_ppn+adder, 8× less data  ·  127 MHz / SMALLEST")
    box(ax, 2.2, 6.8, 2.6, 1.0, "2× {ltag24b,\nbase_ppn44b, contig}\n(DFF — no 16×spa)", C_STORE, 7)
    box(ax, 5.2, 7.4, 1.6, 0.7, "==(24b)×2", C_CMP)
    box(ax, 5.3, 5.4, 2.1, 1.0, "base_ppn +\nVPN[2:0]\nADDER (44b)", C_ARITH, 8, bold=True)
    arrow(ax, 0.7, 6.3, 1.0, 6.3); ax.text(0.55, 6.75, "VPN", fontsize=8)
    arrow(ax, 1.0, 6.3, 3.5, 6.8)
    arrow(ax, 3.5, 7.1, 4.4, 7.4); arrow(ax, 3.5, 6.5, 4.25, 5.7, color="#888")
    arrow(ax, 6.0, 7.4, 9.2, 7.4); ax.text(9.35, 7.4, "hit", fontsize=8)
    arrow(ax, 6.35, 5.4, 9.2, 5.4); ax.text(9.35, 5.4, "spa", fontsize=8)
    ax.text(5, 2.9, "DFF 843→229 (drop 16×spa).\nBUT adder on SPA path → 28 levels.", ha="center", fontsize=8.5, color="#c00")
    ax.text(5, 1.4, "BET: contiguous physical. area↓↓ Fmax↓↓.", ha="center", fontsize=8, color="#333", fontweight="bold")

    # ---- IOTLB T5 vs T6: priority encoder removed ----
    ax = axs[1][3]; panel(ax, "IOTLB T5→T6  drop priority encoder", "16-way FA: one-hot ⇒ AND-OR mux  ·  246→368 MHz +50%")
    box(ax, 2.0, 7.0, 2.2, 0.9, "16× tag\n+ 16× spa (DFF)", C_STORE, 7.5)
    box(ax, 4.8, 7.0, 1.7, 0.8, "16× ==\n(27b)", C_CMP)
    box(ax, 7.2, 8.0, 2.1, 0.9, "T5: PRIORITY\nencoder + 16:1", C_LOGIC, 7.5)
    box(ax, 7.2, 5.9, 2.1, 0.9, "T6: AND-OR\none-hot mux", C_MUX, 7.5, bold=True)
    arrow(ax, 0.7, 6.6, 0.9, 6.6); ax.text(0.45, 7.05, "VPN", fontsize=8)
    arrow(ax, 0.9, 6.6, 3.0, 7.0); arrow(ax, 3.95, 7.0, 4.0, 7.0)
    arrow(ax, 5.65, 7.0, 6.15, 7.8, color="#a00"); arrow(ax, 5.65, 7.0, 6.15, 6.1, color="#070")
    arrow(ax, 8.25, 8.0, 9.2, 8.0); ax.text(9.35, 8.0, "slow", fontsize=7, color="#a00")
    arrow(ax, 8.25, 5.9, 9.2, 5.9); ax.text(9.35, 5.9, "fast", fontsize=7, color="#070")
    ax.text(5, 3.0, "a VPN is cached once ⇒ match is ONE-HOT ⇒\nno priority needed → balanced OR tree.",
            ha="center", fontsize=8.5, color="#070")
    ax.text(5, 1.4, "depth 10→7, +50% Fmax, area ~same.", ha="center", fontsize=8, color="#333")

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    p = os.path.join(FIGS, "cache_structures.png")
    fig.savefig(p, dpi=130, bbox_inches="tight"); plt.close(fig)
    print("wrote", p)


# ============================================================ Fig 2: pipeline split
def fig_pipeline():
    fig, axs = plt.subplots(2, 1, figsize=(15, 10))
    fig.suptitle("Pipelining cfg5: how the fused lookup→issue cone was carved into stages "
                 "(PIPELINE_DEPTH 1 → ≥2)", fontsize=14, fontweight="bold")

    # ---------- top: fused (no pipeline, PD=1) ----------
    ax = axs[0]; ax.set_xlim(0, 16); ax.set_ylim(0, 6); ax.axis("off")
    ax.text(8, 5.6, "BEFORE — fused single-cycle cone (PD=1, ~215–222 MHz)", ha="center",
            fontsize=12, fontweight="bold", color="#a00")
    chain = [
        ("rdata /\nbvpn_q\n(reg)", C_REG),
        ("cache\nlookup\n(CAM)", C_CMP),
        ("next-state /\nstart-base\ndecode", C_LOGIC),
        ("idx_of +\npte_addr\n(addr gen)", C_ARITH),
        ("walker /\nstats\ncounters", C_LOGIC),
        ("issue\narbiter", C_MUX),
        ("araddr\n(reg)", C_REG),
    ]
    n = len(chain); w = 1.7; gap = (16 - n*w) / (n+1)
    xs = [gap + w/2 + i*(w+gap) for i in range(n)]
    for x, (t, c) in zip(xs, chain):
        box(ax, x, 3.2, w, 1.3, t, c, 8)
    for i in range(n-1):
        arrow(ax, xs[i]+w/2, 3.2, xs[i+1]-w/2, 3.2)
    ax.annotate("", xy=(xs[-1], 1.7), xytext=(xs[0], 1.7),
                arrowprops=dict(arrowstyle="<->", color="#a00", lw=1.6))
    ax.text(8, 1.2, "ALL of this in ONE clock period  →  one long combinational path = low Fmax",
            ha="center", fontsize=9.5, color="#a00")

    # ---------- bottom: pipelined (PD>=2) ----------
    ax = axs[1]; ax.set_xlim(0, 16); ax.set_ylim(0, 7.2); ax.axis("off")
    ax.text(8, 6.8, "AFTER — carved into stages by registering at the cut points "
            "(PD≥2 → up to 395 MHz)", ha="center", fontsize=12, fontweight="bold", color="#070")

    # stage A: probe
    box(ax, 2.4, 5.3, 2.6, 1.0, "STAGE A — PROBE\n(cache lookup)", C_CMP, 9, bold=True)
    ax.text(2.4, 4.55, "bvpn_q → IOTLB/PWC CAM compare", ha="center", fontsize=7.5)
    box(ax, 2.4, 3.7, 2.6, 0.7, "stg_* regs (v5)", C_REG, 8)
    # stage B: commit + precompute
    box(ax, 6.2, 5.3, 2.8, 1.0, "STAGE B — COMMIT\nlaunch + precompute", C_LOGIC, 9, bold=True)
    ax.text(6.2, 4.55, "start_base mux; iaddr_of precompute (v4/v9)", ha="center", fontsize=7.2)
    box(ax, 6.2, 3.7, 2.8, 0.7, "wiaddr_q / wbase_q (v4)", C_REG, 8)
    # stage C: addr-gen (v10)
    box(ax, 10.2, 5.3, 2.6, 1.0, "STAGE C — ADDR-GEN\n(consume side, v10)", C_ARITH, 9, bold=True)
    ax.text(10.2, 4.55, "iaddr_of from registered walker state", ha="center", fontsize=7.2)
    box(ax, 10.2, 3.7, 2.6, 0.7, "wiaddr_q (wia_rdy)", C_REG, 8)
    # stage D: issue
    box(ax, 13.7, 5.3, 2.2, 1.0, "STAGE D — ISSUE\narbiter → AR", C_MUX, 9, bold=True)
    box(ax, 13.7, 3.7, 2.2, 0.7, "araddr (reg)", C_REG, 8)

    for x1, x2 in [(3.7, 4.9), (7.6, 8.9), (11.5, 12.6)]:
        arrow(ax, x1, 4.5, x2, 4.5, lw=1.6, color="#070")

    # removed-from-path callouts
    box(ax, 4.3, 1.9, 3.2, 1.0, "v3: stats counters\n→ registered enable\n(off critical path)", C_STORE, 7.5)
    box(ax, 8.6, 1.9, 3.4, 1.0, "v6: 16-way CAM → line IOTLB\n(tag-compare + offset mux)\narea −20%, depth↓", C_STORE, 7.5)
    box(ax, 12.6, 1.9, 3.0, 1.0, "v13: prefetch dedup\nprecomputed at probe\n(off launch path)", C_STORE, 7.5)
    ax.text(8, 0.5, "Each register cut = one pipeline stage. Latency +1 cyc/step (hidden by memory "
            "latency & prefetch); throughput unchanged. Fmax rises because each stage's cone is shorter.",
            ha="center", fontsize=9, color="#333")

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    p = os.path.join(FIGS, "pipeline_split.png")
    fig.savefig(p, dpi=130, bbox_inches="tight"); plt.close(fig)
    print("wrote", p)


if __name__ == "__main__":
    fig_structures()
    fig_pipeline()
