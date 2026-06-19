// iotlb_t6 -- 16-way FA, but ASSUMES at most one entry matches (one-hot match). Because a
// VPN is cached at most once, the match vector is one-hot, so the priority encoder of T5
// is unnecessary: the SPA is a pure AND-OR one-hot mux  spa = OR_i (match[i] ? spa[i] : 0),
// a balanced OR tree (no serial priority chain) -> shallower / faster than T5.
// ASSUMPTION/BET: fills never create duplicate tags (true for a normal IOMMU: one VPN ->
// one entry). FALLBACK: if a duplicate ever occurred, the output would be the bitwise OR of
// the colliding SPAs (wrong) -- real designs prevent duplicate fills (fill checks/invalidates
// an existing tag first), which keeps the one-hot invariant. Same hit detection as T5.
module iotlb_t6 (
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
  // one-hot SPA mux: NO priority -- balanced AND-OR reduction over the 16 entries
  always_comb begin
    lk_spa = '0;
    for (int i=0;i<16;i++) lk_spa |= ({44{match[i]}} & spa_q[i]);
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
