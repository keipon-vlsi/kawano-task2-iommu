// iotlb_t0 -- baseline (current design): line-organized 2 lines x 8 pages.
// VPN -> {line_tag = VPN[26:3] (24b), offset = VPN[2:0] (3b)}. Lookup = 2 line-tag
// compares + offset-indexed 8:1 SPA mux + per-page valid. Fill per page (match line or
// allocate round-robin victim). Robust to any line; offset never needs a compare.
module iotlb_t0 (
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
  logic        vptr_q;

  logic [2:0]  lo; assign lo = lk_tag[2:0];
  logic [23:0] lt; assign lt = lk_tag[26:3];
  logic m0, m1;
  assign m0 = lval_q[0] & (ltag_q[0]==lt) & subv_q[0][lo];
  assign m1 = lval_q[1] & (ltag_q[1]==lt) & subv_q[1][lo];
  assign lk_hit = m0 | m1;
  assign lk_spa = m0 ? data_q[0][lo] : data_q[1][lo];

  logic [2:0]  fo; assign fo = fill_tag[2:0];
  logic [23:0] ft; assign ft = fill_tag[26:3];
  logic        fe0, fe1, fslot;
  assign fe0 = lval_q[0] & (ltag_q[0]==ft);
  assign fe1 = lval_q[1] & (ltag_q[1]==ft);
  assign fslot = fe1 ? 1'b1 : (fe0 ? 1'b0 : vptr_q);     // matching line, else victim
  integer p;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      lval_q[0]<=0; lval_q[1]<=0; subv_q[0]<=0; subv_q[1]<=0; ltag_q[0]<=0; ltag_q[1]<=0;
      vptr_q<=0; for(p=0;p<8;p++) begin data_q[0][p]<=0; data_q[1][p]<=0; end
    end else if (fill_en) begin
      if (fe0 | fe1) begin                                // add page to existing line
        data_q[fslot][fo] <= fill_spa; subv_q[fslot][fo] <= 1'b1;
      end else begin                                       // allocate victim line
        ltag_q[vptr_q] <= ft; lval_q[vptr_q] <= 1'b1;
        subv_q[vptr_q] <= (8'b1 << fo); data_q[vptr_q][fo] <= fill_spa;
        vptr_q <= ~vptr_q;
      end
    end
  end
endmodule
