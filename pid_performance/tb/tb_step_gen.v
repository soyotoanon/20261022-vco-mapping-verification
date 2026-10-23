`timescale 1ns / 1ps
// ============================================================================
//  tb_step_gen.v —— 【17 阶跃】【19 正弦/三角】扰动发生器回归
//  （2026-10-14 第二轮自查新增）
//
//  为什么补这个 tb：step_gen 是【17】【19】的实体，**改前一个 testbench 都没有**，
//  于是两个真缺陷一直没人发现：
//    ① 正弦/三角周期 = 256×DIST_PERIOD²（同一模块的方波却是 DIST_PERIOD 拍），
//      默认 DIST_PERIOD=4096 ⇒ 一圈约 16 天；
//    ② 正弦用非奇函数的四次多项式，正峰 +2086 / 负峰 −4746（严重不对称）。
//  本 tb 直接断言：周期口径、峰值幅度口径、正负对称性、方波/阶跃形状、
//  enable/amp=0 的静默、amp 上限夹取、update 复位相位。
//  改回老实现的话，[A]/[B]/[D]/[G] 会立刻失败。
// ============================================================================
`include "hw_params.vh"

module tb_step_gen;

    reg sys_clk = 1'b0;
    always #10 sys_clk = ~sys_clk;              // 50 MHz

    reg        sys_rst_n = 1'b0;
    reg        tick      = 1'b0;
    reg        enable    = 1'b0;
    reg        update    = 1'b0;
    reg [2:0]  mode      = 3'd0;
    reg [15:0] amp       = 16'd1024;
    reg [15:0] period    = 16'd64;

    wire signed [15:0] delta;

    integer n_pass = 0, n_fail = 0;
    integer i, dv, dmin, dmax;
    integer prev_cross, meas_period;
    reg     prev_nonpos;

    task check;
        input [8*80-1:0] name;
        input            cond;
        begin
            if (cond) begin n_pass = n_pass + 1; $display("  PASS  %0s", name); end
            else      begin n_fail = n_fail + 1; $display("  FAIL  %0s", name); end
        end
    endtask

    step_gen dut (
        .sys_clk   (sys_clk),
        .sys_rst_n (sys_rst_n),
        .tick      (tick),
        .enable    (enable),
        .update    (update),
        .mode      (mode),
        .amp       (amp),
        .period    (period),
        .delta     (delta)
    );

    task one_tick;
        begin
            @(negedge sys_clk); tick = 1'b1;
            @(negedge sys_clk); tick = 1'b0;
        end
    endtask

    // 跑 n 个 tick：统计 delta 的 min/max，并量出「相邻两次上升过零」的间隔
    task run_wave;
        input integer n;
        begin
            dmin       = 32767;
            dmax       = -32768;
            prev_cross = -1;
            meas_period = -1;
            prev_nonpos = 1'b1;
            for (i = 0; i < n; i = i + 1) begin
                one_tick;
                dv = delta;
                if (dv > dmax) dmax = dv;
                if (dv < dmin) dmin = dv;
                if (prev_nonpos && (dv > 0)) begin
                    if (prev_cross >= 0) meas_period = i - prev_cross;
                    prev_cross = i;
                end
                prev_nonpos = (dv <= 0);
            end
        end
    endtask

    // |got - want| <= |want| × permille/1000
    function integer within;
        input integer got;
        input integer want;
        input integer permille;
        integer d, lim;
        begin
            d   = (got > want) ? (got - want) : (want - got);
            lim = (want >= 0) ? (want * permille / 1000) : ((-want) * permille / 1000);
            within = (d <= lim);
        end
    endfunction

    initial begin
        $display("=== tb_step_gen：扰动发生器回归 ===");
        sys_rst_n = 1'b0;
        repeat (4) @(negedge sys_clk);
        sys_rst_n = 1'b1;

        // ---------------- [A] 正弦：周期口径 = DIST_PERIOD 拍 ----------------
        mode = 3'd3; amp = 16'd1024; period = 16'd64; enable = 1'b1;
        update = 1'b1; one_tick; update = 1'b0;
        run_wave(3 * 64 + 8);
        $display("[A] 正弦 period=64 ：实测周期=%0d 拍  峰=+%0d/%0d", meas_period, dmax, dmin);
        check("正弦周期 = DIST_PERIOD 拍", meas_period == 64);
        check("正弦峰值 ≈ +amp（±2%）", within(dmax, 1024, 20));
        check("正弦谷值 ≈ -amp（±2%）", within(dmin, -1024, 20));
        check("正弦正负半周对称（±2%）", (dmax + dmin) <= 21 && (dmax + dmin) >= -21);

        // ---------------- [B] 周期随 DIST_PERIOD 线性（平方关系的判据）--------
        period = 16'd256;
        update = 1'b1; one_tick; update = 1'b0;
        run_wave(3 * 256 + 8);
        $display("[B] 正弦 period=256：实测周期=%0d 拍", meas_period);
        check("正弦周期 = 256 拍（不是 256*256^2）", meas_period == 256);

        // ---------------- [C] 方波 -------------------------------------------
        mode = 3'd2; period = 16'd64; amp = 16'd1024;
        update = 1'b1; one_tick; update = 1'b0;
        run_wave(3 * 64 + 8);
        $display("[C] 方波 period=64 ：实测周期=%0d 拍  峰=+%0d/%0d", meas_period, dmax, dmin);
        check("方波周期 = DIST_PERIOD 拍", meas_period == 64);
        check("方波幅度 = ±amp", (dmax == 1024) && (dmin == -1024));

        // ---------------- [D] 三角 -------------------------------------------
        mode = 3'd4; period = 16'd64; amp = 16'd1024;
        update = 1'b1; one_tick; update = 1'b0;
        run_wave(3 * 64 + 8);
        $display("[D] 三角 period=64 ：实测周期=%0d 拍  峰=+%0d/%0d", meas_period, dmax, dmin);
        check("三角周期 = DIST_PERIOD 拍", meas_period == 64);
        check("三角峰值 ≈ ±amp（±2%）", within(dmax, 1024, 20) && within(dmin, -1024, 20));
        check("三角正负半周对称（±2%）", (dmax + dmin) <= 21 && (dmax + dmin) >= -21);

        // ---------------- [E] 阶跃：每周期前 4 拍 0、之后 +amp ----------------
        mode = 3'd1; period = 16'd64; amp = 16'd100;
        update = 1'b1; one_tick; update = 1'b0;
        one_tick; one_tick; one_tick;
        check("阶跃前 4 拍保持 0", delta == 16'sd0);
        one_tick; one_tick;
        check("阶跃 4 拍后跳到 +amp", delta == 16'sd100);
        run_wave(64);
        check("阶跃只有 0 与 +amp 两个电平", (dmax == 100) && (dmin == 0));

        // ---------------- [F] enable=0 / amp=0 必须静默 ----------------------
        enable = 1'b0; mode = 3'd3; amp = 16'd1024; period = 16'd64;
        update = 1'b1; one_tick; update = 1'b0;
        run_wave(80);
        check("enable=0 时 delta 恒 0", (dmax == 0) && (dmin == 0));

        enable = 1'b1; amp = 16'd0;
        update = 1'b1; one_tick; update = 1'b0;
        run_wave(80);
        check("amp=0 时 delta 恒 0", (dmax == 0) && (dmin == 0));

        // ---------------- [G] amp 写满 16 位不应变成负数 ---------------------
        mode = 3'd2; amp = 16'hFFFF; period = 16'd64;
        update = 1'b1; one_tick; update = 1'b0;
        run_wave(3 * 64 + 8);
        $display("[G] amp=0xFFFF 方波：峰=+%0d/%0d", dmax, dmin);
        check("amp>0x7FFF 被夹到 0x7FFF（不是负数）",
              (dmax == 32767) && (dmin == -32767));

        // ---------------- [H] update 复位相位 --------------------------------
        mode = 3'd3; amp = 16'd1024; period = 16'd256;
        update = 1'b1; one_tick; update = 1'b0;
        run_wave(40);
        check("复位前波形确实在动", dmax > 0);
        update = 1'b1; one_tick; update = 1'b0;
        check("update 后相位回 0（delta 从 0 重新开始）", delta == 16'sd0);

        $display("=== tb_step_gen 结果：%0d PASS / %0d FAIL ===", n_pass, n_fail);
        if (n_fail != 0) $display("*** 有失败项 ***");
        $finish;
    end

endmodule
