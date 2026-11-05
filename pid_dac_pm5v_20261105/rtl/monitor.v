`timescale 1ns / 1ps
// ============================================================================
//  monitor.v —— 【15 闭环保护功能】
//
//  三项保护，全部只出标志和冻结请求，不自己改码值：
//    ① 光丢失：ADC 码持续低于阈值 light_th 超过 2^LOSS_LOG2 个控制周期
//       ⇒ frozen=1、light_lost=1。整环冻结（DESIGN_DECISIONS 6.7）。
//       注意是"整环冻结"而不是只冻积分：e 满量程时比例项一个人就能把输出顶到底。
//    ② ADC 过载：OTR 粘滞位 + 码贴边计数
//    ③ 输出饱和：PI 输出贴 out_min/out_max 或积分到限
//
//  优先级：一旦 light_lost 置位就**锁存**，必须上位机写 CLR_FLAGS 才能复位。
//  理由：光丢失是"必须有人看一眼"的事件，自动恢复会掩盖故障。
// ============================================================================
module monitor #(
    parameter integer LOSS_LOG2 = 8,      // 连续 256 个控制周期无光才判定丢失
    parameter integer CNT_W     = 16
) (
    input  wire                 sys_clk,
    input  wire                 sys_rst_n,
    input  wire                 tick,
    input  wire                 enable,
    input  wire [13:0]          adc_code,
    input  wire [15:0]          light_th,
    input  wire                 otr_sticky,
    input  wire                 i_at_limit,
    input  wire                 out_at_limit,
    input  wire                 clr_flags,     // 清一次标志
    output reg                  frozen,
    output reg                  light_lost,
    output reg                  otr_seen,
    output reg                  out_sat_seen,
    output reg  [CNT_W-1:0]     loss_cnt
);

    reg [LOSS_LOG2-1:0] loss_run;

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            frozen       <= 1'b0;
            light_lost   <= 1'b0;
            otr_seen     <= 1'b0;
            out_sat_seen <= 1'b0;
            loss_run     <= {LOSS_LOG2{1'b0}};
            loss_cnt     <= {CNT_W{1'b0}};
        end else begin
            if (clr_flags) begin
                light_lost   <= 1'b0;
                otr_seen     <= 1'b0;
                out_sat_seen <= 1'b0;
                loss_run     <= {LOSS_LOG2{1'b0}};
            end

            // 粘滞监视（不复位就一直记着）。
            // ★ clr_flags 的优先级必须高于粘滞置位：否则清标志那一拍
            //   `otr_seen<=0` 会被下面的 `if (otr_sticky) otr_seen<=1` 立刻覆盖，
            //   而 otr_sticky 又是"进过一次 ADC 时钟沿就恒 1"的粘滞位 ⇒ 
            //   otr_seen 永远清不掉（实测写 ctrl[9]=1 后 otr_seen 仍为 1）。
            if (otr_sticky && !clr_flags)     otr_seen     <= 1'b1;
            if ((out_at_limit || i_at_limit) && !clr_flags) out_sat_seen <= 1'b1;

            if (clr_flags || !enable) begin
                loss_run <= 0; loss_cnt <= 0;
            end else if (tick) begin
                if (adc_code < light_th[13:0]) begin
                    loss_run <= loss_run + 1'b1;
                    if (loss_cnt != {CNT_W{1'b1}}) loss_cnt <= loss_cnt + 1'b1;
                    // 连续足够长时间仍是暗 ⇒ 判定光丢失
                    // ★ 审查发现：原来写 `if (&loss_run || loss_run == {LOSS_LOG2{1'b1}})`，
                    //   两个条件**完全等价**（`&loss_run` 即"全 1"，`== {W{1'b1}}` 也是全 1），
                    //   属冗余，已合并。阈值实测口径 = 连续 2^LOSS_LOG2 - 1 个控制周期
                    //   （第二轮修正：loss_run 复位 0、每拍 +1、到全 1 回卷，比较用的是
                    //   **自增前**的旧值 ⇒ 置位发生在第 2^LOSS_LOG2 个连续暗控制周期：
                    //   LOSS_LOG2=4 ⇒ 16 拍；=8 ⇒ 256 拍 = 5.12 ms @ 默认 50 kHz tick。
                    //   与本文件头注释一致；第一轮曾误写成 2^N − 1，见 README.md 自查表）。
                    if (loss_run == {LOSS_LOG2{1'b1}})
                        light_lost <= 1'b1;
                end else begin
                    loss_run <= {LOSS_LOG2{1'b0}};
                    loss_cnt <= {CNT_W{1'b0}};
                end
            end

            frozen <= clr_flags ? 1'b0 : light_lost;
        end
    end

endmodule
