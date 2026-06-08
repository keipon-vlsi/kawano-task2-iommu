// txn_buffer.sv -- IOMMU front-end: transaction buffer + MSHR + cache lookup.
//
// Owns the translation caches (instances of cache_store: combined IOTLB + S1 PWC
// + optional S2 PWC + DDT$/PDT$). Per request:
//   1. allocate a BUFFER_DEPTH buffer entry (control state only; no 4 kB payload),
//   2. IOTLB lookup (line-keyed, coalescing-filled) -> hit completes immediately,
//   3. on miss, MSHR-coalesce against any in-flight entry for the same line+ctx
//      (the buffer IS the MSHR: same-line entries share the one dispatched walk),
//   4. else probe the PWC, compute the residual read count and dispatch a walk.
// On walk completion the IOTLB line is filled and every coalesced entry completes.
//
// Happy-path / steady-state only: context (DDT$/PDT$/root) is pre-loaded; no faults.
import iommu_pkg::*;

module txn_buffer #(
  parameter int MODE            = MODE_S1_ONLY,
  parameter int COALESCE_FACTOR = 8,
  parameter int BUFFER_DEPTH    = 16,
  parameter int PREFETCH_EN     = 0,
  parameter int CLOCK_GATING_EN = 0,        // (synth knob; per-entry WE already used)
  // caches
  parameter int IOTLB_ENTRIES = 64, parameter int IOTLB_ASSOC = 4,  parameter int IOTLB_STORAGE = ST_SRAM,
  parameter int S1PWC_ENTRIES = 16, parameter int S1PWC_ASSOC = 16, parameter int S1PWC_STORAGE = ST_FF,
  parameter int S2PWC_ENTRIES = 16, parameter int S2PWC_ASSOC = 16, parameter int S2PWC_STORAGE = ST_FF,
  parameter int DDTC_ENTRIES  = 4,  parameter int PDTC_ENTRIES  = 4,
  // derived (do not override)
  parameter int MSHR_W      = (BUFFER_DEPTH < 2) ? 1 : $clog2(BUFFER_DEPTH),
  parameter int MAXRD_W     = 4,
  parameter int IOTLB_KEY_W = CTX_W + VPN_W
)(
  input  logic              clk,
  input  logic              rst_n,

  // --- request in ---
  input  logic              req_valid,
  output logic              req_ready,
  input  req_t              req,

  // --- response out (translation complete) ---
  output logic              rsp_valid,
  input  logic              rsp_ready,
  output logic [SPA_W-1:0]  rsp_spa,
  output logic [MSHR_W-1:0] rsp_tag,

  // --- walk dispatch / completion (to walk_engine) ---
  output logic              disp_valid,
  input  logic              disp_ready,
  output logic [VPN_W-1:0]  disp_vpn,
  output logic [MAXRD_W-1:0]disp_nreads,
  output logic [MSHR_W-1:0] disp_mshr,

  input  logic              done_valid,
  output logic              done_ready,
  input  logic [MSHR_W-1:0] done_mshr,
  input  logic [SPA_W-1:0]  done_spa,

  // --- preload (testbench warms caches for steady state) ---
  input  logic              pl_valid,
  input  logic [2:0]        pl_sel,    // 0=iotlb 1=s1pwc 2=s2pwc 3=ddtc 4=pdtc
  input  logic [IOTLB_KEY_W-1:0] pl_key,
  input  logic [SPA_W-1:0]  pl_data,

  // --- observability (sim<->RTL cross-check) ---
  output logic [31:0]       cnt_iotlb_hit,
  output logic [31:0]       cnt_coalesced,
  output logic [31:0]       cnt_walks,
  output logic [31:0]       buf_occupancy
);
  // ---- key geometry ----
  localparam int CW          = (COALESCE_FACTOR < 2) ? 0 : $clog2(COALESCE_FACTOR);
  localparam int LINE_W      = VPN_W;                       // line id reuses VPN width
  localparam int PWC_KEY_W   = CTX_W + VPN_W;

  function automatic logic [LINE_W-1:0] line_of(input logic [VPN_W-1:0] v);
    return LINE_W'(v >> CW);
  endfunction
  function automatic logic [IOTLB_KEY_W-1:0] iotlb_key(input logic [VPN_W-1:0] v, input logic [CTX_W-1:0] c);
    return {c, line_of(v)};
  endfunction
  function automatic logic [PWC_KEY_W-1:0] pwc_key(input logic [VPN_W-1:0] v, input logic [CTX_W-1:0] c);
    return {c, {9'b0, v[VPN_W-1:9]}};                       // upper level: per ~2 MB region
  endfunction

  // ---- buffer ----
  localparam logic [1:0] ST_FREE = 2'd0, ST_LOOK = 2'd1, ST_WALK = 2'd2, ST_DONE = 2'd3;
  logic [1:0]        e_state [BUFFER_DEPTH];
  logic [VPN_W-1:0]  e_vpn   [BUFFER_DEPTH];
  logic [CTX_W-1:0]  e_ctx   [BUFFER_DEPTH];
  logic [LINE_W-1:0] e_line  [BUFFER_DEPTH];
  logic              e_leader[BUFFER_DEPTH];               // dispatched the walk
  logic [SPA_W-1:0]  e_spa   [BUFFER_DEPTH];

  // ---- cache instances ----
  logic              iotlb_lk, iotlb_hit, iotlb_fill;
  logic [IOTLB_KEY_W-1:0] iotlb_lk_key, iotlb_fill_key;
  logic [SPA_W-1:0]  iotlb_rdata, iotlb_fill_data;
  cache_store #(.ENTRIES(IOTLB_ENTRIES), .ASSOC(IOTLB_ASSOC), .KEY_W(IOTLB_KEY_W),
                .DATA_W(SPA_W), .STORAGE(IOTLB_STORAGE)) u_iotlb (
    .clk(clk), .rst_n(rst_n), .lookup_en(iotlb_lk), .lookup_key(iotlb_lk_key),
    .hit(iotlb_hit), .rdata(iotlb_rdata),
    .fill_en(iotlb_fill), .fill_key(iotlb_fill_key), .fill_data(iotlb_fill_data),
    .inval_all(1'b0));

  logic              pwc_lk, pwc_hit, pwc_fill;
  logic [PWC_KEY_W-1:0] pwc_lk_key, pwc_fill_key;
  cache_store #(.ENTRIES(S1PWC_ENTRIES), .ASSOC(S1PWC_ASSOC), .KEY_W(PWC_KEY_W),
                .DATA_W(PPN_W), .STORAGE(S1PWC_STORAGE)) u_s1pwc (
    .clk(clk), .rst_n(rst_n), .lookup_en(pwc_lk), .lookup_key(pwc_lk_key),
    .hit(pwc_hit), .rdata(),
    .fill_en(pwc_fill), .fill_key(pwc_fill_key), .fill_data('0), .inval_all(1'b0));

  // S2 PWC + DDT$/PDT$ are instantiated for area completeness; in the happy path
  // the pre-loaded context always hits, so they do not add residual reads here.
  logic s2_lk, s2_hit, s2_fill;
  logic [PWC_KEY_W-1:0] s2_key;
  cache_store #(.ENTRIES(S2PWC_ENTRIES), .ASSOC(S2PWC_ASSOC), .KEY_W(PWC_KEY_W),
                .DATA_W(PPN_W), .STORAGE(S2PWC_STORAGE)) u_s2pwc (
    .clk(clk), .rst_n(rst_n), .lookup_en(s2_lk), .lookup_key(s2_key),
    .hit(s2_hit), .rdata(), .fill_en(s2_fill), .fill_key(s2_key), .fill_data('0), .inval_all(1'b0));
  assign s2_lk = 1'b0; assign s2_fill = (pl_valid && pl_sel == 3'd2);
  assign s2_key = pl_key[PWC_KEY_W-1:0];   // S2 PWC exercised only via preload (area)

  // ---- preload muxing into IOTLB / PWC ----
  // (walk completion & prefetch also fill IOTLB; preload has priority pre-traffic)
  logic              wb_iotlb_fill;  logic [IOTLB_KEY_W-1:0] wb_iotlb_key; logic [SPA_W-1:0] wb_iotlb_data;
  logic              wb_pwc_fill;    logic [PWC_KEY_W-1:0]   wb_pwc_key;
  assign iotlb_fill      = (pl_valid && pl_sel == 3'd0) ? 1'b1            : wb_iotlb_fill;
  assign iotlb_fill_key  = (pl_valid && pl_sel == 3'd0) ? pl_key          : wb_iotlb_key;
  assign iotlb_fill_data = (pl_valid && pl_sel == 3'd0) ? pl_data         : wb_iotlb_data;
  assign pwc_fill        = (pl_valid && pl_sel == 3'd1) ? 1'b1            : wb_pwc_fill;
  assign pwc_fill_key    = (pl_valid && pl_sel == 3'd1) ? pl_key[PWC_KEY_W-1:0] : wb_pwc_key;

  // ---- lookup FSM ----
  typedef enum logic [1:0] {L_IDLE, L_IOTLB, L_PWC} lstate_e;
  lstate_e lstate;
  logic [MSHR_W-1:0] cur;          // entry under lookup

  // pick an entry in ST_LOOK (fixed priority)
  logic any_look; logic [MSHR_W-1:0] look_id;
  // free slot for new request
  logic any_free; logic [MSHR_W-1:0] free_id;
  // a ST_DONE entry to respond
  logic any_done_e; logic [MSHR_W-1:0] done_e_id;
  always_comb begin
    any_look=0; look_id='0; any_free=0; free_id='0; any_done_e=0; done_e_id='0;
    for (int i = BUFFER_DEPTH-1; i >= 0; i--) begin
      if (e_state[i]==ST_LOOK) begin any_look=1; look_id=MSHR_W'(i); end
      if (e_state[i]==ST_FREE) begin any_free=1; free_id=MSHR_W'(i); end
      if (e_state[i]==ST_DONE) begin any_done_e=1; done_e_id=MSHR_W'(i); end
    end
  end

  // coalesce check: is there an in-flight (ST_WALK) entry for cur's line+ctx?
  logic coalesce_hit;
  always_comb begin
    coalesce_hit = 1'b0;
    for (int i = 0; i < BUFFER_DEPTH; i++)
      if (e_state[i]==ST_WALK && e_line[i]==e_line[cur] && e_ctx[i]==e_ctx[cur])
        coalesce_hit = 1'b1;
  end

  // residual read count: steady (PWC warm) vs cold; nested ~2x single
  function automatic logic [MAXRD_W-1:0] nreads(input logic pwc_h);
    if (MODE == MODE_NESTED) return pwc_h ? MAXRD_W'(2) : MAXRD_W'(15);
    else                     return pwc_h ? MAXRD_W'(1) : MAXRD_W'(3);
  endfunction

  // accept a new request into a free slot
  assign req_ready = any_free;
  // response from a ST_DONE entry
  assign rsp_valid = any_done_e;
  assign rsp_spa   = e_spa[done_e_id];
  assign rsp_tag   = done_e_id;

  // dispatch outputs (registered in FSM)
  logic disp_valid_q; logic [VPN_W-1:0] disp_vpn_q; logic [MAXRD_W-1:0] disp_nreads_q; logic [MSHR_W-1:0] disp_mshr_q;
  assign disp_valid  = disp_valid_q;
  assign disp_vpn    = disp_vpn_q;
  assign disp_nreads = disp_nreads_q;
  assign disp_mshr   = disp_mshr_q;
  assign done_ready  = 1'b1;       // completion always accepted

  // IOTLB/PWC lookup port drive
  assign iotlb_lk     = (lstate==L_IDLE) && any_look;
  assign iotlb_lk_key = iotlb_key(e_vpn[look_id], e_ctx[look_id]);
  assign pwc_lk       = (lstate==L_IOTLB) && !iotlb_hit && !coalesce_hit;
  assign pwc_lk_key   = pwc_key(e_vpn[cur], e_ctx[cur]);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int i=0;i<BUFFER_DEPTH;i++) begin e_state[i]<=ST_FREE; e_leader[i]<=1'b0; end
      lstate<=L_IDLE; cur<='0;
      disp_valid_q<=1'b0; disp_vpn_q<='0; disp_nreads_q<='0; disp_mshr_q<='0;
      wb_iotlb_fill<=1'b0; wb_pwc_fill<=1'b0;
      cnt_iotlb_hit<='0; cnt_coalesced<='0; cnt_walks<='0;
    end else begin
      wb_iotlb_fill<=1'b0; wb_pwc_fill<=1'b0;

      // (1) accept new request
      if (req_valid && req_ready) begin
        e_state[free_id] <= ST_LOOK;
        e_vpn[free_id]   <= req.vpn;
        e_ctx[free_id]   <= ctx_of(req);
        e_line[free_id]  <= line_of(req.vpn);
        e_leader[free_id]<= 1'b0;
      end

      // (2) lookup FSM
      case (lstate)
        L_IDLE: if (any_look) begin cur <= look_id; lstate <= L_IOTLB; end
        L_IOTLB: begin
          if (iotlb_hit) begin
            e_spa[cur] <= iotlb_rdata; e_state[cur] <= ST_DONE;
            cnt_iotlb_hit <= cnt_iotlb_hit + 1'b1; lstate <= L_IDLE;
          end else if (coalesce_hit) begin
            e_state[cur] <= ST_WALK;                          // MSHR-coalesced (follower)
            cnt_coalesced <= cnt_coalesced + 1'b1; lstate <= L_IDLE;
          end else begin
            lstate <= L_PWC;                                  // probe PWC (result next cyc)
          end
        end
        L_PWC: begin
          // dispatch a walk (leader)
          if (!disp_valid_q) begin
            disp_valid_q <= 1'b1; disp_vpn_q <= e_vpn[cur];
            disp_nreads_q <= nreads(pwc_hit); disp_mshr_q <= cur;
          end else if (disp_ready) begin
            disp_valid_q <= 1'b0;
            e_state[cur] <= ST_WALK; e_leader[cur] <= 1'b1;
            cnt_walks <= cnt_walks + 1'b1;
            wb_pwc_fill <= 1'b1; wb_pwc_key <= pwc_key(e_vpn[cur], e_ctx[cur]);  // warm PWC
            lstate <= L_IDLE;
          end
        end
        default: lstate <= L_IDLE;
      endcase

      // (3) walk completion: fill IOTLB + complete all coalesced entries on the line
      if (done_valid) begin
        wb_iotlb_fill <= 1'b1;
        wb_iotlb_key  <= {e_ctx[done_mshr], e_line[done_mshr]};
        wb_iotlb_data <= done_spa;
        for (int i=0;i<BUFFER_DEPTH;i++)
          if (e_state[i]==ST_WALK && e_line[i]==e_line[done_mshr] && e_ctx[i]==e_ctx[done_mshr]) begin
            e_spa[i] <= done_spa; e_state[i] <= ST_DONE;
          end
      end

      // (4) drain a completed entry to the response port
      if (rsp_valid && rsp_ready) e_state[done_e_id] <= ST_FREE;
    end
  end

  // occupancy
  always_comb begin
    automatic int unsigned occ = 0;
    for (int i=0;i<BUFFER_DEPTH;i++) if (e_state[i]!=ST_FREE) occ++;
    buf_occupancy = 32'(occ);
  end
endmodule
