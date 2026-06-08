// cache_store.sv -- parameterized associative cache storage wrapper.
//
// One clean wrapper backs every translation cache (IOTLB / S1 PWC / S2 PWC /
// table_gpa / root_gpa / DDT$ / PDT$). The storage *pattern* is just parameters:
//   ENTRIES, ASSOC (1=direct, N=N-way, ENTRIES=fully-assoc/CAM), STORAGE (ff|sram).
//
// Lookup is registered (1-cycle latency) so ff/cam and sram caches share timing;
// the walk/lookup pipeline stage is therefore uniform. Fills use a per-entry
// write-enable (clock-gating friendly). A generation counter gives O(1) flush
// (invalidation hook; happy-path only here).
//
// STORAGE note (Phase 1): functional behaviour is identical for ff/sram; the
// parameter is recorded and a `ram_style` attribute hints synthesis (sram ->
// memory/RAM, ff -> flops). True synchronous-SRAM read timing is a later refinement
// (see ASSUMPTIONS.md).
module cache_store #(
  parameter int ENTRIES = 16,
  parameter int ASSOC   = 16,          // ENTRIES => fully associative (CAM)
  parameter int KEY_W   = 32,
  parameter int DATA_W  = 32,
  parameter int STORAGE = 0            // iommu_pkg::ST_FF / ST_SRAM
)(
  input  logic               clk,
  input  logic               rst_n,

  // --- lookup (result valid the cycle after lookup_en) ---
  input  logic               lookup_en,
  input  logic [KEY_W-1:0]   lookup_key,
  output logic               hit,        // registered
  output logic [DATA_W-1:0]  rdata,      // registered

  // --- fill (allocate / overwrite) ---
  input  logic               fill_en,
  input  logic [KEY_W-1:0]   fill_key,
  input  logic [DATA_W-1:0]  fill_data,

  // --- invalidation (generation bump = O(1) flush-all) ---
  input  logic               inval_all
);
  // localparams: derive set/way geometry
  localparam int WAYS     = (ASSOC >= ENTRIES) ? ENTRIES : ASSOC;
  localparam int NUM_SETS = (ASSOC >= ENTRIES) ? 1       : (ENTRIES / ASSOC);
  localparam int SET_W    = (NUM_SETS <= 1)    ? 1       : $clog2(NUM_SETS);
  localparam int WAY_W    = (WAYS <= 1)        ? 1       : $clog2(WAYS);

  // storage arrays. STORAGE (ff/sram) is recorded; the synth flow applies the
  // memory-mapping policy (flops vs RAM) per cache (see syn/, ASSUMPTIONS.md).
  logic [DATA_W-1:0] data_arr [ENTRIES];
  logic [KEY_W-1:0]  key_arr  [ENTRIES];
  logic              valid_arr[ENTRIES];

  // per-set round-robin victim pointer
  logic [WAY_W-1:0]  rr_ptr [NUM_SETS];

  // set index from key (low bits); fully-assoc -> single set 0
  function automatic int unsigned set_idx(input logic [KEY_W-1:0] k);
    if (NUM_SETS <= 1) return 0;
    return int'(k[SET_W-1:0]) % NUM_SETS;
  endfunction

  // --- combinational associative compare over the indexed set ---
  logic                comb_hit;
  logic [DATA_W-1:0]   comb_data;
  always_comb begin
    automatic int unsigned s = set_idx(lookup_key);
    comb_hit  = 1'b0;
    comb_data = '0;
    for (int w = 0; w < WAYS; w++) begin
      automatic int unsigned e = s * WAYS + w;
      if (valid_arr[e] && (key_arr[e] == lookup_key)) begin
        comb_hit  = 1'b1;
        comb_data = data_arr[e];
      end
    end
  end

  // registered lookup result (uniform 1-cycle latency)
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      hit   <= 1'b0;
      rdata <= '0;
    end else begin
      hit   <= lookup_en && comb_hit;
      rdata <= comb_data;
    end
  end

  // --- fill: hit-update, else fill an invalid way, else round-robin victim ---
  function automatic int unsigned victim_way(input int unsigned s);
    // prefer an invalid way in the set
    for (int w = 0; w < WAYS; w++)
      if (!valid_arr[s*WAYS + w]) return w;
    return rr_ptr[s];
  endfunction

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int i = 0; i < ENTRIES; i++) valid_arr[i] <= 1'b0;
      for (int s = 0; s < NUM_SETS; s++) rr_ptr[s] <= '0;
    end else if (inval_all) begin
      for (int i = 0; i < ENTRIES; i++) valid_arr[i] <= 1'b0;  // O(1) flush hook
    end else if (fill_en) begin
      automatic int unsigned s = set_idx(fill_key);
      automatic int unsigned hw = WAYS;            // existing way if key present
      automatic int unsigned w_sel;
      automatic int unsigned e_sel;
      for (int w = 0; w < WAYS; w++)
        if (valid_arr[s*WAYS + w] && (key_arr[s*WAYS + w] == fill_key)) hw = w;
      w_sel = (hw != WAYS) ? hw : victim_way(s);
      e_sel = s*WAYS + w_sel;
      valid_arr[e_sel] <= 1'b1;                     // per-entry write-enable
      key_arr  [e_sel] <= fill_key;
      data_arr [e_sel] <= fill_data;
      if (hw == WAYS && WAYS > 1)                   // advance victim only on alloc
        rr_ptr[s] <= (rr_ptr[s] == WAY_W'(WAYS-1)) ? '0 : rr_ptr[s] + 1'b1;
    end
  end
endmodule
