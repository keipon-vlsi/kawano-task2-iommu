// fa_cache.sv -- fully-associative DFF cache (CAM). Used for every PWC level and the
// IOTLB. ALL critical-path lookup logic is real RTL (the point of this task):
//   * parallel tag compare across ALL entries (CAM),
//   * priority encoder over the one-hot match vector,
//   * base mux tree selecting the stored data of the winning entry.
// Storage is flip-flops (small, fully-associative). Single fill port (1 write/cycle);
// coalesced multi-entry fills are sequenced by the engine, 1 entry/cycle.
//
// Replacement: round-robin / FIFO victim pointer (replacement policy is near-moot for
// monotonic streaming IOVA -- see CLAUDE.md; structural per-level separation is the
// real lever, which the engine provides by instancing one fa_cache per level).
module fa_cache #(
  parameter int ENTRIES = 2,
  parameter int TAG_W   = 9,
  parameter int DATA_W  = 28
)(
  input  logic               clk,
  input  logic               rst_n,

  // combinational lookup
  input  logic [TAG_W-1:0]   lk_tag,
  output logic               lk_hit,
  output logic [DATA_W-1:0]  lk_data,

  // single-entry fill (round-robin victim)
  input  logic               fill_en,
  input  logic [TAG_W-1:0]   fill_tag,
  input  logic [DATA_W-1:0]  fill_data
);
  localparam int PTR_W = (ENTRIES < 2) ? 1 : $clog2(ENTRIES);

  logic                valid_q [ENTRIES];
  logic [TAG_W-1:0]    tag_q   [ENTRIES];
  logic [DATA_W-1:0]   data_q  [ENTRIES];
  logic [PTR_W-1:0]    vptr_q;             // round-robin victim pointer

  // ---- CAM: parallel tag compare across all entries ----
  logic [ENTRIES-1:0]  match;
  always_comb begin
    for (int i = 0; i < ENTRIES; i++)
      match[i] = valid_q[i] & (tag_q[i] == lk_tag);
  end
  assign lk_hit = |match;

  // ---- priority encoder (lowest matching index wins) + base data mux tree ----
  always_comb begin
    lk_data = '0;
    for (int i = ENTRIES-1; i >= 0; i--)
      if (match[i]) lk_data = data_q[i];   // lowest index has final say
  end

  // ---- fill + replacement pointer ----
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int i = 0; i < ENTRIES; i++) begin
        valid_q[i] <= 1'b0;
        tag_q[i]   <= '0;
        data_q[i]  <= '0;
      end
      vptr_q <= '0;
    end else if (fill_en) begin
      valid_q[vptr_q] <= 1'b1;
      tag_q[vptr_q]   <= fill_tag;
      data_q[vptr_q]  <= fill_data;
      vptr_q <= (vptr_q == PTR_W'(ENTRIES-1)) ? '0 : (vptr_q + PTR_W'(1));
    end
  end
endmodule
