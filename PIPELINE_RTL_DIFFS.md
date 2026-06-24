# パイプライン化のレジスタ挿入：挿入前/後 RTL と「どこで 1 サイクルを切ったか」

v4 / v5 / v9 / v10 について、(1) 挿入前後の RTL（要点抜粋）と、(2) 元は 1 サイクルに融合していた
論理のどこを切ったかを文章＋図で示す。元の融合コーン（v0）は：

```
 [reg] rdata / bvpn_q
   │
   ├─(A) cache lookup (IOTLB/PWC CAM 比較)
   ├─(B) 次状態 / start 判定 (most-complete-hit, svc 判定)
   ├─(C) idx_of + pte_addr  … アドレス生成
   ├─(D) prefetch: pf_line(+LEAD 加算) + iaddr_of
   │
 [reg] walker 状態 / araddr(発行)
```
これが 1 本の reg→reg 経路（160.8 MHz）。v4,v5,v9,v10 はこの A〜D のどこかにレジスタを挿して段を割る。
全体図：`cache_study/figs/figures/pipeline_cuts.png`。

---

## v4 — 発行アドレス(C)を walker 状態書込み時に事前計算　189.0→207.0 MHz (+9.5%)

**切る場所**：(C) アドレス生成 `idx_of+pte_addr` を、発行サイクルから**状態書込みサイクル**へ移し、
結果を `wiaddr_q` レジスタに置く。発行は「レジスタ読み＋アービタ mux」だけになる。

**挿入前**（発行サイクルで毎回アドレス生成）：
```systemverilog
// 発行アービタ内：WRUN walker から発行するとき、その場で idx/pte_addr を計算
end else if (ws_q[w]==WRUN) begin
  pc=wpc_q[w]; base=wbase_q[w]; vp=wvpn_q[w]; gp=wgpn_q[w]; dgv=wdgvpn_q[w]; sel=1'b1;
end
...
if (sel) begin
  ix = idx_of(pc, vp, gp, dgv);                 // ← (C) 発行経路に乗る
  iaddr[w] = ... pte_addr(base, ix) ...;        // ← (C)
end
```

**挿入後**（`wiaddr_q` を追加し、状態書込み時に事前計算）：
```systemverilog
logic [PA_W-1:0] wiaddr_q [NCTX];               // ★挿入したレジスタ
logic            wiburst_q[NCTX];

// 発行：登録済みアドレスを読むだけ（idx_of/pte_addr は経路から消える）
end else if (ws_q[w]==WRUN) begin
  if (PIPELINE_DEPTH>=2) begin
    iwant[w]=1'b1; iaddr[w]=wiaddr_q[w]; iburst[w]=wiburst_q[w];   // precomputed
  end ...
end

// launch / consume / prefetch の「状態を書く」サイクルで先に計算しておく
if (svc_launch) begin
  ...
  wiaddr_q[wfree_i] <= iaddr_of(start_pc, start_base, bvpn_q[bsel], '0,'0);  // ★ここで(C)
end
if (do_consume && next_pc!=PC_DONE)
  wiaddr_q[cons_w] <= iaddr_of(next_pc, next_base, cons_vpn, next_gpn, next_dg); // ★ここで(C)
```

**要点**：(C) のコーンを「発行の手前」から「状態書込みと同じサイクル（次状態と並列）」へ移動。
発行→AR が register+mux だけになり +9.5%。

---

## v5 — キャッシュ lookup(A)と判定(B)を probe/commit に分割　207.0→241.5 MHz (+16.7%)

**切る場所**：(A) CAM ルックアップと (B) start 判定を、`bvpn_q → CAM → start → 状態書込み` の 1 本から、
**probe（A を実行して staging レジスタに格納）／commit（staging から B を実行して launch）**の 2 段に割る。

**挿入前**（1 サイクルで CAM→判定→launch）：
```systemverilog
if (bsel_v) begin
  if (svc_iotlb) begin                          // (A)iotlb_hit を直接使い
    bs_q[bsel]<=BRES; bspa_q[bsel]<={ppn28s(iotlb_d),...};
  end else if (svc_ride) begin
  end else if (svc_launch) begin                // (B)start_pc/start_base を直接使い
    ws_q[wfree_i]<=WRUN; wpc_q[wfree_i]<=start_pc; wbase_q[wfree_i]<=start_base;
    wvpn_q[wfree_i]<=bvpn_q[bsel]; ...
  end
end
```

**挿入後**（staging レジスタ `stg_*_q` を挿入し 2 段化）：
```systemverilog
// ★挿入したレジスタ群（probe の結果を保持）
logic stg_v_q; logic [VPN_W-1:0] stg_vpn_q; logic stg_iotlb_hit_q;
logic [CDW-1:0] stg_iotlb_d_q; logic [3:0] stg_start_pc_q; logic [PPN_W-1:0] stg_start_base_q; ...

if (SVC_PIPE) begin
  if (bsel_v & ~stg_v_q) begin            // === PROBE（cycle A）: (A)ルックアップ結果をラッチ
    stg_v_q<=1'b1; stg_vpn_q<=bvpn_q[bsel]; stg_line_q<=bsel_line;
    stg_iotlb_hit_q<=(HAS_IOTLB!=0)&iotlb_hit; stg_iotlb_d_q<=iotlb_d;
    stg_start_pc_q<=start_pc; stg_start_base_q<=start_base;   // (B)の入力も確定値で格納
  end
  if (stg_v_q) begin                      // === COMMIT（cycle B）: staging から launch
    stg_v_q<=1'b0;
    if (e_svc_iotlb)      bs_q[e_bsel]<=BRES, bspa_q[e_bsel]<=...;
    else if (e_svc_launch) ws_q[wfree_i]<=WRUN, wpc_q[wfree_i]<=e_start_pc, ...;  // e_* = stg_*_q
  end
end
// e_* は staging（PD>=2）か combinational（PD<2）かを選ぶ別名
assign e_iotlb_hit = SVC_PIPE ? stg_iotlb_hit_q : iotlb_hit;  // ほか e_start_pc 等も同様
```

**要点**：`bvpn_q → 16-way CAM 比較 →（深い）→ launch` の (A)(B) コーンを **probe サイクルに分離**。
launch は staging レジスタからの読み出しになり、CAM 比較が発行/launch から消えて +16.7%。

---

## v9 — commit(B)/consume(A’)/prefetch(D) の 3 経路を**同時に**事前計算　234.7→261.8 MHz (+11.5%)

**切る場所**：v5 後に ~230MHz で並んでいた 3 つの co-critical を全部 probe で事前計算してレジスタ化。
(i) demand launch アドレス、(ii) メモリ R チャネル、(iii) prefetch の `pf_line`(+LEAD 加算) と launch アドレス。

**挿入前**（prefetch は launch サイクルで +LEAD 加算と iaddr_of を実行）：
```systemverilog
assign pf_launch = (PREFETCH_EN!=0) & pf_trig & wfree_v & ~e_svc_launch;
// launch 時に：
wvpn_q[wfree_i] <= {pf_line,{LINE_LSB{1'b0}}};                      // pf_line = demand_line+LEAD
wiaddr_q[wfree_i]<= iaddr_of(4'd8, region_vml0_q, {pf_line,...});   // (D) launch 経路で加算+生成
```

**挿入後**（prefetch 用 staging `stg_pf_*` を追加、probe で先に計算）：
```systemverilog
// ★挿入したレジスタ群
logic [VPNLINE_W-1:0] stg_pf_line_q;   // demand_line + LEAD（加算を probe で）
logic [PA_W-1:0]      stg_pf_addr_q;   // iaddr_of(pc8, region base, pf_line)（生成も probe で）
logic                 stg_pf_same_q, stg_pf_region_ok_q; logic [PPN_W-1:0] stg_pf_regbase_q;
logic [PA_W-1:0]      stg_start_addr_q;            // (i) demand launch アドレスも事前計算
logic                 rvalid_q,rlast_q; logic [PTE_W-1:0] rdata_q; ...   // (ii) R チャネル登録

// probe サイクルで prefetch の加算・アドレス生成を済ませる（launch 経路から (D) を除去）
// commit/launch は登録値を読むだけ + dedup 比較：
assign pf_launch_c = stg_v_q & (e_svc_iotlb|e_svc_launch) & stg_pf_region_ok_q
                   & stg_pf_same_q & wfree_v & ~e_svc_launch & (stg_pf_line_q != pf_last_q);
// + (b) 単一 walker では e_busy 比較を launch enable から除外（冗長なので経路短縮）
assign e_svc_launch = e_v & ~e_svc_iotlb & wfree_v & ((NCTX>1) ? ~e_svc_ride : 1'b1);
```

**要点**：v7（consume 単独）/ v8（2 経路）は **co-critical の残り 1 本が露出**して効かなかった。
**3 経路を同時にレジスタ化**して初めて全部が下がり +11.5%。

---

## v10 — consume の次状態(A’)とアドレス生成(C)を別段に分離　261.8→287.4 MHz (+9.8%)

**切る場所**：consume は「次状態 `wpc/wbase/...` 更新」と「`iaddr_of` アドレス生成」を 1 サイクルで
やっていた（`rdata_q → 次状態 → iaddr_of → wiaddr_q`）。これを **consume＝次状態だけ／専用 addr-gen 段＝
登録済み状態から iaddr_of** の 2 段に割る。`wia_rdy_q` で「アドレス準備済み」を示す。

**挿入前**（consume サイクルで次状態と一緒にアドレス生成）：
```systemverilog
if (do_consume) begin
  wpc_q[cons_w]<=next_pc; wbase_q[cons_w]<=next_base; wgpn_q[cons_w]<=next_gpn; ...
  if (PIPELINE_DEPTH>=2 && next_pc!=PC_DONE) begin
    wiaddr_q[cons_w] <= iaddr_of(next_pc, next_base, cons_vpn, next_gpn, next_dg);  // ← (C)を consume と同サイクル
    wiburst_q[cons_w]<= (CO>1)&&(next_pc==4'd11);
  end
end
```

**挿入後**（consume はアドレスを「未準備」にし、専用段で次サイクルに生成）：
```systemverilog
logic wia_rdy_q[NCTX];                       // ★挿入：アドレス準備済みフラグ

if (do_consume) begin
  wpc_q[cons_w]<=next_pc; wbase_q[cons_w]<=next_base; ...        // 次状態だけ更新
  if (PIPELINE_DEPTH>=2 && next_pc!=PC_DONE) wia_rdy_q[cons_w]<=1'b0;  // ★アドレスは未準備に
end

// ★専用 addr-gen 段：登録済み walker 状態から iaddr_of を別サイクルで計算
if (PIPELINE_DEPTH>=2)
  for (int w=0;w<NCTX;w++)
    if (ws_q[w]==WRUN && !wia_rdy_q[w]) begin
      wiaddr_q[w] <= iaddr_of(wpc_q[w], wbase_q[w], wvpn_q[w], wgpn_q[w], wdgvpn_q[w]); // (C)を別段で
      wiburst_q[w]<= (CO>1)&&(wpc_q[w]==4'd11);
      wia_rdy_q[w]<= 1'b1;
    end

// 発行は wia_rdy_q を待つ
if (wia_rdy_q[w]) begin iwant[w]=1'b1; iaddr[w]=wiaddr_q[w]; ... end
```

**要点**：consume の `次状態(A’)+iaddr_of(C)` 融合を分割。次状態は consume サイクル、(C) は次サイクルの
専用段。launch/prefetch は probe で事前計算済み（wia_rdy=1）なので初回発行は遅延なし、walk 中間ステップ
だけ +1cyc。+9.8%。

---

## どこで切ったか（まとめ図的整理）

元の 1 サイクル融合コーン `[reg]→A→B→C→D→[reg]` に対して：

| 版 | 切った境界（挿入レジスタ） | A:lookup | B:判定 | C:addr-gen | D:prefetch |
|---|---|---|---|---|---|
| v4 | C を状態書込み側へ（`wiaddr_q`） | | | **★分離** | |
| v5 | A/B を probe へ（`stg_*_q`） | **★分離** | **★分離** | |
| v9 | D と R を probe へ（`stg_pf_*`,`rdata_q`）+ 残 co-critical 同時 | | | (i 再) | **★分離** |
| v10 | C(consume 側)を専用段へ（`wia_rdy_q`） | | | **★分離(consume)** | |

→ v4=C、v5=A+B、v9=D(+R+残)、v10=C(consume 版) を順に別サイクルへ移し、各段のコーンを短縮。
合計 hd 160.8→287.4 MHz（+78.7%）。レイテンシは段ごとに +1cyc だが wire rate は不変
（メモリ待ち＋prefetch で隠蔽、cyc/trans 11.08→11.47）。
