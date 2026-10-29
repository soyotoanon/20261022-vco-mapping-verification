`timescale 1ns/1ps
module tb_fast_equivalence;
 reg clk=0; always #10 clk=~clk;
 reg rst=0,tick=1,en=1,clr=0,load=0,dir=1,freeze=0;
 reg [13:0] y=8192,sp=8192,bias=8192,lo=0,hi=16383;
 reg [15:0] kp=16'h8900,ki=16'hc51f;
 reg [31:0] lim=8388608;
 wire [13:0] u,old_u,out,old_out;
 wire signed [15:0] err,old_err;
 wire signed [31:0] integ,old_integ;
 wire il,ul,old_il,old_ul;
 pi_ctrl #(.CURRENT_SAMPLE_I(0)) dut(clk,rst,tick,en,clr,load,y,sp,bias,kp,ki,lim,lo,hi,dir,freeze,u,err,integ,il,ul,1'b0,14'd8192);
 pi_ctrl_reference refpi(clk,rst,tick,en,clr,load,y,sp,bias,kp,ki,lim,lo,hi,dir,freeze,old_u,old_err,old_integ,old_il,old_ul);
 reg we=0,active=1;reg [5:0] addr=0;reg [15:0] wd=0;
 wire [15:0] rd,old_rd;wire hold_loop,old_hold,clip,old_clip;
 performance_probe #(.AW(4)) probe(clk,rst,tick,y,u,lo,hi,1'b0,active,we,addr,wd,rd,hold_loop,out,clip);
 performance_probe_reference #(.AW(4)) refprobe(clk,rst,tick,y,old_u,lo,hi,1'b0,active,we,addr,wd,old_rd,old_hold,old_out,old_clip);
 wire [13:0] dq,old_dq,ds,old_ds;wire dc,dw,old_dc,old_dw;
 dac_drv dac(.sys_clk(clk),.sys_rst_n(rst),.data1(out),.data2(out),.da1_data(dq),.da1_clk(dc),.da1_wrt(dw),.dac_snapshot(ds));
 dac_drv old_dac(.sys_clk(clk),.sys_rst_n(rst),.data1(old_out),.data2(old_out),.da1_data(old_dq),.da1_clk(old_dc),.da1_wrt(old_dw),.dac_snapshot(old_ds));
 integer n,j,seed=271029,checks=0;
 longint signed prev_i,l;
 reg expected_il;
 task cycle;
 begin
   prev_i=integ; l=lim>32'h7fffffff ? 2147483647 : lim;
   expected_il=(load||clr)?(l==0):(prev_i>=l||prev_i<=-l);
   @(posedge clk);#1;
   if(u!==old_u||integ!==old_integ||err!==old_err||ul!==old_ul)
     $fatal(1,"PI mismatch check=%0d U=%0d/%0d I=%0d/%0d",checks,u,old_u,integ,old_integ);
   if(il!==expected_il)$fatal(1,"status retiming mismatch");
   if(out!==old_out||hold_loop!==old_hold||clip!==old_clip)
     $fatal(1,"probe mismatch check=%0d mode=%d out=%0d/%0d",checks,probe.control,out,old_out);
   if(dq!==old_dq||dc!==old_dc||dw!==old_dw||ds!==old_ds)
     $fatal(1,"DAC value or cycle latency changed");
   checks=checks+1;@(negedge clk);
 end
 endtask
 initial begin
   #1;rst=0;#24;rst=1;@(negedge clk);
   // Exhaust every representable input error with fixed and legacy gain modes.
   for(j=0;j<3;j=j+1) begin
     kp=j==0?16'hffff:j==1?16'h8900:16'd0;
     ki=j==0?16'hffff:j==1?16'hc51f:16'd0;
     for(n=-16383;n<=16383;n=n+1) begin
       sp=n>=0?n:0;y=n>=0?0:-n;load=(n%101)==0;cycle;
     end
   end
   // Exhaust all fixed-gain words at full error, alternate plant polarity.
   load=0;sp=16383;y=0;lim=32'h7fffffff;
   for(n=0;n<32768;n=n+1)begin kp=16'h8000|n;ki=16'hffff-n;dir=n[0];cycle;end
   // Runtime clamps (including reversed ranges), enable, freeze and all modes.
   for(n=0;n<20000;n=n+1)begin
     sp=$random(seed);y=$random(seed);bias=$random(seed);kp=$random(seed);ki=$random(seed);
     dir=$random(seed);tick=$random(seed);en=$random(seed);active=$random(seed);
     freeze=($random(seed)&7)==0;load=($random(seed)&31)==0;clr=($random(seed)&31)==0;
     if(n%7==0)begin lo=$random(seed);hi=$random(seed);lim=$random(seed);end
     we=(n%11)==0;addr=(n%3)==0?6'h30:(n%3)==1?6'h31:6'h33;wd=$random(seed);
     cycle;
   end
   $display("REVISION_PASS fast_equivalence checks=%0d PI/DAC cycle-exact; status-only retimed",checks);$finish;
 end
 initial begin #20000000;$fatal(1,"timeout");end
endmodule
