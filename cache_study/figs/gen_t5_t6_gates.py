#!/usr/bin/env python3
"""Gate-level block diagram of IOTLB T5 (priority encoder) vs T6 (one-hot AND-OR mux).
Both share 16x 27b comparators + DFF storage; only the match->SPA reduction differs.
  figures/t5_t6_gates.png
Run: .venv/bin/python3 cache_study/figs/gen_t5_t6_gates.py
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle

BASE = os.path.dirname(os.path.abspath(__file__))
FIGS = os.path.join(BASE, "figures"); os.makedirs(FIGS, exist_ok=True)

C_STORE="#cfe2f3"; C_CMP="#fff2cc"; C_AND="#d9ead3"; C_OR="#cce5ff"
C_PRIO="#f4cccc"; C_TREE="#d9ead3"; C_REG="#ead1dc"; C_NOTE="#f3f3f3"

def box(ax,x,y,w,h,t,fc,fs=8,bold=False,ec="black",lw=1.1):
    ax.add_patch(FancyBboxPatch((x-w/2,y-h/2),w,h,boxstyle="round,pad=0.02,rounding_size=0.05",
        fc=fc,ec=ec,lw=lw)); ax.text(x,y,t,ha="center",va="center",fontsize=fs,
        fontweight="bold" if bold else "normal",zorder=6)

def gate(ax,x,y,w,h,t,fc,fs=7.5,bold=False):  # plain rectangle = a logic gate
    ax.add_patch(Rectangle((x-w/2,y-h/2),w,h,fc=fc,ec="black",lw=1.0,zorder=5))
    ax.text(x,y,t,ha="center",va="center",fontsize=fs,fontweight="bold" if bold else "normal",zorder=6)

def ar(ax,x1,y1,x2,y2,lw=1.2,c="black",s="-|>"):
    ax.add_patch(FancyArrowPatch((x1,y1),(x2,y2),arrowstyle=s,mutation_scale=9,lw=lw,color=c,
        shrinkA=1,shrinkB=1,zorder=4))

fig=plt.figure(figsize=(16,11))
fig.suptitle("IOTLB T5 vs T6 — gate-level: same 16 comparators, DIFFERENT match→SPA reduction",
             fontsize=14,fontweight="bold")

# ---------------- shared front-end (drawn once, top strip) ----------------
axc=fig.add_axes([0.04,0.78,0.92,0.16]); axc.set_xlim(0,16); axc.set_ylim(0,4); axc.axis("off")
axc.text(8,3.7,"SHARED FRONT-END (identical in T5 & T6): per-entry 27-bit tag comparator → match[i]",
         ha="center",fontsize=10,fontweight="bold")
# one comparator expanded
box(axc,1.6,2.0,2.2,1.0,"entry i (DFF)\nvalid, tag_q[i]27, spa_q[i]44",C_STORE,7.5)
gate(axc,4.3,2.4,1.5,0.7,"XNOR ×27\n(tag==lk_tag)",C_CMP,7)
gate(axc,6.2,2.4,1.5,0.7,"AND-tree\n(all 27 eq)",C_AND,7)
gate(axc,8.0,2.0,1.3,0.7,"AND valid",C_AND,7)
ar(axc,2.7,2.2,3.55,2.4); ar(axc,5.05,2.4,5.45,2.4); ar(axc,6.95,2.4,7.35,2.1)
ar(axc,8.65,2.0,9.4,2.0); axc.text(9.9,2.0,"match[i]",fontsize=8,fontweight="bold")
axc.text(12.6,2.4,"×16 entries  →  match[0..15]  (16-bit vector)",fontsize=9.5,fontweight="bold")
axc.text(12.6,1.5,"hit = |match  (balanced OR tree, both)",fontsize=8.5,color="#333")
axc.add_patch(Rectangle((0.3,0.5),15.4,3.4,fill=False,ls="--",ec="#888"))

# ---------------- T5 (left) : priority encoder + 16:1 mux ----------------
ax=fig.add_axes([0.03,0.06,0.45,0.68]); ax.set_xlim(0,10); ax.set_ylim(0,12); ax.axis("off")
ax.text(5,11.4,"T5  —  PRIORITY encoder (lowest index wins)",ha="center",fontsize=12,fontweight="bold",color="#a00")
ax.text(5,10.8,"246 MHz · depth 10 · area 53,466 µm²",ha="center",fontsize=9,style="italic")
# match bits down the left
ys=[9.6,8.6,7.6,6.0,5.0]; labs=["match[0]","match[1]","match[2]","…","match[15]"]
for y,l in zip(ys,labs): ax.text(0.9,y,l,fontsize=8,ha="center")
# serial priority chain: g[i]=match[i] & ~(match[0]|..|match[i-1])
ax.text(5,9.9,"win[i] = match[i] & ¬(match[0]…match[i-1])",ha="center",fontsize=8,color="#a00")
px=3.2
for i,(y,c) in enumerate(zip(ys[:3],["win0","win1","win2"])):
    gate(ax,px,y,1.3,0.6,c+"\n(prio)",C_PRIO,7)
    ar(ax,1.6,y,px-0.65,y)
# serial dependency arrows (the killer): each stage feeds the next
ar(ax,px,9.3,px,8.9,c="#a00",lw=1.6); ar(ax,px,8.3,px,7.9,c="#a00",lw=1.6)
ax.text(px+1.2,8.6,"SERIAL\nripple\n(¬OR of\nall prior)",fontsize=7,color="#a00")
gate(ax,px,5.0,1.3,0.6,"win15\n(prio)",C_PRIO,7); ar(ax,1.6,5.0,px-0.65,5.0)
ax.add_patch(FancyArrowPatch((px,7.3),(px,5.6),arrowstyle="-|>",mutation_scale=10,lw=1.6,
    color="#a00",ls="dashed",zorder=4))
# 16:1 mux selected by one-hot win[]
box(ax,6.6,7.2,2.2,3.0,"16:1 MUX\n(select = win[0..15])\nspa_q[winner]",C_PRIO,8.5,bold=True)
for y in ys: ar(ax,px+0.65,y,5.5,7.2,c="#999",lw=0.8)
ax.text(2.0,3.4,"spa_q[0..15] (data)",fontsize=7,color="#666")
ar(ax,2.0,3.7,5.5,6.4,c="#999",lw=0.8)
box(ax,9.0,7.2,1.2,0.8,"lk_spa",C_REG,8)
ar(ax,7.7,7.2,8.4,7.2)
ax.text(5,2.4,"critical path: clkinvlp→a221oi→nand4→or3→o21ai→o211ai\n→a311o→a2111oi→o21ai→a31oi  (10 levels)",
        ha="center",fontsize=7.5,color="#a00")
ax.text(5,1.0,"The priority logic is a SERIAL chain over 16 entries\n(each needs ¬OR of all lower indices) → deep.",
        ha="center",fontsize=8.5,color="#a00",fontweight="bold")

# ---------------- T6 (right) : one-hot AND-OR balanced tree ----------------
ax=fig.add_axes([0.52,0.06,0.45,0.68]); ax.set_xlim(0,10); ax.set_ylim(0,12); ax.axis("off")
ax.text(5,11.4,"T6  —  one-hot AND-OR mux (no priority)",ha="center",fontsize=12,fontweight="bold",color="#070")
ax.text(5,10.8,"368 MHz · depth 7 · area 52,874 µm²  (+50% Fmax)",ha="center",fontsize=9,style="italic")
ax.text(5,10.1,"assume match is ONE-HOT (a VPN is cached once)",ha="center",fontsize=8.5,color="#070")
# per-entry AND: match[i] gates spa[i] (44b)
ax.text(0.8,9.7,"match[i]",fontsize=7.5,ha="center"); ax.text(0.8,9.25,"spa_q[i]",fontsize=7,ha="center",color="#666")
ax.text(2.3,10.3,"16× AND  (match[i] ? spa[i] : 0)  — all parallel, no inter-entry dependency",
        ha="left",fontsize=7.5,color="#070")
gys=[9.4,8.5,7.6,6.3,5.4]; glab=["m0&spa0","m1&spa1","m2&spa2","…","m15&spa15"]
for y,l in zip(gys,glab):
    gate(ax,2.4,y,1.4,0.55,l,C_AND,7)
    ar(ax,1.4,y,1.75,y)
# balanced OR tree: 16 -> 8 -> 4 -> 2 -> 1  (4 levels)
def orcol(x,n,y0,y1):
    ys=[y0+(y1-y0)*k/(n-1) for k in range(n)] if n>1 else [(y0+y1)/2]
    for y in ys: gate(ax,x,y,0.85,0.45,"OR",C_OR,7)
    return ys
l1=orcol(4.5,4,5.6,9.2);  ax.text(4.5,9.7,"OR ×8",fontsize=7,ha="center",color="#070")
l2=orcol(6.0,3,6.2,8.6);  ax.text(6.0,9.1,"OR ×4",fontsize=7,ha="center",color="#070")
l3=orcol(7.3,2,6.7,8.0);  ax.text(7.3,8.4,"OR ×2",fontsize=7,ha="center",color="#070")
gate(ax,8.5,7.3,0.85,0.45,"OR",C_OR,7); ax.text(8.5,7.8,"OR×1",fontsize=7,ha="center",color="#070")
for y in gys: ar(ax,3.1,y,4.05,7.3,c="#bbb",lw=0.6)
for y in l1: ar(ax,4.95,y,5.55,7.4,c="#070",lw=0.8)
for y in l2: ar(ax,6.45,y,6.85,7.4,c="#070",lw=0.8)
for y in l3: ar(ax,7.75,y,8.05,7.3,c="#070",lw=0.8)
box(ax,9.5,7.3,1.0,0.7,"lk_spa",C_REG,7.5); ar(ax,8.95,7.3,9.0,7.3)
ax.text(5,3.7,"balanced OR tree: 16→8→4→2→1 = log2(16)=4 levels\n(NO serial ¬OR-of-prior chain)",
        ha="center",fontsize=8,color="#070")
ax.text(5,2.2,"critical path: clkinvlp→a2111oi→nand4→nor4\n→a22o→a221oi→nand4  (7 levels)",
        ha="center",fontsize=7.5,color="#070")
ax.text(5,1.0,"one-hot ⇒ at most one AND passes its SPA ⇒ pure OR.\nNo priority needed → shallow balanced tree → faster.",
        ha="center",fontsize=8.5,color="#070",fontweight="bold")

p=os.path.join(FIGS,"t5_t6_gates.png")
fig.savefig(p,dpi=130,bbox_inches="tight"); plt.close(fig); print("wrote",p)
