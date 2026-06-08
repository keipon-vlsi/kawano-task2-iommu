# syn/flow.py — RTL→論理合成→P&R→GDS ワンコマンド実行ガイド

`syn/flow.py` は、既存スクリプト（`synth_osic.py` / `run_pnr.sh` / `ppa_compare.py`
/ `openlane/*.tcl`）を **1 コマンドに束ねる薄いオーケストレータ**です。RTL の config と
標準セルライブラリを指定すると、**論理合成 → 配置配線 → PPA 比較 → GDS** までを通しで実行し、
すべての成果物を per-run ディレクトリ `results/<cfg>_<lib>/` に集約します。

> 既存スクリプトは再実装していません。flow.py はそれらを順に呼び、出力を収集して
> 「追加1〜4」（後述）を生成するだけです。単体スクリプトも従来どおり個別実行できます。

---

## 1. 前提

- **Docker イメージ** `hpretl/iic-osic-tools:latest`（native yosys / OpenSTA(`sta`) /
  OpenROAD / magic / klayout / sky130 PDK 同梱）が必要。リポジトリは `/foss/designs` に
  マウントされます。
- **PDK**: 既定 `--pdk-ref /foss/designs/open_pdks/sky130`。これは `open_pdks` で
  ビルド済みの **全ライブラリ**（hd/hs/hdll/ms/ls/lp/hvl）を含むため、`--lib` に何を
  指定しても動きます。
  - イメージ同梱 PDK（`/foss/pdks`）は **hd / hvl のみ**。`hd` だけ使うなら
    `--pdk-ref /foss/pdks` でも可。**hd 以外（hs/hdll/ms/ls/…）を使うには
    `open_pdks` の full ビルドが必須**です（このリポジトリでは構築済み）。
- **VCD 注釈付き電力**には `.venv`（cocotb + Verilator）が必要。無くても他段は動き、
  注釈付き電力はデフォルトトグル値にフォールバックします。

---

## 2. 1 コマンド実行例

```bash
# 既定: config=full, lib=hd, period=2.5ns(=400MHz 目標)
python3 syn/flow.py

# 明示指定
python3 syn/flow.py --config full --lib hd --period 2.5

# 高速ライブラリ(hs)で、detailed-route まで(DRC signoff 付き・低速)
python3 syn/flow.py --config full --lib hs --detailed

# クロックを 4ns(=250MHz) に緩めて評価
python3 syn/flow.py --config no_coalesce --lib hd --period 4.0
```

### 主な引数
| 引数 | 既定 | 意味 |
|---|---|---|
| `--config` | `full` | `synth.py` の `CONFIGS` 名（full / no_coalesce / no_cache / full_nested） |
| `--lib` | `hd` | sky130 標準セル variant（`STD_VARIANT`）: hd / hs / hdll / ms / ls |
| `--period` | `2.5` | クロック周期 [ns]。2.5=400MHz, 4.0=250MHz |
| `--corner` | `tt_025C_1v80` | liberty コーナー |
| `--pdk-ref` | `/foss/designs/open_pdks/sky130` | PDK の libs.ref ルート |
| `--maxfo` | `16` | `repair_design` の最大ファンアウト |
| `--detailed` | off | `detailed_route` を実行（低速）。**DRC signoff はこれが必要** |
| `--no-vcd` | off | VCD 注釈付き電力をスキップ（高速化） |

- **冪等**: 同じ引数で何度でも再実行可。途中段が失敗しても既存成果物は壊しません
  （各段は独立にログを残し、収集は上書きコピー）。
- 最後に **stage ごとの PASS/FAIL サマリ**を標準出力します。終了コードは
  synth/pnr/gds が全 PASS なら 0、部分成功なら 1。

---

## 3. 出力（すべて `results/<cfg>_<lib>/` 配下）

例: `python3 syn/flow.py --config full --lib hd` → `results/full_hd/`

| ファイル | 内容 / 読み方 |
|---|---|
| `report.md` / `report.html` | **全ステージ集約レポート**。まずこれを見る。stage 合否表 / 段間 PPA / 電力内訳 / default vs VCD 電力 / レイアウト画像を 1 枚に集約 |
| `ppa_stages.md` / `.json` | 段間 PPA 比較表（アーキ推定 GE → synth → place → CTS → route）。単位が段ごとに違う点に注意（GE は相対参照、EDA 段は sky130 の um²/MHz/W） |
| `synth.json` | 論理合成詳細：モジュール別面積 / Fmax / クリティカルパス（始点・終点・支配セル）/ 電力 split |
| `synth_sta.txt` | OpenSTA 生ログ（`report_checks` のパス詳細はここ） |
| `synth_area.txt` / `synth_area_flat.txt` | yosys 階層 / flatten の `stat` 生ログ |
| `pnr.json` | **P&R 各段 PPA + 内訳（追加1）**。`stages.<STAGE>.power_breakdown` に internal/switching/leakage（カテゴリ別）と Sequential/Combinational/Clock/Macro（グループ別）。`cell_usage_raw` にセル種別使用量 |
| `pnr.txt` | OpenROAD 生ログ。`##STAGE PLACE/CTS/GROUTE/DROUTE` と `##SIGNOFF *` マーカーで区切られる |
| `power_default.json` | **デフォルトトグル電力（追加2）**。ゲートネットリスト・統計的トグル前提（高速・粗い） |
| `power_annotated.json` | **VCD 注釈付き電力（追加2）**。cocotb 実ワークロード波形で算出。`vcd_annotated` が true なら実注釈成功 |
| `signoff/drc.rpt` | detailed-route DRC（`--detailed` 時のみ実体。未実行時は注記） |
| `signoff/hold.rpt` | hold slack（`report_checks -path_delay min`） |
| `signoff/timing_worstN.rpt` | worst-N パス（`-group_path_count 10`） |
| `signoff/clock.rpt` | クロックツリー統計（skew 等） |
| `signoff/wirelength.rpt` | 総配線長 |
| `signoff/congestion.rpt` | global_route の混雑/オーバーフロー行を抽出 |
| `provenance.json` | **再現情報（追加4）**: git commit / ツールバージョン / lib・corner / period / config パラメータ |
| `layout.png` | klayout でレンダした配置配線後レイアウト（report.md に埋め込み） |
| `<cfg>_<lib>.gds` | **klayout で開ける GDS** |
| `<cfg>_<lib>.def` / `.odb` | DEF / OpenDB |
| `results/ppa_compare.md` | （共有・追記式）全ラン履歴に **1 行**追記。ライブラリ/アーキ変更で PPA がどう動くかの台帳 |

その他、中間生成物は `syn/build/<cfg>*`、活性取得波形は `tb_coco/sim_build/<cfg>.vcd`。

---

## 4. 追加1〜4 の要点

### 追加1: P&R 段の面積/電力 *内訳*
従来の P&R 段は die 面積と電力 total のみでした。flow.py は `pnr.txt` の各 `##STAGE`
ブロックの `report_power` を再パースし、`pnr.json` に
- **電力**: internal / switching / leakage（カテゴリ）× Sequential / Combinational /
  Clock / Macro（グループ）
- **面積**: `report_cell_usage`（セル種別）を `cell_usage_raw` に
を格納します。

### 追加2: 活性注釈付き電力（VCD）
1. cocotb テストを **Verilator `--trace`** で走らせ `tb_coco/sim_build/<cfg>.vcd` を生成。
2. OpenSTA で **同一ゲートネットリスト・同一タイミング条件**から `report_power` を 2 回:
   - 1 回目 = デフォルトトグル → `power_default.json`
   - 2 回目 = `read_power_activity -vcd` 注釈後 → `power_annotated.json`

> **注意（近似）**: RTL の VCD 信号名は **flatten 後のゲートネット名と一致しない**ため、
> 注釈が効くのは主にトップレベルポート等の一致ネットのみで、内部ネットはデフォルト
> 活性のまま残ります。よって annotated はゲートレベル活性を完全反映した値ではなく
> **近似**です。VCD が無い/注釈に失敗した場合は default と同値にフォールバックし、
> `vcd_annotated:false` を記録します。厳密なゲートレベル活性が必要なら、ゲートネット
> リストに対するポストシム（合成後シミュレーション）で VCD を取得する必要があります。

### 追加3: signoff 系レポート
`pnr.tcl` の global-route 後に `##SIGNOFF`（cell usage / hold / worst-N / clock skew /
wirelength）を出力し、flow.py が `signoff/*.rpt` に分割。`--detailed` 指定時は
`detailed_route` の DRC を `signoff/drc.rpt` に収集します。

### 追加4: 集約レポート + プロビナンス
`report.md`（+`report.html`）に全ステージを 1 枚集約し、`layout.png` を埋め込み。
`provenance.json` に git hash / ツールバージョン / lib・corner / 日時相当の再現情報を記録。

---

## 5. よくある落とし穴

- **hd 以外が動かない**: `--pdk-ref` が `open_pdks` の full ビルドを指しているか確認
  （既定はそれ）。イメージ同梱 `/foss/pdks` には hd/hvl しか無い。
- **GDS が空（数十バイト）**: `gds.tcl` は `def read` の前に `gds read $CELLGDS`
  （セルジオメトリ）が必要。flow 経由なら設定済み。
- **400MHz が閉じない**: 本ブロックは sky130 では 400MHz は非現実的（synth で十数〜
  数十 MHz）。`--period` を緩める / `--lib hs` / パイプライン化・SRAM 化が必要。詳細は
  `ASSUMPTIONS.md` 参照。
- **detailed_route が遅い/不安定**: 全 FF・数万セルの設計では時間がかかる。通常評価は
  global-route まで（`--detailed` 無し）で十分。

---

## 6. klayout でレイアウト確認

```bash
# GUI（X 環境）
klayout results/full_hd/full_hd.gds
# あるいは report.md / layout.png を直接閲覧
```
コンテナ内 headless では `syn/openlane/render_layout.py` が `klayout -z` で PNG を
生成しています（flow.py が自動実行）。
