# IOMMU PyMTL3 シミュレータ 使い方ガイド

このドキュメントは、`iommu_sim_pymtl/` 配下にあるサイクルレベル IOMMU シ
ミュレータを **これから初めて触る方** に向けて書いています。インストール
方法、デモの動かし方、設定ファイル（`SimConfig`）の書き換え方、そして
「ここを変えたい → このファイルの何行目を編集」というレシピを順に説明し
ます。

---

## 1. インストール

Python 3.10 以降を推奨します（動作確認は 3.14 で実施）。

```bash
cd /space/kawano-task2-iommu
python3 -m venv venv
source venv/bin/activate
pip install pymtl3 'pytest<8'
```

> `pytest<8` を明示しているのは、PyMTL3 同梱の pytest プラグインが
> pytest 8 以降の API 変更に追いついていないためです。シミュレータ本体
> の動作には影響しません。

---

## 2. 実行

```bash
cd iommu_sim_pymtl

# シナリオ A〜E（プロンプト記載の検証ケース）を全て走らせる
python3 run_demo.py            # 標準出力に表 + results.csv を生成

# パラメータスイープ（walker 数、buffer 数、coalesce 係数、prefetch 距離、IOVA パターン）
python3 sweep.py               # sweep.csv + 文字での cliff サマリ

# テスト
python3 -m pytest tests/ -v
```

`run_demo.py` の最後に出る `==== A-E comparison ====` 表が一目で全シナリ
オを比較できる形になっています。`sweep.py` の末尾には「どの値までは
ワイヤレートを維持できたか」「どこから落ちたか」を 1 行で示す summary
が出ます。

---

## 3. 設定の中心：`SimConfig`

シミュレータの **あらゆる** 振る舞いは `iommu_sim_pymtl/config.py` で
定義された `SimConfig` データクラスを 1 つ作って `run_simulation(cfg)`
に渡すことで決まります。例えば次のような形です。

```python
from iommu_sim_pymtl import (
    SimConfig, IOTLBCfg, PWCCfg, PrefetchCfg, TraceCfg, run_simulation,
)

cfg = SimConfig(
    label             = "my-experiment",
    wire_gbs          = 100.0,
    page_kb           = 4,
    clock_mhz         = 400.0,
    mem_latency_ns    = 100.0,
    coalesce_factor   = 8,
    levels            = 3,
    nested            = False,
    iotlb             = IOTLBCfg(sets=1, assoc=256, policy="lru"),
    pwc               = PWCCfg(sets=1, assoc=16,  policy="lru"),
    prefetcher        = PrefetchCfg(kind="nextline", distance=16, coalesce=8),
    num_walkers       = None,       # None なら無限（必要数を測る用）
    buffer_size       = None,       # None なら無限
    mem_max_outstanding = None,
    hit_latency_cycles  = 1,
    trace             = TraceCfg(kind="sequential", n=8000),
    max_cycles        = 10_000_000,
)
m, eng = run_simulation(cfg)
```

`run_simulation` は `(Metrics, IOMMUEngine)` を返します。レポート整形は
`iommu_sim_pymtl.harness.fmt_report(name, cfg, m)` を使うのが手早いで
す。

---

## 4. レシピ集：「ここを変えたい」 → 「どこを編集」

すべて `SimConfig` のフィールドを差し替えるだけです。サンプルコードは
`run_demo.py` 中のシナリオ関数（`scenario_A()`〜`scenario_E()`）を参考
にすると分かりやすいです。

### 4-1. ワイヤレートを変えたい（例：200 GbE 相当 = 25 GB/s）
```python
cfg.wire_gbs = 25.0          # 単位: GB/s
```
内部で 1 ページあたりの到着間隔（cycle 数）と target throughput が
自動再計算されます（`SimConfig.wire_inter_arrival_cycles()` 参照）。

### 4-2. ページサイズを変えたい（2 MB ヒュージページ）
```python
cfg.page_kb = 2048           # 単位: KB（4=4KB、2048=2MB）
```
注意：粒度が増えると到着間隔も同じだけ伸びる前提のモデルです。複合
ページサイズを扱いたい場合は `iommu_sim_pymtl/workload.py` の
`sequential()` を改造してください。

### 4-3. IOMMU クロックを変えたい（例：1 GHz）
```python
cfg.clock_mhz = 1000.0       # 1 cycle = 1.0 ns
```
`mem_latency_cycles()` は自動で 100 cycle になります。

### 4-4. メモリレイテンシを変えたい（例：HBM 想定で 60 ns）
```python
cfg.mem_latency_ns = 60.0    # 400 MHz なら 24 cycle
```

### 4-5. IOTLB の容量／関連度／置換ポリシを変えたい
```python
cfg.iotlb = IOTLBCfg(sets=4, assoc=64, policy="fifo")
# assoc=None : 無限（必要サイズを測りたい時）
# assoc=0    : IOTLB を無効化（常に miss）
# policy     : "lru" / "fifo" / "random"
```

### 4-6. PWC（Page-Walk Cache）の設定を変えたい
```python
cfg.pwc = PWCCfg(sets=2, assoc=8, policy="lru")
```
キーは `('L1', vpn>>9)` / `('L2', vpn>>18)` 形式で、`walker_cost.py`
の `SingleStageCost.cost()` が参照します。

### 4-7. プリフェッチャを変えたい
```python
# 何もしない
cfg.prefetcher = PrefetchCfg(kind="none")

# 単純な距離 d の next-line prefetch
cfg.prefetcher = PrefetchCfg(kind="nextline", distance=16, coalesce=8)

# 連続性を学習する confidence stride
cfg.prefetcher = PrefetchCfg(kind="stride", distance=16, threshold=4, coalesce=8)
```
新しいアルゴリズムを試したい時は `iommu_sim_pymtl/prefetch.py` で
`Prefetcher` を継承して `predict(vpn, cycle)` を実装し、
`make_prefetcher()` に `elif kind == "myname": ...` を 1 行追加してくだ
さい。

### 4-8. 64 B ラインあたりの PTE 数（coalescing factor）を変えたい
```python
cfg.coalesce_factor = 4      # 例：ライン幅 32 B
```
これを 1 にすると leaf coalescing 無しでの required N を測れます
（シナリオ A 相当）。

### 4-9. 2 段アドレス変換（nested）を試したい
```python
cfg.nested = True
cfg.nested_s2_residual = 1   # S1 1 回あたり追加で必要な S2 アクセス
```
`walker_cost.py` の `NestedCost` が使われ、メモリトラフィックが
おおよそ 2 倍以上に伸びるはずです。

### 4-10. Walker 並列度・トランザクションバッファを変えたい
```python
cfg.num_walkers = 4          # None = 無限（必要数 N を測る用）
cfg.buffer_size = 8          # None = 無限（必要バッファ深さ B を測る用）
```
必要数 N と B を「測る」ためには **両方を None** にしてください。報告
される `peak_walks` / `peak_buffer` がそのまま設計値です。

### 4-11. ワークロード（IOVA パターン）を変えたい
```python
# 連続（既定）
cfg.trace = TraceCfg(kind="sequential", n=8000)

# ランダム
cfg.trace = TraceCfg(kind="random", n=8000, span_pages=1_000_000, seed=42)

# マルチストリーム（複数の連続ストリームを round-robin で混ぜる）
cfg.trace = TraceCfg(kind="multi_stream", n=8000, streams=4, stride_pages=1)
```

### 4-12. メモリの outstanding 上限を入れたい（DRAM のキュー制限）
```python
cfg.mem_max_outstanding = 32
```
これを越える issue は walker を再 enqueue します（バックプレッシャ）。

### 4-13. IOTLB ヒット時の完了レイテンシを変えたい
```python
cfg.hit_latency_cycles = 2   # 既定 1 cycle (= 2.5 ns @ 400 MHz)
```

### 4-14. シミュレーションの安全上限を変えたい（ハング検出）
```python
cfg.max_cycles = 50_000_000
```

---

## 5. 新しい構成要素を足したい場合

すべて ABC + factory のパターンになっています。**エンジンには手を加え
ません**。

| 足したいもの            | 編集するファイル          | 増やすクラス                    |
|------------------------|--------------------------|---------------------------------|
| 置換ポリシ              | `caches.py`              | `ReplacementPolicy` 派生 + `make_policy()` |
| キャッシュ構造          | `caches.py`              | `CacheABC` 派生                |
| プリフェッチアルゴリズム | `prefetch.py`            | `Prefetcher` 派生 + `make_prefetcher()` |
| ウォークコスト計算       | `walker_cost.py`         | `WalkCostModel` 派生 + `harness.py` 分岐 |
| ワークロード             | `workload.py`            | 関数 1 つ + `make_trace()` 分岐 |
| メモリモデル             | `memory.py`              | `MemoryModel` を継承            |

たとえば「8-way pseudo-LRU」を試したいなら `caches.py` に
```python
class PLRU(ReplacementPolicy):
    def touch(self, s, key): ...
    def victim(self, s, keys): ...
```
を書いて、`make_policy("plru")` で返るように 1 行足すだけで `SimConfig`
から指定可能になります。

---

## 6. メトリクスの読み方

`Metrics` は `iommu_sim_pymtl/metrics.py` にあります。`run_simulation`
が返すオブジェクトから直接読めます。

| フィールド                | 意味                                                                     |
|--------------------------|--------------------------------------------------------------------------|
| `completed`              | 完了した demand 翻訳数                                                   |
| `peak_walks`             | **必要 walker 数 N**（無限資源で測ったとき）                              |
| `peak_buffer`            | **必要トランザクションバッファ深さ B**                                     |
| `walks_started`          | true miss（実際にメモリへ行った walk の本数）                            |
| `mshr_coalesced`         | 同一ラインの先行 walk に相乗りした件数                                    |
| `iotlb_hit`              | IOTLB ヒット数                                                            |
| `mem_accesses`           | 累積メモリアクセス数（mem/page で比較するのが鉄板）                       |
| `mem_peak_outstanding`   | 同時 outstanding アクセスのピーク                                         |
| `avg_lat_cycles` / `p99_lat_cycles` | 平均/p99 翻訳レイテンシ（cycle 単位、ns 換算は `cfg.ns_per_cycle()` を掛ける） |
| `first_arrival_cycle` / `last_complete_cycle` | スループット計算に使う両端時刻                                 |
| `sim_cycles`             | 終了までに走らせたシミュレーション cycle 数                               |

`results.csv` / `sweep.csv` も同じ値群を CSV に書いています。

---

## 7. よくあるトラブル

- **「ImportError: No module named 'iommu_sim_pymtl'」**
  `iommu_sim_pymtl/` ディレクトリの直下から実行してください。
  `run_demo.py` / `sweep.py` 自身は冒頭で `sys.path` 調整しています。

- **テストが INTERNALERROR**
  pytest 8 以降は PyMTL3 同梱のプラグインと衝突します。`pip install
  'pytest<8'` で 7.x 系を入れてください。`pytest.ini` 内の
  `addopts = -p no:pymtl3` も併用してあります。

- **シミュレーションが終わらない**
  `cfg.max_cycles` を超えたら強制終了します。スタックしているのに気付
  かないときは `eng.cycle_out` をデバッグ出力で覗いてください。

- **メトリクスの単位が ns でなく cycle**
  `metrics.py` は cycle で持っています。ns に直すときは
  `m.avg_lat_cycles * cfg.ns_per_cycle()`。`harness.fmt_report` も同じ
  方法で換算しています。

---

## 8. 参考：必要 N と必要 B はどう導出されるか

無限資源（`num_walkers=None`, `buffer_size=None`）で走らせると、
`peak_walks` と `peak_buffer` がそれぞれ **架空の「使えた最大本数」**
を示します。これが、実機で wire rate を **絶対に維持** するために必要
な最低本数（Little's law による）と一致します。

たとえばシナリオ A（キャッシュ無し）：
- メモリ往復 100 ns、平均 walk は 3 回 → 翻訳あたり 300 ns
- 到着間隔 40.96 ns
- 必要本数 N ≒ 300 / 40.96 ≒ 7.3 → **8**

シナリオ B（PWC + coalescing）：
- PWC ヒット率がほぼ 100 %、leaf 1 アクセスで 8 ページ分カバー
- 平均 walk ≒ 0.125 → wire を 1 本でほぼ捌ける → **N = 1**

`run_demo.py` を走らせるとこの「8 → 1 → 3 → 8 → 4(failed)」の値が
そのまま `required N (peak walks)` 行に出ます。
