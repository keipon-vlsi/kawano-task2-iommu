// Shared PWC testbench (iverilog -g2012 -DDUT=<variant>). Functional sanity:
// sequential (contiguous) fill+lookup, plus a random-tag check. Tag = VPN[2:1] 18b.
`timescale 1ns/1ps
module pwc_tb;
  logic clk=0, rst_n=0;
  logic [17:0] lk_tag, fill_tag;
  logic        lk_hit, fill_en;
  logic [43:0] lk_spa, fill_spa;
  int errors=0;

  `DUT dut (.clk, .rst_n, .lk_tag, .lk_hit, .lk_spa, .fill_en, .fill_tag, .fill_spa);
  always #5 clk = ~clk;

  task automatic do_fill(input [17:0] t, input [43:0] s);
    @(negedge clk); fill_en=1; fill_tag=t; fill_spa=s; @(negedge clk); fill_en=0;
  endtask
  task automatic chk(input [17:0] t, input exp_hit, input [43:0] exp_spa);
    @(negedge clk); lk_tag=t; #1;
    if (lk_hit !== exp_hit) begin errors++; $display("  MISS-HIT t=%h got_hit=%b exp=%b", t, lk_hit, exp_hit); end
    else if (exp_hit && lk_spa !== exp_spa) begin errors++; $display("  BAD-SPA t=%h got=%h exp=%h", t, lk_spa, exp_spa); end
  endtask

  initial begin
    fill_en=0; lk_tag=0; fill_tag=0; fill_spa=0;
    @(negedge clk); rst_n=1;
    // sequential window: fill two adjacent tags (contiguous IOVA stream)
    do_fill(18'h00010, 44'hAAAA0);
    do_fill(18'h00011, 44'hAAAA1);
    chk(18'h00010, 1, 44'hAAAA0);
    chk(18'h00011, 1, 44'hAAAA1);
    chk(18'h00020, 0, 44'h0);           // not present
    // refill (replace) and re-check
    do_fill(18'h00012, 44'hBBBB2);
    chk(18'h00012, 1, 44'hBBBB2);
    if (errors==0) $display("PWC_TB PASS"); else $display("PWC_TB FAIL errors=%0d", errors);
    $finish;
  end
endmodule
