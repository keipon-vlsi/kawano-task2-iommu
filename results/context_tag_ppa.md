# Context タグ（device_id + PASID）除去の PPA 影響 — cfg2 / cfg3 / cfg4

各 config のキャッシュタグから **context = device_id(24b) + PASID(20b) = 44 bit** を除去
（`TAG_CONTEXT_EN=1 → 0`）した場合の PPA。単一コンテキスト運用なら context タグは機能的に不要。

- 合成: sky130_fd_sc_hd, tt 1v80, 現行 RTL（line IOTLB + counter 合成除外込み）, `sv2v -D SYNTHESIS`。
- post-opt: `syn/fmax_opt/opt.tcl`（canonical knobs: SDC + repair_design + repair_timing,
  MAXTRANS 0.5 / MAXFO 12, UTIL 35, ideal clock, no CTS/route）。全 cfg PIPELINE_DEPTH=1。
- 電力は比較公平性のため switching activity を全 cfg 一律 0.12 で算出（with/without の差分が目的。
  絶対値は較正済み値とは別）。再現: `python3 syn/notag_ppa.py`。

| cfg | 構成 | tag | Fmax [MHz] | area(post-opt) [µm²] | area(synth) [µm²] | power@400 [mW] |
|---|---|---|---|---|---|---|
| cfg2 | PWC | with ctx | 102.9 | 124,315 | 107,111 | 51.5 |
| cfg2 | PWC | **no ctx** | **105.3** | **111,529** | 93,552 | **45.8** |
| | | Δ | **+2.3%** | **−10.3%** | −12.7% | **−11.2%** |
| cfg3 | PWC+IOTLB+CO8 | with ctx | 119.2 | 128,755 | 112,881 | 56.0 |
| cfg3 | PWC+IOTLB+CO8 | **no ctx** | **130.7** | **106,705** | 93,621 | **47.0** |
| | | Δ | **+9.7%** | **−17.1%** | −17.1% | **−16.1%** |
| cfg4 | +prefetch | with ctx | 201.2 | 107,243 | 93,078 | 46.5 |
| cfg4 | +prefetch | **no ctx** | **203.7** | **85,802** | 73,993 | **37.8** |
| | | Δ | **+1.2%** | **−20.0%** | −20.5% | **−18.6%** |

## 観察

- **面積削減が cfg2(−10%) → cfg3(−17%) → cfg4(−20%) と増大**。理由:
  - cfg2 は IOTLB 無し。context タグが乗るのは小さな PWC タグ（vml1/vml2/gl1/gl2、各 1–2 エントリ）
    のみ → 削減は中程度。
  - cfg3/cfg4 は **IOTLB（CO+CO=16 エントリ）**を持ち、各タグが TCW(44)+VPN(27)=71b → 27b に縮む。
    16 エントリ × 44b の削減が効く。
  - cfg4 は NCTX=1/BUF=1 で walker/buffer/制御の比率が小さく、**キャッシュが総面積に占める割合が最大**
    → タグ除去の相対効果が最大（−20%）。
- **電力も同方向（−11〜−19%）**：タグ FF と CAM 比較器のトグルが減るため。
- **Fmax は +1〜+10%**。cfg3 が +9.7% と最大（IOTLB CAM の tag 比較が 71b→27b に浅くなり、cfg3 の
  律速だった IOTLB ルックアップが短縮）。cfg2/cfg4 は律速が tag 比較以外（PWC 経路・consume 経路）の
  ため +1〜2% に留まる。
- **整合性チェック**: cfg4(no ctx, PD=1) は cfg5 を PD=1 にした構成（= `cfg5_nopipe`）と等価。本表
  203.7 MHz（canonical knobs）vs `cfg5_nopipe` hd 214.6 MHz（v14-tuned knobs）で、ノブ差ぶんの差として整合。

## 結論

単一コンテキスト（Task2 の 800GbE 単一ストリーム）では context タグは不要で、除去により
**面積 −10〜−20%・電力 −11〜−19%**（IOTLB を持つ cfg3/cfg4 ほど大）が、性能劣化なし（むしろ Fmax 微増）
で得られる。これが cfg5（= cfg4 から context 除去）を採用した定量的根拠。
