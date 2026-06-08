// txn_buffer.sv -- IOMMU front-end: transaction buffer + MSHR + caches + PWC.
//
// Detailed (STEP 1) version for the full Sv39 pointer chase:
//  * per-context ROOT pointer register (satp-like), pre-loaded by the TB; the
//    walk's level-2 table base comes from here.
//  * S1 PWC (L2 + L1) now stores the real next-level table-base PPN, so a PWC hit
//    short-circuits the walk: the front-end dispatches a start_level + base and the
//    walker only chases the residual levels (steady state -> 1 leaf read / line).
//  * combined IOTLB stores the coalesced line-base SPA; per-page SPA = base +
//    page-offset-within-line (valid for linear leaf mappings; the 512b leaf line
//    buffer in the walker realises the coalescing storage).
//  * MSHR = the buffer (same-line in-flight entries share one walk).
// Happy-path / steady-state only: context pre-loaded; no fault/permission logic.
import iommu_pkg::*;

module txn_buffer #(
  parameter int MODE            = MODE_S1_ONLY,
  parameter int COALESCE_FACTOR = 8,
  parameter int BUFFER_DEPTH    = 16,
  parameter int PREFETCH_EN     = 0,
  parameter int CLOCK_GATING_EN = 0,
  parameter int IOTLB_ENTRIES = 64, parameter int IOTLB_ASSOC = 4,  parameter int IOTLB_STORAGE = ST_SRAM,
  parameter int S1PWC_ENTRIES = 16, parameter int S1PWC_ASSOC = 16, parameter int S1PWC_STORAGE = ST_FF,
  parameter int S2PWC_ENTRIES = 16, parameter int S2PWC_ASSOC = 16, parameter int S2PWC_STORAGE = ST_FF,
  parameter int DDTC_ENTRIES  = 4,  parameter int PDTC_ENTRIES  = 4,
  parameter int MSHR_W        = (BUFFER_DEPTH < 2) ? 1 : $clog2(BUFFER_DEPTH),
  parameter int IOTLB_KEY_W   = CTX_W + VPN_W,
  parameter int PWC_KEY_W     = CTX_W + VPN_W
)(
  input  logic              clk,
  input  logic              rst_n,

  input  logic              req_valid,
  output logic              req_ready,
  input  req_t              req,

  output logic              rsp_valid,
  input  logic              rsp_ready,
  output logic [SPA_W-1:0]  rsp_spa,
  output logic [MSHR_W-1:0] rsp_tag,

  // walk dispatch / completion (to walk_engine)
  output logic              disp_valid,
  input  logic              disp_ready,
  output logic [VPN_W-1:0]  disp_vpn,
  output logic [1:0]        disp_start_level,
  output logic [PPN_W-1:0]  disp_base,
  output logic [MSHR_W-1:0] disp_mshr,

  input  logic              done_valid,
  output logic              done_ready,
  input  logic [MSHR_W-1:0] done_mshr,
  input  logic [VPN_W-1:0]  done_vpn,
  input  logic [1:0]        done_start_level,
  input  logic [SPA_W-1:0]  done_spa,
  input  logic [PPN_W-1:0]  done_l1tab,
  input  logic [PPN_W-1:0]  done_leaftab,
  input  logic [LINE_W-1:0] done_leafline,

  // preload (TB): 0=iotlb 1=s1_l1 2=s2pwc 3=ddtc 4=pdtc 5=s1_l2 6=root
  input  logic              pl_valid,
  input  logic [2:0]        pl_sel,
  input  logic [IOTLB_KEY_W-1:0] pl_key,
  input  logic [SPA_W-1:0]  pl_data,

  output logic [31:0]       cnt_iotlb_hit,
  output logic [31:0]       cnt_coalesced,
  output logic [31:0]       cnt_walks,
  output logic [31:0]       buf_occupancy
);
  localparam int CW     = (COALESCE_FACTOR < 2) ? 0 : $clog2(COALESCE_FACTOR);
  localparam int COFFM  = COALESCE_FACTOR - 1;   // page-offset-within-line mask

  // key builders ------------------------------------------------------------
  function automatic logic [VPN_W-1:0] line_of(logic [VPN_W-1:0] v); return VPN_W'(v >> CW); endfunction
  function automatic logic [IOTLB_KEY_W-1:0] ik(logic [VPN_W-1:0] v, logic [CTX_W-1:0] c); return {c, line_of(v)}; endfunction
  function automatic logic [PWC_KEY_W-1:0]   l2k(logic [VPN_W-1:0] v, logic [CTX_W-1:0] c); return {c, VPN_W'(v >> 18)}; endfunction
  function automatic logic [PWC_KEY_W-1:0]   l1k(logic [VPN_W-1:0] v, logic [CTX_W-1:0] c); return {c, VPN_W'(v >> 9)};  endfunction
  function automatic logic [SPA_W-1:0] page_off(logic [VPN_W-1:0] v); return SPA_W'(v & VPN_W'(COFFM)) << OFFSET_W; endfunction

  // per-context root pointer (satp-like), pre-loaded by the TB ---------------
  logic [PPN_W-1:0] root_ppn_q;

  // buffer ------------------------------------------------------------------
  localparam logic [1:0] ST_FREE=2'd0, ST_LOOK=2'd1, ST_WALK=2'd2, ST_DONE=2'd3;
  logic [1:0]        e_state [BUFFER_DEPTH];
  logic [VPN_W-1:0]  e_vpn   [BUFFER_DEPTH];
  logic [CTX_W-1:0]  e_ctx   [BUFFER_DEPTH];
  logic [VPN_W-1:0]  e_line  [BUFFER_DEPTH];
  logic [SPA_W-1:0]  e_spa   [BUFFER_DEPTH];
  logic              e_leader[BUFFER_DEPTH];

  // caches ------------------------------------------------------------------
  logic              iotlb_lk, iotlb_hit, iotlb_fill;
  logic [IOTLB_KEY_W-1:0] iotlb_lk_key, iotlb_fill_key;
  logic [SPA_W-1:0]  iotlb_rdata, iotlb_fill_data;
  cache_store #(.ENTRIES(IOTLB_ENTRIES), .ASSOC(IOTLB_ASSOC), .KEY_W(IOTLB_KEY_W),
                .DATA_W(SPA_W), .STORAGE(IOTLB_STORAGE)) u_iotlb (
    .clk, .rst_n, .lookup_en(iotlb_lk), .lookup_key(iotlb_lk_key),
    .hit(iotlb_hit), .rdata(iotlb_rdata),
    .fill_en(iotlb_fill), .fill_key(iotlb_fill_key), .fill_data(iotlb_fill_data), .inval_all(1'b0));

  logic              l2_lk, l2_hit, l2_fill;
  logic [PWC_KEY_W-1:0] l2_lk_key, l2_fill_key;
  logic [PPN_W-1:0]  l2_rdata, l2_fill_data;
  cache_store #(.ENTRIES(S1PWC_ENTRIES), .ASSOC(S1PWC_ASSOC), .KEY_W(PWC_KEY_W),
                .DATA_W(PPN_W), .STORAGE(S1PWC_STORAGE)) u_s1_l2 (
    .clk, .rst_n, .lookup_en(l2_lk), .lookup_key(l2_lk_key), .hit(l2_hit), .rdata(l2_rdata),
    .fill_en(l2_fill), .fill_key(l2_fill_key), .fill_data(l2_fill_data), .inval_all(1'b0));

  logic              l1_lk, l1_hit, l1_fill;
  logic [PWC_KEY_W-1:0] l1_lk_key, l1_fill_key;
  logic [PPN_W-1:0]  l1_rdata, l1_fill_data;
  cache_store #(.ENTRIES(S1PWC_ENTRIES), .ASSOC(S1PWC_ASSOC), .KEY_W(PWC_KEY_W),
                .DATA_W(PPN_W), .STORAGE(S1PWC_STORAGE)) u_s1_l1 (
    .clk, .rst_n, .lookup_en(l1_lk), .lookup_key(l1_lk_key), .hit(l1_hit), .rdata(l1_rdata),
    .fill_en(l1_fill), .fill_key(l1_fill_key), .fill_data(l1_fill_data), .inval_all(1'b0));

  // S2 PWC + DDT$/PDT$ kept for area completeness (pre-loaded context; happy path).
  logic s2_fill; cache_store #(.ENTRIES(S2PWC_ENTRIES), .ASSOC(S2PWC_ASSOC), .KEY_W(PWC_KEY_W),
                .DATA_W(PPN_W), .STORAGE(S2PWC_STORAGE)) u_s2pwc (
    .clk, .rst_n, .lookup_en(1'b0), .lookup_key('0), .hit(), .rdata(),
    .fill_en(s2_fill), .fill_key(pl_key[PWC_KEY_W-1:0]), .fill_data('0), .inval_all(1'b0));
  assign s2_fill = pl_valid && pl_sel == 3'd2;

  // selection of buffer entries --------------------------------------------
  logic any_look, any_free, any_done_e;
  logic [MSHR_W-1:0] look_id, free_id, done_e_id;
  always_comb begin
    any_look=0; look_id='0; any_free=0; free_id='0; any_done_e=0; done_e_id='0;
    for (int i = BUFFER_DEPTH-1; i >= 0; i--) begin
      if (e_state[i]==ST_LOOK) begin any_look=1; look_id=MSHR_W'(i); end
      if (e_state[i]==ST_FREE) begin any_free=1; free_id=MSHR_W'(i); end
      if (e_state[i]==ST_DONE) begin any_done_e=1; done_e_id=MSHR_W'(i); end
    end
  end

  logic [MSHR_W-1:0] cur;
  logic coalesce_hit;
  always_comb begin
    coalesce_hit = 1'b0;
    for (int i = 0; i < BUFFER_DEPTH; i++)
      if (e_state[i]==ST_WALK && e_line[i]==e_line[cur] && e_ctx[i]==e_ctx[cur]) coalesce_hit = 1'b1;
  end

  typedef enum logic [1:0] {L_IDLE, L_IOTLB, L_PWC} lstate_e;
  lstate_e lstate;

  assign req_ready = any_free;
  assign rsp_valid = any_done_e;
  assign rsp_spa   = e_spa[done_e_id];
  assign rsp_tag   = done_e_id;
  assign done_ready = 1'b1;

  // dispatch outputs
  logic disp_valid_q; logic [VPN_W-1:0] disp_vpn_q; logic [1:0] disp_slvl_q;
  logic [PPN_W-1:0] disp_base_q; logic [MSHR_W-1:0] disp_mshr_q;
  assign disp_valid=disp_valid_q; assign disp_vpn=disp_vpn_q; assign disp_start_level=disp_slvl_q;
  assign disp_base=disp_base_q;   assign disp_mshr=disp_mshr_q;

  // lookup port drive (registered-lookup caches: result valid next cycle)
  assign iotlb_lk     = (lstate==L_IDLE) && any_look;
  assign iotlb_lk_key = ik(e_vpn[look_id], e_ctx[look_id]);
  assign l2_lk        = (lstate==L_IOTLB) && !iotlb_hit && !coalesce_hit;
  assign l1_lk        = l2_lk;
  assign l2_lk_key    = l2k(e_vpn[cur], e_ctx[cur]);
  assign l1_lk_key    = l1k(e_vpn[cur], e_ctx[cur]);

  // --- completion fills (one cycle; IOTLB + the two PWC levels in parallel) ---
  logic [CTX_W-1:0] d_ctx;   assign d_ctx = e_ctx[done_mshr];
  logic [SPA_W-1:0] line_base; assign line_base = done_spa - page_off(done_vpn);
  assign iotlb_fill      = pl_valid ? (pl_sel==3'd0) : done_valid;
  assign iotlb_fill_key  = pl_valid ? pl_key : ik(done_vpn, d_ctx);
  assign iotlb_fill_data = pl_valid ? pl_data : line_base;
  assign l2_fill         = pl_valid ? (pl_sel==3'd5) : (done_valid && done_start_level>=2'd2);
  assign l2_fill_key     = pl_valid ? pl_key[PWC_KEY_W-1:0] : l2k(done_vpn, d_ctx);
  assign l2_fill_data    = pl_valid ? pl_data[PPN_W-1:0]    : done_l1tab;
  assign l1_fill         = pl_valid ? (pl_sel==3'd1) : (done_valid && done_start_level>=2'd1);
  assign l1_fill_key     = pl_valid ? pl_key[PWC_KEY_W-1:0] : l1k(done_vpn, d_ctx);
  assign l1_fill_data    = pl_valid ? pl_data[PPN_W-1:0]    : done_leaftab;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int i=0;i<BUFFER_DEPTH;i++) begin e_state[i]<=ST_FREE; e_leader[i]<=1'b0; end
      lstate<=L_IDLE; cur<='0; root_ppn_q<='0;
      disp_valid_q<=1'b0; disp_vpn_q<='0; disp_slvl_q<='0; disp_base_q<='0; disp_mshr_q<='0;
      cnt_iotlb_hit<='0; cnt_coalesced<='0; cnt_walks<='0;
    end else begin
      if (pl_valid && pl_sel==3'd6) root_ppn_q <= pl_data[PPN_W-1:0];   // preload root

      if (req_valid && req_ready) begin
        e_state[free_id]<=ST_LOOK; e_vpn[free_id]<=req.vpn; e_ctx[free_id]<=ctx_of(req);
        e_line[free_id]<=line_of(req.vpn); e_leader[free_id]<=1'b0;
      end

      case (lstate)
        L_IDLE: if (any_look) begin cur<=look_id; lstate<=L_IOTLB; end
        L_IOTLB: begin
          if (iotlb_hit) begin
            e_spa[cur]<=iotlb_rdata + page_off(e_vpn[cur]); e_state[cur]<=ST_DONE;
            cnt_iotlb_hit<=cnt_iotlb_hit+1'b1; lstate<=L_IDLE;
          end else if (coalesce_hit) begin
            e_state[cur]<=ST_WALK; cnt_coalesced<=cnt_coalesced+1'b1; lstate<=L_IDLE;
          end else lstate<=L_PWC;   // PWC results valid this -> next cycle
        end
        L_PWC: begin
          if (!disp_valid_q) begin
            disp_valid_q<=1'b1; disp_vpn_q<=e_vpn[cur]; disp_mshr_q<=cur;
            if (l1_hit)      begin disp_slvl_q<=2'd0; disp_base_q<=l1_rdata; end  // leaf-table base
            else if (l2_hit) begin disp_slvl_q<=2'd1; disp_base_q<=l2_rdata; end  // L1-table base
            else             begin disp_slvl_q<=2'd2; disp_base_q<=root_ppn_q; end
          end else if (disp_ready) begin
            disp_valid_q<=1'b0; e_state[cur]<=ST_WALK; e_leader[cur]<=1'b1;
            cnt_walks<=cnt_walks+1'b1; lstate<=L_IDLE;
          end
        end
        default: lstate<=L_IDLE;
      endcase

      // walk completion: complete every coalesced entry on the line (per-page SPA)
      if (done_valid) begin
        for (int i=0;i<BUFFER_DEPTH;i++)
          if (e_state[i]==ST_WALK && e_line[i]==e_line[done_mshr] && e_ctx[i]==e_ctx[done_mshr]) begin
            e_spa[i] <= line_base + page_off(e_vpn[i]);
            e_state[i] <= ST_DONE;
          end
      end

      if (rsp_valid && rsp_ready) e_state[done_e_id]<=ST_FREE;
    end
  end

  always_comb begin
    automatic int unsigned occ=0;
    for (int i=0;i<BUFFER_DEPTH;i++) if (e_state[i]!=ST_FREE) occ++;
    buf_occupancy = 32'(occ);
  end
endmodule
