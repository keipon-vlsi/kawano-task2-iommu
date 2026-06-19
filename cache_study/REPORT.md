# PWC / IOTLB ルックアップ・マイクロアーキテクチャ QoR 研究レポート

IOMMU の page-walk cache (PWC) と IOTLB の**タグ構成・fill（登録）論理・lookup 論理**が、
合成後の**論理構造・論理段数・面積内訳・クリティカルパス**にどう効くかを、孤立モジュールで
測定した QoR 比較。全 variant を同一フロー（sky130_fd_sc_hd, Yosys + OpenSTA + OpenROAD
repair）で合成。前提は `ASSUMPTIONS.md`、実行は `README.md`、生データは `results/`。

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
| T2 | sequential pointer（current ライン優先） | ✅ | 293.8 | 37,868 | 10,098 | 843 | 17,128 | 7 | mux2i→o22ai→a211oi→nand4→o21a→nor4b→mux2 | ○連続 |
| T5 | **16-way FA CAM**（基準: 16 比較 + 優先） | ✅ | 245.6 | **53,466** | 14,258 | 1,228 | 23,126 | 10 | clkinvlp→a221oi→nand4→or3→…→a31oi | × |
| **T4** | per-line base+offset（base_ppn + adder, 連続前提） | ✅ | 127.0 | **12,090** | **3,224** | **229** | 6,720 | **28** | …→maj3×2→nor4b×多数→…→adder | ○連続物理 |

- **面積順**: **T4(12,090) ≪ T1=T3(32,572) < T0(34,556) < T2(37,868) < T6(52,874) < T5(53,466)**。

### 考察
- **T4（base+offset）が最小面積（他の 1/3〜1/4）かつ最低 Fmax**。連続物理なら **16×44b の data を捨て、
  ライン毎に base_ppn(44b)+contig bit だけ格納**（DFF 229 vs T0 843 ＝ storage を激減）。代償は
  **`base_ppn + offset` の 44bit 加算器**がパスに乗り、段数 28・127 MHz と最遅。**面積と速度の極端な
  トレードオフ**。連続物理（スーパーページ的）への賭け。外れ（非連続 fill）時は contig flag を落として
  そのラインをミス化（正しく再ウォークへフォール）。
- **T1（aligned window）が最速・小さめ**。16 ページを 1 整列窓とみなし **比較 1 個 + 16:1 index mux**。
  T0 の 2 ライン比較を 1 比較に減らし、offset は元々比較不要なので段数 5。
- **T1 と T3 が完全同一に合成**された（同 Fmax/面積/段数）。**「speculative read」と「aligned window」は
  offset/index 駆動の IOTLB では合成後に等価**（どちらも index で data を引き、tag を並列 validate）。
  重要な知見：この種の最適化は別物に見えて回路は同じになる。
- **T0（現行 line baseline）は中庸**。2 ライン tag 比較（24b ×2）+ 8:1 mux で段数 7・343 MHz。整列を仮定
  しないぶん T1 より 1 比較多い。
- **T2（sequential pointer）は indexed 系で最遅・最大**。ポインタ優先論理（mux2i）が段数・面積を増やすだけで
  worst-case 利得なし（PWC の P3 と同じ結論）。
- **T5（16-way FA CAM）は最大面積（53k）・遅い**。16 比較器 + 16 入力優先 + 16:1 mux。任意アクセスに
  強いが高コスト。**構造化変種が削減対象とする基準**。T0/T1 はこれに対し面積 **−35〜39%**、Fmax **+40〜89%**。
- **T6（one-hot 前提で優先エンコーダ廃止）は T5 を Fmax +49.8%（245.6→367.8 MHz）改善**。VPN は高々
  1 エントリにしかキャッシュされない（fill が重複タグを作らない）ので **match は one-hot**＝T5 の優先
  エンコーダ（"lowest index wins" の直列鎖）が**不要**。SPA を **AND-OR の one-hot mux**
  `spa = Σ_i (match[i] ? spa[i] : 0)`（平衡 OR ツリー）にでき、**論理段数 10→7**、cells 4,089→3,729 と
  クリティカルパスが浅くなる。面積はほぼ不変（−1.1%：storage DFF 支配で、削れた優先論理は面積比では
  小さいがパス上にあったため Fmax に大きく効く）。**賭け = タグ一意（IOMMU の通常動作で成立）**。
  外れ（重複タグ）時は出力が複数 SPA の bitwise-OR になり得るので、fill 側で既存タグの上書き/無効化に
  より one-hot 不変量を保つのが前提（通常の TLB fill 動作）。**T5 を採るなら T6 にすべき**という明確な結論。

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
- ◯ T5 16-way FA CAM — 最大・基準（任意アクセス用, 優先エンコーダあり）
- ◯ T6 16-way FA, one-hot 前提（優先エンコーダ廃止）— **T5 比 Fmax +50%**。FA を採るならこちら
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
| **本ワークロード総合** | **P2** または **P4** | **T1**（= T3） |

- **PWC**: 連続ストリームでは **P4（speculative/LSB 直接）か P2（整列窓）** が最良。P4 は最速、P2 は最小。
  どちらも「隣接ペア」の賭けで、外れても compare で正しくミス（フォール健全）。**P1 の算術窓は避ける**
  （減算器が致命）。
- **IOTLB**: **T1（aligned window）が速度・面積・段数のバランス最良**で推奨。**T4 は連続物理が強く保証できる
  場合のみ**（面積 1/3 だが 127 MHz＝ただし IOMMU 400MHz spec には未達なので、T4 単独採用は不可。
  T4 は「面積最優先かつ別途パイプ化前提」の選択肢）。**フル連想（FA）が要る場面なら T5 ではなく T6**
  （タグ一意 ⇒ 優先エンコーダ廃止で Fmax +50%、245→368 MHz）を使うべき。とはいえ本ワークロードでは
  整列窓 T1 が FA より速く小さいので、IOTLB は T1 が第一候補。
- **賭けの安全性**: 連続性に賭ける全変種（P2/P4/P1/P3, T1/T2/T3/T4）は、外れても**最低限のタグ比較で
  正しくミス化**し、IOMMU 本体の walk フォールバックへ落ちる（遅くなるだけで誤翻訳しない）。tagless
  系（X6）だけは誤ヒットの危険で却下。

### 現行設計との対応
現行 IOMMU の IOTLB は **T0（line baseline）**相当。本研究は、連続 IOVA が強く保証できるなら **T1（aligned
window）**で Fmax +35%・面積 −6%・段数 7→5 が得られることを示す。PWC は現行が combined SPA を 1 段 mux で
返す構造で、本研究の **P2/P4** の知見（算術を避け、index 直接選択／speculative read を使う）が適用できる。
