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

  // prefetch (next-line) region capture
  logic [PPN_W-1:0]  region_vml0_q;
  logic              region_valid_q;
  logic [REGID_W-1:0]region_id_q;

  logic [TAGW-1:0]  arb_rr_q;
  logic [BW-1:0]    brr_q;
  logic [31:0]      walks_q, resp_q;
  assign walks_o=walks_q; assign resp_o=resp_q;

  // ---------------- helpers ----------------
  function automatic logic [PPN_W-1:0] ppn28(input logic [PTE_W-1:0] p);
    logic [43:0] f; f = pte_ppn44(p); return f[PPN_W-1:0];
  endfunction
  function automatic logic [GPN_W-1:0] gpn27(input logic [PTE_W-1:0] p);
    logic [43:0] f; f = pte_ppn44(p); return f[GPN_W-1:0];
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
  // consuming walker (from memory return tag)
  logic [TAGW-1:0]   cons_w;
  logic [VPN_W-1:0]  cons_vpn;  logic [CTX_W-1:0] cons_ctx;  logic [GVPN_W-1:0] cons_dgvpn;
  logic [3:0]        cons_pc;
  assign cons_w     = rid[TAGW-1:0];
  assign cons_vpn   = wvpn_q[cons_w];
  assign cons_ctx   = wctx_q[cons_w];
  assign cons_dgvpn = wdgvpn_q[cons_w];
  assign cons_pc    = wpc_q[cons_w];
  // a pc11 leaf read (CO>1) returns 8 beats -> burst path; everything else is 1 beat
  logic        burst_beat, do_consume;
  logic [PTE_W-1:0] cons_pte;
  assign burst_beat = (CO>1) & rvalid & rready & (ws_q[cons_w]==WWAIT) & (cons_pc==4'd11);
  assign do_consume = rvalid & rready & (ws_q[cons_w]==WWAIT) & ~burst_beat;
  assign cons_pte   = rdata;            // 8 B data bus: rdata IS the PTE
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
  logic [PPN_W-1:0] vml2_fd, vml1_fd, gl2_fd, gl1_fd, iotlb_fd;
  logic [VML2_TW-1:0] vml2_fk;  logic [VML1_TW-1:0] vml1_fk;
  logic [GL2_TW-1:0]  gl2_fk;   logic [GL1_TW-1:0]  gl1_fk;
  logic [IOTLB_TW-1:0] iotlb_fk;
  assign vml2_fe = (HAS_PWC!=0) & do_consume & (cons_pc==4'd3);
  assign vml1_fe = (HAS_PWC!=0) & do_consume & (cons_pc==4'd7);
  assign gl2_fe  = (HAS_PWC!=0) & do_consume & (cons_pc==4'd9);
  assign gl1_fe  = (HAS_PWC!=0) & do_consume & (cons_pc==4'd10);
  assign vml2_fd = ppn28(cons_pte); assign vml1_fd = ppn28(cons_pte);
  assign gl2_fd  = ppn28(cons_pte); assign gl1_fd  = ppn28(cons_pte);
  // IOTLB filled one entry per burst beat (beat j -> page j of the line)
  assign iotlb_fe = (HAS_IOTLB!=0) & burst_beat;
  assign iotlb_fd = ppn28(rdata);

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
  logic [PPN_W-1:0] vml2_d, vml1_d, gl2_d, gl1_d, iotlb_d;
  generate
    if (HAS_PWC != 0) begin : g_pwc
      fa_cache #(.ENTRIES(1), .TAG_W(VML2_TW), .DATA_W(PPN_W)) u_pwc_vml2 (
        .clk,.rst_n,.lk_tag(vml2_lk),.lk_hit(vml2_hit),.lk_data(vml2_d),
        .fill_en(vml2_fe),.fill_tag(vml2_fk),.fill_data(vml2_fd));
      fa_cache #(.ENTRIES(2), .TAG_W(VML1_TW), .DATA_W(PPN_W)) u_pwc_vml1 (
        .clk,.rst_n,.lk_tag(vml1_lk),.lk_hit(vml1_hit),.lk_data(vml1_d),
        .fill_en(vml1_fe),.fill_tag(vml1_fk),.fill_data(vml1_fd));
      fa_cache #(.ENTRIES(1), .TAG_W(GL2_TW), .DATA_W(PPN_W)) u_pwc_gl2 (
        .clk,.rst_n,.lk_tag(gl2_lk),.lk_hit(gl2_hit),.lk_data(gl2_d),
        .fill_en(gl2_fe),.fill_tag(gl2_fk),.fill_data(gl2_fd));
      fa_cache #(.ENTRIES(2), .TAG_W(GL1_TW), .DATA_W(PPN_W)) u_pwc_gl1 (
        .clk,.rst_n,.lk_tag(gl1_lk),.lk_hit(gl1_hit),.lk_data(gl1_d),
        .fill_en(gl1_fe),.fill_tag(gl1_fk),.fill_data(gl1_fd));
    end else begin : g_nopwc
      assign vml2_hit=1'b0; assign vml1_hit=1'b0; assign gl2_hit=1'b0; assign gl1_hit=1'b0;
      assign vml2_d='0; assign vml1_d='0; assign gl2_d='0; assign gl1_d='0;
    end
    if (HAS_IOTLB != 0) begin : g_iotlb
      fa_cache #(.ENTRIES(IOTLB_N), .TAG_W(IOTLB_TW), .DATA_W(PPN_W)) u_iotlb (
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
    if (HAS_PWC!=0 && vml1_hit)      begin start_pc=4'd8; start_base=vml1_d; end
    else if (HAS_PWC!=0 && vml2_hit) begin start_pc=4'd4; start_base=vml2_d; end
    else                             begin start_pc=4'd0; start_base=vs_root_spa_q; end
  end

  // next-line prefetch trigger (cfg4/cfg5)
  logic pf_free, pf_trig, pf_region_ok, pf_launch;  logic [VPNLINE_W-1:0] pf_line;
  generate
    if (PREFETCH_EN != 0) begin : g_pf
      assign pf_free      = wfree_v;     // the single shared walker is idle
      assign pf_region_ok = region_valid_q & (region_id_q == bvpn_q[bsel][VPN_W-1:IDX_W]);
      prefetch_ctrl #(.VPNLINE_W(VPNLINE_W), .LINE_IN_L0(LINE_IN_L0), .LEAD(PREFETCH_LEAD)) u_pf (
        .clk,.rst_n, .demand_service_v(bsel_v), .demand_line(bsel_line),
        .region_valid(pf_region_ok), .pf_free(pf_free), .pf_trig(pf_trig), .pf_line(pf_line));
    end else begin : g_nopf
      assign pf_free=1'b0; assign pf_trig=1'b0; assign pf_line='0; assign pf_region_ok=1'b0;
    end
  endgenerate
  // prefetch may launch the shared walker only when a demand isn't taking it
  assign pf_launch = (PREFETCH_EN!=0) & pf_trig & wfree_v & ~svc_launch;

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
                  if (HAS_PWC!=0 && gl1_hit)      begin next_pc=4'd11; next_base=gl1_d; end
                  else if (HAS_PWC!=0 && gl2_hit) begin next_pc=4'd10; next_base=gl2_d; end
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
      if (do_consume && (w==int'(cons_w)) && next_pc!=PC_DONE) begin
        pc=next_pc; base=next_base; vp=cons_vpn; gp=next_gpn; dgv=next_dg; sel=1'b1;
      end else if (svc_launch && (w==int'(wfree_i))) begin
        pc=start_pc; base=start_base; vp=bvpn_q[bsel]; sel=1'b1;
      end else if (ws_q[w]==WRUN) begin
        pc=wpc_q[w]; base=wbase_q[w]; vp=wvpn_q[w]; gp=wgpn_q[w]; dgv=wdgvpn_q[w]; sel=1'b1;
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
      end
      for (int i=0;i<BUFFER_DEPTH;i++) begin bs_q[i]<=BFREE; bvpn_q[i]<='0; bctx_q[i]<='0; bspa_q[i]<='0; end
      arb_rr_q<='0; brr_q<='0; walks_q<='0; resp_q<='0;
      region_vml0_q<='0; region_valid_q<=1'b0; region_id_q<='0;
    end else begin
      // accept request
      if (req_valid & req_ready) begin
        bs_q[bfree_i]<=BNEED; bvpn_q[bfree_i]<=req_vpn; bctx_q[bfree_i]<={req_device_id,req_pasid};
      end

      // buffer servicer
      if (bsel_v) begin
        if (svc_iotlb) begin
          bs_q[bsel]<=BRES; bspa_q[bsel]<={iotlb_d,{OFFSET_W{1'b0}}};
        end else if (svc_ride) begin
          // wait for the in-flight / just-filled line
        end else if (svc_launch) begin
          ws_q[wfree_i]<=WRUN; wpc_q[wfree_i]<=start_pc; wbase_q[wfree_i]<=start_base;
          wvpn_q[wfree_i]<=bvpn_q[bsel]; wctx_q[wfree_i]<=bctx_q[bsel];
          wline_q[wfree_i]<=bsel_line;
        end
        brr_q <= (brr_q==BW'(BUFFER_DEPTH-1)) ? '0 : brr_q+BW'(1);
      end

      // prefetch launch: reuse the idle shared walker (only when demand isn't taking
      // it), warm-start at the VM-L0 leaf for line+LEAD using the captured region base
      if (pf_launch) begin
        ws_q[wfree_i]<=WRUN; wpc_q[wfree_i]<=4'd8; wbase_q[wfree_i]<=region_vml0_q;
        wvpn_q[wfree_i]<={pf_line, {LINE_LSB{1'b0}}}; wctx_q[wfree_i]<=bctx_q[bsel];
        wline_q[wfree_i]<=pf_line;
      end
      walks_q <= walks_q + 32'(svc_launch) + 32'(pf_launch);

      // consume a tagged return: advance state (or complete), update PWC fills are comb
      if (do_consume) begin
        wpc_q[cons_w]   <= next_pc;
        wbase_q[cons_w] <= next_base;
        wgpn_q[cons_w]  <= next_gpn;
        wdgvpn_q[cons_w]<= next_dg;
        if (next_pc==PC_DONE) begin
          // CO==1 leaf (single beat): resolve the demand entry directly. (CO>1 leaf
          // completes via the burst path below, never here.)
          for (int b=0;b<BUFFER_DEPTH;b++)
            if (bs_q[b]==BNEED && bvpn_q[b][VPN_W-1:LINE_LSB]==wline_q[cons_w] &&
                bctx_q[b]==wctx_q[cons_w]) begin
              bs_q[b]<=BRES;
              bspa_q[b]<={ppn28(rdata),{OFFSET_W{1'b0}}};
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
            bspa_q[b]<={ppn28(rdata),{OFFSET_W{1'b0}}};
          end
        wbeat_q[cons_w] <= wbeat_q[cons_w] + 4'd1;
        if (rlast) begin ws_q[cons_w]<=WFREE; wpc_q[cons_w]<=4'd0; wbeat_q[cons_w]<=4'd0; end
      end

      // arbiter grant: the issuing walker goes WWAIT (overrides WRUN set above)
      if (issue_ok) begin
        ws_q[grant_w]<=WWAIT; wbeat_q[grant_w]<=4'd0;
        arb_rr_q <= (arb_rr_q==TAGW'(NCTX-1)) ? '0 : arb_rr_q+TAGW'(1);
      end

      // response handshake
      if (rsel_v & rsp_ready) begin bs_q[rsel]<=BFREE; resp_q<=resp_q+32'd1; end
    end
  end
endmodule
