// iotlb_t2 -- sequential pointer. 2 lines x 8, with a "current line" pointer marking the
// hot line; the stream is expected to hit it. The current line is compared with priority
// (its hit drives the SPA mux); a hit on the other line promotes it. BET on sequential
// streams. Fallback: a true miss returns hit=0 (correct). Worst case = 2 line compares
// (~T0); the offset already needs no compare, so the pointer mainly reorders replacement
// rather than cutting worst-case logic depth.
module iotlb_t2 (
  input  logic clk, rst_n,
  input  logic [26:0] lk_tag,
  output logic        lk_hit,
  output logic [43:0] lk_spa,
  input  logic        fill_en,
  input  logic [26:0] fill_tag,
  input  logic [43:0] fill_spa
);
  logic        cur_q;
  logic [23:0] ltag_q [2];
  logic        lval_q [2];
  logic [7:0]  subv_q [2];
  logic [43:0] data_q [2][8];

  logic [2:0]  lo; assign lo = lk_tag[2:0];
  logic [23:0] lt; assign lt = lk_tag[26:3];
  logic mc, mo;
  assign mc = lval_q[cur_q]  & (ltag_q[cur_q]  == lt) & subv_q[cur_q][lo];
  assign mo = lval_q[~cur_q] & (ltag_q[~cur_q] == lt) & subv_q[~cur_q][lo];
  assign lk_hit = mc | mo;
  assign lk_spa = mc ? data_q[cur_q][lo] : data_q[~cur_q][lo];

  logic [2:0]  fo; assign fo = fill_tag[2:0];
  logic [23:0] ft; assign ft = fill_tag[26:3];
  logic        same_cur; assign same_cur = lval_q[cur_q] & (ltag_q[cur_q]==ft);
  integer p;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cur_q<=0; lval_q[0]<=0; lval_q[1]<=0; subv_q[0]<=0; subv_q[1]<=0; ltag_q[0]<=0; ltag_q[1]<=0;
      for(p=0;p<8;p++) begin data_q[0][p]<=0; data_q[1][p]<=0; end
    end else if (fill_en) begin
      if (same_cur) begin                                  // extend current line
        data_q[cur_q][fo] <= fill_spa; subv_q[cur_q][fo] <= 1'b1;
      end else begin                                       // open new line in the other slot, promote
        ltag_q[~cur_q] <= ft; lval_q[~cur_q] <= 1'b1; subv_q[~cur_q] <= (8'b1<<fo);
        data_q[~cur_q][fo] <= fill_spa; cur_q <= ~cur_q;
      end
    end
  end
endmodule
