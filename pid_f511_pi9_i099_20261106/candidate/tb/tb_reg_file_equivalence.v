`timescale 1ns/1ps
module tb_reg_file_equivalence;
reg  sys_clk;
reg  sys_rst_n;
reg  we;
reg [5:0] addr;
reg [15:0] wdata;
reg [23:0] live_display_bcd;
reg  live_display_valid;
reg [15:0] live_adc;
reg [15:0] live_err;
reg [15:0] live_out;
reg [31:0] live_integ;
reg [15:0] live_mean;
reg [15:0] live_var;
reg [15:0] live_min;
reg [15:0] live_max;
reg [15:0] live_status;
wire [15:0] ref_rdata, dut_rdata;
wire [15:0] ref_ctrl, dut_ctrl;
wire [15:0] ref_dac_code, dut_dac_code;
wire [15:0] ref_sweep_start, dut_sweep_start;
wire [15:0] ref_sweep_end, dut_sweep_end;
wire [15:0] ref_sweep_step_lo, dut_sweep_step_lo;
wire [15:0] ref_sweep_step_hi, dut_sweep_step_hi;
wire [15:0] ref_sweep_hold, dut_sweep_hold;
wire [15:0] ref_sweep_ctl, dut_sweep_ctl;
wire [15:0] ref_ctrl_half, dut_ctrl_half;
wire [15:0] ref_adc_half, dut_adc_half;
wire [15:0] ref_sig_en, dut_sig_en;
wire [15:0] ref_sig_mode, dut_sig_mode;
wire [15:0] ref_sig_fixed, dut_sig_fixed;
wire [15:0] ref_sig_amp, dut_sig_amp;
wire [15:0] ref_sig_off, dut_sig_off;
wire [15:0] ref_sig_period, dut_sig_period;
wire [15:0] ref_setpoint, dut_setpoint;
wire [15:0] ref_bias_code, dut_bias_code;
wire [15:0] ref_kp_shift, dut_kp_shift;
wire [15:0] ref_ki_shift, dut_ki_shift;
wire [15:0] ref_i_clamp_lo, dut_i_clamp_lo;
wire [15:0] ref_i_clamp_hi, dut_i_clamp_hi;
wire [15:0] ref_out_min, dut_out_min;
wire [15:0] ref_out_max, dut_out_max;
wire [15:0] ref_dir_pol, dut_dir_pol;
wire [15:0] ref_light_th, dut_light_th;
wire [15:0] ref_stats_log2, dut_stats_log2;
wire [15:0] ref_dist_en, dut_dist_en;
wire [15:0] ref_dist_mode, dut_dist_mode;
wire [15:0] ref_dist_amp, dut_dist_amp;
wire [15:0] ref_dist_period, dut_dist_period;
wire [15:0] ref_log_every, dut_log_every;
wire [15:0] ref_stream_en, dut_stream_en;
wire [15:0] ref_feedback_ch, dut_feedback_ch;
wire [15:0] ref_loop_arm, dut_loop_arm;
wire [15:0] ref_baud_div, dut_baud_div;
reg_file_reference ref_inst(.sys_clk(sys_clk),.sys_rst_n(sys_rst_n),.we(we),.addr(addr),.wdata(wdata),.rdata(ref_rdata),.live_display_bcd(live_display_bcd),.live_display_valid(live_display_valid),.live_adc(live_adc),.live_err(live_err),.live_out(live_out),.live_integ(live_integ),.live_mean(live_mean),.live_var(live_var),.live_min(live_min),.live_max(live_max),.live_status(live_status),.ctrl(ref_ctrl),.dac_code(ref_dac_code),.sweep_start(ref_sweep_start),.sweep_end(ref_sweep_end),.sweep_step_lo(ref_sweep_step_lo),.sweep_step_hi(ref_sweep_step_hi),.sweep_hold(ref_sweep_hold),.sweep_ctl(ref_sweep_ctl),.ctrl_half(ref_ctrl_half),.adc_half(ref_adc_half),.sig_en(ref_sig_en),.sig_mode(ref_sig_mode),.sig_fixed(ref_sig_fixed),.sig_amp(ref_sig_amp),.sig_off(ref_sig_off),.sig_period(ref_sig_period),.setpoint(ref_setpoint),.bias_code(ref_bias_code),.kp_shift(ref_kp_shift),.ki_shift(ref_ki_shift),.i_clamp_lo(ref_i_clamp_lo),.i_clamp_hi(ref_i_clamp_hi),.out_min(ref_out_min),.out_max(ref_out_max),.dir_pol(ref_dir_pol),.light_th(ref_light_th),.stats_log2(ref_stats_log2),.dist_en(ref_dist_en),.dist_mode(ref_dist_mode),.dist_amp(ref_dist_amp),.dist_period(ref_dist_period),.log_every(ref_log_every),.stream_en(ref_stream_en),.feedback_ch(ref_feedback_ch),.loop_arm(ref_loop_arm),.baud_div(ref_baud_div));
reg_file dut_inst(.sys_clk(sys_clk),.sys_rst_n(sys_rst_n),.we(we),.addr(addr),.wdata(wdata),.rdata(dut_rdata),.live_display_bcd(live_display_bcd),.live_display_valid(live_display_valid),.live_adc(live_adc),.live_err(live_err),.live_out(live_out),.live_integ(live_integ),.live_mean(live_mean),.live_var(live_var),.live_min(live_min),.live_max(live_max),.live_status(live_status),.ctrl(dut_ctrl),.dac_code(dut_dac_code),.sweep_start(dut_sweep_start),.sweep_end(dut_sweep_end),.sweep_step_lo(dut_sweep_step_lo),.sweep_step_hi(dut_sweep_step_hi),.sweep_hold(dut_sweep_hold),.sweep_ctl(dut_sweep_ctl),.ctrl_half(dut_ctrl_half),.adc_half(dut_adc_half),.sig_en(dut_sig_en),.sig_mode(dut_sig_mode),.sig_fixed(dut_sig_fixed),.sig_amp(dut_sig_amp),.sig_off(dut_sig_off),.sig_period(dut_sig_period),.setpoint(dut_setpoint),.bias_code(dut_bias_code),.kp_shift(dut_kp_shift),.ki_shift(dut_ki_shift),.i_clamp_lo(dut_i_clamp_lo),.i_clamp_hi(dut_i_clamp_hi),.out_min(dut_out_min),.out_max(dut_out_max),.dir_pol(dut_dir_pol),.light_th(dut_light_th),.stats_log2(dut_stats_log2),.dist_en(dut_dist_en),.dist_mode(dut_dist_mode),.dist_amp(dut_dist_amp),.dist_period(dut_dist_period),.log_every(dut_log_every),.stream_en(dut_stream_en),.feedback_ch(dut_feedback_ch),.loop_arm(dut_loop_arm),.baud_div(dut_baud_div));
always #10 sys_clk=~sys_clk;
integer checks=0; integer i,j; reg [31:0] rng=32'h18374291;
function [31:0] next_random; input [31:0] x; reg [31:0] y; begin y=x^(x<<13); y=y^(y>>17); next_random=y^(y<<5); end endfunction
task compare; begin #1;
if (ref_rdata !== dut_rdata) begin $display("[FAIL] output rdata addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_rdata,dut_rdata); $fatal(1); end
if (ref_ctrl !== dut_ctrl) begin $display("[FAIL] output ctrl addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_ctrl,dut_ctrl); $fatal(1); end
if (ref_dac_code !== dut_dac_code) begin $display("[FAIL] output dac_code addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_dac_code,dut_dac_code); $fatal(1); end
if (ref_sweep_start !== dut_sweep_start) begin $display("[FAIL] output sweep_start addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_sweep_start,dut_sweep_start); $fatal(1); end
if (ref_sweep_end !== dut_sweep_end) begin $display("[FAIL] output sweep_end addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_sweep_end,dut_sweep_end); $fatal(1); end
if (ref_sweep_step_lo !== dut_sweep_step_lo) begin $display("[FAIL] output sweep_step_lo addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_sweep_step_lo,dut_sweep_step_lo); $fatal(1); end
if (ref_sweep_step_hi !== dut_sweep_step_hi) begin $display("[FAIL] output sweep_step_hi addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_sweep_step_hi,dut_sweep_step_hi); $fatal(1); end
if (ref_sweep_hold !== dut_sweep_hold) begin $display("[FAIL] output sweep_hold addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_sweep_hold,dut_sweep_hold); $fatal(1); end
if (ref_sweep_ctl !== dut_sweep_ctl) begin $display("[FAIL] output sweep_ctl addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_sweep_ctl,dut_sweep_ctl); $fatal(1); end
if (ref_ctrl_half !== dut_ctrl_half) begin $display("[FAIL] output ctrl_half addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_ctrl_half,dut_ctrl_half); $fatal(1); end
if (ref_adc_half !== dut_adc_half) begin $display("[FAIL] output adc_half addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_adc_half,dut_adc_half); $fatal(1); end
if (ref_sig_en !== dut_sig_en) begin $display("[FAIL] output sig_en addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_sig_en,dut_sig_en); $fatal(1); end
if (ref_sig_mode !== dut_sig_mode) begin $display("[FAIL] output sig_mode addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_sig_mode,dut_sig_mode); $fatal(1); end
if (ref_sig_fixed !== dut_sig_fixed) begin $display("[FAIL] output sig_fixed addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_sig_fixed,dut_sig_fixed); $fatal(1); end
if (ref_sig_amp !== dut_sig_amp) begin $display("[FAIL] output sig_amp addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_sig_amp,dut_sig_amp); $fatal(1); end
if (ref_sig_off !== dut_sig_off) begin $display("[FAIL] output sig_off addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_sig_off,dut_sig_off); $fatal(1); end
if (ref_sig_period !== dut_sig_period) begin $display("[FAIL] output sig_period addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_sig_period,dut_sig_period); $fatal(1); end
if (ref_setpoint !== dut_setpoint) begin $display("[FAIL] output setpoint addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_setpoint,dut_setpoint); $fatal(1); end
if (ref_bias_code !== dut_bias_code) begin $display("[FAIL] output bias_code addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_bias_code,dut_bias_code); $fatal(1); end
if (ref_kp_shift !== dut_kp_shift) begin $display("[FAIL] output kp_shift addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_kp_shift,dut_kp_shift); $fatal(1); end
if (ref_ki_shift !== dut_ki_shift) begin $display("[FAIL] output ki_shift addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_ki_shift,dut_ki_shift); $fatal(1); end
if (ref_i_clamp_lo !== dut_i_clamp_lo) begin $display("[FAIL] output i_clamp_lo addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_i_clamp_lo,dut_i_clamp_lo); $fatal(1); end
if (ref_i_clamp_hi !== dut_i_clamp_hi) begin $display("[FAIL] output i_clamp_hi addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_i_clamp_hi,dut_i_clamp_hi); $fatal(1); end
if (ref_out_min !== dut_out_min) begin $display("[FAIL] output out_min addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_out_min,dut_out_min); $fatal(1); end
if (ref_out_max !== dut_out_max) begin $display("[FAIL] output out_max addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_out_max,dut_out_max); $fatal(1); end
if (ref_dir_pol !== dut_dir_pol) begin $display("[FAIL] output dir_pol addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_dir_pol,dut_dir_pol); $fatal(1); end
if (ref_light_th !== dut_light_th) begin $display("[FAIL] output light_th addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_light_th,dut_light_th); $fatal(1); end
if (ref_stats_log2 !== dut_stats_log2) begin $display("[FAIL] output stats_log2 addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_stats_log2,dut_stats_log2); $fatal(1); end
if (ref_dist_en !== dut_dist_en) begin $display("[FAIL] output dist_en addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_dist_en,dut_dist_en); $fatal(1); end
if (ref_dist_mode !== dut_dist_mode) begin $display("[FAIL] output dist_mode addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_dist_mode,dut_dist_mode); $fatal(1); end
if (ref_dist_amp !== dut_dist_amp) begin $display("[FAIL] output dist_amp addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_dist_amp,dut_dist_amp); $fatal(1); end
if (ref_dist_period !== dut_dist_period) begin $display("[FAIL] output dist_period addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_dist_period,dut_dist_period); $fatal(1); end
if (ref_log_every !== dut_log_every) begin $display("[FAIL] output log_every addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_log_every,dut_log_every); $fatal(1); end
if (ref_stream_en !== dut_stream_en) begin $display("[FAIL] output stream_en addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_stream_en,dut_stream_en); $fatal(1); end
if (ref_feedback_ch !== dut_feedback_ch) begin $display("[FAIL] output feedback_ch addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_feedback_ch,dut_feedback_ch); $fatal(1); end
if (ref_loop_arm !== dut_loop_arm) begin $display("[FAIL] output loop_arm addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_loop_arm,dut_loop_arm); $fatal(1); end
if (ref_baud_div !== dut_baud_div) begin $display("[FAIL] output baud_div addr=%h data=%h ref=%h dut=%h",addr,wdata,ref_baud_div,dut_baud_div); $fatal(1); end
checks=checks+1; end endtask
task drive; input [5:0] a; input [15:0] d; begin
@(negedge sys_clk); addr=a; wdata=d; we=1; @(posedge sys_clk); compare; @(negedge sys_clk); we=0; compare; end endtask
initial begin
sys_clk=0;
sys_rst_n=0;
we=0;
addr=0;
wdata=0;
live_display_bcd=0;
live_display_valid=0;
live_adc=0;
live_err=0;
live_out=0;
live_integ=0;
live_mean=0;
live_var=0;
live_min=0;
live_max=0;
live_status=0;
#3; compare; repeat(3) @(negedge sys_clk); sys_rst_n=1;
for(i=0;i<64;i=i+1) begin drive(i,0); drive(i,16'h7fff); drive(i,16'h8000); drive(i,16'hffff); drive(i,16'hA501); end
drive(6'h2e,16'hA501); drive(6'h20,1); drive(6'h2e,16'hA501); drive(6'h2d,0);
for(i=0;i<20000;i=i+1) begin
@(negedge sys_clk); rng=next_random(rng); addr=rng[5:0]; rng=next_random(rng); wdata=rng[15:0]; we=rng[20];
rng=next_random(rng); live_display_bcd=rng;
rng=next_random(rng); live_display_valid=rng;
rng=next_random(rng); live_adc=rng;
rng=next_random(rng); live_err=rng;
rng=next_random(rng); live_out=rng;
rng=next_random(rng); live_integ=rng;
rng=next_random(rng); live_mean=rng;
rng=next_random(rng); live_var=rng;
rng=next_random(rng); live_min=rng;
rng=next_random(rng); live_max=rng;
rng=next_random(rng); live_status=rng;
if(i%1009==0) sys_rst_n=0; else sys_rst_n=1;
@(posedge sys_clk); compare; end
@(negedge sys_clk); we=0; for(j=0;j<64;j=j+1) begin addr=j; compare; end
$display("REVISION_PASS reg_file equivalence checks=%0d",checks); $finish; end
initial begin #10000000; $display("TIMEOUT"); $fatal(1); end
endmodule
