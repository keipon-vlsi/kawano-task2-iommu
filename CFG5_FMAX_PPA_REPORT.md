# cfg5 IOMMU コア Fmax 最適化 PPA レポート (v0 → v14)

対象: `cfg5_notag`（nested 2-stage IOMMU 翻訳コア: PWC + IOTLB + coalesce + prefetch、
1 walker、context タグ無し）。RTL（`rtl/iommu_top.sv` ほか）のクリティカルパス特定→解消を
v0→v13 で反復し、v14 で P&R 制約を最適化した記録。

## 計測条件（重要）

- **WNS / TNS は clock target = 2.5 ns（= 400 MHz spec）に対する値**。したがって WNS は
  「400 MHz closure までの不足量」、TNS は「全違反パスの合計」を直接表す。
- **post-opt = OpenROAD で floorplan + global_placement + repair_design + repair_timing -setup
  + detailed_place 後の STA**。**ideal clock（CTS なし）・route なし**。`syn/fmax_opt/opt.tcl`。
- Fmax(post-opt) = 1000 / (2.5 − WNS) [MHz]。energy/trans = power@400 × (cyc/trans) / 400 [nJ]
  （周波数非依存の iso-work 指標）。power は較正 switching activity 0.053・@400MHz。
- **ライブラリ**: v0–v11 = `sky130_fd_sc_hd`（high-density）、v12–v14 = `sky130_fd_sc_hs`
  （high-speed）。tt 025C 1v80。
- 全数値は post-synthesis / post-layout の **実ログから抽出**（`results/fmax_opt/postopt_*.log`,
  `cfg5_notag/results_hs/postopt*.log`、git 各版コミットに保存）。推測ではない。

---

## 1. PPA トレンドサマリ表（v0 baseline → v14）

WNS/TNS/area は post-opt STA（@2.5ns）。Fmax は post-opt。技法は当該版の主 RTL/フロー変更。

| 版 | lib | WNS [ns] | TNS [ns] | Fmax [MHz] | Δ Fmax vs v0 | area [µm²] | power@400 [mW] | energy/trans [nJ] | 主たる技法 | 採否 |
|---|---|---|---|---|---|---|---|---|---|---|
| **v0** | hd | −3.72 | −3422.94 | 160.8 | — | 110,465 | 37.9 | 1.05 | baseline（flat 12-state pc FSM） | 基準 |
| v1 | hd | −3.95 | −3474.64 | 155.0 | −3.6% | 111,489 | 40.7 | 1.13 | logic restructuring（G-walk FSM factoring） | 破棄 |
| v2 | hd | −3.82 | −3623.05 | 158.2 | −1.6% | 108,537 | 40.4 | 1.13 | pipeline insertion（PIPELINE_DEPTH=2） | 破棄 |
| **v3** | hd | −2.79 | −2877.01 | 189.0 | +17.5% | 110,291 | 40.6 | 1.12 | retiming（観測カウンタ enable レジスタ化） | 採用 |
| **v4** | hd | −2.33 | −3031.33 | 207.0 | +28.7% | 111,447 | 41.2 | 1.15 | precompute + retiming（発行アドレス事前計算） | 採用 |
| **v5** | hd | −1.64 | −2251.98 | 241.5 | +50.2% | 115,611 | 43.5 | 1.21 | pipeline insertion（servicer probe/commit） | 採用 |
| **v6** | hd | −1.76 | −1348.12 | 234.7 | +46.0% | 92,845 | 35.8 | 0.99 | datapath restructuring（line-organized IOTLB） | 採用 |
| v7 | hd | ≈−1.85※ | n/a※ | 229.9 | +43.0% | 92,773 | 36.6 | 1.04 | pipeline insertion（R チャネル登録） | 破棄(null) |
| v8 | hd | ≈−2.18※ | n/a※ | 213.7 | +32.9% | 93,376 | 36.8 | 1.04 | precompute（commit+consume 同時短縮） | 破棄(回帰) |
| **v9** | hd | −1.32 | −812.38 | 261.8 | +62.8% | 98,517 | 38.1 | 1.08 | precompute（3 co-critical 同時解消） | 採用 |
| **v10** | hd | −0.98 | −919.31 | 287.4 | +78.7% | 96,616 | 38.0 | 1.09 | pipeline insertion（consume addr-gen 段分割） | 採用 |
| **v11** | hd | −1.25 | −1279.98 | 266.7 | +65.9% | 94,091 | 36.7 | 1.05 | dead-logic removal（観測カウンタ合成除外） | 採用(hd最終) |
| **v12** | hs | −0.38 | −35.32 | 347.2 | +115.9% | 131,926 | 50.5 | 1.45 | library swap（hd→hs high-speed） | 採用(hs) |
| **v13** | hs | −0.31 | −19.77 | 355.9 | +121.3% | 134,165 | 50.8 | 1.46 | precompute（prefetch dedup 事前計算） | 採用(hs) |
| **v14** | hs | **−0.03** | **−0.20** | **395.3** | **+145.8%** | 123,510 | 50.1 | 1.44 | P&R constraint + placement 最適化 | 採用(hs最終) |

※ v7/v8 は計測後に v6 へリバートしたため、git 各版コミットの post-opt ログは v6 の値。
v7/v8 の Fmax（229.9/213.7）はリバート前の測定値（`CFG5_FMAX_LOG.md` 記録）。WNS は Fmax から
逆算（2.5 − 1000/Fmax）した近似で、TNS はログ未保存（n/a）。それ以外の全値は実ログ抽出。

### トレンドの読み方
- **WNS（@2.5ns）**: v0 −3.72 → v11 −1.25（hd）→ v14 **−0.03**（hs）。400 MHz への不足が
  3.72ns → 0.03ns まで縮小。
- **TNS（@2.5ns）**: v0 −3422.94 → v9 −812 → v12 −35.3 → v14 **−0.20**。違反パスの総量が
  ~17,000 倍改善し、v14 では「最悪 1 本が −0.03ns 違反するだけ」の状態。
- **Fmax**: hd で 160.8→287.4（最大 +78.7%、v10）、観測カウンタ除去後の hd 機能クリーン版
  v11=266.7、高速ライブラリ+P&R 最適化で hs v14=**395.3（spec 400 の 98.8%）**。
- **トレードオフの一般像**: hd 内では面積/電力ほぼ横ばい〜微増で Fmax を倍増（v6 でむしろ減）。
  hs 化（v12）で Fmax +30% と引き換えに面積 +40%・電力 +38%。

---

## 2. 版ごとの詳細分析

各版: **変更点**（どのモジュールの RTL/フローをどう書き換えたか, before/after）、**動機**
（解消を狙ったクリティカルパス）、**効果・副作用**（ログ実数値とトレードオフ）。

### v1 — G-walk FSM factoring（logic restructuring）→ 破棄
- **変更点**: `iommu_top.sv` の walk FSM を、flat な 12 状態 `pc`（before）から
  `{kind, level, ret}` の構造化状態（after）へ factor。3 つの同一 G-walk を共通化。
- **動機**: 「12-way の `pc` デコード mux が律速」という仮説。状態数削減で次状態論理を浅く。
- **効果・副作用**: post-opt **WNS −3.72→−3.95**（悪化）、Fmax 160.8→**155.0（−3.6%）**、
  TNS −3422.94→−3474.64（悪化）、area 110,465→111,489。**仮説は誤り**: flat pc は合成器が
  安価にマップしており、factoring はかえって分岐の多い次状態論理を生み段数が増えた。リバート。

### v2 — issue パイプライン化（pipeline insertion）→ 破棄
- **変更点**: `PIPELINE_DEPTH=2`。consume→発行を脱融合し、発行を registered walker 状態
  （WRUN）からのみ行う（before: 同一サイクル融合）。
- **動機**: 融合 consume→発行コーンの段分割。
- **効果・副作用**: WNS −3.72→**−3.82**、Fmax 160.8→**158.2（−1.6%, ノイズ域）**、
  TNS 悪化（−3623.05）、area 108,537、+1 cycle/read レイテンシ増。**境界を跨いでいなかった**:
  真の律速は consume→発行境界ではなかった（v3 で判明）。リバート。

### v3 — 観測カウンタ脱結合（retiming）→ 採用 **(+17.5%)**
- **変更点**: `walks_q`/`resp_q`（32bit 統計カウンタ）の増分 enable をレジスタ化
  （`walk_inc_q`/`pf_inc_q`/`resp_inc_q`）。before: `walks_q <= walks_q + svc_launch + pf_launch`
  が IOTLB ルックアップ由来の `svc_launch` に直結。after: enable を 1 段遅らせる（最終カウント不変）。
- **動機**: ネットリスト FF 確認で真の律速 = `bvpn_q → IOTLB CAM → svc_launch → walks_q[31]
  桁上げ`（**統計カウンタの桁上げ連鎖が IOTLB に gate されていた**）と判明。
- **効果・副作用**: WNS −3.72→**−2.79**、Fmax 160.8→**189.0（+17.5%）**、TNS −3422.94→
  **−2877.01**、area ほぼ不変（110,291）、power +0.7mW。cocotb 全 config walks/resp 不変・PASS。

### v4 — 発行アドレス事前計算（precompute / retiming）→ 採用 **(+28.7%)**
- **変更点**: `idx_of + pte_addr`（アドレス生成）を発行サイクルではなく**walker 状態書込み時に
  計算**し `wiaddr_q` にレジスタ化。発行は register 読み出し+arbiter mux のみ（before: 発行時に
  combinational 生成）。
- **動機**: v3 後の律速 = 発行アドレス生成コーン `walker reg → idx_of → pte_addr → araddr`。
- **効果・副作用**: WNS −2.79→**−2.33**、Fmax 189.0→**207.0（+28.7% vs v0）**、area 111,447
  （+wiaddr レジスタ）、power 41.2mW、cyc/trans 11.08→11.20（+1 cycle/read）。TNS −3031（≈横ばい
  ＝別パス群が残存）。

### v5 — servicer probe/commit パイプ化（pipeline insertion）→ 採用 **(+50.2%)**
- **変更点**: buffer servicer を 2 サイクルに分割。**probe**: 選択エントリの IOTLB/PWC ルックアップ
  結果を staging レジスタ（`stg_*_q`）へ。**commit**: staging から resolve または launch。
- **動機**: v4 後の律速 = `bvpn_q → IOTLB 16-way CAM 比較 → start → {wbase_q,wiaddr_q}`。
- **効果・副作用**: WNS −2.33→**−1.64**、Fmax 207.0→**241.5（+50.2%）**、TNS −3031→**−2251.98**、
  **area 111,447→115,611（+3.6%、staging FF 分）**、power 43.5mW。CAM コーンが probe サイクルに分離。

### v6 — ライン枠 IOTLB（datapath restructuring）→ 採用
- **変更点**: 新 `line_iotlb.sv`（fa_cache とポート互換）。before: 16-way フルアソシ CAM
  （tag=VPN 27bit ×16）。after: **2 ライン枠 × 8 ページ**、VPN を {line_tag(24b), offset(3b)}
  に分解し「2×ライン tag 比較 + offset 駆動 8:1 mux」。`HAS_IOTLB` 経路で差替え。
- **動機**: v5 で分離した IOTLB CAM 比較自体の削減。連続 IOVA/coalesce 構造の活用。
- **効果・副作用**: WNS −1.64→−1.76（≈横ばい）、Fmax 241.5→**234.7（−2.8%, P&R ノイズ域）**だが
  **area 115,611→92,845（−19.7%）**、**power 43.5→35.8mW（−17.7%）**、**energy 1.21→0.99nJ
  （v0 以下）**、TNS −2251→**−1348**。比較器/タグ FF が ~1/8。cocotb 機能等価（cfg3/4/5 全 PASS）。
  **Fmax 横ばい・効率大幅改善のトレードオフ**で、IOTLB を律速から外し律速が consume 側へ移動。

### v7 — メモリ R チャネル登録（pipeline insertion）→ 破棄(null)
- **変更点**: `rvalid/rdata/rid/rlast` を 1 段レジスタ化し consume を `r_*` で駆動。
- **動機**: consume パス（input port `rdata` → next_base → wbase_q、input_delay 込み）の reg2reg 化。
- **効果・副作用**: Fmax 234.7→**229.9（利得なし）**、synth 見積はむしろ悪化、cyc/trans +0.25。
  ネットリスト確認で **consume と commit が同水準で co-critical**と判明（片方だけ直しても無効）。リバート。

### v8 — commit+consume 同時短縮（precompute）→ 破棄(回帰)
- **変更点**: (a) launch アドレス probe 事前計算 + (b) `e_busy` を launch enable から除外 +
  (R) R チャネル登録、をまとめて投入。
- **動機**: v7 で判明した co-critical の同時解消。
- **効果・副作用**: Fmax 234.7→**213.7（回帰）**。**第 3 の co-critical（prefetch launch:
  `+LEAD 加算器 → iaddr_of → wiaddr_q`）が露出**して最長化。「2 本直すと 3 本目が出る」を実証。リバート。

### v9 — 3 co-critical 同時解消（precompute）→ 採用 **(+62.8%)**
- **変更点**: v8 の (a)(b)(R) に **(c) prefetch の pf_line（+LEAD 加算器）・launch アドレス・
  same-table を probe 事前計算**して staging（`stg_pf_*`）。commit/launch は reg→reg + dedup 比較。
- **動機**: demand-commit・consume・prefetch-launch の 3 本を**同時に**下げる。
- **効果・副作用**: WNS −1.76→**−1.32**、Fmax 234.7→**261.8（+62.8%）**、TNS −1348→**−812.38**、
  area 92,845→98,517（+5.7%、staging FF）、power 38.1mW、cyc/trans 11.35。突破成功。

### v10 — consume addr-gen 段分割（pipeline insertion）→ 採用 **(+78.7%)**
- **変更点**: consume の「next-state + iaddr_of」融合を分割。consume は walker 状態のみ更新し
  `wia_rdy_q=0`。専用 **addr-gen 段**が registered 状態から `iaddr_of` を計算（`wia_rdy_q=1`）、
  発行は `wia_rdy_q` を待つ。launch/prefetch は事前計算済みで初回遅延なし。
- **動機**: v9 後の律速 = consume の `rdata_q → next-state → iaddr_of → wiaddr_q`。
- **効果・副作用**: WNS −1.32→**−0.98**、Fmax 261.8→**287.4（+78.7%、hd 最高）**、
  **area 98,517→96,616（−1.9%、iaddr_of 重複解消）**、power 38.0mW、TNS −919.31（≈横ばい）。

### v11 — 観測カウンタ合成除外（dead-logic removal）→ 採用(hd 機能クリーン)
- **変更点**: `walks_q`/`resp_q`（翻訳機能に不使用）を `` `ifndef SYNTHESIS `` で囲い**合成時除去**
  （`walks_o/resp_o`=0）、sim 時は保持。合成フローに `sv2v -D SYNTHESIS`。
- **動機**: v10 律速が 32bit カウンタの桁上げ連鎖（`walks_o[0]→[31]`）= デバッグ計装。
- **効果・副作用**: **area 96,616→94,091（−2.5k）**、power 38.0→36.7mW、energy 1.09→1.05nJ。
  Fmax 287.4→**266.7**（WNS −0.98→−1.25）。**論理悪化ではなく P&R 配置変動**: v10 律速だった
  カウンタを除去→ネットリスト変化→実パス（PWC probe）が新配置で −1.25 に着地。cocotb walks=38 維持。

### v12 — 高速ライブラリ hd→hs（library swap）→ 採用(hs)
- **変更点**: RTL は v11 と**完全同一**。標準セルライブラリのみ `sky130_fd_sc_hd` →
  `sky130_fd_sc_hs`（高速・大面積・高漏れ）。`syn/v12_hs.py`、`opt.tcl` の driving cell を
  `DRVCELL` env 化、site `unithd`→`unit`。
- **動機**: アーキ最適化を出し切った後の Fmax 上積み。
- **効果・副作用**: WNS −1.25→**−0.38**、Fmax 266.7→**347.2（+30%、spec の 87%）**。
  **area 94,091→131,926（+40%）**、**power 36.7→50.5mW（+38%）**、energy 1.05→1.45nJ、
  TNS −1279.98→**−35.32**。典型的 high-speed vs high-density トレードオフ。

### v13 — prefetch dedup 事前計算（precompute）→ 採用(hs)
- **変更点**: v12 律速 `pf_last_q → 24bit dedup 比較 → pf_launch → wiaddr_q` を解消。
  dedup `(pf_line != pf_last_q)` を probe で事前計算（`stg_pf_fresh_q`）。launch enable は register のみ。
- **動機**: hs での最長パス（prefetch dedup 比較）を launch から外す。
- **効果・副作用**: WNS −0.38→**−0.31**、Fmax 347.2→**355.9（+2.5%）**、TNS −35.32→**−19.77**、
  area 134,165、power 50.8mW。汎用 RTL 改良（hd でも有効）。cocotb walks=38。
  新律速 = consume next-state `rdata_q → gpn27 → G-PWC CAM 比較 → next_base → wbase_q`。

### v14 — P&R 制約・配置最適化（synthesis constraint / placement）→ 採用(hs 最終)
- **変更点**: RTL/ネットリストは v13 と**同一（RTL 差分ゼロ）**。`opt.tcl` の P&R 制約のみ:
  (1) slew 緩和 `MAXTRANS 0.5→0.75`, `SLEWM/CAPM=0`（過剰バッファ抑制）、(2) フロアプラン密度
  `UTIL 35→65`（ダイ横断配線短縮）、(3) `SETUPM=0.15`。`UTIL`/`DRVCELL` を env 化。
- **動機**: v13 律速の ~44% がダイ横断バッファ（配線）だったため、配線短縮と過剰バッファ抑制を狙う。
- **効果・副作用**: WNS −0.31→**−0.03**、Fmax 355.9→**395.3（+11%、spec 400 の 98.8%）**、
  **TNS −19.77→−0.20**（ほぼ全パスが 400 を満たす）、**area 134,165→123,510（−7.9%、過剰バッファ減）**、
  power 50.1mW、energy 1.44nJ。ノブ探索（hs netlist 固定）: slew 0.75 で 387.6、UTIL 65 で 395.3
  （45/55/65/70/80 = 374.5/380.2/395.3/384.6/390.6）。UTIL65 は SETUPM 0.15/0.25/0.35 全てで
  WNS −0.03 = **決定論的な配置律速の天井**。

---

## 3. 打ち切り判断と結論

**打ち切り条件（指定）に合致して v14 で終了**:
1. **P&R 制約調整では 395.3 MHz が決定論的天井**（UTIL65 で飽和、SETUPM 不感）。これ以上は
   フローで削れない。
2. **クリティカルパスが配線長依存**: 最悪パス `rdata_q → gpn27 → G-PWC CAM 比較 → next_base
   → wbase_q` の ~44% がダイ横断バッファ鎖、残りは G-PWC 短絡＝**翻訳機能そのものの論理**。
3. 残り **WNS −0.03ns** を消すには G-PWC 短絡をパイプ化（walk レイテンシ増を伴う機能論理改変）が
   必要で、しかも **この 395.3 は ideal-clock 値**（CTS で skew/insertion ~0.2–0.5ns、route 寄生
   を加えると実シリコンで ~10–25% 低下）。形式的 ideal 400 を作っても 400 closure ではない。

**到達点**:
- hd 最終（機能クリーン, v11）: **266.7 MHz（spec 400 の 67%）**, 94,091 µm², 36.7 mW, 1.05 nJ。
- hs 最終（P&R 最適化, v14）: **395.3 MHz（spec 400 の 98.8%）**, 123,510 µm², 50.1 mW, 1.44 nJ。
- ベースライン比 **Fmax +145.8%**（160.8→395.3）。**TNS は −3422.94 → −0.20**（@2.5ns）。

**主たる効いた技法**（採用版のみ）: precompute / retiming（v3,v4,v9 — クリティカルなコーンを
state-write サイクルへ前倒し）、pipeline insertion（v5,v10 — 深いコーンの段分割）、
datapath restructuring（v6 — CAM をワークロード特化ライン構造に置換、面積/電力で最大効果）、
dead-logic removal（v11 — 非機能カウンタの合成除外）、library/P&R（v12,v14 — high-speed セル +
配線短縮）。**外れ版（v1,v2,v7,v8）の共通教訓**: 真の律速は合成後ネットリストの実 FF を見るまで
分からず、co-critical パス群は同時に下げないと効かない。

**rollback タグ**: `cfg5-v6-best`（234.7, 最小電力 hd）/ `cfg5-v9-best` / `cfg5-v10-best`
（287.4, hd 最高 Fmax）/ `cfg5-v11-clean`（hd 機能クリーン）/ `cfg5-v14-hs-tuned`（395.3, hs 最終）。

---

## 4. 補足: パイプライン無し（論理構造・段数最適化のみ）の上限

「パイプラインを一切入れず（`PIPELINE_DEPTH=1`）、論理構造・段数の最適化のみ」行った場合の
最大周波数。構成 `cfg5_nopipe_top`：probe/commit 分割・発行アドレス事前計算・addr-gen 段・
R チャネル登録・prefetch staging（全 PD≥2 ロジック）を **OFF**。非パイプの構造最適化＝
**ライン枠 IOTLB（v6）と観測カウンタ合成除外（v11）は保持**。P&R は v14 と同じ tuned knobs。
cocotb PASS（walks=38, cyc/trans **11.08**＝パイプ無しで最小レイテンシ）。

| lib | synth Fmax | **post-opt Fmax** | WNS [ns] | TNS [ns] | area [µm²] | power [mW] | energy/trans [nJ] |
|---|---|---|---|---|---|---|---|
| hd | 106.3 | **214.6 MHz** | −2.16 | −3002.2 | 80,938 | 31.1 | 0.86 |
| hs | 124.5 | **222.2 MHz** | −2.00 | −989.4 | 108,750 | 43.1 | 1.19 |

- **クリティカルパス（両 lib 共通）**: `rdata（入力ポート）→ consume 次状態 → アドレス生成 → araddr
  （出力ポート）`。= **メモリ返却 → メモリ発行の融合コンビネーショナルコーン**（1 サイクルに
  cache lookup + next-state + idx_of+pte_addr + arbiter が全て載る、input→output パス）。これは
  まさに v4/v5/v9/v10 のパイプ段が分割していた経路で、**パイプ無しでは 1 本の長大コーン**として残る。
- **対比（同じ logic 最適化済み・P&R も同条件）**:
  - hd: パイプ無し **214.6** → パイプ有り(v11) **266.7 MHz**（**+24%**）。
  - hs: パイプ無し **222.2** → パイプ有り(v14) **395.3 MHz**（**+78%**）。
- **示唆**: 論理構造・段数最適化（ライン枠 IOTLB、カウンタ除去）だけでも v0 比では大きく改善するが、
  **融合コーンの段分割（パイプライン）無しでは hd/hs とも ~215–222 MHz が上限**。とくに hs の利得は
  パイプ無しだと +3.5%（214.6→222.2）に留まる ← 長大な input→output コーン（配線+IO delay 込み）が
  支配的で、高速セルの効果が薄い。**hs の高速性はコーンを段分割して初めて活きる**（パイプ有りで +78%）。
- **トレードオフ**: パイプ無しは **面積最小・電力最小・レイテンシ最小**（hd 80,938µm²/31.1mW/0.86nJ、
  cyc/trans 11.08）。Fmax を犠牲に PPA 効率は最良。

> 注: no-pipe の数値は `cfg5_nopipe/results_{hd,hs}/postopt.log`（post-place+resize, ideal clock,
> no CTS/route, v14-tuned knobs）から抽出。

---

> 注: 全 WNS/TNS/area/power は `results/fmax_opt/postopt_cfg5_notag.log`（v0–v11, 各版 git コミット）
> および `cfg5_notag/results_hs/postopt*.log`（v12–v14）から抽出した post-place+resize（ideal clock,
> no CTS/route）の実測値。v7/v8 はリバートのため WNS は Fmax 逆算・TNS は未保存（表注記参照）。
