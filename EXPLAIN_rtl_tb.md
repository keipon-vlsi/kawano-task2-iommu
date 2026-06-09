# IOMMU RTL / テストベンチ 解説

`./rtl/` の SystemVerilog 実装と `./tb_coco/` の cocotb シミュレーションについて、
**モジュール構成・定常時のデータフロー・テストシナリオ**をまとめる。

対象は **Phase-1 RTL**(`iommu_pkg.sv` 冒頭コメント参照)で、スコープは次に限定される。

- 定常状態 / ハッピーパスのみ(フォルトなし)
- root / device-context / process-context は**事前ロード済みレジスタ**として扱う(コールド解決なし)
- 4 kB のデータ移動そのものは扱わない(I/O ブリッジの仕事。IOMMU は**アドレス変換**だけ)

設計の背景となる数値・原則は `CLAUDE.md` の "Derived key numbers" / "Established findings" を参照。
本 RTL は Python 探索器 `iommu_sim/` のマイクロアーキを写したもの。

---

## 1. モジュール全体像

`./rtl/` は 7 ファイル。役割で分けると **共通定義 1 / ストレージ部品 1 / 5 つの合成可能ブロック** になる。

| ファイル | 役割 | 種別 |
|---|---|---|
| `iommu_pkg.sv` | 共通 package。アドレス幅・enum・`req_t`・`ctx_of()` | 型定義 |
| `cache_store.sv` | 汎用の連想キャッシュ・ストレージ(全キャッシュの土台) | 部品 |
| `iommu_core.sv` | **トップ**。5 ブロックを配線 | ブロック |
| `txn_buffer.sv` | フロントエンド:トランザクションバッファ + MSHR + キャッシュ群 | ブロック |
| `walk_engine.sv` | N 個の walker + メモリ調停(アービタ) | ブロック |
| `walker.sv` | 1 本のページテーブルウォーク文脈(FSM) | ブロック |
| `mem_if.sv` | AXI ライクな read master(タグ付き outstanding) | ブロック |

### 階層(インスタンス関係)

```
iommu_core  (top)
├── u_front : txn_buffer            ← フロントエンド
│   ├── u_iotlb : cache_store       (IOTLB:  最終リーフ, line-keyed)
│   ├── u_s1pwc : cache_store       (S1 PWC: 上位レベル)
│   └── u_s2pwc : cache_store       (S2 PWC: 面積計上用。happy-path では preload のみ)
├── u_walk  : walk_engine
│   └── g_walk[0..N-1] : walker     (generate で N 本)
└── u_mem   : mem_if                ← AXI read master
```

`iommu_core.sv` 冒頭の ASCII 図がそのまま全体像:

```
request --> [txn_buffer + MSHR + caches] --dispatch--> [walk_engine: N walkers]
                   ^  |                                        |  (arbiter)
                   |  +--------- completion (SPA) <------------+
                   |                                          [mem_if: AXI read]
                 response                                       AR/R  <--> TB memory
```

### アドレスモデル(`iommu_pkg.sv`)

RISC-V Sv39 + Sv39x4 に対応。

- `IOVA = VPN(27) + offset(12) = 39b`(デバイス DMA アドレス)
- `GPA  = GPN(29) + offset(12) = 41b`(ゲスト物理 / 中間)
- `SPA  = PPN(28) + offset(12) = 40b`(ホスト物理 / 最終)
- 文脈タグ `CTX_W = device_id(16) + pasid(20) + vmid(14) = 50b`
- リクエスト記述子 `req_t` は **制御状態のみ**(4 kB ペイロードは持たない)
- `ctx_of(r) = {vmid, pasid, device_id}`

`MODE` (BARE/S1_ONLY/S2_ONLY/NESTED)、`LOOKUP_MODE`、`STORAGE` (FF/SRAM) はすべて
int パラメータとして定義され、ツール間移植性を確保している。

---

## 2. 各モジュール詳細

### 2.1 `cache_store.sv` — 汎用連想キャッシュ

全ての変換キャッシュ(IOTLB / S1 PWC / S2 PWC / DDT\$ / PDT\$ …)を**この 1 つの
ラッパで賄う**。キャッシュの"形"は単なるパラメータ:

- `ENTRIES` … 総エントリ数
- `ASSOC` … 1=ダイレクト, N=N-way, `ENTRIES`=フルアソシ(CAM)
- `STORAGE` … `ST_FF`(フロップ)/ `ST_SRAM`(合成ヒント。Phase-1 では機能は同一、
  `ram_style` 属性で合成にヒントするのみ)

動作:

- **lookup は registered(1 サイクルレイテンシ)**。`lookup_en` を立てた**次サイクル**に
  `hit` / `rdata` が出る。FF/CAM と SRAM でタイミングを揃え、ウォーク/ルックアップ段を
  均一化するため。
- セット index は key の下位ビット(`set_idx`)。フルアソシなら 1 セット。
- **fill** … 既存 key があれば更新、なければ invalid way、それも無ければ
  ラウンドロビン victim(`rr_ptr`)を置換。per-entry write-enable(クロックゲート向き)。
- `inval_all` で O(1) フラッシュ(無効化フック。Phase-1 では未使用)。

### 2.2 `walker.sv` — 1 本のウォーク文脈(FSM)

walker は「小さな状態箱 + メモリ読みを 1 件 outstanding で保持する権利」(`CLAUDE.md`
の design_premises)。ALU ではない。

フロントから渡される**フェッチ計画**を実行する:

- 入力 `disp_nreads` = 連鎖するタグ付き PTE 読みの本数(PWC/IOTLB ショートカット後の**残り**)
- `disp_nreads` 回 read を連鎖発行 → 最後(リーフ)の PTE から SPA を合成 → `done_mshr` 付きで完了報告

FSM(4 状態):

```
W_IDLE → W_ISSUE → W_WAIT → (残りあれば W_ISSUE に戻る) → W_DONE → W_IDLE
```

- `W_IDLE`: `disp_valid` で vpn / mshr / left(=nreads) をラッチして発行開始
- `W_ISSUE`: `mreq_valid` を立て、`mreq_ready`(AR 受理)で `W_WAIT` へ
- `W_WAIT`: `mrsp_valid`(自分宛 R)で PTE をラッチ、`done_cnt++`。残り 1 本なら `W_DONE`、
  そうでなければ `left--` して `W_ISSUE` へ
- `W_DONE`: `done_ready` で `W_IDLE`

ポイント:

- `mreq_tag = walker_id`(タグ = walker 番号)。これで応答を発行元へ demux でき、
  かつ `MEM_MAX_OUTSTANDING` が並列ウォーク数 N の実効上限になる。
- `mreq_addr = {done_cnt, vpn}` … レベル(read index)と vpn をアドレスに**エンコード**。
  TB のメモリスタブが決定的に PTE を返せるようにするため。
- `done_spa = {last_q[PPN-1:0], 12'b0}` … 最後の PTE の PPN をリーフとして SPA を合成。

### 2.3 `walk_engine.sv` — N 並列 walker + 調停

`NUM_WALKERS` 本の `walker` を `generate` で並べ、単一のメモリ IF へ多重化する。
レイテンシ隠蔽に必要な並列度(Little's law の N)を実装する層。

4 つの仕事:

1. **dispatch**: 空いている walker へ着信ウォークを振る
2. **メモリ要求の調停**: 各 walker のタグ付き mreq を 1 本のメモリ IF へ mux
3. **応答の demux**: タグ付き R を `mrsp_tag == i` で発行元 walker へ戻す
4. **完了の調停**: walker の done をフロントへ mux

調停は **fixed-priority(最小 index 優先)**。Phase-1 の簡略化で、ラウンドロビンは後の改良。
`for (i = N-1; i>=0; i--)` で回し、最後に勝った(=最小 index の)候補が残る実装。

3 つの select 信号:`free_id`(dispatch 先)/`mreq_id`(メモリ要求グラント)/
`done_id`(完了グラント)。`active_walks_o` は busy な walker 数(3c の peak_walks 観測用)。

### 2.4 `mem_if.sv` — AXI ライク read master

IOMMU が出す **PTE 読み**だけを扱う(4 kB データ書き込みパスはスコープ外)。

- 内部側(アービタ側): 単純な valid/ready 要求 + タグ付き応答
- 外部側(TB メモリスタブ側): AR/R チャネル(ID 付き)
- `outstanding` カウンタが `MEM_MAX_OUTSTANDING` 未満のときだけ AR を発行
  (`can_issue`)。これが**並列ページウォークの上限**(design_premises §6:
  AXI outstanding == 並列ウォーク上限)。
- R は常に受理(`rready=1`、walker 側がタグで latch)。
- `ar_fire` で +1、`r_fire` で −1。

### 2.5 `txn_buffer.sv` — フロントエンド(バッファ + MSHR + キャッシュ)

最重要モジュール。変換キャッシュ(IOTLB / S1 PWC / S2 PWC)を**所有**し、
リクエストごとに次を行う:

1. `BUFFER_DEPTH` のバッファエントリを確保(制御状態のみ)
2. **IOTLB lookup**(line-keyed, coalescing-fill)→ hit なら即完了
3. miss なら、**同一 line+ctx で in-flight(ST_WALK)のエントリへ MSHR coalesce**
   (= バッファ自身が MSHR。同一 line のエントリは 1 本のウォークを共有)
4. それも無ければ **PWC を probe** し、残り read 数を計算してウォークを dispatch
5. ウォーク完了で IOTLB の line を fill し、coalesce された全エントリを完了させる

**キー幾何**(coalescing の肝):

```
CW        = log2(COALESCE_FACTOR)             // 例: 8 → 3
line_of(v)= v >> CW                           // 8 ページを 1 line に束ねる
iotlb_key = {ctx, line_of(v)}                 // IOTLB は line 粒度
pwc_key   = {ctx, {9'b0, v[26:9]}}            // 上位レベル: 約 2 MB 領域ごと
```

→ 1 本の 64 B(=8 PTE)読みで 8 ページぶんのリーフが取れる(coalescing)。
これが "PTE coalescing is the biggest cheap win"(`CLAUDE.md`)の RTL 実体。

**バッファエントリの状態機械**(エントリ単位):

```
ST_FREE → ST_LOOK → (IOTLB hit) ───────────────→ ST_DONE → ST_FREE
                  → (coalesce hit) ─────────────→ ST_WALK → ST_DONE → ST_FREE
                  → (PWC probe → dispatch leader)→ ST_WALK → ST_DONE → ST_FREE
```

**lookup FSM**(全エントリ共有の 1 ポート、`L_IDLE→L_IOTLB→L_PWC`):

- `L_IDLE`: ST_LOOK のエントリがあれば `cur` に取り、IOTLB lookup を起動 → `L_IOTLB`
- `L_IOTLB`: 次サイクルに IOTLB 結果。
  - hit → `e_spa[cur]=iotlb_rdata`, ST_DONE, `cnt_iotlb_hit++`
  - coalesce_hit(同一 line の ST_WALK 存在)→ ST_WALK(follower), `cnt_coalesced++`
  - どちらも無し → `L_PWC` へ
- `L_PWC`: ウォークを dispatch(leader)。`disp_valid_q` を立て、`disp_ready` で
  ST_WALK + leader、`cnt_walks++`、**PWC を warm**(`wb_pwc_fill`)

**残り read 数**(`nreads`):

| MODE | PWC hit(定常) | PWC miss(コールド) |
|---|---|---|
| 単段(S1/S2) | 1 | 3 |
| NESTED | 2 | 15 |

(15 = nested cold 2D walk の `(3+1)(3+1)-1`。`CLAUDE.md` 参照)

**ウォーク完了処理**:`done_valid` で IOTLB の line を fill し、同一 line+ctx の
ST_WALK エントリを**全て** ST_DONE に。→ coalesce した follower もまとめて完了。

**応答の SPA 合成**:`e_spa` は coalesce した **line ベース** SPA。各ページに別 SPA を
返すため、page の line 内オフセットを足す:

```
rsp_spa = e_spa[id] + ((e_vpn[id] & (COALESCE_FACTOR-1)) << OFFSET_W)
```

**preload ポート**:`pl_valid` / `pl_sel`(0=iotlb 1=s1pwc 2=s2pwc …)/ `pl_key` /
`pl_data`。TB が定常状態を作るためにキャッシュを温める入口。トラフィック前は
preload が fill より優先される。

**観測カウンタ**:`cnt_iotlb_hit` / `cnt_coalesced` / `cnt_walks` / `buf_occupancy`。
sim↔RTL のクロスチェックに使う。

### 2.6 `iommu_core.sv` — トップ

5 ブロックを配線するだけ。やっていること:

- 平坦化された req 信号を `req_t req_w` に組み立て(TB から扱いやすいように)
- `txn_buffer (u_front)` ↔ `walk_engine (u_walk)` を dispatch / completion で結ぶ
- `walk_engine (u_walk)` ↔ `mem_if (u_mem)` を mreq / mrsp で結ぶ
- AXI AR/R を外部(TB)へ引き出す
- 全 config パラメータと観測カウンタを外に出す

「1 つのパラメタライズド設計」であり、**"config" とはこのパラメータ集合のこと**。

---

## 3. 定常時のデータフロー(steady state)

定常状態 = PWC は warm、IOTLB には直近の line がある状態。
sequential IOVA(連続ページ DMA)を流す前提。1 リクエストの旅:

### ケース A: IOTLB ヒット(同一 64 B line の 2 ページ目以降)

```
req_valid → txn_buffer: 空き slot を ST_LOOK で確保 (e_vpn/e_ctx/e_line 記録)
          → lookup FSM: L_IDLE→L_IOTLB で IOTLB lookup
          → IOTLB hit (前ページが line を fill 済み)
          → e_spa = rdata, ST_DONE, cnt_iotlb_hit++
          → rsp_valid: rsp_spa = line_base_spa + page_offset → ST_FREE
```
メモリアクセス 0。最速。**8 ページのうち 7 ページはこれ**(coalesce_factor=8 のとき)。

### ケース B: ウォーク leader(各 64 B line の先頭ページ)

```
req → ST_LOOK → IOTLB miss かつ coalesce 相手なし
    → L_PWC: PWC probe(定常なら hit)→ nreads = 1(単段)
    → disp_valid: walk_engine が空き walker に dispatch、ST_WALK + leader、cnt_walks++
                  PWC を warm-fill
    walker: W_ISSUE → mem_if が outstanding 上限内なら AR 発行
            → 100 ns 後(TB: MEM_LATENCY cycle)に R 返却 → W_DONE
    walk_engine: mrsp_tag で leader walker へ demux、done を mux してフロントへ
    txn_buffer: done_valid → IOTLB line を fill、同一 line の ST_WALK を全て ST_DONE
    → rsp_valid で各ページに別 SPA を返す
```
1 line につき**メモリアクセス 1 回**(8 PTE をまとめ取り)。

### ケース C: MSHR コアレッシング(leader 飛行中に来た同一 line の後続)

```
req → ST_LOOK → IOTLB miss だが coalesce_hit(同一 line+ctx の ST_WALK あり)
    → ST_WALK(follower)、cnt_coalesced++、ウォークは発行しない
    → leader の done_valid で follower も一緒に ST_DONE → 各自の SPA で完了
```
重複フェッチを 1 本に集約。

### 定常時のメモリトラフィック

- 8 ページごとに 1 回のリーフ line 読み(coalescing)
- 上位レベルは PWC ヒットで read 0(`nreads=1`)
- → **約 0.13 mem/page**(`CLAUDE.md` の検証表シナリオ B 相当)

並列度は `mem_if` の outstanding(=飛行中ウォーク)で吸収。`MEM_MAX_OUTSTANDING=8`、
`NUM_WALKERS=4` がレイテンシ隠蔽の予算(Little's law: N ≈ 300ns/40.96ns ≈ 8 に対し、
coalescing 後はリーフ miss が 1/8 なので少ない N で足りる)。

---

## 4. テストベンチ(`./tb_coco/`)

cocotb + Verilator。ファイルは 2 つ:

- `run.py` … cocotb ランナー。RTL を Verilator でビルドし、テストを起動
- `test_iommu.py` … テスト本体(`@cocotb.test() happy_path`)

### 4.1 `run.py`(ビルド設定)

- ソース 7 ファイルを `iommu_core` をトップにビルド(`--timing` 付き)
- **被テスト config(Full)** をパラメータで固定:

  ```
  MODE=1 (s1_only), COALESCE_FACTOR=8, PREFETCH_EN=0,
  NUM_WALKERS=4, BUFFER_DEPTH=16, MEM_MAX_OUTSTANDING=8,
  IOTLB: 64 entries / 4-way / SRAM,
  S1PWC: 16 entries / full-assoc / FF
  ```

- 環境変数で TB に渡す: `COALESCE_FACTOR=8`, `N_REQS=256`, `MEM_LATENCY=40`
  - **`MEM_LATENCY=40` cycle ×2.5 ns = 100 ns** = 仕様のメモリレイテンシ
  - `COALESCE_FACTOR` は必ずパラメータと env を**一致**させる(line 幾何が両者で要る)

### 4.2 `test_iommu.py` が流すシナリオ

**ハッピーパス / sequential トレース**を 1 本。手順:

1. **クロック**: 2.5 ns 周期 = **400 MHz**(仕様クロック)
2. **リセット**: 5 cycle、全入力を初期化(`rsp_ready=1`, `arready=1` など)
3. **メモリスタブ起動**(`mem_stub`、coroutine):
   - AXI read slave を模擬。AR は毎サイクル受理。
   - AR 受理から **`MEM_LATENCY`(=40)cycle 後**に R を返す(`pending` リストで管理)
   - **アドレスから決定的に PTE を生成**:
     `araddr` 下位 `VPN_W` ビット = vpn → `rdata = (line_base(vpn) + PA_BASE)`
     (line_base = vpn を coalesce 単位に丸めたもの)
   - 1 サイクルに最大 1 件の R を駆動(タグ `rid` も返す)
4. **PWC preload**(`preload_pwc`):トレースが触る**全 ~2 MB 領域**について S1 PWC を warm。
   `pl_sel=1`、`pl_key = vpn>>9` 相当。これで**定常状態**を作る(PWC は常にヒット)。
5. **collector**(coroutine):`rsp_valid && rsp_ready` のサイクルで `rsp_spa` を収集
6. **トレース駆動**:`vpn = 0..N_REQS-1` を順に投入。
   - 各リクエスト後 **15 cycle 待つ → inter-arrival ≈ 16 cycle**(≈ wire-rate ペーシング)
   - `req_ready` のバックプレッシャを尊重(立つまで待つ)
7. **drain**:全 `N_REQS` の応答が揃うまで(最大 `N_REQS*MEM_LATENCY+2000` cycle)待つ

### 4.3 チェック項目(assert)

| 検査 | 内容 |
|---|---|
| 完了数 | `len(got_spa) == N_REQS`(全リクエスト完了) |
| SPA 正当性 | `sorted(got_spa) == sorted(expected_spa(v))`。`expected_spa(v)=(v+PA_BASE)<<12`。**各ページが正しい SPA に変換**されたこと |
| ウォーク数 | `rtl_walks == exp_lines`。`exp_lines = ceil(N_REQS/COALESCE)`。**coalesce された line ごとに 1 ウォーク**(タイミング非依存) |
| sim↔RTL | `abs(rtl_walks - ref_walks) <= 1`。Python 参照シム(`iommu_sim/`)を同一ワークロードで回し、ウォーク数を突き合わせ |
| 内訳整合 | `rtl_hits + rtl_coal == N_REQS - rtl_walks`。leader 以外は全て IOTLB ヒットか coalesce |

### 4.4 何を保証しているか

- **機能正当性**: 全ページが正しい SPA に変換される(アドレス合成 + coalesce オフセット)
- **アーキ正当性**: coalescing が効いて `N_REQS/8` 本しかウォークしない
- **MSHR / IOTLB**: leader 以外は必ずヒットか coalesce(重複フェッチが無い)
- **sim↔RTL 一致**: RTL とリファレンス Python シムでウォーク数が一致(`CLAUDE.md` の
  検証トレンドを RTL でも再現)

> 注意(`CLAUDE.md` "Sim ≠ timing"):このシムは**並列度が十分=アーキが正しい**ことを
> 示すもので、合成/P&R 後に 400 MHz が閉じることは保証しない。タイミングは syn 側で別途検証。

---

## 5. 1 枚でわかるまとめ

```
                      ┌──────────────────────── iommu_core ────────────────────────┐
   req (vpn,ctx) ───▶ │  txn_buffer (u_front)                                       │
                      │   ├ buffer[BUFFER_DEPTH] : ST_FREE/LOOK/WALK/DONE           │
                      │   ├ MSHR = 同一 line の ST_WALK へ coalesce                 │
                      │   ├ IOTLB(line 粒度) / S1 PWC(2MB 粒度) = cache_store     │
                      │   └ lookup FSM: L_IDLE→L_IOTLB→L_PWC                        │
                      │        │ disp(vpn,nreads,mshr)        ▲ done(spa,mshr)      │
                      │        ▼                              │                     │
   rsp (spa) ◀─────── │  walk_engine (u_walk): walker[N] + fixed-priority arbiter   │
                      │        │ mreq(addr=,tag=walker_id)    ▲ mrsp(tag→demux)     │
                      │        ▼                              │                     │
                      │  mem_if (u_mem): outstanding<MAX で AR 発行                  │
                      └────────│ AR/R ─────────────────────────────────────────────┘
                               ▼
                       TB mem_stub: AR→(40cyc=100ns)→R, PTE=f(araddr) 決定的
```

定常時の支配的経路:**8 ページ中 7 は IOTLB ヒット(mem 0)、1 はウォーク(mem 1 回で
8 PTE 取得)**。これが coalescing による「約 0.13 mem/page」の正体。
