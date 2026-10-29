`timescale 1ns / 1ps
`include "hw_params.vh"
// ADC codes are normalized unsigned offset binary. Error is signed.
// u[n] = clip(bias + direction*(Kp*e[n] + I[n])). I is signed Q9.
// Ki is a DISCRETE gain per valid sample, not a gain per second.
module pi_ctrl #(
 parameter CURRENT_SAMPLE_I = 1
) (
 input wire sys_clk, sys_rst_n, tick, enable, clr_i, set_valid,
 input wire [13:0] y_code, sp_code, bias_code,
 input wire [15:0] kp_shift, input wire [15:0] ki_shift,
 input wire [31:0] i_clamp,
 input wire [13:0] out_min, out_max,
 input wire dir_pol, freeze,
 output reg [13:0] out_code,
 output reg signed [15:0] err,
 output reg signed [31:0] integ,
  output reg i_at_limit, out_at_limit,
  input wire track_valid,
  input wire [13:0] track_code
);
 // Two unsigned 14-bit codes differ by -16383..16383: signed 15 bits.
 wire signed [14:0] e15 = $signed({1'b0,sp_code}) - $signed({1'b0,y_code});
 wire signed [31:0] e32 = {{17{e15[14]}},e15};
 // Truncate magnitude, then restore sign: no negative-only rounding drift.
 wire [14:0] magnitude = e15[14] ? -e15 : e15;
 wire [13:0] emag = magnitude[13:0];
 wire [22:0] emag_q9 = {emag,9'd0};
 // bit15 selects fixed gain: P unsigned Q8, I unsigned Q15.
 // Signed DSP multiplication avoids an absolute-value carry chain before the
 // multiplier and a second negation afterwards. Correct the fractional bits
 // after the shift to retain exact truncation towards zero for either sign.
 (* use_dsp = "yes" *) wire signed [30:0] p_product = e15 * $signed({1'b0,kp_shift[14:0]});
 (* use_dsp = "yes" *) wire signed [30:0] i_product = e15 * $signed({1'b0,ki_shift[14:0]});
 wire signed [31:0] p_fixed = $signed(p_product[30:8]) +
                             ((p_product[30] && |p_product[7:0]) ? 32'sd1 : 32'sd0);
 wire signed [31:0] i_fixed = $signed(i_product[30:6]) +
                             ((i_product[30] && |i_product[5:0]) ? 32'sd1 : 32'sd0);
 wire signed [31:0] pmag = kp_shift >= 15 ? 0 : (emag >> kp_shift);
 wire signed [31:0] imag = ki_shift >= 23 ? 0 : (emag_q9 >> ki_shift);
 wire signed [31:0] prop = kp_shift[15] ? p_fixed : (e15[14] ? -pmag : pmag);
 wire signed [31:0] istep = ki_shift[15] ? i_fixed : (e15[14] ? -imag : imag);
 // Invalid signed clamp values saturate to the largest positive Q9 limit.
 wire signed [31:0] limit_i = i_clamp[31] ? 32'sh7fffffff : $signed(i_clamp);
 wire signed [31:0] ibase = integ > limit_i ? limit_i :
                                  integ < -limit_i ? -limit_i : integ;
 // Signed Q9 -> integer, truncating towards zero without an abs/negate chain.
 wire signed [23:0] iout = $signed(ibase[31:9]) +
                         ((ibase[31] && |ibase[8:0]) ? 24'sd1 : 24'sd0);
 wire signed [23:0] signed_prop = dir_pol ? $signed(prop[21:0]) : -$signed(prop[21:0]);
 wire signed [23:0] p_bias = $signed({1'b0,bias_code}) + signed_prop;
 wire signed [23:0] u_old = p_bias + (dir_pol ? iout : -iout);
 // A reversed range collapses to out_min, rather than wrapping the output.
 wire [13:0] hi = out_max < out_min ? out_min : out_max;
 wire signed [23:0] lo32 = $signed({1'b0,out_min});
 wire signed [23:0] hi32 = $signed({1'b0,hi});
 wire [13:0] bias_lim = bias_code > hi ? hi : bias_code < out_min ? out_min : bias_code;
 wire signed [31:0] actuator_step = dir_pol ? istep : -istep;
 wire windup = (u_old >= hi32 && actuator_step > 0) ||
               (u_old <= lo32 && actuator_step < 0);
 wire signed [32:0] isum = {ibase[31],ibase} + {istep[31],istep};
 wire signed [32:0] lim33 = {1'b0,limit_i};
 wire above_i = isum > lim33;
 wire below_i = isum < -lim33;
 wire signed [31:0] candidate_i = above_i ? limit_i : below_i ? -limit_i : isum[31:0];
 wire signed [31:0] inext = windup ? ibase : candidate_i;
 wire signed [23:0] sum_iout = $signed(isum[32:9]) +
                             ((isum[32] && |isum[8:0]) ? 24'sd1 : 24'sd0);
 wire signed [23:0] limit_iout = $signed({1'b0,limit_i[31:9]});
 // If windup is blocked, candidate_i moves only further into the same output
 // rail. Its clipped output equals that from the held integral, so the output
 // need not traverse the anti-windup mux a second time.
 // Evaluate the three integral-bound cases in parallel. The late bound
 // comparison selects already-clipped codes, rather than preceding more sums.
 wire signed [23:0] u_sum = p_bias + (dir_pol ? sum_iout : -sum_iout);
 wire signed [23:0] u_ipos = p_bias + (dir_pol ? limit_iout : -limit_iout);
 wire signed [23:0] u_ineg = p_bias + (dir_pol ? -limit_iout : limit_iout);
 wire [13:0] sum_lim = u_sum > hi32 ? hi : u_sum < lo32 ? out_min : u_sum[13:0];
 wire [13:0] ipos_lim = u_ipos > hi32 ? hi : u_ipos < lo32 ? out_min : u_ipos[13:0];
 wire [13:0] ineg_lim = u_ineg > hi32 ? hi : u_ineg < lo32 ? out_min : u_ineg[13:0];
 wire [13:0] old_lim = u_old > hi32 ? hi : u_old < lo32 ? out_min : u_old[13:0];
 wire [13:0] u_lim = CURRENT_SAMPLE_I ? (above_i ? ipos_lim : below_i ? ineg_lim : sum_lim) : old_lim;
 wire [13:0] track_lim = track_code > hi ? hi : track_code < out_min ? out_min : track_code;
 wire signed [14:0] track_delta = dir_pol ?
     $signed({1'b0,track_lim})-$signed({1'b0,bias_code}) :
     $signed({1'b0,bias_code})-$signed({1'b0,track_lim});
 wire signed [31:0] track_i = {{8{track_delta[14]}},track_delta,9'b0};
 wire signed [31:0] track_clamped = track_i > limit_i ? limit_i :
                                   track_i < -limit_i ? -limit_i : track_i;
 always @(posedge sys_clk or negedge sys_rst_n) begin
   if (!sys_rst_n) begin
     out_code <= `DAC_CODE_MID; err <= 0; integ <= 0;
   end else begin
     if (set_valid) begin
       integ <= 0; out_code <= bias_lim; err <= 0;
     end else if (clr_i) begin
       integ <= 0;
      end else if (track_valid) begin
        integ <= track_clamped; out_code <= track_lim; err <= 0;
     end else if (tick && enable && !freeze) begin
       err <= e32[15:0]; integ <= inext; out_code <= u_lim;
     end
     // Limits remain effective during hold/freeze and asynchronous config writes.
     if (out_code > hi) out_code <= hi;
     else if (out_code < out_min) out_code <= out_min;
      if (!set_valid && !clr_i && !track_valid && !(tick && enable && !freeze)) begin
       integ <= ibase;
     end
   end
 end
  generate if (CURRENT_SAMPLE_I) begin: current_output_status
    // Observation only: keep the final rail comparison off the compute path.
    always @(posedge sys_clk or negedge sys_rst_n)
      if (!sys_rst_n) out_at_limit <= 0;
      else out_at_limit <= (out_code >= hi || out_code <= out_min);
  end else begin: legacy_output_status
    always @(posedge sys_clk or negedge sys_rst_n)
      if (!sys_rst_n) out_at_limit <= 0;
      else if (set_valid) out_at_limit <= (bias_lim == out_min || bias_lim == hi);
      else if (!clr_i && track_valid) out_at_limit <= (track_lim == out_min || track_lim == hi);
      else if (!clr_i && tick && enable && !freeze) out_at_limit <= (u_lim == hi || u_lim == out_min);
  end endgenerate
  // Observation only. Compare the registered state, not the entire next-state
 // arithmetic cone. Status settles one sys_clk after a normal integral update;
 // PI output, integral update, saturation and freeze retain their exact cadence.
 always @(posedge sys_clk or negedge sys_rst_n) begin
   if (!sys_rst_n) i_at_limit <= 0;
   else if (set_valid || clr_i) i_at_limit <= (limit_i == 0);
   else i_at_limit <= (integ >= limit_i || integ <= -limit_i);
 end
endmodule
