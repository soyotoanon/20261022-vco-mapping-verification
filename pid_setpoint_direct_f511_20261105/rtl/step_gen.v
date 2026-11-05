`timescale 1ns / 1ps
`include "hw_params.vh"
// ============================================================================
//  step_gen.v —— 【17 阶跃扰动发生器】【19 正弦扰动发生器】
//
//  输出 16 位**有符号**扰动 delta，由顶层加到 PI 的目标码上（饱和加法）：
//      r[n] = clamp(setpoint + delta, 0, 16383)
//  这样扰动只进设定值，不动反馈支路，跟示波器上直接"调偏置"的物理效果一致。
//
//  DIST_MODE: 0 关 / 1 阶跃(单向) / 2 方波 / 3 正弦 / 4 三角
//  幅度与周期都是运行时寄存器。
//
//  ★ 周期口径（2026-10-14 第二轮自查统一）
//    四种波形一律：**实际周期 = DIST_PERIOD 个控制周期**
//    （默认 50 kHz 节拍 ⇒ DIST_PERIOD × 20 µs）。
//    相位是 24 位、一整圈 = 2^24，**每个 tick 推进 2^24/DIST_PERIOD**，
//    与 dac_sig_gen.v 的 20 位相位是同一套口径。
//
//    改前的错法（原文，记下来免得再犯）：正弦/三角的相位**只在 DIST_PERIOD
//    边界推进一次**、步长取 65536/DIST_PERIOD ⇒ 实际周期 = 256×DIST_PERIOD²
//    个控制周期（对 DIST_PERIOD 是**平方**关系），与同一模块里 cnt 直接产生的
//    方波口径不一致。DIST_PERIOD 默认 4096 ⇒ 正弦一圈约 16 天，
//    等于"开了正弦等于没开"。而且原来那句 `per_safe >= 4096 ? 1 : div`
//    还会把 4096 以上的步长统统压成 1，越写越慢。
//
//  ★ 幅度口径（同一轮统一）
//    四种波形的峰值偏移一律 = **DIST_AMP 码**。
//    改前正弦/三角走多项式后还要 >>>15，实际峰值只有 DIST_AMP/16
//    （方波/阶跃用的是 DIST_AMP），同一个 DIST_AMP 在不同波形下差 16 倍。
//    DIST_AMP 是 16 位寄存器，而 delta 是有符号 16 位 ⇒ 这里先夹到 0x7FFF，
//    避免高半区被当成负数（那是第二个坑）。
//
//  ★ 正弦实现（重要改动）
//    原来用 5 级流水的四次多项式近似（1.5878t − 0.6460t² + 0.0797t³ − 0.0033t⁴）。
//    该多项式**不是奇函数**（含 t²、t⁴ 偶次项），代入 t=+1 得 +2086、
//    t=−1 得 −4746（按代码里的定点标度实算）⇒ 正弦正负半周严重不对称；
//    而且它当初流水是为了打断长组合路径，换查表后路径更短，整块流水都不需要了。
//    现在与 dac_sig_gen 共用 rtl/sin_lut.vh 的四分之一波表（Q15，精确对称）。
//
//  ★ 与 dac_sig_gen 的差别：这里的 delta 是**相对设定值的偏移**（可正可负），
//    所以三角/正弦都以 0 为中心，而不是围绕某个 offset。
// ============================================================================
module step_gen (
    input  wire                 sys_clk,
    input  wire                 sys_rst_n,
    input  wire                 tick,
    input  wire                 enable,
    input  wire                 update,         // 参数更新脉冲 ⇒ 相位/计数清零
    input  wire [2:0]           mode,
    input  wire [15:0]          amp,
    input  wire [15:0]          period,         // 单位：控制周期数
    output reg  signed [15:0]   delta
);

    localparam integer PHASE_W = 24;

    `include "sin_lut.vh"        // qsin / sincos（依赖上面的 PHASE_W）

    reg [PHASE_W-1:0]  phase;
    reg [15:0]         cnt;

    wire [15:0] per_safe  = (period == 16'd0) ? 16'd1 : period;
    wire [15:0] half_safe = (per_safe < 16'd2) ? 16'd1 : (per_safe >> 1);

    // ---- 幅度：夹到 0x7FFF 再零扩展成 16 位有符号 ---------------------------
    wire [14:0]        amp_s   = (amp[15:0] > 16'h7FFF) ? 15'h7FFF : amp[14:0];
    wire signed [15:0] amp_ext = $signed({1'b0, amp_s});   // 0 .. +32767

    // ---- 相位步长：顺序除法器（不再是组合除法）------------------------------
    // ★ 与 dac_sig_gen 同一个坑：组合除法 `2^24 / per_safe` 会被综合成一整条
    //   移位-减法链，是 50 MHz 下的时序主因（见 recip_pow2.v 头注释）。
    //   换成多周期**精确**除法：per_safe 只在主机写寄存器时变化，
    //   (K+1)=26 拍延迟对功能零影响（控制节拍只有 50 kHz）。
    //   QW=25：per_safe=1 时商恰为 2^24，需要 25 位才装得下。
    wire [24:0] pstep_div;
    recip_pow2 #(.K(24), .QW(25)) u_pstep (
        .sys_clk   (sys_clk),
        .sys_rst_n (sys_rst_n),
        .d         (per_safe),
        .q         (pstep_div)
    );
    // 相位只有 24 位：per_safe=1 时 2^24 ≡ 0（相位不动，退化），
    // 其余 per_safe>=2 时步长 <= 2^23，正常推进。DIST_PERIOD 有效下限按 2 算。
    wire [PHASE_W-1:0] pstep = pstep_div[PHASE_W-1:0];

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            phase <= {PHASE_W{1'b0}};
            cnt   <= 16'd0;
        end else if (update) begin
            phase <= {PHASE_W{1'b0}};
            cnt   <= 16'd0;
        end else if (tick && enable) begin
            // 相位每 tick 推进 ⇒ 累计 per_safe 拍正好一整圈
            phase <= phase + pstep;
            if (cnt >= per_safe - 1'b1) cnt <= 16'd0;
            else                        cnt <= cnt + 1'b1;
        end
    end

    // ---- 三角波（12 位相位，正负对称，±2048）-------------------------------
    wire [11:0] tri_p  = phase[PHASE_W-1 -: 12];
    wire [11:0] tri_in = tri_p[11] ? (~tri_p) : tri_p;   // 折回 0..2047
    wire signed [16:0] tri_full = $signed({5'b0, tri_in}) * 2 - 17'sd2048;

    // ---- 正弦：与 dac_sig_gen 共用的 Q15 四分之一波表 ------------------------
    wire signed [15:0] sinv = sincos(1'b1, phase);       // ±32767 = sin × 2^15

    // 峰值偏移 = amp：正弦除以 2^15，三角除以 2^11。
    // 乘积必须落在 32 位语境（最坏 32767×32767 = 0.5×2^31 < 2^31），
    // 否则赋值给 16 位的 delta 会先把乘积截断（老 bug）。
    wire signed [31:0] sine_prod = sinv     * amp_ext;
    wire signed [31:0] tri_prod  = tri_full * amp_ext;

    wire signed [15:0] sine_delta = sine_prod >>> 15;
    wire signed [15:0] tri_delta  = tri_prod  >>> 11;

    // ---- 波形选择（组合，输出再打一拍）-------------------------------------
    reg signed [15:0] delta_c;

    always @(*) begin
        case (mode)
            // 阶跃：每个周期前 4 拍给 0、之后一直给 +amp（周期性阶跃串，
            //       配合示波器看整环延迟与建立时间）
            3'd1:    delta_c = (cnt >= 16'd4) ? amp_ext : 16'sd0;
            3'd2:    delta_c = (cnt < half_safe) ? amp_ext : -amp_ext;
            3'd3:    delta_c = sine_delta;
            3'd4:    delta_c = tri_delta;
            default: delta_c = 16'sd0;
        endcase
    end

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) delta <= 16'sd0;
        else            delta <= delta_c;
    end

endmodule
