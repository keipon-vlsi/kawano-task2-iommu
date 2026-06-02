# IOMMU PyMTL3 シミュレータ コード詳細解説

このドキュメントは、`iommu_sim_pymtl/` のソースコードを **モジュールご
とに、初学者向けに** 解説します。読み終えると「1 つの DMA リクエスト
が cycle 単位でどう旅をするか」「メトリクスがどう必要数 N / バッファ
深さ B にマッピングされるか」「PyMTL3 のどの構文が何をやっているか」が
イメージできるようになります。

> 関連ドキュメント：使い方の手順は `USAGE_ja.md` を、設計上の決定事項
> や PyMTL3 を選んだ理由は `../ASSUMPTIONS.md` を参照してください。

---

## 0. 全体像（30 秒で）

```
        +--------------------------+        injects Python policy objects
        |        SimConfig         |---------------------------+
        +-------------+------------+                           |
                      v                                        |
        +--------------------------+                           v
        | harness.build_engine_... |  --> IOMMUEngine ◀-- {iotlb, pwc,
        +-------------+------------+         (PyMTL3 Component)  prefetcher,
                      |                                       memory,
                      v                                       cost_model,
        +--------------------------+                          workload}
        |   harness.run_simulation |  -- sim_tick() loop --
        +-------------+------------+
                      v
                  Metrics (mem/page, peak_walks, peak_buffer, lat, tput, …)
```

エンジンは **datapath（fixed なサイクル駆動）** だけを書き、置換ポリシ
やプリフェッチアルゴリズム、ワークロードといった **policy** はすべて
ABC + factory で差し替え可能にしています。「設計パラメータを 1 つ変えた
い」が「`SimConfig` のフィールドを 1 つ変える」になるのが本シムの売り
です。

---

## 1. 1 リクエストが辿るサイクルレベルの旅

ある DMA リクエスト（`vpn = v`）が時刻 `cycle = a` に到着したと仮定し
ます。エンジン (`engine.IOMMUEngine`) は毎サイクル内部の
`_tick_cycle(c)` を呼び、以下の順で動かします。

1. **arrival**（`_on_arrival → _admit`）
   - `buffer_size` が None でなければ「バッファに空きが無いか」確認。
     満員なら `_buf_wait` に保留（**back-pressure**）。
   - 空きがあれば `buffer` を 1 増やし、`peak_buffer` を更新。
   - 同時にプリフェッチャの `predict(v, c)` を呼び、戻ってきた vpn 群
     を `_translate(..., is_prefetch=True)` で「需要を消費しない翻訳要
     求」として後段へ。
2. **translate**（`_translate`）
   - `iotlb.lookup(v)` がヒットなら `hit_latency_cycles` 後に完了をス
     ケジュール。`iotlb_hit` インクリメント。
   - ミスなら `line = (v // c) * c` で 64 B ライン番号を計算し、その
     ラインへの walk が既に飛んでいる（`_mshr` に登録済み）かを確認。
   - 既にあれば **MSHR coalescing**：完了時刻 `comp_cycle` に相乗りし
     て、自分専用の memory issue は出さない（`mshr_coalesced++`）。
3. **start_walk**（`_start_walk`）
   - `num_walkers` を満たすか、`memory.can_issue()` が False なら
     `_walk_wait` に積んで延期。
   - そうでなければ `cost_model.cost(v, pwc)` で必要アクセス数 `acc`
     と「完了時に挿入する IOTLB / PWC キー」を貰う。
   - `memory.issue(acc)` してアクセスをカウント、`_walker_pool` に登
     録（`peak_walks` 更新）、完了時刻 `c + acc * mem_latency_cycles`
     を `_completions[comp_cycle]` に登録。
4. **finish_walk**（`_finish_walk`）
   - 該当 cycle に到達したら、walker を返却・MSHR を消去・IOTLB と
     PWC を warming（leaf line の 8 vpn 一括 + L1/L2 prefix）。
   - demand であれば `_on_complete` を即時呼んで完了 latency を記録。
5. **buffer 解放**（`_drain_buf_wait`）
   - `buffer_size` 上限で待たされていた arrivals があれば、空いた分
     `_admit` し直す。

これを `_running == True` の間、`pymtl3` の `@update_ff` 内で毎サイク
ル繰り返します。「全 arrival 投入済み」かつ「`completed == 入力数`」
かつ「walker / mshr / completions / 待ち行列が空」になったら
`done_reg` を立て、ハーネスの tick ループが抜けて終了です。

---

## 2. PyMTL3 構文の対応表

実機 RTL に育てやすいよう、CL でも PyMTL3 の「正しい」書き方をなぞって
います。

| 本シムでの用途                       | PyMTL3 の構文                                  |
|-------------------------------------|------------------------------------------------|
| サイクルカウンタ・done フラグの保持   | `Wire(Bits64)` / `Wire(Bits1)`                  |
| サイクル毎に 1 回呼ばれる本体        | `@update_ff def tick(): ...`                    |
| 配線の即時露出（OutPort へコピー）   | `@update def expose(): s.cycle_out @= s.cycle` |
| FF 形式の次サイクル代入              | `s.cycle <<= Bits64(c)`                         |
| 組合せ代入（同一サイクル内）         | `s.cycle_out @= s.cycle`                        |
| シミュレーションの 1 cycle 進行       | `eng.sim_tick()`                                |
| リセット                             | `eng.sim_reset()`                               |
| エラボレーション・パス               | `eng.elaborate(); eng.apply(DefaultPassGroup())` |

「Python のデータ構造（dict / list）で待ち行列を管理しているのに本当に
RTL に降ろせるの？」という疑問に対しては：

* `_completions` は固定段数の **delay pipeline**（最大段数 =
  `mem_latency_cycles * max_walks`）に置き換える。
* `_walk_wait` / `_buf_wait` は `stdlib.queues.NormalQueueRTL` 等の
  FIFO に。
* `_mshr` は CAM（content-addressable memory）か、ライン番号をキーとす
  る小さな set-associative 構造に。

…という対応関係です。CL モデルが Python のコレクションでロジックを表現
できる代わりに、RTL に降ろす時はこれら 1 つずつをハードウェア構造に置き
換えていけば良い、という構造の写像になっています。

---

## 3. 各モジュール解説

### 3-1. `config.py`（`SimConfig` ほか）

`SimConfig` は **唯一の入力面**。プロンプトに挙がっている全パラメータ
（wire_gbs / page_kb / clock_mhz / mem_latency_ns / coalesce_factor /
levels / nested / iotlb / pwc / num_walkers / buffer_size / prefetcher
/ mem_max_outstanding / hit_latency_cycles / trace / max_cycles）をフ
ィールドに持ちます。

便利メソッド：
* `cycles_per_ns()`, `ns_per_cycle()` — 単位換算
* `mem_latency_cycles()` — `mem_latency_ns` から整数 cycle へ
  （400 MHz, 100 ns → ちょうど 40）
* `wire_inter_arrival_cycles()` — `page / wire` から平均 cycle 数
  （100 GB/s, 4 KB → 16.384 cycles）— 整数化はワークロード側で行う
* `target_throughput_per_s()` — 「保たねばならない最低スループット」

### 3-2. `caches.py`（IOTLB / PWC）

- `ReplacementPolicy`（ABC）と LRU / FIFO / RandomRepl を実装。
- `SetAssocCache` は `num_sets × assoc` の連想キャッシュ。
  - `assoc=None` → 無限（容量を測定したい時用）
  - `assoc=0` → 完全無効（常に miss）
  - 上記以外 → 通常の set-associative
- ハッシュは `hash(key) % num_sets`。PWC は `('L1', vpn>>9)` のように
  キーがタプルになります（Python の `hash()` で十分エントロピーが取れ
  ます）。
- `hits / misses` はモジュールが自前で持ち、エンジンは `_run_simulation`
  の最後で `metrics.iotlb_hits` 等にコピーします。

### 3-3. `prefetch.py`

- `NoPrefetch` — 何もしない（baseline 比較用）
- `NextLineStride(distance, coalesce)` — 連続 IOVA を仮定し、
  `vpn` から `distance` ページ先までを **leaf ライン境界に揃えて**
  発行。`frontier` で過去発行済みを覚えており、重複しません。
- `ConfidenceStride(distance, threshold, coalesce)` — 連続 2 アクセス
  の差分（stride）を観測し、threshold 回続いて初めて発行を始める。
  乱数 IOVA だと confidence が 0 のまま動かない（**graceful
  degradation**）。

エンジンはプリフェッチャを「優先度の低い翻訳要求」として扱い、
**buffer は専有しない／demand latency 統計に乗らない** ようにしてい
ます。代わりに walker と memory のリソースは共有して**します**。

### 3-4. `memory.py`

固定 latency と outstanding 監視。`can_issue(n)` が False のとき walker
は `_walk_wait` に再 enqueue。今後 DRAM bank / 行バッファモデルに差し
替えやすい最小限の表面しか持っていません。

### 3-5. `walker_cost.py`

`SingleStageCost` が Sv39-like の 3 段歩行を表現します：
* L2 prefix が PWC にあれば +0、無ければ +1（root PTE フェッチ）
* L1 prefix が PWC にあれば +0、無ければ +1（中間 PTE フェッチ）
* leaf line は無条件で +1
完了時に PWC へ `[('L1', vpn>>9), ('L2', vpn>>18)]` を、IOTLB へは
ラインに属する 8 個分の vpn を一括挿入（leaf coalescing）。

`NestedCost` は単純化として「各 S1 アクセスに対し `s2_residual` 分の
S2 アクセスを上乗せ」する形でメモリトラフィックを 2 倍化します。完全
版を作りたい人へのテンプレ／ABC。

### 3-6. `workload.py`

到着時刻は **整数 cycle** を返します。本来の 40.96 ns 間隔は 400 MHz
で 16.384 cycle ですが、整数化のドリフトを避けるため `_quantise(n,
ia_cycles)` が浮動小数アキュムレータで 1 ステップずつ繰り返し、各
arrival を `int(acc)` に丸めます。8000 件流して累積ずれ < 1 cycle。

* `sequential(n)` — 単純連続
* `random_trace(n, span_pages)` — 一様乱数 vpn
* `multi_stream(n, streams, stride_pages)` — 複数連続を round-robin で
  混ぜたもの（local 性はあるが monotonic でない）

### 3-7. `metrics.py`

数値を貯めるだけのデータクラス。`avg_lat_cycles` / `p99_lat_cycles` は
プロパティです。cycle ↔ ns の換算は `cfg.ns_per_cycle()` を通すのが原
則（`harness.fmt_report` が好例）。

### 3-8. `engine.py`（`IOMMUEngine`）

本シムの **唯一の PyMTL3 Component**。詳細：

* `construct(s)` は引数を取りません（policy オブジェクトをハッシュ可能
  にする手間を避けるため）。ハーネスがインスタンスを作った後に
  `s.iotlb = …`, `s.workload = …` といった形で属性として差し込みます。
* `s.cycle` (Wire) と `s.done_reg` (Wire) が **唯一の RTL レジスタ**。
  これらを `@update_ff` で更新し、`@update` で同名の OutPort
  (`cycle_out`, `done_out`) に露出させます。波形ダンプや外部モニタが
  欲しくなった時にそのまま使えます。
* `_tick_cycle(c)` は純 Python。**fixed-order** で
  `completions → arrivals → walk_wait → buf_wait` を流します。`bool`
  を返し、True なら `done_reg <<= 1` で次サイクルから idle。
* `_running` フラグで「`sim_reset()` 内の暗黙 tick の間は何もしない」
  を実現しています（PyMTL3 の reset は @update_ff 本体を必ず一度呼ぶ
  ため、初期化前に走ると `_completions` が無いと落ちる）。

`peak_walks` / `peak_buffer` の更新タイミングが超重要：
* `_admit` でバッファ占有を増やした **直後** に `peak_buffer` を更新。
* `_start_walk` で walker 登録した **直後** に `peak_walks` 更新。

この 2 つの「最大値」がそのまま実機で必要なハードウェア量です。

### 3-9. `harness.py`

`SimConfig` → policy オブジェクトを作る → `IOMMUEngine()` を生成 → 属
性差し込み → `elaborate()` → `apply(DefaultPassGroup())` → `sim_reset()`
→ `reset_state()` → `sim_tick()` ループ → cache/memory のカウンタを
metrics に折り畳む、までを行います。`fmt_report` は reference simulator
と同じ書式の文字列を作る関数です。

---

## 4. メトリクスから設計値へ：3c / 3d の読み方

本シムの **「測ったら設計値になる」場所** は次の通り：

| 報告フィールド | 物理的意味                                        |
|----------------|---------------------------------------------------|
| `peak_walks`   | **必要 walker 数 N**（同時並行で動かす必要のある PTW コンテキスト数） |
| `peak_buffer`  | **必要トランザクションバッファ深さ B** |
| `mem_accesses / completed` | 1 翻訳あたりのメモリトラフィック（mem/page） |
| `throughput_Mps` vs `target_Mps` | ワイヤレートを保てるか／落ちるか |
| `avg_lat_cycles * ns_per_cycle()` | DMA 視点の翻訳レイテンシ |

「3c 必要 N」は **無限資源で測った peak_walks**、「3d 必要 B」は
**無限資源で測った peak_buffer** が答えです（リトルの法則を当てるとも
言えるし、シミュレーションそのものをそれの empirical 版と捉えても良
い）。`num_walkers=None, buffer_size=None` のままシナリオを走らせる
だけで両方が得られます。

これに対し「実機を 5 倍にして N=5 で十分か？」「buffer は 4 だと足り
るか？」を確認したい時は、`num_walkers=5, buffer_size=4` と入れて走ら
せ、`throughput_Mps` が target に届くかを見ます。シナリオ E（プロンプ
ト記載）はまさにこの組み合わせで wire rate が破綻する例です。

---

## 5. 「もう少し詳しく」用 — リクエスト 1 本の完全シーケンス

例：シナリオ B（PWC=16, IOTLB=256, coalesce=8, 連続）で 2 本目（vpn=1）が
arrival したとします。

1. `c = a₁`（arrival cycle）。`_on_arrival(c, a₁, 1)` 呼び出し。
2. バッファ空きあり → `_admit(c, a₁, 1)`。`buffer` += 1。
3. `NoPrefetch` なので prefetcher.predict は []。
4. `_translate(c, a₁, 1, is_prefetch=False)`：
   - `iotlb.lookup(1)` → ヒット（1 本目の walk で leaf coalescing に
     より vpn=0..7 が一括 warm されている）。
   - 完了を `c + 1`（hit latency 1 cycle）に登録、`iotlb_hit++`。
5. 次サイクル `c+1`：`_completions[c+1]` 中の lambda が
   `_on_complete(c+1, a₁, 1)` を呼ぶ。`completed++`、
   `latencies_cycles.append(c+1 - a₁)`、`buffer` -= 1。

これがシナリオ B では 7/8 のリクエストで起こり、残り 1/8 で leaf line
walk → coalescing による secondary hit が発生します。結果として
`mem_accesses / N ≈ 0.127`（≒ 1 walk あたり 1.02 アクセス を 8 ページに
シェア）。

---

## 6. テストとの対応

`tests/test_validation.py` は本ドキュメントの議論をそのまま assertion
に落としています：

* `test_A_no_cache_required_walkers_around_8` — A: peak_walks ∈ [6,10]、
  mem/pg ≈ 3。
* `test_B_pwc_coalescing_collapses_memory_traffic` — B: mem/pg < 0.2,
  peak_walks ≤ 2。
* `test_C_prefetch_collapses_observed_latency` — C: avg_lat < 10 ns。
* `test_D_random_iova_regresses_to_no_cache_regime` — D: 乱数で
  キャッシュ効果が消滅。
* `test_E_finite_under_provisioning_fails_wire_rate` — E:
  throughput < 0.7 × target。
* `test_confidence_stride_disables_on_random` — confidence prefetch が
  乱数下でも無駄打ちしない。
* `test_nested_translation_increases_memory_traffic` — nested で
  mem/page が増える。

これらが緑である限り、本シムは要求された「order/trend を再現する」要件
を満たしていると考えられます。
