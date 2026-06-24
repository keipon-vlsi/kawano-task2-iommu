#!/usr/bin/env python3
"""Where v4/v5/v9/v10 inserted registers into cfg5's originally-1-cycle fused cone.
  figures/pipeline_cuts.png
Run: .venv/bin/python3 cache_study/figs/gen_pipeline_cuts.py
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams['font.family']=['Noto Sans CJK JP','DejaVu Sans']; plt.rcParams['axes.unicode_minus']=False
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

BASE=os.path.dirname(os.path.abspath(__file__)); FIGS=os.path.join(BASE,"figures"); os.makedirs(FIGS,exist_ok=True)
C_REG="#ead1dc"; C_A="#fff2cc"; C_B="#fce5cd"; C_C="#f4cccc"; C_D="#d9ead3"

def box(ax,x,y,w,h,t,fc,fs=9,bold=False):
    ax.add_patch(FancyBboxPatch((x-w/2,y-h/2),w,h,boxstyle="round,pad=0.02,rounding_size=0.05",
        fc=fc,ec="black",lw=1.2)); ax.text(x,y,t,ha="center",va="center",fontsize=fs,
        fontweight="bold" if bold else "normal",zorder=6)
def ar(ax,x1,y1,x2,y2,lw=1.5,c="black"):
    ax.add_patch(FancyArrowPatch((x1,y1),(x2,y2),arrowstyle="-|>",mutation_scale=12,lw=lw,color=c,zorder=4))
def cut(ax,x,y,label,col):  # a register-insertion mark (scissor line) between stages
    ax.plot([x,x],[y-0.55,y+0.55],color=col,lw=2.4,zorder=7)
    ax.plot([x-0.07,x+0.07],[y+0.55,y+0.7],color=col,lw=2.4)
    ax.text(x,y+0.95,label,ha="center",fontsize=8.5,color=col,fontweight="bold")

fig,ax=plt.subplots(figsize=(16,7)); ax.set_xlim(0,17); ax.set_ylim(0,8); ax.axis("off")
fig.suptitle("cfg5 パイプライン化：元の 1 サイクル融合コーンの「どこ」にレジスタを挿入したか (v4/v5/v9/v10)",
             fontsize=13,fontweight="bold")

# the fused cone, left->right
y=4.0
stages=[("[reg]\nrdata /\nbvpn_q",C_REG,1.3),
        ("A: cache\nlookup\n(IOTLB/PWC\nCAM 比較)",C_A,1.7),
        ("B: 次状態 /\nstart 判定\n(most-complete,\nsvc)",C_B,1.9),
        ("C: idx_of +\npte_addr\n(アドレス生成)",C_C,1.9),
        ("D: prefetch\npf_line(+LEAD)\n+ iaddr_of",C_D,1.9),
        ("[reg]\nwalker 状態 /\naraddr(発行)",C_REG,1.6)]
xs=[]; x=1.2
for t,c,w in stages:
    box(ax,x+w/2,y,w,1.6,t,c,8.5); xs.append((x,x+w,x+w/2)); x+=w+0.7
for i in range(len(stages)-1):
    ar(ax,xs[i][1],y,xs[i+1][0],y)
ax.text(8.5,6.4,"v0 = この A→B→C→D が 1 本の reg→reg 経路（160.8 MHz）。色付き縦線＝各版が挿入したレジスタ（段の切れ目）",
        ha="center",fontsize=9.5,style="italic",color="#333")

# cut marks between stages (x positions are the gaps between boxes)
gap=lambda i:(xs[i][1]+xs[i+1][0])/2
# v5: probe stage captures A(lookup)+B(decode) -> mark at the A/B boundary
cut(ax,gap(1),y,"v5\nprobe/commit\nstg_*_q","#1f6fb2")
# v4 / v10: C(addr-gen) moved to a separate cycle -> mark at the B/C boundary
cut(ax,gap(2),y,"v4 / v10\nwiaddr_q\n(C を別段へ)","#c0392b")
# v9: D(prefetch +LEAD/iaddr) precomputed at probe -> mark at the C/D boundary
cut(ax,gap(3),y,"v9\nstg_pf_*\n(D を probe へ)","#1e8449")

# legend / notes per version
notes=[
 ("v4  (189.0→207.0, +9.5%)",  "C: idx_of+pte_addr を発行サイクル→状態書込みサイクルへ。wiaddr_q に事前計算。発行は reg 読み+mux のみ","#c0392b"),
 ("v5  (207.0→241.5, +16.7%)", "A+B: CAM ルックアップ+start 判定を probe サイクルへ分離。stg_*_q に格納し commit で launch","#1f6fb2"),
 ("v9  (234.7→261.8, +11.5%)", "D: prefetch の pf_line(+LEAD 加算)+iaddr_of を probe で事前計算(stg_pf_*)。R チャネル登録+残 co-critical も同時","#1e8449"),
 ("v10 (261.8→287.4, +9.8%)",  "C(consume 側): 次状態と iaddr_of の融合を分離。consume=次状態のみ、専用 addr-gen 段が次サイクルに生成(wia_rdy_q)","#7d3c98"),
]
yy=2.2
for tt,dd,cc in notes:
    ax.text(0.5,yy,"■",color=cc,fontsize=11,va="center")
    ax.text(1.0,yy,tt,fontsize=9.5,fontweight="bold",va="center",color=cc)
    ax.text(5.0,yy,dd,fontsize=8.5,va="center",color="#222")
    yy-=0.55
ax.text(8.5,0.1,"hd: 160.8 → 287.4 MHz (+78.7%)。レイテンシは段ごとに +1cyc だが wire rate 不変"
        "（メモリ待ち+prefetch で隠蔽, cyc/trans 11.08→11.47）",ha="center",fontsize=9,color="#333")

p=os.path.join(FIGS,"pipeline_cuts.png"); fig.savefig(p,dpi=130,bbox_inches="tight"); plt.close(fig); print("wrote",p)
