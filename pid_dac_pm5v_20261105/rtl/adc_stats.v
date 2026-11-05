`timescale 1ns / 1ps
// ============================================================================
//  adc_stats.v —— 【09 PD 平均值与噪声统计】【10 PD 电压与光功率换算】
//
//  【09】滑动式累加统计，窗口 = 2^log2_n (每控制周期采一个点)：
//        mean = 累加和 >> log2_n
//        var  = E[x²] - (E[x])²       —— 除法全部退化成右移
//      只算这两个量就够用：光强看 mean，噪声看 var 的平方根（上位机开方）。
//
//  【10】换算放在**上位机**（见 tools/pd_convert.py）。原因：
//        - 标定系数要跟着 PD 实测走，写死在 FPGA 里就得重综合
//        - 浮点换算在 FPGA 里要么占 DSP 要么掉精度
//        这里只提供 mean/var 的原始码值，换算由串口上传后在电脑上做。
//        两条权威公式：
//          光功率  P[mW] = V_PD / (R_load * R)   （R = PD 响应度 A/W，标定时定）
//          波长    λ[nm] = ln(r) / (gamma*(1/4 - 1/beta) - beta*ln(r))，文献公式
//
//  ---------------------------------------------------------------------------
//  ★ 2026-10-14 第二轮审查修复（三处，都会让本模块在上机时读出假数据）：
//    ① 移位量用错参数：原实现 mean/var 都右移 `LOG2_W`（默认 5），而窗口长度由
//       **端口 log2_n** 决定（STATS_LOG2 默认 12 ⇒ 4096 个样本）。4096 个样本只除 32
//       ⇒ 均值大 128 倍；且 (Σx/32)² > Σx²/32 ⇒ 46 位无符号回卷 ⇒ VAR_Q4 恒 0xFFFF。
//       现改为按 log2_n 动态移位，均值带饱和。
//    ② 窗口丢样本：窗口满那一拍既累加又清零（同一条 always 里非阻塞后被覆盖）⇒
//       实际每窗只有 2^log2_n − 1 个样本；现用 sum_next/sq_next 先并入本拍样本再算结果。
//    ③ CTRL[10]「清统计」原是无消费者的死位（本模块没有 clr 端口、极值只能断电才复位）。
//       现增加 `clr` 输入清零累加器与 min/max；min/max 语义 = 自 clr/上电以来的极值。
//    ④ 综合时序：上面改成"动态移位 + 直接相乘"后一度写成 46×46 位乘法（92 位积），
//       综合后它成了全设计最差路径（`u_stats/acc_sum_reg[0] → var_q4_reg[*]`，
//       WNS 从 +4.75 ns 掉到 +0.73 ns）。现在按数据的**真实位宽**收窄：
//       样本 14 位 ⇒ `sq_ext = sample*sample`（28 位）、均值 15 位 ⇒ `mean_sq` 30 位；
//       `sq_avg` 取 32 位（真值 ≤ 0x3FFF² < 2^28）、方差×16 取 37 位后饱和。
// ============================================================================
module adc_stats #(
    parameter integer LOG2_W = 5,      // log2_n 端口的位宽（可表示的最大窗口指数 2^LOG2_W-1）
    parameter integer SUM_W  = 46
) (
    input  wire                 sys_clk,
    input  wire                 sys_rst_n,
    input  wire                 tick,
    input  wire                 enable,
    input  wire                 clr,          // 清零累加器与极值（接 CTRL[10]）
    input  wire [LOG2_W-1:0]    log2_n,       // 窗口 = 2^log2_n 个样本
    input  wire [13:0]          sample,       // ADC 码
    output reg  [SUM_W-1:0]     sum_out,
    output reg  [SUM_W-1:0]     var_out,
    output reg  [15:0]          mean,         // 码域均值
    output reg  [15:0]          var_q4,       // 方差 × 16，保 4 位小数
    output reg  [13:0]          min_code,
    output reg  [13:0]          max_code
);

    reg [SUM_W-1:0]            acc_sum;
    reg [SUM_W*2-1:0]          acc_sq;
    reg [SUM_W-1:0]            cnt;

    // ---- 窗口边界：累加满 2^log2_n 个样本 ⇒ 冻结并更新输出 ---------------
    wire [SUM_W-1:0] win_max = ({{(SUM_W-1){1'b0}}, 1'b1} << log2_n) - 1'b1;

    // ---- 本拍样本先并入"下一个和"，窗口满时用这个值算结果 -----------------
    wire [SUM_W-1:0]   s_ext    = {{(SUM_W-14){1'b0}}, sample};
    wire [27:0]        sq_ext   = sample * sample;                          // 14×14 → 28 位
    wire [SUM_W-1:0]   sum_next = acc_sum + s_ext;
    wire [SUM_W*2-1:0] sq_next  = acc_sq + {{(SUM_W*2-28){1'b0}}, sq_ext};

    // ---- 方差（组合算出，让 mean/var_q4 在同一个窗口边界更新）--------------
    //  E[x] = sum_next >> log2_n，E[x²] = sq_next >> log2_n（窗口正好 2^log2_n 个样本，
    //  由柯西–施瓦茨保证 E[x²] ≥ (E[x])²，无符号相减不会下溢）。
    //  位宽按真实数据收窄（见文件头 ④），否则 46×46 乘法会成为全设计最差路径。
    wire [14:0]        mean_avg = sum_next >> log2_n;                       // 真值 ≤ 0x3FFF 时无损
    wire [31:0]        sq_avg   = sq_next >> log2_n;                        // 真值 ≤ 0x3FFF² < 2^28
    wire [29:0]        mean_sq  = mean_avg * mean_avg;                      // 15×15 → 30 位
    wire [32:0]        var_dif  = {1'b0, sq_avg} - {3'b000, mean_sq};       // ≥ 0（窗口一致时）
    wire [36:0]        var_full = var_dif << 4;                             // 方差 × 16
    wire [15:0]        mean_sat = {1'b0, mean_avg};
    wire [15:0]        var_sat  = |var_full[36:16] ? 16'hFFFF : var_full[15:0];

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            acc_sum  <= {SUM_W{1'b0}};
            acc_sq   <= {(SUM_W*2){1'b0}};
            cnt      <= {SUM_W{1'b0}};
            sum_out  <= {SUM_W{1'b0}};
            var_out  <= {SUM_W{1'b0}};
            mean     <= 16'd0;
            var_q4   <= 16'd0;
            min_code <= 14'h3FFF;
            max_code <= 14'h0000;
        end else if (clr) begin
            // ★ CTRL[10] clr_stats：累加器与极值一起清零（极值不是"窗口内"，而是自 clr 以来）
            acc_sum  <= {SUM_W{1'b0}};
            acc_sq   <= {(SUM_W*2){1'b0}};
            cnt      <= {SUM_W{1'b0}};
            sum_out  <= {SUM_W{1'b0}};
            var_out  <= {SUM_W{1'b0}};
            mean     <= 16'd0;
            var_q4   <= 16'd0;
            min_code <= 14'h3FFF;
            max_code <= 14'h0000;
        end else if (enable) begin
            if (tick) begin
                acc_sum <= sum_next;
                acc_sq  <= sq_next;
                if (sample < min_code) min_code <= sample;
                if (sample > max_code) max_code <= sample;

                if (cnt >= win_max) begin
                    // 窗口满：注意 acc_sum/acc_sq 这里被下面的清零覆盖是有意的 ——
                    // 本拍样本已经并进 sum_next/sq_next 并算进结果，窗口正好 2^log2_n 个样本。
                    sum_out <= sum_next;
                    var_out <= {{(SUM_W-37){1'b0}}, var_full};
                    mean    <= mean_sat;
                    var_q4  <= var_sat;
                    acc_sum <= {SUM_W{1'b0}};
                    acc_sq  <= {(SUM_W*2){1'b0}};
                    cnt     <= {SUM_W{1'b0}};
                end else begin
                    cnt <= cnt + 1'b1;
                end
            end
        end
    end

endmodule
