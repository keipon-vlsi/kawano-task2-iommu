# PWC / IOTLB 各構造の合成後回路イメージ（詳細解説）

`cache_study/REPORT.md` の補足。各 variant を「格納(DFF) / lookup 組合せ論理 / fill 論理 /
クリティカルパス」の粒度で、**合成後の回路（比較器・mux・優先・加算器の個数と深さ）が
イメージできるよう**に解説する。数値は全て実測（post-place+resize, sky130_fd_sc_hd, ideal
clock。`results/<variant>.json` / `.stat.txt` / `.opt.log`）。ブロック図は
`figs/figures/cache_structures.png`。

## 共通の前提（合成後に何ができるか）

- **格納はすべて DFF**。1 エントリ = valid(1) + tag + SPA(44b)。PWC tag=18b、IOTLB tag=27b
  （ライン構造は分解後の幅）。
- 測定ハーネスは DUT を req/resp レジスタで挟むので、各 variant の DFF 数には**ラッパ分
  （lk_tag + hit + spa ≈ 63〜72 DFF）**が常に乗る。以下「storage DFF」= DUT 本体ぶん。
- **比較器**は合成後 `XOR/XNOR(各ビット) → AND ツリー(全ビット一致)` に展開。18b 比較なら
  XNOR×18 + 数段の AND ツリー。
- **mux**：2:1 = `mux2` 1 セル、N:1 = log2(N) 段ツリー。
- **優先エンコーダ**：match ベクタから「最小 index 優先」を作る直列 OR/AND 鎖（深い）。
- **加算器**：桁上げ伝搬（`maj3`＝全加算器の桁上げ）の連鎖で、ビット幅にほぼ比例した深さ＝
  **最も深くなりやすい地雷**。

---

# PWC（2 エントリ, tag=VPN[2:1] 18b, SPA 44b）

## P0 — 2-way フルアソシアティブ（基準）  460 MHz / 6,965µm² / DFF190 / 段数5
- **格納**：2 ×(valid1+tag18+spa44)=126 DFF + victim ptr1 + ラッパ ≈ 190。
- **lookup**：18b 比較器 2 個並列 → 各 valid と AND → `match[0/1]`。`hit=match[0]|match[1]`（2入力OR）。
  `spa = match[0]?spa[0]:spa[1]`＝44b 幅 2:1 mux（mux2 ×44, 1 段）。
- **クリティカルパス**：`xnor2 → a221o → nor4 → nand4 → mux2`（5 段）。**比較→mux が直列**
  （比較結果が mux セレクトを駆動）。2 エントリなので比較器 2 個と軽く、基準として妥当。

## P2 — 偶数整列ウィンドウ（最小面積）  480 MHz / 6,068µm² / DFF170 / 段数5
- **着想**：隣接ペアを偶数境界整列。tag を `{baseHi=tag[17:1], LSB=tag[0]}`。
- **格納**：baseHi17（共有1個）+ 2×(valid1+spa44)=107 DFF + ラッパ ≈ 170。**tag を 1 個に共有**
  できて DFF 減＝最小面積。
- **lookup**：比較は**高位 17b 等価 1 個だけ**。**エントリ選択は tag[0](LSB) が直接 2:1 mux の
  セレクト**＝比較を介さない。`hit = valid[LSB] & (17b一致)`。
- **クリティカルパス**：`xnor2 → o221ai → or4 → o31ai → mux2`（5 段）。比較幅 17b・1 系統・
  選択 LSB 直結で論理量最少。**窓判定を算術なしで実装**した好例。

## P1 — base + delta（アンチパターン）  222 MHz / 6,833µm² / DFF171 / 段数15
- **着想**：base 1 個 + `(lk_tag−base)∈{0,1}` を窓ヒット、delta LSB で選択。
- **格納**：base18 + 2×(valid+spa) ≈ DFF171（P2 並み）。
- **lookup（致命）**：`d=lk_tag−base` の **18bit 減算器**が経路先頭。合成後 **`maj3`（桁上げ）が
  4 段以上連鎖**。その後 `d[17:1]==0`（窓内）→ OR → 44b 2:1 mux。
- **クリティカルパス**：`nand2b → maj3 → maj3 → maj3 → maj3 → a21oi → o211ai → a211oi → o311ai
  → a311oi → or2 → a22o → nand4b → or4 → mux2`（**15 段**）。減算器の桁上げ連鎖がそのまま深さ。
  **同じ隣接窓を P2 は算術なし(480MHz)、P1 は減算器(222MHz)** ＝教訓「窓テストに算術を使うな」。

## P3 — sequential pointer  367 MHz / 7,124µm² / DFF190 / 段数7
- **着想**：current(hot) を 1bit レジスタで持ち、まず current を優先比較。
- **lookup**：誤ヒット不可なので **current/other の 2 比較は残る**（worst-case は両検証）。加えて
  `cur_q` で which を選ぶ **mux2i が比較の前段に増える**。
- **クリティカルパス**：`mux2i → xnor2 → nor4 → and3 → nand4 → xor2 → mux2`（7 段, P0 より深い）。
  ポインタは「共通ケースを 1 比較に」する意図だが worst-case 論理段数は増え面積も最大。
  **予測/ポインタは単一サイクル Fmax を縮めない**実例。

## P4 — speculative read（最速）  616 MHz / 6,614µm² / DFF187 / 段数5
- **着想**：tag LSB を予測 index、**SPA を即読み**（`spa[tag[0]]`）。tag 比較は hit を並列に作るだけ。
- **lookup**：`spa = spa[lk_tag[0]]`＝**44b 2:1 mux のセレクトが tag[0](入力直結)**＝比較を待たない
  最短データ経路。`hit = valid[pred] & (tag[pred]==lk_tag)`＝18b 比較は hit 専用に並列。
- **クリティカルパス**：`mux2i → o22ai → a211oi → nand4 → nor3`（5 段, 律速は hit 経路側で SPA は
  更に浅い）。**「比較→mux 直列」を「mux(予測)∥compare(検証)」並列化**で最速。実質 LSB 直接マップ。

### PWC まとめ
全 variant DFF が面積の ~7 割（comb 2.1〜2.9k µm²）。速い構造=比較を mux セレクト経路から外す
（P4 投機 / P2 LSB 直結）。遅い構造=算術を経路に入れる（P1 減算器）/ 予測 mux を前段に足す（P3）。

---

# IOTLB（16 = 2 ライン × 8, tag=VPN[2:0] 27b, SPA 44b）

VPN を `{line_tag=VPN[26:3] 24b, offset=VPN[2:0] 3b}` に分けるのが基本（offset=ライン内ページ位置）。

## T0 — ライン構成（現行設計）  344 MHz / 34,556µm² / DFF843 / 段数7
- **格納**：2 ライン ×(line_tag24+valid1+subv8+**8×spa44=352**)=770 DFF + vptr1 + ラッパ ≈ 843。
  **16 個の SPA を全部 DFF**で持つので大きい。
- **lookup**：**24b ライン比較器 2 個**。offset は**比較せず 8:1 mux セレクトに直結**（X1/X5 内包）。
  ライン一致で 2:1、計 `8:1 → 2:1`。
- **クリティカルパス**：`nor2b → a2111o → nor4 → nand4 → a21o → nor4 → mux2`（7 段）。offset は浅いが
  24b 比較 2 個 + mux ツリーで 7 段。

## T1 — 整列単一ウィンドウ（最速）  464 MHz / 32,572µm² / DFF816 / 段数5
- **着想**：16 ページを**1 整列窓**とみなし `{base=VPN[26:4] 23b, idx=VPN[3:0] 4b}`。
- **格納**：base23+valid1+subv16+**16×spa44=704**=744 DFF + ラッパ ≈ 816。
- **lookup**：**比較は 23b 窓比較 1 個だけ**（T0 の 2→1）。**16:1 mux セレクトが VPN[3:0] 直結**
  （フラット index、比較なし）。
- **クリティカルパス**：`nand2b → o2111ai → or4 → o31a → mux2`（5 段）。比較 1 系統＋フラット 16:1
  で T0 より 2 段浅い＝最速・最小。「タグ数を減らしフラット化」が効く。

## T3 — speculative read  = T1 と完全同一に合成
index 先読み + 窓 tag 並列 validate。**合成結果は T1 と Fmax/面積/段数すべて一致**。indexed-mux
構造では「投機読み」と「整列窓」は同じ回路に収束、という知見。

## T2 — sequential pointer  294 MHz / 37,868µm² / DFF843 / 段数7
T0 + current ライン 1bit。P3 同様、**2 ライン比較は残り `mux2i` 予測選択が前段に増える**ぶん
面積最大・最遅。予測は効かない。

## T4 — base + offset（最小面積 / 連続物理の賭け）  127 MHz / 12,090µm² / DFF229 / 段数28
- **着想**：ラインの 8 ページが**連続 SPA**なら 8 個の SPA を捨て **base_ppn(44b)+contig**だけ持ち、
  `spa = base_ppn + offset` を**加算器**で計算。
- **格納**：2 ライン ×(line_tag24+valid1+contig1+subv8+base44)=156 DFF + ラッパ ≈ 229。
  **16×44 の SPA 配列が消えて DFF 843→229**＝面積 1/3。
- **lookup**：2 ライン比較 + **`base_ppn + VPN[2:0]` の 44b 加算器が SPA 経路に乗る**。
- **クリティカルパス**：`… → maj3 → maj3 → nor4b×多数 → … → 加算器`（**28 段**＝全変種最深）。
  **面積最小だが Fmax 最低**の極端トレードオフ。連続物理が崩れたら contig を落としミス化（正フォール）。

## T5 — 16-way フルアソシアティブ CAM（基準）  246 MHz / 53,466µm² / DFF1228 / 段数10
- **格納**：16 ×(valid1+tag27+spa44)=1152 DFF + vptr4 + ラッパ ≈ 1228（最大）。
- **lookup**：**27b 比較器 16 個並列**（最多）。16 入力 OR で hit。**優先エンコーダ(最小index優先)
  + 16:1 mux**＝直列優先鎖。
- **クリティカルパス**：`clkinvlp → a221oi → nand4 → or3 → o21ai → o211ai → a311o → a2111oi →
  o21ai → a31oi`（10 段）。比較は並列でも**優先鎖が深い**。任意アクセスに強いが最大・遅い＝
  構造化変種の削減対象基準。

## T6 — 16-way FA, one-hot 前提（優先エンコーダ廃止）  368 MHz / 52,874µm² / DFF1228 / 段数7
- **着想**：VPN は高々 1 エントリにしかキャッシュされない ⇒ **match は one-hot** ⇒ 優先不要。
- **lookup**：格納・16 比較器は T5 と同じ。**SPA mux を `spa = OR_i(match[i]?spa[i]:0)` の AND-OR
  平衡ツリー**に置換（直列優先鎖→平衡 OR）。
- **クリティカルパス**：`clkinvlp → a2111oi → nand4 → nor4 → a22o → a221oi → nand4`（**段数 10→7**）。
  比較器・DFF は T5 と同（面積ほぼ同）だが**優先鎖を消し +50% Fmax**。FA を採るなら T5 でなく T6。
- **ゲートレベル図**：`figs/figures/t5_t6_gates.png`（T5 の直列優先鎖 vs T6 の AND-OR 平衡ツリーを対比）。

## T7 — line predictor（current 1 本だけ参照）  335 MHz / 35,515µm² / DFF842 / 段数7
ダブルバッファで current ライン専用レジスタを lookup、外れたら即ミス、offset wrap で shadow swap。
**lookup 経路は浅い**が、**合成 Fmax の律速は fill 経路**（`cur_tag → ==ft 比較 → nxt_data 書込み`
＝2 ライン判別）。安全のため validate 比較は残り、2 ライン常駐の構造コストが fill に出て T0 級止まり。
ただし data を current 1 本しか読まない（power 微利）/ 2 ラインが 16 整列でなくてよい（柔軟性）。

### IOTLB まとめ
- **比較器の数と幅**：T1(23b×1) < T0/T4(24b×2) ≪ T5/T6(27b×16)。比較器数が面積・パスに直結。
- **SPA データ格納**：T4 が 16×44 を base+adder に畳んで DFF 激減（最小）／ T5・T6 はフル（最大）。
- **mux/選択**：offset/index 直結(T0/T1/T3/T4)は浅い、優先エンコーダ(T5)は深い、廃した T6 で浅い。
- **算術は致命**：T4 の 44b 加算器で 28 段＝最遅。**予測は効かない**：T2/T7 とも validate 比較が残る。

---

## 一望（合成後の支配要因）

| | 速さの源 | 面積の源 |
|---|---|---|
| PWC | 比較を mux 経路から外す（P4 投機 / P2 LSB 直結） | tag 共有で DFF 削減（P2） |
| IOTLB | 比較器数削減＋フラット index（T1）／優先鎖廃止（T6） | SPA 配列の有無（T4 base+adder で激減 / T5・T6 最大） |
| 共通の地雷 | 経路上の**算術（減算器 P1 / 加算器 T4）**＝桁上げ連鎖で深い | **DFF（格納）が面積の支配項** |
