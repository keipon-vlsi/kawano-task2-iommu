// iotlb_t0x3 -- T0 line-organized IOTLB scaled to 3 lines x 8 = 24 entries (T0 is 2x8=16).
// Same scheme: line_tag = VPN[26:3] (24b), offset = VPN[2:0] (3b) is a direct mux index.
// Lookup = 3 line-tag compares + offset 8:1 mux + 3:1 line select. Round-robin victim over
// 3 slots. For an area/Fmax data point vs T0 (16 entries).
module iotlb_t0x3 (
  input  logic clk, rst_n,
  input  logic [26:0] lk_tag,
  output logic        lk_hit,
  output logic [43:0] lk_spa,
  input  logic        fill_en,
  input  logic [26:0] fill_tag,
  input  logic [43:0] fill_spa
);
  logic [23:0] ltag_q [3];
  logic        lval_q [3];
  logic [7:0]  subv_q [3];
  logic [43:0] data_q [3][8];
  logic [1:0]  vptr_q;                 // 0->1->2->0

  logic [2:0]  lo; assign lo = lk_tag[2:0];
  logic [23:0] lt; assign lt = lk_tag[26:3];
  logic m0, m1, m2;
  assign m0 = lval_q[0] & (ltag_q[0]==lt) & subv_q[0][lo];
  assign m1 = lval_q[1] & (ltag_q[1]==lt) & subv_q[1][lo];
  assign m2 = lval_q[2] & (ltag_q[2]==lt) & subv_q[2][lo];
  assign lk_hit = m0 | m1 | m2;
  assign lk_spa = m0 ? data_q[0][lo] : m1 ? data_q[1][lo] : data_q[2][lo];

  logic [2:0]  fo; assign fo = fill_tag[2:0];
  logic [23:0] ft; assign ft = fill_tag[26:3];
  logic        fe0, fe1, fe2; logic [1:0] fslot;
  assign fe0 = lval_q[0] & (ltag_q[0]==ft);
  assign fe1 = lval_q[1] & (ltag_q[1]==ft);
  assign fe2 = lval_q[2] & (ltag_q[2]==ft);
  assign fslot = fe0 ? 2'd0 : fe1 ? 2'd1 : fe2 ? 2'd2 : vptr_q;   // matching line, else victim
  integer p;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (p=0;p<3;p++) begin lval_q[p]<=0; subv_q[p]<=0; ltag_q[p]<=0; end
      for (p=0;p<8;p++) begin data_q[0][p]<=0; data_q[1][p]<=0; data_q[2][p]<=0; end
      vptr_q<=0;
    end else if (fill_en) begin
      if (fe0 | fe1 | fe2) begin                          // add page to existing line
        data_q[fslot][fo] <= fill_spa; subv_q[fslot][fo] <= 1'b1;
      end else begin                                       // allocate victim line
        ltag_q[vptr_q] <= ft; lval_q[vptr_q] <= 1'b1;
        subv_q[vptr_q] <= (8'b1 << fo); data_q[vptr_q][fo] <= fill_spa;
        vptr_q <= (vptr_q==2'd2) ? 2'd0 : vptr_q + 2'd1;
      end
    end
  end
endmodule
