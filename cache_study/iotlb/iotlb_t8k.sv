// iotlb_t8k -- like T8 (16-way FA, priority baked into a serial 2:1-mux cascade) but with
// the cascade intermediate nodes marked (* keep *) so the synthesizer CANNOT rebalance the
// chain into a tree. This forces the literal 16-deep 2:1-mux cascade to be generated, to
// measure its PPA (vs T8 where abc collapsed it to ~11 levels). Same storage/compares as T5/T8.
module iotlb_t8k (
  input  logic clk, rst_n,
  input  logic [26:0] lk_tag,
  output logic        lk_hit,
  output logic [43:0] lk_spa,
  input  logic        fill_en,
  input  logic [26:0] fill_tag,
  input  logic [43:0] fill_spa
);
  logic        v_q   [16];
  logic [26:0] tag_q [16];
  logic [43:0] spa_q [16];
  logic [3:0]  vptr_q;

  logic [15:0] match;
  always_comb for (int i=0;i<16;i++) match[i] = v_q[i] & (tag_q[i]==lk_tag);
  assign lk_hit = |match;

  // explicit 16-stage 2:1-mux cascade; (* keep *) prevents abc from rebalancing it.
  // r[i] = match[i] ? spa[i] : r[i+1]   (index 0 highest priority, at the output)
  (* keep *) logic [43:0] r [17];
  assign r[16] = 44'd0;
  genvar g;
  generate for (g=15; g>=0; g--) begin : CHAIN
    assign r[g] = match[g] ? spa_q[g] : r[g+1];
  end endgenerate
  assign lk_spa = r[0];

  integer k;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (k=0;k<16;k++) begin v_q[k]<=0; tag_q[k]<=0; spa_q[k]<=0; end
      vptr_q<=0;
    end else if (fill_en) begin
      v_q[vptr_q]<=1'b1; tag_q[vptr_q]<=fill_tag; spa_q[vptr_q]<=fill_spa; vptr_q<=vptr_q+4'd1;
    end
  end
endmodule
