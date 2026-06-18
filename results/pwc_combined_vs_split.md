# PWC: 結合型 (combined) vs 分離型 (split) の PPA 比較

VS-stage の page-walk cache の作り方の比較。
- **combined（現行設計）**：VPN でルックアップし **G-stage 解決済みの SPA を返す**（1 段ルックアップ）。
- **split**：VPN で VS PWC を引き、ヒットしたエントリの **GPA で別の G-PWC を連鎖ルックアップ**して SPA を得る（2 段連鎖）。

## 測定方法

VS-stage の PWC ルックアップ部のみを単体モジュール化して同一フローで合成し、**キャッシュ方式そのものの
構造的 PPA 差を分離測定**（`syn/pwc_compare.py`）。同じ `fa_cache` プリミティブ・同じ幅
（no-context tag: VML2=9b, VML1=18b, data=48b）・同じ P&R（hd, canonical knobs, ideal clock）。
- combined = `vml2(1) + vml1(2)`（SPA 格納）→ most-complete-hit mux → SPA（1 ルックアップ）。
- split = `vml2(1) + vml1(2)`（GPA 格納）→ GPA → **連鎖**で `gl2(1) + gl1(2)`（GPA→SPA）→ SPA。

## 結果（post-opt, hd, @2.5ns target）

| 方式 | Fmax [MHz] | area(post-opt) [µm²] | area(synth) [µm²] | power [mW] |
|---|---|---|---|---|
| combined | 400.0 | 10,242 | 9,151 | 4.356 |
| split | 406.5 | 20,048 | 17,937 | 8.680 |
| **Δ split vs combined** | **+1.6%** | **+95.7%** | **+96.0%** | **+99.2%** |

## 解釈

- **面積・電力はほぼ 2 倍（+96% / +99%）**：split は VS PWC に加えて **GPA→SPA 用の G-PWC を丸ごと
  追加**するため（連鎖の 2 段目）。これがロバストな主結果。
- **Fmax は本測定では差が出ない（両者 ≥400MHz）**：単体モジュールが小さく slack が大きいため、split の
  連鎖 2 段ルックアップの段数増がまだ律速にならず、resizer が両者とも 2.5ns を閉じてしまう。
  **実エンジンでは PWC ルックアップは（v4 以降の）クリティカルパス近傍にあり、連鎖の 2 段目が深さを
  足すので、フルエンジンでは Fmax は負方向に効く見込み**（正確な数値はフルエンジン variant が必要）。
- **本タスク（単一コンテキスト・静的・VS テーブルは少数で高再利用）では split の機能的利得はゼロ**
  （steady state はどちらもキャッシュヒットで追加メモリアクセス 0）。よって **combined が面積/電力で
  約 2 倍有利、性能は同等以上**＝圧倒的に combined 優位、という前回の定性評価が定量的に裏付けられた。

## 注意（この測定が捉えていないもの）

- VS-stage のルックアップ構造のみの分離測定。フルエンジンでは split はさらに **walk FSM の連鎖制御**
  （VS ヒット→G ルックアップ→ミス時 G サブウォーク）と**追加の制御論理**が要り、オーバーヘッドは
  本測定の差（下限）**以上**になる。
- データリーフ経路（IOTLB + データ用 G-PWC）は combined/split で不変。差は VS テーブル caching のみ。
- split が有利になるのは多コンテキスト/多 VM で G 翻訳を共有する場合（本タスクでは該当せず）。

再現: `python3 syn/pwc_compare.py`。ワークスペース `syn/pwc_cmp/` は gitignore（再生成可）。
