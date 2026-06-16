// cfg1_top.sv -- config #1 "no cache": full cold nested walk every translation.
// HAS_PWC=0 HAS_IOTLB=0 WALKERS=37 BUFFER=37 COALESCE=1 PREFETCH=0 TAG_CONTEXT=1
// (37 walkers/buffer come from the simulator: 12-access cold nested walk, Little's law.)
import iommu_pkg::*;
module cfg1_top (
  input  logic clk, rst_n,
  input  logic pl_valid, input logic [1:0] pl_sel, input logic [PPN_W-1:0] pl_data,
  input  logic req_valid, output logic req_ready,
  input  logic [VPN_W-1:0] req_vpn, input logic [DEVICE_W-1:0] req_device_id,
  input  logic [PASID_W-1:0] req_pasid, input logic req_is_write,
  output logic rsp_valid, input logic rsp_ready,
  output logic [VPN_W-1:0] rsp_vpn, output logic [SPA_W-1:0] rsp_spa,
  output logic arvalid, input logic arready, output logic [PA_W-1:0] araddr,
  output logic [TAG_W_TOP-1:0] arid,
  input  logic rvalid, output logic rready, input logic [LINE_W-1:0] rdata,
  input  logic [TAG_W_TOP-1:0] rid,
  output logic [31:0] walks_o, resp_o, outstanding_o
);
  iommu_top #(
    .HAS_PWC(0), .HAS_IOTLB(0), .NUM_WALKERS(37), .BUFFER_DEPTH(37),
    .COALESCE_FACTOR(1), .PREFETCH_EN(0), .TAG_CONTEXT_EN(1),
    .MEM_LATENCY_CYCLES(40), .MEM_MAX_OUTSTANDING(40), .PIPELINE_DEPTH(1)
  ) u_core (.*);
endmodule
