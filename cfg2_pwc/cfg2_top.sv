// cfg2_top.sv -- config #2 "PWC, no coalescing": PWC hits collapse upper levels,
// steady state = VM-L0 leaf + G-L0 leaf = 2 reads. WALKERS=5 BUFFER=5 (200ns/40.96).
import iommu_pkg::*;
module cfg2_top (
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
    .HAS_PWC(1), .HAS_IOTLB(0), .NUM_WALKERS(5), .BUFFER_DEPTH(5),
    .COALESCE_FACTOR(1), .PREFETCH_EN(0), .TAG_CONTEXT_EN(1),
    .MEM_LATENCY_CYCLES(40), .MEM_MAX_OUTSTANDING(8), .PIPELINE_DEPTH(1)
  ) u_core (.*);
endmodule
