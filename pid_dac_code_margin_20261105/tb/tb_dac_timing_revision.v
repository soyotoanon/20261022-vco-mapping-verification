`timescale 1ns/1ps
module tb_dac_timing_revision;
reg clk=0; always #10 clk=~clk;
reg rst=0;
reg [13:0] d1=8192,d2=8192;
wire [13:0] q1,q2,snap;
wire c1,c2,w1,w2;
dac_drv dut(clk,rst,d1,d2,q1,c1,w1,q2,c2,w2,snap);
time data_time=0,wtime=0,ctime=0;
integer writes=0,updates=0,n,seed=71,old_count;
reg [13:0] latch1,latch2,analog1,analog2;
always @(q1 or q2) begin
 if(rst && wtime>0 && $time-wtime<20)$fatal(1,"data hold violation");
 data_time=$time;
end
always @(posedge w1) if(rst)begin
 if($time-data_time<20)$fatal(1,"data setup violation");
 if(c1!==0 || w1!==w2)$fatal(1,"invalid WRT phase");
 latch1=q1;latch2=q2;wtime=$time;writes=writes+1;
end
always @(negedge w1) if(rst && wtime>0)
 if($time-wtime<20)$fatal(1,"short WRT pulse");
always @(posedge c1) if(rst)begin
 if($time-wtime<20)$fatal(1,"CLK not separated from WRT");
 if(c1!==c2)$fatal(1,"channel clocks differ");
 analog1=latch1;analog2=latch2;ctime=$time;updates=updates+1;
 #1;if(snap!==analog1)$fatal(1,"snapshot not at DAC latch stage");
end
always @(negedge c1) if(rst && ctime>0)
 if($time-ctime<20)$fatal(1,"short CLK pulse");
task check_output;
begin
 if(analog1!==(d1<100?100:d1>16283?16283:d1) ||
    analog2!==(d2<100?100:d2>16283?16283:d2))
   $fatal(1,"DAC latch mismatch %h/%h requested %h/%h",analog1,analog2,d1,d2);
end
endtask
initial begin
 #30;@(negedge clk);rst=1;
 repeat(10)@(negedge clk);check_output;
 for(n=0;n<2000;n=n+1)begin
   d1=n[0]?14'h2000:14'h1fff;
   d2=n[0]?14'h3fff:14'h0000;
   repeat(10)@(negedge clk);check_output;
 end
 for(n=0;n<2000;n=n+1)begin
   d1=$random(seed);d2=$random(seed);
   repeat(10)@(negedge clk);check_output;
 end
 d1=8224;d2=8191;repeat(10)@(negedge clk);check_output;
 old_count=updates;repeat(100)@(negedge clk);
 if(updates!=old_count)$fatal(1,"static code causes unnecessary strobes");
 if(writes!=updates)$fatal(1,"unflushed input latch");
 repeat(50)begin d1=$random(seed);d2=$random(seed);@(negedge clk);end
 repeat(10)@(negedge clk);check_output;
 $display("REVISION_PASS tb_dac_timing_revision checked_5MSPS=4001 writes=%0d updates=%0d",writes,updates);
 $finish;
end
initial begin #2000000;$fatal(1,"timeout");end
endmodule
