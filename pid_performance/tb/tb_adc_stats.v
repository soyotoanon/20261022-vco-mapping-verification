`timescale 1ns / 1ps
// ============================================================================
//  tb_adc_stats.v —— 【09/10】统计模块的窗口口径、均值/方差算法、clr 与饱和
//
//  第二轮自查重写了 adc_stats（移位量、窗口丢样本、clr 端口、乘法位宽收窄），
//  但 tb 目录里**没有任何 testbench 覆盖它**。本 tb 补上，重点：
//    [W] 窗口正好 = 2^log2_n 个样本（不是 2^log2_n - 1）
//    [M] mean = 累加和 >> log2_n（不是 >> LOG2_W）
//    [V] var_q4 = (E[x²] - E[x]²) × 16
//    [C] clr 同时清累加器与 min/max
//    [S] 方差超出 16 位时饱和到 0xFFFF（不是回卷）
//  log2_n = 2 ⇒ 窗口 4 个样本。
// ============================================================================
module tb_adc_stats;

    reg         sys_clk = 1'b0;
    reg         sys_rst_n = 1'b0;
    reg         tick = 1'b0;
    reg         enable = 1'b0;
    reg         clr = 1'b0;
    reg  [4:0]  log2_n = 5'd2;
    reg  [13:0] sample = 14'd0;

    wire [45:0] sum_out;
    wire [45:0] var_out;
    wire [15:0] mean;
    wire [15:0] var_q4;
    wire [13:0] min_code;
    wire [13:0] max_code;

    integer pass_cnt = 0;
    integer fail_cnt = 0;

    adc_stats #(.LOG2_W(5)) dut (
        .sys_clk  (sys_clk),
        .sys_rst_n(sys_rst_n),
        .tick     (tick),
        .enable   (enable),
        .clr      (clr),
        .log2_n   (log2_n),
        .sample   (sample),
        .sum_out  (sum_out),
        .var_out  (var_out),
        .mean     (mean),
        .var_q4   (var_q4),
        .min_code (min_code),
        .max_code (max_code)
    );

    always #10 sys_clk = ~sys_clk;

    task check(input [255:0] name, input [31:0] got, input [31:0] exp);
        begin
            if (got === exp) begin
                pass_cnt = pass_cnt + 1;
                $display("PASS %0s: got %0d (0x%h)", name, got, got);
            end else begin
                fail_cnt = fail_cnt + 1;
                $display("FAIL %0s: got %0d (0x%h), expected %0d (0x%h)",
                         name, got, got, exp, exp);
            end
        end
    endtask

    // 一个控制拍：把 sample 送进去并产生一个 tick 脉冲
    task do_tick(input [13:0] s);
        begin
            @(negedge sys_clk);
            sample = s;
            tick   = 1'b1;
            @(negedge sys_clk);
            tick   = 1'b0;
        end
    endtask

    task do_reset;
        begin
            sys_rst_n = 1'b0;
            tick = 1'b0; enable = 1'b0; clr = 1'b0; sample = 14'd0;
            repeat (4) @(negedge sys_clk);
            sys_rst_n = 1'b1;
            repeat (2) @(negedge sys_clk);
        end
    endtask

    initial begin
        $display("===== tb_adc_stats start =====");
        do_reset;
        enable = 1'b1;

        // ---- [W][M][V] 窗口 4：100,100,100,140 -> mean=110, var_q4=4800 ----
        do_tick(14'd100);
        do_tick(14'd100);
        do_tick(14'd100);
        // 3 个样本后窗口还没满（老实现 2^log2_n - 1 = 3 个样本就出结果，这里应是 0）
        #1;
        check("W3 not yet full", {16'd0, mean}, 32'd0);

        do_tick(14'd140);           // 第 4 个样本 -> 窗口满，本拍就用它算结果
        #1;
        check("M mean=110",   {16'd0, mean},   32'd110);
        check("V varq4=4800", {16'd0, var_q4}, 32'd4800);
        check("W sum_out=440", sum_out[31:0],  32'd440);
        check("X min=100",    {18'd0, min_code}, 32'd100);
        check("Y max=140",    {18'd0, max_code}, 32'd140);

        // ---- [W] 下一窗口从头开始：3 个 200 不出结果，第 4 个才出 ----
        do_tick(14'd200);
        do_tick(14'd200);
        do_tick(14'd200);
        #1;
        check("W window restart keeps last mean", {16'd0, mean}, 32'd110);
        do_tick(14'd200);
        #1;
        check("M second window mean=200", {16'd0, mean}, 32'd200);
        check("V second window var=0",    {16'd0, var_q4}, 32'd0);

        // ---- [C] clr：累加器与 min/max 一起清零 ----
        @(negedge sys_clk); clr = 1'b1;
        @(negedge sys_clk); clr = 1'b0;
        #1;
        check("C mean cleared",   {16'd0, mean},     32'd0);
        check("C varq4 cleared",  {16'd0, var_q4},   32'd0);
        check("C min=0x3FFF",     {18'd0, min_code}, 32'd16383);
        check("C max=0",          {18'd0, max_code}, 32'd0);

        // ---- [S] 饱和：0,0x3FFF,0,0x3FFF -> mean=0x1FFF, 方差×16 超出 16 位 ----
        do_tick(14'd0);
        do_tick(14'h3FFF);
        do_tick(14'd0);
        do_tick(14'h3FFF);
        #1;
        check("S mean=0x1FFF",  {16'd0, mean},   32'h00001FFF);
        check("S varq4 sat",    {16'd0, var_q4}, 32'h0000FFFF);

        // ---- 关闭 enable 后 tick 不再累加 ----
        enable = 1'b0;
        do_tick(14'd3000);
        do_tick(14'd3000);
        do_tick(14'd3000);
        do_tick(14'd3000);
        #1;
        check("E enable=0 freezes", {16'd0, mean}, 32'h00001FFF);

        $display("===== tb_adc_stats 结果：%0d 项通过，%0d 项失败 =====", pass_cnt, fail_cnt);
        if (fail_cnt == 0) $display("result: PASS");
        else               $display("result: FAIL");
        $finish;
    end

endmodule
