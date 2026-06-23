#!/usr/bin/env python3
"""Gate-level block diagram of T5's actual synthesized circuit (16-way FA CAM, priority).
From the netlist: 16 comparators -> match[16]; a 44-bit-wide 16:1 reduction MUX tree
(mux2 cells, data plane); selects = group-OR signals (the critical-path control cone).
  figures/t5_gatelevel.png
Run: .venv/bin/python3 cache_study/figs/gen_t5_gatelevel.py
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams['font.family']=['Noto Sans CJK JP','DejaVu Sans']
plt.rcParams['axes.unicode_minus']=False
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Polygon

BASE = os.path.dirname(os.path.abspath(__file__))
FIGS = os.path.join(BASE, "figures"); os.makedirs(FIGS, exist_ok=True)
C_STORE="#cfe2f3"; C_CMP="#fff2cc"; C_MUX="#d9ead3"; C_OR="#cce5ff"; C_REG="#ead1dc"; C_CRIT="#f4cccc"

def box(ax,x,y,w,h,t,fc,fs=8,bold=False,ec="black",lw=1.0):
    ax.add_patch(FancyBboxPatch((x-w/2,y-h/2),w,h,boxstyle="round,pad=0.02,rounding_size=0.04",
        fc=fc,ec=ec,lw=lw)); ax.text(x,y,t,ha="center",va="center",fontsize=fs,
        fontweight="bold" if bold else "normal",zorder=6)

def mux(ax,x,y,w=0.5,h=0.7,fc=C_MUX,sel=""):
    # trapezoid = 2:1 mux (wide top = 2 inputs, narrow bottom = 1 output)
    ax.add_patch(Polygon([(x-w/2,y+h/2),(x+w/2,y+h/2),(x+w/2-0.12,y-h/2),(x-w/2+0.12,y-h/2)],
        closed=True,fc=fc,ec="black",lw=1.0,zorder=5))
    if sel: ax.text(x-w/2-0.04,y,sel,ha="right",va="center",fontsize=6.5,color="#c00",zorder=7)

def ar(ax,x1,y1,x2,y2,lw=1.0,c="black",s="-|>"):
    ax.add_patch(FancyArrowPatch((x1,y1),(x2,y2),arrowstyle=s,mutation_scale=8,lw=lw,color=c,
        shrinkA=1,shrinkB=1,zorder=4))

fig=plt.figure(figsize=(17,11))
fig.suptitle("T5  16-way FA CAM (priority) — gate-level: comparators → match → 16:1 reduction MUX tree "
             "(selects = group-OR = critical path)",fontsize=13,fontweight="bold")

# ============ panel 1: comparator front-end (one expanded) ============
axc=fig.add_axes([0.04,0.80,0.92,0.15]); axc.set_xlim(0,17); axc.set_ylim(0,4); axc.axis("off")
axc.text(8.5,3.6,"① 比較器フロントエンド（16 個並列）: 各 entry の match[i] を生成",ha="center",fontsize=10,fontweight="bold")
box(axc,1.8,2.0,2.2,1.0,"entry i (DFF)\nvalid, tag27, spa44",C_STORE,7.5)
box(axc,4.4,2.3,1.5,0.7,"XNOR ×27\ntag==lk_tag",C_CMP,7)
box(axc,6.3,2.3,1.4,0.7,"AND-tree\n(全27bit一致)",C_MUX,7)
box(axc,8.1,2.0,1.2,0.7,"& valid",C_MUX,7)
ar(axc,2.9,2.1,3.65,2.3); ar(axc,5.15,2.3,5.6,2.3); ar(axc,7.0,2.3,7.5,2.1)
ar(axc,8.7,2.0,9.5,2.0); axc.text(9.9,2.0,"match[i]",fontsize=8,fontweight="bold",va="center")
axc.text(13.2,2.2,"×16 →  match[0..15]  (16bit)",fontsize=9.5,fontweight="bold")
axc.text(13.2,1.4,"spa_q[0..15] (44bit×16) は\nMUX 木のデータ入力へ",fontsize=8,color="#333")

# ============ panel 2: reduction MUX tree (data plane) ============
ax=fig.add_axes([0.03,0.06,0.66,0.70]); ax.set_xlim(0,17); ax.set_ylim(0,11); ax.axis("off")
ax.text(8.5,10.6,"② データ面：44bit 幅 16:1 reduction MUX 木（mux2 ~1000 個）",ha="center",fontsize=11,fontweight="bold")
ax.text(8.5,10.1,"各 mux: sel=1 で左(高優先)側、sel=0 で右側。葉=生 match、内部=左サブツリーの group-OR",
        ha="center",fontsize=8,style="italic",color="#444")

# 16 data inputs (spa) at top
xs16=[0.6+i*1.06 for i in range(16)]
for i,x in enumerate(xs16):
    box(ax,x,9.4,0.9,0.5,f"spa{i}",C_STORE,6.5)
# level1: 8 muxes, sel = m0,m2,...,m14 (raw match of lower index)
y1=8.0; x1=[(xs16[2*i]+xs16[2*i+1])/2 for i in range(8)]
for i,x in enumerate(x1):
    mux(ax,x,y1,sel=f"m{2*i}")
    ar(ax,xs16[2*i],9.15,x-0.12,y1+0.35,lw=0.8); ar(ax,xs16[2*i+1],9.15,x+0.12,y1+0.35,lw=0.8)
ax.text(16.7,y1,"L1: sel=生 match\n(m0,m2,…,m14)",fontsize=7,color="#c00",va="center")
# level2: 4 muxes, sel=(m0|m1),(m4|m5),(m8|m9),(m12|m13)
y2=6.2; x2=[(x1[2*i]+x1[2*i+1])/2 for i in range(4)]; s2=["m0|m1","m4|m5","m8|m9","m12|m13"]
for i,x in enumerate(x2):
    mux(ax,x,y2,w=0.6,sel=s2[i])
    ar(ax,x1[2*i],y1-0.35,x-0.14,y2+0.35,lw=0.8); ar(ax,x1[2*i+1],y1-0.35,x+0.14,y2+0.35,lw=0.8)
ax.text(16.7,y2,"L2: sel=2エントリOR",fontsize=7,color="#c00",va="center")
# level3: 2 muxes, sel=(m0..3),(m8..11)
y3=4.4; x3=[(x2[0]+x2[1])/2,(x2[2]+x2[3])/2]; s3=["m0..3","m8..11"]
for i,x in enumerate(x3):
    mux(ax,x,y3,w=0.7,sel=s3[i])
    ar(ax,x2[2*i],y2-0.35,x-0.16,y3+0.35,lw=0.9); ar(ax,x2[2*i+1],y2-0.35,x+0.16,y3+0.35,lw=0.9)
ax.text(16.7,y3,"L3: sel=4エントリOR",fontsize=7,color="#c00",va="center")
# level4 root: sel=(m0..7)
y4=2.6; x4=(x3[0]+x3[1])/2
mux(ax,x4,y4,w=0.9,h=0.8,sel="m0..7",fc="#b6d7a8")
ar(ax,x3[0],y3-0.35,x4-0.2,y4+0.4,lw=1.0); ar(ax,x3[1],y3-0.35,x4+0.2,y4+0.4,lw=1.0)
ax.text(16.7,y4,"L4(root): sel=8エントリOR",fontsize=7,color="#c00",va="center")
box(ax,x4,1.2,1.2,0.5,"lk_spa",C_REG,8); ar(ax,x4,y4-0.4,x4,1.45,lw=1.2)
ax.text(8.5,0.4,"MUX 木は 16→8→4→2→1 の 4 段で浅い（mux2 はクリティカルパス外）",ha="center",fontsize=8.5,color="#070")

# ============ panel 3: select generation (control plane = CRITICAL) ============
ax=fig.add_axes([0.70,0.06,0.28,0.70]); ax.set_xlim(0,10); ax.set_ylim(0,11); ax.axis("off")
ax.text(5,10.6,"③ セレクタ生成 = group-OR 網",ha="center",fontsize=10.5,fontweight="bold",color="#a00")
ax.text(5,10.1,"（= クリティカルパス, ~10段の AOI）",ha="center",fontsize=8,style="italic",color="#a00")
box(ax,5,9.1,4.2,0.7,"match[0..15]",C_CMP,8,bold=True)
# group-OR ladder
items=[("m0  (L1 sel, 生)",7.9,C_OR),
       ("m0|m1  (L2 sel)",6.9,C_OR),
       ("m0|m1|m2|m3  (L3 sel)",5.9,C_OR),
       ("m0|…|m7  (L4 sel)",4.9,C_CRIT)]
prev=8.75
for t,y,c in items:
    box(ax,5,y,5.6,0.6,t,c,7.5)
    ar(ax,5,prev-0.0,5,y+0.3,lw=1.2,c="#a00"); prev=y
ax.text(5,4.0,"各段は「下位までの OR」を\n累積（prefix-OR 構造）",ha="center",fontsize=7.5,color="#a00")
box(ax,5,3.0,5.4,0.9,"hit = |match[0..15]\n(平衡 OR ツリー)",C_OR,8)
ar(ax,5,8.75,5,3.45,lw=0.6,c="#999",s="-")
ax.text(5,1.7,"この group-OR(prefix) の生成が深い\n→ T5 の段数10/246MHz の正体。\nT6 は one-hot 前提でこれが丸ごと不要\n→ 段数7/368MHz（+50%）",
        ha="center",fontsize=7.8,color="#a00")

p=os.path.join(FIGS,"t5_gatelevel.png")
fig.savefig(p,dpi=130,bbox_inches="tight"); plt.close(fig); print("wrote",p)
