# IOMMU アーキテクチャ探索シミュレータ 設計書

TASK2 のアーキテクチャ探索用シミュレータの設計仕様（人が読む正典）。
背景・設計判断の根拠は `design_premises.md` を参照。実装は Python、コード/コメントは英語。
本書と `simulator_usage_manual.md` が **インターフェースの契約**で、Claude Code はこれに準拠して構築する。

---

## 1. 目的・スコープ

このシミュレータが答える問い:
- **3a/3b**：キャッシュ無/有でのアドレス変換レイテンシ（cycle）。
- **3c**：wire rate 維持に必要な並列ウォーク数 N（バッファ無限と仮定）。
- **3d**：wire rate 維持に必要な最小バッファ（ウォーカー十分と仮定）。
- **最小HW**：wire rate を満たす最小のハードウェア資源（N・バッファ・メモリ outstanding 等）。
- **PPA**：正規化した面積・電力を **Pareto＋表** で比較し、アーキの良し悪しを議論。
- **メモリ/IOブリッジへの性能要求**：peak outstanding・帯域・IOブリッジバッファ。

設計方針：**後で数アーキを SystemVerilog RTL 化し sky130 で論理合成する**ことを見据え、シミュレータを RTL と地続きに作る（§12）。本シミュレータのスコープは「RTL対応の探索sim」まで。RTL実装＋合成は次フェーズ別作業。

---

## 2. モデル化方針

- **全時間を cycle に統一**（400MHz, 1cycle=2.5ns）。アーキ間を同じ物差しで比較。
- **サイクル近似イベント駆動**：各処理（ルックアップ/調停/メモリ/パイプライン段）を**設定可能なサイクル数**で表現し、イベントキュー（時刻=cycle）で進行。
- **config = 仕様**：1つの config が sim を駆動し、そのまま将来の RTL パラメータ表になる（RTLで実際にパラメータ化する項目だけを持つ）。

---

## 3. システム・固定条件

`design_premises.md §2` 参照。要点：実効 100 GB/s、4kB、変換 24.41 M/s、到着 16.4 cycle、mem 100ns=40cycle。
ベースライン前提（ネスト対応・連続IOVA・hugepage連続・単一文脈・ピン留め・メモリ十分）も同書 §7。

---

## 4. アーキテクチャ・パラメータ（config）

config は構造化データ（Python dataclass、YAML/JSON ロード可）。各フィールドは将来の SV パラメータに対応。

### 4.1 構成/モード
| param | 値 | 説明 |
|---|---|---|
| `mode` | bare / s1_only / s2_only / nested | 変換段構成 |
| `superpage` | off / 2M / 1G | スーパーページ対応 |

### 4.2 キャッシュ（各 entries / assoc / tag_fields）
| キャッシュ | 主パラメータ | 備考 |
|---|---|---|
| `iotlb` | entries, assoc | 結合 IOVA→SPA（コアレッシング充填） |
| `s1_pwc` | per-level entries/assoc (L2,L1) | guest 上位。root はレジスタ（パラメータ無） |
| `s2_pwc` | per-level entries/assoc | G-stage 上位。root はレジスタ |
| `table_gpa_cache` | entries, assoc | guest 表ページの GPA→SPA |
| `data_gpa_cache` | **on/off**, entries, assoc | S2データ結果の独立キャッシュ。invalidation 時に有効化推奨 |
| `ddtc` | entries(size), assoc | デバイス文脈 |
| `pdtc` | entries(size), assoc / disabled | プロセス文脈（PASID未使用なら disabled） |
| `msi_cache` | entries | 割り込みリマップ（性能は無視可） |
| `lookup_mode` | parallel / sequential / hybrid | ルックアップ並列性（latency/energy に影響） |
| `walk_trigger` | demand / predictive | ミス時ウォーク or ミスしそうなら先行 |
| `coalesce_factor` | 既定8 (64B/8B) | leaf まとめ取り幅 |

「連想度」「タグ」の方針は §5。tag_fields は各キャッシュで文脈タグ（device_id/PASID/VMID）を含む。

### 4.3 ウォーカー / パイプライン
| param | 説明 |
|---|---|
| `num_walkers` | 同時発行ウォーク本数（**None=無限→必要数を実測**） |
| `walk_pipeline_depth` | ウォークエンジンのパイプライン段 |
| `lookup_cycles` | キャッシュルックアップのサイクル（lookup_mode 依存） |
| `arbitration_cycles` | 調停サイクル |
| `hit_latency_cycles` | ヒット時の完了サイクル |

### 4.4 バッファ
| param | 説明 |
|---|---|
| `iommu_req_buffer` | IOMMU が同時受付できるリクエスト数（**None=無限→peak実測**） |
| `io_bridge_buffer` | I/Oブリッジのバッファ（4kBデータ保持。性能要求算出） |

### 4.5 プリフェッチ
| param | 説明 |
|---|---|
| `prefetch` | off / next_line / stride / rpt / dcpt / sms |
| `prefetch_distance` | 先読みページ数 |
| `confidence_threshold` | 信頼度throttle（ランダムで自己無効化） |

### 4.6 メモリ
| param | 説明 |
|---|---|
| `mem_latency_cycles` | 既定40 |
| `mem_max_outstanding` | None=無限。上限で律速を見る |
| `bank_parallel` | on/off（バンク並列） |
| `coalescing_effective` | on/off（連続アドレスで行内まとめ取りが効くか） |

### 4.7 ワークロード / 感度
| param | 説明 |
|---|---|
| `iova_pattern` | sequential / stride(k) / random |
| `data_gpa` | sequential / random（ゲストバッファのGPA連続性） |
| `n_requests` | トレース長 |
| `invalidation` | rate, target(s1/s2/both), granularity(page/range/context) |
| `fault_rate` | フォルト発生頻度 |
| `context_switch_rate`, `n_devices`, `n_pasids` | 文脈スイッチ頻度と同時文脈数 |

### 4.8 クロック / P&A tech（§10）
`clock_mhz`(400), `cycle_ns`(2.5)／正規化P&A重み（§10）／`scale_factor`（絶対化用、任意）。

---

## 5. キャッシュ構成と連想度・タグ方針

`design_premises.md §「ネスト時キャッシュ構成」` 準拠。

- **結合IOTLB**（IOVA→SPA, コアレッシング充填）＋ **S1 PWC**（L2/L1）＋ **S2 PWC** ＋ **表GPA→SPA** ＋ **DDT$/PDT$/MSI$**。
- **root はレジスタ化**（単一文脈で不変、ミスゼロ）。
- **独立データGPAキャッシュは on/off**：off=結合IOTLBのみ（streaming無効化なしで最小）、on=ステージ分離でS2結果を温存（S1側 invalidation で有効）。
- **連想度方針**：極小の上位/文脈（≤~16〜32）＝**フル連想**（CAM、競合ゼロ、必ずヒット）／大きめの leaf/data（~64）＝**4-way セット連想**（ストリーム間競合回避）。
- **タグ方針**：全変換キャッシュに**文脈タグ（device_id/PASID は S1、VMID は S2）**を付け、文脈切替で**フラッシュ不要**に。leaf=全ページ番号、上位=VPN/GPA prefix＋level。
- **ルックアップ**：`lookup_mode` で 逐次（IOTLB先→ミス時PWC）/並列（全引き＋最完成度優先）/折衷（IOTLB先→ミス時PWC並列）。並列時は**最も完成度の高いヒットを選ぶ固定優先**。
- **無効化**：文脈タグ＋世代カウンタで**選択的・O(1)一括無効化**、バッチ/遅延（lazy）を既定。

---

## 6. タイミングモデル（cycle）

各処理を cycle で表現し、**想定RTLパイプライン段に対応**させる:
- メモリ1アクセス = `mem_latency_cycles`(40)。
- キャッシュルックアップ = `lookup_cycles`（lookup_mode 依存、1〜数 cycle）。
- 調停 = `arbitration_cycles`。
- ウォークは `walk_pipeline_depth` 段＋メモリ往復の連鎖。
- **on-demand ミスペナルティ**（cycle）を**ミス種別ごとに分解して出力**：
  - IOTLBヒット：lookup のみ／MSHR相乗り：ライン完了まで待ち／PWC全ヒット：1 leaf=40／PWC部分：+40/欠落段／フルコールド：単段3×40・ネスト最大15×40。
- これらの cycle 値は**早期の試し合成（sky130で2.5nsに収まる規模）から更新**する（§12）。

---

## 7. ワークロード / トレースモデル

- トレース = `(arrival_cycle, iova, device_id, pasid)` の列。`iova_pattern`/`data_gpa`/文脈/イベント率から生成。
- **invalidation/fault/context-switch はイベントとして注入**（指定 rate で）。
- **トレース形式は RTL テストベンチで再利用可能**な形（CSV等）でエクスポート可能にする（§12）。

---

## 8. メトリクス（全て cycle ベース）

- スループット（M/s）と `wire_rate_met`（bool）。
- **peak_walks（=必要N, 3c）**、**peak_buffer（=必要バッファ, 3d）**。
- メモリ：**peak outstanding**、**消費帯域(GB/s)** → メモリ性能要求。
- **io_bridge_buffer peak** → IOブリッジ性能要求。
- 各キャッシュの hit/miss、**accesses/translation**。
- レイテンシ：平均/最大/p99（cycle と ns）。
- **ミスペナルティ：種別別分布（cycle）**。
- P&A：per-module 面積(GE)・動的/静的電力(正規化)・**エネルギー/変換**、内訳。

---

## 9. 最小HWリソース探索

- **wire rate 達成の判定**：cold-start を除外した**定常で stall ゼロ＋小マージン**（達成スループット ≥ 24.41 M/s × (1+margin)）。
- 手順：構成を固定し、`num_walkers`・`iommu_req_buffer`・`io_bridge_buffer`・`mem_max_outstanding` 等を**スイープし、達成する最小値**を探索。
- 3c は `iommu_req_buffer=None` で `peak_walks`、3d は `num_walkers=None` で `peak_buffer` を実測（無限リソースで測る）。

---

## 10. 電力・面積モデル（プロセス非依存・内訳付き）

`design_premises.md §13` の方針。**絶対値はプロセス依存だが部品の相対比はほぼ非依存** → 正規化単位で出し、1スケール係数で絶対化。

### 10.1 面積（ゲート等価 GE, 1GE=2入力NAND）
正規化プリミティブ（調整可）：SRAMビット 0.2 / CAMビット 0.6 / FFビット 6 / ロジック=ゲート数 / 配列周辺 ×1.3。
面積 = Σ部品 [ (SRAMbit×0.2 ＋ CAMbit×0.6 ＋ FFbit×6)×周辺 ＋ logicGE ]。**per-module 出力**。

### 10.2 電力（正規化エネルギー単位, 1単位=NAND1スイッチ相当）
- 動的 = Σ部品( 活動回数 × アクセスエネルギー ) ＋ FFクロック( FFビット × クロックエネルギー × 稼働cycle )。
- 静的 = Σ( SRAM/CAM/FFビット ＋ ゲート ) × 相対リーク重み。
- DRAM アクセスのエネルギーは**別枠集計**（メモリサブシステム側）。
- **per-module の動的/静的内訳**を出力（円グラフ化可）。

### 10.3 出力
部品別（iotlb / s1_pwc / s2_pwc / table_gpa / data_gpa / ddtc / pdtc / msi / walkers / arbiter / buffer / control）の面積(GE)・電力・**エネルギー/変換**、合計、内訳。任意で `scale_factor` を掛け絶対値化。

---

## 11. PPA 比較・Figure of Merit

`design_premises.md` の整理に準拠。スループットは固定目標なので PPA は「達成下での電力・面積最小化」に縮約。

- **(ゲート)** `wire_rate_met`（§9）。満たさない構成は土俵外。
- **(アーキ効率)** accesses/translation（↓）。
- **(PPA Pareto 2軸)** エネルギー/変換（↓, 正規化）と 面積(GE,↓)。
- **(HWコスト副指標)** 必要 N / buffer / mem-outstanding（↓）。
- 出力：wire rate 達成構成群で **面積–エネルギー/変換 Pareto＋表**。補助スカラー **面積 × エネルギー/変換** も可。

---

## 12. RTL / sky130 対応方針（次フェーズへの地続き）

- **config = SVパラメータ**：sim config の各フィールドが SystemVerilog の `parameter` に対応（cache size/assoc, N, pipeline_depth, buffer 等、RTL実装可能な項目のみ）。
- **cycle モデル = RTL パイプライン**：§6 のサイクルコストは想定SV段に対応。早期試し合成で得た「2.5nsに収まる規模」で更新する手順を持つ。
- **モジュール分解 = RTL階層**：§13 の sim モジュールが SV モジュールに1対1対応 → 合成の per-module レポートと比較可能。
- **per-module 正規化PPA ↔ 合成**：estimator の per-module 出力を sky130 合成(OpenLane)の per-module 面積/電力と突き合わせ、**校正係数を fit**（凍結予測→合成→誤差分解→校正）。
- **候補config出力**：Pareto/表から数点（knee／最小面積でwire rate達成／最小電力）を選び、**その厳密 config を SVパラメータ表として出力**。
- **トレース再利用**：sim のトレースを RTL テストベンチ刺激に流用（同一入力で sim↔RTL 比較）。
- スコープ外（次フェーズ）：SV RTL 実装、OpenLane 合成、estimate↔synth 校正の実行。

---

## 13. 実装構成（モジュール、差し替え）

エンジンとポリシーを分離。各ポリシーは ABC で差し替え可能（既存 `iommu_sim/` の発展）。RTL階層に対応:
- `config` … 全パラメータ（dataclass＋YAML/JSON）。
- `engine` … イベント駆動コア（バッファ/ウォーカー/MSHR/メトリクス）。
- `caches` … CacheABC＋SetAssoc/CAM、置換、世代無効化。
- `walker` … WalkCostModel（mode別: bare/s1/s2/nested）、walk_trigger。
- `prefetch` … Prefetcher（off/next_line/stride/rpt/dcpt/sms＋confidence）。
- `memory` … latency/outstanding/bank/coalescing。
- `workload` … トレース生成＋イベント注入＋エクスポート。
- `estimator` … 正規化 area/power（per-module, 内訳）。
- `sweep` … 最小HW探索＋Pareto生成。
- `metrics` … 全指標。
新ポリシーは ABC を継承して config で選択。`engine` はポリシー変更で触らない。

---

## 14. 検証

- `design_premises.md` の A〜E トレンドを再現（no-cache: N≈8/buffer≈8、PWC+coalescing: ~0.13 mem/page・N≈1、random で崖、finite で wire rate 未達）。
- リトル則クロスチェック（N ≈ 平均レイテンシ ÷ 到着間隔）。
- 内訳合計 = 総計（面積・電力）。
- 凍結予測 JSON（config ハッシュ付き）を出力し、後の合成比較に使う。

---

## 15. 用語
`design_premises.md §15` 参照（IOVA/GPA/SPA, IOTLB/PWC/DDT$/PDT$/MSI$, コアレッシング, MSHR, Sv39/Sv39x4, ATS/PRI, RDMA, MR）。