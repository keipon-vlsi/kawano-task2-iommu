# IOMMU 探索シミュレータ 使い方説明書

`simulator_design_doc.md` のインターフェース仕様に沿った操作マニュアル（人が読む）。
このマニュアルと設計書が**契約**で、実装はこの通りに動くこと。コード/コメントは英語、本書は日本語。

---

## 1. これで何ができるか
- 1構成を流して **3a/3b（レイテンシ）, 3c（必要N）, 3d（必要バッファ）** とヒット率・帯域・ミスペナルティを得る。
- **最小HWリソース**（wire rate を満たす最小の N・バッファ・outstanding）を探索。
- **正規化の面積(GE)・電力**を per-module 内訳付きで見積もり。
- 複数構成を **面積–エネルギー/変換 Pareto＋表** で比較（FoM）。
- 感度実験（IOVA/GPA/invalidation/fault/文脈スイッチを振る）。
- **RTL候補configとトレースをエクスポート**（次フェーズの SystemVerilog 化用）。

---

## 2. インストール・実行
```bash
cd iommu_sim          # 実装ディレクトリ
python3 run.py --config configs/baseline.yaml          # 単一実行
python3 sweep.py --config configs/baseline.yaml --search min_hw   # 最小HW探索
python3 sweep.py --config configs/space.yaml --pareto             # Pareto + 表
python3 -m pytest                                      # 検証テスト
```
- `run.py` / `sweep.py` 本体は **標準ライブラリ + PyYAML** のみで動く（システム python3 で可）。
- **pytest（検証テスト）と matplotlib（Pareto 図）はリポジトリ直下の仮想環境 `.venv` に同梱**。
  テスト/図を出すときは:
  ```bash
  ../.venv/bin/python -m pytest -q                       # 検証テスト
  ../.venv/bin/python sweep.py --config configs/space.yaml --pareto   # 図も生成
  ```
  matplotlib が無い環境では Pareto 図はスキップし、`results.csv` は常に出力する。

---

## 3. config の書き方（全パラメータ）
config は YAML/JSON（または Python dataclass）。主なフィールド（詳細は設計書 §4）:
```yaml
mode: nested            # bare / s1_only / s2_only / nested
superpage: off          # off / 2M / 1G
caches:
  iotlb:    {entries: 64, assoc: 4}
  s1_pwc:   {l2: {entries: 4, assoc: full}, l1: {entries: 8, assoc: full}}
  s2_pwc:   {entries: 8, assoc: full}
  table_gpa:{entries: 16, assoc: full}
  data_gpa: {enabled: false, entries: 64, assoc: 4}   # invalidation時に true 推奨
  ddtc:     {entries: 16, assoc: full}
  pdtc:     {enabled: false}                          # PASID未使用なら無効
  msi:      {entries: 16}
  lookup_mode: hybrid     # parallel / sequential / hybrid
  walk_trigger: demand    # demand / predictive
  coalesce_factor: 8
walkers:   {num_walkers: null, pipeline_depth: 2}     # null=無限(必要数を実測)
buffers:   {iommu_req_buffer: null, io_bridge_buffer: 16}  # null=無限(peak実測)
prefetch:  {algo: off, distance: 16, confidence: 2}   # off/next_line/stride/rpt/dcpt/sms
memory:    {latency_cycles: 40, max_outstanding: null, bank_parallel: true, coalescing_effective: true}
timing:    {clock_mhz: 400, lookup_cycles: 2, arbitration_cycles: 1, hit_latency_cycles: 1}
workload:  {iova_pattern: sequential, data_gpa: sequential, n_requests: 8000,
            invalidation: {rate: 0, target: s1, granularity: context},
            fault_rate: 0, context_switch_rate: 0, n_devices: 1, n_pasids: 1}
pa:        {scale_factor: null}   # null=正規化のまま。値を入れると絶対化
```

---

## 4. 単一実行と出力の読み方
`run.py` の出力（全て cycle ベース、`design_doc §8`）:
- `throughput (M/s)` と `wire_rate_met`：目標 24.41 M/s を満たすか。
- `peak_walks`：必要並列ウォーク数（**3c**）。`num_walkers=null` のとき実測値＝必要N。
- `peak_buffer`：必要バッファ（**3d**）。`iommu_req_buffer=null` のとき実測。
- `mem_outstanding_peak`, `mem_bandwidth (GB/s)`：**メモリへの性能要求**。
- `io_bridge_buffer_peak`：**IOブリッジへの性能要求**（4kBデータ保持）。
- ヒット率（各キャッシュ）, `accesses_per_translation`：アーキ効率。
- `latency avg/max/p99`（cycle と ns）。
- **`miss_penalty`：種別別（IOTLBヒット/MSHR相乗り/PWC全ヒット/部分/フルコールド）の cycle 分布**（on-demand時に重要）。
- `area_GE`（per-module＋合計）, `power dyn/static`（per-module＋合計）, `energy_per_translation`、内訳。

読み方の例：`peak_walks=1, peak_buffer=8, accesses/translation=0.13` なら「PWC+コアレッシングが効きN=1で足り、バッファ8（cold-start含む）」。

---

## 5. 最小HWリソース探索
```bash
python3 sweep.py --config configs/baseline.yaml --search min_hw
```
- `num_walkers / iommu_req_buffer / io_bridge_buffer / mem_max_outstanding` を昇順に振り、**定常 stall ゼロ＋小マージン**で wire rate を満たす**最小値**を出力。
- 3c/3d を個別に出すには `--measure peaks`（無限リソースで peak_walks / peak_buffer）。

---

## 6. スイープ＆Pareto 生成
```bash
python3 sweep.py --config configs/space.yaml --pareto
```
- `space.yaml` に各パラメータの探索範囲を列挙。
- wire rate を満たす構成群で **面積(GE)–エネルギー/変換 Pareto** と比較表（CSV）を出力。
- 補助スカラー（面積×エネルギー/変換）も表に付く。

---

## 7. 感度実験
1軸ずつ振り、best(連続)と worst(ランダム)を必ずペアで（skill `iommu-arch-sweep` 準拠）:
- `iova_pattern: sequential ↔ random / stride`
- `data_gpa: sequential ↔ random`（nested の S2 コアレッシング感度）
- `invalidation.rate` を 0→高、`target: s1/s2`（`data_gpa.enabled=true` で温存効果を見る）
- `fault_rate`, `context_switch_rate`, `n_devices/n_pasids`
- `mode: nested ↔ single`、`superpage: off ↔ 2M`
出力で「どこで wire rate が崩れるか（崖）」を地図化。

---

## 8. コンポーネント差し替え
- 新しいキャッシュ構造：`caches.py` で `CacheABC` を継承し config で選択。
- 新しいプリフェッチ：`prefetch.py` で `Prefetcher` を継承。
- 新しいウォークコスト（例 nested の別モデル）：`walker.py` の `WalkCostModel` を継承。
- `engine.py`（コア）は触らない。

---

## 9. P&A 内訳の見方・絶対化
- 出力は正規化（面積=GE、電力=正規化エネルギー）。**per-module 内訳**で「どの部品が支配的か」を確認。
- `pa.scale_factor` に sky130（または任意ノード）の係数を入れると**絶対値(µm²/mW)に換算**。係数は後の合成で校正（design_doc §12）。

---

## 10. RTL 候補 config・トレースのエクスポート
```bash
python3 sweep.py --config configs/space.yaml --pareto --emit-candidates
```
- Pareto の代表点（knee／最小面積でwire rate達成／最小電力）の**厳密 config を SystemVerilog パラメータ表**として出力（`candidates/*.svh` 等）。
- `--emit-trace` で**トレースを CSV**出力（RTL テストベンチ刺激に再利用）。

---

## 11. 「変えたい X → ここ」レシピ
- ウォーカー数 → `walkers.num_walkers`（null で必要数実測）。
- バッファ → `buffers.iommu_req_buffer` / `io_bridge_buffer`。
- キャッシュ容量/連想度 → `caches.*.entries/assoc`。
- コアレッシング幅 → `caches.coalesce_factor`（1で無効）。
- 段構成 → `mode`、スーパーページ → `superpage`。
- ルックアップ並列性 → `caches.lookup_mode`。
- ミス時/先行ウォーク → `caches.walk_trigger`。
- 無効化時にS2温存 → `caches.data_gpa.enabled: true`。
- メモリ並列上限 → `memory.max_outstanding`。
- 感度（IOVA/GPA/invalidation/fault/switch）→ `workload.*`。

---

## 12. 検証チェック
- `pytest` が A〜E トレンドを再現（design_doc §14）。
- `peak_walks ≈ 平均レイテンシ ÷ 到着間隔` を確認（リトル則）。
- 面積・電力の per-module 合計＝総計。
- 凍結予測 JSON が出力され、config ハッシュ付き（後の合成比較用）。