// cfg5_top.sv -- config #5: #4 with device_id/PASID removed from all cache tags
// (TAG_CONTEXT_EN=0). Functionally identical for a single context; smaller tags
// (area/timing comparison vs cfg4).
import iommu_pkg::*;
module cfg5_top (
  input  logic clk, rst_n,
  input  logic pl_valid, input logic [1:0] pl_sel, input logic [PPN_W-1:0] pl_data,
  input  logic req_valid, output logic req_ready,
  input  logic [VPN_W-1:0] req_vpn, input logic [DEVICE_W-1:0] req_device_id,
  input  logic [PASID_W-1:0] req_pasid, input logic req_is_write,
  output logic rsp_valid, input logic rsp_ready,
  output logic [VPN_W-1:0] rsp_vpn, output logic [SPA_W-1:0] rsp_spa,
  output logic arvalid, input logic arready, output logic [PA_W-1:0] araddr,
  output logic [TAG_W_TOP-1:0] arid, output logic [2:0] arlen,
  input  logic rvalid, output logic rready, input logic [PTE_W-1:0] rdata,
  input  logic [TAG_W_TOP-1:0] rid, input logic rlast,
  output logic [31:0] walks_o, resp_o, outstanding_o
);
  iommu_top #(
    .HAS_PWC(1), .HAS_IOTLB(1), .NUM_WALKERS(1), .BUFFER_DEPTH(1),
    .COALESCE_FACTOR(8), .PREFETCH_EN(1), .PREFETCH_LEAD(1), .TAG_CONTEXT_EN(0),
    .MEM_LATENCY_CYCLES(40), .MEM_MAX_OUTSTANDING(8), .PIPELINE_DEPTH(2)
  ) u_core (.*);
endmodule
