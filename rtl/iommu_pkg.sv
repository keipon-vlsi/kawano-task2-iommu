// iommu_pkg.sv -- shared types, widths and enums for the parameterized IOMMU core.
//
// The microarchitecture mirrors the Python explorer (iommu_sim/). Phase-1 RTL is
// steady-state / happy-path only: no faults, no cold context resolution (root and
// device/process context live in pre-loaded registers), no 4 kB data movement.
//
// Address model (RISC-V Sv39 + Sv39x4, matching design_premises):
//   IOVA = VPN(27) + offset(12) = 39b ;  SPA = PPN(28) + offset(12) = 40b
//   GPA  = GPN(29) + offset(12) = 41b  (Sv39x4 guest physical / intermediate)
package iommu_pkg;

  // ---- fixed address widths ----
  localparam int OFFSET_W = 12;
  localparam int VPN_W    = 27;
  localparam int PPN_W    = 28;
  localparam int GPN_W    = 29;
  localparam int IOVA_W   = VPN_W + OFFSET_W;   // 39
  localparam int SPA_W    = PPN_W + OFFSET_W;   // 40
  localparam int GPA_W    = GPN_W + OFFSET_W;   // 41

  // ---- context tag widths (device_id / PASID / VMID) ----
  localparam int DEVICE_W = 16;
  localparam int PASID_W  = 20;
  localparam int VMID_W   = 14;
  localparam int CTX_W    = DEVICE_W + PASID_W + VMID_W;  // 50

  // ---- enums encoded as int params (portable across tools) ----
  // MODE
  localparam int MODE_BARE    = 0;
  localparam int MODE_S1_ONLY = 1;
  localparam int MODE_S2_ONLY = 2;
  localparam int MODE_NESTED  = 3;
  // LOOKUP_MODE
  localparam int LK_SEQ = 0;   // IOTLB first, then PWC on miss
  localparam int LK_PAR = 1;   // probe all, most-complete-hit priority
  localparam int LK_HYB = 2;   // IOTLB first, then PWC probed in parallel
  // STORAGE
  localparam int ST_FF   = 0;  // DFF / CAM (register-based)
  localparam int ST_SRAM = 1;  // SRAM / register-file style (synth hint)

  // ---- request descriptor (control state only; no 4 kB payload) ----
  typedef struct packed {
    logic [VPN_W-1:0]    vpn;
    logic [DEVICE_W-1:0] device_id;
    logic [PASID_W-1:0]  pasid;
    logic [VMID_W-1:0]   vmid;
    logic                is_write;
  } req_t;

  function automatic logic [CTX_W-1:0] ctx_of(req_t r);
    return {r.vmid, r.pasid, r.device_id};
  endfunction

endpackage : iommu_pkg
