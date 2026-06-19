// pwc_p1 -- base + delta. Store one base tag + 2 SPAs. Hit if (lk_tag - base) in {0,1};
// the delta LSB selects the entry. BET on contiguous IOVA (adjacent pair). Fallback: a
// tag outside the window misses (correct), forcing a refill -- never wrong, just slower.
// Lookup datapath: 18b subtractor + 17b zero-detect + 2:1 SPA mux.
module pwc_p1 (
  input  logic clk, rst_n,
  input  logic [17:0] lk_tag,
  output logic        lk_hit,
  output logic [43:0] lk_spa,
  input  logic        fill_en,
  input  logic [17:0] fill_tag,
  input  logic [43:0] fill_spa
);
  logic [17:0] base_q;
  logic        v_q   [2];
  logic [43:0] spa_q [2];

  logic [17:0] d;  logic sel, inwin;
  assign d     = lk_tag - base_q;            // subtractor
  assign inwin = (d[17:1] == 17'd0);         // |delta| <= 1
  assign sel   = d[0];
  assign lk_hit = inwin & v_q[sel];
  assign lk_spa = spa_q[sel];

  logic [17:0] fd; assign fd = fill_tag - base_q;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin base_q<='0; v_q[0]<=0; v_q[1]<=0; spa_q[0]<='0; spa_q[1]<='0; end
    else if (fill_en) begin
      if (fd[17:1] == 17'd0) begin           // inside current window: place by delta LSB
        spa_q[fd[0]] <= fill_spa; v_q[fd[0]] <= 1'b1;
      end else begin                          // slide the window to a new base
        base_q <= fill_tag; spa_q[0] <= fill_spa; v_q[0] <= 1'b1; v_q[1] <= 1'b0;
      end
    end
  end
endmodule
