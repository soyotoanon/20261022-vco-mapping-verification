`timescale 1ns / 1ps
`include "hw_params.vh"
// ============================================================================
//  tb_reg_probe.v —— 探针：**绕过串口**，直接层次化写寄存器堆
//
//  目的：把 tb_light_lock 里的失败切成两半
//    ① 如果本 tb 全 PASS ⇒ 数据通路没问题，故障在串口帧解析/写入时序
//    ② 如果本 tb 也 FAIL ⇒ 故障在数据通路或寄存器映射
//  xvlog -i rtl rtl/*.v tb/tb_reg_probe.v ; xelab tb_reg_probe -s p ; xsim p -runall
// ============================================================================
module tb_reg_probe;

    reg         sys_clk = 0;
    reg         sys_rst_n = 0;
    reg  [13:0] adc_d = 14'h2000;
    reg         adc_otr = 0;
    reg         uart_rx_line = 1;

    wire        adc_clk, adc_oeb_n, adc_pdn;
    wire [13:0] da1_data, da2_data;
    wire        uart_tx;

    integer pass_cnt = 0;
    integer fail_cnt = 0;

    always #5 sys_clk = ~sys_clk;

    light_lock_top #(.LOSS_LOG2(4)) dut (
        .sys_clk      (sys_clk),
        .sys_rst_n    (sys_rst_n),
        .adc_d        (adc_d),
        .adc_otr      (adc_otr),
        .adc_clk      (adc_clk),
        .adc_oeb_n    (adc_oeb_n),
        .adc_pdn      (adc_pdn),
        .da1_data     (da1_data),
        .da1_clk      (da1_clk),
        .da1_wrt      (da1_wrt),
        .da2_data     (da2_data),
        .da2_clk      (da2_clk),
        .da2_wrt      (da2_wrt),
        .uart_tx      (uart_tx),
        .uart_rx      (uart_rx_line),
        .led_heartbeat(),
        .led_status   ()
    );

    task check;
        input [255:0] name;
        input         cond;
        begin
            if (cond) begin $display("  [PASS] %0s", name); pass_cnt = pass_cnt + 1; end
            else      begin $display("  [FAIL] %0s", name); fail_cnt = fail_cnt + 1; end
        end
    endtask

    // 直接写寄存器堆（层次化强制）
    // ★ 第四轮自查认定：这条捷径**整份绕过了串口通路**（帧校验、两段式提交、地址译码、
    //   reg_we 脉冲宽度全被跳过），所以本 tb 只能证明"寄存器 → 各模块"的连线对吗，
    //   **不能**证明写通路可用。真实 8 字节帧驱动真 reg_file 的用例在
    //   `tb/tb_light_loop.v`（A/B/C/F 段：写→读回、相邻地址不串扰、只读地址写无效）。
    task force_reg;
        input [5:0]  a;
        input [15:0] d;
        begin
            @(negedge sys_clk);
            dut.u_regs.mem[a] = d;
            @(negedge sys_clk);
        end
    endtask

    integer i, per, mn, mx, edges, tick_cnt;

    initial begin
        $display("");
        $display("========== tb_reg_probe: 绕过串口直接写寄存器 ==========");
        sys_rst_n = 0;
        #200; sys_rst_n = 1;
        #2000;

        force_reg(6'h10, 16'd4);        // 200 ns 控制周期
        #2000;

        $display("");
        $display("P1 读回检查：写入后 mem 是否真的变了");
        force_reg(6'h02, 16'h1555);
        check("mem[0x02] == 1555", dut.u_regs.mem[6'h02] === 16'h1555);
        check("reg_file 输出 dac_code == 1555", dut.dac_code_cfg === 16'h1555);

        $display("");
        $display("P2 M2 固定码模式（ctrl[3:2]=2 ⇒ ctrl=0x0008）：da1 应等于 DAC_CODE");
        force_reg(6'h00, 16'h0008);
        #2000;
        $display("    ctrl=%h mode=%0d code_sel=%h dac_code_cfg=%h sig_code=%h da1=%h",
                 dut.ctrl, dut.mode, dut.code_sel, dut.dac_code_cfg, dut.sig_code, da1_data);
        check("ctrl == 0008", dut.ctrl === 16'h0008);
        check("mode == 2", dut.mode === 2'd2);
        check("da1 == 1555", da1_data === 14'h1555);
        force_reg(6'h02, 16'h0F0F);
        #2000;
        check("改 DAC_CODE 立即生效 0F0F", da1_data === 14'h0F0F);

        $display("");
        $display("P3 M0 直通：adc_d 应原样出现在 da1");
        force_reg(6'h00, 16'h0002);
        adc_d = 14'h1234;
        #2000;
        check("code_sel == 1234", dut.code_sel === 14'h1234);
        check("da1 == 1234", da1_data === 14'h1234);

        $display("");
        $display("P4 ADC 时钟：实测的是**翻转间隔**（两次 posedge 之差）");
        $display("   语义：翻转间隔 = (ADC_HALF+1) 拍 ⇒ ADC 周期 = 2*(ADC_HALF+1)*20ns");
        force_reg(6'h11, 16'd4);
        #2000;
        #20000;
        @(posedge adc_clk); i = $time;
        @(posedge adc_clk); per = $time - i;
        $display("    ADC_HALF=4 翻转间隔 = %0d ns ⇒ ADC 周期 %0d ns (%.1f MHz)",
                 per, per*2, 1000.0/(per*2));
        check("ADC_HALF=4 ⇒ 翻转间隔 100ns ⇒ 5MHz", per == 100);
        force_reg(6'h11, 16'd9);
        #20000;
        @(posedge adc_clk); i = $time;
        @(posedge adc_clk); per = $time - i;
        $display("    ADC_HALF=9 翻转间隔 = %0d ns ⇒ ADC 周期 %0d ns (%.1f MHz)",
                 per, per*2, 1000.0/(per*2));
        check("ADC_HALF=9 ⇒ 翻转间隔 200ns ⇒ 2.5MHz", per == 200);
        force_reg(6'h11, 16'd4);

        // ---- 正弦幅度：BUG#2 修好之前这里输出恒为 0x2000（不动）----
        $display("");
        $display("P5 信号发生器正弦幅度（sincos 定点位宽，曾恒 0）");
        force_reg(6'h05, 16'h0000);   // 暂不直通
        force_reg(6'h10, 16'd4);      // 控制周期 100ns，让相位快走
        force_reg(6'h13, 16'd5);      // SIG_MODE = 5 正弦
        force_reg(6'h16, 16'h2000);   // SIG_OFF 中点
        force_reg(6'h15, 16'h3FFF);   // SIG_AMP 满幅
        force_reg(6'h17, 16'd64);     // SIG_PERIOD = 64 步（仿真里跑完一整周）
        force_reg(6'h12, 16'h0001);   // SIG_EN
        force_reg(6'h00, 16'h0004);   // mode=1 信号发生器
        // ★ 一个正弦周期 = SIG_PERIOD 个控制周期 = 64*100ns = 6.4us。
        //   观察窗必须 ≥ 一个整周期，否则 min/max 只会看到一小段弧（曾因此误判）。
        mn = 14'h3FFF; mx = 0; edges = 0;
        adc_d = 14'h2000;
        for (i = 0; i < 3000; i = i + 1) begin
            @(posedge sys_clk);
            if (da1_data > mx) mx = da1_data;
            if (da1_data < mn) mn = da1_data;
            if (da1_data != 14'h2000) edges = edges + 1;
        end
        $display("    正弦输出 min=%h max=%h 偏离中点拍数=%0d / 3000", mn, mx, edges);
        $display("    内部：run_cnt=%0d phase=%0d phase_step=%0d period_safe=%0d",
                 dut.u_sig.run_cnt, dut.u_sig.phase, dut.u_sig.phase_step,
                 dut.u_sig.period_safe);
        $display("    通路：ctrl=%h mode=%0d sig_en=%h sig_en[0]=%b sig_en[1]=%b sig_code=%h",
                 dut.ctrl, dut.mode, dut.sig_en_r, dut.sig_en_r[0], dut.sig_en_r[1],
                 dut.sig_code);
        $display("    通路：update=%b enable=%b tick=%b ctrl_half=%0d period=0x%h",
                 dut.u_sig.update, dut.u_sig.enable, dut.u_sig.tick,
                 dut.ctrl_half, dut.u_sig.period);
        $display("    波形参数：sig_amp_r=%h sig_off_r=%h sig_mode_r=%h sig_period_r=%0d",
                 dut.sig_amp_r, dut.sig_off_r, dut.sig_mode_r, dut.sig_period_r);
        $display("    模块内：amp=%h offset=%h amp_norm=%h amp_norm2=%h",
                 dut.u_sig.amp, dut.u_sig.offset, dut.u_sig.amp_norm, dut.u_sig.amp_norm2);
        $display("    正弦内部：sinv=%0d sine_mul=%0d sine_sum=%0d sine_v=%h sig_code=%h code_sel=%h da1=%h",
                 $signed(dut.u_sig.sinv), $signed(dut.u_sig.sine_mul),
                 $signed(dut.u_sig.sine_sum), dut.u_sig.sine_v,
                 dut.sig_code, dut.code_sel, da1_data);
        $display("    幅度/积原始：amp=%b amp_v(signed)=%0d amp_v(unsigned)=%0d",
                 dut.u_sig.amp, $signed(dut.u_sig.amp_v), dut.u_sig.amp_v);
        $display("    积原始：sine_mul=%b  sine_mul[33:15]=%b (%0d)",
                 dut.u_sig.sine_mul, dut.u_sig.sine_mul[33:15],
                 $signed(dut.u_sig.sine_mul[33:15]));
        for (i = 0; i < 8; i = i + 1) begin
            if (dut.tick) begin
                @(posedge sys_clk);
                $display("      phase=%h sinv=%0d amp_v=%0d sine_mul=%0d [33:15]=%0d sine_sum=%0d sine_v=%h",
                         dut.u_sig.phase, $signed(dut.u_sig.sinv), $signed(dut.u_sig.amp_v),
                         $signed(dut.u_sig.sine_mul), $signed(dut.u_sig.sine_mul[33:15]),
                         $signed(dut.u_sig.sine_sum), dut.u_sig.sine_v);
            end else begin
                @(posedge sys_clk);
                i = i - 1;
            end
        end
        // 再等一段，看 phase 到底动没动，并统计 tick 脉冲数
        #20000;
        $display("    再等 20000ns 后：run_cnt=%0d phase=%0d", dut.u_sig.run_cnt, dut.u_sig.phase);
        tick_cnt = 0;
        for (i = 0; i < 3000; i = i + 1) begin
            @(posedge sys_clk);
            if (dut.tick) tick_cnt = tick_cnt + 1;
        end
        $display("    3000 个 sys_clk 内 tick 脉冲数 = %0d（期望约 300）", tick_cnt);
        $display("    ctrl_half_w=%0d u_tick.half=%0d", dut.ctrl_half_w, dut.u_tick.half);
        $display("    u_tick.cnt=%0d u_tick.phase=%b u_tick.tick=%b", 
                 dut.u_tick.cnt, dut.u_tick.phase, dut.u_tick.tick);
        for (i = 0; i < 6; i = i + 1) begin
            @(posedge sys_clk);
            $display("      t=%0t cnt=%0d phase=%b half=%0d tick=%b",
                     $time, dut.u_tick.cnt, dut.u_tick.phase, dut.u_tick.half, dut.u_tick.tick);
        end
        // ★ 第四轮自查：原来只判"max > 0x3800 / min < 0x0800 / 不越上限"，配 amp=0x3FFF
        //   时波形被压扁（旧 bug 实测只能摆到 [468, 15915]）也照样过。正弦用夹取后的
        //   amp_norm2=0x1FFF ⇒ pk=(32767*8191)>>15=8190 ⇒ 峰 0x3FFE、谷 0x0001（取整 ±2）。
        check("正弦确有摆动（不再恒 0x2000）", edges > 100);
        check("正弦峰 ≈ off+pk (3FFE)", mx <= 14'h3FFF && mx >= 14'h3FFC);
        check("正弦谷 ≈ off-pk (0001)", mn >= 14'h0000 && mn <= 14'h0003);
        check("不越 14 位上限（max <= 14'h3FFF）", mx <= 14'h3FFF);

        $display("");
        $display("P6 信号发生器幅度为 0 ⇒ 应恒在 SIG_OFF");
        force_reg(6'h15, 16'h0000);
        #2000;
        mn = 14'h3FFF; mx = 0;
        for (i = 0; i < 100; i = i + 1) begin
            @(posedge sys_clk);
            if (da1_data > mx) mx = da1_data;
            if (da1_data < mn) mn = da1_data;
        end
        $display("    min=%h max=%h（应都等于 2000）", mn, mx);
        check("幅度 0 ⇒ 恒 2000", (mn === 14'h2000) && (mx === 14'h2000));

        // ---- 三角波 / 锯齿波：此前中间量被截成 14 位 ⇒ 阶梯丢失/回卷 ----
        $display("");
        $display("P7 三角波（mode=4）：应平滑往返于 bot/top");
        force_reg(6'h15, 16'h3FFF);   // AMP 满幅
        force_reg(6'h16, 16'h2000);   // OFF 中点
        force_reg(6'h17, 16'd64);     // 一个周期 64 步
        force_reg(6'h13, 16'd4);      // TRI
        #2000;
        mn = 14'h3FFF; mx = 0;
        for (i = 0; i < 3000; i = i + 1) begin
            @(posedge sys_clk);
            if (da1_data > mx) mx = da1_data;
            if (da1_data < mn) mn = da1_data;
        end
        $display("    三角 min=%h max=%h", mn, mx);
        check("三角达到顶部附近（max > 14'h3E00）", mx > 14'h3E00);
        check("三角达到底部附近（min < 14'h0200）", mn < 14'h0200);

        $display("");
        $display("P8 锯齿波（mode=3）：应从 OFF 单调爬到顶再回卷");
        force_reg(6'h13, 16'd3);
        #2000;
        mn = 14'h3FFF; mx = 0; edges = 0;
        for (i = 0; i < 3000; i = i + 1) begin
            @(posedge sys_clk);
            if (da1_data > mx) mx = da1_data;
            if (da1_data < mn) mn = da1_data;
            if (da1_data < {1'b0, 14'h3FFF} - 1) edges = edges + 1;
        end
        $display("    锯齿 min=%h max=%h", mn, mx);
        check("锯齿爬升到顶（max > 14'h3F00）", mx > 14'h3F00);
        check("锯齿回落到 OFF（min <= 2001）", mn <= 14'h2001);

        $display("");
        $display("========== 结果：%0d 通过 / %0d 失败 ==========", pass_cnt, fail_cnt);
        $finish;
    end

    initial begin
        #20_000_000;
        $display(" !! 超时");
        $finish;
    end

endmodule
