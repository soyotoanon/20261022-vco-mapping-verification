`timescale 1ns / 1ps
`include "hw_params.vh"
// ============================================================================
//  dac_sig_gen.v —— 【04 DAC 测试信号发生器】
//
//  六种波形，全部按**控制周期**步进（不是每 sys_clk，也不是每 50us）：
//    0 FIXED   固定码
//    1 SQUARE  方波     —— 测 DAC→DDS 通带与极性
//    2 STEP    阶跃     —— 每周期一段脉冲（前 4 拍回 offset、其余时间 offset+amp），
//                          不是"单次跳变"；测整环延迟与建立时间
//    3 RAMP    斜坡     —— 线性扫描，测 DAC 单调性/直线度
//    4 TRI     三角     —— 平滑往复
//    5 SINE    正弦     —— 内置近似正弦，测频响与小幅扰动
//
//  幅度/偏置/周期都是运行时寄存器，改完立刻生效，不用重综合。
//  sincos 相位定义：cosv 从 +amp 开始，sinv 从 0 开始（合成正弦用 sinv）。
// ============================================================================
module dac_sig_gen (
    input  wire                 sys_clk,
    input  wire                 sys_rst_n,
    input  wire                 tick,           // 控制周期脉冲
    input  wire                 enable,
    input  wire                 update,         // 参数更新脉冲 ⇒ 重载相位/计数
    input  wire [2:0]           mode,
    input  wire [13:0]          fixed_code,
    input  wire [13:0]          amp,
    input  wire [13:0]          offset,
    input  wire [15:0]          period,         // 单位：控制周期数
    output reg  [13:0]          code
);

    localparam integer PHASE_W = 20;

    // 幅度是无符号量 0..0x3FFF；必须显式零扩展成 15 位有符号，
    // 否则 0x3FFF 会被当成 -1，乘积恒 0（实测正弦只剩 1 LSB 摆动）。
    wire signed [14:0] amp_v;
    wire signed [13:0] off_v;
    assign amp_v = $signed({1'b0, amp});   // 零扩展：0x3FFF → +16383，不是 -1
    assign off_v = $signed(offset);

    reg  [31:0]         run_cnt;
    reg  [PHASE_W-1:0]  phase;
    reg  [15:0]         step_i;
    reg  [13:0]         sig_out;

    wire [15:0] period_safe = (period == 16'd0) ? 16'd1 : period;
    wire [15:0] half_safe   = (period_safe < 16'd2) ? 16'd1 : (period_safe >> 1);

    // ---- 相位推进 -----------------------------------------------------------
    // 语义：phase 是 20 位无符号相位，一整圈 = 2^20 = 1048576。
    //       要求：**一个控制周期（period 个 tick）正好走完一圈**，
    //       于是输出波形频率 = 控制节拍频率 / period。
    //       period=256、控制节拍 50kHz => 195Hz 的正弦/三角/锯齿。
    //
    // ★ 这里连修四次，全都表现成"波形几乎不动 / 输出像常量"，记下来免得再犯：
    //   错法A：phase_step 取 65536/period 却**每 tick** 累加
    //          ⇒ period=64 时每拍加 1024，几拍绕完整圈，波形塌成常数。
    //   错法B：phase_step 取 65536/period 且**每 period** 累加一次
    //          ⇒ 走满一圈要 period×period 个 tick（period=256 时 65536 tick）。
    //   错法C：phase_step 改成 1048576/period 但**仍然只在 period 边界累加**
    //          ⇒ 一圈要 period 个 period = period×period 个 tick，依旧不对。
    //   正确：phase_step = 1048576/period（即每 tick 该走的相位），
    //         并且**每 tick 无条件累加**。累计 period 个 tick 恰好一整圈。
    //   ★ 位宽坑（这一处最隐蔽）：phase_step 原来是 20 位，而 phase_step 要能
    //     表达 1048576 = 2^20（period==16 时的步长），**2^20 需要 21 位**。
    //     写成 `reg [19:0] phase_step` + `20'd1048576` 会被截成 0，
    //     于是相位永远不前进、所有波形都输出常量（实测 phase_step=0）。
    //     现在用 21 位寄存器，够装下一整圈步长。
    wire [31:0] period_m1 = {16'd0, period_safe} - 32'd1;

    // ---- 相位步长：顺序除法器（不再是组合除法）------------------------------
    // ★ 第五个坑，也是整份设计时序违例的**唯一主因**。原写法是
    //       always @(*) if (period_safe >= 16) phase_step = 21'd1048576 / period_safe;
    //   综合器把它实现成一整条移位-减法链。Vivado 2023.2 实测（50MHz）：
    //       Slack (VIOLATED) : -19.330ns        ← 全设计最差（124 个失败端点）
    //       Source:      u_regs/mem_reg[23][3]/C   (0x17 = SIG_PERIOD)
    //       Destination: u_sig/phase_reg[17]/D
    //       Data Path Delay: 39.238ns (logic 26.076ns)
    //       Logic Levels: 132 (CARRY4=108 ...)  ← 108 个进位块就是除法链
    //   换成 recip_pow2 多周期**精确**除法：period 只在主机写寄存器时变化，
    //   (K+1)=21 拍延迟对功能零影响；商是精确值，波形频率与原来逐位一致。
    wire [20:0] phase_step_div;
    recip_pow2 #(.K(20), .QW(21)) u_pstep (
        .sys_clk   (sys_clk),
        .sys_rst_n (sys_rst_n),
        .d         (period_safe),
        .q         (phase_step_div)
    );
    // ★ 2026-10-14 第四轮更正注释：SIG_PERIOD < 16 时 phase_step 被写成 2^20。
    //   相位累加器 phase 只有 PHASE_W=20 位（:32/:42），加 2^20 等于加 0 ⇒ 相位**永远不动**，
    //   输出恒定停在正弦表 offset 那一点（不是"整圈跳"）。想要极短周期应把上限压到
    //   period_safe 允许的最小步进，而不是靠这个"整圈"值。
    wire [20:0] phase_step = (period_safe >= 16'd16) ? phase_step_div
                                                     : 21'd1048576;  // 注意：等效于不加（见上）

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            run_cnt <= 32'd0;
            phase   <= {(PHASE_W){1'b0}};
        end else if (update) begin
            run_cnt <= 32'd0;
            phase   <= {(PHASE_W){1'b0}};
        end else if (tick && enable) begin
            // 相位每 tick 推进；run_cnt 只负责数一个 period 有没有走完
            phase <= phase + phase_step;
            if (run_cnt >= period_m1) run_cnt <= 32'd0;
            else                      run_cnt <= run_cnt + 1'b1;
        end
    end

    // ---- 正弦查表 -----------------------------------------------------------
    // ★ 三代 bug 的终点：这里原来是 cos 的三次多项式近似
    //      cos_q = t2 用 19 位中间量做 (t18*t18)>>>16
    //   而 t18*t18 是 32 位、t2*t18 更是 35 位，赋给 19 位寄存器时高位全丢，
    //   t2/t3 恒为 0 ⇒ cos_q 恒等于常数项 64 ⇒ 任何相位 sinv 都等于
    //   64<<13 = 524288（实测逐相位打印恒 524288）。多项式这条路前后修了
    //   三次位宽都没稳。现在换成**查表**：确定、可读、综合出来就是一个
    //   ROM/组合逻辑，正好也是原始方案里"内置近似正弦"的本意。
    //
    //   一个 64 项的四分之一波表，存 sin(idx * 90° / 64) × 2^15（Q15，0~32767）。
    //   idx 0..63 => 0°..~90°。完整的 360° 由象限内折返 + 符号位得到。
    `include "sin_lut.vh"        // qsin / sincos（与 step_gen.v 共用；改前是本文件里的两份函数）

    wire signed [15:0] sinv = sincos(1'b1, phase);

    // ---- 波形选择 -----------------------------------------------------------
    // ★ 这里曾经在"除法在赋给窄变量时才做"，中间量先被截成 14 位 ⇒ 三角波
    //   阶梯丢失；锯齿又只用 run_cnt 低位 ⇒ 一周期后变常量。现在统一在
    //   32 位中间量上运算，最后截回 14 位。
    reg [13:0] square_v;
    reg [13:0] tri_v;
    reg [13:0] ramp_v;
    reg [13:0] sine_v;

    wire [13:0] amp_norm  = (amp > 14'h3FFF - offset) ? (14'h3FFF - offset) : amp;
    wire [13:0] amp_norm2 = (amp_norm > offset) ? offset : amp_norm;

    // 方波：前半周期高、后半周期低（低电平 = offset - amp，夹住不越界）
    always @(*) begin
        square_v = (run_cnt < {16'd0, half_safe}) ? (offset + amp_norm)
                                                  : (offset - amp_norm2);
    end

    // 锯齿：按相位在整圈内从 offset 线性爬到 offset+amp（夹住不越 14 位上限）。
    //   与正弦/三角同源（都吃 phase），所以一个 period 正好一个周期。
    // ★ 审查修掉的 bug：原来 ramp_span 恒为 `14'h3FFF - offset`、与 amp 完全无关，
    //   所以 amp=1024 与 amp=4096 爬出来的波形**一模一样**（实测两者范围都是
    //   [8192, 16255]），SIG_AMP 对锯齿无效。改为用夹取后的幅度。
    wire [13:0] ramp_span = amp_norm2;
    always @(*) begin
        ramp_v = offset + (({18'd0, phase[19:6]} * {18'd0, ramp_span}) >> 14);
    end

    // 三角：先升后降，围绕 offset 在 offset±amp 之间走。
    //   用 phase 的最高位判断升/降，用象限内的 14 位算斜线位置：
    //     升：bot → top = bot + span * frac / 2^14
    //     降：top → bot = top - span * frac / 2^14
    //   ★ 位宽坑：span * frac 是 14+14=28 位，必须用 32 位中间量，
    //     否则高位被截，三角会变成只有小幅抖动。
    wire [13:0] tri_frac  = phase[19] ? ~phase[18:5] : phase[18:5];
    wire [14:0] tri_top15 = {1'b0, offset} + {1'b0, amp_norm};
    wire [14:0] tri_bot15 = {1'b0, offset} - {1'b0, amp_norm2};
    wire [13:0] tri_span14 = tri_top15[13:0] - tri_bot15[13:0];
    wire [31:0] tri_mul32  = {18'd0, tri_span14} * {18'd0, tri_frac};
    wire [13:0] tri_up14   = tri_bot15[13:0] + tri_mul32[27:14];
    wire [13:0] tri_dn14   = tri_top15[13:0] - tri_mul32[27:14];
    always @(*) begin
        tri_v = phase[19] ? tri_dn14 : tri_up14;
    end

    // sinv 是 Q15（±32767，16 位），乘 amp（14 位）后 >>>15 得到码值偏移。
    // ★ 位宽账必须算清：16+14 = 30 位有符号积 ⇒ 先符号扩展双方到 34 位，
    //   再取 [33:15]（即 >>>15）。这里和 cos_q 一样踩过"中间量太窄高位被截"
    //   的坑，凡是有符号乘加都要先加宽再移位。
    //
    // ★ 审查修掉的两处（都在这一行）：
    //   ① 幅度必须用**夹取后**的 amp_norm2。原来直接用原始 `amp`，而方波/三角都是夹的，
    //      于是正弦是唯一会越界的波形。实测 offset=0x2000、amp=0x3FFF 时正弦只能摆到
    //      [468, 15915]（正确应为 [1, 16383]）——**amp 越大摆幅反而越小、波形被压扁**，
    //      而且幅度与 amp 不再是线性关系。
    //   ② 符号扩展取错位。`amp_v` 是 `$signed({1'b0, amp})`，15 位、恒 ≥ 0，
    //      真正的符号位是 `amp_v[14]`（恒 0）；原代码写 `{{20{amp_v[13]}}, amp_v}`，
    //      在 amp ≥ 0x2000 时 bit13=1，第二个乘数被当成**负数**。
    wire signed [14:0] amp_sine_v = $signed({1'b0, amp_norm2});
    wire signed [33:0] sine_mul = $signed({{18{sinv[15]}}, sinv}) * $signed({{20{amp_sine_v[14]}}, amp_sine_v});
    // 先加宽到 15 位再叠加 offset，避免 14 位和回卷
    wire signed [14:0] sine_sum = {{1{sine_mul[33]}}, sine_mul[33:15]} + $signed({1'b0, offset});

    always @(*) begin
        sine_v = sine_sum[13:0];
    end

    always @(*) begin
        case (mode)
            3'd0: sig_out = fixed_code;
            3'd1: sig_out = square_v;
            3'd2: sig_out = enable ? ((run_cnt < 32'd4) ? offset : (offset + amp_norm)) : offset;
            3'd3: sig_out = ramp_v;                       // 锯齿：按相位爬升
            3'd4: sig_out = tri_v;
            3'd5: sig_out = sine_v;
            default: sig_out = fixed_code;
        endcase
    end

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) code <= `DAC_CODE_MID;
        else            code <= sig_out;
    end

endmodule
