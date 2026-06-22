# PWC / IOTLB ルックアップ・マイクロアーキテクチャ QoR 研究レポート

IOMMU の page-walk cache (PWC) と IOTLB の**タグ構成・fill（登録）論理・lookup 論理**が、
合成後の**論理構造・論理段数・面積内訳・クリティカルパス**にどう効くかを、孤立モジュールで
測定した QoR 比較。全 variant を同一フロー（sky130_fd_sc_hd, Yosys + OpenSTA + OpenROAD
repair）で合成。前提は `ASSUMPTIONS.md`、実行は `README.md`、生データは `results/`。
各構造の**合成後回路イメージ（比較器・mux・優先・加算器の個数と深さ）の詳細解説**は
`STRUCTURES.md`、ブロック図は `figs/figures/cache_structures.png`。

- PWC = 2 エントリ、tag = VPN[2:1] (18b)、value = base SPA (PPN 44b)。
- IOTLB = 16 エントリ = 2 ライン × 8 ページ、tag = VPN[2:0] (27b)、value = data SPA (44b)。
- 格納は全て **DFF**。lookup/fill 両方を実装。各 variant は req/resp レジスタで挟み、測定経路を
  clean な reg→reg「lookup 経路」に統一。
- **Fmax** = **配置 + ファンアウト/バッファ最適化込み**の reg→reg 経路。フローは OpenROAD で
  `initialize_floorplan` → `global_placement` → `estimate_parasitics`（実配置寄生）→ `repair_design`
  （slew/cap/**fanout** DRC = 高ファンアウトのバッファツリー化）→ `repair_timing -setup`（セル
  サイジング + バッファ挿入）→ `detailed_placement` → STA。**CTS/route のみ省略**（ideal clock）。
- **論理段数** = repair 後クリティカルパス上の**論理ゲート段数**（buffer/clk/delay セル除外）。
- **面積** = µm² と GE（= 面積 ÷ nand2_1 3.75µm²）、内訳は **storage DFF vs 組合せ**。
- 全 variant **機能チェック PASS**（iverilog, 連続 IOVA トレース + window 外ミス）。

---

## 1. PWC 比較表（2 エントリ, tag=VPN[2:1]）

**Fmax 降順 / 面積（µm²）**:

| 変種 | 方式 | func | **Fmax [MHz]** | area [µm²] | GE | DFF | comb [µm²] | **論理段数** | クリティカルパス | 賭け |
|---|---|---|---|---|---|---|---|---|---|---|
| **P4** | speculative read（予測 index 直読み + 並列 validate） | ✅ | **615.8** | 6,614 | 1,764 | 187 | 2,253 | **5** | mux2i→o22ai→a211oi→nand4→nor3 | ○連続 |
| **P2** | even-aligned window（LSB 直接選択, 比較 17b） | ✅ | 479.5 | **6,068** | **1,618** | 170 | 2,127 | 5 | xnor2→o221ai→or4→o31ai→mux2 | ○整列 |
| P0 | 2-way FA（基準: 2 比較器 + 優先 mux） | ✅ | 460.3 | 6,965 | 1,857 | 190 | 2,524 | 5 | xor2→a221o→nor4→nand4→mux2 | × |
| P3 | sequential pointer（current 優先 + promote） | ✅ | 367.2 | 7,124 | 1,900 | 190 | 2,683 | 7 | mux2i→xnor2→nor4→and3→nand4→xor2→mux2 | ○連続 |
| P1 | base + delta（(tag−base)∈{0,1}） | ✅ | 221.9 | 6,833 | 1,822 | 171 | 2,872 | **15** | nand2b→maj3×4→…→or4→mux2 | ○連続 |

- **面積順**: P2(6,068) < P4(6,614) < P1(6,833) < P0(6,965) < P3(7,124)。
- どの variant も面積は **DFF（格納）が支配**（comb は 2.1–2.9k µm² で僅差）。差は主に lookup 論理の段数。

### 考察
- **P4（speculative read）が最速**。SPA を「予測 index（=tag LSB）で即読み」し、tag 比較は hit を
  並列に作るだけ。**SPA 経路が比較・優先 OR を待たない**ので段数 5 で最短遅延、Fmax 615 MHz。
  実質「LSB で直接マップ」＝連続ペアなら index 予測が当たる賭け。外れ時は compare 不一致で
  hit=0（正しくミス）。
- **P2（even-aligned window）が最小面積かつ準最速**。base を偶数整列し、**LSB がそのままエントリ選択
  （比較不要）**。比較は高位 17b のみ。減算器が要らないぶん P1 より圧倒的に浅く小さい。
- **P1（base+delta）が最悪（221 MHz, 段数 15）**。`tag−base` の **18bit 減算器（maj3 の桁上げ連鎖）**が
  そのままクリティカルパス。**「窓判定に算術を使う」と桁上げ伝搬で深くなる**ことを実証。整列窓 P2 が
  同じ「隣接ペア窓」を**算術なし**で実現して勝つ。
- **P3（sequential pointer）は worst-case で得しない**。current を優先比較しても、最悪は 2 比較 + 優先
  mux で段数 7・面積最大。ポインタは「common-case を 1 比較に」する設計意図だが、**lookup の最悪
  論理段数は減らない**（共通ケース最適化であって QoR では不利）。
- 2 エントリでは FA(P0) でも比較器は 2 個と小さく、P0 が悪くない基準。

---

## 2. IOTLB 比較表（16 = 2 ライン × 8, tag=VPN[2:0]）

**Fmax 降順**:

| 変種 | 方式 | func | **Fmax [MHz]** | area [µm²] | GE | DFF | comb [µm²] | **論理段数** | クリティカルパス | 賭け |
|---|---|---|---|---|---|---|---|---|---|---|
| **T1** | aligned single-window（比較 23b ×1 + 16:1 mux） | ✅ | **464.2** | 32,572 | 8,686 | 816 | 12,532 | **5** | nand2b→o2111ai→or4→o31a→mux2 | ○整列 |
| **T3** | speculative read（index 直読み + 並列 validate） | ✅ | **464.2** | 32,572 | 8,686 | 816 | 12,532 | 5 | （T1 と同一に合成） | ○整列 |
| **T6** | **16-way FA, one-hot 前提（優先エンコーダ廃止, AND-OR mux）** | ✅ | **367.8** | 52,874 | 14,100 | 1,228 | 22,534 | **7** | clkinvlp→a2111oi→nand4→nor4→a22o→a221oi→nand4 | ○一意 |
| T0 | line baseline（現行: 2 ライン tag 比較 + 8:1 mux） | ✅ | 343.7 | 34,556 | 9,215 | 843 | 13,816 | 7 | nor2b→a2111o→nor4→nand4→a21o→nor4→mux2 | × |
| T7 | line predictor（current ライン 1 本だけ参照, 外れたら即ミス, ダブルバッファ swap） | ✅ | 335.4 | 35,515 | 9,471 | 842 | 13,512 | 7※ | cur_tag→(==ft 比較)→nxt_data（**fill 経路**） | ○連続 |
| T2 | sequential pointer（current ライン優先） | ✅ | 293.8 | 37,868 | 10,098 | 843 | 17,128 | 7 | mux2i→o22ai→a211oi→nand4→o21a→nor4b→mux2 | ○連続 |
| T5 | **16-way FA CAM**（基準: 16 比較 + 優先エンコーダ + mux ツリー） | ✅ | 245.6 | **53,466** | 14,258 | 1,228 | 23,126 | 10 | clkinvlp→a221oi→nand4→or3→…→a31oi | × |
| T8 | 16-way FA, 優先を **2:1 mux 直列カスケード**に埋込（encoder 無し） | ✅ | 284.3 | 53,787 | 14,343 | 1,228 | 23,447 | **11** | clkinv→o2111ai→nor4→and4→…→o21ai | × |
| **T4** | per-line base+offset（base_ppn + adder, 連続前提） | ✅ | 127.0 | **12,090** | **3,224** | **229** | 6,720 | **28** | …→maj3×2→nor4b×多数→…→adder | ○連続物理 |

- **面積順**: **T4(12,090) ≪ T1=T3(32,572) < T0(34,556) < T7(35,515) < T2(37,868) < T6(52,874) < T5(53,466) < T8(53,787)**。
- ※ T7 の論理段数/Fmax の律速は **lookup ではなく fill 経路**（下記）。lookup 経路自体は浅い。

### 考察
- **T4（base+offset）が最小面積（他の 1/3〜1/4）かつ最低 Fmax**。連続物理なら **16×44b の data を捨て、
  ライン毎に base_ppn(44b)+contig bit だけ格納**（DFF 229 vs T0 843 ＝ storage を激減）。代償は
  **`base_ppn + offset` の 44bit 加算器**がパスに乗り、段数 28・127 MHz と最遅。**面積と速度の極端な
  トレードオフ**。連続物理（スーパーページ的）への賭け。外れ（非連続 fill）時は contig flag を落として
  そのラインをミス化（正しく再ウォークへフォール）。
- **T1（aligned window）が最速・小さめ**。16 ページを 1 整列窓とみなし **比較 1 個 + 16:1 index mux**。
  T0 の 2 ライン比較を 1 比較に減らし、offset は元々比較不要なので段数 5。
- **⚠ T1 の重要な限界（整列前提・境界跨ぎで実効容量低下）**：base を 1 個しか持たず、fill で
  `VPN[26:4]` が変わると **window 全体をクリアして再ベース**する。よって 16 エントリをフルに使えるのは
  **16 ページが 16(=128B)整列の時だけ**。連続ストリームが 16 ページ境界を跨ぐと前半が消える。**実測**：
  非整列の連続 16 ページ（0x85〜0x94, 0x90 で境界跨ぎ）fill 後のヒットは **T1=5/16 に対し T0=13/16**
  （`results/iotlb_align_tb.sv` / `results/iotlb_align_demo.txt`）。誤ヒットせず正しくミス→walk
  フォールバックなので**機能は正しい**が、実効容量・ヒット率が落ちる。
  - **整合性**：実 IOMMU の coalescing は 64B PTE ライン（連続 8 PTE）を 1 バーストで取るので **8 ページは
    構造的に 8 整列**。T0 の「2 ライン×8（line_tag=VPN[26:3]）」はこの 64B ライン単位で、2 ライン持つこと
    で境界を 1 回跨いでも両側を保持。T1 の単一 16-window は **2 本の連続 64B ラインが 128B 整列**という
    **より強い前提**で跨ぐと崩れる。→ **速度/面積だけなら T1、機能ロバスト性まで含めると T0（または独立
    2-tag の T7）が妥当**。T1 推奨は「16 整列が保証できる場合に限る」と読むべき。共有 TB は 16 整列の
    BASE=0x80 のみ試しており、この弱点を露出していなかった（テスト不足）。
  - **整列制約の正体（何整列か）**：T1 がフル活用できる条件＝「同時に載る 16 ページが**16 ページ整列
    ブロック**に収まる」。別表現で **ページ番号 16 整列 = IOVA 64 KB 整列 = PTE 上 128 B 整列**（16 PTE×8B
    ＝連続 2 本の 64B PTE ライン）。**T1 が使えるのは**：(1) DMA バッファが 64 KB 整列割り付けでブロック
    境界に揃ってアクセス、(2) 同時 working set が 16 ページ以内で境界跨ぎ両側同時保持が不要、(3) superpage
    （2 MB なら 4 KB を 16 個並べる必要が消える）、(4) プリフェッチでブロック境界に合わせ window 切替。
    **使えない**＝任意オフセットの連続ストリーム（本タスク一般形）。**coalescing の保証は 8 整列（64B
    ライン）まで**で 128B 整列は保証されないため、本タスクでは T0/T7 が正解、T1 は上記特殊条件のみ。
- **T1 と T3 が完全同一に合成**された（同 Fmax/面積/段数）。**「speculative read」と「aligned window」は
  offset/index 駆動の IOTLB では合成後に等価**（どちらも index で data を引き、tag を並列 validate）。
  重要な知見：この種の最適化は別物に見えて回路は同じになる。
- **T0（現行 line baseline）は中庸**。2 ライン tag 比較（24b ×2）+ 8:1 mux で段数 7・343 MHz。整列を仮定
  しないぶん T1 より 1 比較多い。
- **T7（line predictor＝見るラインを決め打ち、外れたら即ミス・他ラインは参照しない）**を、ご指摘どおり
  **current ライン 1 本を専用レジスタ（cur_*）に持ち、lookup はそれだけ参照**（`cur_val & (cur_tag==lt)
  & cur_subv[lo]`、SPA=`cur_data[lo]`）、offset wrap でダブルバッファの shadow(nxt) を swap-in、外れは
  即ミス、という構造で実装。結果 **335 MHz / depth 7 ＝ T0 同等で、T1 の 464 には届かない**。
  - **ただし律速は lookup ではなく fill 経路**：クリティカルパスは `cur_tag → (==ft の 24b 比較) →
    nxt_data 書込み`（2 ライン常駐させるための「この fill は現ライン延長か新ラインか」の判定が shadow の
    8×44 レジスタ書込みを gate）。**lookup 経路（1 比較 + 8:1 mux、ライン選択 mux 無し）は浅い**。fill は
    1 ページ/ライン毎の稀事象なので steady-state スループットは lookup が決めるが、合成 Fmax は worst
    reg→reg＝この fill 経路で頭打ち。
  - つまり「他ラインを見ない」要求は**満たしている**（lookup は cur 1 本のみ、外れ＝即ミス）。それでも
    **T1 に勝てない理由は 2 つ**：(1) 安全のため lookup でも tag 比較（validate）は必須で、これは 1 ライン
    でも T1 と同等の深さ。(2) 2 ライン常駐 + swap + fill 判別の**構造コスト**が fill 経路に出る。
  - **本当の速度の源は「タグを減らす」**：T1（整列窓）は 16 ページを**単一窓タグ 1 個 + フラット 16:1
    index 配列**で表し、ライン毎タグ・ライン判別・swap を全部消して 464 MHz。**決め打ち（どのラインか）
    ではなく、比較対象のタグ数を減らし構造をフラットにするのが効く**。
  - 利点：T7 は **data を current 1 本しか読まない**（T0 は両ライン読んで 2:1）ので**動的電力は微有利**。
    また 2 ラインが 16 整列でなくてよい（T1 は要整列）柔軟性はある。総合では「Fmax は T0 級、power 微利、
    柔軟性あり、ただし T1 が速度・面積で上」。
- **T2（sequential pointer）は indexed 系で最遅・最大**。ポインタ優先論理（mux2i）が段数・面積を増やすだけで
  worst-case 利得なし（PWC の P3、IOTLB の T7 と同じ「予測/ポインタは単一サイクル Fmax を縮めない」結論）。
- **T5（16-way FA CAM）は最大面積（53k）・遅い**。16 比較器 + 16 入力優先 + 16:1 mux。任意アクセスに
  強いが高コスト。**構造化変種が削減対象とする基準**。T0/T1 はこれに対し面積 **−35〜39%**、Fmax **+40〜89%**。
- **T8（優先を 2:1 mux 直列カスケードに埋め込む別実装）は、構造指標で FA 系の最悪**：論理段数 **11
  （T5 の 10 より深い）・セル数 4,304・面積 53,787 とも最大**。`match[0]?spa[0]:match[1]?spa[1]:…`
  の前向きネストが **16 段の 2:1 mux 依存鎖**になり、abc が AOI 化しても 11 段残る。「優先を mux 網に
  埋めるとさらに深い」というユーザ予想は**段数・面積では実証**された。
  - ただし **post-place Fmax は 284.3 MHz と T5(245.6) を僅かに上回った**（段数は逆に深いのに）。理由は
    **ファンアウト**：T8 のカスケードは各段が次段だけを駆動する**低ファンアウトの鎖**でリサイザが綺麗に
    サイジング/バッファできる。一方 T5 の優先エンコーダは「下位全 match の OR」が**多数の win ゲートへ
    高ファンアウトで分配**され、repair 後も遅延が残る。**段数が少ない＝速い、とは限らない**好例
    （fanout が効く）。とはいえ両者とも T6 の 368 MHz には遠く及ばない。
  - 結論：T8 は「優先を mux に埋める」最悪構造（最深・最大）。**優先論理を持つ限り（T5 でも T8 でも）
    T6 には勝てない**＝速さの源は構造の組み替えでなく**優先論理そのものの除去（one-hot 前提）**。
- **T6（one-hot 前提で優先エンコーダ廃止）は T5 を Fmax +49.8%（245.6→367.8 MHz）改善**。VPN は高々
  1 エントリにしかキャッシュされない（fill が重複タグを作らない）ので **match は one-hot**＝T5 の優先
  エンコーダ（"lowest index wins" の直列鎖）が**不要**。SPA を **AND-OR の one-hot mux**
  `spa = Σ_i (match[i] ? spa[i] : 0)`（平衡 OR ツリー）にでき、**論理段数 10→7**、cells 4,089→3,729 と
  クリティカルパスが浅くなる。面積はほぼ不変（−1.1%：storage DFF 支配で、削れた優先論理は面積比では
  小さいがパス上にあったため Fmax に大きく効く）。**賭け = タグ一意（IOMMU の通常動作で成立）**。
  外れ（重複タグ）時は出力が複数 SPA の bitwise-OR になり得るので、fill 側で既存タグの上書き/無効化に
  より one-hot 不変量を保つのが前提（通常の TLB fill 動作）。**T5 を採るなら T6 にすべき**という明確な結論。

---

## 2.5 PWC 結合型 (combined) vs 分離型 (split) — nested 2-stage の VS PWC 構成

PWC の「タグ構成」とは別軸の探索：nested 2-stage（VS-stage + G-stage）で VS テーブルの
page-walk cache を **どう作るか**。詳細・スクリプトは `results/pwc_combined_vs_split.md` /
`syn/pwc_compare.py`、ワークスペース `syn/pwc_cmp/`（gitignore）。

- **combined（現行設計）**：VS PWC が **G-stage 解決済みの SPA** を格納。VPN ルックアップ→ヒットで
  即 SPA（**1 ルックアップ**）。`vml2(1)+vml1(2)` が SPA を持つ。
- **split**：VS PWC は **GPA**（VS PTE の中身＝次 VS テーブルの GPA）を格納。VPN→GPA を引き、その GPA で
  **別の G-PWC を連鎖ルックアップ**して SPA（**2 段連鎖**）。VS テーブル用 G-PWC を新設し walk FSM に
  連鎖制御を追加。

### 測定（VS-stage ルックアップ部のみ単体合成・同一フロー hd / post-opt @2.5ns target）
| 方式 | Fmax [MHz] | area [µm²] | power [mW] |
|---|---|---|---|
| combined（VPN→SPA, 1 lookup） | 400.0 | 10,242 | 4.356 |
| split（VPN→GPA →連鎖 G→SPA） | 406.5 | 20,048 | 8.680 |
| **Δ split vs combined** | **+1.6%** | **+95.7%** | **+99.2%** |

### 解釈
- **面積・電力がほぼ 2 倍（+96% / +99%）**＝ロバストな主結果。split は VS PWC に加えて
  **GPA→SPA 用 G-PWC を丸ごと追加**するため（一次見積もり「VS キャッシュ部がほぼ倍 +~15k µm²」と一致）。
- **Fmax はこの分離測定では差が出ない**（両者 ≥400MHz）：モジュールが小さく slack が大きいので連鎖 2 段目の
  段数増がまだ律速にならず resizer が両方 2.5ns を閉じる。**フルエンジンでは PWC ルックアップが
  クリティカルパス近傍なので、連鎖 2 段目は Fmax を負方向に効かせる見込み**（厳密値はフルエンジン版が必要）。
- **本タスク（単一コンテキスト・静的・VS テーブルは少数で高再利用）では split の機能的利得はゼロ**
  （steady state はどちらもキャッシュヒットで追加メモリアクセス 0）。→ **combined が面積/電力で約 2 倍有利・
  性能同等以上＝圧倒的に combined 優位**。
- **split が逆転するのは多コンテキスト/多 VM で G 翻訳を共有する場合**（本タスクでは該当せず）。

### 測定の限界（正直に）
- VS ルックアップ構造のみの**分離測定**。フルエンジンの split はさらに walk FSM の連鎖制御
  （VS ヒット→G ルックアップ→ミス時 G サブウォーク）が要り、オーバーヘッドは本測定（≒下限）**以上**。
- データリーフ経路（IOTLB + データ用 G-PWC）は両者不変。差は VS テーブル caching のみ。

---

## 2.6 マルチレベル PWC の引き方：並列+優先 vs 直列（リーフ近い順）

VS PWC を L1（tag=VPN[26:9] 18b）/ L2（VPN[26:18] 9b）/ root（レジスタ）で持つとき、**どう引くか**の
比較（キャッシュ構造とは別軸）。詳細 `results/pwc_level_lookup.md`、`syn/pwc_level_compare.py`、
`pwc/pwc_lvl_{par,seq}.sv`。
- **parallel（並列+最リーフ優先, 現行）**：3 レベル同時ルックアップ→near>mid>root の優先 mux、**1 cycle**。
- **sequential（直列, リーフ近い順）**：near→（ミス時）mid→（ミス時）root を 1 レベル/サイクル、FSM で **1–3 cyc**。

| 方式 | func | Fmax [MHz] | area [µm²] | DFF | depth | レイテンシ |
|---|---|---|---|---|---|---|
| parallel + priority | ✅ | **416.8** | **10,167** | 248 | 7 | 1 cycle |
| sequential（leaf-first） | ✅ | 375.6 | 12,508 | 303 | 7 | 1–3 cyc |
| Δ seq vs par | | −9.9% | +23.0% | +22% | 同 | +latency |

- **本構成では parallel が全軸で勝ち**。3 レベルとも tag 幅/内容が違い**比較器を共有できない**ので直列にしても
  比較器も storage も減らず、**FSM オーバーヘッド（DFF +55）と追加レイテンシだけが乗る**。期待した「1 レベル/
  サイクルで論理が浅く」も**実測段数は同じ 7**（並列の優先 mux は 3 レベルでは浅い）。
- sequential が有利になり得るのは**動的電力のみ**（steady で near 常時ヒットなら mid/root の比較を使わない）
  だが、flat-activity 見積もりでは出ず、FSM 常時稼働が相殺。レベル数が少ない本構成では面積/Fmax/レイテンシの
  確実な不利を覆せない。直列が効くのは「多レベルで 1 個の大比較器を共有して面積を削れる」構成（本タスク非該当）。
- → **現行 iommu_top の並列+most-complete-hit が妥当**。

---

## 3. cross-cutting 技法（X1–X6）の扱い

実装した core variant にどう折り込まれたか／なぜ別 variant にしなかったか:

- **X1 precomputed select（fill で比較・lookup は純 mux）**: lookup index が**アドレス由来で既知**の時のみ
  有効。IOTLB の **offset 選択（T0/T1/T3/T4 の low-bit mux）が正にこれ**＝既に全 indexed 変種が採用済み。
  一方 **runtime tag の FA（P0, T5）には適用不可**（どのエントリが当たるかは lookup 時の tag 次第で fill 時に
  決められない）。よって独立 variant は作らず、indexed 変種に内包。
- **X2 partial-tag match（数ビット先行比較 + 残りを並列/次段検証）**: 段数を僅かに削るが、2 エントリ/2 ライン
  では元の比較が浅く（17–24b 1 段の等価比較）利得が小さい。大連想度（T5 16-way）でこそ効くが、T5 自体が
  非推奨なので不採用。設計空間として記載。
- **X3 pipelined 2-stage（stage1 比較 / stage2 SPA 読み, +1 レイテンシ）**: 段数を半減でき Fmax を更に上げ得るが、
  本研究の最速群は既に 460–615 MHz で IOMMU の 400 MHz spec を満たすため**過剰**。+1 レイテンシは
  本ワークロード（メモリ律速・隠蔽可能）では throughput に無害だが面積/制御増。必要なら P4/T1 に適用可能。
- **X4 comparator 構造（linear-OR vs balanced tree）**: 2 エントリ/2 ライン/16-way とも、合成器（abc）が
  優先/OR を自動で平衡木化するため RTL での区別はほぼ消える。T5 の 16 入力優先 OR が tree 化された
  （段数 10 に収束）のがその例。RTL 段での明示は不要。
- **X5 set-associative（2 ライン × 8 を set とみなす）**: **T0 が実質これ**（ライン = set, offset = way 直接選択）。
- **X6 FIFO / streaming tagless（窓内タグレス）**: タグを持たないと**ミス検出ができず逸脱時に誤ヒット**する。
  「常に当たる」前提は IOMMU の安全要件（誤翻訳は不可）に反するため**却下**。最低限の窓タグ比較（= P2/T1）が
  タグレスの実用的下限。

---

## 4. 検討した設計空間（design space considered, 却下含む）

完全性のため、思い付いた組織を全列挙（◯=合成済 / △=設計空間で議論 / ×=却下）:

**PWC**
- ◯ P0 2-way FA（基準）
- ◯ P2 even-aligned window — **最小面積・準最速（推奨）**
- ◯ P4 speculative / LSB-direct-mapped — **最速**
- ◯ P1 base+delta — 減算器が深く遅い（反面教師）
- ◯ P3 sequential pointer — worst-case 利得なし
- △ high-bit direct-mapped — PWC tag に良い index 場が無い（LSB index = P4 に帰着）
- △ X2 partial-tag / X3 pipeline — 2 エントリでは利得小
- × X6 tagless FIFO — 逸脱時に誤ヒット（安全性違反）
- × content-addressable hash — 2 エントリに過剰、ハッシュ衝突管理が無駄

**IOTLB**
- ◯ T0 line baseline（現行 / set-assoc 相当 = X5）
- ◯ T1 aligned single-window — **最速・小さめ（推奨）**
- ◯ T3 speculative — T1 と合成等価（知見）
- ◯ T4 base+offset 連続圧縮 — **最小面積**だが加算器で最遅
- ◯ T2 sequential pointer — 最遅・最大（不採用）
- ◯ T7 line predictor（current 1 本だけ参照・外れたら即ミス・ダブルバッファ swap）— 335 MHz, T0 同等。
  「他ラインを見ない」要求は実装済み。lookup は浅いが合成 Fmax は **fill 経路（2 ライン判別）**で頭打ち、
  かつ安全のため validate 比較は残る。速さの源はタグ数削減のフラット構造 T1（power だけ片側 read で微利）
- × validate を省いた決め打ち read（純予測, 比較なし）— 予測外れで誤 SPA＝誤翻訳（安全性違反）で却下
- ◯ T5 16-way FA CAM — 最大・基準（任意アクセス用, 優先エンコーダあり）
- ◯ T6 16-way FA, one-hot 前提（優先エンコーダ廃止）— **T5 比 Fmax +50%**。FA を採るならこちら
- ◯ T8 16-way FA, 優先を 2:1 mux 直列カスケードに埋込 — 段数11/面積最大＝FA 系最悪構造。
  post-place Fmax は低 fanout で T5 を僅かに上回るが T6 には遠く及ばず。優先を持つ限り T6 に勝てない
- △ 4 ライン × 4 / 8 ライン × 2 等の line/way 配分スイープ — T0/T1 の中間、別途スイープ可
- △ X2 partial-tag（16-way 用） / X3 pipeline — 大連想度で有効だが本タスク非対象
- △ base+offset を **超ページ単一エントリ**化 — 粒度が粗すぎ（4kB ストリームには不適）
- × tagless ストリーミング — 安全性違反（誤ヒット）
- × ハッシュ連想 / Bloom 前段 — 16 エントリに過剰、誤判定管理が無駄

---

## 5. 推奨

**前提ワークロード = 連続 IOVA・連続物理・単一コンテキストの 800GbE ストリーム。**

| 観点 | PWC 推奨 | IOTLB 推奨 |
|---|---|---|
| **最小面積** | **P2 even-window**（6,068µm²） | **T4 base+offset**（12,090µm², 連続物理が前提なら） |
| **最高 Fmax** | **P4 speculative**（615.8 MHz） | **T1 aligned-window**（464.2 MHz） |
| **本ワークロード総合** | **P2** または **P4** | **T0/T7**（境界跨ぎに強い）, T1 は 16 整列保証時のみ |

- **PWC**: 連続ストリームでは **P4（speculative/LSB 直接）か P2（整列窓）** が最良。P4 は最速、P2 は最小。
  どちらも「隣接ペア」の賭けで、外れても compare で正しくミス（フォール健全）。**P1 の算術窓は避ける**
  （減算器が致命）。
- **IOTLB**: T1（aligned window）は **速度・面積・段数では最良だが、16(=128B)整列を前提**とし、連続
  ストリームが境界を跨ぐと実効容量が激減する（実測 5/16, 上記）。**coalescing が保証するのは 8 整列まで**
  なので、**第一候補は現行 T0（2 ライン×8）または独立 2-tag の T7**（境界跨ぎに強い、13/16）。T1 は
  「128B 整列が保証できる」特殊条件でのみ。**T4 は連続物理が強く保証できる場合のみ**（面積 1/3 だが
  127 MHz＝400MHz spec 未達なので単独採用不可、パイプ化前提）。**フル連想（FA）が要る場面なら T5 ではなく
  T6**（タグ一意⇒優先エンコーダ廃止で +50%、245→368 MHz）。
- **賭けの安全性**: 連続性に賭ける全変種（P2/P4/P1/P3, T1/T2/T3/T4）は、外れても**最低限のタグ比較で
  正しくミス化**し、IOMMU 本体の walk フォールバックへ落ちる（遅くなるだけで誤翻訳しない）。tagless
  系（X6）だけは誤ヒットの危険で却下。

### 現行設計との対応
現行 IOMMU の IOTLB は **T0（line baseline）**相当で、これは **coalescing の 8 整列保証と一致し境界跨ぎに
強い**妥当な選択。T1（aligned window）は Fmax +35%・面積 −6%・段数 7→5 と魅力的だが **128B 整列前提**で、
跨ぐと実効容量が落ちる（実測）ため、整列が保証できる特殊条件でのみ。PWC は現行が combined SPA を 1 段 mux
で返す構造で、本研究の **P2/P4** の知見（算術を避け、index 直接選択／speculative read を使う）が適用できる。
nested の VS PWC は **combined（SPA 格納・1 ルックアップ）が split（GPA→連鎖 G→SPA）に対し面積/電力 約 2 倍
有利・性能同等以上**（§2.5）で、単一コンテキスト・静的の本タスクでは combined が明確に優位。
