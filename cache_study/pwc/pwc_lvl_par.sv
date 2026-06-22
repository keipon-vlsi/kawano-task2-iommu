// pwc_lvl_par -- multi-level PWC lookup, PARALLEL + most-complete-hit priority.
// All three levels are looked up at once every access; the result picks the level closest
// to the leaf (near=L1 > mid=L2 > root). Single cycle. This is what iommu_top does today.
//   near = L1 PWC: 2-entry, tag = VPN[26:9] (18b)  -- closest to leaf, most specific
//   mid  = L2 PWC: 2-entry, tag = VPN[26:18] (9b)
//   root : 1 register (no tag, always the fallback)
// out: lk_base (PPN 28b) + lk_lvl (2=near,1=mid,0=root). Cost: all comparators active every
// access + priority mux; benefit: 1-cycle latency.
module pwc_lvl_par (
  input  logic        clk, rst_n,
  input  logic [26:0] lk_vpn,
  output logic [27:0] lk_base,
  output logic [1:0]  lk_lvl,
  input  logic        fn_en, input logic [17:0] fn_tag, input logic [27:0] fn_d,  // L1 fill
  input  logic        fm_en, input logic [8:0]  fm_tag, input logic [27:0] fm_d,  // L2 fill
  input  logic        fr_en, input logic [27:0] fr_d                              // root fill
);
  logic        nh, mh;
  logic [27:0] nd, md, root_q;
  fa_cache #(.ENTRIES(2),.TAG_W(18),.DATA_W(28)) u_near (.clk,.rst_n,
     .lk_tag(lk_vpn[26:9]), .lk_hit(nh), .lk_data(nd), .fill_en(fn_en),.fill_tag(fn_tag),.fill_data(fn_d));
  fa_cache #(.ENTRIES(2),.TAG_W(9), .DATA_W(28)) u_mid  (.clk,.rst_n,
     .lk_tag(lk_vpn[26:18]),.lk_hit(mh), .lk_data(md), .fill_en(fm_en),.fill_tag(fm_tag),.fill_data(fm_d));
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) root_q <= '0; else if (fr_en) root_q <= fr_d;
  end
  // most-complete-hit: near (closest to leaf) wins, else mid, else root
  assign lk_base = nh ? nd : mh ? md : root_q;
  assign lk_lvl  = nh ? 2'd2 : mh ? 2'd1 : 2'd0;
endmodule
