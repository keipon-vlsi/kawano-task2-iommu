#!/usr/bin/env python3
# cfg5 pipelining: Fmax vs version. Self-contained (data inlined) for Colab.
# x = version (v0..v10, hd), y = post-opt Fmax [MHz]. Adopted register-insertion steps
# are highlighted; rejected attempts (v2/v7/v8) shown hollow. Run: just `pip install
# matplotlib` and execute. Edit DATA freely.
import matplotlib.pyplot as plt
# Japanese font for Colab (auto-install japanize-matplotlib; harmless if it fails)
try:
    import japanize_matplotlib  # noqa: F401
except Exception:
    try:
        import subprocess, sys
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "japanize-matplotlib"], check=False)
        import japanize_matplotlib  # noqa: F401
    except Exception:
        pass  # fall back to default font (Japanese labels may not render)

# (version, Fmax_MHz, kind, note). kind: "base" | "pipe" (adopted reg-insertion) |
# "other" (adopted, non-pipeline) | "reject" (measured then reverted)
DATA = [
    ("v0",  160.8, "base",   "baseline (fused 1-cycle cone)"),
    ("v1",  155.0, "reject", "G-walk factoring (logic restructure)"),
    ("v2",  158.2, "reject", "issue pipe, wrong cut"),
    ("v3",  189.0, "pipe",   "retiming: counter enable reg"),
    ("v4",  207.0, "pipe",   "precompute issue addr (wiaddr_q)"),
    ("v5",  241.5, "pipe",   "servicer probe/commit (stg_*_q)"),
    ("v6",  234.7, "other",  "line-IOTLB (datapath restructure)"),
    ("v7",  229.9, "reject", "R-channel reg only (null)"),
    ("v8",  213.7, "reject", "commit+consume 2 paths (regress)"),
    ("v9",  261.8, "pipe",   "3 co-critical precomputed (stg_pf_*)"),
    ("v10", 287.4, "pipe",   "consume addr-gen stage (wia_rdy_q)"),
]
# kind -> (color, marker, filled, legend)
STY = {
    "base":   ("#333333", "o", True,  "baseline (v0)"),
    "pipe":   ("#c0392b", "o", True,  "パイプ化(レジスタ挿入)・採用"),
    "other":  ("#27ae60", "s", True,  "非パイプ最適化・採用 (v6)"),
    "reject": ("#888888", "x", False, "計測後リバート (v2/v7/v8/v1)"),
}

xs = list(range(len(DATA)))
labels = [d[0] for d in DATA]
fig, ax = plt.subplots(figsize=(12, 6.5))

# connect the ADOPTED trajectory (v0 + pipe + other) in version order
adopted = [(i, DATA[i][1]) for i, d in enumerate(DATA) if d[2] in ("base", "pipe", "other")]
ax.plot([i for i, _ in adopted], [v for _, v in adopted], "-", color="#c0392b", lw=1.6, zorder=2,
        alpha=0.7, label="採用版の Fmax 推移")

seen = set()
for i, (ver, f, kind, note) in enumerate(DATA):
    c, m, filled, leg = STY[kind]
    ax.scatter(i, f, s=130, marker=m, zorder=4,
               facecolor=(c if filled else "none"), edgecolor=c, linewidth=1.8,
               label=(leg if kind not in seen else None))
    seen.add(kind)
    dy = 9 if kind != "reject" else -16
    ax.annotate(f"{f:.0f}", (i, f), textcoords="offset points", xytext=(0, dy),
                ha="center", fontsize=8.5, fontweight="bold", color=c)

# delta arrows / labels for the adopted pipeline steps
for (ver, f, kind, note) in DATA:
    pass
deltas = [("v3", "+17.5%"), ("v4", "+9.5%"), ("v5", "+16.7%"), ("v9", "+11.5%"), ("v10", "+9.8%")]
for ver, d in deltas:
    i = labels.index(ver)
    ax.annotate(d, (i, DATA[i][1]), textcoords="offset points", xytext=(0, 22),
                ha="center", fontsize=8, color="#c0392b")

ax.set_xticks(xs); ax.set_xticklabels(labels)
ax.set_xlabel("バージョン", fontsize=11)
ax.set_ylabel("Fmax (post-opt, sky130 hd, ideal clock) [MHz]", fontsize=11)
ax.set_title("cfg5 パイプライン化による Fmax 向上：160.8 → 287.4 MHz (+78.7%, hd)",
             fontsize=13, fontweight="bold")
ax.grid(True, ls="--", alpha=0.4, axis="y")
ax.axhline(160.8, ls=":", color="#999", lw=1)
ax.text(0.1, 163, "v0 baseline 160.8", fontsize=8, color="#666")
ax.legend(loc="lower right", fontsize=9)
ax.set_ylim(140, 305)
fig.tight_layout()
fig.savefig("pipeline_fmax.png", dpi=140, bbox_inches="tight")
print("wrote pipeline_fmax.png")
plt.show()
