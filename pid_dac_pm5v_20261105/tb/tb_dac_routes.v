`timescale 1ns/1ps
module tb_dac_routes;
  reg clk=0; always #10 clk=~clk;
  reg rst=0;
  wire [13:0] q1,q2;
  light_lock_top dut(.sys_clk(clk),.sys_rst_n(rst),.adc_d(14'd0),.adc_otr(1'b0),
    .uart_rx(1'b1),.da1_data(q1),.da2_data(q2));
  task check(input [13:0] e1,input [13:0] e2);
    begin repeat(12)@(negedge clk);#1;
      if(q1!==e1||q2!==e2) $fatal(1,"route output %d/%d expected %d/%d",q1,q2,e1,e2);
      if(dut.dac_code_now!==e1) $fatal(1,"live DAC readback mismatch");
    end
  endtask
  initial begin
    #30;@(negedge clk);rst=1;repeat(12)@(negedge clk);
    // PI, test waveform, fixed code and sweep all pass the same final guard.
    force dut.ctrl=16'd0;force dut.pi_out=14'd0;check(545,545);
    force dut.pi_out=14'd16383;check(15851,15851);
    force dut.ctrl=16'd4;force dut.sig_code=14'd0;check(545,545);
    force dut.sig_code=14'd16383;check(15851,15851);
    force dut.ctrl=16'd8;force dut.dac_code_cfg=16'd0;check(545,545);
    force dut.dac_code_cfg=16'd16383;check(15851,15851);
    force dut.ctrl=16'd12;force dut.sweep_code=14'd0;check(545,545);
    force dut.sweep_code=14'd16383;check(15851,15851);
    // Passthrough selects each ADC channel inside adc_dac_core.
    force dut.ctrl=16'd2;force dut.u_core.adc_sample=14'd0;
    force dut.u_core.adc_sample2=14'd16383;check(545,15851);
    force dut.u_core.adc_sample=14'd10000;
    force dut.u_core.adc_sample2=14'd9000;check(10000,9000);
    $display("REVISION_PASS tb_dac_routes PI signal fixed sweep and dual passthrough");
    $finish;
  end
  initial begin #1000000;$fatal(1,"timeout");end
endmodule
