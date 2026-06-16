# クリティカルパス詳細と最適化アプローチ（各 config）

sky130_fd_sc_hd, tt 1v80, 目標 2.5ns(400MHz)。OpenSTA `report_checks -path_delay max`。
Phase 2 にはまだ進まないが、各 config の遅延の正体と取りうる手を整理する。

## 共通：クリティカルパスの正体
全 config でパスは **FF → 組合せロジック → FF** で、組合せ部は基本的に同じコーン：

```
walker 状態 FF
   → キャッシュヒット / 最完全ヒット短絡判定（PWC/IOTLB の CAM 比較＋優先エンコーダ）
   → 統一メモリ発行アービタ（WRUN/consume/launch 候補の回転プライオリティ選択）
   → 選択 walker の pte_addr=(base<<12)+(idx<<3) アドレス合成加算器（候補ごとに並列展開）
   → メモリ要求アドレス / 次状態 FF
```

`consume→次発行` を同一サイクルで融合しているため、このコーンが 1 本に伸びる。長さを
決める主因は **(a) アービタ/アドレス mux の幅 ∝ NUM_WALKERS** と **(b) キャッシュ CAM 比較
の幅（エントリ数 × タグ幅）** と **(c) バッファ/MSHR の連想比較の幅 ∝ BUFFER_DEPTH**。

## config 別

| cfg | 経路遅延 | Fmax | 主ゲート | 律速要因 |
|---|---|---|---|---|
| 1 nocache | **60.6 ns** | 16.5 MHz | nand4/nor4/and4b ×多数(~112 段) | **37-way 回転プライオリティ + 37:1 アドレス mux** |
| 2 pwc | 15.1 ns | 65.5 MHz | nand4b/nor4b/and4 | 5-way アービタ + 加算器 + PWC 比較 |
| 3 iotlb | 18.5 ns | 53.9 MHz | or4/or3/nor4/nand3 | **BUFFER=5 の MSHR/broadcast 連想 + IOTLB 16-way CAM** |
| 4 prefetch | 11.1 ns | 90.3 MHz | mux2/nand2/nor4 (~16 段) | 短い融合コーン（cache 短絡→加算器→FF）。BUFFER=1 で MSHR 扇が消失 |
| 5 notag | **8.7 ns** | 113.0 MHz | nand4/nor4 | cfg4 から ctx タグ除去 → CAM 比較幅縮小で最速 |

### cfg1 (16.5 MHz) — 最悪
- start/end は walker 状態 FF。経路は 37 個の `iwant[i]` を `(rr+k)%37` で先頭一致させる
  **37 段リップル状プライオリティチェーン**と、選ばれた walker の発行アドレスを選ぶ
  **37:1 の 40bit mux**、その各入力にある `pte_addr` 加算器。約 112 段・60.6ns。
- キャッシュは無い（HAS_PWC/IOTLB=0）。遅さは純粋に**総当たり 37 並列の発行選択**。
- **アプローチ**: ①ツリー型 find-first アービタ（log 深さ）②発行アドレスをレジスタ化して
  「選択」と「加算」を別段に③そもそも 37 walker を要しない設計（=キャッシュを入れる）。
  cfg1 は「キャッシュ無し総当たり」が面積・電力・周波数すべてで最悪という反面教師。

### cfg2 (65.5 MHz)
- 5-way アービタ + 加算器 + PWC(1–2 エントリ) 比較。NUM_WALKERS=5 でアービタ/ mux はまだ
  小さく、PWC も浅いので 15ns。
- **アプローチ**: 発行アドレスのレジスタ化（consume→加算→発行の融合を 2 段化）で大幅改善見込み。

### cfg3 (53.9 MHz) — cfg2/4 より遅い
- walker は 1 個だがパスが伸びる主因は **BUFFER_DEPTH=5**：MSHR の同一ライン連想比較と pc11
  broadcast の 5-way 比較、加えて **IOTLB 16 エントリ × 63bit の CAM 比較**が
  buffer-servicer 経路に乗る（or3/or4/nor4 群）。
- **アプローチ**: ①IOTLB ルックアップを 1 段パイプ化②MSHR/broadcast を縮小（プリフェッチで
  BUFFER=1 にできれば cfg4 のように激減）。実際 cfg4 はこれで 90MHz に。

### cfg4 (90.3 MHz) — best
- B 案で 512b `fill_data_q` とその 512:64 `line_pte` 抽出 mux が**消えた**ため、consume→発行
  コーンが短縮（旧 62MHz → 90MHz）。BUFFER=1 で MSHR 扇が消え、1 walker でアービタも自明。
  残る経路は cache 短絡判定 → `pte_addr` 加算器 → FF（~16 段, 11ns）。
- **アプローチ（Phase 2 本命）**: 発行アドレスを 1 段レジスタ化して「cache 短絡＋加算器」と
  「アービタ＋メモリ発行」を分離 → 加算器単体＋短いロジックなら 400MHz(2.5ns) に近づく。
  代償は 1 サイクル/リードのレイテンシ増（cfg2 で確認済みの +1 walker 余裕で吸収）。

### cfg5 (113.0 MHz) — 最速
- cfg4 と同構成でキャッシュタグから device_id/PASID(36bit) を除去 → **CAM 比較幅が縮小**
  （IOTLB 63→27bit, PWC 54→18 / 45→9bit）し、ヒット判定段が短く 8.7ns。
- **アプローチ**: cfg4 と同じ発行アドレスのレジスタ化。タグが狭い分、最も 400MHz に近い。

## Phase 2 への示唆（実施は承認後）
1. **発行アドレスのレジスタ化**（全 config 共通の本命）：consume→`pte_addr`→アービタ→発行の
   融合コーンを 2 段に分割。1 サイクル/リード増を walker/buffer の +1 余裕で吸収。
2. **アービタのツリー化**（cfg1 必須、他も有効）：O(N) リップル find-first を log 深さに。
3. **キャッシュ lookup のパイプ化 / lookup-mode**：IOTLB 16-way CAM 比較を 1 段に切る。
4. **PIPELINE_DEPTH パラメータ**で段数を可変にし、before/after Fmax を計測。
   期待：cfg4/5 は 90/113 → 200–300MHz 級、cfg1 は構造変更（ツリーアービタ）で数倍。
