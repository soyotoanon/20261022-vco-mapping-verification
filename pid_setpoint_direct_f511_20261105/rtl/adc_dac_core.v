`timescale 1ns / 1ps
`include "hw_params.vh"
// ============================================================================
//  adc_dac_core.v —— 【03 ADC→DAC 直通 + 01/02 的实例化】
//
//  行为与现役 passthrough_5mhz 的 bit **逐条等价**（便于新旧 bit 对比验证）：
//    - 50MHz 分频出 5MHz 给 AD9248，OEB=0、PDN=0
//    - Complement-to-offset conversion and front-end inversion match passthrough_5mhz_ob.
//    - 每个 ADC 采样沿后一拍更新输出
//  唯一新增：ADC 采样时钟半周期运行时可改、OTR 粘滞统计可读。
//
//  passthrough=1 时选择 ADC 直通；=0 时输出由上层（信号发生器/PI）驱动。
// ============================================================================
module adc_dac_core #(
    parameter integer HALF_W = 8
) (
    input  wire                 sys_clk,
    input  wire                 sys_rst_n,
    input  wire                 passthrough,    // 1 = ADC 码直通到 DAC
    input  wire [HALF_W-1:0]    adc_half,
    input  wire [13:0]          sel_code,       // 上层码值（信号发生器 / PI 输出）
    input  wire [13:0]          adc_d_pin,
    input  wire                 adc_otr_pin,
    output wire                 adc_clk,
    output wire                 adc_oeb_n,
    output wire                 adc_pdn,
    output wire [13:0]          da1_data,
    output wire                 da1_clk,
    output wire                 da1_wrt,
    output wire [13:0]          da2_data,
    output wire                 da2_clk,
    output wire                 da2_wrt,
    output wire [13:0]          adc_sample,
    output wire                 adc_sample_valid,
    output wire [13:0] adc_sample2,
    output wire adc_sample2_valid,
    output wire                 otr_sticky,
    output wire [15:0]          otr_count,
    output wire                 adc_clk_alive,
    output wire [13:0]          dac_code_now
);

    wire [13:0] code_to_dac;

    assign code_to_dac = passthrough ? adc_sample : sel_code;

    adc_drv #(.HALF_W(HALF_W)) u_adc (
        .sys_clk        (sys_clk),
        .sys_rst_n      (sys_rst_n),
        .half_period    (adc_half),
        .adc_d_pin      (adc_d_pin),
        .adc_otr_pin    (adc_otr_pin),
        .adc_clk        (adc_clk),
        .adc_oeb_n      (adc_oeb_n),
        .adc_pdn        (adc_pdn),
        .sample         (adc_sample),
        .sample_valid   (adc_sample_valid),
        .sample2 (adc_sample2), .sample2_valid (adc_sample2_valid),
        .otr_sticky     (otr_sticky),
        .otr_count      (otr_count),
        .clk_alive      (adc_clk_alive)
    );

    dac_drv u_dac (
        .sys_clk        (sys_clk),
        .sys_rst_n      (sys_rst_n),
        .data1          (code_to_dac),
        .data2          (passthrough ? adc_sample2 : sel_code),
        .da1_data       (da1_data),
        .da1_clk        (da1_clk),
        .da1_wrt        (da1_wrt),
        .da2_data       (da2_data),
        .da2_clk        (da2_clk),
        .da2_wrt        (da2_wrt),
        .dac_snapshot   (dac_code_now)
    );

endmodule
