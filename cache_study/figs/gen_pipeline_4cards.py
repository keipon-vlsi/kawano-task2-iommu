#!/usr/bin/env python3
"""One-glance figure of the 4 pipeline changes (v4/v5/v9/v10): top = the original fused
1-cycle cone with the 4 cut points; below = one card per version (what was inserted,
before->after one-liner, Fmax + area delta).
  figures/pipeline_4cards.png
Run: .venv/bin/python3 cache_study/figs/gen_pipeline_4cards.py
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams['font.family']=['Noto Sans CJK JP','DejaVu Sans']; plt.rcParams['axes.unicode_minus']=False
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

BASE=os.path.dirname(os.path.abspath(__file__)); FIGS=os.path.join(BASE,"figures"); os.makedirs(FIGS,exist_ok=True)
C_REG="#ead1dc"; C_A="#fff2cc"; C_B="#fce5cd"; C_C="#f4cccc"; C_D="#d9ead3"
COL={"v4":"#c0392b","v5":"#1f6fb2","v9":"#1e8449","v10":"#7d3c98"}

def rbox(ax,x,y,w,h,t,fc,fs=9,bold=False,ec="black",lw=1.2):
    ax.add_patch(FancyBboxPatch((x-w/2,y-h/2),w,h,boxstyle="round,pad=0.02,rounding_size=0.04",
        fc=fc,ec=ec,lw=lw)); ax.text(x,y,t,ha="center",va="center",fontsize=fs,
        fontweight="bold" if bold else "normal",zorder=6)
def ar(ax,x1,y1,x2,y2,lw=1.5,c="black"):
    ax.add_patch(FancyArrowPatch((x1,y1),(x2,y2),arrowstyle="-|>",mutation_scale=11,lw=lw,color=c,zorder=4))

fig=plt.figure(figsize=(17,10.5))
fig.suptitle("cfg5 パイプライン化：4 つの変更（v4 / v5 / v9 / v10）一覧",fontsize=15,fontweight="bold")

# ===== top: the fused cone with cut marks =====
ax=fig.add_axes([0.03,0.66,0.94,0.24]); ax.set_xlim(0,17); ax.set_ylim(0,4); ax.axis("off")
ax.text(8.5,3.7,"元の v0：1 サイクルに融合した長いコーン（160.8 MHz）。↓ 各版がレジスタを挿して区間を切る",
        ha="center",fontsize=10,fontweight="bold")
y=1.7
stages=[("[reg]\nrdata /\nbvpn_q",C_REG,1.5),("A: cache\nlookup\n(CAM 比較)",C_A,1.8),
        ("B: 次状態 /\nstart 判定",C_B,1.8),("C: idx_of+\npte_addr\n(アドレス生成)",C_C,1.9),
        ("D: prefetch\n+LEAD / iaddr",C_D,1.8),("[reg]\nwalker /\naraddr(発行)",C_REG,1.6)]
xs=[]; x=1.0
for t,c,w in stages:
    rbox(ax,x+w/2,y,w,1.5,t,c,8.5); xs.append((x,x+w)); x+=w+0.55
for i in range(len(stages)-1): ar(ax,xs[i][1],y,xs[i+1][0],y)
def gap(i): return (xs[i][1]+xs[i+1][0])/2
def cut(xx,lab,col):
    ax.plot([xx,xx],[y-0.95,y+0.95],color=col,lw=2.6,zorder=7)
    ax.text(xx,y+1.15,lab,ha="center",fontsize=8.5,color=col,fontweight="bold")
cut(gap(1),"v5",COL["v5"]); cut(gap(2),"v4 / v10",COL["v4"]); cut(gap(3),"v9",COL["v9"])

# ===== 4 cards =====
cards=[
 ("v4","発行アドレスの事前計算","#c0392b",
  "発行の瞬間に idx_of+pte_addr を計算していた","起動/状態書込み時に wiaddr_q へ事前計算→発行は reg 読みだけ",
  "挿入: wiaddr_q (40b)+burst","189→207 MHz (+9.5%)","面積 +1.0%"),
 ("v5","検索を probe/commit に分離","#1f6fb2",
  "CAM 検索→start 判定→起動 を 1 サイクル","probe(検索を stg_*_q にラッチ) / commit(起動) の 2 段",
  "挿入: stg_*_q (178b)","207→241.5 MHz (+16.7%)","面積 +3.7%"),
 ("v9","先読みアドレスも probe で前倒し","#1e8449",
  "prefetch が起動時に +LEAD 加算 + iaddr_of","stg_pf_* に probe で事前計算+R 登録（3 経路同時）",
  "挿入: stg_pf_*/R 登録 (231b)","234.7→261.8 MHz (+11.5%)","面積 +6.1%"),
 ("v10","consume のアドレス生成を専用段へ","#7d3c98",
  "返却 PTE→次状態→iaddr_of を 1 サイクル","consume=次状態のみ / 専用 addr-gen 段で生成 (wia_rdy_q)",
  "挿入: wia_rdy_q (1b)","261.8→287.4 MHz (+9.8%)","面積 −1.9%（論理統合で減）"),
]
W=0.235
for k,(ver,title,col,bef,aft,ins,fm,ar_) in enumerate(cards):
    x0=0.03+k*(W+0.0066); axc=fig.add_axes([x0,0.06,W,0.55]); axc.set_xlim(0,10); axc.set_ylim(0,10); axc.axis("off")
    axc.add_patch(FancyBboxPatch((0.2,0.2),9.6,9.6,boxstyle="round,pad=0.02,rounding_size=0.2",
        fc="white",ec=col,lw=2.4))
    axc.add_patch(FancyBboxPatch((0.2,8.5),9.6,1.3,boxstyle="round,pad=0.02,rounding_size=0.2",fc=col,ec=col))
    axc.text(5,9.15,f"{ver}  {title}",ha="center",va="center",fontsize=10.5,fontweight="bold",color="white")
    axc.text(5,7.7,"【元】",ha="center",fontsize=8.5,color="#a00",fontweight="bold")
    axc.text(5,6.9,bef,ha="center",va="center",fontsize=8.2,wrap=True)
    ar(axc,5,6.2,5,5.7,lw=1.6,c=col)
    axc.text(5,5.2,"【後】",ha="center",fontsize=8.5,color="#070",fontweight="bold")
    axc.text(5,4.2,aft,ha="center",va="center",fontsize=8.2,wrap=True)
    axc.add_patch(FancyBboxPatch((0.7,2.4),8.6,1.0,boxstyle="round,pad=0.02,rounding_size=0.1",fc="#f3f3f3",ec="#aaa"))
    axc.text(5,2.9,ins,ha="center",va="center",fontsize=8,color="#333")
    axc.text(5,1.6,fm,ha="center",va="center",fontsize=9.5,fontweight="bold",color=col)
    axc.text(5,0.8,ar_,ha="center",va="center",fontsize=8.5,color="#555")

fig.text(0.5,0.015,"合計: 160.8 → 287.4 MHz (+78.7%, sky130 hd)。レイテンシは段ごと +1cyc だが throughput 不変"
         "（メモリ待ち+先読みで隠蔽）",ha="center",fontsize=9.5,color="#333")
p=os.path.join(FIGS,"pipeline_4cards.png"); fig.savefig(p,dpi=130,bbox_inches="tight"); plt.close(fig); print("wrote",p)
