# RTL 詳細説明 — ネスト 2 段 IOMMU 変換コア

1 つのパラメータ化コア(`rtl/`)を 5 つの config として実体化。本書は各モジュールの
役割・ポート・内部構造・クリティカルパス上の実体ロジックを説明する（クリティカルパス
最適化が目的なので、比較器/エンコーダ/mux/加算器は抽象化せず実 RTL）。

対象スコープ（happy path・ネスト 2 段・4KB・DDTC/PDTC ヒット後）は `ASSUMPTIONS.md` 参照。

---

## 1. `iommu_pkg.sv` — 共有型・幅・アドレスモデル
パッケージ。全モジュールが `import iommu_pkg::*`。

- **アドレス幅**: `OFFSET_W=12`, `IDX_W=9`（各レベルのページテーブルインデックス）,
  `VPN_W=GVPN_W=27`(3×9), `PPN_W=28`(SPA=40b), `GPN_W=27`(GPA=39b)。
  VS 段・G 段とも 3 レベル Sv39 形式で統一（Sv39x4 の 16KiB G ルート拡張は happy-path 外）。
- **コンテキストタグ**: `DEVICE_W=16`, `PASID_W=20`, `CTX_W=36`（VMID 無し）。
- **メモリ/PTE**: `PTE_W=64`, `LINE_PTES=8`, `LINE_W=512`（64B ライン=8PTE。B 案では
  データバスは 8B=`PTE_W` で、ラインは 8 ビートで届く。`LINE_W` は概念上の定数）。
  `PA_W=40`（バイトアドレス幅）、`TAG_W_TOP=6`（AXI read-id, 最大 37 walker をカバー）。
- **関数**（クリティカルパス上の実演算）:
  - `pte_ppn44(pte)` = `pte[53:10]`（PTE の PPN 抽出）。
  - `vidx/gidx(vpn,lvl)` = レベル別 9bit インデックス選択（`{vpn[26:18],[17:9],[8:0]}`）。
  - `pte_addr(base_ppn, idx)` = **アドレス合成加算器** `(base<<12)+(idx<<3)`。各メモリ発行で
    使われ、`iommu_top` の発行アービタ内で walker ごとに並列展開される（cfg1 で 37 個）。

---

## 2. `fa_cache.sv` — 完全連想 DFF キャッシュ（CAM）
PWC 各段と IOTLB の実体。パラメータ `ENTRIES / TAG_W / DATA_W`。

- **ストレージ**: `valid_q/tag_q/data_q`（DFF 配列）、`vptr_q`（ラウンドロビン置換ポインタ）。
- **ルックアップ（コンビ, クリティカルパス）**:
  1. `match[i] = valid_q[i] & (tag_q[i]==lk_tag)` — **全エントリ並列タグ比較（CAM）**。
  2. `lk_hit = |match` — ヒット OR。
  3. プライオリティエンコーダ + データ mux ツリー: 最下位マッチエントリの `data_q` を選択。
- **充填**: 単一ポート `fill_en/fill_tag/fill_data`、`vptr_q` の指すエントリへ書込み（FIFO 近似）。
  ストリーミング単調 IOVA では置換ポリシーはほぼ無関係（CLAUDE.md）。レベル別に独立インス
  タンス化することで、リーフ流が上位ホットエントリを追い出さない構造分離が本質。
- **面積特性**: 「lookup logic > tag > data」。CAM 比較器・優先エンコーダ・mux がストレージ
  より高い。IOTLB（16 エントリ×広タグ）が全 config 中の最大ブロック。

---

## 3. `mem_master.sv` — AXI 風タグ付きリードマスタ（8B・マルチビート）
IOMMU は PTE を読むだけ（4KB payload は範囲外）。

- **データバス 8B**（`DATA_W=PTE_W=64`、1PTE/ビート）= 実 IOMMU 相当の控えめな PTE フェッチ
  ポート。64B リーフラインは **8 ビートバースト**(`req_burst=1`→`arlen=7`)、歩進 PTE は
  **単発 1 ビート**(`arlen=0`)。
- **ポート**: 内部 `req_valid/ready/addr/tag/burst`、外部 AR(`arvalid/araddr/arid/arlen`)
  と R(`rvalid/rdata/rid/rlast`)。`rready=1`（常に受理）。
- **outstanding**: `MEM_MAX_OUTSTANDING` で**トランザクション数**（ビート数でない）を上限。
  AR ハンドシェイクで +1、`rvalid&rlast` で −1。並列ウォークの実上限。

---

## 4. `prefetch_ctrl.sv` — next-line プリフェッチトリガ（cfg4/5）
- 入力: `demand_service_v`（デマンドラインがタッチされた）, `demand_line`, `region_valid`
  （VM-L0 テーブル基底を捕捉済み）, `pf_free`（共有 walker が空き）。
- 実ロジック: **line 加算器** `tgt = demand_line + LEAD`、**同一 VM-L0 テーブルガード**
  `tgt[hi]==demand_line[hi]`（領域境界をまたぐ stale プリフェッチを禁止）、**重複抑止比較器**
  `tgt != last_q`。出力 `pf_trig / pf_line`。
- `LEAD`（lead 距離）= 1 で次ライン。大きくすれば 2MB/1GB 境界の事前リフィル（上位 PWC）。

---

## 5. `iommu_top.sv` — ネスト PTW 本体（コア）
N コンテキスト walker RF + トランザクションバッファ + MSHR + 統一発行アービタ + キャッシュ
配線 + プリフェッチ。以下のサブブロックで構成。

### 5.1 ルートレジスタ（DDTC/PDTC ヒット後）
`vs_root_spa_q`（VM-L2 テーブル基底 SPA）/ `g_root_spa_q`（G-L2 基底 SPA）。`pl_*` で事前
ロード。「VM-root PWC は常にヒット」=`vs_root_spa_q` レジスタそのもの。

### 5.2 Walker コンテキストレジスタファイル（`*_q[NCTX]`）
1 in-flight ウォーク = 1 コンテキスト。状態 `ws`(FREE/RUN/WAIT)、`wpc`(0..11 の歩進 pc)、
`wvpn/wctx`、現テーブル基底 `wbase`、table-G 用 `wgpn`、データ `wdgvpn`、結合キー `wline`、
リーフバーストのビート計数 `wbeat`。`NCTX=NUM_WALKERS`（プリフェッチも**この共有 walker を
再利用**、専用コンテキストは持たない）。

### 5.3 ネスト 12 歩進ウォーク（pc 0..11）
```
0  VM-L2 PTE   -> VM-L1 GPA      | 1-3  table-G(VM-L1 GPA) -> VM-L1 SPA  (pc3 で VM-L2 PWC 充填)
4  VM-L1 PTE   -> VM-L0 GPA      | 5-7  table-G(VM-L0 GPA) -> VM-L0 SPA  (pc7 で VM-L1 PWC 充填)
8  VM-L0 リーフ -> data GPA       | 9 G-L2->G-L1 SPA  10 G-L1->G-L0 SPA  11 G-L0 リーフ-> data SPA
```
- PWC は **G 解決済み次段基底 SPA** を格納 → ヒットで table-G サブウォークを丸ごと短絡。
- 起動時ショートカット（`start_pc`）: VM-L1 ヒット→pc8 / VM-L2 ヒット→pc4（最完全ヒット優先）。
- pc8 consume 時: G-L1 ヒット→pc11 / G-L2 ヒット→pc10。
- 充填はすべて**コンビ駆動**（consume と同サイクル、位相整合）。

### 5.4 トランザクションバッファ（`b*_q[BUFFER_DEPTH]`）
受理済み変換エントリ：`bs`(FREE/NEED/RES)、`bvpn/bctx/bspa`。`req_ready=空きエントリ有`。
解決後 `rsp_*` で応答、ハンドシェイクで解放。`rsp_vpn` を返すので順不同応答可。

### 5.5 統一メモリ発行アービタ（パイプライン）
- 候補 `iwant[w]`: ①consume した walker の次発行（融合）②launch する walker の初回発行（融合）
  ③WRUN の walker（フォールバック）。各候補のアドレスは `pte_addr` 加算器で算出。
- `iburst[w]`= (CO>1 && 発行 pc==11)。バーストは 64B ライン先頭（idx 下位 3bit クリア）アドレス。
- 回転プライオリティで 1 件/サイクル grant → その walker を WWAIT に。**consume→発行 を同一
  サイクル融合**するので 1 メモリ読み ≒ `MEM_LATENCY` サイクル（固定バブル無し）。
- **このアービタ＋加算器のコーンが全 config のクリティカルパス**（詳細 `CRITICAL_PATH.md`）。

### 5.6 メモリ復路・MSHR・結合
- タグ(=walker id)で復路を逆多重化。単発ビート → `do_consume`（歩進前進 or CO==1 リーフ完了）。
- **MSHR**: バッファエントリの `{ctx, vpn>>log2(CO)}` を在飛 walker の `wline` と連想比較
  （`bsel_line_busy`）。同一ライン在飛なら相乗り（ride）。
- **リーフバースト(pc11, CO>1)**: 8 ビートを 1/サイクル受信。**ビート j → IOTLB エントリ j を
  直接充填**（per-beat、512b 保持レジスタ不要）＋ ページ j の rider を broadcast 解決。
  `rlast` で walker 解放。これが coalescing の実体（1 ライン読み＝8 変換充足）。

### 5.7 プリフェッチ統合（cfg4/5）
- `region_vml0_q`: cold ウォークの pc7 で VM-L0 テーブル基底を捕捉（定常では demand launch が
  起きないため）。
- `pf_launch = pf_trig & wfree_v & ~svc_launch`: デマンドが walker を取らない時だけ、**共有
  walker** を pc8 から温起動して line+LEAD を先読み → IOTLB を事前充填。定常デマンドは IOTLB
  ヒットで walker に触れないので、1 walker・1 バッファで成立。

---

## 6. config ラッパ `cfgN_*/cfgN_top.sv`
`iommu_top` をパラメータ固定で実体化する薄いラッパ（= config の定義）。cocotb の最上位かつ
合成の最上位。5 つの差は `HAS_PWC/HAS_IOTLB/NUM_WALKERS/BUFFER_DEPTH/COALESCE_FACTOR/`
`PREFETCH_EN/TAG_CONTEXT_EN` のみ。

## 7. テストベンチ `tb_coco/`
`iommu_tb.py`（共有テスト）: 整合ネストページテーブルを SPA メモリに構築（G 段は恒等写像で
全ウォークステップを実行、期待 SPA=`(vpn+BASE)<<12`）、逐次連続 IOVA を wire rate で投入。
`mem_stub` は 8B バスを模擬し、`MEM_LAT` 後にビートを 1/サイクル供給（バースト 8 ビート＋
`rlast`）。検証: 全 SPA 正、結合 `walks≒N/CO`、温定常スループット ≤ 16.384 cyc/変換。
`runner_common.py` が Verilator ビルド + テスト起動を共通化、`cfgN/tb_coco/run.py` が config 別。

## 8. 合成 `syn/synth_nested.py`
Docker `iic-osic-tools` 内で sv2v → yosys 0.65（sky130 hd マップ）→ OpenSTA。per-module
面積・Fmax・クリティカルパス・電力（`report_power`）を出力。`results/nested_ppa.json` と
`cfgN/results/` に成果物。`syn/area_breakdown.py`/`control_split.py`/`fine_breakdown.py` が
内訳表・円/積み上げグラフを生成。
