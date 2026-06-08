// mem_if.sv -- AXI-like read master (AR/R) with tagged outstanding reads.
//
// The IOMMU issues page-table-entry reads; the data-write path (4 kB DMA payload)
// is out of scope (handled by the I/O bridge). Outstanding reads are capped by
// MEM_MAX_OUTSTANDING -- this is the ceiling on concurrent page-table walks
// (design_premises §6: AXI outstanding count == parallel walk upper bound).
//
// Internal side (from the arbiter): simple valid/ready request + tagged response.
// External side (to the testbench memory stub): AR/R channels with IDs.
module mem_if #(
  parameter int ADDR_W = 41,
  parameter int DATA_W = 28,
  parameter int TAG_W  = 4,
  parameter int MEM_MAX_OUTSTANDING = 8
)(
  input  logic              clk,
  input  logic              rst_n,

  // --- internal request/response (arbiter side) ---
  input  logic              req_valid,
  output logic              req_ready,
  input  logic [ADDR_W-1:0] req_addr,
  input  logic [TAG_W-1:0]  req_tag,

  output logic              rsp_valid,
  output logic [DATA_W-1:0] rsp_data,
  output logic [TAG_W-1:0]  rsp_tag,

  // --- external AXI-like read channels (TB memory stub) ---
  output logic              arvalid,
  input  logic              arready,
  output logic [ADDR_W-1:0] araddr,
  output logic [TAG_W-1:0]  arid,

  input  logic              rvalid,
  output logic              rready,
  input  logic [DATA_W-1:0] rdata,
  input  logic [TAG_W-1:0]  rid,

  // observability
  output logic [31:0]       outstanding_o
);
  localparam int CNT_W = (MEM_MAX_OUTSTANDING < 2) ? 1 : $clog2(MEM_MAX_OUTSTANDING+1);
  logic [CNT_W-1:0] outstanding;

  logic can_issue;
  assign can_issue = (outstanding < CNT_W'(MEM_MAX_OUTSTANDING));

  // AR: forward internal request when allowed by the outstanding cap
  assign arvalid   = req_valid & can_issue;
  assign araddr    = req_addr;
  assign arid      = req_tag;
  assign req_ready = arready & can_issue;

  // R: always accept (the walker latches the tagged result)
  assign rready    = 1'b1;
  assign rsp_valid = rvalid;
  assign rsp_data  = rdata;
  assign rsp_tag   = rid;

  wire ar_fire = arvalid & arready;
  wire r_fire  = rvalid  & rready;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)       outstanding <= '0;
    else case ({ar_fire, r_fire})
      2'b10:          outstanding <= outstanding + 1'b1;
      2'b01:          outstanding <= outstanding - 1'b1;
      default:        outstanding <= outstanding;
    endcase
  end

  assign outstanding_o = 32'(outstanding);
endmodule
