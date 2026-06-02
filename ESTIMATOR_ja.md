# IOMMU 面積・電力エスティメータ 説明書（ESTIMATOR_ja.md）

本ドキュメントは `iommu_sim/estimator.py` が実装する**一次近似（first-order）の
面積・電力モデル**を説明する。目的は **アーキテクチャ間の相対比較**と、**後段の
論理合成（sky130）結果による較正（calibration）**であり、**絶対値のサインオフ
ではない**。

---

## 1. 全体方針

- **非侵襲（non-invasive）**：エスティメータはシミュレータの `sim.run()` 後に、
  静的コンフィグ（`EstimatorConfig`）と既存のアクティビティ計数（`Metrics` と
  各コンポーネント）を**読むだけ**。イベントループやポリシ挙動には一切介入しない。
- 入力 = 「静的コンフィグ」+「シミュレータが既に生成する活動量」。
- 出力 = コンポーネント別の面積（µm²）・電力（mW）、合計、変換1回あたりエネルギー
  （pJ/translation）、および凍結（freeze）した予測レコード（JSON）。
- 単位：面積 µm²（合計は mm² も）、電力 mW、エネルギー pJ。

### コンポーネント分解
IOTLB / PWC / DDT$ / PDT$ / MSI$ / トランザクションバッファ / ウォーカ論理 /
制御・グルー論理。

---

## 2. シード技術定数（sky130 SEED, `TechParams`）

**すべて仮値（プレースホルダ）**。コード中で `REFINE` と明記し、後で OpenRAM /
CACTI / 合成結果で較正する。

| 定数 | 値 | 意味 / 較正元 |
|---|---|---|
| `vdd_v` | 1.8 V | sky130 コア電圧 |
| `freq_hz` | 400 MHz（2.5 ns） | 動作周波数 |
| `sram_bit_um2` | 1.0 | 6T SRAM ビット面積（OpenRAM sky130 から較正） |
| `cam_bit_um2` | 4.0 | フル連想タグ照合用 CAM セル（SRAM の約4倍） |
| `ff_bit_um2` | 25.0 | DFF 1ビット（sky130 の DFF は大きい） |
| `gate_um2` | 3.0 | NAND2 換算の平均ゲート |
| `sram_e_access_pj` | 1.0 | アレイ1アクセス当たりエネルギー（配列サイズ依存、CACTI で較正） |
| `ff_e_clk_pj_per_bit` | 0.02 | FF 1ビット・1サイクル当たりのクロック/トグルエネルギー |
| `leak_nw_per_bit` | 0.05 | 記憶ビット当たりリーク（nW） |
| `leak_nw_per_gate` | 0.1 | ゲート当たりリーク（nW） |
| `peripheral_overhead` | 1.4 | SRAM デコーダ/センスアンプ等の周辺オーバーヘッド係数 |

> sky130（130nm）では**動的電力が支配的**になる想定で、リークは小さく設定して
> いる（出力でも dyn ≫ stat になる）。

### アクセスエネルギーのサイズ依存
`sram_e_access_pj` は基準配列サイズ `e_access_ref_bits`（=4096 bit）での値とし、
アレイが大きいほど増える**緩やかな依存**を入れている：

```
access_energy_pj(array_bits) = sram_e_access_pj * max(1, sqrt(array_bits / 4096))
```

平方根則は CACTI 的な配線・ビット線容量の増加を粗く模したもの。**REFINE 対象**。

---

## 3. 構造ビット幅（`StructParams`）

各構造のタグ/データ幅、制御状態ビットを保持する。デフォルトは Sv39 を想定した
妥当値で、容易に上書き可能。

- IOTLB：tag = VPN(27) + valid + ASID(16)、data = PPN(28) + 属性(8)
- PWC：tag = 部分VPN(18) + level(2) + valid、data = 次段ベース PPN(28)
- DDT$ / PDT$ / MSI$：device_id / process_id / MSI index をタグに、対応ベース PPN
  + フラグをデータに（既定値、上書き可）
- トランザクションバッファ：`buffer_ctrl_bits = 96`（**制御ビットのみ**。後述）
- ウォーカ：`walker_context_bits = 112`（{VPN, level, 現ベースPPN, status, tag}）、
  `walker_control_gates = 400`（FSM/アドレス生成のラフ見積り）
- 制御・グルー：`control_glue_gates = 2000`（トップレベル調停/ルーティング/CSR）

---

## 4. 面積モデル（`AreaModel`）

### キャッシュ（IOTLB / PWC / DDT$ / PDT$ / MSI$）
```
タグ格納が フル連想 → タグビットは CAM セル、データビットは SRAM セル
                       + タグ照合のコンパレータ・ゲート面積
タグ格納が セット/ダイレクト → 全ビット SRAM セル（way 選択ロジックは小）
面積 = セル面積 * peripheral_overhead + コンパレータゲート * gate_um2
```
`entries == 0`（無効化）は面積 0。

### トランザクションバッファ（**設計決定1**）
4 kB のデータペイロードは **I/O ブリッジ側**にあり IOMMU には載らない。よって
バッファは**制御ビットのみ**（tag/ID, IOVA, device_id, process_id, type/length,
status）を保持する。**4 kB データの面積・電力は一切計上しない。**
```
面積 = depth * entry_ctrl_bits * ff_bit_um2      （既定 entry_ctrl_bits = 96）
```

### ウォーカ論理
```
面積 = num_walkers * context_state_bits * ff_bit_um2
       + num_walkers * control_gates * gate_um2
```

### 制御・グルー
```
面積 = control_glue_gates * gate_um2   （小。見積りは StructParams に明記）
```

各コンポーネント面積を合計し、mm² も併記する。

---

## 5. 電力モデル（`PowerModel`）

### 動的（dynamic）
```
動的[pJ/s] = アクセス数 * access_energy_pj(配列ビット数) / シム時間[s]
           + FFビット数 * ff_e_clk_pj_per_bit * freq_hz * 稼働率
動的[mW]   = 動的[pJ/s] * 1e-9      （1 pJ/s = 1 pW = 1e-9 mW）
```
- キャッシュのアクセス数 = `hits + misses + inserts`（`Metrics`/キャッシュ計数）。
- バッファ：書込 = `completed`、FF は毎サイクルクロック（稼働率 1.0）。
- ウォーカ：ゲートトグルは `memory.accesses` に比例、FF クロックは
  `walker_busy_ns / (num_walkers * sim_time)` の**稼働率**でゲーティング。
- 制御：おおよそ `completed` 回トグル（粗い）。

### 静的（leakage）
```
静的[nW] = 総ビット * leak_nw_per_bit + 総ゲート * leak_nw_per_gate
静的[mW] = 静的[nW] * 1e-6
```

### 変換1回あたりエネルギー
```
総エネルギー[pJ] = 総電力[mW] * 1e-3 * シム時間[s] * 1e12
pJ / translation = 総エネルギー / completed
```
動的・静的はコンポーネント別／合計の双方で別々に報告する。

---

## 6. 較正（`CalibParams`）

コンポーネント別の乗数（既定 1.0）。合成後に
```
mult = 実測値 / 予測値
```
で面積（または電力）を一致させる。`CalibParams.fit(predicted, measured, key="area")`
が予測辞書と実測辞書から乗数辞書を生成する。較正済み `CalibParams` を
`estimate(..., calib=...)` に渡せば、以後の予測に反映される。

凍結 JSON（`EstimateResult.freeze(path)`）には `config_hash`（config + tech +
struct + calib の SHA-256）、使用した `tech_params` / `struct_params`、タイムスタンプ、
コンポーネント表・合計が含まれ、合成結果との突合・再現に使える。

---

## 7. 既知のギャップ・限界

- **一次近似であり、相対比較＋較正用**。絶対サインオフには使わない。
- ランダム論理（ウォーカ FSM、制御・グルー）は**ゲート数の粗い見積り**。合成で
  最も振れやすい部分。
- **インターコネクト／クロックツリーは未モデル**（配線容量・バッファ・スキュー
  補償の面積/電力を含まない）。
- **DDT$ / PDT$ / MSI$ は現行の単段ワークロードでは未稼働**。面積とリークは計上
  するが、動的電力は 0（専用アクセス計数が未実装のため）。これらを実シミュレート
  したら、`components` 辞書にヒット/ミス/挿入計数を渡すだけで動的電力が出る。
- DRAM（ページテーブルメモリ）はオフチップのため IOMMU 面積・電力に**含めない**
  （`memory.accesses` はウォーカ活動量の駆動にのみ使用）。
- アクセスエネルギーのサイズ依存は平方根則の仮置き。CACTI 曲線で要較正。
- 技術定数はすべて **sky130 SEED の仮値**。`TechParams` 内の `REFINE` を参照。

---

## 8. 較正の進め方（今後）

1. 各構造を sky130 で論理合成（または SRAM は OpenRAM、メモリは CACTI）。
2. コンポーネント別の面積・電力を取得。
3. `CalibParams.fit()` で乗数を算出し、`estimate(..., calib=fitted)` に適用。
4. `TechParams` のシード定数自体（`sram_bit_um2` 等）も実測へ更新（`REFINE`）。
5. 凍結 JSON の `config_hash` を使って予測と実測を恒久的に紐付け。
