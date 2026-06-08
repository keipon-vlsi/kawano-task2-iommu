# IOMMU RTL 操作ガイド（構成・シミュレーション・合成・P&R）

このドキュメントは `rtl/`（SystemVerilog）・`tb_coco/`（cocotb）・`syn/`（sky130 合成）
の使い方をまとめたもの。コード/パラメータ名は英語、説明は日本語。
**1つのパラメータ化設計**で、各「コンフィグ」は**パラメータの組**にすぎない。

- 環境：プロジェクトの `.venv` に Verilator 5.046 / cocotb 2.0.1 / sv2v / yowasp-yosys /
  sky130 PDK（volare, `PDK_ROOT=$PWD/pdk`）が入っている。前提は `ASSUMPTIONS.md`（RTL phase）参照。
- Phase-1 スコープ：定常・ハッピーパス（fault 無し、文脈はプリロード、4kB データ経路は TB 側）。

---

## 1. RTL の構成

`rtl/` に5ブロック＋トップ。1ファイル＝1ブロックで、合成の per-module レポートと対応する。

| ファイル | ブロック | 役割 |
|---|---|---|
| `iommu_pkg.sv` | （共通） | 型・ビット幅・enum（Sv39+Sv39x4 アドレスモデル） |
| `cache_store.sv` | Caches | 汎用キャッシュ。`ENTRIES/ASSOC/STORAGE(ff\|sram\|cam)`、per-entry write-enable、世代フラッシュフック |
| `walker.sv` | PTW | 1ウォーク文脈の FSM（連鎖タグ付き PTE リード → SPA 合成） |
| `walk_engine.sv` | PTW+Arbiter | `NUM_WALKERS` 個のウォーカー＋メモリ要求アービタ＋R デマルチプレクサ |
| `txn_buffer.sv` | Buffer+MSHR+Caches | バッファ＋MSHR（ライン一致コアレッシング）＋IOTLB/S1-PWC/S2-PWC ルックアップ FSM＋プリロード |
| `mem_if.sv` | Memory IF | AXI 風リードマスタ（AR/R, タグ付き, `MEM_MAX_OUTSTANDING`） |
| `iommu_core.sv` | top | 上記を結線。`req/rsp`・AXI・preload・観測カウンタを公開 |

データフロー：
```
req → [txn_buffer: IOTLB/PWC lookup + MSHR] --dispatch--> [walk_engine: N walkers] --AR/R--> [mem_if] → TB memory
                 ↑                                              |
              response ←──────── 完了(SPA) ←────────────────────┘
```

### 1.1 パラメータ一覧（= 合成の `parameter` 表）

すべて `iommu_core` のパラメータ。**ここを変えれば全コンフィグが作れる**。

| パラメータ | 値 | 意味 |
|---|---|---|
| `MODE` | 0=bare /1=s1_only /2=s2_only /3=nested | 変換段構成 |
| `COALESCE_FACTOR` | 既定8（1で無効） | 64B ライン＝何ページぶんまとめ取りするか |
| `PREFETCH_EN` | 0/1 | プリフェッチ（Phase-1 はフック） |
| `NUM_WALKERS` | 例4 | 同時ウォーク数（メモリ並列度） |
| `BUFFER_DEPTH` | 例16 | トランザクションバッファ段数（=MSHR 容量） |
| `MEM_MAX_OUTSTANDING` | 例8 | メモリ同時 outstanding 上限 |
| `LOOKUP_MODE` | 0=seq /1=par /2=hyb | ルックアップ並列性（Phase-1 はフック） |
| `PIPELINE_DEPTH` | 例1 | ルックアップ/ウォーク段数（フック） |
| `CLOCK_GATING_EN` | 0/1 | クロックゲーティング（per-entry WE は実装済） |
| `IOTLB_ENTRIES/ASSOC/STORAGE` | 例 64/4/1 | 結合 IOTLB。STORAGE 0=ff/cam,1=sram |
| `S1PWC_ENTRIES/ASSOC/STORAGE` | 例 16/16/0 | S1 PWC（ASSOC=ENTRIES でフル連想=CAM） |
| `S2PWC_ENTRIES/ASSOC/STORAGE` | 例 16/16/0 | S2(G-stage) PWC |
| `DDTC_ENTRIES` / `PDTC_ENTRIES` | 例 4/4 | デバイス/プロセス文脈（プリロード前提） |

### 1.2 パラメータをどこで編集するか（用途別）

「コンフィグ＝パラメータの組」なので、**用途ごとに編集場所が違う**（RTL 本体は触らない）：

| やりたいこと | 編集する場所 |
|---|---|
| **既定値そのものを変える** | `rtl/iommu_core.sv` のパラメータ既定値 |
| **cocotb シミュレーションの構成** | `tb_coco/run.py` の `PARAMS` 辞書（＋環境変数 `COALESCE_FACTOR` を一致させる） |
| **合成するコンフィグ** | `syn/synth.py` の `CONFIGS` 辞書（名前→パラメータ辞書を追加/編集） |
| **P&R するコンフィグ** | まず `syn/synth.py <name>` でそのパラメータの Verilog を生成し、`syn/openlane/config.json` の `DESIGN_NAME`/`VERILOG_FILES` を指す |

> 仕組み：合成・P&R では sv2v がトップをエラボレートするため、**各コンフィグは
> `syn/synth.py` が生成する薄いラッパ `cfg_<name>`（`iommu_core #(...) u(.*)` でパラメータを固定）**
> を介して指定する。これにより RTL 本体を編集せずに任意のパラメータ組を合成できる。

---

## 2. RTL シミュレーション（cocotb + Verilator）

ハッピーパス検証＋ sim↔RTL クロスチェック（Python リファレンス sim と walk 数を突合）。

### 2.1 まず lint（任意・速い）
```bash
cd rtl
verilator --lint-only -Wno-fatal -Wno-UNUSEDPARAM -Wno-UNUSEDSIGNAL -Wno-WIDTHEXPAND \
  -Wno-DECLFILENAME --timing --top-module iommu_core \
  iommu_pkg.sv cache_store.sv mem_if.sv walker.sv walk_engine.sv txn_buffer.sv iommu_core.sv
```

### 2.2 cocotb テスト実行
```bash
cd tb_coco
../.venv/bin/python run.py
```
- 構成は `tb_coco/run.py` の `PARAMS`（Verilator に `parameters=` で渡る）で指定。
- ワークロード規模は環境変数：`N_REQS`（既定256）・`MEM_LATENCY`（既定40）・`COALESCE_FACTOR`。
  例：`N_REQS=1024 MEM_LATENCY=40 ../.venv/bin/python run.py`
- 期待結果（Full）：全変換が正しい per-page SPA で完了し、
  `RTL: walks=32 … REF sim: walks=32`（=256/8 ライン）で **sim↔RTL 一致**、`PASS`。

別コンフィグを試す：`tb_coco/run.py` の `PARAMS` を書き換える（例 `MODE=3` でネスト、
`COALESCE_FACTOR=1` でコアレッシング無効）。`COALESCE_FACTOR` は env も合わせること。

### 2.3 波形（任意）
Verilator のトレースを有効化する場合は `run.py` の `build_args` に `--trace` を足し、
TB で `cocotb` 実行すると `sim_build/` に VCD が出る（後述のゲートレベル電力見積りにも使える）。

---

## 3. 論理合成（Yosys + sv2v + sky130）

`syn/synth.py` が「ラッパ生成 → sv2v → yosys（generic synth → sky130 `sc_hd` マップ）
→ per-module 面積（stat）＋クリティカルパス（ltp）」を一括実行する。

### 3.1 実行
```bash
export PDK_ROOT=$PWD/pdk
.venv/bin/python syn/synth.py full            # Full コンフィグ
.venv/bin/python syn/synth.py full no_coalesce no_cache full_nested   # 複数まとめて
```
出力：
- `results/<name>.json` … per-module 面積・クリティカルパス（深さ/経路モジュール）
- `results/<name>_area.txt` … yosys `stat -liberty` 生ログ（セル別面積）
- `results/<name>_timing.txt` / `_ltp.txt` … クリティカルパス（ltp）生ログ
- `results/ppa_full.md` … Full のまとめ表

### 3.2 合成コンフィグ（パラメータ）の指定方法
`syn/synth.py` の `CONFIGS` 辞書を編集／追加する。1エントリ＝1パラメータ組：
```python
CONFIGS = {
  "full": dict(MODE=1, COALESCE_FACTOR=8, NUM_WALKERS=4, BUFFER_DEPTH=16,
               MEM_MAX_OUTSTANDING=8, IOTLB_ENTRIES=64, IOTLB_ASSOC=4, IOTLB_STORAGE=1,
               S1PWC_ENTRIES=16, S1PWC_ASSOC=16, S1PWC_STORAGE=0),
  "my_cfg": dict(MODE=3, COALESCE_FACTOR=1, NUM_WALKERS=8, BUFFER_DEPTH=32, ...),
}
```
`python syn/synth.py my_cfg` で、`syn/build/cfg_my_cfg.sv`（ラッパ）と `syn/build/my_cfg.v`
（sv2v 出力）が生成され、それを合成する。

### 3.3 ライブラリ・クロック
- 標準セル liberty：`pdk/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib`
  （`syn/synth.py` の `LIB`）。slow コーナーで見るなら `__ss_100C_1v40.lib` に変更。
- 目標クロック：`syn/synth.py` の `TARGET_PERIOD_PS = 2500`（400 MHz）。

---

## 4. 合成後の確認（面積・クリティカルパス・周波数・電力・ゲートレベル sim）

### 4.1 面積（確認できる）
```bash
cat results/full.json            # total_area_um2 と area_um2_per_module
sed -n '/Chip area for top/p' results/full_area.txt
```
Full の例：**total 526,057 µm²**（sky130 sc_hd tt）。キャッシュが ~72%（IOTLB 294k＋PWC 84k、
全て FF マップ）。per-module 内訳は `ppa_full.md` 参照。

### 4.2 クリティカルパス（確認できる）
```bash
grep -A20 "Longest topological path" results/full_ltp.txt
```
Full の例：`mem_if.can_issue` → `walk_engine` アービタ → `txn_buffer` FSM →
**S1 PWC の連想（CAM）ルックアップ**。深さ713（generic levels）。
→ 次フェーズは**このルックアップのパイプライン化**で 400 MHz を狙う、という指針が出る。

### 4.3 動作周波数（Fmax）・クリティカルパス・電力（IIC-OSIC-TOOLS で取得）
`/space/iic-osic-tools` の docker に native yosys + OpenSTA + sky130 PDK があるので、
**`syn/synth_osic.py` がこれを使って実 Fmax・クリティカルパス・電力まで出す**（面積のみの
`syn/synth.py` の上位版）。
```bash
.venv/bin/python syn/synth_osic.py full          # docker 経由で yosys(flatten)+OpenSTA
cat results/full.json                            # 面積/Fmax/クリティカルパス/電力が全部入り
```
Full の実測（合成のみ・P&R前）：**Fmax ≈ 19.5 MHz（crit 51.4 ns, WNS −48.9 ns, 400MHz 未達）**、
**電力 0.292 W**（internal 0.258 / switching 0.034 / leakage ~0）。
クリティカルパスの支配セルは **fanout 466 の `nor4`（単体31ns）**＝CAM 連想一致リダクション。
→ ルックアップ/アービタのパイプライン化＋P&R のバッファ挿入で改善（§4.3 の値は P&R 前の最悪値）。
`results/full_sta.txt` に OpenSTA の `report_checks`（パス詳細）と `report_power` の生ログ。

> 注：この OpenSTA 数値が**サインオフ前の合成段階の値**。P&R 後は OpenLane（§5）でさらに正確化。
> 以下は OpenSTA を手で叩く場合の参考（`syn/synth_osic.py` が自動でやっている内容）：

- **Fmax**：合成ネットリストを OpenSTA に読ませ、目標周期で slack を見る。
  `Fmax = 1 / (CLOCK_PERIOD − worst_slack)`。
  ```tcl
  # OpenSTA 例（sta コマンド）
  read_liberty .../sky130_fd_sc_hd__tt_025C_1v80.lib
  read_verilog results/full_netlist.v          ; link_design cfg_full
  create_clock -name clk -period 2.5 [get_ports clk]
  report_checks -path_delay max -fields {slew cap input nets} -group_count 5
  report_wns ; report_tns
  ```
- **電力**：cocotb 実行で VCD（§2.3）を出し、OpenSTA `read_vcd` →
  ```tcl
  read_power_activities -vcd sim_build/dump.vcd
  report_power                                  ; # internal/switching/leakage
  ```
  これは simulator の per-module 正規化電力（`iommu_sim` の estimator）と突合する校正対象。

### 4.4 合成後ネットリストの生成（ゲートレベル sim 用）
`syn/synth.py` の面積パスに `write_verilog` を足すか、直接 yosys で：
```bash
LIB=pdk/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib
.venv/bin/yowasp-yosys -p "read_verilog syn/build/full.v; hierarchy -top cfg_full; \
  synth -top cfg_full -flatten; dfflibmap -liberty $LIB; abc -liberty $LIB; \
  write_verilog results/full_netlist.v"
```

### 4.5 合成後（ゲートレベル）シミュレーション
RTL と同じ cocotb TB を**マップ済みネットリスト＋sky130 セルモデル**に対して流し、機能等価を確認する。
```bash
# tb_coco/run.py の sources をネットリスト＋セルモデルに差し替えて実行する例：
#   sources = ["../results/full_netlist.v",
#              "$PDK_ROOT/sky130A/libs.ref/sky130_fd_sc_hd/verilog/primitives.v",
#              "$PDK_ROOT/sky130A/libs.ref/sky130_fd_sc_hd/verilog/sky130_fd_sc_hd.v"]
#   hdl_toplevel = "cfg_full"      # ラッパ名
# build_args に -Wno-fatal -Wno-TIMESCALEMOD などを追加。
../.venv/bin/python run.py
```
ポイント：トップは `cfg_full`（ラッパ）。RTL sim と同じ刺激・同じチェックで**合成前後一致**を確認できる。
セルモデルのタイミングを使う厳密 GLS は本来の遅延 sim（SDF 注釈）まで行うが、それは P&R 後が一般的。

---

## 5. P&R（sky130, OpenROAD）

**小規模なら P&R まで一気に通して評価するのが楽**。配置→`repair_design`（max-fanout バッファ
挿入・サイジング）→（CTS/配線）まで EDA が自動でやるので、§4 の合成のみ最悪値（fanout466 が
そのまま=Fmax 19.5MHz）と違い、**現実的・信頼できる PPA**が一発で出る。
この環境には OpenLane は無いが **OpenROAD はある**（OpenLane が中で叩いているのと同じ）ので、
`syn/run_pnr.sh`（`syn/openlane/pnr.tcl` を docker 実行）で配置＋修復 PPA を出せる。

### 5.1 実行コマンド（検証済み）
```bash
export PDK_ROOT=$PWD/pdk
# 1) コンフィグの合成（ゲートネットリスト syn/build/<name>_netlist.v を生成）
.venv/bin/python syn/synth_osic.py full
# 2) P&R（配置 + repair_design）。引数: name period_ns max_fanout
bash syn/run_pnr.sh full 2.5 16
cat results/full_pnr.json
```
実測（Full, 配置+修復, CTS/配線前）：**Fmax ≈ 60 MHz（crit 16.6 ns, slack −14.1 ns）／
die 530,988 µm² @ 37% util／電力 0.47 W**（internal 0.30 / switching 0.17）。
合成のみ 19.5 MHz → P&R で 60 MHz に改善（fanout466 がバッファツリー化された）。
ただし依然 400 MHz 未達で、経路は挿入バッファの長い連鎖＝**本質的にはパイプライン化が必要**。

### 5.2 パラメータ（コンフィグ）の指定方法
- **アーキパラメータ**：`syn/synth.py`/`synth_osic.py` の `CONFIGS`（→ ネットリストに焼き込み）。
- **P&R 制約**：`syn/run_pnr.sh` の引数（`period_ns`・`max_fanout`）と `syn/openlane/pnr.tcl`
  内の `set_max_fanout`/`set_max_transition`/`initialize_floorplan -utilization`。
  例：`bash syn/run_pnr.sh full 2.5 8`（max-fanout を 8 に絞る）。
- **セルライブラリ**：`pnr.tcl` の LEF/LIB を別バリアント（`sky130_fd_sc_hs` 等）に。

### 5.3 P&R 後の PPA 取得・読み方
`results/<name>_pnr.json`（`run_pnr.sh` が自動生成）または生ログ `results/<name>_pnr.txt`：
- **面積**：`Design area NNN um^2 PP% utilization`（→ `die_area_um2`）。
- **Fmax / クリティカルパス**：`report_worst_slack -max`（`worst slack max <s>`）。
  `Fmax = 1000 / (period − s)` MHz、`s ≥ 0` で目標達成。`report_checks` に経路詳細。
- **電力**：`report_power` の `Total` 行（internal / switching / leakage / total[W]）。
  正確化は §4.4/4.5 のゲートレベル sim の VCD を `read_power_activities -vcd` で注釈。
- これらを `iommu_sim` の**凍結予測 PPA**（`iommu_sim/freeze/*.json`）と突合し、
  per-module 校正係数を fit（estimate↔synth キャリブレーション＝次フェーズ）。

### 5.3b サインオフまで詰める（任意）
`pnr.tcl` 末尾に CTS→配線→寄生抽出を足すと配線込みの値になる：
```tcl
clock_tree_synthesis -buf_list sky130_fd_sc_hd__clkbuf_4 -root_buf sky130_fd_sc_hd__clkbuf_16
set_propagated_clock [all_clocks]
global_route
estimate_parasitics -global_routing
report_worst_slack -max ; report_power
```
完全自動のサインオフフローが要るなら OpenLane（要インストール）に `syn/openlane/config.json`
（`SYNTH_MAX_FANOUT` 等を設定済み）を渡す。

### 5.4 SRAM マクロ版（ストレージパターン実験・後フェーズ）
キャッシュを FF でなく SRAM マクロにする場合は、cache の `STORAGE=sram` 化に加え、
`config.json` に sky130 SRAM マクロの LEF/LIB（`EXTRA_LEFS`/`EXTRA_LIBS`）とマクロ配置を追加する。
all-SRAM / all-DFF / mixed の3パターン比較は後フェーズのサブ実験。

---

## 6. まとめ（最短コマンド）
```bash
# RTL シミュレーション
cd tb_coco && ../.venv/bin/python run.py

# 論理合成（Full）＋面積・クリティカルパス
export PDK_ROOT=$PWD/pdk
.venv/bin/python syn/synth.py full
cat results/full.json ; grep -A20 "Longest topological" results/full_ltp.txt

# 合成後ネットリスト → ゲートレベル sim（§4.4/4.5）
# P&R（要 OpenLane）：syn/synth.py <name> で .v 生成 → openlane syn/openlane/config.json
```
パラメータ変更箇所：**sim=`tb_coco/run.py`／合成=`syn/synth.py` CONFIGS／P&R=同 CONFIGS＋`config.json`／
既定値=`rtl/iommu_core.sv`**。
