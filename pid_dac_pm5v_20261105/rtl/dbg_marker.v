`timescale 1ns / 1ps
// ============================================================================
//  dbg_marker.v —— 【06 数字链路延迟测试】的硬件标记
//
//  ⚠️ 2026-10-14 第三轮审查更正：本模块**当前没有接到任何封装引脚**。
//  实际接法：只在 `light_lock_top.v:460-484` 的 `ifdef ILA_ENABLE` 里接到 ILA 的
//  probe3/probe4，顶层没有对应端口、XDC 也没有脚（README_TESTS.md §5 已如此说明）。
//  所以"各引一根线到示波器、量引脚间延迟"目前做不到；要做必须先加顶层输出端口 + XDC。
//
//  另外两点已知瑕疵（做这个测量前要知道）：
//    · marker_dac 接的是 dac_update（= adc_sample_valid），那是**电平**、在 ADC 时钟
//      高电平期间恒 1（默认约 5 个 sys_clk），不是"一个 sys_clk 宽的脉冲"；
//    · 本模块在 sys_clk 上升沿打拍，而 DAC 数据在 sys_clk 下降沿更新（dac_drv.v），
//      两个标记的上升沿之差比真实的"ADC 时钟沿 → DAC 数据变"少约半拍（≈10 ns）。
// ============================================================================
module dbg_marker (
    input  wire sys_clk,
    input  wire sys_rst_n,
    input  wire adc_clk,          // 生成的 ADC 采样时钟
    input  wire dac_update,       // DAC 数据更新的那一拍
    output reg  marker_adc,       // ADC 时钟上升沿标记
    output reg  marker_dac        // DAC 更新标记
);

    reg adc_clk_d;

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            adc_clk_d  <= 1'b0;
            marker_adc <= 1'b0;
            marker_dac <= 1'b0;
        end else begin
            adc_clk_d <= adc_clk;
            // adc_clk 由 sys_clk 分频而来，组合边沿检测是安全的
            marker_adc <= adc_clk & ~adc_clk_d;
            marker_dac <= dac_update;
        end
    end

endmodule
