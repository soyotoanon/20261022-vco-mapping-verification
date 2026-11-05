`timescale 1ns / 1ps
`include "hw_params.vh"
// ADC codes are normalized unsigned offset binary. Error is signed.
// u[n] = clip(bias + direction*(Kp*e[n] + I[n])). I is signed Q9.
// Ki is a DISCRETE gain per valid sample, not a gain per second.
module pi_ctrl_reference (
 input wire sys_clk, sys_rst_n, tick, enable, clr_i, set_valid,
 input wire [13:0] y_code, sp_code, bias_code,
 input wire [15:0] kp_shift, input wire [15:0] ki_shift,
 input wire [31:0] i_clamp,
 input wire [13:0] out_min, out_max,
 input wire dir_pol, freeze,
 output reg [13:0] out_code,
 output reg signed [15:0] err,
 output reg signed [31:0] integ,
 output reg i_at_limit, out_at_limit
);
 wire signed [31:0] e32 = $signed({1'b0,sp_code}) - $signed({1'b0,y_code});
 // Truncate magnitude, then restore sign: no negative-only rounding drift.
 wire [31:0] emag = e32 < 0 ? -e32 : e32;
 // bit15 selects fixed gain: P unsigned Q8, I unsigned Q15.
 wire [31:0] p_product = emag * {17'd0,kp_shift[14:0]};
 wire [31:0] i_product = emag * {17'd0,ki_shift[14:0]};
 wire signed [31:0] pmag = kp_shift[15] ? (p_product >> 8) : kp_shift >= 15 ? 0 : (emag >> kp_shift);
 wire signed [31:0] imag = ki_shift[15] ? (i_product >> 6) : ki_shift >= 23 ? 0 : ((emag << 9) >> ki_shift);
 wire signed [31:0] prop = e32 < 0 ? -pmag : pmag;
 wire signed [31:0] istep = e32 < 0 ? -imag : imag;
 // Invalid signed clamp values saturate to the largest positive Q9 limit.
 wire signed [31:0] limit_i = i_clamp[31] ? 32'sh7fffffff : $signed(i_clamp);
 wire signed [31:0] ibase = integ > limit_i ? limit_i :
                                  integ < -limit_i ? -limit_i : integ;
 wire signed [31:0] iabs = ibase < 0 ? -ibase : ibase;
 wire signed [31:0] iout = ibase < 0 ? -(iabs >>> 9) : (iabs >>> 9);
 wire signed [31:0] term = prop + iout;
 wire signed [31:0] u_pre = $signed({1'b0,bias_code}) + (dir_pol ? term : -term);
 // A reversed range collapses to out_min, rather than wrapping the output.
 wire [13:0] hi = out_max < out_min ? out_min : out_max;
 wire signed [31:0] lo32 = $signed({1'b0,out_min});
 wire signed [31:0] hi32 = $signed({1'b0,hi});
 wire [13:0] u_lim = u_pre > hi32 ? hi : u_pre < lo32 ? out_min : u_pre[13:0];
 wire [13:0] bias_lim = bias_code > hi ? hi : bias_code < out_min ? out_min : bias_code;
 wire signed [31:0] actuator_step = dir_pol ? istep : -istep;
 wire windup = (u_pre >= hi32 && actuator_step > 0) ||
               (u_pre <= lo32 && actuator_step < 0);
 wire signed [32:0] isum = {ibase[31],ibase} + {istep[31],istep};
 wire signed [32:0] lim33 = {1'b0,limit_i};
 wire signed [31:0] inext = windup ? ibase :
                              isum > lim33 ? limit_i :
                              isum < -lim33 ? -limit_i : isum[31:0];
 always @(posedge sys_clk or negedge sys_rst_n) begin
   if (!sys_rst_n) begin
     out_code <= `DAC_CODE_MID; err <= 0; integ <= 0;
     i_at_limit <= 0; out_at_limit <= 0;
   end else begin
     if (set_valid) begin
       integ <= 0; out_code <= bias_lim; err <= 0;
       i_at_limit <= (limit_i == 0);
       out_at_limit <= (bias_lim == out_min || bias_lim == hi);
     end else if (clr_i) begin
       integ <= 0; i_at_limit <= (limit_i == 0);
     end else if (tick && enable && !freeze) begin
       err <= e32[15:0]; integ <= inext; out_code <= u_lim;
       i_at_limit <= (inext >= limit_i || inext <= -limit_i);
       out_at_limit <= (u_pre >= hi32 || u_pre <= lo32);
     end
     // Limits remain effective during hold/freeze and asynchronous config writes.
     if (out_code > hi) out_code <= hi;
     else if (out_code < out_min) out_code <= out_min;
     if (!set_valid && !clr_i && !(tick && enable && !freeze)) begin
       integ <= ibase;
       i_at_limit <= (ibase >= limit_i || ibase <= -limit_i);
     end
   end
 end
endmodule
