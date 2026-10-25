`timescale 1ns / 1ps
// ============================================================================
//  tb_sig_lut.v --- 验证正弦查表（qsin）与三角/锯齿/方波
//
//  ★★ 三个必须记住的教训（都是这个 tb 踩出来的，别再犯）★★
//    1) 一秒多少 tick 不重要，**跑满一整圈需要多少个 tick** 才重要：
//       整圈 tick 数 = phase 满量程 / phase_step
//                    = 2^20 / (65536 / period) = 16 × period
//       所以 period=1024 时，跑一圈要 16384 个 tick！只跑 4096 个 tick
//       相位才走 4%，采样点又总落在过零点上，就会误判成"正弦输出不动"。
//       本 tb 取 period=256 ⇒ 一圈 4096 tick，跑 2048 tick = 半圈够看形状。
//    2) phase_step = 65536 / period_safe，(slot) 只在 period_safe >= 16 时成立；
//       period 太小（<16）会退化成"每 tick 跳整圈"，那才是真的常量输出。
//    3) 波形每 tick 才走一步 ⇒ 观察必须在打 tick 的过程中，不能先打完再空跑。
//
//  跑法：xvlog -i rtl rtl/*.v tb/tb_sig_lut.v ; xelab tb_sig_lut -s lut ; xsim lut -runall
// ============================================================================
module tb_sig_lut;

    reg         sys_clk = 0;
    reg         sys_rst_n = 0;
    reg         tick = 0;
    reg         enable = 0;
    reg         update = 0;
    reg  [2:0]  mode = 3'd5;
    reg  [13:0] fixed_code = 14'h1555;
    reg  [13:0] amp = 14'h0FFF;
    reg  [13:0] offset = 14'h2000;
    reg  [15:0] period = 16'd256;

    // period=256 => phase_step=4096 => 整圈只要 256 tick（一个控制周期 = 一个输出周期）
    localparam integer FULL_CYCLE = 256;

    wire [13:0] code;

    integer pass_cnt = 0;
    integer fail_cnt = 0;
    integer i;
    integer mn, mx;

    always #5 sys_clk = ~sys_clk;

    dac_sig_gen dut (
        .sys_clk (sys_clk), .sys_rst_n (sys_rst_n), .tick (tick),
        .enable (enable), .update (update), .mode (mode),
        .fixed_code (fixed_code), .amp (amp), .offset (offset),
        .period (period), .code (code)
    );

    task check;
        input [255:0] name;
        input         cond;
        begin
            if (cond) begin $display("  [PASS] %0s", name); pass_cnt = pass_cnt + 1; end
            else      begin $display("  [FAIL] %0s", name); fail_cnt = fail_cnt + 1; end
        end
    endtask

    task one_tick;
        begin
            @(negedge sys_clk);
            tick = 1'b1;
            @(posedge sys_clk);
            #1;
            tick = 1'b0;
            repeat (9) @(posedge sys_clk);
        end
    endtask

    task reset_run;
        begin
            update = 1'b1; @(posedge sys_clk); update = 1'b0;
        end
    endtask

    task sweep;
        input integer count;
        begin
            mn = 14'h3FFF; mx = 14'h0000;
            for (i = 0; i < count; i = i + 1) begin
                one_tick;
                if (code < mn[13:0]) mn = code;
                if (code > mx[13:0]) mx = code;
            end
        end
    endtask

    initial begin
        $display("");
        $display("========== tb_sig_lut: 正弦查表 / 各波形幅度 ==========");
        $display("  period=256 => phase_step=%0d => 整圈 %0d tick", dut.phase_step, FULL_CYCLE);
        sys_rst_n = 0; #200; sys_rst_n = 1; #200;
        enable = 1'b1;
        $display("  实测 phase_step=%0d period_m1=%0d", dut.phase_step, dut.period_m1);

        // ---------------- 正弦：跑满一整圈 ----------------
        $display("");
        $display("SINE: amp=0FFF off=2000 period=256，跑满一圈 %0d tick", FULL_CYCLE);
        mode = 3'd5; reset_run;
        sweep(FULL_CYCLE);
        $display("    SINE => min=%h max=%h  (期望 1001..2FFF)", mn, mx);
        check("SINE 峰值 ≈ offset+amp (2FFF)", mx > 14'h2E00);
        check("SINE 谷值 ≈ offset-amp (1001)", mn < 14'h1200);

        // 逐点看形状（每 1/16 圈打一次）
        $display("    逐点形状（每 1/16 圈）：");
        mode = 3'd5; reset_run;
        for (i = 0; i < 16; i = i + 1) begin
            repeat (FULL_CYCLE / 16) one_tick;
            $display("      phase=%0d sinv=%0d code=%h", dut.phase, dut.sinv, code);
        end

        // ---------------- 三角 ----------------
        $display("");
        $display("TRI: amp=0FFF off=2000 period=256");
        mode = 3'd4; reset_run;
        sweep(FULL_CYCLE);
        $display("    TRI => min=%h max=%h  (期望 1001..2FFF)", mn, mx);
        check("TRI 峰值 ≈ 2FFF", mx > 14'h2E00);
        check("TRI 谷值 ≈ 1001", mn < 14'h1200);

        // ---------------- 锯齿 ----------------
        $display("");
        $display("RAMP: amp=0FFF off=2000 period=256");
        mode = 3'd3; reset_run;
        sweep(FULL_CYCLE);
        // ★ 审查修正：锯齿原来**完全无视 SIG_AMP**（ramp_span 恒为 `0x3FFF - offset`），
        //   所以 amp=0x0FFF 时也照爬满到 0x3FFF，而 amp 改大改小波形**一模一样**。
        //   修正后 ramp_span = amp_norm2 ⇒ 期望范围 = 0x2000 .. 0x2000+0x0FFF = 0x2FEF。
        $display("    RAMP => min=%h max=%h  (期望 2000 爬到 2000+amp = 2FEF)", mn, mx);
        // ★ 第四轮自查：下界原来留了 495 码余量（mx > 0x2E00），幅度错 12% 也能过；
        //   锯齿起点就是 offset、峰值 = offset+amp_norm2-1，都是确定值，按确定值判。
        check("RAMP 起点 = offset (2000)", mn == 14'h2000);
        check("RAMP 峰值 = offset+amp-1 (2FEF)", mx == 14'h2FEF);

        // ---------------- 方波 ----------------
        $display("");
        $display("SQUARE: amp=0FFF off=2000 period=256");
        mode = 3'd1; reset_run;
        sweep(FULL_CYCLE);
        $display("    SQUARE => min=%h max=%h  (期望 1001 / 2FFF)", mn, mx);
        check("SQUARE 高 ≈ 2FFF", mx > 14'h2E00);
        check("SQUARE 低 ≈ 1001", mn < 14'h1200);

        // ---------------- 固定码 ----------------
        $display("");
        $display("FIXED: mode=0");
        mode = 3'd0; reset_run;
        sweep(64);
        $display("    FIXED => code=%h  (期望 1555)", code);
        check("FIXED == fixed_code", code === 14'h1555);

        $display("");
        $display("========== 结果：%0d 通过 / %0d 失败 ==========", pass_cnt, fail_cnt);
        $finish;
    end

    initial begin
        #120_000_000;
        $display(" !! 超时");
        $finish;
    end

endmodule
