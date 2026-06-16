// mem_master.sv -- AXI-like read master (AR/R) with tagged outstanding reads.
//
// The IOMMU only READS page-table entries; the 4 KB DMA payload path is out of scope.
// One read returns a full 64 B line (8 PTEs) -- this is what makes COALESCE_FACTOR=8
// work. Outstanding reads are capped by MEM_MAX_OUTSTANDING == the hard ceiling on
// concurrent in-flight page-table memory accesses. Returns carry the request TAG
// (= walker/context id); the engine demuxes by tag. The external AR/R side is driven
// by the TB memory stub (which injects MEM_LATENCY).
module mem_master #(
  parameter int ADDR_W = 40,
  parameter int DATA_W = 512,
  parameter int TAG_W  = 6,
  parameter int MEM_MAX_OUTSTANDING = 8
)(
  input  logic              clk,
  input  logic              rst_n,

  // internal request/response (arbiter side)
  input  logic              req_valid,
  output logic              req_ready,
  input  logic [ADDR_W-1:0] req_addr,
  input  logic [TAG_W-1:0]  req_tag,

  output logic              rsp_valid,
  output logic [DATA_W-1:0] rsp_data,
  output logic [TAG_W-1:0]  rsp_tag,

  // external AXI-like read channels (TB memory stub)
  output logic              arvalid,
  input  logic              arready,
  output logic [ADDR_W-1:0] araddr,
  output logic [TAG_W-1:0]  arid,
  input  logic              rvalid,
  output logic              rready,
  input  logic [DATA_W-1:0] rdata,
  input  logic [TAG_W-1:0]  rid,

  output logic [31:0]       outstanding_o
);
  localparam int CNT_W = (MEM_MAX_OUTSTANDING < 2) ? 1 : $clog2(MEM_MAX_OUTSTANDING+1);
  logic [CNT_W-1:0] outstanding_q;

  logic can_issue, issue, ret;
  assign can_issue = (outstanding_q < CNT_W'(MEM_MAX_OUTSTANDING));

  // AR: forward an internal request when under the outstanding cap
  assign arvalid   = req_valid & can_issue;
  assign araddr    = req_addr;
  assign arid      = req_tag;
  assign req_ready = arready & can_issue;
  assign issue     = arvalid & arready;

  // R: always accept; pass tagged result straight to the engine
  assign rready    = 1'b1;
  assign rsp_valid = rvalid;
  assign rsp_data  = rdata;
  assign rsp_tag   = rid;
  assign ret       = rvalid & rready;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) outstanding_q <= '0;
    else        outstanding_q <= outstanding_q + CNT_W'(issue) - CNT_W'(ret);
  end
  assign outstanding_o = {{(32-CNT_W){1'b0}}, outstanding_q};
endmodule
