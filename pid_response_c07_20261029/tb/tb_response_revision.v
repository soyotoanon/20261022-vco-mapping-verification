`timescale 1ns/1ps
module tb_response_revision;
reg clk=0; always #10 clk=~clk;
reg rst=0;
reg [13:0] pins=0;
wire adcclk,dc,dw;
wire [13:0] data,snapshot;
integer adc_code=9000;
integer cases=0,n,j,k,expected=8731,guard,previous_sp;
reg watching=0;
light_lock_top dut(.sys_clk(clk),.sys_rst_n(rst),.adc_d(pins),.adc_otr(1'b0),
 .adc_clk(adcclk),.uart_rx(1'b1),.da1_clk(dc),.da1_wrt(dw),.da1_data(data));
assign snapshot=dut.dac_code_now;
always @(adcclk) begin #6; pins=(adc_code ^ 14'h3fff)^14'h2000; end
always @(posedge clk) if(watching) begin
 #1;
 if(data!==expected[13:0] || snapshot!==expected[13:0])
   $fatal(1,"bumpless failure case=%0d data=%0d actual=%0d expected=%0d",cases,data,snapshot,expected);
end
task write_reg(input [5:0] addr,input [15:0] value);
begin
 @(negedge clk);force dut.reg_we=1;force dut.reg_addr=addr;force dut.reg_wdata=value;
 @(negedge clk);release dut.reg_we;release dut.reg_addr;release dut.reg_wdata;
end
endtask
initial begin
 #100;@(negedge clk);rst=1;
 repeat(30)@(negedge clk);
 write_reg(0,8);write_reg(6'h2E,0);write_reg(6'h21,0);
 write_reg(6'h18,9000);write_reg(6'h2C,16383);
 for(j=0;j<2;j=j+1)begin
   write_reg(6'h20,j);
   for(k=0;k<4;k=k+1)begin
     expected=k==0?1400:k==1?8198:k==2?8731:15000;
     for(n=0;n<10;n=n+1)begin
       watching=0;write_reg(0,8);write_reg(6'h2E,0);write_reg(2,expected);
       repeat(20+n)@(negedge clk);
       watching=1;write_reg(6'h2E,16'hA501);write_reg(0,1);
       repeat(100)@(negedge clk);
       if(dut.pi_integ!==((j?1:-1)*(expected-8198)*512))$fatal(1,"integral preload wrong");
       cases=cases+1;
     end
   end
 end
 watching=0;
 write_reg(6'h20,1);write_reg(0,8);write_reg(2,8731);
 repeat(20)@(negedge clk);write_reg(6'h2E,16'hA501);write_reg(0,1);
 repeat(30)@(negedge clk);
 // Register-write edge -> first subsequent valid sample: P and this sample's I.
 write_reg(6'h18,9100);
 guard=0;
 while(dut.pi_err!=100 && guard<12)begin @(negedge clk);guard=guard+1;end
 if(dut.pi_err!=100 || guard>10)$fatal(1,"direct target has an extra sample delay");
 if(dut.pi_out_c!=9685)$fatal(1,"first C07 sample must include 900 P + 54 I: %0d",dut.pi_out_c);
 cases=cases+1;
 // Hardware ramp remains available and bounded when explicitly selected.
 write_reg(0,8);write_reg(6'h18,9000);write_reg(6'h2C,3);
 repeat(20)@(negedge clk);write_reg(0,1);
 repeat(20)@(negedge clk);write_reg(6'h18,9100);
 previous_sp=dut.sp_active;
 repeat(400)begin
   @(posedge clk);#1;
   if(dut.sp_active-previous_sp>3)$fatal(1,"ramp limit lost");
   previous_sp=dut.sp_active;
 end
 if(dut.sp_active!=9100)$fatal(1,"ramp did not reach target");
 cases=cases+1;
 $display("REVISION_PASS response cases=%0d bumpless_both_signs=80 direct_target_next_sample=1 current_I_first_sample=1 bounded_ramp=1",cases);
 $finish;
end
initial begin #5000000;$fatal(1,"timeout");end
endmodule
