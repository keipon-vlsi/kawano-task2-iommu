// iotlb_t7 -- 2 lines x 8 with a DETERMINISTIC line predictor. With coalescing a line is
// hit 8 times (offset 0..7) in a row; for sequential IOVA the access right after offset 7
// is GUARANTEED to be the other line. So a 1-bit "current line" register predicts which
// line to look at: the lookup reads data[cur][offset] (SPA mux is index-driven by the
// register + offset -- it does NOT wait for a tag compare), and validates with ONE tag
// compare in parallel. The predictor flips to the other line when offset wraps (after the
// last page of the current line). vs T0/T2 (compare BOTH lines): only one line is consulted
// and the SPA read is off the compare path.
// BET: sequential IOVA (the next line is the predicted one). Unlike T1/T3 (single 16-aligned
// window), T7 keeps 2 INDEPENDENT line tags, so the two cached lines need not be 16-aligned.
// FALLBACK: a misprediction (out-of-order / non-sequential) sees the wrong line -> miss
// (correct), forcing a re-walk. Lookup datapath: 2:1 (cur) line-tag mux -> 1x 24b compare
// (parallel) + (cur,offset)-indexed 16:1 SPA mux.
module iotlb_t7 (
  input  logic clk, rst_n,
  input  logic [26:0] lk_tag,
  output logic        lk_hit,
  output logic [43:0] lk_spa,
  input  logic        fill_en,
  input  logic [26:0] fill_tag,
  input  logic [43:0] fill_spa
);
  logic [23:0] ltag_q [2];
  logic        lval_q [2];
  logic [7:0]  subv_q [2];
  logic [43:0] data_q [2][8];
  logic        cur_q;                                   // predicted current line
  logic        vptr_q;                                  // fill victim

  logic [2:0]  lo; assign lo = lk_tag[2:0];
  logic [23:0] lt; assign lt = lk_tag[26:3];
  // consult ONLY the predicted line: validate with one compare; SPA is index-driven (no compare gate)
  assign lk_hit = lval_q[cur_q] & (ltag_q[cur_q] == lt) & subv_q[cur_q][lo];
  assign lk_spa = data_q[cur_q][lo];

  logic [2:0]  fo; assign fo = fill_tag[2:0];
  logic [23:0] ft; assign ft = fill_tag[26:3];
  logic        fe0, fe1, fslot;
  assign fe0 = lval_q[0] & (ltag_q[0]==ft);
  assign fe1 = lval_q[1] & (ltag_q[1]==ft);
  assign fslot = fe1 ? 1'b1 : (fe0 ? 1'b0 : vptr_q);
  integer p;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      lval_q[0]<=0; lval_q[1]<=0; subv_q[0]<=0; subv_q[1]<=0; ltag_q[0]<=0; ltag_q[1]<=0;
      cur_q<=0; vptr_q<=0; for(p=0;p<8;p++) begin data_q[0][p]<=0; data_q[1][p]<=0; end
    end else if (fill_en) begin
      if (fe0 | fe1) begin data_q[fslot][fo] <= fill_spa; subv_q[fslot][fo] <= 1'b1; end
      else begin
        ltag_q[vptr_q] <= ft; lval_q[vptr_q] <= 1'b1; subv_q[vptr_q] <= (8'b1<<fo);
        data_q[vptr_q][fo] <= fill_spa; vptr_q <= ~vptr_q;
      end
    end else if (lk_hit & (lo == 3'd7)) begin
      cur_q <= ~cur_q;                                   // finished current line -> predict the other
    end
  end
endmodule
