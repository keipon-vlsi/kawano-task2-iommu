// iotlb_t1 -- aligned single-window. Treat the 16 entries as ONE aligned 16-page window:
// tag = VPN[26:4] (23b), index = VPN[3:0] (4b). Lookup = 1 high-bit compare + 16:1 SPA mux
// + per-page valid. BET on 16 contiguous, 16-aligned pages. Fallback: outside the window or
// misaligned -> miss (correct). Half the compares of T0 (1 vs 2) but a wider 16:1 mux.
module iotlb_t1 (
  input  logic clk, rst_n,
  input  logic [26:0] lk_tag,
  output logic        lk_hit,
  output logic [43:0] lk_spa,
  input  logic        fill_en,
  input  logic [26:0] fill_tag,
  input  logic [43:0] fill_spa
);
  logic [22:0] base_q;
  logic        lval_q;
  logic [15:0] subv_q;
  logic [43:0] data_q [16];

  logic [3:0]  idx; assign idx = lk_tag[3:0];
  logic [22:0] hi; assign hi = lk_tag[26:4];
  assign lk_hit = lval_q & (hi == base_q) & subv_q[idx];
  assign lk_spa = data_q[idx];

  logic [3:0]  fidx; assign fidx = fill_tag[3:0];
  logic [22:0] fhi; assign fhi = fill_tag[26:4];
  integer p;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin base_q<=0; lval_q<=0; subv_q<=0; for(p=0;p<16;p++) data_q[p]<=0; end
    else if (fill_en) begin
      if (lval_q & (fhi==base_q)) begin                 // same window: add page
        data_q[fidx] <= fill_spa; subv_q[fidx] <= 1'b1;
      end else begin                                     // new window
        base_q <= fhi; lval_q <= 1'b1; subv_q <= (16'b1 << fidx); data_q[fidx] <= fill_spa;
      end
    end
  end
endmodule
