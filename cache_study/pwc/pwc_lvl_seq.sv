// pwc_lvl_seq -- multi-level PWC lookup, SEQUENTIAL (probe leaf-nearest first, on miss
// advance to the next-higher level next cycle). Same storage as pwc_lvl_par (near L1 18b,
// mid L2 9b, root reg) but probed ONE LEVEL PER CYCLE via a small FSM:
//   cycle1 probe near; hit→done(near)   else
//   cycle2 probe mid ; hit→done(mid)    else
//   cycle3            →done(root)
// Cost: FSM + multi-cycle latency (1..3 cyc; ~1 in steady state since near usually hits);
// benefit: per-cycle logic is ONE level's compare (no priority mux), and only the active
// level's comparator output matters each cycle (lower per-access toggling -> dynamic power).
// req pulses to start; resp_valid pulses when resolved.
module pwc_lvl_seq (
  input  logic        clk, rst_n,
  input  logic        req,
  input  logic [26:0] lk_vpn,
  output logic        resp_valid,
  output logic [27:0] lk_base,
  output logic [1:0]  lk_lvl,
  input  logic        fn_en, input logic [17:0] fn_tag, input logic [27:0] fn_d,
  input  logic        fm_en, input logic [8:0]  fm_tag, input logic [27:0] fm_d,
  input  logic        fr_en, input logic [27:0] fr_d
);
  logic        nh, mh;
  logic [27:0] nd, md, root_q;
  logic [26:0] vpn_q;
  fa_cache #(.ENTRIES(2),.TAG_W(18),.DATA_W(28)) u_near (.clk,.rst_n,
     .lk_tag(vpn_q[26:9]), .lk_hit(nh), .lk_data(nd), .fill_en(fn_en),.fill_tag(fn_tag),.fill_data(fn_d));
  fa_cache #(.ENTRIES(2),.TAG_W(9), .DATA_W(28)) u_mid  (.clk,.rst_n,
     .lk_tag(vpn_q[26:18]),.lk_hit(mh), .lk_data(md), .fill_en(fm_en),.fill_tag(fm_tag),.fill_data(fm_d));
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) root_q <= '0; else if (fr_en) root_q <= fr_d;
  end

  typedef enum logic [1:0] {IDLE=2'd0, S_NEAR=2'd1, S_MID=2'd2, S_ROOT=2'd3} st_e;
  st_e state;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state<=IDLE; vpn_q<='0; resp_valid<=1'b0; lk_base<='0; lk_lvl<=2'd0;
    end else begin
      resp_valid <= 1'b0;
      case (state)
        IDLE:   if (req) begin vpn_q<=lk_vpn; state<=S_NEAR; end
        // probe ONE level per cycle; act only on that level's hit
        S_NEAR: if (nh) begin lk_base<=nd;     lk_lvl<=2'd2; resp_valid<=1'b1; state<=IDLE; end
                else state<=S_MID;
        S_MID:  if (mh) begin lk_base<=md;     lk_lvl<=2'd1; resp_valid<=1'b1; state<=IDLE; end
                else state<=S_ROOT;
        S_ROOT: begin       lk_base<=root_q; lk_lvl<=2'd0; resp_valid<=1'b1; state<=IDLE; end
        default: state<=IDLE;
      endcase
    end
  end
endmodule
