# cfg5 Fmax 最大化ログ

cfg5（PWC+IOTLB+coalesce+prefetch、1 walker、context タグ無し）の Fmax を、アーキ
（RTL）変更ごとに合成して効果を記録する。各版で cocotb（全 config 機能）→ 合成（synth
見積）→ 制約+サイジング（post-opt = OpenROAD repair_design+repair_timing, syn/fmax_opt）の
PPA を測る。sky130_fd_sc_hd, tt 1v80。

合成見積 Fmax = OpenSTA（理想 wireload, placement 無し）。post-opt Fmax = 配置+リサイザ後
（CTS/route 省略, ideal clock）。電力は @400MHz・較正 activity 0.053。

## PPA 履歴（全版サマリ）

| 版 | 変更点 | cocotb cyc/trans | walks | area_synth µm² | Fmax_synth MHz | area_postopt µm² | **Fmax_postopt MHz** | Δ vs v0 | power@400 mW | energy/trans nJ | 結果 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| v0 | baseline（フラット 12 状態 pc） | 11.08 | 38 | 95,374 | 99.5 | 110,465 | **160.8** | — | 37.9 | 1.05 | 基準 |
| v1 | G-walk factoring（{kind,lev,ret}） | 11.08 | 38 | 95,195 | 87.3 | 111,489 | **155.0** | −3.6% | 40.7 | 1.13 | 外れ・破棄 |
| v2 | issue パイプ化（PIPELINE_DEPTH=2） | 11.20 | 38 | 93,705 | 87.3 | 108,537 | **158.2** | −1.6% | 40.4 | 1.13 | 外れ・破棄 |
| v3 | 観測カウンタ脱結合 | 11.08 | 38 | 94,900 | 86.4 | 110,291 | **189.0** | **+17.5%** | 40.6 | 1.12 | 採用 |
| v4 | 発行アドレス事前計算（wiaddr_q, PD=2） | 11.20 | 38 | 95,463 | 80.1 | 111,447 | **207.0** | **+28.7%** | 41.2 | 1.15 | 採用 |
| v5 | servicer probe/commit パイプ化 | 11.10 | 38 | 99,911 | 115.2 | 115,611 | **241.5** | **+50.2%** | 43.5 | 1.21 | 採用 |
| v6 | ライン枠 IOTLB（2枠×8, CAM→tag比較+offset index） | 11.10 | 38 | 81,049 | 121.4 | 92,845 | **234.7** | **+46.0%** | 35.8 | 0.99 | 採用 |
| v7 | メモリ R チャネル 1 段レジスタ化 | 11.35 | 38 | 82,452 | 108.0 | 92,773 | **229.9** | +43.0% | 36.6 | 1.04 | 外れ・破棄 |
| v8 | (a)launch addr事前計算+(b)e_busy除外+R登録（commit/consume 同時短縮） | 11.35 | 38 | 82,615 | 118.1 | 93,376 | **213.7** | +32.9% | 36.8 | 1.04 | 外れ・破棄（回帰） |
| v9 | v8 + (c)prefetch line/addr の probe 事前計算（3経路同時解消） | 11.35 | 38 | 83,986 | 94.1 | 98,517 | **261.8** | **+62.8%** | 38.1 | 1.08 | **採用（ベスト）** |

注: v3/v4/v5/v6/v9 は採用。v9 が現状ベスト（261.8MHz）。v6 は rollback タグ `cfg5-v6-best`。v7/v8 は破棄。（v4 は v3 を含む）。Δ vs v0 は post-opt Fmax の対 v0 比。v1/v2 は計測後に破棄（v0 へリバート）、
v3 は汎用改善として常時適用、v4 は cfg5 で PIPELINE_DEPTH=2。詳細な分析は各版の節を参照。
energy/trans = power@400 × (cyc/trans) ÷ 400 [nJ]（周波数非依存の iso-work 指標）。Fmax を稼ぐ過程で
バッファ挿入により power がやや増え、energy/trans は 1.05→1.15 nJ と微増（性能と引き換えのコスト）。

## 版ごとの記録（時系列・分析付き）

| 版 | 変更点 | cocotb (cyc/trans, walks) | area_synth µm² | Fmax_synth | area_postopt µm² | **Fmax_postopt** | power@400 |
|---|---|---|---|---|---|---|---|
| v0 baseline | フラット 12 状態 pc | 11.08, 38 | 95,374 | 99.5 MHz | 110,465 | **160.8 MHz** | 37.9 mW |
| v1 G-walk factoring | {kind,lev,ret} 構造化状態 | 11.08, 38 | 95,195 | **87.3 MHz** | 111,489 | **155.0 MHz** | 40.7 mW |

### v1 結果：**改善せず（むしろ微悪化）** — 仮説は外れ
- synth Fmax 99.5→**87.3 MHz（−12%）**、post-opt 160.8→**155.0 MHz（−4%, P&R ノイズ域）**。面積ほぼ不変、cocotb 不変（機能等価）。
- **理由**：仮説「12-way の pc デコードが律速」は誤り。フラット pc は実質「ほぼ +1 インクリメント」で合成器が安価にマップしており、デコードは critical path の主因ではなかった。逆に {kind,lev,ret} 化で「KVPN→KGPN 遷移／ret 依存の戻り／level デクリメント＋kind 変化」という**分岐の多い次状態論理**が増え、段数が伸びた。
- post-opt 後の worst path は reg2reg（clk群, slack −3.95）。reset recovery（async, −1.6）はそれより良く、律速ではない。
- **学び**：critical path の主因は pc デコードではなく、**融合 consume→発行コーン全体（キャッシュ最完全ヒット短絡＋アドレス生成＋アービタ）の論理深さ**。状態数削減では効かない。→ パスを**段分割（パイプライン化）**する方が効く見込み。
- **対応方針**：v1 は破棄（v0 にリバート）し、v2 として**発行アドレスのレジスタ化（PIPELINE_DEPTH=2）**を試す。

| v2 issue パイプライン化 | PIPELINE_DEPTH=2（consume→発行を脱融合） | 11.20, 38 | 93,705 | 87.3 MHz | 108,537 | **158.2 MHz** | 40.4 mW |

### v2 結果：**改善せず（≈v0, P&R ノイズ域）** — また外れ、ただし真の律速が判明
- post-opt 160.8→**158.2 MHz**（reg2reg slack −3.82, async recovery −1.61 は非律速）。+1 サイクル/read のレイテンシだけ増えて Fmax 利得なし。
- **理由**：consume→発行の融合境界を脱融合したが、critical path はその境界を**跨いでいない**。実パスは
  `FF _07218_ → buf12 → and4_4 ×7 → xnor3 → nand4 → … → nand4/nor4 鎖 → xnor2 → FF _06959_`
  という **CAM 比較＋優先エンコーダ＋mux＋reduction の深い FF→FF コーン（~26 段, 6.18ns）**。
- このパターン（並列 XNOR 比較 → and4 reduction → 優先/nand-nor 鎖 → mux）は **IOTLB 16-way ルックアップ**そのもの。経路は **buffer servicer の probe→resolve**（`bvpn_q[bsel] → IOTLB CAM 比較+優先+データ mux → bspa_q[bsel]`）で、walker FSM / 発行経路ではない。
- **学び**：cfg5 の律速は walker の状態数でも consume→発行融合でもなく、**IOTLB（16 エントリ CAM）ルックアップの compare+priority+mux 深さ**。v1/v2 はどちらもこの経路に触れていなかった。
- **対応方針**：v2 破棄。次は v3。ただしネットリストで実 FF を確認したら真因が想定と違った（下記）。

| v3 観測カウンタ脱結合 | walks_q/resp_q の increment enable をレジスタ化 | 11.08, 38 | 94,900 | 86.4 MHz | 110,291 | **189.0 MHz** | 40.6 mW |
| v4 発行アドレス事前計算 | idx_of+pte_addr を状態書込時に計算→wiaddr_q にレジスタ化（PIPELINE_DEPTH=2） | 11.20, 38 | 95,463 | 80.1 MHz | 111,447 | **207.0 MHz** | 41.2 mW |

### v3 結果：**+18%（160.8→189.0 MHz）大勝ち** — 真因は「観測カウンタ」だった
- **ネットリストで critical path の実 FF を確認**したのが決定打：startpoint=`bvpn_q[0]`、**endpoint=`walks_q[31]`（walk 起動の 32bit 統計カウンタ MSB）**。真の律速は
  `bvpn_q → IOTLB CAM 比較+MSHR/region 比較 → svc_launch → walks_q 32bit インクリメント桁上げ → walks_q[31]`。
  v2 で見た後段の「nand4/nor4 鎖→xnor2」は **32bit カウンタの桁上げ連鎖**で、遅い svc_launch(IOTLB 由来) に gate されていた。**統計カウンタが Fmax を律速していた。**
- **対策**：walks_q/resp_q は観測専用 → increment enable をレジスタ化（統計が 1 サイクル遅れるだけ、最終カウント不変）。IOTLB→カウンタ桁上げ経路が切れ **160.8→189.0 MHz**。cocotb 全 config の walks/resp 不変・全 PASS。汎用改善（RTL に常時適用）。
- **学び**：v1/v2 が外れたのは、この**観測カウンタ経路が全てをマスク**していたから。**合成後ネットリストで実 FF を確認**するまで真因に届かなかった。
- **新ボトルネック**：worst path は `_07668_(walker base/dgvpn) → idx + pte_addr → araddr[3]`（**発行アドレス生成**）、次点 `bspa_q → rsp_spa`（demand 応答）。= v2 が狙っていた発行経路が、カウンタ除去で**ようやく露出**。
- **次の方針**：**v4 = 発行アドレス(araddr)のレジスタ化**（mem_master 入口で AR を 1 段レジスタ化）。v2 の意図を、真のボトルネックが見えた今こそ適用。

### v4 結果：**+9.5%（189.0→207.0 MHz）／v0 比 +28.7%** — コーン分割が効いた
- **方針の修正**：mem_master 出口だけのレジスタ化では `walker reg → idx_of → pte_addr → arbiter mux → AR` の**コンビ段数は不変**（終点が FF になるだけ）。本命は**発行アドレスの事前計算レジスタ化**：`idx_of+pte_addr` を walker 状態書込みサイクル（launch/consume）で計算し `wiaddr_q` に格納、発行サイクルは**レジスタ読み出し＋アービタ mux のみ**。PIPELINE_DEPTH=2 のパイプ段に address-gen コーンを載せ替えた（v2 が外したのは cycle B に同じコーンが残っていたため）。
- **効果**：post-opt 189.0→**207.0 MHz**。発行→AR から idx_of+pte_addr が消え、コーンが短縮。cocotb 全 PASS、walks=38 不変、cyc/trans は 11.08→11.20（+1 cycle/read のパイプ段ぶん、wire rate=16.4cyc に対し十分余裕）。
- **新ボトルネック**（ネットリスト FF 確認）：startpoint=`bvpn_q[0]`（demand VPN）、endpoint=`wbase_q[0][14]`（slack −2.33）と `wiaddr_q[0][35]`（−2.32、ほぼ同値）。= **buffer-servicer の launch コーン**：`bvpn_q → IOTLB/PWC ルックアップ(CAM compare: nor4b→nand4→and4b) → start_base/svc 判定 → {wbase_q, wiaddr_q} へ書込み`。address-gen はもう載っておらず、**キャッシュ・ルックアップ→start アドレス計算→状態書込み**が律速。
- **学び**：レジスタ化は「終点を FF にする」だけでは無意味で、**コーン自体を別サイクルに分割**して初めて効く。v4 は launch サイクルにコーンを移し、それでも全体が短くなった＝旧（issue+arbiter+IO）より新（consume/launch+precompute）の方が浅い。
- **次の方針**：**v5 = buffer-servicer のパイプ化**。demand VPN のキャッシュ・ルックアップ結果（PWC/IOTLB probe）を 1 段レジスタ化してから start_base/start アドレスを計算・launch する（cycle A=probe、cycle B=launch+precompute）。今 wiaddr_q/wbase_q を律速している `bvpn_q → CAM → start` の段を割る。

### v5 結果：**+16.7%（207.0→241.5 MHz）／v0 比 +50.2%** — CAM コーンの分離に成功
- **実装**：servicer を probe / commit の 2 サイクルに分割（`SVC_PIPE=(PIPELINE_DEPTH>=2)`）。**probe**＝選択 BNEED エントリの IOTLB/PWC ルックアップ結果（hit / data / start_pc / start_base / vpn / ctx / line）を staging レジスタ（`stg_*_q`）に格納。**commit**（翌サイクル）＝staging から resolve-on-hit（bspa 書込み）または launch+precompute（wbase_q/wiaddr_q 書込み）。staging は 1-shot（commit 毎にクリア、未処理なら再 probe）でデッドロック無し。`e_*`/`e_svc_*` で PD<2 は従来コンビ servicer と完全等価（cfg1-4 不変、cocotb で確認: cfg4 11.08/walks38）。
- **効果**：synth 見積 80.1→**115.2 MHz**、post-opt 207.0→**241.5 MHz**。cocotb 全 PASS、walks=38 不変、cyc/trans 11.20→**11.10**（probe/commit で +1 だが IOTLB-hit 解決経路の取り回しで実質同等、wire rate 16.4cyc に余裕）。area 111,447→115,611（staging FF ~160bit ぶん +4,164µm²）。
- **新ボトルネック**（ネットリスト FF 確認）：startpoint=`bvpn_q[0]`（demand VPN）、endpoint=`stg_iotlb_d_q`（=`e_iotlb_d`、staged IOTLB data）。slack −1.64。= **IOTLB 16 エントリ CAM ルックアップそのもの**：`bvpn_q → IOTLB CAM 比較(16-way, tag=VPN27) → 優先+データ mux → stg_iotlb_d_q`。狙い通り CAM コンペアが probe サイクルに分離され、commit 側（reg→start→wbase/wiaddr）は非律速化。
- **学び**：v3/v4 で「カウンタ」「発行アドレス」を順に除去し、v5 でついに**本丸の IOTLB CAM 比較**が露出。ここからは「キャッシュ・ルックアップ構造」そのものの最適化（CAM のパイプ化／連想度・タグ幅削減／set-assoc 化）が効く領域。
- **次の方針 v6 候補**：(a) IOTLB CAM のパイプ化（tag 比較を 1 段、data mux を次段）、(b) IOTLB を full-assoc → set-assoc/直接マップ化して比較器段数を削減（coalesce 済みストリームなら命中率影響は小）、(c) タグ幅短縮。まず (a) か (b) を測る。

### v6 結果：**Fmax ほぼ横ばい（241.5→234.7, −2.8% P&R ノイズ域）だが面積 −19.7%・電力 −17.7%・energy/trans −18% の大幅効率改善**
- **実装**：連続 IOVA / coalesce 構造を直接利用した `line_iotlb.sv`（fa_cache とポート互換、`HAS_IOTLB` 経路で差替え）。VPN を `{line_tag=VPN[26:3], offset=VPN[2:0]}` に分解し、**2 ライン枠 × 8 ページ**で保持。ルックアップは **16-way×27bit CAM → 2×ライン tag 比較(24bit) + offset 駆動の 8:1 データ mux**（match デコード不要）。fill は従来のビート単位のまま、書き先が「ライン枠の offset スロット」になるだけ。
- **効果**：synth 見積 Fmax 115.2→**121.4 MHz**（決定論的には改善）、post-opt 241.5→**234.7 MHz**（resizer のばらつきで微減＝ノイズ域）。一方 **面積 115,611→81,049µm²(synth)/92,845µm²(post-opt)（−19.7%）**、**電力 43.5→35.8mW（−17.7%）**、**energy/trans 1.21→0.99nJ（v0 の 1.05 すら下回る）**。比較器・タグ FF が 1/8 になった効果。cocotb 全 IOTLB config（cfg3/4/5）PASS・walks/cyc 完全不変（機能等価）。
- **新ボトルネック**（ネットリスト FF 確認）：startpoint=`rdata[35]`（メモリ返却データ入力）、endpoint=`wbase_q[0]`(slack −1.76)/`wiaddr_q[0]`。= **consume 経路**：`rdata → ppn28(cons_pte) → next_base(unique case cons_pc) → wbase_q`、および wiaddr 事前計算。**IOTLB CAM はもはや律速ではない**（狙い達成）。律速はメモリ返却→次状態計算のコーン（startpoint が入力ポートなので input_delay も乗る）。
- **学び**：ワークロード特化のライン枠化は **Fmax よりむしろ面積・電力で効く**（CAM の比較器/タグが支配的だった）。Fmax は v5 でリサイザが既に IOTLB パスを十分バッファ化していたため横ばいだが、**律速が consume 側に移った**＝IOTLB はもう問題でない。energy/trans が v0 以下に戻ったのも収穫。
- **次の方針 v7 候補**：consume 経路の短縮。(a) mem_master の R データ入力を 1 段レジスタ化（input_delay 経路を内部 reg2reg 化＝実機の R チャネルレジスタスライス相当）、(b) consume の next-state 計算と wbase/wiaddr 事前計算を分割（launch 側 v4/v5 の consume 版）。まず (a)（軽量・実機妥当）を測る。

### v7 結果：**改善せず（234.7→229.9, −2.0% ノイズ域）＋レイテンシ +0.25cyc** — 外れ、破棄。ただし真因が判明
- **実装**：R チャネル（rvalid/rdata/rid/rlast）を `PIPELINE_DEPTH>=2` で 1 段レジスタ化し、consume を `r_*`（登録版）で駆動。input_delay 経路（startpoint=`rdata`）を内部 reg2reg 化する狙い。cocotb 全 PASS、walks=38 不変、cyc/trans 11.10→**11.35**（+1 cycle/return）。
- **結果**：post-opt 234.7→**229.9 MHz**（ノイズ域の微減）、synth 見積は 121.4→**108.0** とむしろ悪化。**Fmax 利得ゼロ＋レイテンシ増**。
- **真因（ネットリスト FF 確認）**：v7 後の startpoint=`stg_line_q`(=`e_line`)、endpoint=`wbase_q[0]`(slack −1.85)/`wiaddr_q[0]`。= **commit（launch）経路**：`stg_line_q → e_busy 比較(walker line と照合) → e_svc_launch → wbase_q / iaddr_of→wiaddr_q`。つまり R 登録で **consume 経路（rdata→wbase）は確かに非律速化**したが、**commit 経路が同水準（~230–235MHz）で待ち構えていた**ため総合 Fmax は不変。
- **学び**：v6 後、**consume 経路と commit 経路が ~230–235MHz で“同着のクリティカル”**だった。片方（consume）だけ直しても、もう片方（commit）が即座に律速になり総合は動かない。**両方を同時に下げない限り Fmax は上がらない**。R 登録は単独では純コスト（レイテンシ増）なので破棄。
- **次の方針 v8**：露出した **commit（launch）経路**を短縮。具体的には `e_busy`（staging line と walker wline の比較）と launch 時の `iaddr_of`（idx_of+pte_addr）を commit クリティカルから外す。案：(a) launch 用 start アドレスを probe サイクルで事前計算し staging に積む（`stg_start_addr_q`）＝commit は reg→reg 書込みのみ、(b) e_busy を probe 側で算出して staging 化。これで commit を下げ、必要なら consume 側（v7 の R 登録）と**セットで**再投入して両クリティカルを同時に解消する。

### v8 結果：**回帰（234.7→213.7, −9%）** — 外れ、破棄。第 3 の co-critical「prefetch launch」が露出
- **実装**：(a) demand launch アドレスを probe で事前計算（`stg_start_addr_q`）→ commit は `wiaddr_q<=stg_start_addr_q` の reg→reg、(b) `e_svc_launch` から `e_busy` 項を除外（単一 walker では `wfree_v` が `~busy` を含意するため冗長）＋ commit を if-else→独立 if 化（launch enable から `~e_svc_ride` を排除）、(c) v7 の R チャネル登録を同時投入。cocotb 全 PASS、walks=38、cyc/trans 11.35。
- **結果**：post-opt 234.7→**213.7 MHz（回帰）**。狙った demand-commit と consume は短縮できたが、**第 3 の経路が露出**。
- **新ボトルネック**（ネットリスト FF 確認）：startpoint=`stg_line_q`(=`e_line`)、endpoint=`wpc_q[0]`/`wiaddr_q`。経路本体は `stg_line_q → and4_4 ×7（+LEAD 加算器の桁上げ鎖）→ pf_trig → pf_launch → wpc_q/wiaddr_q`。= **prefetch launch 経路**：`prefetch_ctrl` が `pf_line = demand_line + LEAD` を**加算器**で作り、`iaddr_of(8, region_vml0_q, {pf_line})` を launch 時に計算して `wiaddr_q`/`wvpn_q`/`wline_q` へ。demand 側だけ事前計算したので、**prefetch 側の「加算器 + iaddr_of」が今や最長**に。
- **学び**：co-critical はもう一段あった。**demand-commit・consume・prefetch-launch の 3 本が ~214–235MHz に密集**。v8 は前 2 本を下げて 3 本目（prefetch）を露出させ、その prefetch 経路は「+LEAD 加算器 → idx_of+pte_addr」と**むしろ最長**だったため総合が悪化。1〜2 本ずつ潰す限り、最後に残った最長経路で頭打ち（むしろ表面化で悪化し得る）。
- **判断**：v8 破棄、**v6（234.7MHz, area 92,845, power 35.8, energy 0.99）を現状ベストとして確定**。さらに上げるには prefetch の `pf_line` 加算器と iaddr_of も probe 段へ事前計算（(c)）し、demand+consume+prefetch の 3 本を**同時に**下げる必要がある＝複雑度が増し利得は逓減。
- **次の方針（任意）v9**：(c) prefetch アドレス/`pf_line` の probe 事前計算を v8 の (a)(b)(R) と**全部まとめて**投入し 3 本同時に解消。やる価値があるかは費用対効果次第（v6 で既に +46%・面積/電力大幅減を達成済み）。

### v9 結果：**+11.5%（234.7→261.8 MHz）／v0 比 +62.8%** — 3 経路同時解消で突破、現状ベスト
- **実装**：v8 の (a)(b)(R) に **(c) prefetch の事前計算**を追加。`prefetch_ctrl` の `pf_line = demand_line + LEAD`（加算器）・`same_table`・launch アドレス `iaddr_of(pc8,...)` を **probe サイクルで計算**して staging（`stg_pf_line_q`/`stg_pf_addr_q`/`stg_pf_same_q`/`stg_pf_region_ok_q`/`stg_pf_regbase_q`）に積む。commit/launch は **reg→reg 書込み + dedup 比較（`stg_pf_line_q != pf_last_q`）**のみ。PD≥2 専用（cfg4=PD<2 は従来 `prefetch_ctrl` 経路のまま不変）。
- **効果**：post-opt 234.7→**261.8 MHz**（v6 比 +11.5%、これまでのベスト v5 241.5 も更新）。cocotb 全 PASS、walks=38 不変（prefetch + dedup 正常）、cyc/trans 11.35（R 登録ぶん +0.25、wire rate 16.4 に余裕）。area 92,845→98,517（staging FF ぶん +5,672、なお v0 の 110,465 は下回る）、power 38.1mW、energy/trans 1.08nJ。
- **学び（核心）**：v7（consume 単独）= null、v8（commit+consume 2 本）= 第 3 経路露出で回帰、**v9（demand-commit + consume + prefetch-launch の 3 本同時）= 突破**。**co-critical なパス群は「全部まとめて下げる」必要がある**ことを実証。1〜2 本ずつでは最長経路が残って効かない（むしろ表面化で悪化し得る）。
- **新ボトルネック**（ネットリスト FF 確認）：startpoint=`rdata_q`（登録 R データ）、endpoint=`wiaddr_q[0]`（slack −1.32）。= **consume 側のアドレス事前計算**：`rdata_q → gpn27/next-state(next_dg/next_base) → iaddr_of → wiaddr_q`。3 本を潰した結果、**第 4 の経路（consume の iaddr_of 事前計算）**が露出。これは v4 の consume 版を「もう一段 probe 化」する話で、さらに逓減。
- **判断**：v9 を**新ベストとして採用**（261.8MHz, area 98,517, power 38.1, energy 1.08）。rollback タグ `cfg5-v6-best` は維持。これ以上は consume 側 iaddr_of の段分割（v10）になるが、+62.8% 達成済みで費用対効果は更に低下。

## v0 baseline（現状）の critical path
融合 consume→次状態→発行コーン: walker 状態 FF → cache 最完全ヒット短絡 → `unique
case(cons_pc)`（12-way）+ `idx_of(next_pc)`（12-way）→ pte_addr 連結 → 次状態 FF。
post-opt 後の path: FF → buf12（高fanout バッファ）→ and4 ×7（case/比較デコード）→ xnor3
（CAM タグ比較）→ nand4 → FF。律速は **12-way の状態デコード mux 縦続**。

---

## 変更候補（予定）
- **v1: G-walk factoring** — 3 つの同一 G-walk（table-G ×2 + data-G）を「VM フェーズ ×
  G レベル」の構造化状態に factor。flat 12 状態 → {kind, level, ret} で index/next-state
  デコードを 12-way → 3-way 級に浅く。レイテンシ不変を狙う。
- v2 以降: 発行アドレスのレジスタ化（PIPELINE_DEPTH=2）、CAM ルックアップのパイプ化 等。
