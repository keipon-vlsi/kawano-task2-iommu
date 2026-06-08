// walker.sv -- one page-table-walk context: a full Sv39 pointer chase.
//
// Detailed (STEP 1) version: no synthetic addresses. The walker latches the real
// 64-bit PTE returned by memory, generates the next-level table address from the
// PTE contents (next = (ppn<<12) | (vpn_index[level]<<3)), and chases from a
// dispatched start level down to the leaf. The leaf fetch returns a full 64 B
// line (8 PTEs) that is latched into a 512-bit line buffer (coalescing).
//
// Registers kept on purpose (area/power realism; happy-path ignores fault/perm
// logic but the BITS exist): 64-bit PTE register incl. all flags+RSW+PPN, the
// 512-bit leaf line buffer, the running table-base, the level index, and the
// captured upper-level base PPNs returned for PWC fill.
import iommu_pkg::*;

module walker #(
  parameter int TAG_W  = 4,
  parameter int MSHR_W = 6
)(
  input  logic               clk,
  input  logic               rst_n,
  input  logic [TAG_W-1:0]   walker_id,

  // --- dispatch (from front-end): start the chase at start_level from base ---
  input  logic               disp_valid,
  output logic               disp_ready,
  input  logic [VPN_W-1:0]   disp_vpn,
  input  logic [1:0]         disp_start_level,   // 2=root,1,0=leaf
  input  logic [PPN_W-1:0]   disp_base,          // table base PPN at start_level
  input  logic [MSHR_W-1:0]  disp_mshr,

  // --- memory (to arbiter): one 64 B line per read ---
  output logic               mreq_valid,
  input  logic               mreq_ready,
  output logic [GPA_W-1:0]   mreq_addr,
  output logic [TAG_W-1:0]   mreq_tag,
  input  logic               mrsp_valid,
  input  logic [LINE_W-1:0]  mrsp_line,

  // --- completion (to front-end / MSHR) ---
  output logic               done_valid,
  input  logic               done_ready,
  output logic [MSHR_W-1:0]  done_mshr,
  output logic [VPN_W-1:0]   done_vpn,
  output logic [1:0]         done_start_level,
  output logic [SPA_W-1:0]   done_spa,           // requested page SPA
  output logic [PPN_W-1:0]   done_l1tab,         // L1-table base (from L2 PTE)  [valid if start>=2]
  output logic [PPN_W-1:0]   done_leaftab,       // leaf-table base (from L1 PTE)[valid if start>=1]
  output logic [LINE_W-1:0]  done_leafline,      // 8 leaf PTEs (coalescing)
  output logic               busy
);
  typedef enum logic [1:0] {W_IDLE, W_ISSUE, W_WAIT, W_DONE} state_e;
  state_e state;

  logic [VPN_W-1:0]  vpn_q;
  logic [MSHR_W-1:0] mshr_q;
  logic [1:0]        start_lvl_q;
  logic [1:0]        level_q;        // current level being fetched
  logic [PPN_W-1:0]  base_q;         // running table base PPN
  sv39_pte_t         pte_q;          // last latched PTE (full 64 b: flags+RSW+PPN)
  logic [LINE_W-1:0] line_q;         // last fetched 64 B line
  logic [PPN_W-1:0]  l1tab_q, leaftab_q;
  logic [LINE_W-1:0] leafline_q;
  logic [SPA_W-1:0]  spa_q;

  // current PTE address and its position inside the 64 B line
  logic [8:0]        idx;            // vpn index for this level
  logic [GPA_W-1:0]  pte_addr;
  assign idx      = vpn_index(vpn_q, int'(level_q));
  assign pte_addr = GPA_W'((GPA_W'(base_q) << 12) | (GPA_W'(idx) << 3));
  // selected 64-bit PTE within the returned line (idx[2:0] = which of 8)
  sv39_pte_t sel_pte;
  assign sel_pte  = mrsp_line[ idx[2:0]*PTE_W +: PTE_W ];

  assign busy        = (state != W_IDLE);
  assign disp_ready  = (state == W_IDLE);
  assign mreq_valid  = (state == W_ISSUE);
  assign mreq_addr   = pte_addr;
  assign mreq_tag    = walker_id;

  assign done_valid        = (state == W_DONE);
  assign done_mshr         = mshr_q;
  assign done_vpn          = vpn_q;
  assign done_start_level  = start_lvl_q;
  assign done_spa          = spa_q;
  assign done_l1tab        = l1tab_q;
  assign done_leaftab      = leaftab_q;
  assign done_leafline     = leafline_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= W_IDLE; vpn_q <= '0; mshr_q <= '0; start_lvl_q <= '0;
      level_q <= '0; base_q <= '0; pte_q <= '0; line_q <= '0;
      l1tab_q <= '0; leaftab_q <= '0; leafline_q <= '0; spa_q <= '0;
    end else begin
      case (state)
        W_IDLE: if (disp_valid) begin
          vpn_q       <= disp_vpn;
          mshr_q      <= disp_mshr;
          start_lvl_q <= disp_start_level;
          level_q     <= disp_start_level;
          base_q      <= disp_base;
          state       <= W_ISSUE;
        end
        W_ISSUE: if (mreq_ready) state <= W_WAIT;   // AR accepted
        W_WAIT:  if (mrsp_valid) begin              // 64 B line returned
          line_q <= mrsp_line;
          pte_q  <= sel_pte;
          base_q <= pte_ppn(sel_pte);               // next-level table base
          // capture per-level results for the front-end (PWC / IOTLB fill)
          if (level_q == 2'd2) l1tab_q   <= pte_ppn(sel_pte);  // L2 PTE -> L1 table
          if (level_q == 2'd1) leaftab_q <= pte_ppn(sel_pte);  // L1 PTE -> leaf table
          if (level_q == 2'd0) begin
            leafline_q <= mrsp_line;                            // 8 leaf PTEs
            spa_q      <= {pte_ppn(sel_pte), {OFFSET_W{1'b0}}}; // requested page SPA
            state      <= W_DONE;
          end else begin
            level_q <= level_q - 2'd1;
            state   <= W_ISSUE;
          end
        end
        W_DONE: if (done_ready) state <= W_IDLE;
        default: state <= W_IDLE;
      endcase
    end
  end
endmodule
