`timescale 1ns/1ps
module tb_instant_display_revision;
reg clk=0; always #10 clk=~clk;
reg rst_n=0, sample_valid=0, feedback_channel=0, otr=0;
reg [13:0] pi_code=8998;
wire [5:0] sel;
wire [7:0] seg;
wire [19:0] power;
wire valid,frame_valid;
wire [23:0] bcd;
power_display_piecewise #(.UPDATE_CYCLES(1000),.SCAN_DIV(8),.STALE_CYCLES(1500)) dut(
 .clk(clk),.rst_n(rst_n),.pi_code(pi_code),.sample_valid(sample_valid),
 .feedback_channel(feedback_channel),.otr(otr),.seg_sel_n(sel),.seg_n(seg),
 .power_units(power),.display_valid(valid),.display_bcd(bcd),.display_frame_valid(frame_valid));

task check(input bit ok,input string label);
 if(!ok) $fatal(1,"FAIL %s power=%d valid=%d BCD=%h",label,power,valid,bcd);
endtask
task sample(input integer code);
 @(negedge clk);pi_code=code;sample_valid=1;
 @(negedge clk);sample_valid=0;
endtask
integer n;
reg [5:0] seen;
initial begin
 repeat(5) @(negedge clk);rst_n=1;
 sample(8998);
 repeat(8) @(negedge clk);
 check(valid && power==59993,"first single sample displayed without averaging latency");
 repeat(120) @(negedge clk);
 check(frame_valid && bcd==24'h059993,"calibrated six digit BCD");
 seen=0;
 for(n=0;n<60;n=n+1) begin
   @(negedge clk);
   if(sel!=6'h3f) begin
     check(seg[7]==(sel!=6'b101111),"decimal point after second left digit");
     seen=seen | ~sel;
   end
 end
 check(seen==6'h3f,"all six digits scanned");
 // Many differing samples must not be accumulated or change the held display.
 for(n=0;n<50;n=n+1) sample(n%2 ? 8900 : 9100);
 check(power==59993,"hold between 10 Hz updates");
 wait(dut.update_count==0);
 sample(9003);
 repeat(8) @(negedge clk);
 check(valid && power==60407,"next display uses last fresh sample, not mean of intervening samples");
 repeat(120) @(negedge clk);
 check(frame_valid && bcd==24'h060407,"BCD updates to new instantaneous value");
 wait(dut.update_count==0);sample(8997);
 repeat(8) @(negedge clk);check(valid && power==59910,"next sample follows measured fluctuations");
 otr=1;repeat(3) @(negedge clk);check(!valid && !frame_valid,"overrange invalidates display");
 otr=0;sample(8998);repeat(8) @(negedge clk);check(valid,"overrange recovery");
 feedback_channel=1;repeat(3) @(negedge clk);check(!valid,"wrong channel invalidates display");
 feedback_channel=0;repeat(3) @(negedge clk);sample(8998);repeat(8) @(negedge clk);check(valid,"channel recovery");
 repeat(1510) @(negedge clk);check(!valid,"stopped ADC clears stale reading");
 sample(16000);repeat(8) @(negedge clk);check(!valid,"out of calibration range is invalid");
 wait(dut.update_count==0);sample(8998);repeat(8) @(negedge clk);check(valid,"valid range recovery");
 $display("REVISION_PASS single sample, update hold, BCD, scan, decimal, invalidation, recovery");
 $finish;
end
initial begin #1000000;$fatal(1,"TIMEOUT");end
endmodule
