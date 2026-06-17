// iommu_top.sv -- ONE parameterized nested 2-stage IOMMU translation core.
//
// A config (cfg1..cfg5) is just a parameter set; cfgN/cfgN_top.sv instantiates this.
// All critical-path logic is real RTL: tag compare / priority encoder / mux tree live
// in fa_cache; the address-composition adder, MSHR associative compare, walker context
// register file, the memory-issue arbiter and the memory tag demux live here.
//
// Pipelining: walks advance event-driven. The unified memory arbiter fuses
// consume->next-issue and launch->first-issue in the same cycle (round-robin, fair),
// so a memory read costs ~MEM_LATENCY cycles with no fixed per-step bubble. The
// VS-stage most-complete-hit shortcut folds into launch (probes VM PWCs); the G-stage
// shortcut folds into the VM-L0-leaf consume (probes G PWCs). Caches stay single-
// ported, one driver each.
//
// Nested walk (12 ordered memory steps; pc):
//   0  VM-L2 PTE -> VM-L1 GPA        4  VM-L1 PTE -> VM-L0 GPA        8  VM-L0 leaf -> data GPA
//   1..3 table-G(VM-L1 GPA)->SPA     5..7 table-G(VM-L0 GPA)->SPA     9  G-L2 ->G-L1 SPA
//   (fill VM-L2 PWC @pc3)            (fill VM-L1 PWC @pc7)            10 G-L1 ->G-L0 SPA
//   launch short: VM-L1 hit->pc8, VM-L2 hit->pc4                     11 G-L0 leaf -> data SPA
//   pc8 short: G-L1 hit->pc11, G-L2 hit->pc10  (fill G-L2 @pc9, G-L1 @pc10, IOTLB @pc11)
import iommu_pkg::*;

module iommu_top #(
  parameter int HAS_PWC            = 1,
  parameter int HAS_IOTLB          = 1,
  parameter int NUM_WALKERS        = 1,
  parameter int BUFFER_DEPTH       = 5,
  parameter int COALESCE_FACTOR    = 8,   // 1 or 8
  parameter int PREFETCH_EN        = 0,
  parameter int PREFETCH_LEAD      = 1,   // lead distance (1 = next line)
  parameter int TAG_CONTEXT_EN     = 1,
  parameter int MEM_LATENCY_CYCLES = 40,
  parameter int MEM_MAX_OUTSTANDING= 8,
  parameter int PIPELINE_DEPTH     = 1
)(
  input  logic                clk,
  input  logic                rst_n,
  input  logic                pl_valid,
  input  logic [1:0]          pl_sel,
  input  logic [PPN_W-1:0]    pl_data,
  input  logic                req_valid,
  output logic                req_ready,
  input  logic [VPN_W-1:0]    req_vpn,
  input  logic [DEVICE_W-1:0] req_device_id,
  input  logic [PASID_W-1:0]  req_pasid,
  input  logic                req_is_write,
  output logic                rsp_valid,
  input  logic                rsp_ready,
  output logic [VPN_W-1:0]    rsp_vpn,
  output logic [SPA_W-1:0]    rsp_spa,
  // AXI-like read: 8 B (1 PTE) data bus; a 64 B leaf line arrives as an 8-beat burst,
  // a walk-step PTE as a single beat. arlen = beats-1 (0 or 7).
  output logic                arvalid,
  input  logic                arready,
  output logic [PA_W-1:0]     araddr,
  output logic [TAG_W_TOP-1:0] arid,
  output logic [2:0]          arlen,
  input  logic                rvalid,
  output logic                rready,
  input  logic [PTE_W-1:0]    rdata,
  input  logic [TAG_W_TOP-1:0] rid,
  input  logic                rlast,
  output logic [31:0]         walks_o,
  output logic [31:0]         resp_o,
  output logic [31:0]         outstanding_o
);
  // ONE shared walker pool: prefetch reuses an idle demand walker (demand has
  // priority). In steady state demands are IOTLB hits (never touch the walker), so
  // the single walker is free to run the one-line-ahead prefetch walk.
  localparam int NCTX     = NUM_WALKERS;
  localparam int TAGW     = (NCTX < 2) ? 1 : $clog2(NCTX);
  localparam int BW       = (BUFFER_DEPTH < 2) ? 1 : $clog2(BUFFER_DEPTH);
  localparam int CO       = COALESCE_FACTOR;
  localparam int LINE_LSB = (CO > 1) ? $clog2(CO) : 0;
  localparam int LBW      = (LINE_LSB > 0) ? LINE_LSB : 1;   // safe slice width (CO==1)
  localparam int VPNLINE_W= VPN_W - LINE_LSB;
  localparam int TCW      = (TAG_CONTEXT_EN != 0) ? CTX_W : 0;
  localparam int VML2_TW  = TCW + IDX_W;
  localparam int VML1_TW  = TCW + 2*IDX_W;
  localparam int GL2_TW   = TCW + IDX_W;
  localparam int GL1_TW   = TCW + 2*IDX_W;
  localparam int IOTLB_TW = TCW + VPN_W;
  localparam int IOTLB_N  = (HAS_IOTLB != 0) ? (CO + CO) : 1;
  localparam int CDW      = 48;   // cache data width = PTE[47:0] (flags + used PPN)
  localparam int LINE_IN_L0 = IDX_W - LINE_LSB;     // line-index bits within a VM-L0 table
  localparam int REGID_W  = VPN_W - IDX_W;          // VM-L0-table region id = VPN[26:9]
  localparam logic [3:0] PC_DONE = 4'd12;

  typedef enum logic [1:0] {WFREE=2'd0, WRUN=2'd1, WWAIT=2'd2} wst_e;
  typedef enum logic [1:0] {BFREE=2'd0, BNEED=2'd1, BRES=2'd2} bst_e;

  // ---------------- roots ----------------
  logic [PPN_W-1:0] vs_root_spa_q, g_root_spa_q;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin vs_root_spa_q<='0; g_root_spa_q<='0; end
    else if (pl_valid) begin
      if (pl_sel==2'd0) vs_root_spa_q<=pl_data;
      if (pl_sel==2'd1) g_root_spa_q <=pl_data;
    end
  end

  // ---------------- walker context register file ----------------
  wst_e             ws_q   [NCTX];
  logic [3:0]       wpc_q  [NCTX];
  logic [VPN_W-1:0] wvpn_q [NCTX];
  logic [CTX_W-1:0] wctx_q [NCTX];
  logic [PPN_W-1:0] wbase_q[NCTX];      // base SPA for the read at wpc
  logic [GPN_W-1:0] wgpn_q [NCTX];      // GPN under table-G sub-walk
  logic [GVPN_W-1:0]wdgvpn_q[NCTX];     // data GVPN
  logic [VPNLINE_W-1:0] wline_q[NCTX];  // merge key

  // ---------------- transaction buffer ----------------
  bst_e             bs_q   [BUFFER_DEPTH];
  logic [VPN_W-1:0] bvpn_q [BUFFER_DEPTH];
  logic [CTX_W-1:0] bctx_q [BUFFER_DEPTH];
  logic [SPA_W-1:0] bspa_q [BUFFER_DEPTH];

  // per-walker beat counter for the pc11 leaf burst (8B beats stream into the IOTLB)
  logic [3:0]       wbeat_q[NCTX];

  // PIPELINE_DEPTH>=2: precomputed next read address + burst flag per walker. Written
  // when the walker state is set (launch/consume); the issue path reads these registers
  // instead of recomputing idx_of+pte_addr, splitting the address-gen cone off the
  // issue->AR critical path (the bottleneck exposed after v3).
  logic [PA_W-1:0]  wiaddr_q [NCTX];
  logic             wiburst_q[NCTX];
  // v10: address-ready bit. wiaddr_q is computed in a dedicated addr-gen stage from the
  // REGISTERED walker state (one cycle after a consume updates wpc/wbase/wdgvpn), so the
  // consume cone is just next-state (no iaddr_of). Launch precomputes the addr at probe,
  // so it is ready immediately. The issue waits for wia_rdy_q. PD>=2 only.
  logic             wia_rdy_q[NCTX];

  // PIPELINE_DEPTH>=2: servicer probe/commit pipeline. Cycle A probes the caches
  // (IOTLB/PWC CAM compare on the demand VPN) and latches the result here; cycle B
  // commits (resolve-on-hit or launch+precompute) from these registers. This takes the
  // CAM-compare cone (and the die-crossing wire to the walker regs) OFF the launch arc
  // bvpn_q -> CAM -> start -> {wbase_q,wiaddr_q} that limited Fmax after v4.
  logic                  stg_v_q;       // a probe result is staged, awaiting commit
  logic [BW-1:0]         stg_bsel_q;
  logic [VPN_W-1:0]      stg_vpn_q;
  logic [CTX_W-1:0]      stg_ctx_q;
  logic [VPNLINE_W-1:0]  stg_line_q;
  logic                  stg_iotlb_hit_q;
  logic [CDW-1:0]        stg_iotlb_d_q;
  logic [3:0]            stg_start_pc_q;
  logic [PPN_W-1:0]      stg_start_base_q;
  // v8(a): demand launch read address precomputed at probe (iaddr_of off the commit path)
  logic [PA_W-1:0]       stg_start_addr_q;
  logic                  stg_start_burst_q;
  // v9(c): prefetch target line + launch address precomputed at probe (the +LEAD adder
  // and iaddr_of leave the commit/launch path; commit does reg->reg writes + a dedup compare)
  logic [VPNLINE_W-1:0]  stg_pf_line_q;       // demand_line + LEAD
  logic [PA_W-1:0]       stg_pf_addr_q;       // iaddr_of(pc8, region base, pf_line)
  logic                  stg_pf_same_q;       // same VM-L0 table guard
  logic                  stg_pf_region_ok_q;  // region captured & matches
  logic [PPN_W-1:0]      stg_pf_regbase_q;    // region VM-L0 base snapshot
  logic [VPNLINE_W-1:0]  pf_last_q;           // prefetch dedup (PD>=2)

  // v8: register the memory R channel (AXI R register slice) so the consume cone
  // rdata -> ppn -> next_base -> {wbase_q,wiaddr_q} is reg2reg, not an input-delay path.
  logic                  rvalid_q, rlast_q;
  logic [PTE_W-1:0]      rdata_q;
  logic [TAG_W_TOP-1:0]  rid_q;

  // prefetch (next-line) region capture
  logic [PPN_W-1:0]  region_vml0_q;
  logic              region_valid_q;
  logic [REGID_W-1:0]region_id_q;

  logic [TAGW-1:0]  arb_rr_q;
  logic [BW-1:0]    brr_q;
  // walks_o/resp_o are SIM-ONLY performance counters (walks launched / responses sent),
  // read by the testbench to check coalescing/throughput. They are NOT used by the
  // translation datapath. Their 32b ripple-carry was the last Fmax limiter, so they are
  // EXCLUDED FROM SYNTHESIS (`SYNTHESIS defined by the synth flow) and tied to 0 there.
`ifndef SYNTHESIS
  logic [31:0]      walks_q, resp_q;
  logic             walk_inc_q, pf_inc_q, resp_inc_q;
  assign walks_o=walks_q; assign resp_o=resp_q;
`else
  assign walks_o='0; assign resp_o='0;
`endif

  // ---------------- helpers ----------------
  function automatic logic [PPN_W-1:0] ppn28(input logic [PTE_W-1:0] p);
    logic [43:0] f; f = pte_ppn44(p); return f[PPN_W-1:0];
  endfunction
  function automatic logic [GPN_W-1:0] gpn27(input logic [PTE_W-1:0] p);
    logic [43:0] f; f = pte_ppn44(p); return f[GPN_W-1:0];
  endfunction
  // Cache data = lower 48b of the PTE (flags + the PPN we use); the top 16b
  // (reserved/PBMT/N + unused high PPN) are redundant for this happy-path design.
  function automatic logic [PPN_W-1:0] ppn28s(input logic [CDW-1:0] d);
    return d[10 +: PPN_W];        // Sv39 PTE PPN starts at bit 10 -> ppn28 = d[37:10]
  endfunction
  function automatic logic [IDX_W-1:0] idx_of(input logic [3:0] pc, input logic [VPN_W-1:0] vpn,
                                              input logic [GPN_W-1:0] gpn, input logic [GVPN_W-1:0] dg);
    unique case (pc)
      4'd0: idx_of=vidx(vpn,2);  4'd4: idx_of=vidx(vpn,1);  4'd8: idx_of=vidx(vpn,0);
      4'd1,4'd5: idx_of=gidx(gpn,2);  4'd2,4'd6: idx_of=gidx(gpn,1);  4'd3,4'd7: idx_of=gidx(gpn,0);
      4'd9: idx_of=gidx(dg,2);  4'd10: idx_of=gidx(dg,1);  4'd11: idx_of=gidx(dg,0);
      default: idx_of='0;
    endcase
  endfunction
  // full issue-address compose (idx_of + pte_addr + burst alignment). Used to PRECOMPUTE
  // the next read address at walker-state write time, so the issue->AR path is a register
  // read + arbiter mux only (the idx_of+pte_addr cone leaves the issue critical path).
  function automatic logic [PA_W-1:0] iaddr_of(input logic [3:0] pc, input logic [PPN_W-1:0] base,
        input logic [VPN_W-1:0] vpn, input logic [GPN_W-1:0] gpn, input logic [GVPN_W-1:0] dg);
    logic [IDX_W-1:0] ix; ix = idx_of(pc, vpn, gpn, dg);
    iaddr_of = ((CO>1) && (pc==4'd11)) ? pte_addr(base, {ix[IDX_W-1:3],3'd0})
                                       : pte_addr(base, ix);
  endfunction

  // ===================================================== buffer selection
  logic            bsel_v;  logic [BW-1:0] bsel;
  always_comb begin
    bsel_v=1'b0; bsel='0;
    for (int k=0;k<BUFFER_DEPTH;k++) begin
      int i; i=(int'(brr_q)+k)%BUFFER_DEPTH;
      if (!bsel_v && bs_q[i]==BNEED) begin bsel_v=1'b1; bsel=BW'(i); end
    end
  end
  logic            bfree_v;  logic [BW-1:0] bfree_i;
  always_comb begin
    bfree_v=1'b0; bfree_i='0;
    for (int i=BUFFER_DEPTH-1;i>=0;i--) if (bs_q[i]==BFREE) begin bfree_v=1'b1; bfree_i=BW'(i); end
  end
  logic            rsel_v;  logic [BW-1:0] rsel;
  always_comb begin
    rsel_v=1'b0; rsel='0;
    for (int i=BUFFER_DEPTH-1;i>=0;i--) if (bs_q[i]==BRES) begin rsel_v=1'b1; rsel=BW'(i); end
  end
  logic            wfree_v;  logic [TAGW-1:0] wfree_i;
  always_comb begin
    wfree_v=1'b0; wfree_i='0;
    for (int i=NCTX-1;i>=0;i--) if (ws_q[i]==WFREE) begin wfree_v=1'b1; wfree_i=TAGW'(i); end
  end

  // ===================================================== cache lookups
  // effective R channel: registered (PD>=2) or pass-through (PD<2). rready is always 1.
  logic                 r_valid, r_last;  logic [PTE_W-1:0] r_data;  logic [TAG_W_TOP-1:0] r_id;
  always_comb begin
    if (PIPELINE_DEPTH>=2) begin r_valid=rvalid_q; r_last=rlast_q; r_data=rdata_q; r_id=rid_q; end
    else                   begin r_valid=rvalid;   r_last=rlast;   r_data=rdata;   r_id=rid;   end
  end
  // consuming walker (from memory return tag)
  logic [TAGW-1:0]   cons_w;
  logic [VPN_W-1:0]  cons_vpn;  logic [CTX_W-1:0] cons_ctx;  logic [GVPN_W-1:0] cons_dgvpn;
  logic [3:0]        cons_pc;
  assign cons_w     = r_id[TAGW-1:0];
  assign cons_vpn   = wvpn_q[cons_w];
  assign cons_ctx   = wctx_q[cons_w];
  assign cons_dgvpn = wdgvpn_q[cons_w];
  assign cons_pc    = wpc_q[cons_w];
  // a pc11 leaf read (CO>1) returns 8 beats -> burst path; everything else is 1 beat
  logic        burst_beat, do_consume;
  logic [PTE_W-1:0] cons_pte;
  assign burst_beat = (CO>1) & r_valid & rready & (ws_q[cons_w]==WWAIT) & (cons_pc==4'd11);
  assign do_consume = r_valid & rready & (ws_q[cons_w]==WWAIT) & ~burst_beat;
  assign cons_pte   = r_data;            // 8 B data bus: rdata IS the PTE
  logic [GVPN_W-1:0] cons_newdg;        // data GVPN just produced at pc8 consume
  assign cons_newdg = gpn27(cons_pte);

  // VM PWC lookup port <- launching buffer entry; G PWC lookup port <- pc8 consume
  logic [VML2_TW-1:0] vml2_lk;  logic [VML1_TW-1:0] vml1_lk;
  logic [GL2_TW-1:0]  gl2_lk;   logic [GL1_TW-1:0]  gl1_lk;
  logic [IOTLB_TW-1:0] iotlb_lk;
  // IOTLB fill key VPN for the current burst beat = {line, beat-within-line}
  logic [VPN_W-1:0]   fill_vpn;
  generate
    if (CO>1) begin : g_fillvpn_co
      assign fill_vpn = {wline_q[cons_w], wbeat_q[cons_w][LINE_LSB-1:0]};
    end else begin : g_fillvpn_1
      assign fill_vpn = wline_q[cons_w];
    end
  endgenerate

  // fill ports (combinational, same cycle as the triggering event)
  logic vml2_fe, vml1_fe, gl2_fe, gl1_fe, iotlb_fe;
  logic [CDW-1:0] vml2_fd, vml1_fd, gl2_fd, gl1_fd, iotlb_fd;
  logic [VML2_TW-1:0] vml2_fk;  logic [VML1_TW-1:0] vml1_fk;
  logic [GL2_TW-1:0]  gl2_fk;   logic [GL1_TW-1:0]  gl1_fk;
  logic [IOTLB_TW-1:0] iotlb_fk;
  assign vml2_fe = (HAS_PWC!=0) & do_consume & (cons_pc==4'd3);
  assign vml1_fe = (HAS_PWC!=0) & do_consume & (cons_pc==4'd7);
  assign gl2_fe  = (HAS_PWC!=0) & do_consume & (cons_pc==4'd9);
  assign gl1_fe  = (HAS_PWC!=0) & do_consume & (cons_pc==4'd10);
  // store the lower 48b of the resolving PTE (PWC: the table-resolving PTE; IOTLB: the
  // data-leaf PTE). ppn is extracted on use via ppn28s().
  assign vml2_fd = cons_pte[CDW-1:0]; assign vml1_fd = cons_pte[CDW-1:0];
  assign gl2_fd  = cons_pte[CDW-1:0]; assign gl1_fd  = cons_pte[CDW-1:0];
  // IOTLB filled one entry per burst beat (beat j -> page j of the line)
  assign iotlb_fe = (HAS_IOTLB!=0) & burst_beat;
  assign iotlb_fd = r_data[CDW-1:0];

  generate
    if (TAG_CONTEXT_EN != 0) begin : g_tag_ctx
      assign vml2_lk  = {bctx_q[bsel], bvpn_q[bsel][26:18]};
      assign vml1_lk  = {bctx_q[bsel], bvpn_q[bsel][26:9]};
      assign gl2_lk   = {cons_ctx, cons_newdg[26:18]};
      assign gl1_lk   = {cons_ctx, cons_newdg[26:9]};
      assign iotlb_lk = {bctx_q[bsel], bvpn_q[bsel]};
      assign vml2_fk  = {cons_ctx, cons_vpn[26:18]};
      assign vml1_fk  = {cons_ctx, cons_vpn[26:9]};
      assign gl2_fk   = {cons_ctx, cons_dgvpn[26:18]};
      assign gl1_fk   = {cons_ctx, cons_dgvpn[26:9]};
      assign iotlb_fk = {cons_ctx, fill_vpn};
    end else begin : g_tag_noctx
      assign vml2_lk  = bvpn_q[bsel][26:18];
      assign vml1_lk  = bvpn_q[bsel][26:9];
      assign gl2_lk   = cons_newdg[26:18];
      assign gl1_lk   = cons_newdg[26:9];
      assign iotlb_lk = bvpn_q[bsel];
      assign vml2_fk  = cons_vpn[26:18];
      assign vml1_fk  = cons_vpn[26:9];
      assign gl2_fk   = cons_dgvpn[26:18];
      assign gl1_fk   = cons_dgvpn[26:9];
      assign iotlb_fk = fill_vpn;
    end
  endgenerate

  logic vml2_hit, vml1_hit, gl2_hit, gl1_hit, iotlb_hit;
  logic [CDW-1:0] vml2_d, vml1_d, gl2_d, gl1_d, iotlb_d;
  generate
    if (HAS_PWC != 0) begin : g_pwc
      fa_cache #(.ENTRIES(1), .TAG_W(VML2_TW), .DATA_W(CDW)) u_pwc_vml2 (
        .clk,.rst_n,.lk_tag(vml2_lk),.lk_hit(vml2_hit),.lk_data(vml2_d),
        .fill_en(vml2_fe),.fill_tag(vml2_fk),.fill_data(vml2_fd));
      fa_cache #(.ENTRIES(2), .TAG_W(VML1_TW), .DATA_W(CDW)) u_pwc_vml1 (
        .clk,.rst_n,.lk_tag(vml1_lk),.lk_hit(vml1_hit),.lk_data(vml1_d),
        .fill_en(vml1_fe),.fill_tag(vml1_fk),.fill_data(vml1_fd));
      fa_cache #(.ENTRIES(1), .TAG_W(GL2_TW), .DATA_W(CDW)) u_pwc_gl2 (
        .clk,.rst_n,.lk_tag(gl2_lk),.lk_hit(gl2_hit),.lk_data(gl2_d),
        .fill_en(gl2_fe),.fill_tag(gl2_fk),.fill_data(gl2_fd));
      fa_cache #(.ENTRIES(2), .TAG_W(GL1_TW), .DATA_W(CDW)) u_pwc_gl1 (
        .clk,.rst_n,.lk_tag(gl1_lk),.lk_hit(gl1_hit),.lk_data(gl1_d),
        .fill_en(gl1_fe),.fill_tag(gl1_fk),.fill_data(gl1_fd));
    end else begin : g_nopwc
      assign vml2_hit=1'b0; assign vml1_hit=1'b0; assign gl2_hit=1'b0; assign gl1_hit=1'b0;
      assign vml2_d='0; assign vml1_d='0; assign gl2_d='0; assign gl1_d='0;
    end
    if (HAS_IOTLB != 0) begin : g_iotlb
      // line-organized IOTLB: 2 line slots x CO pages (= IOTLB_N entries), matching the
      // coalesced sequential-IOVA stream. Collapses the CO+CO-way VPN CAM into 2 line-tag
      // compares + an offset-indexed CO:1 data mux (see line_iotlb.sv).
      line_iotlb #(.NLINES(2), .PAGES(CO), .TAG_W(IOTLB_TW), .DATA_W(CDW)) u_iotlb (
        .clk,.rst_n,.lk_tag(iotlb_lk),.lk_hit(iotlb_hit),.lk_data(iotlb_d),
        .fill_en(iotlb_fe),.fill_tag(iotlb_fk),.fill_data(iotlb_fd));
    end else begin : g_noiotlb
      assign iotlb_hit=1'b0; assign iotlb_d='0;
    end
  endgenerate

  // ===================================================== buffer servicer decision (comb)
  logic [VPNLINE_W-1:0] bsel_line;
  assign bsel_line = bvpn_q[bsel][VPN_W-1:LINE_LSB];
  logic bsel_line_busy;
  always_comb begin
    bsel_line_busy=1'b0;
    for (int i=0;i<NCTX;i++)
      if (ws_q[i]!=WFREE && wline_q[i]==bsel_line && wctx_q[i]==bctx_q[bsel]) bsel_line_busy=1'b1;
  end
  logic svc_iotlb, svc_ride, svc_launch;
  assign svc_iotlb  = bsel_v & (HAS_IOTLB!=0) & iotlb_hit;
  assign svc_ride   = bsel_v & ~svc_iotlb & bsel_line_busy;
  assign svc_launch = bsel_v & ~svc_iotlb & ~svc_ride & wfree_v;
  // VS-stage most-complete-hit shortcut for the launched walker
  logic [3:0]       start_pc;  logic [PPN_W-1:0] start_base;
  always_comb begin
    if (HAS_PWC!=0 && vml1_hit)      begin start_pc=4'd8; start_base=ppn28s(vml1_d); end
    else if (HAS_PWC!=0 && vml2_hit) begin start_pc=4'd4; start_base=ppn28s(vml2_d); end
    else                             begin start_pc=4'd0; start_base=vs_root_spa_q; end
  end

  // ---- effective servicer signals: probe-phase (PD<2) or staged probe result (PD>=2).
  // For PD<2 these alias the combinational probe values exactly (no behavior change).
  localparam int SVC_PIPE = (PIPELINE_DEPTH>=2) ? 1 : 0;
  logic                 e_v;       logic [BW-1:0]   e_bsel;
  logic [VPN_W-1:0]     e_vpn;     logic [CTX_W-1:0]e_ctx;
  logic [VPNLINE_W-1:0] e_line;
  logic                 e_iotlb_hit; logic [CDW-1:0] e_iotlb_d;
  logic [3:0]           e_start_pc;  logic [PPN_W-1:0] e_start_base;
  always_comb begin
    if (SVC_PIPE) begin
      e_v=stg_v_q; e_bsel=stg_bsel_q; e_vpn=stg_vpn_q; e_ctx=stg_ctx_q; e_line=stg_line_q;
      e_iotlb_hit=stg_iotlb_hit_q; e_iotlb_d=stg_iotlb_d_q;
      e_start_pc=stg_start_pc_q;   e_start_base=stg_start_base_q;
    end else begin
      e_v=bsel_v; e_bsel=bsel; e_vpn=bvpn_q[bsel]; e_ctx=bctx_q[bsel]; e_line=bsel_line;
      e_iotlb_hit=iotlb_hit; e_iotlb_d=iotlb_d;
      e_start_pc=start_pc;   e_start_base=start_base;
    end
  end
  // commit-time busy re-check (against the CURRENT walker occupancy) + decision
  logic e_busy;
  always_comb begin
    e_busy=1'b0;
    for (int i=0;i<NCTX;i++)
      if (ws_q[i]!=WFREE && wline_q[i]==e_line && wctx_q[i]==e_ctx) e_busy=1'b1;
  end
  logic e_svc_iotlb, e_svc_ride, e_svc_launch;
  assign e_svc_iotlb  = e_v & (HAS_IOTLB!=0) & e_iotlb_hit;
  assign e_svc_ride   = e_v & ~e_svc_iotlb & e_busy;
  // v8(b): single walker -> wfree_v already implies ~e_busy, and a busy walker can't be
  // re-launched anyway, so the e_busy compare is redundant in the launch decision and is
  // dropped off the commit critical path. NCTX>1 keeps the ride guard.
  assign e_svc_launch = e_v & ~e_svc_iotlb & wfree_v & ((NCTX>1) ? ~e_svc_ride : 1'b1);

  // next-line prefetch trigger (cfg4/cfg5)
  logic pf_free, pf_trig, pf_region_ok, pf_launch;  logic [VPNLINE_W-1:0] pf_line;
  generate
    if (PREFETCH_EN != 0) begin : g_pf
      assign pf_free      = wfree_v;     // the single shared walker is idle
      // piped: trigger off the COMMITTED demand service (e_*), one cycle later than probe
      assign pf_region_ok = region_valid_q &
        (region_id_q == (SVC_PIPE ? e_vpn[VPN_W-1:IDX_W] : bvpn_q[bsel][VPN_W-1:IDX_W]));
      prefetch_ctrl #(.VPNLINE_W(VPNLINE_W), .LINE_IN_L0(LINE_IN_L0), .LEAD(PREFETCH_LEAD)) u_pf (
        .clk,.rst_n,
        .demand_service_v(SVC_PIPE ? (e_svc_iotlb | e_svc_launch) : bsel_v),
        .demand_line(SVC_PIPE ? e_line : bsel_line),
        .region_valid(pf_region_ok), .pf_free(pf_free), .pf_trig(pf_trig), .pf_line(pf_line));
    end else begin : g_nopf
      assign pf_free=1'b0; assign pf_trig=1'b0; assign pf_line='0; assign pf_region_ok=1'b0;
    end
  endgenerate
  // prefetch may launch the shared walker only when a demand isn't taking it.
  // PD>=2 (v9c): launch from the PROBE-precomputed prefetch staging (pf_line/addr already
  // computed), gated by a short dedup compare -> the +LEAD adder and iaddr_of are off the
  // launch path. PD<2: the combinational prefetch_ctrl path (unchanged).
  logic pf_launch_c;
  assign pf_launch_c = (PREFETCH_EN!=0) & stg_v_q & (e_svc_iotlb | e_svc_launch)
                     & stg_pf_region_ok_q & stg_pf_same_q & wfree_v & ~e_svc_launch
                     & (stg_pf_line_q != pf_last_q);
  assign pf_launch = SVC_PIPE ? pf_launch_c
                              : ((PREFETCH_EN!=0) & pf_trig & wfree_v & ~e_svc_launch);

  // ===================================================== consume next-state (comb)
  logic [3:0]        next_pc;   logic [PPN_W-1:0] next_base;
  logic [GPN_W-1:0]  next_gpn;  logic [GVPN_W-1:0] next_dg;
  always_comb begin
    next_pc=PC_DONE; next_base=wbase_q[cons_w];
    next_gpn=wgpn_q[cons_w]; next_dg=wdgvpn_q[cons_w];
    unique case (cons_pc)
      4'd0: begin next_gpn=cons_newdg; next_pc=4'd1; next_base=g_root_spa_q; end
      4'd1: begin next_pc=4'd2;  next_base=ppn28(cons_pte); end
      4'd2: begin next_pc=4'd3;  next_base=ppn28(cons_pte); end
      4'd3: begin next_pc=4'd4;  next_base=ppn28(cons_pte); end
      4'd4: begin next_gpn=cons_newdg; next_pc=4'd5; next_base=g_root_spa_q; end
      4'd5: begin next_pc=4'd6;  next_base=ppn28(cons_pte); end
      4'd6: begin next_pc=4'd7;  next_base=ppn28(cons_pte); end
      4'd7: begin next_pc=4'd8;  next_base=ppn28(cons_pte); end
      4'd8: begin next_dg=cons_newdg;
                  if (HAS_PWC!=0 && gl1_hit)      begin next_pc=4'd11; next_base=ppn28s(gl1_d); end
                  else if (HAS_PWC!=0 && gl2_hit) begin next_pc=4'd10; next_base=ppn28s(gl2_d); end
                  else                            begin next_pc=4'd9;  next_base=g_root_spa_q; end end
      4'd9: begin next_pc=4'd10; next_base=ppn28(cons_pte); end
      4'd10:begin next_pc=4'd11; next_base=ppn28(cons_pte); end
      4'd11:begin next_pc=PC_DONE; end
      default: ;
    endcase
  end
  // Note: cons_newdg = gpn27(cons_pte) is the data GVPN read at pc0/pc4 (table GPN) and
  // pc8 (data GPN); next_gpn/next_dg use it for the respective stages.

  // ===================================================== unified memory-issue arbiter
  // per-walker issue request this cycle (fused consume/launch + WRUN fallback)
  logic            iwant [NCTX];
  logic            iburst[NCTX];          // this issue is an 8-beat leaf burst
  logic [PA_W-1:0] iaddr [NCTX];
  always_comb begin
    for (int w=0;w<NCTX;w++) begin
      logic [3:0] pc; logic [PPN_W-1:0] base; logic [VPN_W-1:0] vp;
      logic [GPN_W-1:0] gp; logic [GVPN_W-1:0] dgv; logic sel;
      logic [IDX_W-1:0] ix;
      iwant[w]=1'b0; iaddr[w]='0; iburst[w]=1'b0;
      pc='0; base='0; vp='0; gp='0; dgv='0; sel=1'b0; ix='0;
      // PIPELINE_DEPTH<2: fuse consume->issue and launch->issue (1-cycle/read, long cone).
      // PIPELINE_DEPTH>=2: issue ONLY from a WRUN walker, and read the PRECOMPUTED address
      // register (wiaddr_q/wiburst_q). The idx_of+pte_addr cone runs in the state-write
      // cycle (parallel to next-state), so the issue->AR path is just register+arbiter mux
      // (+1 cycle/read latency). This is the v4 split of the cone exposed after v3.
      if ((PIPELINE_DEPTH<2) && do_consume && (w==int'(cons_w)) && next_pc!=PC_DONE) begin
        pc=next_pc; base=next_base; vp=cons_vpn; gp=next_gpn; dgv=next_dg; sel=1'b1;
      end else if ((PIPELINE_DEPTH<2) && svc_launch && (w==int'(wfree_i))) begin
        pc=start_pc; base=start_base; vp=bvpn_q[bsel]; sel=1'b1;
      end else if (ws_q[w]==WRUN) begin
        if (PIPELINE_DEPTH>=2) begin
          // issue only once the addr-gen stage has produced wiaddr_q for the current wpc
          if (wia_rdy_q[w]) begin iwant[w]=1'b1; iaddr[w]=wiaddr_q[w]; iburst[w]=wiburst_q[w]; end
        end else begin
          pc=wpc_q[w]; base=wbase_q[w]; vp=wvpn_q[w]; gp=wgpn_q[w]; dgv=wdgvpn_q[w]; sel=1'b1;
        end
      end
      if (sel) begin
        iwant[w]=1'b1;
        ix = idx_of(pc, vp, gp, dgv);
        iburst[w] = (CO>1) && (pc==4'd11);             // coalesced G-L0 leaf line
        // burst start = 64 B line base (low 3 index bits cleared); else exact PTE addr
        iaddr[w]  = iburst[w] ? pte_addr(base, {ix[IDX_W-1:3],3'd0}) : pte_addr(base, ix);
      end
    end
  end
  logic            grant_v;  logic [TAGW-1:0] grant_w;
  always_comb begin
    grant_v=1'b0; grant_w='0;
    for (int k=0;k<NCTX;k++) begin
      int i; i=(int'(arb_rr_q)+k)%NCTX;
      if (!grant_v && iwant[i]) begin grant_v=1'b1; grant_w=TAGW'(i); end
    end
  end

  logic mreq_ready;
  mem_master #(.ADDR_W(PA_W), .DATA_W(PTE_W), .TAG_W(TAG_W_TOP),
               .MEM_MAX_OUTSTANDING(MEM_MAX_OUTSTANDING)) u_mem (
    .clk,.rst_n,
    .req_valid(grant_v), .req_ready(mreq_ready),
    .req_addr(iaddr[grant_w]), .req_tag(TAG_W_TOP'(grant_w)), .req_burst(iburst[grant_w]),
    .arvalid, .arready, .araddr, .arid, .arlen, .rvalid, .rready, .rdata, .rid, .rlast,
    .outstanding_o(outstanding_o));
  logic issue_ok;
  assign issue_ok = grant_v & mreq_ready;

  // ===================================================== outputs
  assign rsp_valid = rsel_v;
  assign rsp_vpn   = bvpn_q[rsel];
  assign rsp_spa   = bspa_q[rsel];
  assign req_ready = bfree_v;

  // ===================================================== clocked engine
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int i=0;i<NCTX;i++) begin
        ws_q[i]<=WFREE; wpc_q[i]<=4'd0; wvpn_q[i]<='0; wctx_q[i]<='0; wbase_q[i]<='0;
        wgpn_q[i]<='0; wdgvpn_q[i]<='0; wline_q[i]<='0; wbeat_q[i]<=4'd0;
        wiaddr_q[i]<='0; wiburst_q[i]<=1'b0; wia_rdy_q[i]<=1'b0;
      end
      for (int i=0;i<BUFFER_DEPTH;i++) begin bs_q[i]<=BFREE; bvpn_q[i]<='0; bctx_q[i]<='0; bspa_q[i]<='0; end
      arb_rr_q<='0; brr_q<='0;
`ifndef SYNTHESIS
      walks_q<='0; resp_q<='0;
      walk_inc_q<=1'b0; pf_inc_q<=1'b0; resp_inc_q<=1'b0;
`endif
      region_vml0_q<='0; region_valid_q<=1'b0; region_id_q<='0;
      stg_v_q<=1'b0; stg_bsel_q<='0; stg_vpn_q<='0; stg_ctx_q<='0; stg_line_q<='0;
      stg_iotlb_hit_q<=1'b0; stg_iotlb_d_q<='0; stg_start_pc_q<=4'd0; stg_start_base_q<='0;
      stg_start_addr_q<='0; stg_start_burst_q<=1'b0;
      stg_pf_line_q<='0; stg_pf_addr_q<='0; stg_pf_same_q<=1'b0; stg_pf_region_ok_q<=1'b0;
      stg_pf_regbase_q<='0; pf_last_q<='0;
      rvalid_q<=1'b0; rlast_q<=1'b0; rdata_q<='0; rid_q<='0;
    end else begin
      // R channel register slice (consumed by the engine when PIPELINE_DEPTH>=2)
      rvalid_q <= rvalid; rlast_q <= rlast; rdata_q <= rdata; rid_q <= rid;
      // accept request
      if (req_valid & req_ready) begin
        bs_q[bfree_i]<=BNEED; bvpn_q[bfree_i]<=req_vpn; bctx_q[bfree_i]<={req_device_id,req_pasid};
      end

      // buffer servicer.
      if (SVC_PIPE) begin
        // PD>=2: probe/commit pipeline. PROBE latches the cache-lookup result of the
        // selected BNEED entry (puts the CAM-compare cone in this cycle, ending at the
        // staging FFs). COMMIT (next cycle) resolves-on-hit or launches from the staged
        // values, so the launch arc is reg->start mux->{wbase_q,wiaddr_q} (no CAM). The
        // staging is 1-shot (cleared every commit), so a stale probe re-probes, no deadlock.
        if (bsel_v & ~stg_v_q) begin                       // probe: latch lookup result
          stg_v_q<=1'b1; stg_bsel_q<=bsel; stg_vpn_q<=bvpn_q[bsel]; stg_ctx_q<=bctx_q[bsel];
          stg_line_q<=bsel_line; stg_iotlb_hit_q<=(HAS_IOTLB!=0)&iotlb_hit;
          stg_iotlb_d_q<=iotlb_d; stg_start_pc_q<=start_pc; stg_start_base_q<=start_base;
          // v8(a): precompute the demand launch read address now (iaddr_of off commit path)
          stg_start_addr_q  <= iaddr_of(start_pc, start_base, bvpn_q[bsel], '0, '0);
          stg_start_burst_q <= (CO>1) && (start_pc==4'd11);
          // v9(c): precompute the next-line prefetch target+address+guards now (the +LEAD
          // adder and iaddr_of leave the launch path; commit just reg->reg + dedup compare)
          if (PREFETCH_EN!=0) begin
            stg_pf_line_q     <= bsel_line + VPNLINE_W'(PREFETCH_LEAD);
            stg_pf_same_q     <= ((bsel_line + VPNLINE_W'(PREFETCH_LEAD)) >> LINE_IN_L0)
                                  == (bsel_line >> LINE_IN_L0);
            stg_pf_addr_q     <= iaddr_of(4'd8, region_vml0_q,
                                  {(bsel_line + VPNLINE_W'(PREFETCH_LEAD)), {LINE_LSB{1'b0}}}, '0, '0);
            stg_pf_region_ok_q<= region_valid_q & (region_id_q == bvpn_q[bsel][VPN_W-1:IDX_W]);
            stg_pf_regbase_q  <= region_vml0_q;
          end
          brr_q <= (brr_q==BW'(BUFFER_DEPTH-1)) ? '0 : brr_q+BW'(1);
        end
        if (stg_v_q) begin                                 // commit: act on staged probe
          stg_v_q<=1'b0;
          // independent (mutually exclusive) actions: launch does NOT gate on ~e_svc_ride,
          // so e_busy stays off the commit critical path (v8(b)).
          if (e_svc_iotlb) begin
            bs_q[e_bsel]<=BRES; bspa_q[e_bsel]<={ppn28s(e_iotlb_d),{OFFSET_W{1'b0}}};
          end
          if (e_svc_launch) begin                          // reg->reg writes only (v8(a))
            ws_q[wfree_i]<=WRUN; wpc_q[wfree_i]<=e_start_pc; wbase_q[wfree_i]<=e_start_base;
            wvpn_q[wfree_i]<=e_vpn; wctx_q[wfree_i]<=e_ctx; wline_q[wfree_i]<=e_line;
            wiaddr_q[wfree_i] <= stg_start_addr_q;
            wiburst_q[wfree_i]<= stg_start_burst_q;
            wia_rdy_q[wfree_i]<= 1'b1;                      // addr precomputed at probe
          end
          // else (ride / no free walker): wait, re-probe next cycle
        end
      end else begin
        // PD<2: single-cycle combinational servicer
        if (bsel_v) begin
          if (svc_iotlb) begin
            bs_q[bsel]<=BRES; bspa_q[bsel]<={ppn28s(iotlb_d),{OFFSET_W{1'b0}}};
          end else if (svc_ride) begin
            // wait for the in-flight / just-filled line
          end else if (svc_launch) begin
            ws_q[wfree_i]<=WRUN; wpc_q[wfree_i]<=start_pc; wbase_q[wfree_i]<=start_base;
            wvpn_q[wfree_i]<=bvpn_q[bsel]; wctx_q[wfree_i]<=bctx_q[bsel];
            wline_q[wfree_i]<=bsel_line;
          end
          brr_q <= (brr_q==BW'(BUFFER_DEPTH-1)) ? '0 : brr_q+BW'(1);
        end
      end

      // prefetch launch: reuse the idle shared walker (only when demand isn't taking
      // it), warm-start at the VM-L0 leaf for line+LEAD using the captured region base
      if (pf_launch) begin
        if (SVC_PIPE) begin             // v9(c): launch from probe-precomputed staging
          ws_q[wfree_i]<=WRUN; wpc_q[wfree_i]<=4'd8; wbase_q[wfree_i]<=stg_pf_regbase_q;
          wvpn_q[wfree_i]<={stg_pf_line_q, {LINE_LSB{1'b0}}}; wctx_q[wfree_i]<=stg_ctx_q;
          wline_q[wfree_i]<=stg_pf_line_q;
          wiaddr_q[wfree_i]<=stg_pf_addr_q; wiburst_q[wfree_i]<=1'b0;
          wia_rdy_q[wfree_i]<=1'b1;      // addr precomputed at probe
          pf_last_q <= stg_pf_line_q;   // dedup
        end else begin                  // PD<2: combinational prefetch_ctrl path
          ws_q[wfree_i]<=WRUN; wpc_q[wfree_i]<=4'd8; wbase_q[wfree_i]<=region_vml0_q;
          wvpn_q[wfree_i]<={pf_line, {LINE_LSB{1'b0}}}; wctx_q[wfree_i]<=bctx_q[bsel];
          wline_q[wfree_i]<=pf_line;
        end
      end

      // v10 addr-gen stage: for a WRUN walker whose address isn't ready (just set by a
      // consume), compute the next read address from the REGISTERED walker state. This
      // moves idx_of+pte_addr out of the consume next-state cone into its own cycle. The
      // walker just-consumed this cycle is still WWAIT here, so it is picked up next cycle;
      // launch/prefetch precompute the addr (wia_rdy=1), so only mid-walk steps pay +1cyc.
      if (PIPELINE_DEPTH>=2) begin
        for (int w=0;w<NCTX;w++)
          if (ws_q[w]==WRUN && !wia_rdy_q[w]) begin
            wiaddr_q[w]  <= iaddr_of(wpc_q[w], wbase_q[w], wvpn_q[w], wgpn_q[w], wdgvpn_q[w]);
            wiburst_q[w] <= (CO>1) && (wpc_q[w]==4'd11);
            wia_rdy_q[w] <= 1'b1;
          end
      end
`ifndef SYNTHESIS
      // sim-only perf counters (registered enables; excluded from synthesis)
      walk_inc_q <= e_svc_launch; pf_inc_q <= pf_launch; resp_inc_q <= (rsel_v & rsp_ready);
      walks_q <= walks_q + 32'(walk_inc_q) + 32'(pf_inc_q);
`endif

      // consume a tagged return: advance state (or complete), update PWC fills are comb
      if (do_consume) begin
        wpc_q[cons_w]   <= next_pc;
        wbase_q[cons_w] <= next_base;
        wgpn_q[cons_w]  <= next_gpn;
        wdgvpn_q[cons_w]<= next_dg;
        // v10: do NOT compute the address here (that fused next-state + iaddr_of into one
        // cone). Mark it stale; the addr-gen stage computes it next cycle from the
        // registered walker state. The WRUN issue waits for wia_rdy_q.
        if (PIPELINE_DEPTH>=2 && next_pc!=PC_DONE) wia_rdy_q[cons_w] <= 1'b0;
        if (next_pc==PC_DONE) begin
          // CO==1 leaf (single beat): resolve the demand entry directly. (CO>1 leaf
          // completes via the burst path below, never here.)
          for (int b=0;b<BUFFER_DEPTH;b++)
            if (bs_q[b]==BNEED && bvpn_q[b][VPN_W-1:LINE_LSB]==wline_q[cons_w] &&
                bctx_q[b]==wctx_q[cons_w]) begin
              bs_q[b]<=BRES;
              bspa_q[b]<={ppn28(r_data),{OFFSET_W{1'b0}}};
            end
          ws_q[cons_w]<=WFREE; wpc_q[cons_w]<=4'd0;
        end else begin
          ws_q[cons_w]<=WRUN;          // becomes eligible; arbiter may upgrade to WWAIT
        end
        // capture the region's VM-L0 table base from the cold walk (pc7 -> pc8), so a
        // shared-walker prefetch can warm-start at the leaf for the next line.
        if (PREFETCH_EN!=0 && cons_pc==4'd7) begin
          region_vml0_q  <= ppn28(cons_pte);
          region_valid_q <= 1'b1;
          region_id_q    <= cons_vpn[VPN_W-1:IDX_W];
        end
      end

      // pc11 leaf burst beat (CO>1): beat j carries page j's SPA. Fill IOTLB[j]
      // (combinational fill port above), broadcast-resolve the rider on page j, count
      // beats, free the walker on rlast.
      if (burst_beat) begin
        for (int b=0;b<BUFFER_DEPTH;b++)
          if (bs_q[b]==BNEED && bvpn_q[b][VPN_W-1:LINE_LSB]==wline_q[cons_w] &&
              bctx_q[b]==wctx_q[cons_w] &&
              bvpn_q[b][LBW-1:0]==wbeat_q[cons_w][LBW-1:0]) begin
            bs_q[b]<=BRES;
            bspa_q[b]<={ppn28(r_data),{OFFSET_W{1'b0}}};
          end
        wbeat_q[cons_w] <= wbeat_q[cons_w] + 4'd1;
        if (r_last) begin ws_q[cons_w]<=WFREE; wpc_q[cons_w]<=4'd0; wbeat_q[cons_w]<=4'd0; end
      end

      // arbiter grant: the issuing walker goes WWAIT (overrides WRUN set above)
      if (issue_ok) begin
        ws_q[grant_w]<=WWAIT; wbeat_q[grant_w]<=4'd0;
        arb_rr_q <= (arb_rr_q==TAGW'(NCTX-1)) ? '0 : arb_rr_q+TAGW'(1);
      end

      // response handshake
      if (rsel_v & rsp_ready) bs_q[rsel]<=BFREE;   // functional free (combinational decide)
`ifndef SYNTHESIS
      resp_q <= resp_q + 32'(resp_inc_q);          // sim-only perf counter (excluded from synth)
`endif
    end
  end
endmodule
