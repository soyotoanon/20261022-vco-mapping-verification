`timescale 1ns / 1ps
// ============================================================================
//  tb_sweep_gen.v —— 【07 DAC 自动扫描】回归（2026-10-14 第二轮自查新增）
//
//  为什么补这个 tb：sweep_gen 改前**一个 testbench 都没有**，于是两个真缺陷
//  一直没人发现：
//    ① 比较域是 14 位有符号 ⇒ 任何跨中点 0x2000 的向上扫描第一步就"到终点"
//       （0x1000 → 0x3000 直接跳到 0x3000；默认 0x0000 → 0x3FFF 同理）；
//    ② 每步实际保持 hold+1 个控制周期。
//  断言 [A]/[B]/[C] 就是这两条的判据：改回老实现立刻失败。
//  （断言名用 ASCII，避免 xsim 控制台按 GBK 打印中文变成乱码。）
// ============================================================================
`include "hw_params.vh"

module tb_sweep_gen;

    reg sys_clk = 1'b0;
    always #10 sys_clk = ~sys_clk;              // 50 MHz

    reg         sys_rst_n = 1'b0;
    reg         tick      = 1'b0;
    reg         enable    = 1'b1;
    reg         start     = 1'b0;
    reg [13:0]  start_code = 14'h1000;
    reg [13:0]  end_code   = 14'h3000;
    reg signed [23:0] step_code = 24'd16;
    reg [23:0]  hold      = 24'd0;
    reg         cont      = 1'b0;

    wire [13:0] code;
    wire        done;
    wire [15:0] step_count;

    integer n_pass = 0, n_fail = 0;
    integer i, gap, steps;
    reg [13:0] prev_code;
    reg [13:0] seen_first, seen_second, seen_third;

    task check;
        input [8*80-1:0] name;
        input            cond;
        begin
            if (cond) begin n_pass = n_pass + 1; $display("  PASS  %0s", name); end
            else      begin n_fail = n_fail + 1; $display("  FAIL  %0s", name); end
        end
    endtask

    sweep_gen dut (
        .sys_clk    (sys_clk),
        .sys_rst_n  (sys_rst_n),
        .tick       (tick),
        .enable     (enable),
        .start      (start),
        .start_code (start_code),
        .end_code   (end_code),
        .step_code  (step_code),
        .hold       (hold),
        .cont       (cont),
        .code       (code),
        .done       (done),
        .step_count (step_count)
    );

    task one_tick;
        begin
            @(negedge sys_clk); tick = 1'b1;
            @(negedge sys_clk); tick = 1'b0;
        end
    endtask

    // 干净地打一发 start（一个完整时钟宽，期间 tick=0）
    task pulse_start;
        begin
            @(negedge sys_clk); start = 1'b1;
            @(negedge sys_clk); start = 1'b0;
        end
    endtask

    task run_until_done;
        input integer maxn;
        begin
            i = 0;
            while (!done && i < maxn) begin one_tick; i = i + 1; end
        end
    endtask

    initial begin
        $display("===== tb_sweep_gen =====");
        #100; sys_rst_n = 1'b1;
        #40;

        // ---------------------------------------------------------------- [F]
        check("F1 reset: code = DAC mid 0x2000", code == `DAC_CODE_MID);
        check("F2 reset: done = 0",              done == 1'b0);

        // ------------------------------------------- [A] 跨中点向上扫描
        start_code = 14'h1000; end_code = 14'h3000; step_code = 24'd16;
        hold = 24'd0; cont = 1'b0; enable = 1'b1;
        pulse_start;
        check("A1 start: code = start_code 0x1000", code == 14'h1000);
        one_tick;
        check("A2 1 tick -> 0x1010 (not 0x3000, old bug)",
              code == 14'h1010);
        run_until_done(4000);
        check("A3 reached end 0x3000",  code == 14'h3000);
        check("A4 done = 1",            done == 1'b1);
        check("A5 step_count = 512",    step_count == 16'd512);

        // ------------------------------------------- [G] 单次模式到终点后冻结
        prev_code = code;
        steps     = step_count;
        for (i = 0; i < 50; i = i + 1) one_tick;
        check("G1 code frozen after done",       code == prev_code);
        check("G2 step_count frozen after done", step_count == steps);

        // ------------------------------------------- [B] 跨中点向下扫描
        start_code = 14'h3000; end_code = 14'h1000; step_code = 24'd16;
        hold = 24'd0; cont = 1'b0;
        pulse_start;
        check("B1 start: code = 0x3000", code == 14'h3000);
        one_tick;
        check("B2 1 tick -> 0x2FF0", code == 14'h2FF0);
        run_until_done(4000);
        check("B3 reached end 0x1000", code == 14'h1000);
        check("B4 done = 1",           done == 1'b1);
        check("B5 step_count = 512",   step_count == 16'd512);

        // ------------------------------------------- [C] 每步保持正好 hold 拍
        start_code = 14'h1000; end_code = 14'h1100; step_code = 24'd1;
        hold = 24'd4; cont = 1'b0;
        pulse_start;
        prev_code = code;
        gap = 0;
        for (i = 0; i < 200 && gap == 0; i = i + 1) begin
            one_tick;
            if (code != prev_code) gap = i + 1;
        end
        check("C1 hold=4 -> first step after 4 ticks", gap == 4);
        prev_code = code;
        gap = 0;
        for (i = 0; i < 200 && gap == 0; i = i + 1) begin
            one_tick;
            if (code != prev_code) gap = i + 1;
        end
        check("C2 hold=4 -> next step also 4 ticks", gap == 4);

        // ------------------------------------------- [D] step_code 取幅值（写负数等价）
        start_code = 14'h1000; end_code = 14'h1040; step_code = -24'sd8;
        hold = 24'd0; cont = 1'b0;
        pulse_start;
        one_tick;
        check("D1 negative step -> magnitude 8 (0x1008)", code == 14'h1008);
        run_until_done(2000);
        check("D2 reached 0x1040", code == 14'h1040);
        check("D3 step_count = 8", step_count == 16'd8);

        // ------------------------------------------- [E] start == end
        start_code = 14'h2345; end_code = 14'h2345; step_code = 24'd7;
        hold = 24'd0; cont = 1'b0;
        pulse_start;
        one_tick;
        check("E1 start == end -> code = 0x2345", code == 14'h2345);
        check("E2 start == end -> done = 1",      done == 1'b1);

        // ------------------------------------------- [H] 连续模式来回扫
        start_code = 14'h1000; end_code = 14'h1020; step_code = 24'd8;
        hold = 24'd0; cont = 1'b1;
        pulse_start;
        // 记录依次出现的三个"端点/折返"码：上端 0x1020、下端 0x1000、再回上端 0x1020
        seen_first = 14'hFFFF; seen_second = 14'hFFFF; seen_third = 14'hFFFF;
        prev_code = code;
        for (i = 0; i < 200; i = i + 1) begin
            one_tick;
            if (code != prev_code) begin
                if (code == 14'h1020 && seen_first  == 14'hFFFF) seen_first  = code;
                else if (code == 14'h1000 && seen_second == 14'hFFFF && seen_first != 14'hFFFF)
                    seen_second = code;
                else if (code == 14'h1020 && seen_second != 14'hFFFF && seen_third == 14'hFFFF)
                    seen_third = code;
            end
            prev_code = code;
        end
        check("H1 cont: reached upper end 0x1020", seen_first  == 14'h1020);
        check("H2 cont: came back to lower end 0x1000", seen_second == 14'h1000);
        check("H3 cont: went up again to 0x1020",  seen_third  == 14'h1020);
        check("H4 cont: done stays 0",             done == 1'b0);

        // ------------------------------------------- [I] enable = 0 时冻结
        enable = 1'b0;
        prev_code = code;
        for (i = 0; i < 20; i = i + 1) one_tick;
        check("I1 enable=0 freezes code", code == prev_code);
        enable = 1'b1;

        $display("=== tb_sweep_gen result: %0d PASS / %0d FAIL ===", n_pass, n_fail);
        $finish;
    end

endmodule
