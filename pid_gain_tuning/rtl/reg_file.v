`include "power_calibration_20261020.vh"
`timescale 1ns / 1ps
// ============================================================================
//  reg_file.v —— 寄存器堆（【05 参数设置】【12 目标光强】【16 PI 在线调整】的载体）
//
//  64 个 16 位寄存器，串口按地址读写。写进去立刻生效，不用重综合。
//  默认值用函数+cnt 常量表实现（**可综合**，不是 initial 块）：
//  综合器会把 default_value 折叠成一个小 LUT，不占 BRAM。
//
//  地址表（详见 README_REGISTERS.md）：
//    0x00 CTRL        控制位：[0]使能 PI [1]直通（ADC 原样送到 DAC）
//                             [3:2]输出模式 0=PI闭环 1=正弦 2=平台 3=扫描
//                             [4]set_valid（**电平有效**：为 1 时每拍把 out_code 压回
//                                BIAS_CODE、积分清零、到限标志清零；当脉冲用得写 1 再写 0）
//                             [8]清积分 [9]清标志 [10]清统计
//                             [12:11]调试 LED 输出字节选择
//    0x01 STATUS      只读：bit0 光丢失 bit1 OTR bit2 输出饱和 bit3 积分到限
//                             bit4 冻结 bit5 ADC 时钟停摆 bit6 扫描完成
//    0x02 DAC_CODE    调试用固定码
//    0x03 ADC_CODE    只读：当前 ADC 码
//    0x04 ERROR       只读：误差 e（有符号）
//    0x05 OUT_CODE    只读：实际输出码
//    0x06 MEAN        只读：PD 均值       0x07 VAR_Q4 只读：方差×16
//    0x08 MIN_CODE    只读：窗口内最小码 0x09 MAX_CODE 只读：窗口内最大码
//    0x0A SWEEP_START 0x0B SWEEP_END      0x0C SWEEP_STEP(24位，占 0x0C/0x0D)
//    0x0E SWEEP_HOLD  0x0F SWEEP_CTL [0]使能 [1]连续 [2]启动
//    0x10 CTRL_HALF   控制周期半计数
//    0x11 ADC_HALF    ADC 采样时钟半周期（sys_clk 数）
//    0x12 SIG_EN      0x13 SIG_MODE  0x14 SIG_FIXED
//    0x15 SIG_AMP     0x16 SIG_OFF   0x17 SIG_PERIOD
//    0x18 SETPOINT    0x19 BIAS_CODE
//    0x1A KP_SHIFT    0x1B KI_SHIFT  0x1C I_CLAMP(32位，占 0x1C/0x1D)
//    0x1E OUT_MIN     0x1F OUT_MAX   0x20 DIR_POL
//    0x21 LIGHT_TH    0x22 STATS_LOG2
//    0x23 DIST_EN     0x24 DIST_MODE  0x25 DIST_AMP  0x26 DIST_PERIOD
//    0x27 LOG_EVERY   0x28 STREAM_EN
//    0x29 BAUD_DIV    0x2A I_VALUE(只读)
//    0x2B..0x3F 保留（读回 0）
// ============================================================================
module reg_file (
    input  wire        sys_clk,
    input  wire        sys_rst_n,
    input  wire        we,
    input  wire [5:0]  addr,
    input  wire [15:0] wdata,
    output reg  [15:0] rdata,
    // 实时只读量（外部驱动）
    input wire [23:0] live_display_bcd,
    input wire live_display_valid,
    input  wire [15:0] live_adc,
    input  wire [15:0] live_err,
    input  wire [15:0] live_out,
    input  wire [31:0] live_integ,
    input  wire [15:0] live_mean,
    input  wire [15:0] live_var,
    input  wire [15:0] live_min,
    input  wire [15:0] live_max,
    input  wire [15:0] live_status,
    // 配置输出
    output wire [15:0] ctrl,
    output wire [15:0] dac_code,
    output wire [15:0] sweep_start,
    output wire [15:0] sweep_end,
    output wire [15:0] sweep_step_lo,
    output wire [15:0] sweep_step_hi,
    output wire [15:0] sweep_hold,
    output wire [15:0] sweep_ctl,
    output wire [15:0] ctrl_half,
    output wire [15:0] adc_half,
    output wire [15:0] sig_en,
    output wire [15:0] sig_mode,
    output wire [15:0] sig_fixed,
    output wire [15:0] sig_amp,
    output wire [15:0] sig_off,
    output wire [15:0] sig_period,
    output wire [15:0] setpoint,
    output wire [15:0] bias_code,
    output wire [15:0] kp_shift,
    output wire [15:0] ki_shift,
    output wire [15:0] i_clamp_lo,
    output wire [15:0] i_clamp_hi,
    output wire [15:0] out_min,
    output wire [15:0] out_max,
    output wire [15:0] dir_pol,
    output wire [15:0] light_th,
    output wire [15:0] stats_log2,
    output wire [15:0] dist_en,
    output wire [15:0] dist_mode,
    output wire [15:0] dist_amp,
    output wire [15:0] dist_period,
    output wire [15:0] log_every,
    output wire [15:0] stream_en,
    output wire [15:0] sp_slew, feedback_ch, loop_arm,
    output wire [15:0] baud_div
);

    localparam integer NREG = 64;

    reg [15:0] mem [0:NREG-1];
    integer    i;

    // ★ 第四轮：删掉原来的 `wire [15:0] dflt = default_value(addr);`
    //   它没有任何消费者（综合报告把它整个折掉），只是让人误以为"读回默认值"。
    //   真正的默认值只在下面的复位循环里用 default_value()。

    function [15:0] default_value;
        input [5:0] a;
        begin
            case (a)
                6'h00:   default_value = 16'd1; // Autonomous PI on boot, user-confirmed 2026-10-23
                6'h2E:   default_value = 16'd1; // armed on boot
                6'h2C:   default_value = 16'd1; // max setpoint codes per valid ADC sample
                6'h01:   default_value = 16'h0000;  // STATUS（只读，占位）
                6'h02:   default_value = 16'h0F0F;  // DAC_CODE（固定码模式的自检码）★第三轮补：手册一直写 0x0F0F，RTL 却漏了这个分支 ⇒ 实际复位 0
                6'h10:   default_value = 16'd499;   // CTRL_HALF  ⇒ 2*(499+1)*20ns = 20us ⇒ 50 kHz
                6'h11:   default_value = 16'd4;     // ADC_HALF   ⇒ 5 MHz（与现役 bit 同频）
                6'h15:   default_value = 16'd8192;  // SIG_AMP
                6'h16:   default_value = 16'h2000;  // SIG_OFF = 中点
                6'h17:   default_value = 16'd1000;  // SIG_PERIOD
                6'h18:   default_value = 16'd9199;  // SETPOINT
                6'h19:   default_value = 16'd6860;  // BIAS_CODE
                6'h1A:   default_value = 16'd8;     // KP_SHIFT  ⇒ Kp = 1/64
                6'h1B:   default_value = 16'd12;    // KI_SHIFT
                6'h1C:   default_value = 16'h0000;  // I_CLAMP 低
                6'h1D:   default_value = 16'h0080;  // I_CLAMP 高 ⇒ 32'h00200000
                6'h1E:   default_value = 16'd1400;  // OUT_MIN
                6'h1F:   default_value = 16'd15000;  // OUT_MAX
                6'h20:   default_value = 16'd1;     // DIR_POL 正作用
                6'h21:   default_value = 16'd16;    // LIGHT_TH
                6'h22:   default_value = 16'd12;    // STATS_LOG2 ⇒ 4096 点
                6'h24:   default_value = 16'd3;     // DIST_MODE = 正弦
                6'h25:   default_value = 16'd64;    // DIST_AMP（码）
                6'h26:   default_value = 16'd4096;  // DIST_PERIOD
                6'h27:   default_value = 16'd64;    // LOG_EVERY
                6'h29:   default_value = 16'd434;   // BAUD_DIV = 50MHz/115200
                6'h30: default_value = 16'h0001;
                6'h31: default_value = `POWER_ZERO_Q16 & 16'hffff;
                6'h32: default_value = (`POWER_ZERO_Q16 >> 16) & 16'hffff;
                6'h33: default_value = `POWER_GAIN_Q20 & 16'hffff;
                6'h34: default_value = (`POWER_GAIN_Q20 >> 16) & 16'hffff;
                6'h35: default_value = `POWER_CODE_MIN;
                6'h36: default_value = `POWER_CODE_MAX;
                default: default_value = 16'd0;
            endcase
        end
    endfunction

    // ---- 复位后装载默认值（可综合）------------------------------------------
    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            for (i = 0; i < NREG; i = i + 1)
                mem[i] <= default_value(i[5:0]);
        end else if (we) begin
            case (addr)
              6'h02,6'h18,6'h19,6'h1E,6'h1F,6'h2C:
                mem[addr] <= wdata > 16'd16383 ? 16'd16383 : wdata;
              6'h1A: mem[addr] <= wdata[15] ? wdata : wdata > 31 ? 31 : wdata;
              6'h1B: mem[addr] <= wdata[15] ? wdata : wdata > 63 ? 63 : wdata;
              6'h20,6'h2D: begin
                mem[addr] <= {15'd0,wdata[0]};
                mem[6'h2E] <= 0; // polarity/channel changes invalidate arming
              end
              6'h2E: mem[addr] <= {15'd0,wdata == 16'hA501};
              default: mem[addr] <= wdata;
            endcase
        end
    end

    // ---- 读出（组合读：同一拍写入的新值要下一个时钟沿才读得到，不是"以写为准"）----
    always @(*) begin
        case (addr)
            6'h00:   rdata = mem[6'h00];
            6'h01:   rdata = live_status;
            6'h02:   rdata = mem[6'h02];
            6'h03:   rdata = live_adc;
            6'h04:   rdata = live_err;
            6'h05:   rdata = live_out;
            6'h06:   rdata = live_mean;
            6'h07:   rdata = live_var;
            6'h08:   rdata = live_min;
            6'h09:   rdata = live_max;
            6'h0A:   rdata = mem[6'h0A];
            6'h0B:   rdata = mem[6'h0B];
            6'h0C:   rdata = mem[6'h0C];
            6'h0D:   rdata = mem[6'h0D];
            6'h0E:   rdata = mem[6'h0E];
            6'h0F:   rdata = mem[6'h0F];
            6'h10:   rdata = mem[6'h10];
            6'h11:   rdata = mem[6'h11];
            6'h12:   rdata = mem[6'h12];
            6'h13:   rdata = mem[6'h13];
            6'h14:   rdata = mem[6'h14];
            6'h15:   rdata = mem[6'h15];
            6'h16:   rdata = mem[6'h16];
            6'h17:   rdata = mem[6'h17];
            6'h18:   rdata = mem[6'h18];
            6'h19:   rdata = mem[6'h19];
            6'h1A:   rdata = mem[6'h1A];
            6'h1B:   rdata = mem[6'h1B];
            6'h1C:   rdata = mem[6'h1C];
            6'h1D:   rdata = mem[6'h1D];
            6'h1E:   rdata = mem[6'h1E];
            6'h1F:   rdata = mem[6'h1F];
            6'h20:   rdata = mem[6'h20];
            6'h21:   rdata = mem[6'h21];
            6'h22:   rdata = mem[6'h22];
            6'h23:   rdata = mem[6'h23];
            6'h24:   rdata = mem[6'h24];
            6'h25:   rdata = mem[6'h25];
            6'h26:   rdata = mem[6'h26];
            6'h27:   rdata = mem[6'h27];
            6'h28:   rdata = mem[6'h28];
            6'h29:   rdata = mem[6'h29];
            6'h2F: rdata = 16'hD216; // DAC sequencer revision ID
            6'h2A:   rdata = live_integ[15:0];
            6'h2B: rdata = live_integ[31:16];
            6'h2C,6'h2D,6'h2E: rdata = mem[addr];
            6'h30,6'h31,6'h32,6'h33,6'h34,6'h35,6'h36,6'h37: rdata = 0; // legacy affine config inactive
            6'h38: rdata = 16'h5030; // piecewise power firmware ID
            6'h39: rdata = live_display_bcd[15:0];
            6'h3A: rdata = {live_display_valid,7'd0,live_display_bcd[23:16]};
            default: rdata = 16'd0;
        endcase
    end

    // ---- 配置输出 -----------------------------------------------------------
    assign sp_slew = mem[6'h2C];
    assign feedback_ch = mem[6'h2D];
    assign loop_arm = mem[6'h2E];
    assign ctrl          = mem[6'h00];
    assign dac_code      = mem[6'h02];
    assign sweep_start   = mem[6'h0A];
    assign sweep_end     = mem[6'h0B];
    assign sweep_step_lo = mem[6'h0C];
    assign sweep_step_hi = mem[6'h0D];
    assign sweep_hold    = mem[6'h0E];
    assign sweep_ctl     = mem[6'h0F];
    assign ctrl_half     = mem[6'h10];
    assign adc_half      = mem[6'h11];
    assign sig_en        = mem[6'h12];
    assign sig_mode      = mem[6'h13];
    assign sig_fixed     = mem[6'h14];
    assign sig_amp       = mem[6'h15];
    assign sig_off       = mem[6'h16];
    assign sig_period    = mem[6'h17];
    assign setpoint      = mem[6'h18];
    assign bias_code     = mem[6'h19];
    assign kp_shift      = mem[6'h1A];
    assign ki_shift      = mem[6'h1B];
    assign i_clamp_lo    = mem[6'h1C];
    assign i_clamp_hi    = mem[6'h1D];
    assign out_min       = mem[6'h1E];
    assign out_max       = mem[6'h1F];
    assign dir_pol       = mem[6'h20];
    assign light_th      = mem[6'h21];
    assign stats_log2    = mem[6'h22];
    assign dist_en       = mem[6'h23];
    assign dist_mode     = mem[6'h24];
    assign dist_amp      = mem[6'h25];
    assign dist_period   = mem[6'h26];
    assign log_every     = mem[6'h27];
    assign stream_en     = mem[6'h28];
    assign baud_div      = mem[6'h29];

endmodule
