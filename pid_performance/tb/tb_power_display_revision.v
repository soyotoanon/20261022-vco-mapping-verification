`timescale 1ns/1ps
module tb_power_display_revision;
reg clk=0; always #10 clk=~clk;
reg rst_n=0, sample_valid=0, feedback_channel=0, otr=0,cfg_we=0;
reg [13:0] pi_code=0;
reg [5:0] cfg_addr=0;
reg [15:0] cfg_data=0;
wire [5:0] sel;
wire [7:0] seg;
wire [19:0] power;
wire valid;
power_display #(.AVG_LOG2(4),.SCAN_DIV(8),.STALE_CYCLES(1000)) dut(
 .clk(clk),.rst_n(rst_n),.pi_code(pi_code),.sample_valid(sample_valid),
 .feedback_channel(feedback_channel),.otr(otr),
 .cfg_we(cfg_we),.cfg_addr(cfg_addr),.cfg_data(cfg_data),
 .seg_sel_n(sel),.seg_n(seg),.power_units(power),.display_valid(valid));
task check(input bit ok,input string label);
 if(!ok) $fatal(1,"FAIL %s power=%d valid=%d",label,power,valid);
endtask
task wr(input [5:0] a,input [15:0] d);
 @(negedge clk);cfg_we=1;cfg_addr=a;cfg_data=d;
 @(negedge clk);cfg_we=0;
endtask
task samples(input integer c);
 integer i;
 for(i=0;i<16;i=i+1) begin
  @(negedge clk);pi_code=16383-c;sample_valid=1;
  @(negedge clk);sample_valid=0;
  repeat(4) @(negedge clk);
 end
 repeat(5) @(negedge clk);
endtask
reg [5:0] seen;
integer n;
initial begin
 repeat(5) @(negedge clk);rst_n=1;
 samples(7000);
 check(valid && power>=90457 && power<=90458,"actual fitted calibration and PI inversion");
 // Exact synthetic calibration: P units = (C - 100) * 1024.
 // Set C=112 => 12288 units => display 01.2288.
 wr('h31,0);wr('h32,100);wr('h33,0);wr('h34,'h4000);
 wr('h35,0);wr('h36,16383);
 samples(7000);check(power>=90457 && power<=90458,"shadow writes must not change active coefficients");
 wr('h37,'ha501);
 samples(112);check(valid && power==12288,"atomic coefficient update");
 repeat(180) @(negedge clk);
 check(dut.bcd_frame==24'h012288,"six digit BCD");
 seen=0;
 for(n=0;n<60;n=n+1) begin
  @(negedge clk);
  if(sel!=6'h3f) begin
   check(seg[7]==(sel!=6'b101111),"decimal point after second left digit");
   case(sel)
    6'b111110,6'b111101: check(seg[6:0]==7'b0000000,"digit8");
    6'b111011,6'b110111: check(seg[6:0]==7'b0100100,"digit2");
    6'b101111: check(seg[6:0]==7'b1111001,"digit1");
    6'b011111: check(seg[6:0]==7'b1000000,"digit0");
    default:$fatal(1,"FAIL more than one active digit");
   endcase
   seen=seen | ~sel;
  end
 end
 check(seen==6'h3f,"all six digits scanned");
 // Fractional mean must survive averaging: alternate C112/113 => 12800.
 for(n=0;n<16;n=n+1) begin
  @(negedge clk);pi_code=16383-112-(n%2);sample_valid=1;
  @(negedge clk);sample_valid=0;
  repeat(4) @(negedge clk);
 end
 repeat(5) @(negedge clk);
 check(valid && power==12800,"fractional average preserved");
 otr=1;repeat(3) @(negedge clk);check(!valid,"OTR invalidates");
 otr=0;samples(112);check(valid,"OTR recovery");
 feedback_channel=1;repeat(3) @(negedge clk);check(!valid,"wrong channel invalidates");
 feedback_channel=0;samples(112);samples(112);check(valid,"channel recovery");
 repeat(1100) @(negedge clk);check(!valid,"stale data invalidates");
 samples(16300);samples(16300);check(!valid,"display overflow is invalid");
 samples(99);check(valid && power==0,"small negative clamps zero");
 samples(90);check(!valid,"large negative invalid");
 wr('h35,110);wr('h36,120);wr('h37,'ha501);
 samples(109);check(!valid,"out of calibration range");
 wr('h30,0);wr('h37,'ha501);samples(112);check(!valid,"disabled");
 $display("REVISION_PASS power conversion, fractional mean, BCD, scan, decimal, invalidation, atomic config");
 $finish;
end
initial begin #2000000;$fatal(1,"TIMEOUT");end
endmodule
