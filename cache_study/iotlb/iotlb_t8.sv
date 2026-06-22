// iotlb_t8 -- 16-way FA where the PRIORITY is baked into the MUX network as a serial
// 2:1-mux cascade (vs T5 which builds a separate priority encoder + balanced mux tree,
// and T6 which assumes one-hot and uses a flat AND-OR tree).
//   lk_spa = match[0] ? spa[0] : match[1] ? spa[1] : ... : match[15] ? spa[15] : 0
// i.e. a chain of 16 2:1 muxes, each selected by one match bit; index 0 has highest
// priority (it sits at the head of the cascade). Functionally identical to T5
// (lowest-index-wins), but the structure is a SERIAL mux chain rather than tree+encoder.
// Expected: deepest of the FA family (serial 2:1 cascade). Robust to any access pattern.
module iotlb_t8 (
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

  // priority baked into a SERIAL 2:1-mux cascade via explicit forward nesting: the index-0
  // mux is the output stage and its else-input is the index-1 mux result, whose else is
  // index-2's, ... = a 16-deep dependency chain (index 0 highest priority). This differs
  // structurally from T5's accumulator loop (which yosys maps to encoder + balanced tree).
  assign lk_spa =
    match[0]?spa_q[0]: match[1]?spa_q[1]: match[2]?spa_q[2]: match[3]?spa_q[3]:
    match[4]?spa_q[4]: match[5]?spa_q[5]: match[6]?spa_q[6]: match[7]?spa_q[7]:
    match[8]?spa_q[8]: match[9]?spa_q[9]: match[10]?spa_q[10]: match[11]?spa_q[11]:
    match[12]?spa_q[12]: match[13]?spa_q[13]: match[14]?spa_q[14]: match[15]?spa_q[15]:
    44'd0;

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
