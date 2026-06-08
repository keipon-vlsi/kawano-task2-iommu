// iommu_core.sv -- top level: wires the 5 synthesizable blocks.
//
//   request --> [txn_buffer + MSHR + caches] --dispatch--> [walk_engine: N walkers]
//                      ^  |                                        |  (arbiter)
//                      |  +--------- completion (SPA) <------------+
//                      |                                          [mem_if: AXI read]
//                   response                                       AR/R  <--> TB memory
//
// One parameterized design; a "config" is just this parameter set. Phase-1 scope:
// steady-state happy path (no faults; context pre-loaded; no 4 kB data path).
import iommu_pkg::*;

module iommu_core #(
  // ---- architecture / config ----
  parameter int MODE                = MODE_NESTED,
  parameter int COALESCE_FACTOR     = 8,
  parameter int PREFETCH_EN         = 0,
  parameter int NUM_WALKERS         = 4,
  parameter int BUFFER_DEPTH        = 16,
  parameter int MEM_MAX_OUTSTANDING = 8,
  parameter int LOOKUP_MODE         = LK_HYB,    // hook (Phase 1: lookup is hybrid-like)
  parameter int PIPELINE_DEPTH      = 1,         // hook (cache lookup latency = 1)
  parameter int CLOCK_GATING_EN     = 0,
  // ---- per-cache geometry ----
  parameter int IOTLB_ENTRIES = 64, parameter int IOTLB_ASSOC = 4,  parameter int IOTLB_STORAGE = ST_SRAM,
  parameter int S1PWC_ENTRIES = 16, parameter int S1PWC_ASSOC = 16, parameter int S1PWC_STORAGE = ST_FF,
  parameter int S2PWC_ENTRIES = 16, parameter int S2PWC_ASSOC = 16, parameter int S2PWC_STORAGE = ST_FF,
  parameter int DDTC_ENTRIES  = 4,  parameter int PDTC_ENTRIES  = 4,
  // ---- derived widths ----
  parameter int TAG_W      = (NUM_WALKERS < 2) ? 1 : $clog2(NUM_WALKERS),
  parameter int MSHR_W     = (BUFFER_DEPTH < 2) ? 1 : $clog2(BUFFER_DEPTH),
  parameter int IOTLB_KEY_W= CTX_W + VPN_W
)(
  input  logic              clk,
  input  logic              rst_n,

  // --- translation request (fields flattened for TB friendliness) ---
  input  logic              req_valid,
  output logic              req_ready,
  input  logic [VPN_W-1:0]    req_vpn,
  input  logic [DEVICE_W-1:0] req_device_id,
  input  logic [PASID_W-1:0]  req_pasid,
  input  logic [VMID_W-1:0]   req_vmid,
  input  logic                req_is_write,

  // --- translation response ---
  output logic              rsp_valid,
  input  logic              rsp_ready,
  output logic [SPA_W-1:0]  rsp_spa,
  output logic [MSHR_W-1:0] rsp_tag,

  // --- AXI-like read master to memory (TB models the slave + latency) ---
  output logic              arvalid,
  input  logic              arready,
  output logic [GPA_W-1:0]  araddr,
  output logic [TAG_W-1:0]  arid,
  input  logic              rvalid,
  output logic              rready,
  input  logic [LINE_W-1:0] rdata,        // 64 B line (8 PTEs) per beat
  input  logic [TAG_W-1:0]  rid,

  // --- cache preload (steady-state warm-up by TB) ---
  input  logic              pl_valid,
  input  logic [2:0]        pl_sel,
  input  logic [IOTLB_KEY_W-1:0] pl_key,
  input  logic [SPA_W-1:0]  pl_data,

  // --- observability ---
  output logic [31:0]       cnt_iotlb_hit,
  output logic [31:0]       cnt_coalesced,
  output logic [31:0]       cnt_walks,
  output logic [31:0]       buf_occupancy,
  output logic [31:0]       active_walks,
  output logic [31:0]       mem_outstanding
);
  // assemble the request descriptor
  req_t req_w;
  always_comb begin
    req_w.vpn       = req_vpn;
    req_w.device_id = req_device_id;
    req_w.pasid     = req_pasid;
    req_w.vmid      = req_vmid;
    req_w.is_write  = req_is_write;
  end

  // front-end <-> walk-engine
  logic                disp_valid, disp_ready;
  logic [VPN_W-1:0]    disp_vpn;
  logic [1:0]          disp_start_level;
  logic [PPN_W-1:0]    disp_base;
  logic [MSHR_W-1:0]   disp_mshr;
  logic                done_valid, done_ready;
  logic [MSHR_W-1:0]   done_mshr;
  logic [VPN_W-1:0]    done_vpn;
  logic [1:0]          done_start_level;
  logic [SPA_W-1:0]    done_spa;
  logic [PPN_W-1:0]    done_l1tab, done_leaftab;
  logic [LINE_W-1:0]   done_leafline;

  // walk-engine <-> mem_if
  logic                mreq_valid, mreq_ready;
  logic [GPA_W-1:0]    mreq_addr;
  logic [TAG_W-1:0]    mreq_tag;
  logic                mrsp_valid;
  logic [LINE_W-1:0]   mrsp_line;
  logic [TAG_W-1:0]    mrsp_tag;

  // ---- block 2/3: transaction buffer + MSHR + caches ----
  txn_buffer #(
    .MODE(MODE), .COALESCE_FACTOR(COALESCE_FACTOR), .BUFFER_DEPTH(BUFFER_DEPTH),
    .PREFETCH_EN(PREFETCH_EN), .CLOCK_GATING_EN(CLOCK_GATING_EN),
    .IOTLB_ENTRIES(IOTLB_ENTRIES), .IOTLB_ASSOC(IOTLB_ASSOC), .IOTLB_STORAGE(IOTLB_STORAGE),
    .S1PWC_ENTRIES(S1PWC_ENTRIES), .S1PWC_ASSOC(S1PWC_ASSOC), .S1PWC_STORAGE(S1PWC_STORAGE),
    .S2PWC_ENTRIES(S2PWC_ENTRIES), .S2PWC_ASSOC(S2PWC_ASSOC), .S2PWC_STORAGE(S2PWC_STORAGE),
    .DDTC_ENTRIES(DDTC_ENTRIES), .PDTC_ENTRIES(PDTC_ENTRIES)
  ) u_front (
    .clk(clk), .rst_n(rst_n),
    .req_valid(req_valid), .req_ready(req_ready), .req(req_w),
    .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_spa(rsp_spa), .rsp_tag(rsp_tag),
    .disp_valid(disp_valid), .disp_ready(disp_ready), .disp_vpn(disp_vpn),
    .disp_start_level(disp_start_level), .disp_base(disp_base), .disp_mshr(disp_mshr),
    .done_valid(done_valid), .done_ready(done_ready), .done_mshr(done_mshr),
    .done_vpn(done_vpn), .done_start_level(done_start_level), .done_spa(done_spa),
    .done_l1tab(done_l1tab), .done_leaftab(done_leaftab), .done_leafline(done_leafline),
    .pl_valid(pl_valid), .pl_sel(pl_sel), .pl_key(pl_key), .pl_data(pl_data),
    .cnt_iotlb_hit(cnt_iotlb_hit), .cnt_coalesced(cnt_coalesced),
    .cnt_walks(cnt_walks), .buf_occupancy(buf_occupancy));

  // ---- block 1/4: walk engine (N walkers) + memory-request arbiter ----
  walk_engine #(
    .NUM_WALKERS(NUM_WALKERS), .TAG_W(TAG_W), .MSHR_W(MSHR_W)
  ) u_walk (
    .clk(clk), .rst_n(rst_n),
    .disp_valid(disp_valid), .disp_ready(disp_ready), .disp_vpn(disp_vpn),
    .disp_start_level(disp_start_level), .disp_base(disp_base), .disp_mshr(disp_mshr),
    .mreq_valid(mreq_valid), .mreq_ready(mreq_ready), .mreq_addr(mreq_addr), .mreq_tag(mreq_tag),
    .mrsp_valid(mrsp_valid), .mrsp_line(mrsp_line), .mrsp_tag(mrsp_tag),
    .done_valid(done_valid), .done_ready(done_ready), .done_mshr(done_mshr),
    .done_vpn(done_vpn), .done_start_level(done_start_level), .done_spa(done_spa),
    .done_l1tab(done_l1tab), .done_leaftab(done_leaftab), .done_leafline(done_leafline),
    .active_walks_o(active_walks));

  // ---- block 5: memory interface (AXI-like read master, 64 B line) ----
  mem_if #(
    .ADDR_W(GPA_W), .DATA_W(LINE_W), .TAG_W(TAG_W), .MEM_MAX_OUTSTANDING(MEM_MAX_OUTSTANDING)
  ) u_mem (
    .clk(clk), .rst_n(rst_n),
    .req_valid(mreq_valid), .req_ready(mreq_ready), .req_addr(mreq_addr), .req_tag(mreq_tag),
    .rsp_valid(mrsp_valid), .rsp_data(mrsp_line), .rsp_tag(mrsp_tag),
    .arvalid(arvalid), .arready(arready), .araddr(araddr), .arid(arid),
    .rvalid(rvalid), .rready(rready), .rdata(rdata), .rid(rid),
    .outstanding_o(mem_outstanding));
endmodule
