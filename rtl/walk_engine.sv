// walk_engine.sv -- NUM_WALKERS concurrent walk contexts + memory arbitration.
//
// Holds N `walker` FSMs (the concurrency that hides memory latency). It:
//   * dispatches each incoming walk to a free walker,
//   * arbitrates the walkers' tagged memory requests onto the single memory IF,
//   * demuxes tagged R responses back to the originating walker,
//   * arbitrates walker completions back to the front-end.
// Arbitration is fixed-priority (lowest index) for Phase 1; round-robin is a
// later refinement. Tag = walker id, so MEM_MAX_OUTSTANDING bounds N usefully.
import iommu_pkg::*;

module walk_engine #(
  parameter int NUM_WALKERS = 4,
  parameter int TAG_W = (NUM_WALKERS < 2) ? 1 : $clog2(NUM_WALKERS),
  parameter int ADDR_W = GPA_W,
  parameter int DATA_W = PPN_W,
  parameter int MSHR_W = 6,
  parameter int MAXRD_W = 4
)(
  input  logic              clk,
  input  logic              rst_n,

  // --- dispatch (from front-end) ---
  input  logic              disp_valid,
  output logic              disp_ready,
  input  logic [VPN_W-1:0]  disp_vpn,
  input  logic [MAXRD_W-1:0]disp_nreads,
  input  logic [MSHR_W-1:0] disp_mshr,

  // --- memory IF (single channel) ---
  output logic              mreq_valid,
  input  logic              mreq_ready,
  output logic [ADDR_W-1:0] mreq_addr,
  output logic [TAG_W-1:0] mreq_tag,
  input  logic              mrsp_valid,
  input  logic [DATA_W-1:0] mrsp_data,
  input  logic [TAG_W-1:0] mrsp_tag,

  // --- completion (to front-end / MSHR) ---
  output logic              done_valid,
  input  logic              done_ready,
  output logic [MSHR_W-1:0] done_mshr,
  output logic [SPA_W-1:0]  done_spa,

  output logic [31:0]       active_walks_o
);
  // per-walker buses
  logic [NUM_WALKERS-1:0]          w_disp_ready, w_busy;
  logic [NUM_WALKERS-1:0]          w_mreq_valid, w_mreq_ready, w_mrsp_valid;
  logic [ADDR_W-1:0]              w_mreq_addr [NUM_WALKERS];
  logic [NUM_WALKERS-1:0]          w_done_valid, w_done_ready;
  logic [MSHR_W-1:0]             w_done_mshr [NUM_WALKERS];
  logic [SPA_W-1:0]              w_done_spa  [NUM_WALKERS];

  // --- fixed-priority selects ---
  // free walker for dispatch
  logic [TAG_W-1:0] free_id; logic any_free;
  // memory request grant
  logic [TAG_W-1:0] mreq_id; logic any_mreq;
  // completion grant
  logic [TAG_W-1:0] done_id; logic any_done;

  always_comb begin
    any_free = 1'b0; free_id = '0;
    any_mreq = 1'b0; mreq_id = '0;
    any_done = 1'b0; done_id = '0;
    for (int i = NUM_WALKERS-1; i >= 0; i--) begin
      if (w_disp_ready[i]) begin any_free = 1'b1; free_id = TAG_W'(i); end
      if (w_mreq_valid[i]) begin any_mreq = 1'b1; mreq_id = TAG_W'(i); end
      if (w_done_valid[i]) begin any_done = 1'b1; done_id = TAG_W'(i); end
    end
  end

  // dispatch fan-out
  assign disp_ready = any_free;

  // memory request mux
  assign mreq_valid = any_mreq;
  assign mreq_addr  = w_mreq_addr[mreq_id];
  assign mreq_tag   = mreq_id;

  // completion mux
  assign done_valid = any_done;
  assign done_mshr  = w_done_mshr[done_id];
  assign done_spa   = w_done_spa[done_id];

  genvar i;
  generate
    for (i = 0; i < NUM_WALKERS; i++) begin : g_walk
      walker #(.ADDR_W(ADDR_W), .DATA_W(DATA_W), .TAG_W(TAG_W),
               .MSHR_W(MSHR_W), .MAXRD_W(MAXRD_W)) u_w (
        .clk(clk), .rst_n(rst_n), .walker_id(TAG_W'(i)),
        .disp_valid (disp_valid & any_free & (free_id == TAG_W'(i))),
        .disp_ready (w_disp_ready[i]),
        .disp_vpn   (disp_vpn),
        .disp_nreads(disp_nreads),
        .disp_mshr  (disp_mshr),
        .mreq_valid (w_mreq_valid[i]),
        .mreq_ready (mreq_ready & any_mreq & (mreq_id == TAG_W'(i))),
        .mreq_addr  (w_mreq_addr[i]),
        .mreq_tag   (/* set by engine mux */),
        .mrsp_valid (mrsp_valid & (mrsp_tag == TAG_W'(i))),
        .mrsp_data  (mrsp_data),
        .done_valid (w_done_valid[i]),
        .done_ready (done_ready & any_done & (done_id == TAG_W'(i))),
        .done_mshr  (w_done_mshr[i]),
        .done_spa   (w_done_spa[i]),
        .busy       (w_busy[i])
      );
    end
  endgenerate

  // active walk count (observability / 3c peak_walks)
  always_comb begin
    automatic int unsigned cnt = 0;
    for (int k = 0; k < NUM_WALKERS; k++) if (w_busy[k]) cnt++;
    active_walks_o = 32'(cnt);
  end
endmodule
