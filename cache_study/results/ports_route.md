# 配線込み：共有 IOTLB を NP 主体で使うときの面積・配線スケーリング

`ports_area.md`（セル面積のみ）の続き。**配線（ルーティング）込み**で測り直し：各 (cfg, NP) を
OpenROAD で **floorplan + place_pins + global_place + global_route** まで回し、**die 面積**・
**総ルーティング配線長（global route GRT-0018 / per-net 合計）**・**placer HPWL**・**congestion
overflow** を測定。`syn/ports_route.tcl` / `syn/ports_route_sweep.py` / `results/ports_route.csv` /
`figs/figures/ports_route.png`、生ログ `results/portsrt/*/route.log`。

## 結果（sky130 hd, util 固定）

### util≈45%（緩い）
| cfg | NP | die area [µm²] | routed WL [µm] | placer HPWL | overflow |
|---|---|---|---|---|---|
| mport | 1 | 33,281 | 34,162 | 48,099 | なし |
| mport | 10 | **71,740** | 52,592 | **342,663** | なし |
| muxport | 1 | 33,169 | 35,273 | 48,287 | なし |
| muxport | 10 | **34,957** | 38,405 | **91,742** | なし |

### util≈80%（tight）総ルーティング配線長（GRT-0018）
| cfg | NP=1 | NP=4 | NP=10 |
|---|---|---|---|
| muxport | 115,443 | 128,547 | **150,081** µm（+30%）|
| mport | — | — | **700,481** µm |

両 util・両構成とも **overflow は反復で解消（route failure なし）**。

## 観察（セル面積のみの結論からの更新）

1. **die 面積（util 固定）はセル面積に追従**：mport 線形（33k→72k）、muxport ほぼ平坦（33k→35k）。
   小さい 2×8 IOTLB はルーティング余裕が大きく、**util 45〜90% で congestion failure は起きない**
   （overflow は global route の追加反復で解消）。→ **配線増を die 面積に転嫁するほど混まなかった**。
2. **MUX 版の "配線" は NP で確かに増える**：placer HPWL 48k→92k（**~2×**）、routed WL も +30%（tight）。
   **セル面積は平坦なのに配線は増える**＝タグ配線（27b×NP 本）が 1 点に集中するため。**「MUX でも配線は
   増える」という直感は方向として正しい**。ただしこの規模では die 面積を押し上げるには至らない（二次効果）。
3. **予想と逆**：**配線総量はマルチポート版の方が圧倒的に多い**（NP=10 tight で 700k vs MUX 150k ＝ ~4.7×）。
   各ポートが lookup データパス全体を die 全域へ配線するため。→ **配線込みで不利になるのは MUX ではなく
   マルチポート側**。セル面積で出した「MUX << マルチポート」は**配線込みでも覆らず、むしろ強化**。

## 結論

| 指標 | (A) マルチポート | (B) 1 ポート+MUX |
|---|---|---|
| セル面積（lookup 論理） | NP に線形（×3.75@10） | ほぼ一定（+13%@10） |
| die 面積（util 固定） | 線形（33k→72k） | ほぼ一定（33k→35k） |
| 総ルーティング配線長 | **最大**（NP10 で 700k@80%） | 増えるが小（150k@80%, HPWL ~2×） |
| route 可否（util≤90%） | clean（overflow 解消） | clean（overflow 解消） |

- **MUX 版の配線集中（HPWL ~2×）は実在**するが、小 IOTLB + global route では **die 面積増/congestion
  失敗には至らない**（緩〜中 util で吸収）。本当に面積・congestion を押し上げるのは、**大容量キャッシュ・
  tight floorplan・detailed route + DRC** の領域。
- **配線込みで見ても、面積効率は MUX 版 >> マルチポート版**（マルチポートはセルも配線も最大）。「ルックアップ
  ポートを単一に保つ」効果（MUX or 主体ごと分離）は配線込みでも有効。

## 限界（この測定が捉えていないもの）

- **global route 止まり**（detailed route + DRC なし）。詳細配線の via/DRC 起因の面積増・真の congestion
  破綻は未評価。孤立モジュールのピン配置（`place_pins` 均等）に congestion は敏感。
- 小さな 2×8 IOTLB なのでルーティング余裕が大きい。大容量・多ポートではこの結論は変わり得る
  （MUX の 1 点集中がより効く可能性）。定量化には detailed route + 大容量での再評価が必要。
