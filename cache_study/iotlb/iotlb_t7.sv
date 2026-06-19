// iotlb_t7 -- double-buffered single current line (deterministic line predictor).
// Refinement: the lookup consults ONLY the "current" line; if it misses, it is a MISS --
// the other line is NEVER consulted on the lookup path. So the current line lives in its
// own flat registers (cur_*), and the lookup is just  1x 24b tag compare + 8:1 offset SPA
// mux  -- NO 2-line array index mux. The "next" line is a shadow (nxt_*) filled ahead
// (prefetch); when the offset wraps past page 7 the shadow is swapped in (cur <= nxt).
// BET: sequential IOVA (after a line's 8 pages, the stream is on the prefetched next line).
// FALLBACK: any access not in the current line misses (correct) -> re-walk. Compared to T0
// (compares both lines) and the array-indexed predictor, this keeps the lookup to a single
// 8-entry line, so the data mux is 8:1 (not 16:1) and there is no line-select mux.
module iotlb_t7 (
  input  logic clk, rst_n,
  input  logic [26:0] lk_tag,
  output logic        lk_hit,
  output logic [43:0] lk_spa,
  input  logic        fill_en,
  input  logic [26:0] fill_tag,
  input  logic [43:0] fill_spa
);
  // current line (the only thing looked up)
  logic [23:0] cur_tag_q;  logic cur_val_q;  logic [7:0] cur_subv_q;  logic [43:0] cur_data_q [8];
  // shadow / next line (prefetch target, swapped in on wrap)
  logic [23:0] nxt_tag_q;  logic nxt_val_q;  logic [7:0] nxt_subv_q;  logic [43:0] nxt_data_q [8];

  logic [2:0]  lo; assign lo = lk_tag[2:0];
  logic [23:0] lt; assign lt = lk_tag[26:3];
  // single-line lookup: 1 compare + 8:1 mux, no line-select mux
  assign lk_hit = cur_val_q & (cur_tag_q == lt) & cur_subv_q[lo];
  assign lk_spa = cur_data_q[lo];

  logic [2:0]  fo; assign fo = fill_tag[2:0];
  logic [23:0] ft; assign ft = fill_tag[26:3];
  logic        f_cur, f_nxt;
  assign f_cur = cur_val_q & (cur_tag_q == ft);            // fill belongs to current line
  assign f_nxt = ~f_cur;                                   // else it targets the shadow/next line
  integer p;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cur_val_q<=0; cur_tag_q<=0; cur_subv_q<=0; nxt_val_q<=0; nxt_tag_q<=0; nxt_subv_q<=0;
      for (p=0;p<8;p++) begin cur_data_q[p]<=0; nxt_data_q[p]<=0; end
    end else if (fill_en) begin
      if (f_cur) begin
        cur_subv_q[fo] <= 1'b1; cur_data_q[fo] <= fill_spa;
      end else begin                                        // (re)open the shadow line
        if (!nxt_val_q || nxt_tag_q != ft) begin            // new shadow line: clear then set
          nxt_tag_q <= ft; nxt_val_q <= 1'b1; nxt_subv_q <= (8'b1<<fo);
          for (p=0;p<8;p++) nxt_data_q[p] <= (p==fo) ? fill_spa : 44'd0;
        end else begin
          nxt_subv_q[fo] <= 1'b1; nxt_data_q[fo] <= fill_spa;
        end
        if (!cur_val_q) begin                               // bootstrap: first line becomes current
          cur_tag_q <= ft; cur_val_q <= 1'b1; cur_subv_q <= (8'b1<<fo);
          for (p=0;p<8;p++) cur_data_q[p] <= (p==fo) ? fill_spa : 44'd0;
          nxt_val_q <= 1'b0;
        end
      end
    end else if (lo == 3'd7) begin
      // offset wrap (deterministic predictor advances by access count, NOT by the tag
      // compare -- keeps the 24b compare off the cur_data register update path): swap the
      // prefetched shadow in as current. A misprediction just misses next (correct).
      cur_tag_q <= nxt_tag_q; cur_val_q <= nxt_val_q; cur_subv_q <= nxt_subv_q;
      for (p=0;p<8;p++) cur_data_q[p] <= nxt_data_q[p];
    end
  end
endmodule
