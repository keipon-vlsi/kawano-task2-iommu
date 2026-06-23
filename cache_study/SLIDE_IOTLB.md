# スライド：IOTLB キャッシュアーキテクチャ比較（簡潔版）

プレゼン用の 1〜2 枚分。数値は実測（sky130 hd, post-opt, ideal clock）。図は
`figs/figures/cache_structures.png`（各構造）/ `t5_t6_gates.png`（T5 vs T6 ゲートレベル）。

## 2 つのキーメッセージ

1. **同じ CAM 型 IOTLB でも、RTL の書き方で回路も周波数も大きく変わる**
   — 格納・16 比較器は同一。**(a) 同じ優先選択を 2 通りに書くだけ**（T5 累算ループ vs T8 ネスト三項、
   yosys 抽象は同一だが abc 感度で）**246↔284 MHz（+15%）**。**(b) 優先論理を消す**（T6 one-hot）と
   **368 MHz（vs T5 +50%）**。＝書き方で +15%、構造（優先の有無）で +50%。
2. **構成をライン化（line-tag + offset 直接 index）すると、フラット CAM より小さく・速い**
   — offset を比較せず mux の index に直結 → 比較器を減らし幅も狭く。**面積 −35%**（53k→34k）。

## 各アーキテクチャ（簡潔説明・スライド表）

| 版 | 構造（1 行） | Fmax [MHz] | 面積 [µm²] | 段数 | 一言 |
|---|---|---|---|---|---|
| **T5** CAM 優先選択（累算ループ） | 16 比較器 + lowest-index-wins 優先 mux | 246 | 53,466 | 10 | 優先選択の書き方① |
| **T8** CAM 優先選択（ネスト三項） | **T5 と論理同一**（yosys 抽象 16$mux+31$eq 一致） | 284 | 53,787 | 11 | 書き方②。abc 感度で +15% |
| **T6** CAM + one-hot | one-hot 前提 → **優先を廃止** → AND-OR 平衡ツリー | **368** | 52,874 | 7 | CAM の最速形（+50% vs T5） |
| **T0** ライン（現行） | **ライン tag 2 比較 + offset 8:1 mux**（offset は無比較 index） | 344 | **34,556** | 7 | 小さく堅実。coalescing と整合 |
| **T1** 整列単一窓 | tag 1 比較 + 16:1 index（最速・最小） | **464** | **32,572** | 5 | ただし **128B 整列前提**（下記）|
| **T4** base + offset | SPA を捨て **base + 加算器** | 127 | **12,090** | 28 | 面積 1/3 だが加算器で最遅 |

（参考：T7 line-predictor 335 / 35,515、T2 seq-pointer 294 / 37,868 — どちらも予測/ポインタは Fmax を縮めず）

## 各構造の特徴を表すコード（1〜2 行）

match 計算は共通なので、それを SPA/hit にどう落とすか＝構造の個性が出る部分のみ。

```systemverilog
// T0  ライン構成（現行）: ライン tag 2 比較 + offset を無比較で 8:1 mux
assign m0 = lval_q[0] & (ltag_q[0]==lt) & subv_q[0][lo];       // 24b ライン比較 ×2
assign lk_spa = m0 ? data_q[0][lo] : data_q[1][lo];            // offset lo は直接 index

// T1  整列単一窓: tag 比較 1 個 + 16:1 を VPN[3:0] で直接 index
assign lk_hit = lval_q & (lk_tag[26:4]==base_q) & subv_q[idx]; // 23b 窓比較 1 系統だけ
assign lk_spa = data_q[idx];                                   // idx=VPN[3:0] フラット 16:1

// T2  sequential pointer: current ラインを優先比較
assign mc = lval_q[cur_q] & (ltag_q[cur_q]==lt) & subv_q[cur_q][lo];  // cur を先に見る
assign lk_spa = mc ? data_q[cur_q][lo] : data_q[~cur_q][lo];

// T3  speculative read: index で即読み、tag は並列 validate（合成は T1 と同一に収束）
assign lk_spa = data_q[idx];                                   // 比較を待たず読む
assign lk_hit = lval_q & (lk_tag[26:4]==base_q) & subv_q[idx]; // hit は並列に作るだけ

// T4  base + offset: SPA を捨て base_ppn + offset の加算器（最深）
assign lk_spa = (m0 ? base_q[0] : base_q[1]) + {41'd0, lo};    // 44b 加算器が SPA 経路に乗る

// T5  16-way CAM 優先選択（累算ループ）: 最小 index 優先
always_comb begin lk_spa='0; for (int i=15;i>=0;i--) if (match[i]) lk_spa=spa_q[i]; end // priority

// T8  T5 と論理同一の優先選択（ネスト三項で記述）: yosys 抽象は T5 と同じ 16$mux+31$eq
assign lk_spa = match[0]?spa_q[0]: match[1]?spa_q[1]: /*…*/ : match[15]?spa_q[15]: 44'd0;

// T6  16-way CAM + one-hot: 優先を廃止、AND-OR 平衡ツリー（← 真の構造差。+50%）
always_comb begin lk_spa='0; for (int i=0;i<16;i++) lk_spa |= ({44{match[i]}} & spa_q[i]); end // one-hot OR

// T7  line predictor: 予測した current ライン 1 本だけ参照（外れたら即ミス）
assign lk_hit = cur_val_q & (cur_tag_q==lt) & cur_subv_q[lo]; // もう一方は見ない
assign lk_spa = cur_data_q[lo];

// T8  優先を 2:1 mux 直列カスケードに埋込: 前向きネスト三項（16 段依存鎖）
assign lk_spa = match[0]?spa_q[0]: match[1]?spa_q[1]: /*…*/ : match[15]?spa_q[15]: 44'd0;
```

対比の軸：**比較器の数/幅**（T1 23b×1 < T0/T2/T7 24bライン ≪ T5/T6/T8 27b×16）、**offset の扱い**
（T0/T1/T3/T4 は無比較 index `[lo]`/`[idx]`）、**縮約**（T5 直列優先 / T8 mux カスケード=最深 /
T6 one-hot OR ツリー=最速 CAM）、**算術**（T4 だけ `+` が SPA 経路＝28 段で最遅）。

## 効く / 効かない（スライド下部の学び 1 行）

- **効く**：比較器を減らす・狭める（**ライン化**）／優先論理を消す（**one-hot, T6**）。
- **効かない・逆効果**：経路に**算術**を入れる（T4 加算器, PWC P1 減算器）／**予測・ポインタ**（T2/T7）。

## ライン構成 vs フラット CAM（メッセージ②の根拠）

| | ライン構成 (T0/T1) | フラット CAM (T5/T6) |
|---|---|---|
| 面積 | **32–34k** | 53k（**約 1.5 倍**） |
| Fmax | T0 344 / **T1 464** | T5 246 / T6 368 |
| なぜ | offset を無比較 index、比較は線 tag だけ（数↓幅↓） | 16 個の全幅(27b)比較 + 優先/縮約 |

→ **効率（面積×速度）でライン構成が CAM を圧倒**。fastest+smallest は T1、堅実な現実解は T0。

## T1 の使用条件と整列制約（誤用防止・必ず添える）

T1 は **16 ページが「16 ページ整列ブロック」に収まる時だけ** 16 エントリをフル活用できる。
別表現：**ページ番号 16 整列 = IOVA 64 KB 整列 = PTE 上 128 B 整列**（16 PTE×8B＝連続 2 本の 64B ライン）。

- **使える**：(1) DMA バッファが 64 KB 整列割り付けでブロック境界に揃う、(2) 同時 working set が 16 ページ
  以内（境界跨ぎ両側同時保持が不要）、(3) superpage（2 MB ページなら 4 KB を 16 個並べる必要が消える）、
  (4) プリフェッチでブロック境界に合わせ window 切替できる設計。
- **使えない**：任意オフセットの連続ストリーム（本タスク一般形）。**境界跨ぎで前半が消える**
  → 実測ヒット **T1=5/16（T0=13/16）**（`results/iotlb_align_demo.txt`）。誤ヒットはせず walk フォール
  なので機能は正しいが実効容量が落ちる。
- **本タスクの結論**：coalescing が保証するのは **8 整列（64B ライン）まで**で 128B 整列は保証されない。
  よって **第一候補は T0（2 ライン×8, coalescing と整合・境界跨ぎに強い）/ T7**、T1 は上記の整列保証がある
  特殊条件（整列割り付け・superpage・ブロック単位アクセス）でのみ最速・最小として採用。
