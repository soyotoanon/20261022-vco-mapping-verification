`timescale 1ns / 1ps
// ============================================================================
//  ctrl_tick.v —— 控制周期发生器
//
//  产生一个"每个控制周期一个 sys_clk 宽"的脉冲 tick。
//  这是 PI 更新、DAC 更新、自动扫描步进、统计采样、数据上传共用的时间基准。
//
//  设计要点（回答用户提的那条）：
//    老 XADC 代码是"DAC 每 50us 才更新一次"，那条等待机制**在本工程里不存在**。
//    这里没有任何固定延时，控制周期完全由 half 寄存器决定；
//    上位机可以通过 WR_CTRL_PERIOD 在线改，用来做"更新周期扫描"实验。
// ============================================================================
module ctrl_tick #(
    parameter integer CNT_W = 25
) (
    input  wire                  sys_clk,
    input  wire                  sys_rst_n,
    input  wire [CNT_W-1:0]      half,       // 半周期计数：周期 = SYS_CLK*2*(half+1)
    output reg                   tick        // 每周期一个 sys_clk 宽的脉冲
);

    reg [CNT_W-1:0] cnt;
    reg             phase;

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            cnt   <= {CNT_W{1'b0}};
            phase <= 1'b0;
            tick  <= 1'b0;
        end else begin
            tick <= 1'b0;
            if (cnt == {CNT_W{1'b0}}) begin
                cnt   <= half;          // 半周期到，翻转相位
                phase <= ~phase;
                if (phase) tick <= 1'b1; // 后半周期结束 ⇒ 一个完整控制周期
            end else begin
                cnt <= cnt - 1'b1;
            end
        end
    end

endmodule
