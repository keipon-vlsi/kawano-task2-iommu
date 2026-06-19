// pwc_p3 -- sequential / sliding pointer. A "current" pointer marks the hot entry; the
// stream is expected to hit it. Lookup compares current first (priority); a hit on the
// other entry promotes it to current (boundary advance). BET on sequential streams (the
// common case resolves on the current comparator). Fallback: a true miss (neither entry)
// returns hit=0 (correct). Worst-case datapath is still 2 compares -> ~FA depth; the
// pointer mainly helps the common-case / replacement order, not worst-case logic depth.
module pwc_p3 (
  input  logic clk, rst_n,
  input  logic [17:0] lk_tag,
  output logic        lk_hit,
  output logic [43:0] lk_spa,
  input  logic        fill_en,
  input  logic [17:0] fill_tag,
  input  logic [43:0] fill_spa
);
  logic        cur_q;                 // current (hot) entry index
  logic        v_q   [2];
  logic [17:0] tag_q [2];
  logic [43:0] spa_q [2];

  logic m_cur, m_oth;
  assign m_cur = v_q[cur_q]  & (tag_q[cur_q]  == lk_tag);
  assign m_oth = v_q[~cur_q] & (tag_q[~cur_q] == lk_tag);
  assign lk_hit = m_cur | m_oth;
  assign lk_spa = m_cur ? spa_q[cur_q] : spa_q[~cur_q];

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin cur_q<=0; v_q[0]<=0; v_q[1]<=0; tag_q[0]<='0; tag_q[1]<='0; spa_q[0]<='0; spa_q[1]<='0; end
    else begin
      if (fill_en) begin                       // fill the non-current entry, then promote it
        tag_q[~cur_q] <= fill_tag; spa_q[~cur_q] <= fill_spa; v_q[~cur_q] <= 1'b1; cur_q <= ~cur_q;
      end
    end
  end
endmodule
