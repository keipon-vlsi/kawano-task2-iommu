# Claude Code 実行プロンプト集(順番に実行)

このファイルは Claude Code に **STEP 1 → STEP 2 の順**で実行させるためのプロンプト集。
STEP 2 は STEP 1 が完了・検証済みであることが前提(RTL が変わると netlist/PPA が変わるため)。

実行方法:Claude Code に「`cc_prompts.md` の STEP 1 を実行して。完了条件を満たしたら
STEP 2 を実行して」と指示する。各 STEP の「完了条件」を満たさない限り次へ進まないこと。

共通規約(CLAUDE.md):コード・コメントは英語。説明ドキュメント(USAGE/ASSUMPTIONS)は日本語。
engine/policy 分離とパラメタライズ構造を壊さない。全 config がビルドできること。

---

## STEP 1 — RTL 詳細化(rtl/)

```
目的: rtl/ の Phase-1 RTL を「実機で自走する設計が持つレジスタ(DFF)を省略しない」
水準まで詳細化する。面積・電力・クリティカルパスを実機相当で評価できるようにするため。
ハッピーパスのみ通ればよく、フォルト/permission の*判定ロジック*は省いてよいが、
それらに対応する*レジスタ(ビット)*は生成すること。

== 中心: rtl/walker.sv を「完全ポインタチェイス」に ==
1. 返ってきた PTE を 64bit の本物の Sv39 PTE レジスタに latch する。
   - フォーマット: PPN + フラグ(V/R/W/X/U/G/A/D) + RSW を含む実 64bit。
   - フラグはハッピーパスで未使用でも DFF を生成する(面積・電力に効くため省略禁止)。
2. 次レベルのテーブルアドレスを PTE 内容から実際に生成する:
   next_addr = (pte.ppn << 12) | (vpn_index[level] << 3)   // PTE=8B 単位
   - 現状の合成アドレス {done_cnt, vpn} は廃止。
   - 走行中のテーブルベースを保持する running-address レジスタを持つ。
   - level レジスタ(現 done_cnt 相当)は実レベルインデックスとして保持。
3. 1段目のテーブルベースは事前ロード済みの per-context root ポインタ(satp 相当)から取る。
   txn_buffer 側に preload 済みの root/context レジスタを置き、walker へ渡す配線を追加。
4. コアレッシング(64B=8PTE まとめ取り)を実体化する:
   - リーフライン取得時に 8x64bit = 512bit のラインバッファ・レジスタへ取り込む。
   - この line buffer も省略禁止(並列 walker 時の主要面積要因)。
   - メモリ R チャネルは 64bit/beat(8 beat バースト)か 512bit/beat のいずれかで実装し、
     どちらにしたか ASSUMPTIONS.md に記録。

== 他モジュール ==
- mem_if.sv: データ幅を上記に合わせて拡張。outstanding カウンタは現状維持。
  タイミングのため応答スキッド・レジスタの要否を検討し、入れたら理由を記録。
- cache_store.sv: 既に key/data/valid を DFF 配列で持つので機能は維持。ただし
  STORAGE=sram のときの将来 SRAM マクロ化を ASSUMPTIONS.md に TODO として残す。
- txn_buffer.sv: buffer エントリは現状で register-complete。root/context レジスタ追加のみ。
- walk_engine.sv: アービタは現状コンビでよい(クリティカルパス計測対象として残す)。
- nested(MODE_NESTED)の 2D walk は、単段が完全動作したあと、同じ「レジスタ省略なし」
  方針で 2 段ネストの FSM に拡張する(今回の二次優先。単段を先に完成・検証すること)。

== クリティカルパス ==
- 今回は「詳細化 -> 計測 -> 的を絞ってパイプライン」。まず上記詳細化で実クリティカルパスを
  出せる状態にし、その後 syn フロー(STEP 2)で計測。worst path(候補: cache_store の連想
  比較 / txn_buffer の coalesce CAM + 優先エンコーダ)に限定してパイプライン段を入れる。
- パイプライン化でレイテンシ/ハンドシェイクが変わる場合は tb_coco と iommu_sim の参照側も
  更新し、throughput(wire-rate)を維持すること。

== 制約 ==
- フォルト処理・コールド context 解決・4kB データパスはスコープ外(レジスタは持つが判定省略)。
- 全 config がビルドできること。

== 完了条件(これを満たすまで STEP 2 に進まない)==
- ポインタチェイス化に伴い tb_coco のメモリスタブを「整合したページテーブル」を返すモデルに
  更新する(各 PTE の PPN が次段を正しく指す)。
- tb_coco の happy_path が通ること。「walks == coalesced lines」と SPA 正当性が成立。
- CLAUDE.md の検証トレンド(シナリオ A〜E、N≈avg_lat/inter_arrival)を再現。
- 追加した全レジスタの一覧と意図を ASSUMPTIONS.md(日本語)に記録。
```

### STEP 1 完了ゲート
上記「完了条件」を全て満たしたことを報告してから STEP 2 へ進むこと。

---

## STEP 2 — RTL→P&R ワンコマンド化(syn/)

```
目的: 既存の syn/synth_osic.py(yosys+OpenSTA) -> syn/run_pnr.sh(OpenROAD 段階 P&R +
magic GDS) -> syn/ppa_compare.py を 1 つのエントリに束ね、「RTL config とライブラリを
指定すれば RTL->論理合成->配置配線まで 1 コマンド」で流れるようにする。
既存スクリプトは再実装せず再利用(土台に拡張)。

== エントリポイント(新規生成するファイル)==
- syn/flow.py を新規作成。引数:
  --config <cfg>   (synth.py の CONFIGS / wrapper に対応する RTL パラメータ集合名)
  --lib    <hd|hs|hdll|ms|ls>   (STD_VARIANT)
  --period <ns>    (既定 2.5 = 400MHz)
  既定: --config full --lib hd --period 2.5。1 コマンドで synth->P&R->比較表->GDS まで完走。
- 各ステージは results/ にログを残し、途中失敗で既存成果物を壊さない。再実行は冪等。
  最後に pass/fail サマリを標準出力。
- USAGE(日本語)を syn/USAGE_flow.md に新規作成し、1 コマンド実行例と各出力の場所・
  読み方をまとめる。

== 出力の場所と命名(必ずこの通りに)==
run 単位の成果物はすべて per-run ディレクトリ results/<cfg>_<lib>/ 配下に集約する
(ライブラリ違いで上書きしないため。例: results/full_hd/)。
既存スクリプトが results/<cfg>.* に吐く中間物は flow.py が per-run ディレクトリへ収集する。

results/<cfg>_<lib>/report.md            … 全ステージ集約レポート(追加4)
results/<cfg>_<lib>/report.html          … 同上の HTML 版(可能なら)
results/<cfg>_<lib>/ppa_stages.md        … ステージ横断 PPA 比較表(RTL推定/synth/place/CTS/route)
results/<cfg>_<lib>/ppa_stages.json      … 同上 JSON
results/<cfg>_<lib>/synth.json           … 論理合成詳細(モジュール別面積/Fmax/クリティカルパス/電力split)
results/<cfg>_<lib>/synth_sta.txt        … OpenSTA 生ログ
results/<cfg>_<lib>/synth_area.txt       … yosys 階層 stat 生ログ
results/<cfg>_<lib>/synth_area_flat.txt  … yosys flatten stat 生ログ
results/<cfg>_<lib>/pnr.json             … P&R 各段 PPA(面積/電力の*内訳*込み=追加1)
results/<cfg>_<lib>/pnr.txt              … OpenROAD 生ログ
results/<cfg>_<lib>/power_default.json   … デフォルトトグル電力(高速・追加2)
results/<cfg>_<lib>/power_annotated.json … VCD/SAIF 注釈付き電力(高精度・追加2)
results/<cfg>_<lib>/signoff/drc.rpt      … detailed-route DRC(追加3)
results/<cfg>_<lib>/signoff/hold.rpt     … hold slack(report_checks -path_delay min)
results/<cfg>_<lib>/signoff/timing_worstN.rpt … worst-N パス(-group_path_count N)
results/<cfg>_<lib>/signoff/clock.rpt    … クロックツリー統計(skew 等)
results/<cfg>_<lib>/signoff/wirelength.rpt … 総配線長
results/<cfg>_<lib>/signoff/congestion.rpt … 混雑度(レポート or マップ画像)
results/<cfg>_<lib>/provenance.json      … git hash/ツールバージョン/lib・corner/日時/config パラメータ(追加4)
results/<cfg>_<lib>/layout.png           … klayout または magic のレイアウトレンダ
results/<cfg>_<lib>/<cfg>_<lib>.gds      … klayout で開ける GDS
results/<cfg>_<lib>/<cfg>_<lib>.def      … DEF
results/<cfg>_<lib>/<cfg>_<lib>.odb      … OpenDB
results/ppa_compare.md                   … 全ラン追記履歴(既存・共有。維持して 1 行追記)
syn/build/<cfg>/                          … 中間生成物(netlist/ys/tcl/sv2v 出力)を集約
tb_coco/sim_build/<cfg>.vcd               … 活性取得用の波形(追加2 の入力)

== 出力内容の要件 ==
[追加1: P&R 段の面積/電力の内訳]
- 現状 P&R 段は die 面積と電力 total のみ。OpenROAD で
  ・面積: セル種別別 / 階層別の内訳(report_cell_usage 等)
  ・電力: internal/switching/leakage カテゴリ別、かつ Sequential/Combinational/Clock/Macro の
    グループ別(report_power の各行)を pnr.json に格納。run_pnr.sh の Python パーサは現状
    Total 行のみなので拡張する。

[追加2: 活性注釈付き電力(VCD/SAIF)]
- tb_coco の cocotb テストを波形ダンプ付き(Verilator --trace)で走らせ tb_coco/sim_build/<cfg>.vcd
  を生成 -> VCD を SAIF(または OpenSTA read_vcd / set_power_activity)でゲートネットリストに注釈し
  report_power を実ワークロード活性で算出 -> power_annotated.json。
- power_default.json(デフォルトトグル)と power_annotated.json の両方を出し、report.md の表で並べる。
  ゲートレベル活性が必要な点・近似する場合の注意を USAGE_flow.md に明記。

[追加3: signoff 系レポート]
- DETAILED=1 経路に DRC / hold / worst-N timing / clock / wirelength / congestion を組み込み、
  上記 signoff/ 配下へ出力。

[追加4: 集約レポート + プロビナンス]
- report.md(可能なら report.html)に全ステージを 1 枚に集約。
- provenance.json に git commit hash、ツールバージョン(yosys/openroad/magic --version)、
  ライブラリ variant/corner、日時、config パラメータを記録。
- layout.png を report に埋め込む。

== 既存資産の扱い ==
- syn/synth_osic.py / syn/run_pnr.sh / syn/ppa_compare.py / syn/openlane/*.tcl を
  土台に拡張(重複実装禁止)。flow.py はこれらを順に呼び、出力を per-run ディレクトリへ収集し、
  追加1〜4 を生成する薄いオーケストレータにする。
- docker(hpretl/iic-osic-tools)前提・PDK_REF/STD_VARIANT の既存 env 互換を維持。
- ライブラリ hd 以外(hs/hdll/...)は full open_pdks ビルドが要る点を USAGE_flow.md に明記。

== 完了条件 ==
- `python3 syn/flow.py --config full --lib hd` が synth->P&R->比較表->GDS まで完走。
- 上記の出力ファイルが指定パスに生成されること(追加1〜4 を含む)。
- 生成した results/<cfg>_<lib>/<cfg>_<lib>.gds が klayout で開けること。
- syn/USAGE_flow.md(日本語)に 1 コマンド実行例と各出力の場所・読み方をまとめる。
```

### STEP 2 完了ゲート
`python3 syn/flow.py --config full --lib hd` の完走ログと、生成された
`results/full_hd/` 配下のファイル一覧を報告すること。
