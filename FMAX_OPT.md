# Fmax 改善: 合成「制約とサイジング」のみ（RTL 不変）

OpenLane 相当の OpenROAD フローで、**RTL/合成ネットリストを一切変えず**、SDC 制約と
リサイザ（buffer/sizing）だけで Fmax を改善した記録。OpenLane メタフローはイメージに無く
（OpenROAD 26Q2 直叩き）、`syn/fmax_opt/opt.tcl` で実施。各 run の STA ログは
`results/fmax_opt/`。

## フロー（`syn/fmax_opt/opt.tcl`）
合成ネットリスト（`cfgN/results/netlist.v`）を入力に：
1. `create_clock`、`set_max_transition`/`set_max_fanout`、`set_driving_cell`(buf_2)、
   `set_load`、`set_input/output_delay`（SDC）。
2. floorplan + global placement（実寄生）→ `estimate_parasitics`。
3. **`repair_design`**（slew/cap/fanout DRC 修復＝高 fanout ネットをバッファツリー化）。
4. **`repair_timing -setup`**（setup クリティカルパスのセル・サイジング/バッファ挿入）。
5. detailed placement → STA。CTS/route はスイープ高速化のため省略（ideal clock。CTS は
   ~0.2–0.5ns の skew/insertion を追加するので、最終 Fmax はこれより数 % 低下し得る）。

既存の `pnr.tcl` は `repair_design` のみ（DRC）。本作業の要は **`repair_timing -setup` の
追加**と SDC（max_transition/fanout、driving cell、load、IO delay）。

## cfg5：ノブ別の効果（周期 2.5ns）
| ノブ段階 | 内容 | Fmax | path 最大 fanout | path slew | 面積 µm² |
|---|---|---|---|---|---|
| k0 post-place | repair 無し（実寄生のみ） | 51.8 MHz | 50 | 0.244 ns | 95,374 |
| k1 +repair_design | 高 fanout/reset をバッファ化（DRC） | 84.4 MHz | 16 | 0.088 ns | 102,000 |
| k2 +repair_timing | setup サイジング/バッファ | **171.2 MHz** | 5 | 0.041 ns | 104,279 |
| k3 +SDC tight | driving/load/IO + mt0.5/fo12 | 160.8 MHz | 9 | 0.053 ns | 110,465 |

- 起点 FF fanout=34→ツリー化（`buf_12` で駆動、path 上 fanout 50→5）。
- 致命ゲート `nor4b_1`(fanout20, slew1.99ns) の overload **解消**。
- reset net(`rst_n`→全 FF, slew 42ns) も `repair_design` がバッファ。
- DRC: max_slew/fanout/cap 違反は post で解消（slew<0.5ns、fanout≤12）。
- 代償: 面積 +9〜16%（バッファ＋アップサイズ）。

## cfg5：周期スイープ（k3 full knobs）
| 目標周期 | 5.0 | 4.0 | 3.5 | 3.0 | 2.5 ns |
|---|---|---|---|---|---|
| Fmax | 146.8 | 160.0 | 160.8 | 160.5 | 160.8 MHz |

→ ~160 MHz で飽和（リサイザが達成可能限界まで最適化）。k2（slew 緩め 0.75）は 171 MHz と
僅かに上（バッファ少なめ）。

## 全 config：post-opt（制約+サイジング後）
| | cfg1 | cfg2 | cfg3 | cfg4 | cfg5 |
|---|---|---|---|---|---|
| Fmax 合成見積(ideal) | 17.2 | 38.5 | 48.6 | 74.9 | 99.5 MHz |
| **Fmax post-opt** | _light_ | **105.4** | **103.0** | **147.9** | **160.8** MHz |
| 改善率 | — | 2.7× | 2.1× | 2.0× | 1.6× |
| 面積 post-opt µm² | — | 126,420 | 186,325 | 164,742 | 110,465 |
| 電力 post-opt mW@400 | — | 59.1 | 91.7 | 58.7 | 40.6 |

cfg1（37 walker）は `repair_timing` が大規模で非現実的なため `repair_design` のみ（light）。
cfg2–5 はフルフロー。改善率は cfg5(1.6×)〜cfg2(2.7×)。post-opt は実寄生込みでも合成見積を
上回る（リサイザの buffer/size が効く）。同一 P&R 基準での純粋効果は cfg5 で 51.8→160.8 = **3.1×**。

## 効いたノブ（順）
1. **`repair_design`**（高 fanout/reset のバッファツリー化）= 単独で最大効果（cfg5 +63%）。
2. **`repair_timing -setup`**（setup パスのサイジング/バッファ）= さらに +倍。
3. **SDC**（`set_max_transition`/`set_max_fanout`）= slew/fanout DRC をクリーンに。
   driving_cell/load/IO delay は reg2reg クリティカルパスにはほぼ無影響（IO パス用）。

## 完了条件
- **RTL 差分ゼロ**（`rtl/` 無変更）。入力は合成済みネットリスト（`cfgN/results/netlist.v`）。
- ノブ別効果・到達 Fmax は上表。各 run の STA ログ: `results/fmax_opt/*.log`、
  サマリ: `results/fmax_opt/{summary,postopt}.json`。
