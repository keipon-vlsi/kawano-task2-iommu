// line_iotlb.sv -- leaf IOTLB specialized for the coalesced, sequential-IOVA stream.
//
// Port-compatible with fa_cache, but instead of a flat ENTRIES-way CAM it holds NLINES
// "line slots", each caching one coalesced 64 B leaf line = PAGES contiguous pages. A
// VPN is split into {line_tag = VPN[hi:OFF_W], offset = VPN[OFF_W-1:0]} where offset is
// the page-within-line index (OFF_W = clog2(PAGES)). Lookup is then:
//   * NLINES line-tag compares (LTW bits each)  -- NOT ENTRIES full-VPN compares,
//   * an offset-driven PAGES:1 data mux         -- direct index, no match decode.
// This collapses the 16-way (8+8) VPN CAM into 2 line-tag compares + an 8:1 offset mux,
// taking the compare cone (the post-v5 critical path: bvpn_q -> IOTLB CAM -> stg) off the
// critical path. It exploits the workload: leaf PTEs of contiguous IOVA are contiguous,
// so the IOTLB content IS lines of PAGES contiguous pages (the engine fills it per burst
// beat). For non-sequential IOVA it still works as a small line cache; the walk fallback
// is unchanged, so deviations only slow down (never break) -- per CLAUDE.md robustness.
//
// Fill: per-beat (one page) like fa_cache. A beat whose line_tag matches an allocated
// slot writes that slot's data[offset] + sets its per-page valid; otherwise it allocates
// the round-robin victim slot (set line_tag, clear all per-page valids, write this page).
module line_iotlb #(
  parameter int NLINES = 2,
  parameter int PAGES  = 8,
  parameter int TAG_W  = 27,    // full lookup tag = {ctx?, VPN}; offset = low clog2(PAGES) bits
  parameter int DATA_W = 48
)(
  input  logic               clk,
  input  logic               rst_n,

  // combinational lookup
  input  logic [TAG_W-1:0]   lk_tag,
  output logic               lk_hit,
  output logic [DATA_W-1:0]  lk_data,

  // single-page fill (per burst beat); allocates a line slot on a new line_tag
  input  logic               fill_en,
  input  logic [TAG_W-1:0]   fill_tag,
  input  logic [DATA_W-1:0]  fill_data
);
  localparam int OFF_W = (PAGES < 2) ? 1 : $clog2(PAGES);   // page-within-line index bits
  localparam int LTW   = TAG_W - ((PAGES < 2) ? 0 : OFF_W); // line-tag width
  localparam int VW    = (NLINES < 2) ? 1 : $clog2(NLINES); // victim pointer width

  logic [LTW-1:0]    ltag_q   [NLINES];
  logic              lvalid_q [NLINES];
  logic [PAGES-1:0]  subv_q   [NLINES];           // per-page valid within the slot
  logic [DATA_W-1:0] data_q   [NLINES][PAGES];
  logic [VW-1:0]     vptr_q;                        // round-robin victim

  // ---- split lookup tag into {line_tag, offset} ----
  logic [OFF_W-1:0] lk_off;  logic [LTW-1:0] lk_lt;
  assign lk_off = lk_tag[OFF_W-1:0];
  assign lk_lt  = lk_tag[TAG_W-1:OFF_W];

  // ---- NLINES line-tag compares + offset-indexed data mux ----
  logic [NLINES-1:0] lmatch;
  always_comb begin
    for (int k = 0; k < NLINES; k++)
      lmatch[k] = lvalid_q[k] & (ltag_q[k] == lk_lt) & subv_q[k][lk_off];
  end
  assign lk_hit = |lmatch;
  always_comb begin
    lk_data = '0;
    for (int k = NLINES-1; k >= 0; k--)
      if (lmatch[k]) lk_data = data_q[k][lk_off];   // offset drives the PAGES:1 mux
  end

  // ---- fill: match an allocated slot by line_tag, else take the victim ----
  logic [OFF_W-1:0] f_off;  logic [LTW-1:0] f_lt;
  assign f_off = fill_tag[OFF_W-1:0];
  assign f_lt  = fill_tag[TAG_W-1:OFF_W];
  logic            f_exist;  logic [VW-1:0] f_slot;
  always_comb begin
    f_exist = 1'b0; f_slot = '0;
    for (int k = NLINES-1; k >= 0; k--)
      if (lvalid_q[k] & (ltag_q[k] == f_lt)) begin f_exist = 1'b1; f_slot = VW'(k); end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int k = 0; k < NLINES; k++) begin
        lvalid_q[k] <= 1'b0; ltag_q[k] <= '0; subv_q[k] <= '0;
        for (int p = 0; p < PAGES; p++) data_q[k][p] <= '0;
      end
      vptr_q <= '0;
    end else if (fill_en) begin
      if (f_exist) begin                       // same line already allocated: add this page
        subv_q[f_slot][f_off] <= 1'b1;
        data_q[f_slot][f_off] <= fill_data;
      end else begin                           // new line: allocate victim slot
        lvalid_q[vptr_q] <= 1'b1;
        ltag_q[vptr_q]   <= f_lt;
        subv_q[vptr_q]   <= (PAGES'(1) << f_off);   // only this page valid so far
        data_q[vptr_q][f_off] <= fill_data;
        vptr_q <= (vptr_q == VW'(NLINES-1)) ? '0 : (vptr_q + VW'(1));
      end
    end
  end
endmodule
