// iotlb_ports.sv -- shared line-organized IOTLB (2 lines x 8) serving NP lookup subjects,
// two ways: (A) iotlb_mport = NP independent parallel lookup ports (lookup logic x NP);
// (B) iotlb_muxport = 1 lookup port + an N:1 input MUX selecting which subject's tag.
// Storage (the 2x8 entries) is SHARED & identical in both -> isolates how the LOOKUP
// logic area scales with the number of subjects. NP set via `define NP (sv2v -DNP=n).
`ifndef NP
`define NP 1
`endif

// ---------- (A) multi-port: NP parallel lookup datapaths over shared storage ----------
module iotlb_mport (
  input  logic clk, rst_n,
  input  logic [27*`NP-1:0] lk_tag,     // NP request tags, packed
  output logic [`NP-1:0]    lk_hit,
  output logic [44*`NP-1:0] lk_spa,     // NP results, packed
  input  logic        fill_en,
  input  logic [26:0] fill_tag,
  input  logic [43:0] fill_spa
);
  localparam int NP = `NP;
  logic [23:0] ltag_q [2]; logic lval_q [2]; logic [7:0] subv_q [2];
  logic [43:0] data_q [2][8]; logic vptr_q;

  genvar p;
  generate for (p=0; p<NP; p++) begin : LP        // <-- one lookup datapath PER PORT
    wire [26:0] t  = lk_tag[p*27 +: 27];
    wire [2:0]  lo = t[2:0];
    wire [23:0] lt = t[26:3];
    wire mm0 = lval_q[0] & (ltag_q[0]==lt) & subv_q[0][lo];
    wire mm1 = lval_q[1] & (ltag_q[1]==lt) & subv_q[1][lo];
    assign lk_hit[p]          = mm0 | mm1;
    assign lk_spa[p*44 +: 44] = mm0 ? data_q[0][lo] : data_q[1][lo];
  end endgenerate

  wire [2:0]  fo = fill_tag[2:0];
  wire [23:0] ft = fill_tag[26:3];
  wire fe0 = lval_q[0] & (ltag_q[0]==ft);
  wire fe1 = lval_q[1] & (ltag_q[1]==ft);
  wire fslot = fe1 ? 1'b1 : (fe0 ? 1'b0 : vptr_q);
  integer i;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      lval_q[0]<=0; lval_q[1]<=0; subv_q[0]<=0; subv_q[1]<=0; ltag_q[0]<=0; ltag_q[1]<=0; vptr_q<=0;
      for (i=0;i<8;i++) begin data_q[0][i]<=0; data_q[1][i]<=0; end
    end else if (fill_en) begin
      if (fe0|fe1) begin data_q[fslot][fo]<=fill_spa; subv_q[fslot][fo]<=1'b1; end
      else begin ltag_q[vptr_q]<=ft; lval_q[vptr_q]<=1'b1; subv_q[vptr_q]<=(8'b1<<fo);
                 data_q[vptr_q][fo]<=fill_spa; vptr_q<=~vptr_q; end
    end
  end
endmodule

// ---------- (B) single port + N:1 input MUX (one lookup datapath, NP tags time-shared) ----------
module iotlb_muxport (
  input  logic clk, rst_n,
  input  logic [27*`NP-1:0] lk_tag,     // NP candidate tags
  input  logic [3:0]  sel,              // which subject this cycle (NP<=10 -> 4b)
  output logic        lk_hit,
  output logic [43:0] lk_spa,
  input  logic        fill_en,
  input  logic [26:0] fill_tag,
  input  logic [43:0] fill_spa
);
  localparam int NP = `NP;
  logic [23:0] ltag_q [2]; logic lval_q [2]; logic [7:0] subv_q [2];
  logic [43:0] data_q [2][8]; logic vptr_q;

  logic [26:0] t;                                  // <-- N:1 input mux (the only port-scaling logic)
  always_comb begin
    t = lk_tag[0 +: 27];
    for (int j=0; j<NP; j++) if (j==sel) t = lk_tag[j*27 +: 27];
  end
  wire [2:0]  lo = t[2:0];
  wire [23:0] lt = t[26:3];
  wire mm0 = lval_q[0] & (ltag_q[0]==lt) & subv_q[0][lo];   // <-- ONE lookup datapath
  wire mm1 = lval_q[1] & (ltag_q[1]==lt) & subv_q[1][lo];
  assign lk_hit = mm0 | mm1;
  assign lk_spa = mm0 ? data_q[0][lo] : data_q[1][lo];

  wire [2:0]  fo = fill_tag[2:0];
  wire [23:0] ft = fill_tag[26:3];
  wire fe0 = lval_q[0] & (ltag_q[0]==ft);
  wire fe1 = lval_q[1] & (ltag_q[1]==ft);
  wire fslot = fe1 ? 1'b1 : (fe0 ? 1'b0 : vptr_q);
  integer i;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      lval_q[0]<=0; lval_q[1]<=0; subv_q[0]<=0; subv_q[1]<=0; ltag_q[0]<=0; ltag_q[1]<=0; vptr_q<=0;
      for (i=0;i<8;i++) begin data_q[0][i]<=0; data_q[1][i]<=0; end
    end else if (fill_en) begin
      if (fe0|fe1) begin data_q[fslot][fo]<=fill_spa; subv_q[fslot][fo]<=1'b1; end
      else begin ltag_q[vptr_q]<=ft; lval_q[vptr_q]<=1'b1; subv_q[vptr_q]<=(8'b1<<fo);
                 data_q[vptr_q][fo]<=fill_spa; vptr_q<=~vptr_q; end
    end
  end
endmodule
