// iotlb_t3 -- speculative read + parallel validate. The 16 entries are read by the address
// index VPN[3:0] IMMEDIATELY (16:1 SPA mux, no compare in the SPA path); the window tag
// (VPN[26:4]) is validated in parallel to produce hit. So the SPA read does not wait for
// the tag compare. BET on the aligned 16-page window (predicted index correct). Fallback:
// tag mismatch -> hit=0 (correct miss). Differs from T1 in that SPA is not gated by the
// compare (read-then-validate).
module iotlb_t3 (
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
  assign lk_spa = data_q[idx];                               // speculative read (index only)
  assign lk_hit = lval_q & (lk_tag[26:4] == base_q) & subv_q[idx];  // validate in parallel

  logic [3:0]  fidx; assign fidx = fill_tag[3:0];
  logic [22:0] fhi; assign fhi = fill_tag[26:4];
  integer p;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin base_q<=0; lval_q<=0; subv_q<=0; for(p=0;p<16;p++) data_q[p]<=0; end
    else if (fill_en) begin
      if (lval_q & (fhi==base_q)) begin data_q[fidx]<=fill_spa; subv_q[fidx]<=1'b1; end
      else begin base_q<=fhi; lval_q<=1'b1; subv_q<=(16'b1<<fidx); data_q[fidx]<=fill_spa; end
    end
  end
endmodule
