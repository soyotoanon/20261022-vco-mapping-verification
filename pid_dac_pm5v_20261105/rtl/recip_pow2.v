`timescale 1ns / 1ps
// ============================================================================
//  recip_pow2.v —— 【顺序除法器】q = 2^K / d
//
//  为什么要有这个模块（这是整份设计时序违例的唯一主因）：
//    dac_sig_gen / step_gen 里的相位步长原本写成**组合除法**，例如
//        wire [20:0] phase_step = 21'd1048576 / period_safe;
//    其中 period_safe 是运行时寄存器（0x17 SIG_PERIOD）。综合器把它实现成
//    一整条移位-减法链，Vivado 2023.2 实测（50 MHz、xc7a35tfgg484-2）：
//        Slack (VIOLATED) : -19.330ns          ← 全设计最差
//        Source:      u_regs/mem_reg[23][3]/C  (0x17 = SIG_PERIOD)
//        Destination: u_sig/phase_reg[17]/D
//        Data Path Delay: 39.238ns (logic 26.076ns / route 13.162ns)
//        Logic Levels:    132  (CARRY4=108 LUT3=20 ...)   ← 108 个进位块＝除法链
//    124 个失败端点全部集中在同一量级（-18.5 ~ -19.3 ns）。
//
//  改法：把这个除法拆成**多周期顺序除法**。
//    除数 d 只在主机通过串口写寄存器时变化（人手操作，秒级），
//    而 (K+1) 个 sys_clk 只有几百 ns —— 延迟对功能**零影响**。
//    商是**精确值**（不是近似/查表近似），波形频率与原来逐位一致。
//
//  算法：标准恢复余数法（restoring division），被除数是常数 2^K：
//    从第 K 位往下扫；每位先把余数左移一位并塞入被除数当前位，
//    若 余数 >= d 则减 d 并把该商位置 1。
//    余数恒 < d <= 65535，故 余数寄存器 17 位足够（上界 2*65534+1 = 131069）。
//
//  调用约定：
//    - d 必须 >= 1（调用方已用 `(period==0)?1:period` 之类保证）
//    - QW 必须 >= K+1（d==1 时商恰为 2^K）
//    - d 变化后 q **立刻被清 0**（:67），并在 K+1 个 sys_clk 内算完；期间读到的
//      q 是 0 而**不是旧值**（第四轮自查更正：原来写"q 保持旧值"，与 :67 相反）
// ============================================================================
module recip_pow2 #(
    parameter integer K  = 16,   // 分子 = 2^K
    parameter integer QW = 24    // 商位宽（>= K+1）
) (
    input  wire          sys_clk,
    input  wire          sys_rst_n,
    input  wire [15:0]   d,      // 除数，必须 >= 1
    output reg  [QW-1:0] q       // 2^K / d
);

    localparam [4:0] K5 = K;     // K <= 31 ⇒ 5 位够用

    reg  [16:0] rem;             // 余数，恒 < d
    reg  [4:0]  i;               // 当前处理的被除数位
    reg  [15:0] d_lat;           // 锁存的除数（兼作"已算过"标记）
    reg         busy;

    wire        num_bit = (i == K5);             // 2^K 只有第 K 位是 1
    wire [16:0] rem_sh  = {rem[15:0], num_bit};
    wire [16:0] d_ext   = {1'b0, d_lat};
    wire        ge      = (rem_sh >= d_ext);
    wire [16:0] rem_nx  = ge ? (rem_sh - d_ext) : rem_sh;

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            rem   <= 17'd0;
            i     <= 5'd0;
            d_lat <= 16'd0;
            busy  <= 1'b0;
            q     <= {QW{1'b0}};
        end else if (!busy) begin
            if (d != d_lat) begin          // 除数变了 ⇒ 重新算一遍
                d_lat <= d;
                rem   <= 17'd0;
                i     <= K5;
                q     <= {QW{1'b0}};
                busy  <= 1'b1;
            end
        end else begin
            rem <= rem_nx;
            if (ge) q[i] <= 1'b1;
            if (i == 5'd0) busy <= 1'b0;
            else           i    <= i - 5'd1;
        end
    end

endmodule
