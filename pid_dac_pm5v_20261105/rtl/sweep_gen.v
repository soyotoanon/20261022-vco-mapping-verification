`timescale 1ns / 1ps
`include "hw_params.vh"
// ============================================================================
//  sweep_gen.v —— 【07 DAC 自动扫描】
//
//  从 start_code 按 |step_code| 步进到 end_code，每步保持 hold 个控制周期，
//  到终点后：单次模式(sweep_cont=0) 停在终点并置 done；连续模式在两个端点间来回扫。
//  扫描码直接顶掉 PI 输出（mode=SWEEP 时用），配合【11 DDS 工作点设置】找
//  AOM 的最佳工作点（衍射效率 70~80% 对应的码）。
//
//  方向：由 start_code / end_code 的大小决定（end > start 向上，end < start 向下）。
//        step_code 取**幅值**（有符号 24 位，写负数与写正数等价），0 按 1 处理。
//
//  ---------------------------------------------------------------------------
//  ★ 2026-10-14 第二轮审查修复（两处，都能让扫描功能实际不工作）：
//    ① 比较域错误：原实现把 start/end 用 $signed() 当**14 位有符号数**比较
//       （`next_up >= $signed({{10{end_s[13]}}, end_s})`）。码是 0x0000..0x3FFF 的
//       偏移二进制，越过中点 0x2000 后符号翻转，于是**任何跨中点的向上扫描都会
//       立刻判成"到终点"**：0x1000 → 0x3000 时 end_s = −4096、cur = 4096、
//       next_up = 4097 ≥ −4096 成立 ⇒ 第一步就 code <= end_code、done <= 1；
//       默认 0x0000 → 0x3FFF（end_s = −1）同理一步到底；负 step_code 也永远够不到终点。
//       现改为全程无符号 14 位码域比较，用"剩余距离 ≤ 步长就夹到终点"避免下溢。
//    ② 保持拍数 off-by-one：原判据 `if (hold_cnt != hold) hold_cnt <= hold_cnt+1`
//       配 0 起始自增，实际每步保持 hold+1 个控制周期。现改为 `hold_cnt < hold-1`，
//       每步正好 hold 拍（hold=0/1 ⇒ 每拍走一步）。
//    另外：原实现的"连续模式"转身后仍以 end_code 为目标，会在终点原地抖动，
//    现在用一个 goal 寄存器记住当前目标（end_code 或 start_code），转身时交换。
// ============================================================================
module sweep_gen #(
    parameter integer HOLD_W = 24
) (
    input  wire                 sys_clk,
    input  wire                 sys_rst_n,
    input  wire                 tick,
    input  wire                 enable,
    input  wire                 start,          // 上升沿重启扫描
    input  wire [13:0]          start_code,
    input  wire [13:0]          end_code,
    input  wire signed [23:0]   step_code,
    input  wire [HOLD_W-1:0]    hold,
    input  wire                 cont,           // 1 = 来回连续扫
    output reg  [13:0]          code,
    output reg                  done,           // 到达终点（单次模式）
    output reg  [15:0]          step_count      // 已走步数（上位机画曲线用）
);

    // ---- 全程无符号 14 位码域（零扩展到 24 位做加减，不可能下溢）------------
    wire [23:0] start_x = {10'd0, start_code};
    wire [23:0] end_x   = {10'd0, end_code};
    wire        dir_ini = (end_code < start_code);      // 1 = 向下

    // step_code 只取幅值；0 按 1 处理，避免死循环
    wire [23:0] step_abs = step_code[23] ? (~step_code + 24'd1) : step_code;
    wire [23:0] step_use = (step_abs == 24'd0) ? 24'd1 : step_abs;

    // 每步正好 hold 个控制周期（hold=0/1 ⇒ 每拍一步）
    wire [HOLD_W-1:0] hold_m1 = (hold == {HOLD_W{1'b0}}) ? {HOLD_W{1'b0}} : (hold - 1'b1);

    reg [HOLD_W-1:0] hold_cnt;
    reg [23:0]       cur;
    reg [23:0]       goal;               // 当前目标（单次模式恒为 end_x）
    reg              dir;                // 0 向上，1 向下

    // 剩余距离恒 ≥ 0（cur 始终落在起点与 goal 之间）⇒ 够一步就走，否则夹到终点
    wire [23:0] remain = dir ? (cur - goal) : (goal - cur);
    wire        finish = (step_use >= remain);

    // 连续模式转身：另一端 = goal 的两个候选（start_x / end_x）中的另一个
    wire [23:0] other_x = (goal == end_x) ? start_x : end_x;

    // 下一步的位置（Verilog 不允许对表达式做位选，所以先算成线网再取低 14 位）
    wire [23:0] next_x  = dir ? (cur - step_use) : (cur + step_use);

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            cur        <= {10'd0, `DAC_CODE_MID};
            goal       <= {10'd0, `DAC_CODE_MID};
            hold_cnt   <= {HOLD_W{1'b0}};
            dir        <= 1'b0;
            done       <= 1'b0;
            step_count <= 16'd0;
            code       <= `DAC_CODE_MID;
        end else if (start) begin
            cur        <= start_x;
            goal       <= end_x;
            hold_cnt   <= {HOLD_W{1'b0}};
            dir        <= dir_ini;
            done       <= 1'b0;
            step_count <= 16'd0;
            code       <= start_code;
        end else if (enable && tick) begin
            if (hold_cnt < hold_m1) begin
                hold_cnt <= hold_cnt + 1'b1;
            end else begin
                hold_cnt <= {HOLD_W{1'b0}};
                if (!done) step_count <= step_count + 1'b1;
                if (finish) begin
                    if (cont) begin
                        // 先走到端点本身再转身：否则每趟都会短一步、端点码永远不输出
                        cur  <= goal;
                        code <= goal[13:0];
                        goal <= other_x;
                        dir  <= ~dir;
                    end else begin
                        cur  <= goal;
                        code <= goal[13:0];
                        done <= 1'b1;
                    end
                end else begin
                    cur  <= next_x;
                    code <= next_x[13:0];
                end
            end
        end
    end

endmodule
