`timescale 1ns/1ps
module tb; logic clk=0,rst_n=0; logic [26:0] v; logic [27:0] b; logic [1:0] l;
 logic fn,fm,fr; logic [17:0] fnt; logic [8:0] fmt; logic [27:0] fnd,fmd,frd; int e=0;
 qor_top d(.clk,.rst_n,.lk_vpn_i(v),.base_o(b),.lvl_o(l),.fn_en(fn),.fn_tag(fnt),.fn_d(fnd),
   .fm_en(fm),.fm_tag(fmt),.fm_d(fmd),.fr_en(fr),.fr_d(frd));
 always #5 clk=~clk;
 task fill(input int w,input [17:0] t,input [27:0] dd); begin @(negedge clk);
   fn=0;fm=0;fr=0; if(w==2)begin fn=1;fnt=t;fnd=dd;end if(w==1)begin fm=1;fmt=t[8:0];fmd=dd;end
   if(w==0)begin fr=1;frd=dd;end @(negedge clk); fn=0;fm=0;fr=0; end endtask
 task chk(input [26:0] vv,input [1:0] el,input [27:0] eb); begin @(negedge clk); v=vv;
   @(posedge clk); @(posedge clk); #1;   // 2-stage wrapper: vq then bq
   if(l!==el||b!==eb)begin e++; $display("  MISS v=%h l=%0d/%0d b=%h/%h",vv,l,el,b,eb);end end endtask
 initial begin fn=0;fm=0;fr=0; @(negedge clk); rst_n=1;
   fill(0,0,28'h7000);                 // root
   fill(1,18'h0AA,28'h1100);           // L2 tag VPN[26:18]=0xAA  -> for vpn[26:18]=0x0AA
   fill(2,18'h2AAAA,28'h2200);         // L1 tag VPN[26:9]=0x2AAAA
   // vpn whose [26:9]=0x2AAAA AND [26:18]=0x155 ... build a vpn that hits L1
   chk({18'h2AAAA,9'h0},2'd2,28'h2200);          // L1 hit (most complete) -> near
   chk({9'h0AA,18'h001},2'd1,28'h1100);          // [26:18]=0x0AA hits L2, [26:9]!=2AAAA -> mid
   chk(27'h7FFFFFF,2'd0,28'h7000);               // neither -> root
   if(e==0)$display("PWC_LVL_PAR PASS"); else $display("PWC_LVL_PAR FAIL %0d",e); $finish; end
endmodule