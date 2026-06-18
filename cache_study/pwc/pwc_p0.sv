// pwc_p0 -- PWC baseline: 2-way fully-associative.
// Lookup = 2 tag comparators (18b) + OR(hit) + priority 2:1 SPA mux.
// Fill   = round-robin victim pointer. Robust (any tag); reference for PWC.
module pwc_p0 (
  input  logic        clk,
  input  logic        rst_n,
  input  logic [17:0] lk_tag,
  output logic        lk_hit,
  output logic [43:0] lk_spa,
  input  logic        fill_en,
  input  logic [17:0] fill_tag,
  input  logic [43:0] fill_spa
);
  logic        v_q   [2];
  logic [17:0] tag_q [2];
  logic [43:0] spa_q [2];
  logic        vptr_q;                  // round-robin victim

  // ---- lookup: parallel compare + priority mux ----
  logic [1:0] match;
  always_comb begin
    match[0] = v_q[0] & (tag_q[0] == lk_tag);
    match[1] = v_q[1] & (tag_q[1] == lk_tag);
  end
  assign lk_hit = |match;
  assign lk_spa = match[0] ? spa_q[0] : spa_q[1];   // entry0 priority

  // ---- fill: round-robin ----
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      v_q[0] <= 1'b0; v_q[1] <= 1'b0;
      tag_q[0] <= '0; tag_q[1] <= '0; spa_q[0] <= '0; spa_q[1] <= '0; vptr_q <= 1'b0;
    end else if (fill_en) begin
      v_q[vptr_q]   <= 1'b1;
      tag_q[vptr_q] <= fill_tag;
      spa_q[vptr_q] <= fill_spa;
      vptr_q        <= ~vptr_q;
    end
  end
endmodule
