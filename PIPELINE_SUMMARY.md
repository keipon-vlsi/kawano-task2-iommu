# cfg5 パイプライン化（レジスタ挿入）による Fmax 向上まとめ

cfg5（nested 2-stage IOMMU walker, PWC+IOTLB+coalesce+prefetch, 1 walker）の Fmax を、
**長い組合せコーンにレジスタを挿入して段分割**することで段階的に上げた記録。各段で「元の論理
（クリティカルパス）／どこにレジスタを挿入したか／Fmax 向上」を示す。数値は post-place+resize
（sky130_fd_sc_hd, ideal clock）の実測。詳細は `CFG5_FMAX_LOG.md` / `CFG5_FMAX_PPA_REPORT.md`、
切り分け図は `cache_study/figs/figures/pipeline_split.png`。

## 全体像

ベースライン v0 は **1 サイクルに融合した長大コーン**：
`rdata(メモリ返却) → キャッシュ lookup → 次状態/start 判定 → idx_of+pte_addr(アドレス生成)
→ 観測カウンタ → アービタ → araddr(発行)`
これが 1 本の reg→reg 経路に乗り **160.8 MHz**。ここに段階的にレジスタを挿入し、各段のコーンを
短くして **287.4 MHz（hd, +78.7%）** まで上げた（さらに高速ライブラリ hs + P&R で 395.3 MHz）。

| 版 | 挿入したレジスタ（切り口） | 前→後 [MHz] | Δ | 累積 Δ vs v0 | 採否 |
|---|---|---|---|---|---|
| v3 | 観測カウンタの increment enable をレジスタ化（retiming） | 160.8→189.0 | **+17.5%** | +17.5% | 採用 |
| v4 | 発行アドレス `wiaddr_q` を walker 状態書込み時に事前計算・レジスタ化 | 189.0→207.0 | **+9.5%** | +28.7% | 採用 |
| v5 | servicer を probe/commit に分割（`stg_*_q` 挿入） | 207.0→241.5 | **+16.7%** | +50.2% | 採用 |
| v9 | commit/consume/prefetch の 3 経路を**同時に**事前計算レジスタ化 | 234.7→261.8 | **+11.5%** | +62.8% | 採用 |
| v10 | consume の addr-gen を専用段に分離（`wia_rdy_q`） | 261.8→287.4 | **+9.8%** | +78.7% | 採用 |
| — | （v2/v7/v8 はパイプ化を試して **null/回帰** → 破棄。下記） | — | — | — | 破棄 |

> 注：v6（ライン枠 IOTLB）は datapath 構造変更、v11（カウンタ合成除外）は dead-logic 除去、
> v12–v14 はライブラリ/P&R で、いずれも「レジスタ挿入＝パイプライン化」ではないため本まとめでは別枠。

---

## 各段の詳細（元の論理 → 挿入位置 → 効果）

### v3 — 観測カウンタの脱結合（retiming）　160.8→189.0 MHz（+17.5%）
- **元の論理**：`walks_q <= walks_q + svc_launch + pf_launch`。32bit 統計カウンタの**桁上げ連鎖が、
  IOTLB ルックアップ由来の遅い `svc_launch` に gate**されていた。ネットリストで真の律速 FF を確認したら
  startpoint=`bvpn_q`、endpoint=**`walks_q[31]`** だった（＝統計カウンタが律速）。
- **挿入位置**：increment enable を 1 段レジスタ化（`walk_inc_q/pf_inc_q/resp_inc_q <= …`）。カウンタは
  登録版から加算。**IOTLB→カウンタ桁上げ**の経路を切断（統計は 1 サイクル遅れるだけ、最終値不変）。
- **効果**：+17.5%。観測専用ロジックがマスクしていた真の律速がここで初めて露出。

### v4 — 発行アドレスの事前計算（precompute）　189.0→207.0 MHz（+9.5%）
- **元の論理**：発行サイクルに `walker reg → idx_of(12-way mux) → pte_addr(連結) → araddr` の
  アドレス生成コーンが乗っていた。
- **挿入位置**：`idx_of+pte_addr` を**walker 状態を書く時（launch/consume）に計算**し `wiaddr_q` に
  レジスタ化。発行は **レジスタ読み出し + アービタ mux のみ**に（PIPELINE_DEPTH=2）。
- **効果**：+9.5%。アドレス生成コーンが発行→AR から消えた。
- **教訓**：「終点を FF にする」だけでは無意味で、**コーン自体を別サイクルに移して**初めて効く
  （v2 が外したのはこの移動をしていなかったため）。

### v5 — servicer の probe/commit パイプ化（stage 挿入）　207.0→241.5 MHz（+16.7%）
- **元の論理**：`bvpn_q → IOTLB/PWC の 16-way CAM 比較 → start_base/svc 判定 → walker 状態書込み`
  が 1 サイクルに融合（CAM 比較コーンが深い）。
- **挿入位置**：servicer を 2 段に分割。**probe**＝選択エントリの IOTLB/PWC ルックアップ結果を
  staging レジスタ `stg_*_q`（hit/data/start_pc/start_base/vpn/ctx/line）に格納。**commit**（翌サイクル）
  ＝staging から resolve または launch。**CAM 比較コーンを probe サイクルに分離**。
- **効果**：+16.7%。CAM ルックアップが発行/launch 経路から切れた。

### v9 — 3 co-critical 経路の同時事前計算　234.7→261.8 MHz（+11.5%）
- **元の論理**：v5/v6 後、**3 つの経路が ~230–235MHz で同着クリティカル**だった：
  (1) demand-commit `stg → start → wbase/wiaddr`、(2) consume `rdata → next_base → wbase`、
  (3) prefetch-launch `+LEAD 加算器 → iaddr_of → wiaddr`。
- **挿入位置**：3 つ全部を probe サイクルで事前計算してレジスタ化：(a) demand launch アドレス
  `stg_start_addr_q`、(b) `e_busy` を launch enable から除外（単一 walker で冗長）、(c) prefetch の
  `pf_line`(+LEAD 加算器)・launch アドレス・same-table を `stg_pf_*` に。R チャネル登録も同時。
- **効果**：+11.5%。**教訓：co-critical な経路群は「全部まとめて」レジスタ化しないと効かない**
  （v7=consume 単独で null、v8=2 経路で第 3 経路が露出し回帰、v9=3 経路同時で突破）。

### v10 — consume addr-gen の段分離　261.8→287.4 MHz（+9.8%, hd 最高）
- **元の論理**：v9 後の律速は consume の `rdata_q → gpn27/next-state → iaddr_of → wiaddr_q`
  （次状態計算とアドレス生成が同一サイクルに融合）。
- **挿入位置**：consume は walker 状態（wpc/wbase/wgpn/wdgvpn）だけ更新し `wia_rdy_q<=0`。**専用 addr-gen
  段**が次サイクルに registered walker 状態から `iaddr_of` を計算して `wiaddr_q` に格納・`wia_rdy_q<=1`。
  発行は `wia_rdy_q` を待つ。launch/prefetch は probe で事前計算済みなので初回発行は遅延なし。
- **効果**：+9.8%。次状態とアドレス生成を別サイクルに分離。

---

## 失敗したパイプ化（破棄）— なぜ効かなかったか

| 版 | 試したレジスタ挿入 | 結果 | 理由 |
|---|---|---|---|
| v2 | consume→発行を脱融合（PD=2、ただしアドレス生成は移さず） | null（158.2≈v0） | 切った境界が**真の律速を跨いでいなかった**。アドレス生成コーンは cycle B に残った |
| v7 | メモリ R チャネルを 1 段レジスタ化（consume を reg2reg 化） | null（229.9） | consume を直しても **commit 経路が同水準で待機**（co-critical）。片方では効かない |
| v8 | commit+consume を 2 経路同時短縮（R 登録 + launch 事前計算 + e_busy 除外） | 回帰（213.7） | **第 3 の経路（prefetch launch）が露出**して最長化。2 経路では足りず |

**共通教訓**：(1) **合成後ネットリストで真のクリティカル FF を確認**しないと、どこを切るべきか分からない
（v3 の観測カウンタが典型）。(2) **co-critical な経路は同時に全部下げる**必要がある（v7→v8→v9 の流れ）。
(3) レジスタ挿入は「終点を FF にする」のではなく「**深いコーンを別サイクルに移す（事前計算/段分割）**」。

---

## レイテンシ・スループットへの影響

パイプ段を増やすと**レイテンシは +1 サイクル/段**だが、cyc/translation は **11.08→11.47（+3.5%）**に
留まる（メモリ 100ns 待ちと prefetch 先行で隠蔽）。**スループット（wire rate）はほぼ不変**で、Fmax だけ
+78.7%。＝パイプ化は「per-op を速くする」のではなく「**長いコーンを刻んでクロックを上げる**」手段。

> 注：本ワークロードは memory-latency-bound で wire rate は周波数非依存（`CFG5_FMAX_PPA_REPORT.md §5`）。
> パイプ化は **400MHz クロック spec を固定要求された場合のタイミングクロージャ**のために有効で、
> throughput 向上のためではない。

## まとめ（パイプ化のみの寄与）

hd で **160.8 → 287.4 MHz（+78.7%）** を、5 つのレジスタ挿入（v3,v4,v5,v9,v10）で達成。
内訳：retiming（v3, カウンタ）+17.5% / precompute（v4, 発行アドレス）+9.5% / stage 挿入（v5, servicer）
+16.7% / 3 経路同時（v9）+11.5% / consume 段分離（v10）+9.8%。
最大の単発は **v3（観測カウンタ脱結合, +17.5%）**＝「機能ではない観測ロジックが律速をマスクしていた」。
