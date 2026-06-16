// iommu_pkg.sv -- shared types/widths for the parameterized NESTED IOMMU core.
//
// Scope (this task): nested 2-stage, 4 KB pages only, happy path only.
//   - Start AFTER a DDTC/PDTC hit: the VM-root (VS-stage L2 table base SPA) and the
//     G-stage root (hgatp L2 table base SPA) live in pre-loaded registers. No context
//     walk / no context cache.
//   - The "VM-root PWC" is "always hit": it is just the vs_root_spa register.
//   - Every PTE valid, every access permitted; no fault/PRI logic.
//   - Data movement out of scope: a request completes when its SPA is produced.
//
// Address model. Both stages are modelled as 3-level Sv39-style page tables over
// 4 KB pages (uniform 9-bit indices); the Sv39x4 16 KiB G-root widening is out of
// happy-path scope (see ASSUMPTIONS.md). Pointers held as PPN/GPN (offset implicit 0
// for table bases).
//   IOVA = VPN(27)  + off(12) = 39b ;  VPN  = {VPN[2],VPN[1],VPN[0]} (9b each)
//   GPA  = GVPN(27) + off(12) = 39b ;  GVPN = {GVPN[2],GVPN[1],GVPN[0]}
//   SPA  = PPN(28)  + off(12) = 40b
package iommu_pkg;

  // ---- address widths ----
  localparam int OFFSET_W = 12;
  localparam int IDX_W    = 9;            // page-table index per level
  localparam int VPN_W    = 27;           // 3 * 9
  localparam int GVPN_W   = 27;
  localparam int PPN_W    = 28;
  localparam int GPN_W    = 27;
  localparam int IOVA_W   = VPN_W  + OFFSET_W;   // 39
  localparam int GPA_W    = GVPN_W + OFFSET_W;   // 39
  localparam int SPA_W    = PPN_W  + OFFSET_W;   // 40

  // ---- context tag widths (device_id + PASID; no VMID in this task) ----
  localparam int DEVICE_W = 16;
  localparam int PASID_W  = 20;
  localparam int CTX_W    = DEVICE_W + PASID_W;  // 36

  // ---- memory / PTE geometry ----
  localparam int PTE_W     = 64;                 // RISC-V Sv39 PTE
  localparam int LINE_PTES = 8;                  // 64 B line / 8 B PTE
  localparam int LINE_W    = LINE_PTES * PTE_W;  // 512b -- one DRAM burst
  localparam int PA_W      = SPA_W;              // memory byte-address width (40)
  localparam int TAG_W_TOP = 6;                  // AXI read-id width (>= clog2(max walkers=37))

  // Sv39 PTE: [63:54] hi | [53:10] ppn44 | [9:8] rsw | [7:0] D A G U X W R V
  function automatic logic [43:0] pte_ppn44(input logic [PTE_W-1:0] pte);
    return pte[53:10];
  endfunction
  function automatic logic [IDX_W-1:0] vidx(input logic [VPN_W-1:0] vpn, input int lvl);
    case (lvl)
      2:       return vpn[26:18];
      1:       return vpn[17:9];
      default: return vpn[8:0];
    endcase
  endfunction
  function automatic logic [IDX_W-1:0] gidx(input logic [GVPN_W-1:0] g, input int lvl);
    case (lvl)
      2:       return g[26:18];
      1:       return g[17:9];
      default: return g[8:0];
    endcase
  endfunction

  // address-composition adder: byte address of PTE index `idx` in table @ base PPN.
  //   addr = (base_ppn << 12) + (idx << 3)
  function automatic logic [PA_W-1:0] pte_addr(input logic [PPN_W-1:0] base_ppn,
                                               input logic [IDX_W-1:0]  idx);
    return ({{(PA_W-PPN_W){1'b0}}, base_ppn} << OFFSET_W)
         + ({{(PA_W-IDX_W){1'b0}},  idx}     << 3);
  endfunction

endpackage : iommu_pkg
