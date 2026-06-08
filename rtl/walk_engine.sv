// walk_engine.sv -- NUM_WALKERS concurrent Sv39 walk contexts + memory arbitration.
//
// Holds N `walker` FSMs (the concurrency that hides memory latency), arbitrates
// their tagged 64 B line reads onto the single memory IF, demuxes line responses
// back by tag, and arbitrates walker completions to the front-end. Fixed-priority
// arbitration (lowest index) for Phase 1; tag = walker id.
import iommu_pkg::*;

module walk_engine #(
  parameter int NUM_WALKERS = 4,
  parameter int TAG_W = (NUM_WALKERS < 2) ? 1 : $clog2(NUM_WALKERS),
  parameter int MSHR_W = 6
)(
  input  logic               clk,
  input  logic               rst_n,

  // --- dispatch (from front-end) ---
  input  logic               disp_valid,
  output logic               disp_ready,
  input  logic [VPN_W-1:0]   disp_vpn,
  input  logic [1:0]         disp_start_level,
  input  logic [PPN_W-1:0]   disp_base,
  input  logic [MSHR_W-1:0]  disp_mshr,

  // --- memory IF (single channel, 64 B line) ---
  output logic               mreq_valid,
  input  logic               mreq_ready,
  output logic [GPA_W-1:0]   mreq_addr,
  output logic [TAG_W-1:0]   mreq_tag,
  input  logic               mrsp_valid,
  input  logic [LINE_W-1:0]  mrsp_line,
  input  logic [TAG_W-1:0]   mrsp_tag,

  // --- completion (to front-end / MSHR) ---
  output logic               done_valid,
  input  logic               done_ready,
  output logic [MSHR_W-1:0]  done_mshr,
  output logic [VPN_W-1:0]   done_vpn,
  output logic [1:0]         done_start_level,
  output logic [SPA_W-1:0]   done_spa,
  output logic [PPN_W-1:0]   done_l1tab,
  output logic [PPN_W-1:0]   done_leaftab,
  output logic [LINE_W-1:0]  done_leafline,

  output logic [31:0]        active_walks_o
);
  logic [NUM_WALKERS-1:0]  w_disp_ready, w_busy, w_mreq_valid, w_done_valid;
  logic [GPA_W-1:0]        w_mreq_addr  [NUM_WALKERS];
  logic [MSHR_W-1:0]       w_done_mshr  [NUM_WALKERS];
  logic [VPN_W-1:0]        w_done_vpn   [NUM_WALKERS];
  logic [1:0]              w_done_slvl  [NUM_WALKERS];
  logic [SPA_W-1:0]        w_done_spa   [NUM_WALKERS];
  logic [PPN_W-1:0]        w_done_l1    [NUM_WALKERS];
  logic [PPN_W-1:0]        w_done_leaf  [NUM_WALKERS];
  logic [LINE_W-1:0]       w_done_line  [NUM_WALKERS];

  logic [TAG_W-1:0] free_id, mreq_id, done_id;
  logic any_free, any_mreq, any_done;
  always_comb begin
    any_free = 0; free_id = '0; any_mreq = 0; mreq_id = '0; any_done = 0; done_id = '0;
    for (int i = NUM_WALKERS-1; i >= 0; i--) begin
      if (w_disp_ready[i]) begin any_free = 1; free_id = TAG_W'(i); end
      if (w_mreq_valid[i]) begin any_mreq = 1; mreq_id = TAG_W'(i); end
      if (w_done_valid[i]) begin any_done = 1; done_id = TAG_W'(i); end
    end
  end

  assign disp_ready       = any_free;
  assign mreq_valid       = any_mreq;
  assign mreq_addr        = w_mreq_addr[mreq_id];
  assign mreq_tag         = mreq_id;
  assign done_valid       = any_done;
  assign done_mshr        = w_done_mshr[done_id];
  assign done_vpn         = w_done_vpn[done_id];
  assign done_start_level = w_done_slvl[done_id];
  assign done_spa         = w_done_spa[done_id];
  assign done_l1tab       = w_done_l1[done_id];
  assign done_leaftab     = w_done_leaf[done_id];
  assign done_leafline    = w_done_line[done_id];

  genvar i;
  generate
    for (i = 0; i < NUM_WALKERS; i++) begin : g_walk
      walker #(.TAG_W(TAG_W), .MSHR_W(MSHR_W)) u_w (
        .clk(clk), .rst_n(rst_n), .walker_id(TAG_W'(i)),
        .disp_valid (disp_valid & any_free & (free_id == TAG_W'(i))),
        .disp_ready (w_disp_ready[i]),
        .disp_vpn   (disp_vpn), .disp_start_level(disp_start_level),
        .disp_base  (disp_base), .disp_mshr(disp_mshr),
        .mreq_valid (w_mreq_valid[i]),
        .mreq_ready (mreq_ready & any_mreq & (mreq_id == TAG_W'(i))),
        .mreq_addr  (w_mreq_addr[i]), .mreq_tag(),
        .mrsp_valid (mrsp_valid & (mrsp_tag == TAG_W'(i))),
        .mrsp_line  (mrsp_line),
        .done_valid (w_done_valid[i]),
        .done_ready (done_ready & any_done & (done_id == TAG_W'(i))),
        .done_mshr  (w_done_mshr[i]), .done_vpn(w_done_vpn[i]),
        .done_start_level(w_done_slvl[i]), .done_spa(w_done_spa[i]),
        .done_l1tab (w_done_l1[i]), .done_leaftab(w_done_leaf[i]),
        .done_leafline(w_done_line[i]), .busy(w_busy[i]));
    end
  endgenerate

  always_comb begin
    automatic int unsigned cnt = 0;
    for (int k = 0; k < NUM_WALKERS; k++) if (w_busy[k]) cnt++;
    active_walks_o = 32'(cnt);
  end
endmodule
