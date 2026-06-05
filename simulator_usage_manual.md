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
- PDTW/DDTW を発生させる → `caches.pdtc.enabled:true` ＋ `workload.n_pasids`/`n_devices`/`context_switch_rate`（詳細は付録 G）。

---

## 12. 検証チェック
- `pytest` が A〜E トレンドを再現（design_doc §14）。
- `peak_walks ≈ 平均レイテンシ ÷ 到着間隔` を確認（リトル則）。
- 面積・電力の per-module 合計＝総計。
- 凍結予測 JSON が出力され、config ハッシュ付き（後の合成比較用）。

---

# 付録：実装詳解（コード／設定／出力の逐条説明）

`iommu_sim/` の各ファイル・各パラメータ・各出力列が**何を表し、どこで使われるか**をまとめる。
コード/パラメータ名は英語、説明は日本語（リポジトリ規約）。エンジンとポリシーは分離されており、
新ポリシーは「ABCを継承＋configフィールド追加」で差し替える（`engine.py` は触らない）。

## A. タイミングモデル（cycle の内訳）

1サイクル = `1000 / clock_mhz` ns（既定 2.5 ns）。各処理のサイクルは config で可変。

| 量 | 値（既定） | かかる対象 |
|---|---|---|
| `mem_latency_cycles` | 40 | メモリ1アクセス（DRAM 行オープン1回＝100ns）。ウォークは accesses 回**直列**（ポインタ追跡）。 |
| `lookup_cycles` | 2 | キャッシュルックアップ1回（IOTLB/PWC 引き）。`_translate` で計上。 |
| `arbitration_cycles` | 1 | **共有メモリ／AR チャネル獲得の調停**。ウォーク1本につき1回。並列ルックアップ時の最完成度優先エンコーダもここ。 |
| `walk_pipeline_depth` | 2 | **ウォーカー内部パイプライン/FSM段の fill 遅延**（IDLE→ISSUE→WAIT→DECODE→次アドレス計算）。ウォーク1本につき1回。 |
| `hit_latency_cycles` | 1 | ヒット時の完了までの段数。 |

完了時刻の組み立て（`engine.py`）：
- IOTLB ヒット：`t + lookup_cycles + hit_latency_cycles`
- MSHR 相乗り：`max(line完了時刻, t + lookup_cycles)`
- ウォーク：`t + lookup_cycles + (arbitration_cycles + walk_pipeline_depth + accesses × mem_latency)`

> `arbitration_cycles` と `walk_pipeline_depth` はメモリ往復（`accesses × mem_latency`）とは別枠の
> **ロジック遅延**。いずれも seed 値で、試し合成で 2.5ns に収まる段数へ更新する（design_doc §6/§12）。

## B. CLI 詳解

### B.1 `run.py`（単一実行）
| フラグ | 役割 |
|---|---|
| `--config PATH` | 実行する YAML/JSON。必須。 |
| `--measure peaks` | 資源を全て無限化（walkers/buffers/outstanding=None）＋ warmup を強制し、**クリーンな 3c/3d** を測る。末尾に `>>> 3c …N`, `>>> 3d …buffer` を出力。 |
| `--warmup F` | ピーク計測の warmup 割合（cold-start 除外、既定 0）。`--measure peaks` 時は 0.05 以上に。 |
| `--emit-trace PATH` | トレース（リクエスト＋イベント列）を CSV 出力（RTL テストベンチ刺激）。 |
| `--freeze PATH` | 凍結予測 JSON の出力先（既定 `freeze/<name>.json`）。 |

`print_report()` が design_doc §8 の全項目を出力：throughput/wire_rate_met → 3c/3d → mem/IOブリッジ要求 →
キャッシュ hit/miss → レイテンシ avg/p99/max（cycle と ns）→ ミスペナルティ種別別 → per-module 面積(GE)・電力 → FoM。

### B.2 `sweep.py`（探索）
| フラグ | 役割 |
|---|---|
| `--config PATH` | baseline 形式（`--search`/`--measure`）または space 形式（`--pareto`）。 |
| `--measure peaks` | 無限資源で `peak_walks`(3c)/`peak_buffer`(3d)/io_bridge/mem_outstanding を出す。 |
| `--search min_hw` | `num_walkers`/`iommu_req_buffer`/`io_bridge_buffer`/`mem_max_outstanding` を各々昇順に振り、**定常 stall ゼロ**で wire rate を満たす最小値を出す（他資源は寛容に固定）。 |
| `--pareto` | `space.yaml` の grid を全実行→ wire-rate 達成群で 面積–エネルギー/変換 Pareto を計算、`results.csv` と `pareto.png` を出力。 |
| `--emit-candidates` | Pareto 代表点（min_area / min_energy / knee）を `candidates/*.svh`（SVパラメータ）に出力。 |
| `--emit-trace PATH` | トレース CSV 出力。 |
| `--no-plot` | matplotlib を使わず CSV のみ。 |

判定 `wire_rate_met`（design_doc §9）：達成スループット ≥ 目標 **かつ** warmup後の `arrival_stalls==0` **かつ** `walk_stalls==0`（＝定常 stall ゼロ）。

## C. モジュール詳解（`iommu_sim/*.py`）

RTL階層と1対1対応（design_doc §13）。各モジュールの役割・主物・差し替え点：

| ファイル | 役割 | 主なクラス/関数 | 差し替え点 |
|---|---|---|---|
| `config.py` | 全パラメータ（＝将来のSVパラメータ表）。YAML/JSON/dict ロード。`off`→"off" 正規化、`assoc:"full"`→CAM。導出値 `cycle_ns/inter_arrival_cycles/target_throughput_mps`。 | `Config`, 各 `*Cfg` dataclass, `from_dict/load/to_dict/copy` | — |
| `engine.py` | サイクル駆動コア（heapq イベントキュー）。バッファ／ウォーカープール／**MSHR（初回ミスで登録→ライン単位で1ウォーク相乗り）**／IOブリッジ／ピーク・stall 計測。 | `Simulator`, `_MSHR`, `_on_arrival/_translate/_start_line/_on_walk_done/_on_complete/_on_event` | （触らない） |
| `caches.py` | 結合IOTLB（line-key）, S1/S2 PWC, 表/データGPA, DDT$/PDT$/MSI$, root=レジスタ。CAM/セット連想、世代無効化、文脈タグ。 | `SetAssocCache`, `AlwaysHit`, `CacheSet`, `LRU/FIFO/RandomRepl`, `make_cache` | `ReplacementPolicy`/`CacheABC` 継承 |
| `walker.py` | ウォークコスト（bare/s1/s2=単段, nested=2段）。コアレッシング、ミス種別分類、完了時の充填キー。 | `WalkCostModel`, `SingleStageCost`, `NestedCost`, `WalkPlan`, `COLD_DEPTH`, `make_cost_model` | `WalkCostModel` 継承 |
| `memory.py` | メモリ／AXI。レイテンシ・**1ウォーク=1 outstanding**（直列追跡）・帯域・バンク/コアレッシング。 | `MemoryModel`(`can_issue/enter/exit/account/access_cycles/bandwidth_gbs`) | クラス差し替え |
| `prefetch.py` | プリフェッチ（off/next_line/stride/rpt/dcpt/sms）＋信頼度throttle（ランダムで自己無効化）。 | `Prefetcher`, 各実装, `make_prefetcher` | `Prefetcher` 継承 |
| `workload.py` | トレース生成（iova_pattern/data_gpa/文脈）＋イベント注入（invalidation/fault/context_switch）＋CSV出力。 | `Request`, `Event`, `generate`, `export_csv`, `inter_arrival_cycles` | 関数追加 |
| `metrics.py` | 全メトリクス。レイテンシ avg/max/p99、ミスペナルティ種別別、stall、throughput。 | `Metrics`(`add_latency/miss_penalty_table/throughput_mps`) | — |
| `estimator.py` | per-module 面積(GE)＋正規化電力。重みは `PAWeights` に集約。DRAM 別枠。凍結 JSON。 | `PAWeights`, `STRUCT_BITS`, `ModuleEstimate`, `PAResult`, `estimate` | 重み調整 |
| `runner.py` | run.py/sweep.py 共通：`run_sim`（トレース生成→実行）, `wire_rate_met`, `summarize`（フラット dict）。 | 同左 | — |
| `run.py` / `sweep.py` | CLI（B章）。 | `print_report` / `run_pareto`,`search_min_hw`,`emit_candidate_svhs`,`_svh` | — |

ウォークコストの要点（`walker.py`）：
- 単段：上位（L2,L1）が S1 PWC にヒットすれば短絡、leaf は coalesce で1ライン。コールド=3、定常=1。
- nested：各ゲストPTEポインタ(GPA)を G-stage で変換してから読む。定常は「ゲスト leaf ライン＋データGPA S2 leaf ライン」=**2 アクセス/ライン**（≈単段2倍）。`full_cold` の特性値は **15**（=(3+1)(3+1)−1, 構造的最悪）。

## D. YAML 詳解

### D.1 `configs/baseline.yaml`（単一構成）
| キー | 意味（取り得る値） |
|---|---|
| `mode` | 段構成 bare / s1_only / s2_only / nested |
| `superpage` | off / 2M / 1G（leaf 被覆ページ数を拡大＝段数減） |
| `caches.iotlb` | `{entries, assoc}` 結合 IOVA→SPA（line充填）。assoc は int か `full`(=CAM) |
| `caches.s1_pwc` | `{l2:{entries,assoc}, l1:{entries,assoc}}` ゲスト上位（root はレジスタ） |
| `caches.s2_pwc` | G-stage 上位（root はレジスタ） |
| `caches.table_gpa` | ゲスト表ページの GPA→SPA（churn 低・常時ヒット） |
| `caches.data_gpa` | `{enabled, entries, assoc}` データGPA独立キャッシュ。**invalidation時に true 推奨** |
| `caches.ddtc/pdtc/msi` | デバイス／プロセス／割込文脈。`pdtc.enabled:false`=PASID未使用 |
| `caches.lookup_mode` | parallel / sequential / hybrid（latency/energy に影響） |
| `caches.walk_trigger` | demand / predictive |
| `caches.coalesce_factor` | leaf まとめ取り幅（既定8、1で無効） |
| `walkers` | `{num_walkers(null=無限→実測), pipeline_depth}` |
| `buffers` | `{iommu_req_buffer(null=無限→peak実測), io_bridge_buffer(null=無限)}` |
| `prefetch` | `{algo, distance, confidence}` |
| `memory` | `{latency_cycles, max_outstanding(null=無限), bank_parallel, coalescing_effective}` |
| `timing` | `{clock_mhz, lookup_cycles, arbitration_cycles, hit_latency_cycles}`（A章） |
| `workload` | `{iova_pattern, stride, data_gpa, n_requests, invalidation{rate,target,granularity}, fault_rate, context_switch_rate, n_devices, n_pasids, span_pages, seed}` |
| `pa` | `{scale_factor(null=正規化, 数値で絶対化)}` |

> YAML 注意：素の `off`/`on`/`yes` は bool 化されるので、`superpage`/`prefetch.algo` は内部で文字列へ正規化している。

### D.2 `configs/space.yaml`（Pareto 用）
- `base:` … 上記と同形の完全 config。ここでは資源を**有限固定**（面積/電力に差が出るように）。
- `grid:` … ドット記法のキー→値リスト。直積で構成を展開（例：`caches.coalesce_factor: [1,8]`,
  `prefetch.algo: [off,next_line,stride]`, `walkers.num_walkers: [2,4,8]` …）。
  `set_path()` が `base` にパッチし、各構成名 `cfgNNN` で実行。

## E. 出力データ詳解

### E.1 `results.csv`（Pareto 表、`--pareto`）
1構成1行、wire-rate達成→面積昇順で整列：

| 列 | 意味 |
|---|---|
| `name` | 構成ID（`cfgNNN`） |
| `mode` | 段構成 |
| `wire_rate_met` | 定常 stall ゼロで wire rate 達成か |
| `on_pareto` | 面積–エネルギー Pareto front 上か |
| `area_ge` | 総面積（GE） |
| `energy_per_translation` | 1変換あたり正規化エネルギー（IOMMU分。DRAM別枠） |
| `fom_area_x_energy` | 補助スカラー＝面積 × エネルギー/変換（小さいほど良） |
| `accesses_per_translation` | 1変換あたりメモリアクセス数（アーキ効率） |
| `peak_walks` / `peak_buffer` | 3c / 3d |
| `io_bridge_peak` / `mem_outstanding_peak` | IOブリッジ／メモリ性能要求 |
| `throughput_mps` / `avg_lat_ns` | スループット／平均レイテンシ |
| `labels` | その構成で振った grid 軸の値 |

### E.2 トレース CSV（`--emit-trace`）
RTL テストベンチ刺激。1リクエスト/1イベント1行を時刻順：

| 列 | 意味 |
|---|---|
| `arrival_cycle` / `arrival_ns` | 到着時刻（cycle と ns） |
| `kind` | `dma` / `invalidation` / `fault` / `context_switch` |
| `vpn` | IOVA ページ番号 |
| `data_page` | ゲストデータ GPA ページ（nested の S2 入力） |
| `device_id` / `pasid` / `vmid` | 文脈タグ |
| `info` | イベント補足（target/granularity 等） |

### E.3 `pareto.png`（`--pareto`、matplotlib 必要）　**2パネル構成**
1枚の図に「PPA の良し悪し」と「ノブ（walker数・各キャッシュ容量）の違い」を同居させている。

- **左パネル：面積–エネルギー散布図**
  - 横軸＝面積(GE)、縦軸＝エネルギー/変換（ともに小さいほど良＝**原点に近いほど良**）。
  - **マーカー色＝`num_walkers`**（viridis、カラーバー付き）、**マーカーサイズ＝キャッシュ総エントリ数**（IOTLB+PWC）。
  - 赤線＋点＝Pareto front、front 点に `cfgNNN` 注記。
  - これで「同じ面積でもエネルギーが低い／walker を減らしてもエネルギーは下がるか」等を一目で比較できる。
- **右パネル：並行座標（parallel coordinates）**
  - 軸＝`coalesce / iotlb / s1_pwc / walkers / buffer / area / energy`、各軸下に実値の最小..最大を表示。
  - 1本の折れ線＝1構成（**赤＝Pareto front、薄青＝wire rate 達成**）。各ノブの値の違いと、それが
    area/energy にどう効くかを**全次元まとめて**読む。
- **読み方の要点**：左で「最良＝最も原点寄り（＝knee/min area×energy）」を特定し、右でその構成の
  ノブ構成（小coalesce か大coalesce か、walker は何本か、どのキャッシュを盛ったか）を辿る。
- スループットは固定目標（ゲート）なので、達成群では面積・電力が同時最小化に縮約され、front が
  **単一点**に潰れることがある（design_doc §11 の通りで正常）。「バランス点」探索の結論はこの front／
  knee に出る：例ではコアレッシングでウォークが希少化するため **walker=1＋小キャッシュ＋prefetch** が最小。

### E.4 `candidates/*.svh`（`--emit-candidates`）
Pareto 代表点の**厳密 config を SystemVerilog `localparam`** 化（`IOTLB_ENTRIES`, `S1_PWC_*`,
`NUM_WALKERS`, `IOMMU_REQ_BUFFER`, `COALESCE_FACTOR` …）。`ASSOC=0` は fully-assoc(CAM) を表す。
次フェーズの RTL 実装で `include` して使う。

### E.5 `freeze/*.json`（凍結予測 — frozen prediction）
**「凍結予測」とは**：合成（OpenLane/sky130）に着手する**前に**、シミュレータの per-module 面積・電力
予測を `config_hash` 付きで JSON に固めておく成果物。後で合成した実測値と突き合わせ、
**予測→凍結→合成→誤差分解→校正係数 fit** の順で estimator を校正する（design_doc §12, design_premises §13）。
予測を先に確定（凍結）しておくのが肝で、合成後に予測をいじって「当たったことにする」のを防ぎ、
どの部品でどれだけズレたか（＝校正係数）を客観的に出すための基準点になる。

中身：`config`（ハッシュ源）, `weights`, per-module 面積/電力, `totals`（area_ge / *_power /
energy_per_translation / dram_*）, `config_hash`（config＋重みの SHA-256）。**同一 config＋同一重みなら
ハッシュ一致**＝同じ予測の再現を保証。`CalibParams` 相当の per-module 係数を後段で fit して当て込む。

---

## F. ログの読み方（`baseline.log` / `min_hw.log` / `pareto.log`）

代表3本のログを同梱（`iommu_sim/*.log`）。生成は `run.py`/`sweep.py` の標準出力をリダイレクトしたもの。

### F.1 `baseline.log`（`run.py --config configs/baseline.yaml`）
セクション順に読む：
1. **ヘッダ** … `mode/superpage/lookup/prefetch` と クロック・mem・到着間隔（cycle）。
2. **throughput / wire rate** … `throughput` が `target`(24.41 M/s) 以上＆`wire_rate_met: True` なら定常で
   ワイヤレート維持。
3. **required hardware (3c/3d)** … `peak_walks`(3c) と `peak_buffer`(3d)。`[measured @ unlimited]` は
   資源無限で実測した必要数（＝この構成の下限）。`run.py` 既定は**cold-start 込みの真ピーク**なので
   定常値よりやや大きい（クリーン値は `--measure peaks`）。
4. **memory / I/O-bridge 要求** … `mem_outstanding_peak`(=同時 outstanding≈必要N)、`mem_bandwidth`(GB/s)、
   `mem_accesses`(と `/translation`＝アーキ効率)、`io_bridge_buffer_peak`(4kB保持数)。
5. **cache hit/miss** … 各キャッシュの hits/misses/hit_rate と `iotlb_hit / mshr_coalesced / walks`。
   IOTLB の hit_rate が低くても、`mshr_coalesced` が多ければコアレッシングが効いている（同一ラインが相乗り）。
6. **latency** … avg/p99/max を cycle と ns で。3b の答え。
7. **miss-penalty by type** … `iotlb_hit / mshr_coalesced / pwc_full_hit / pwc_partial / full_cold` の
   件数・平均・最大(cycle)。末尾に mode の**特性 cold 深さ**（nested=15×40cyc）。on-demand 時のばらつきを読む。
8. **area & power (per module)** … 部品別 area(GE)/sram_b/cam_b/ff_b/gates/access/dyn/stat と TOTAL。
   CAM 構造は `cam_b>0`、SRAM 構造は `sram_b>0`。`buffer` の dyn が大きいのは FF クロック電力（過剰な
   バッファ深さはここに出る）。最後に `energy/translation`（IOMMU 分）と DRAM 別枠、`FoM`。

### F.2 `min_hw.log`（`sweep.py --search min_hw`）
- 1行目 `peaks @ infinite resources` … 探索の天井になる無限資源ピーク（3c/3d/io_bridge/mem_outstanding）。
- `minimum resource` … 各資源を**他資源は寛容**にして昇順に振り、**定常 stall ゼロ**で wire rate を満たす
  最小値。`num_walkers`(必要N)、`iommu_req_buffer`(3d)、`io_bridge_buffer`、`mem_max_outstanding`。
- 末尾の注記どおり、実装では各値に **+50〜100% マージン**を載せる（design_premises §12）。

### F.3 `pareto.log`（`sweep.py --config configs/space.yaml --pareto --emit-candidates`）
- `=== Pareto sweep: N configurations ===` … grid 直積の構成数。
- `M/N configurations meet wire rate` … 定常 stall ゼロで達成した数（残りは土俵外）。
- `results table -> results.csv` … 全構成の表（E.1）。
- `area-energy Pareto front` … 達成群の非劣解（面積・エネルギーとも最小側）。各行に `area_GE / E/xlate /
  area*E(FoM) / N / buf / labels`。**面積もエネルギーも他に劣らない構成だけ**が残る。
- 単一点になる場合の注記（design_doc §11）。
- `emitting SystemVerilog candidate params` … `min_area / min_energy / knee` を `.svh` 出力。
- 併せて `pareto.png`（E.3 の2パネル図）が出る。

> ログ生成コマンド（再現）：
> ```bash
> ../.venv/bin/python run.py   --config configs/baseline.yaml            > baseline.log
> ../.venv/bin/python sweep.py --config configs/baseline.yaml --search min_hw > min_hw.log
> ../.venv/bin/python sweep.py --config configs/space.yaml --pareto --emit-candidates > pareto.log
> ```

---

## G. 感度レシピ補足：PDTW（プロセス文脈ウォーク）を発生させる

PDTW は **PDT$（`pdtc`）ミス時**に起こる。既定では `pdtc.enabled:false`（PASID 未使用）なので発生しない。
発生させるには：
```yaml
caches:
  pdtc: {enabled: true, entries: 4}     # PDT$ を有効化（小容量にするとミスしやすい）
workload:
  n_pasids: 8                           # 同時 PASID 数 > pdtc.entries にするとスラッシュ
  n_devices: 4                          # DDTW も見たい場合
  context_switch_rate: 0.05             # 文脈スイッチ頻度（>0 で PASID がローテーション）
```
このとき `run.py` の cache hit/miss に `pdtc` 行が現れ、`pdtc misses`＝PDTW 回数。`n_pasids ≤ pdtc.entries`
なら初回のみミス（容量内＝ほぼ常時ヒット＝定常ゼロコスト、design_premises §4）。`n_pasids > entries` で
スラッシュ（容量超の崖）。DDTW も同様に `ddtc` と `n_devices`/`context_switch_rate` で観測できる。