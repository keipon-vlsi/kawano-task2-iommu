# cfg5 Fmax 最大化ログ

cfg5（PWC+IOTLB+coalesce+prefetch、1 walker、context タグ無し）の Fmax を、アーキ
（RTL）変更ごとに合成して効果を記録する。各版で cocotb（全 config 機能）→ 合成（synth
見積）→ 制約+サイジング（post-opt = OpenROAD repair_design+repair_timing, syn/fmax_opt）の
PPA を測る。sky130_fd_sc_hd, tt 1v80。

合成見積 Fmax = OpenSTA（理想 wireload, placement 無し）。post-opt Fmax = 配置+リサイザ後
（CTS/route 省略, ideal clock）。電力は @400MHz・較正 activity 0.053。

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

### v3 結果：**+18%（160.8→189.0 MHz）大勝ち** — 真因は「観測カウンタ」だった
- **ネットリストで critical path の実 FF を確認**したのが決定打：startpoint=`bvpn_q[0]`、**endpoint=`walks_q[31]`（walk 起動の 32bit 統計カウンタ MSB）**。真の律速は
  `bvpn_q → IOTLB CAM 比較+MSHR/region 比較 → svc_launch → walks_q 32bit インクリメント桁上げ → walks_q[31]`。
  v2 で見た後段の「nand4/nor4 鎖→xnor2」は **32bit カウンタの桁上げ連鎖**で、遅い svc_launch(IOTLB 由来) に gate されていた。**統計カウンタが Fmax を律速していた。**
- **対策**：walks_q/resp_q は観測専用 → increment enable をレジスタ化（統計が 1 サイクル遅れるだけ、最終カウント不変）。IOTLB→カウンタ桁上げ経路が切れ **160.8→189.0 MHz**。cocotb 全 config の walks/resp 不変・全 PASS。汎用改善（RTL に常時適用）。
- **学び**：v1/v2 が外れたのは、この**観測カウンタ経路が全てをマスク**していたから。**合成後ネットリストで実 FF を確認**するまで真因に届かなかった。
- **新ボトルネック**：worst path は `_07668_(walker base/dgvpn) → idx + pte_addr → araddr[3]`（**発行アドレス生成**）、次点 `bspa_q → rsp_spa`（demand 応答）。= v2 が狙っていた発行経路が、カウンタ除去で**ようやく露出**。
- **次の方針**：**v4 = 発行アドレス(araddr)のレジスタ化**（mem_master 入口で AR を 1 段レジスタ化）。v2 の意図を、真のボトルネックが見えた今こそ適用。

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
