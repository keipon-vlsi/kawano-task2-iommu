// iotlb_t4 -- per-line base-SPA + offset (contiguous physical). When a line's 8 pages map
// to 8 CONTIGUOUS SPAs, store only the line's base PPN + a contiguity flag instead of 8
// SPAs, and compute the entry as base_ppn + offset with an ADDER. 8x less data storage.
// BET on contiguous physical data (superpage-like). Fallback: a non-contiguous fill clears
// the line's contig flag -> its lookups miss (correct; forces a re-walk). 2 lines x 8.
// Lookup datapath: 2 line-tag compares + (base_ppn + offset) adder + per-page valid.
module iotlb_t4 (
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
  logic        contig_q [2];
  logic [7:0]  subv_q [2];
  logic [43:0] base_q [2];        // base SPA PPN of page 0 of the line
  logic        vptr_q;

  logic [2:0]  lo; assign lo = lk_tag[2:0];
  logic [23:0] lt; assign lt = lk_tag[26:3];
  logic m0, m1;
  assign m0 = lval_q[0] & contig_q[0] & (ltag_q[0]==lt) & subv_q[0][lo];
  assign m1 = lval_q[1] & contig_q[1] & (ltag_q[1]==lt) & subv_q[1][lo];
  assign lk_hit = m0 | m1;
  // base + offset (offset in PPN units = page index). Adder on the SPA path.
  assign lk_spa = (m0 ? base_q[0] : base_q[1]) + {41'd0, lo};

  logic [2:0]  fo; assign fo = fill_tag[2:0];
  logic [23:0] ft; assign ft = fill_tag[26:3];
  logic        fe0, fe1, fslot;
  assign fe0 = lval_q[0] & (ltag_q[0]==ft);
  assign fe1 = lval_q[1] & (ltag_q[1]==ft);
  assign fslot = fe1 ? 1'b1 : (fe0 ? 1'b0 : vptr_q);
  // contiguity check: this page's SPA must equal base + offset
  logic        keep_contig; assign keep_contig = (fill_spa == base_q[fslot] + {41'd0, fo});
  integer p;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      lval_q[0]<=0; lval_q[1]<=0; subv_q[0]<=0; subv_q[1]<=0; ltag_q[0]<=0; ltag_q[1]<=0;
      contig_q[0]<=0; contig_q[1]<=0; base_q[0]<=0; base_q[1]<=0; vptr_q<=0;
    end else if (fill_en) begin
      if (fe0 | fe1) begin                               // extend existing line
        subv_q[fslot][fo] <= 1'b1;
        if (!keep_contig) contig_q[fslot] <= 1'b0;
      end else begin                                      // allocate victim; base = this page's SPA - off
        ltag_q[vptr_q] <= ft; lval_q[vptr_q] <= 1'b1; subv_q[vptr_q] <= (8'b1<<fo);
        base_q[vptr_q] <= fill_spa - {41'd0, fo}; contig_q[vptr_q] <= 1'b1; vptr_q <= ~vptr_q;
      end
    end
  end
endmodule
