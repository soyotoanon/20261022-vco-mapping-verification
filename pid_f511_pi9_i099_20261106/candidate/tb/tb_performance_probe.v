`timescale 1ns/1ps
module tb_performance_probe;
 reg clk=0;always #10 clk=~clk;
 reg rst=0,valid=0,we=0,fault=0,active=1;
 reg [13:0] sample=8300,base=7000,lo=1000,hi=15000;
 reg [5:0] addr=0;reg [15:0] wd=0;
 wire [15:0] rd;wire hold_loop,clip;wire [13:0] output_code;
 performance_probe #(.AW(8)) dut(clk,rst,valid,sample,base,lo,hi,fault,active,we,addr,wd,rd,hold_loop,output_code,clip);
 integer failures=0,i,j,peak,trough;
 task write_reg;input [5:0] a;input [15:0] v;begin
  @(negedge clk);addr=a;wd=v;we=1;
  @(negedge clk);we=0;
 end endtask
 task tick;begin
  @(negedge clk);valid=1;
  @(negedge clk);valid=0;
  repeat(8) @(negedge clk);
 end endtask
 task check;input condition;input [255:0] label;begin
  if(!condition)begin $display("FAIL %s",label);failures=failures+1;end
 end endtask
 initial begin
  repeat(5)@(negedge clk);rst=1;
  for(j=0;j<=4;j=j+2)begin
   write_reg(6'h35,j);write_reg(6'h34,1);
   repeat((270<<j))tick();
   addr=6'h36;#1;check(rd[1]&&!rd[0],"capture completes");
   for(i=0;i<256;i=i+1)begin
    write_reg(6'h37,i);repeat(2)@(negedge clk);addr=6'h3B;#1;
    check(rd==33200,"CIC DC gain / BRAM");
   end
  end
  write_reg(6'h30,2);base=7200;#1;
  check(hold_loop&&output_code==7000,"hold captured operating point");
  write_reg(6'h31,100);write_reg(6'h32,0);write_reg(6'h33,16'h0400);write_reg(6'h30,3);
  peak=0;trough=16383;
  repeat(128)begin tick();if(output_code>peak)peak=output_code;if(output_code<trough)trough=output_code;end
  check(peak>=7098&&peak<=7100&&trough>=6900&&trough<=6902,"sine around held baseline");
  write_reg(6'h30,0);repeat(5)tick();check(output_code==7200&&!hold_loop,"release hold");
  base=14999;write_reg(6'h30,1);repeat(128)tick();addr=6'h36;#1;
  check(rd[2],"clipping latched");check(output_code<=15000,"output remains bounded");
  write_reg(6'h30,4);addr=6'h36;#1;check(!rd[2],"clipping reset");
  write_reg(6'h34,1);repeat(10)tick();write_reg(6'h34,2);addr=6'h36;#1;
  check(!rd[1]&&!rd[0],"abort does not mark complete");
  if(failures) $display("FAIL total=%0d",failures);
  else $display("REVISION_PASS performance_probe");
  $finish;
 end
 initial begin #100000000;$display("FAIL TIMEOUT");$finish;end
endmodule
