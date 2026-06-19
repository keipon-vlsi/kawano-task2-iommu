// Shared IOTLB testbench (iverilog -g2012 -DDUT=<variant>). Tag = VPN[2:0] 27b.
// Fills 16 contiguous pages (2 aligned lines x 8) with contiguous SPAs, checks hits + a miss.
`timescale 1ns/1ps
module iotlb_tb;
  logic clk=0, rst_n=0;
  logic [26:0] lk_tag, fill_tag;
  logic        lk_hit, fill_en;
  logic [43:0] lk_spa, fill_spa;
  int errors=0;
  localparam [26:0] BASE = 27'h000080;        // aligned 16-page window (>>4 == 0x8)
  localparam [43:0] SPA0 = 44'h50000;

  `DUT dut (.clk, .rst_n, .lk_tag, .lk_hit, .lk_spa, .fill_en, .fill_tag, .fill_spa);
  always #5 clk = ~clk;

  task automatic do_fill(input [26:0] t, input [43:0] s);
    @(negedge clk); fill_en=1; fill_tag=t; fill_spa=s; @(negedge clk); fill_en=0;
  endtask
  task automatic chk(input [26:0] t, input exp_hit, input [43:0] exp_spa);
    @(negedge clk); lk_tag=t; #1;
    if (lk_hit !== exp_hit) begin errors++; $display("  HIT t=%h got=%b exp=%b", t, lk_hit, exp_hit); end
    else if (exp_hit && lk_spa !== exp_spa) begin errors++; $display("  SPA t=%h got=%h exp=%h", t, lk_spa, exp_spa); end
  endtask

  int i;
  initial begin
    fill_en=0; lk_tag=0; fill_tag=0; fill_spa=0;
    @(negedge clk); rst_n=1;
    for (i=0;i<16;i++) do_fill(BASE+i, SPA0+i);     // contiguous fill (SPA = 0x50000+tag)
    for (i=0;i<16;i++) chk(BASE+i, 1, SPA0+i);      // all 16 hit with right SPA
    chk(27'h000100, 0, 44'h0);                      // out of window: miss
    if (errors==0) $display("IOTLB_TB PASS"); else $display("IOTLB_TB FAIL errors=%0d", errors);
    $finish;
  end
endmodule
