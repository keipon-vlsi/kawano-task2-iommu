// mem_master.sv -- AXI-like read master (AR/R), 8 B (1 PTE) data bus, multi-beat.
//
// The IOMMU only READS page-table entries; the 4 KB DMA payload path is out of scope.
// The memory data bus is 8 B wide (one PTE per beat), matching a real IOMMU's modest
// PTE-fetch port. A coalesced 64 B leaf line arrives as an 8-beat burst (req_burst=1,
// arlen=7); a single walk-step PTE as one beat (req_burst=0, arlen=0). Returns carry
// the request TAG (= walker id) and rlast on the final beat; the engine streams burst
// beats straight into the IOTLB (1 entry/beat). MEM_MAX_OUTSTANDING caps in-flight
// read TRANSACTIONS (not beats). The external AR/R side is driven by the TB stub, which
// injects MEM_LATENCY and delivers the beats one per cycle.
module mem_master #(
  parameter int ADDR_W = 40,
  parameter int DATA_W = 64,
  parameter int TAG_W  = 6,
  parameter int MEM_MAX_OUTSTANDING = 8
)(
  input  logic              clk,
  input  logic              rst_n,

  // internal request (arbiter side): one read transaction, 1 or 8 beats
  input  logic              req_valid,
  output logic              req_ready,
  input  logic [ADDR_W-1:0] req_addr,
  input  logic [TAG_W-1:0]  req_tag,
  input  logic              req_burst,    // 1 => 8-beat (64 B line), 0 => 1-beat (8 B)

  // external AXI-like read channels (TB memory stub)
  output logic              arvalid,
  input  logic              arready,
  output logic [ADDR_W-1:0] araddr,
  output logic [TAG_W-1:0]  arid,
  output logic [2:0]        arlen,        // beats - 1
  input  logic              rvalid,
  output logic              rready,
  input  logic [DATA_W-1:0] rdata,        // (read by the engine directly)
  input  logic [TAG_W-1:0]  rid,
  input  logic              rlast,

  output logic [31:0]       outstanding_o
);
  localparam int CNT_W = (MEM_MAX_OUTSTANDING < 2) ? 1 : $clog2(MEM_MAX_OUTSTANDING+1);
  logic [CNT_W-1:0] outstanding_q;

  logic can_issue, issue, done;
  assign can_issue = (outstanding_q < CNT_W'(MEM_MAX_OUTSTANDING));

  // AR: forward a transaction when under the outstanding cap
  assign arvalid   = req_valid & can_issue;
  assign araddr    = req_addr;
  assign arid      = req_tag;
  assign arlen     = req_burst ? 3'd7 : 3'd0;
  assign req_ready = arready & can_issue;
  assign issue     = arvalid & arready;

  // R: always accept; a transaction completes on its last beat
  assign rready    = 1'b1;
  assign done      = rvalid & rready & rlast;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) outstanding_q <= '0;
    else        outstanding_q <= outstanding_q + CNT_W'(issue) - CNT_W'(done);
  end
  assign outstanding_o = {{(32-CNT_W){1'b0}}, outstanding_q};
endmodule
