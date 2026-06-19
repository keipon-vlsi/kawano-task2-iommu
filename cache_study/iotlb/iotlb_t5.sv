// iotlb_t5 -- full 16-way fully-associative CAM (reference baseline). 16 tag comparators
// (27b each) + 16-wide OR(hit) + priority + 16:1 SPA mux. Round-robin fill. Robust to any
// access pattern (no contiguity/alignment assumption); the most flexible and the most
// expensive -- the cost reference the structured variants are measured against.
module iotlb_t5 (
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
  // priority mux (lowest index wins)
  always_comb begin
    lk_spa = '0;
    for (int i=15;i>=0;i--) if (match[i]) lk_spa = spa_q[i];
  end

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
