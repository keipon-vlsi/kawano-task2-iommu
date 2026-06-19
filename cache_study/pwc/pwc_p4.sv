// pwc_p4 -- speculative read. Predict the entry index from the tag LSB and read its SPA
// IMMEDIATELY (1-bit-indexed mux), in parallel with the tag compare that validates the
// hit. The SPA read does not wait for a priority/compare result. BET: predicted index is
// right (here = direct-mapped by LSB; correct for adjacent pairs). Fallback: compare
// mismatch -> hit=0 (correct miss). Datapath: spa = fast 1-bit mux; hit = parallel 18b compare.
module pwc_p4 (
  input  logic clk, rst_n,
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

  logic pred; assign pred = lk_tag[0];                 // predicted index
  assign lk_spa = spa_q[pred];                          // speculative read (no compare in path)
  assign lk_hit = v_q[pred] & (tag_q[pred] == lk_tag);  // late validate (parallel)

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin v_q[0]<=0; v_q[1]<=0; tag_q[0]<='0; tag_q[1]<='0; spa_q[0]<='0; spa_q[1]<='0; end
    else if (fill_en) begin                              // index by tag LSB (matches predictor)
      tag_q[fill_tag[0]] <= fill_tag; spa_q[fill_tag[0]] <= fill_spa; v_q[fill_tag[0]] <= 1'b1;
    end
  end
endmodule
