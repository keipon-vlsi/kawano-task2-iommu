`timescale 1ns/1ps
module iotlb_align_tb;
  logic clk=0, rst_n=0;
  logic [26:0] lk_tag, fill_tag; logic lk_hit, fill_en; logic [43:0] lk_spa, fill_spa;
  int hits=0;
  `DUT dut (.clk,.rst_n,.lk_tag,.lk_hit,.lk_spa,.fill_en,.fill_tag,.fill_spa);
  always #5 clk=~clk;
  task automatic dofill(input [26:0] t,input [43:0] s);
    @(negedge clk); fill_en=1; fill_tag=t; fill_spa=s; @(negedge clk); fill_en=0; endtask
  task automatic chk(input [26:0] t); @(negedge clk); lk_tag=t; #1;
    if(lk_hit) hits++; $display("  tag=%h -> hit=%b", t, lk_hit); endtask
  int i; localparam [26:0] B=27'h000085;   // NON-16-aligned start (0x85>>4=8, crosses 0x90)
  initial begin
    fill_en=0; lk_tag=0; @(negedge clk); rst_n=1;
    for(i=0;i<16;i++) dofill(B+i, 44'h60000+i);   // 16 contiguous pages 0x85..0x94
    $display("after filling 16 contiguous pages 0x85..0x94:");
    for(i=0;i<16;i++) chk(B+i);
    $display("HITS = %0d / 16", hits);
    $finish;
  end
endmodule
