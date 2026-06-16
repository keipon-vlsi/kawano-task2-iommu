// prefetch_ctrl.sv -- next-line (data-leaf / IOTLB) prefetch trigger.
//
// On first access to a demand line, request a prefetch walk for line+LEAD (the next
// COALESCE pages) so its IOTLB entries are warm before the demand arrives -> the
// cold-start / boundary demand stall is hidden. Real RTL: line adder, same-VM-L0-table
// guard (a leaf prefetch reuses the region's VM-L0 table base, so it must stay inside
// that table), and a dedup comparator against the last issued target.
//
// LEAD is the lead distance (1 = strict next line; larger = "well in advance" so an
// upper-level 2 MB/1 GB boundary refill is issued before the demand reaches it). For a
// trace shorter than one VM-L0 table (512 pages) LEAD acts as next-line.
module prefetch_ctrl #(
  parameter int VPNLINE_W = 24,
  parameter int LINE_IN_L0 = 6,   // line-index bits inside one VM-L0 table (= 9 - log2(CO))
  parameter int LEAD       = 1
)(
  input  logic                 clk,
  input  logic                 rst_n,
  input  logic                 demand_service_v,   // a demand line was touched this cycle
  input  logic [VPNLINE_W-1:0] demand_line,
  input  logic                 region_valid,       // a VM-L0 table base has been captured
  input  logic                 pf_free,            // prefetch walker available
  output logic                 pf_trig,
  output logic [VPNLINE_W-1:0] pf_line
);
  logic [VPNLINE_W-1:0] last_q;
  logic [VPNLINE_W-1:0] tgt;
  logic same_table;

  assign tgt = demand_line + VPNLINE_W'(LEAD);
  // same VM-L0 table iff the high bits above the in-table line index match
  assign same_table = (tgt[VPNLINE_W-1:LINE_IN_L0] == demand_line[VPNLINE_W-1:LINE_IN_L0]);
  assign pf_line = tgt;
  assign pf_trig = demand_service_v & region_valid & pf_free & same_table & (tgt != last_q);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)        last_q <= '0;
    else if (pf_trig)  last_q <= tgt;
  end
endmodule
