// walker.sv -- one page-table-walk context (FSM).
//
// A walker is "a small state box + the right to hold one outstanding memory read"
// (design_premises). It executes a fetch *plan* handed to it by the front-end:
// `n_reads` chained, tagged PTE reads (the residual after PWC/IOTLB short-circuit),
// then composes the SPA from the final (leaf) PTE and reports completion with the
// MSHR id so all coalesced requests for the line complete together.
//
// Happy-path only: every PTE is valid; no faults. Read addresses encode
// {level, vpn} so the testbench memory stub can return a deterministic PTE.
import iommu_pkg::*;

module walker #(
  parameter int ADDR_W = GPA_W,
  parameter int DATA_W = PPN_W,
  parameter int TAG_W  = 4,
  parameter int MSHR_W = 6,
  parameter int MAXRD_W = 4              // width of the read counter
)(
  input  logic              clk,
  input  logic              rst_n,
  input  logic [TAG_W-1:0]  walker_id,   // becomes the memory tag

  // --- dispatch (from front-end) ---
  input  logic              disp_valid,
  output logic              disp_ready,
  input  logic [VPN_W-1:0]  disp_vpn,
  input  logic [MAXRD_W-1:0]disp_nreads, // >=1 (at least the leaf line)
  input  logic [MSHR_W-1:0] disp_mshr,

  // --- memory request/response (to the arbiter) ---
  output logic              mreq_valid,
  input  logic              mreq_ready,
  output logic [ADDR_W-1:0] mreq_addr,
  output logic [TAG_W-1:0]  mreq_tag,
  input  logic              mrsp_valid,  // routed to this walker by the engine
  input  logic [DATA_W-1:0] mrsp_data,

  // --- completion (to the front-end / MSHR) ---
  output logic              done_valid,
  input  logic              done_ready,
  output logic [MSHR_W-1:0] done_mshr,
  output logic [SPA_W-1:0]  done_spa,
  output logic              busy
);
  typedef enum logic [1:0] {W_IDLE, W_ISSUE, W_WAIT, W_DONE} state_e;
  state_e state;

  logic [VPN_W-1:0]   vpn_q;
  logic [MSHR_W-1:0]  mshr_q;
  logic [MAXRD_W-1:0] left_q;     // reads remaining
  logic [MAXRD_W-1:0] done_cnt;   // reads completed (= level index)
  logic [DATA_W-1:0]  last_q;     // last PTE returned (final = leaf ppn)

  assign busy       = (state != W_IDLE);
  assign disp_ready = (state == W_IDLE);
  assign mreq_tag   = walker_id;
  // address encodes the level (read index) and vpn so the stub mem is deterministic
  assign mreq_addr  = ADDR_W'({done_cnt, vpn_q});
  assign mreq_valid = (state == W_ISSUE);

  assign done_valid = (state == W_DONE);
  assign done_mshr  = mshr_q;
  assign done_spa   = {last_q[PPN_W-1:0], {OFFSET_W{1'b0}}};

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state    <= W_IDLE;
      vpn_q    <= '0; mshr_q <= '0; left_q <= '0; done_cnt <= '0; last_q <= '0;
    end else begin
      case (state)
        W_IDLE: if (disp_valid) begin
          vpn_q    <= disp_vpn;
          mshr_q   <= disp_mshr;
          left_q   <= (disp_nreads == '0) ? MAXRD_W'(1) : disp_nreads;
          done_cnt <= '0;
          state    <= W_ISSUE;
        end
        W_ISSUE: if (mreq_ready) state <= W_WAIT;       // AR accepted
        W_WAIT:  if (mrsp_valid) begin                  // R returned for this walker
          last_q   <= mrsp_data;
          done_cnt <= done_cnt + 1'b1;
          if (left_q == MAXRD_W'(1)) state <= W_DONE;   // last read done
          else begin left_q <= left_q - 1'b1; state <= W_ISSUE; end
        end
        W_DONE:  if (done_ready) state <= W_IDLE;
        default: state <= W_IDLE;
      endcase
    end
  end
endmodule
