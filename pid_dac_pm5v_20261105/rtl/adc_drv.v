`timescale 1ns / 1ps
`include "hw_params.vh"
// ============================================================================
//  adc_drv.v —— 【01 AD9248 采样驱动】
//
//  职责：
//    1. 用 sys_clk 分频产生 AD9248 的采样时钟 CLK（half 运行时可改）
//    2. 拉低 OEB（输出使能）与 PDN（掉电，高有效，必须给 0）
//    3. 采集 14 位数据与 OTR，锁存一个采样值 + 一个更新脉冲
//    4. OTR 粘滞锁存与计数（判 PD 过载，比盯着示波器快）
//    5. ADC 时钟活跃度监视（CLK 停振时给出标记，防止上层拿死数当活数）
//
//  采样法沿用现役已验证的写法（见 passthrough_5mhz 的 XDC 注释第 67-70 行）：
//    在生成的 ADC 时钟沿**之后一拍**采数据，并对 adc_d[*] 下 set_false_path。
//    5 MHz 下余量极大；等以后往 10 MHz 以上抬时再改成 IOB 打拍/IDELAY。
// ============================================================================
module adc_drv #(
    parameter integer HALF_W = 8
) (
    input  wire                 sys_clk,
    input  wire                 sys_rst_n,
    input  wire [HALF_W-1:0]    half_period,    // CLK 半周期 sys_clk 数
    input  wire [13:0]          adc_d_pin,      // 来自 J2-1..14
    input  wire                 adc_otr_pin,    // 来自 J2-17
    (* IOB="TRUE", DONT_TOUCH="TRUE" *) output reg adc_clk, // dedicated pin register, J2-15
    output wire                 adc_oeb_n,      // 到 J2-16，恒 0
    output wire                 adc_pdn,        // 到 J2-18，恒 0
    output reg  [13:0]          sample,         // 锁存的采样值（偏移二进制）
    output reg                  sample_valid, // one sys_clk pulse, rising channel
    output reg [13:0]            sample2,
    output reg                  sample2_valid, // one sys_clk pulse, falling channel
    output reg                  otr_sticky,     // OTR 曾经为高
    output reg  [15:0]          otr_count,      // OTR 为高的 ADC 时钟数（饱和）
    output reg                  clk_alive        // ADC 时钟在动（非严格，作健康参考）
);

    assign adc_oeb_n = 1'b0;
    assign adc_pdn   = 1'b0;

    // ---- 分频产生 ADC 采样时钟 ------------------------------------------
    // 计数语义（已用 tb_reg_probe.v 实测断言，不再靠推导）：
    //   div_cnt 从 half_period 递减到 0 后重装并翻转 adc_clk
    //   ⇒ 两次翻转间隔 = (half_period+1) 个 sys_clk
    //   ⇒ ADC 周期     = 2*(half_period+1) 个 sys_clk
    //   50MHz、half_period=4  ⇒ 半周期 100ns ⇒ 周期 200ns ⇒ **正好 5 MHz**
    //   （half_period 默认值见 hw_params.vh 的 `ADC_HALF_DEFAULT）
    // 改 half_period 只改分频比，不动其他任何逻辑。
    reg [HALF_W-1:0] div_cnt;
    (* DONT_TOUCH="TRUE" *) reg capture_clock;

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            div_cnt <= {HALF_W{1'b0}};
            adc_clk <= 1'b0;
            capture_clock <= 1'b0;
        end else if (div_cnt == {HALF_W{1'b0}}) begin
            // 翻转并重装：两次翻转之间严格是 (half_period+1) 拍
            div_cnt <= half_period;
            adc_clk <= ~capture_clock;
            capture_clock <= ~capture_clock;
        end else begin
            div_cnt <= div_cnt - 1'b1;
        end
    end

    // Same format and one-clock capture delay as passthrough_5mhz_ob.
    // The module multiplexes two channels on rising/falling clock phases.
    wire [13:0] normalized = ~(adc_d_pin ^ 14'h2000);
    reg capture_clk_d;
    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            capture_clk_d <= 0;
            sample <= `ADC_CODE_MID; sample2 <= `ADC_CODE_MID;
            sample_valid <= 0; sample2_valid <= 0;
            otr_sticky <= 0; otr_count <= 0;
        end else begin
            capture_clk_d <= capture_clock;
            sample_valid <= 0; sample2_valid <= 0;
            if (capture_clock != capture_clk_d) begin
                if (capture_clock) begin
                    sample <= normalized; sample_valid <= 1;
                end else begin
                    sample2 <= normalized; sample2_valid <= 1;
                end
                if (adc_otr_pin) begin
                    otr_sticky <= 1;
                    if (otr_count != 16'hffff) otr_count <= otr_count + 1'b1;
                end
            end
        end
    end

    // ---- ADC 时钟活跃度监视 -------------------------------------------------
    // ★ 2026-10-14 第三轮审查修复：adc_clk 是本模块自己的分频寄存器，每
    //   (half_period+1) 拍必翻转，而原判据是死数 256 拍，两头都错：
    //     · ADC_HALF ≤ 254 时它恒为 1 —— 等于在监视自己的触发器，任何外部故障
    //       （AD9248 没接、没供电）都报不出来；
    //     · ADC_HALF ≥ 255 时翻转间隔已超过 256 拍，反而**误报停摆**
    //       （ADC_HALF 是 16 位寄存器，上位机可以写到 0x0100 以上）。
    //   现在阈值跟着 half_period 走：2*(half_period+1)+16 拍内没有翻转才算停。
    //   诚实说明：ADC 时钟由 FPGA 自己产生，本信号只能证明**内部分频器在动**，
    //   证明不了 AD9248 真在转换。要判 AD9248 死活，请看 ADC 读数是否变化。
    localparam integer LIVE_CNT_W = 18;
    wire [LIVE_CNT_W-1:0] live_limit = {2'b00, half_period} + {2'b00, half_period}
                                     + 18'd18;      // 2*(half+1)+16
    reg [LIVE_CNT_W-1:0] live_cnt;
    reg                  adc_clk_d;

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            live_cnt   <= {LIVE_CNT_W{1'b0}};
            adc_clk_d  <= 1'b0;
            clk_alive  <= 1'b0;
        end else begin
            adc_clk_d <= capture_clock;
            if (capture_clock != adc_clk_d) begin
                live_cnt  <= {LIVE_CNT_W{1'b0}};
                clk_alive <= 1'b1;          // 观察到一次翻转
            end else if (live_cnt < live_limit) begin
                live_cnt <= live_cnt + 1'b1;
                if (live_cnt == live_limit - 1'b1)
                    clk_alive <= 1'b0;      // 超过阈值没动 ⇒ 分频器停摆
            end
        end
    end

endmodule
