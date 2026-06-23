#!/usr/bin/env python3
"""Gate-level block diagram of T0 (line-organized IOTLB, current design: 2 lines x 8).
From the RTL: VPN -> {line_tag VPN[26:3] 24b, offset VPN[2:0] 3b}; 2 line-tag compares
(24b) + per-line offset-indexed 8:1 SPA mux (NO compare on offset) + final 2:1 line
select (no priority, 2 ways) + hit = m0|m1.
  figures/t0_gatelevel.png
Run: .venv/bin/python3 cache_study/figs/gen_t0_gatelevel.py
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams['font.family'] = ['Noto Sans CJK JP', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Polygon

BASE = os.path.dirname(os.path.abspath(__file__))
FIGS = os.path.join(BASE, "figures"); os.makedirs(FIGS, exist_ok=True)
C_STORE="#cfe2f3"; C_CMP="#fff2cc"; C_MUX="#d9ead3"; C_OR="#cce5ff"; C_REG="#ead1dc"; C_IDX="#fce5cd"

def box(ax,x,y,w,h,t,fc,fs=8,bold=False,ec="black",lw=1.0):
    ax.add_patch(FancyBboxPatch((x-w/2,y-h/2),w,h,boxstyle="round,pad=0.02,rounding_size=0.05",
        fc=fc,ec=ec,lw=lw)); ax.text(x,y,t,ha="center",va="center",fontsize=fs,
        fontweight="bold" if bold else "normal",zorder=6)

def mux(ax,x,y,w,h,fc=C_MUX,sel="",t=""):
    ax.add_patch(Polygon([(x-w/2,y+h/2),(x+w/2,y+h/2),(x+w/2-0.10,y-h/2),(x-w/2+0.10,y-h/2)],
        closed=True,fc=fc,ec="black",lw=1.1,zorder=5))
    if t: ax.text(x,y,t,ha="center",va="center",fontsize=7.5,zorder=7)
    if sel: ax.text(x,y-h/2-0.18,sel,ha="center",va="top",fontsize=7.5,color="#c00",zorder=7,fontweight="bold")

def ar(ax,x1,y1,x2,y2,lw=1.2,c="black",s="-|>"):
    ax.add_patch(FancyArrowPatch((x1,y1),(x2,y2),arrowstyle=s,mutation_scale=9,lw=lw,color=c,
        shrinkA=1,shrinkB=1,zorder=4))

fig=plt.figure(figsize=(15,9))
ax=fig.add_axes([0.02,0.02,0.96,0.90]); ax.set_xlim(0,16); ax.set_ylim(0,11); ax.axis("off")
fig.suptitle("IOTLB T0  ライン構成（現行, 2 ライン×8）— ゲートレベル：ライン tag 2 比較 + offset 8:1 mux + 2:1 ライン選択（優先なし）",
             fontsize=12.5, fontweight="bold")

# ---- VPN split ----
box(ax,1.3,9.5,2.0,0.7,"VPN (27b)",C_REG,8,bold=True)
box(ax,1.3,8.3,2.4,0.7,"line_tag = VPN[26:3]\n(24b)",C_IDX,7.5)
box(ax,1.3,7.1,2.4,0.7,"offset = VPN[2:0]\n(3b)",C_IDX,7.5)
ar(ax,1.3,9.15,1.3,8.66); ar(ax,1.3,7.96,1.3,7.46)

# ---- line 0 (top) ----
def line(yc, k, mname):
    box(ax,4.2,yc+0.9,3.0,0.8,f"line{k} (DFF)\nltag24, valid, subv8, 8×spa44",C_STORE,7)
    # tag compare
    box(ax,7.3,yc+1.3,1.9,0.7,"==(24b)\nXNOR+AND",C_CMP,7)
    # & valid & subv[off]
    box(ax,9.6,yc+1.3,1.7,0.7,f"& valid{k}\n& subv{k}[off]",C_MUX,6.8)
    ax.text(11.0,yc+1.3,f"{mname}",fontsize=8.5,color="#c00",fontweight="bold",va="center")
    # offset 8:1 mux (data)
    mux(ax,7.3,yc-0.2,1.9,1.0,fc=C_MUX,sel="sel=offset (無比較)",t=f"8:1 mux\ndata{k}[off]\n(44b)")
    ar(ax,5.7,yc+1.0,6.35,yc+1.3)          # line tag -> compare
    ar(ax,2.5,yc+1.3-0.0,6.35,yc+1.35)     # (decorative) line_tag feed handled below
    ar(ax,8.25,yc+1.3,8.75,yc+1.3)         # compare -> & valid
    ar(ax,5.7,yc+0.8,6.4,yc+0.1)           # line data -> 8:1 mux
    return yc

# place two lines
y0=7.4; y1=3.2
# line0
box(ax,4.2,y0+0.5,3.0,0.9,"line0 (DFF)\nltag0(24), valid0, subv0(8), 8×spa44",C_STORE,7)
box(ax,7.6,y0+0.9,1.9,0.7,"==(24b)\nXNOR+AND",C_CMP,7)
box(ax,9.9,y0+0.9,1.8,0.7,"& valid0\n& subv0[off]",C_MUX,6.8)
ax.text(11.6,y0+0.9,"m0",fontsize=10,color="#c00",fontweight="bold",va="center")
mux(ax,7.6,y0-0.7,2.0,1.1,fc=C_MUX,sel="sel = offset（比較なし）",t="8:1 mux\ndata0[off] 44b")
ar(ax,5.7,y0+0.7,6.6,y0+0.9)             # ltag0 -> compare
ar(ax,8.55,y0+0.9,9.0,y0+0.9)           # compare -> m0
ar(ax,5.7,y0+0.3,6.6,y0-0.6)            # line0 data -> 8:1

# line1
box(ax,4.2,y1+0.5,3.0,0.9,"line1 (DFF)\nltag1(24), valid1, subv1(8), 8×spa44",C_STORE,7)
box(ax,7.6,y1+0.9,1.9,0.7,"==(24b)\nXNOR+AND",C_CMP,7)
box(ax,9.9,y1+0.9,1.8,0.7,"& valid1\n& subv1[off]",C_MUX,6.8)
ax.text(11.6,y1+0.9,"m1",fontsize=10,color="#c00",fontweight="bold",va="center")
mux(ax,7.6,y1-0.7,2.0,1.1,fc=C_MUX,sel="sel = offset（比較なし）",t="8:1 mux\ndata1[off] 44b")
ar(ax,5.7,y1+0.7,6.6,y1+0.9)
ar(ax,8.55,y1+0.9,9.0,y1+0.9)
ar(ax,5.7,y1+0.3,6.6,y1-0.6)

# line_tag broadcast to both compares
ar(ax,2.5,8.3,6.6,y0+0.95,lw=1.0,c="#777")
ar(ax,2.5,8.3,6.6,y1+0.95,lw=1.0,c="#777")
ax.text(3.3,6.1,"line_tag を両ラインの\n比較器へ",fontsize=7,color="#777")
# offset broadcast to both 8:1 muxes
ar(ax,2.5,7.1,6.55,y0-0.7,lw=1.0,c="#070")
ar(ax,2.5,7.1,6.55,y1-0.7,lw=1.0,c="#070")
ax.text(3.2,4.6,"offset を両 8:1 mux の\nindex へ（比較なし）",fontsize=7,color="#070")

# ---- hit = m0 | m1 ----
box(ax,13.3,6.5,1.4,0.7,"OR\nhit=m0|m1",C_OR,7.5)
ar(ax,11.9,y0+0.9,12.7,6.7); ar(ax,11.9,y1+0.9,12.7,6.4)
ar(ax,14.0,6.5,15.0,6.5); ax.text(15.1,6.5,"lk_hit",fontsize=8.5,va="center")

# ---- final 2:1 line select (sel = m0) ----
mux(ax,12.9,3.4,2.2,1.3,fc="#b6d7a8",sel="sel = m0",t="2:1 mux\nライン選択\n(優先なし, 2way)")
ar(ax,8.6,y0-0.7,11.85,3.7)   # data0[off] -> final mux (top input)
ar(ax,8.6,y1-0.7,11.85,3.1)   # data1[off] -> final mux (bottom input)
ar(ax,11.9,y0+0.9,12.9,2.78,lw=1.0,c="#c00")  # m0 -> sel
ar(ax,12.9,2.75,12.9,2.0); box(ax,12.9,1.6,1.4,0.6,"lk_spa",C_REG,8);
ax.text(8.7,4.6,"data0[off]",fontsize=7,color="#333"); ax.text(8.7,1.7,"data1[off]",fontsize=7,color="#333")

# notes
ax.text(8,0.5,"クリティカルパス（7段, 344MHz）= line_tag → 24b 比較 → m0 → 最終 2:1 mux。"
        "  offset の 8:1 mux は比較と並列でパス外。",ha="center",fontsize=8.5,color="#a00")
ax.text(8,10.4,"効きどころ：①比較は line_tag(24b)×2 だけ（T5 は 27b×16）  ②offset は無比較 index で 8 ページ選択  "
        "③優先論理なし＝2way の単純 2:1（T5 は 16:1 優先木）",ha="center",fontsize=8.5,color="#070")

p=os.path.join(FIGS,"t0_gatelevel.png")
fig.savefig(p,dpi=130,bbox_inches="tight"); plt.close(fig); print("wrote",p)
