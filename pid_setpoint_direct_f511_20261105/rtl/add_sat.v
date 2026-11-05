`timescale 1ns / 1ps
// ============================================================================
//  add_sat.v —— 有符号加法 + 饱和（【13 PI 输出限幅】用）
//
//  普通加法的溢出会回卷（wraparound），在 PI 输出限幅里这是致命的：
//  输出码一旦回卷，DDS 会突然跳到另一端。这里改成饱和。
// ============================================================================
module add_sat #(
    parameter integer W = 16
) (
    input  wire signed [W-1:0] a,
    input  wire signed [W-1:0] b,
    output reg  signed [W-1:0] y,
    output reg                 ovf
);

    wire signed [W:0] sum = {a[W-1], a} + {b[W-1], b};

    always @(*) begin
        if ((a[W-1] == b[W-1]) && (sum[W-1] != a[W-1])) begin
            // 同号相加却变了符号 ⇒ 溢出
            ovf = 1'b1;
            y   = a[W-1] ? {1'b1, {W-1{1'b0}}} : {1'b0, {(W-1){1'b1}}};
        end else begin
            ovf = 1'b0;
            y   = sum[W-1:0];
        end
    end

endmodule
