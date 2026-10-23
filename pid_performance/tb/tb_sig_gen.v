`timescale 1ns / 1ps
// ============================================================================
//  tb_sig_gen.v --- 只测 dac_sig_gen 一个模块的最小探针
//
//  存在理由：在 light_lock_top 里正弦/三角/锯齿都"不动"，而寄存器值明明写
//  进去了。用一个不带任何外围的 tb 直接驱动 dac_sig_gen，就能立刻分清
//  "模块本身坏了" 还是 "顶层接线/寄存器通路坏了"。
//
//  ★ 两条踩过的坑，写在这里免得重犯：
//    1) 波形只在 tick 上步进 => 观察必须发生在打 tick 的过程中。
//       先打完 tick 再空跑观察窗，phase 是冻结的，会误判成"输出不动"。
//    2) tick 要严格一个 sys_clk 宽。
//  ★ 不要用 PowerShell 的 Set-Content -Encoding UTF8 改这个文件：
//    它会写 UTF-8 BOM / 把中文重新编码，xvlog 报
//    "syntax error near 'non-printable character with the hex value '0xef''"。
//
//  跑法：xvlog -i rtl rtl/*.v tb/tb_sig_gen.v ; xelab tb_sig_gen -s sg ; xsim sg -runall
// ============================================================================
module tb_sig_gen;

    reg         sys_clk = 0;
    reg         sys_rst_n = 0;
    reg         tick = 0;
    reg         enable = 0;
    reg         update = 0;
    reg  [2:0]  mode = 3'd0;
    reg  [13:0] fixed_code = 14'h1555;
    reg  [13:0] amp = 14'h3FFF;
    reg  [13:0] offset = 14'h2000;
    reg  [15:0] period = 16'd32;

    wire [13:0] code;

    integer pass_cnt = 0;
    integer fail_cnt = 0;
    integer i;
    integer mn, mx, changing;
    integer last_code;

    always #5 sys_clk = ~sys_clk;

    dac_sig_gen dut (
        .sys_clk    (sys_clk),
        .sys_rst_n  (sys_rst_n),
        .tick       (tick),
        .enable     (enable),
        .update     (update),
        .mode       (mode),
        .fixed_code (fixed_code),
        .amp        (amp),
        .offset     (offset),
        .period     (period),
        .code       (code)
    );

    task check;
        input [255:0] name;
        input         cond;
        begin
            if (cond) begin $display("  [PASS] %0s", name); pass_cnt = pass_cnt + 1; end
            else      begin $display("  [FAIL] %0s", name); fail_cnt = fail_cnt + 1; end
        end
    endtask

    // 一个 tick = 严格 1 个 sys_clk 宽，后接 9 拍空闲
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

    // 打 n 个 tick，统计 code 的变化，并在每个整数圈打印内部量
    task run_watch;
        input integer n;
        input [255:0] tag;
        begin
            mn = 14'h3FFF; mx = 14'h0000; changing = 0;
            last_code = -1;
            for (i = 0; i < n; i = i + 1) begin
                one_tick;
                if (code < mn[13:0]) mn = code;
                if (code > mx[13:0]) mx = code;
                if (last_code >= 0 && code !== last_code) changing = changing + 1;
                last_code = code;
                if (i % 16 == 0)
                    $display("    %0s tick=%0d phase=%0d sinv=%0d sine_mul=%0d sine_sum=%0d tri_v=%h ramp_v=%h code=%h",
                             tag, i, dut.phase, dut.sinv, dut.sine_mul, dut.sine_sum,
                             dut.tri_v, dut.ramp_v, code);
            end
            $display("    %0s => min=%h max=%h 变化次数=%0d", tag, mn, mx, changing);
        end
    endtask

    initial begin
        $display("");
        $display("========== tb_sig_gen: 只测信号发生器模块 ==========");
        sys_rst_n = 0;
        #200; sys_rst_n = 1;
        #200;

        enable  = 1'b1;
        period  = 16'd32;
        mode    = 3'd0;
        update  = 1'b1; @(posedge sys_clk); update = 1'b0;
        #500;
        check("FIXED: code == fixed_code", code === 14'h1555);

        // ---------------- 正弦 ----------------
        $display("");
        $display("SINE: amp=3FFF off=2000 period=32");
        mode = 3'd5;
        run_watch(64, "SINE");
        check("SINE 变化次数 > 4", changing > 4);
        // ★ 审查收紧阈值：原来写的是 `mx > 14'h3800` / `mn < 14'h0800`，太松 ——
        //   修 bug 之前 amp=0x3FFF 实测只有 [468, 15915]（幅度被压扁、且与 amp 非线性）
        //   也照样通过，这正是该缺陷长期没暴露的原因。
        //   修好后正弦改用夹取幅度 amp_norm2：offset=0x2000 ⇒ amp 夹到 0x1FFF，
        //   期望范围 ≈ 0x2000 ± 0x1FFF = [1, 16383]（实测修好后是 [3, 16380]）。
        //   阈值取 0x3F00(=16128)：坏值 15915 会失败，好值 16380 能通过。
        check("SINE 峰值接近 3FFF", mx > 14'h3F00);
        check("SINE 谷值接近 0000", mn < 14'h0100);

        // ---------------- 三角 ----------------
        $display("");
        $display("TRI: amp=3FFF off=2000 period=32");
        mode = 3'd4;
        update = 1'b1; @(posedge sys_clk); update = 1'b0;
        run_watch(64, "TRI");
        check("TRI 变化次数 > 4", changing > 4);

        // ---------------- 锯齿 ----------------
        $display("");
        $display("RAMP: amp=3FFF off=2000 period=32");
        mode = 3'd3;
        update = 1'b1; @(posedge sys_clk); update = 1'b0;
        run_watch(64, "RAMP");
        check("RAMP 变化次数 > 4", changing > 4);

        // ---------------- 方波 ----------------
        $display("");
        $display("SQUARE: amp=3FFF off=2000 period=32");
        mode = 3'd1;
        update = 1'b1; @(posedge sys_clk); update = 1'b0;
        run_watch(64, "SQUARE");
        // ★ 第四轮自查：原来只判 `changing > 0`（"在动"），幅度/偏置/极性全坏也能过。
        //   方波是精确 ±：offset=0x2000、amp=0x3FFF ⇒ amp_norm=amp_norm2=0x1FFF
        //   ⇒ 高 0x3FFF、低 0x0001，一个周期 2 次跳变、64 拍（period=32）共 4 次。
        check("SQUARE 高电平 = 0x3FFF", mx == 14'h3FFF);
        check("SQUARE 低电平 = 0x0001", mn == 14'h0001);
        check("SQUARE 64 拍内跳变 4 次", changing == 4);

        $display("");
        $display("========== 结果：%0d 通过 / %0d 失败 ==========", pass_cnt, fail_cnt);
        $finish;
    end

    initial begin
        #5_000_000;
        $display(" !! 超时");
        $finish;
    end

endmodule
