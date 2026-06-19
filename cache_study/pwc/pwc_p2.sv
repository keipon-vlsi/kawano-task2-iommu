// pwc_p2 -- even-aligned window. Store base high bits = tag[17:1]; the LSB tag[0]
// DIRECTLY selects the entry (no subtractor). Hit = (tag[17:1]==baseHi) & valid[LSB].
// BET on aligned adjacent pairs (even base). Fallback: misaligned/out-of-window misses.
// Lookup datapath: 17b compare + LSB-indexed 2:1 SPA mux (shallower than P1, no adder).
module pwc_p2 (
  input  logic clk, rst_n,
  input  logic [17:0] lk_tag,
  output logic        lk_hit,
  output logic [43:0] lk_spa,
  input  logic        fill_en,
  input  logic [17:0] fill_tag,
  input  logic [43:0] fill_spa
);
  logic [16:0] baseHi_q;
  logic        v_q   [2];
  logic [43:0] spa_q [2];

  logic sel; assign sel = lk_tag[0];
  assign lk_hit = v_q[sel] & (lk_tag[17:1] == baseHi_q);
  assign lk_spa = spa_q[sel];

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin baseHi_q<='0; v_q[0]<=0; v_q[1]<=0; spa_q[0]<='0; spa_q[1]<='0; end
    else if (fill_en) begin
      if (fill_tag[17:1] == baseHi_q) begin                 // same window: set one entry
        spa_q[fill_tag[0]] <= fill_spa; v_q[fill_tag[0]] <= 1'b1;
      end else begin                                         // new window: set one, clear other
        baseHi_q <= fill_tag[17:1];
        spa_q[fill_tag[0]] <= fill_spa; v_q[fill_tag[0]] <= 1'b1; v_q[~fill_tag[0]] <= 1'b0;
      end
    end
  end
endmodule
