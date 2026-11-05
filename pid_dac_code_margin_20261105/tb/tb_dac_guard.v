`timescale 1ns/1ps
module tb_dac_guard;
  reg clk=0; always #10 clk=~clk;
  reg rst=0, we=0;
  reg [5:0] addr=0;
  reg [15:0] wdata=0;
  wire [15:0] rdata,lo,hi,sp,kp,ki;
  reg [13:0] d1=8192,d2=8192;
  wire [13:0] q1,q2,snap;
  wire c1,c2,w1,w2;
  integer n, writes=0, old_writes;

  reg_file regs(.sys_clk(clk),.sys_rst_n(rst),.we(we),.addr(addr),
    .wdata(wdata),.rdata(rdata),.out_min(lo),.out_max(hi),
    .setpoint(sp),.kp_shift(kp),.ki_shift(ki));
  dac_drv dut(.sys_clk(clk),.sys_rst_n(rst),.data1(d1),.data2(d2),
    .da1_data(q1),.da1_clk(c1),.da1_wrt(w1),.da2_data(q2),
    .da2_clk(c2),.da2_wrt(w2),.dac_snapshot(snap));
  always @(posedge w1) if(rst) writes=writes+1;

  function [13:0] bounded(input [13:0] d);
    bounded=d<100?100:d>16283?16283:d;
  endfunction
  task write_check(input [5:0] a,input [15:0] value,input [15:0] expected);
  begin
    @(negedge clk); addr=a;wdata=value;we=1;
    @(negedge clk); we=0;#1;
    if(rdata!==expected) $fatal(1,"register %h read %d expected %d",a,rdata,expected);
    if((a==6'h1e && lo!==expected)||(a==6'h1f && hi!==expected))
      $fatal(1,"effective PI limit does not match readback");
  end
  endtask
  initial begin
    #30;@(negedge clk);rst=1;repeat(10)@(negedge clk);
    if(lo!==100||hi!==16283) $fatal(1,"wrong startup rails");
    if(sp!==8476||kp!==35072||ki!==50463) $fatal(1,"baseline PI profile changed");
    write_check(6'h1e,0,100); write_check(6'h1e,99,100);
    write_check(6'h1e,100,100); write_check(6'h1e,1400,1400);
    write_check(6'h1e,16283,16283); write_check(6'h1e,16383,16283);
    write_check(6'h1e,65535,16283); write_check(6'h1e,100,100);
    write_check(6'h1f,0,100); write_check(6'h1f,100,100);
    write_check(6'h1f,15000,15000); write_check(6'h1f,16283,16283);
    write_check(6'h1f,16284,16283); write_check(6'h1f,65535,16283);
    write_check(6'h18,2500,2500);write_check(6'h18,16383,16383);

    // Sweep every request code on both channels in opposite directions.
    for(n=0;n<16384;n=n+1) begin
      d1=n;d2=16383-n;repeat(5)@(negedge clk);#1;
      if(q1!==bounded(d1)||q2!==bounded(d2)||snap!==bounded(d1))
        $fatal(1,"guard failed code=%d q1=%d q2=%d snap=%d",n,q1,q2,snap);
    end
    d1=0;d2=16383;repeat(10)@(negedge clk);old_writes=writes;
    for(n=1;n<100;n=n+1) begin d1=n;d2=16383-n;repeat(5)@(negedge clk);end
    if(writes!=old_writes) $fatal(1,"repeated out-of-range requests rewrote same rails");
    $display("REVISION_PASS tb_dac_guard 16384_codes both_channels register_limits_and_runtime_target");
    $finish;
  end
  initial begin #5000000;$fatal(1,"timeout");end
endmodule
