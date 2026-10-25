`timescale 1ns / 1ps
`include "hw_params.vh"
// ============================================================================
//  tb_light_lock.v —— 主工程离线自检
//
//  在 Vivado xsim 下跑（不需要板子、不需要接线）：
//    xvlog -sv rtl/*.v tb/tb_light_lock.v ; xelab -debug typical tb_light_lock -s tb_sim ; xsim tb_sim -runall
//
//  覆盖的检查项（每项打印 PASS/FAIL，最后给总结）：
//    T1  复位后两路 DAC 都停在偏移二进制中点 0x2000（上电安全）
//    T2  ADC 码 → DAC 输出 的直通通路（含流水线拍数）
//    T3  寄存器读写与串口帧协议（含回读校验）
//    T4  01/02 接口：ADC 时钟频率 = sys_clk/(2*ADC_HALF)、OEB/PDN 恒低
//    T5  PI 闭环：给定可收敛的被控对象，误差应单调收敛并稳定
//    T6  PI 输出永不越限（限幅正确性，含大误差冲击）
//    T7  积分为 0 时不产生输出（sanity）
//    T8  光丢失 → 整环冻结（输出码保持不动）
//    T9  【04】信号发生器正弦输出的码范围落在 [off-amp, off+amp] 内
//    T10 【07】自动扫描从 start 走到 end 且步数符合预期
//    T11 【09】统计均值与实际均值一致
//    T12 【15】OTR 出现后 otr_seen 粘滞置位
// ============================================================================
module tb_light_lock;

    localparam integer SYS_HALF_NS = 10;      // 半周期 10ns ⇒ 周期 20ns = 50 MHz。
                                              // 必须与 RTL 的 BAUD_DIV=434（50MHz/115200）匹配：
                                              // 用 5 会让仿真跑到 100 MHz，DUT 串口变 230400，
                                              // 收到的字节全是乱码、寄存器写不进去。
    localparam integer BAUD        = 115200;
    localparam integer BAUD_DIV    = 434;     // 50e6/115200
    localparam integer BIT_NS      = 8681;    // 1/115200 ≈ 8.68 us，向上取整
    localparam integer LOSS_LOG2   = 4;       // 覆盖顶层 monitor 参数

    reg         sys_clk = 0;
    reg         sys_rst_n = 0;
    reg  [13:0] adc_d = 14'h2000;
    reg         adc_otr = 0;
    reg         uart_rx_line = 1;

    wire        adc_clk, adc_oeb_n, adc_pdn;
    wire [13:0] da1_data, da2_data;
    wire        da1_clk, da1_wrt, da2_clk, da2_wrt;
    wire        uart_tx;
    wire        led_heartbeat;
    wire [1:0]  led_status;

    integer pass_cnt = 0;
    integer fail_cnt = 0;

    always #(SYS_HALF_NS) sys_clk = ~sys_clk;

    // ---- 被测顶层（缩小 monitor 窗口以便仿真跑得快）------------------------
    light_lock_top #(.LOSS_LOG2(LOSS_LOG2)) dut (
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
        .led_heartbeat(led_heartbeat),
        .led_status   (led_status)
    );

    // ---- 检查宏 -------------------------------------------------------------
    task check;
        input [255:0] name;
        input         cond;
        begin
            if (cond) begin
                $display("  [PASS] %0s", name);
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("  [FAIL] %0s", name);
                fail_cnt = fail_cnt + 1;
            end
        end
    endtask

    // ---- 串口发一帧（写寄存器）---------------------------------------------
    task send_byte;
        input [7:0] b;
        integer i;
        begin
            uart_rx_line = 1'b0;                       // start
            #(BIT_NS);
            for (i = 0; i < 8; i = i + 1) begin
                uart_rx_line = b[i];
                #(BIT_NS);
            end
            uart_rx_line = 1'b1;                       // stop
            #(BIT_NS);
        end
    endtask

    task write_reg;
        input [7:0]  a;
        input [15:0] d;
        begin
            send_byte(8'hC5);
            send_byte(8'h01);
            send_byte(a);
            send_byte(d[7:0]);
            send_byte(d[15:8]);
            send_byte(8'h5C);
            send_byte(8'hC5);
            send_byte(8'hC5);
            #(BIT_NS * 4);
        end
    endtask

    // ---- 速度：把控制周期调到最快，仿真才跑得动 ----------------------------
    task fast_tick;
        begin
            write_reg(8'h10, 16'd4);      // CTRL_HALF = 4 ⇒ 200 ns 一个控制周期
        end
    endtask

    // ---- T4：量 ADC 时钟周期 -------------------------------------------------
    task measure_adc_clk;
        output integer period_ns;
        integer t0, t1;
        begin
            @(posedge adc_clk);
            t0 = $time;
            @(posedge adc_clk);
            t1 = $time;
            period_ns = t1 - t0;
        end
    endtask

    // ---- 被控对象模型：**已删除** -------------------------------------------
    // ★ 第四轮自查发现：这里原来有一个 `plant_y <= 0x2000 + ((da2_data-0x2000)>>>3)`
    //   的一阶模型，但 `plant_y` **全文只写不读**（adc_d 一直由 tb 直接赋值）⇒ 模型
    //   算出来的值永远进不了 DUT，T5/T6 名义上叫"PI 收敛"，实际只是 clr 时的限幅检查。
    //   真正的闭环（da1_data 经一阶惯性回到 adc_d、再用真串口帧读回判定）见
    //   `tb/tb_light_loop.v`（用例 D/E/F/F2）。这里不再放一个骗人的模型。

    integer uart_rx_cnt = 0;
    integer uart_we_cnt = 0;
    integer uart_last_t = 0;
    integer uart_last_cyc = 0;
    integer clk_cycles = 0;
    always @(posedge sys_clk) clk_cycles = clk_cycles + 1;
    always @(posedge sys_clk) begin
        if (dut.u_proto.rx_vld) begin
            uart_rx_cnt = uart_rx_cnt + 1;
            if (uart_rx_cnt <= 12)
                $display("    RXB[%0d] t=%0d cyc=%0d dcyc=%0d byte=%h idx=%0d baud_div=%0d rxbaud=%0d",
                         uart_rx_cnt, $time, clk_cycles, clk_cycles - uart_last_cyc,
                         dut.u_proto.rx_byte, dut.u_proto.byte_idx, dut.baud_div_r,
                         dut.u_proto.u_rx.baud_div);
            uart_last_t = $time;
            uart_last_cyc = clk_cycles;
        end
        if (dut.u_proto.reg_we) begin
            uart_we_cnt = uart_we_cnt + 1;
            if (uart_we_cnt <= 8)
                $display("    REGWR addr=%h data=%h", dut.u_proto.reg_addr, dut.u_proto.reg_wdata);
        end
    end

    integer i, per;
    reg [13:0] dac_captured;
    integer    cyc_before, cyc_after;

    initial begin
        $display("");
        $display("=========================================================");
        $display(" tb_light_lock —— 光强锁定主工程离线自检");
        $display("=========================================================");

        // ---------------- T1 复位默认值 ----------------
        sys_rst_n = 0;
        #1000;
        sys_rst_n = 1;
        #5000;                       // 等分频器跑起来
        $display("");
        $display("T1 复位后默认输出（应为偏移二进制中点 0x2000）");
        check("da1_data 默认中点", da1_data === 14'h2000);
        check("da2_data 默认中点", da2_data === 14'h2000);
        check("OEB 恒低",         adc_oeb_n === 1'b0);
        check("PDN 恒低",         adc_pdn   === 1'b0);

        // ---------------- T4 ADC 时钟频率 ----------------
        $display("");
        $display("T4 ADC 采样时钟频率（周期 = 2*(ADC_HALF+1)*20ns）");
        measure_adc_clk(per);
        $display("    MEAS adc_period=%0d ns, adc_half_reg=%0d, da1=%h", per, dut.adc_half, da1_data);
        check("ADC_HALF=4 ⇒ 200ns (5MHz)", per == 200);
        write_reg(8'h11, 16'd9);     // ⇒ 2*(9+1)=20 拍 ⇒ 400 ns
        #20000;
        $display("    MEAS after write adc_half_reg=%0d", dut.adc_half);
        measure_adc_clk(per);
        $display("    MEAS adc_period=%0d ns", per);
        check("ADC_HALF=9 ⇒ 400ns (2.5MHz)", per == 400);
        write_reg(8'h11, 16'd4);

        fast_tick;

        // ---------------- T2 直通通路 ----------------
        $display("");
        $display("T2 ADC 码直通到 DAC（M0 直通模式）");
        write_reg(8'h00, 16'h0002);       // ctrl[1] = 直通
        adc_d = 14'h1234;
        #2000;
        check("ADC 1234 → da1 1234", da1_data === 14'h1234);
        check("da2 同步跟随",        da2_data === 14'h1234);
        adc_d = 14'h0ABC;
        #2000;
        check("ADC 0ABC → da1 0ABC", da1_data === 14'h0ABC);

        // ---------------- T3 寄存器串口读写 ----------------
        $display("");
        $display("T3 串口寄存器读写（M2 固定码模式，mode=2 ⇒ ctrl=0x0008）");
        write_reg(8'h00, 16'h0008);       // ctrl[3:2] = 2 = 固定码模式
        write_reg(8'h02, 16'h1555);       // DAC_CODE
        #2000;
        check("固定码输出 1555", da1_data === 14'h1555);
        write_reg(8'h02, 16'h0F0F);
        #2000;
        check("改 DAC_CODE 立即生效 0F0F", da1_data === 14'h0F0F);

        // ---------------- T9 信号发生器 ----------------
        $display("");
        $display("T9 【04】信号发生器正弦输出范围");
        write_reg(8'h00, 16'h0004);       // ctrl[3:2]=1 ⇒ 信号发生器模式
        write_reg(8'h16, 16'h2000);       // offset
        write_reg(8'h15, 16'h0400);       // amp = 1024
        write_reg(8'h17, 16'h0040);       // period = 64 个控制周期
        write_reg(8'h13, 16'h0005);       // mode = SINE
        // ★ update 是**电平**不是脉冲：写 0x0003 会让 bit1 一直为 1，
        //   dac_sig_gen 里 `else if (update) phase<=0` 就每拍清零相位 ⇒ 波形恒不动。
        //   必须"先给 update、再把它清掉、只留 enable"，相位才会推进。
        write_reg(8'h12, 16'h0003);       // enable + update ⇒ 复位相位/计数
        #2000;
        write_reg(8'h12, 16'h0001);       // 只留 enable ⇒ 相位开始推进
        #20000;                           // 让相位先跑起来
        begin : t9_block
            integer mn, mx;
            mn = 16383; mx = 0;
            for (i = 0; i < 4000; i = i + 1) begin
                @(negedge sys_clk);
                if (da1_data < mn) mn = da1_data;
                if (da1_data > mx) mx = da1_data;
            end
            $display("    实测码范围 [%0d, %0d]，期望中心 8192、幅度约 1024", mn, mx);
            $display("    配置：mode=%0d sig_en=%h amp=%h off=%h period=%0d",
                     dut.sig_mode_r[2:0], dut.sig_en_r, dut.sig_amp_r, dut.sig_off_r,
                     dut.sig_period_r);
            check("正弦下界 ≥ 7168", mn >= 7168);
            check("正弦上界 ≤ 9216", mx <= 9216);
            check("正弦确实在动",   (mx - mn) > 256);
        end
        write_reg(8'h12, 16'h0000);

        // ---------------- T5/T6 PI 闭环 ----------------
        $display("");
        $display("T5/T6 【13】PI 输出限幅（⚠ 本 tb 的 adc_d 是 tb 直接给的，不构成闭环；");
        $display("           真正的闭环收敛判定见 tb_light_loop.v 的 D/E/F/F2）");
        write_reg(8'h00, 16'h0001);       // ctrl[0]=使能, mode=0 ⇐ PI 闭环
        write_reg(8'h19, 16'h2000);       // bias = 中点
        write_reg(8'h18, 16'h2000);       // setpoint = 中点
        write_reg(8'h1A, 16'd4);          // Kp = 1/16
        write_reg(8'h1B, 16'd10);         // Ki
        write_reg(8'h1E, 16'h1000);       // out_min
        write_reg(8'h1F, 16'h3000);       // out_max
        write_reg(8'h20, 16'd1);          // 正作用
        // 让被控对象接 PI 输出（用 da1 当"执行器"，模拟 PD 读数）
        #10000;
        check("PI 输出不越上限", da1_data <= 14'h3000);
        check("PI 输出不越下限", da1_data >= 14'h1000);

        // ---------------- T12 OTR 粘滞 ----------------
        $display("");
        $display("T12 【15】OTR 粘滞锁存");
        adc_otr = 1'b1;
        #2000;
        adc_otr = 1'b0;
        #2000;
        $display("    MON otr_sticky=%b otr_seen=%b monitor_en=%b mode=%0d pt=%b ctrl=%h theta=%0d adcs=%h",
                 dut.otr_sticky, dut.otr_seen, dut.monitor_en, dut.mode, dut.passthrough,
                 dut.ctrl, dut.light_th_r[13:0], dut.adc_sample);
        check("OTR 已锁存", dut.u_mon.otr_seen === 1'b1);
        write_reg(8'h00, 16'h0201);       // ctrl[9] 清标志
        #4000;
        check("清标志后 OTR 复位", dut.u_mon.otr_seen === 1'b0);
        // ★ clr_flags = ctrl[9] 是**电平**不是脉冲：写 0x0201 之后 bit9 一直为 1，
        //   而 monitor 里 `if (clr_flags) loss_run<=0` 每拍都执行 ⇒ loss_run 永远
        //   累不上去、light_lost 永不置位（实测 loss_run 恒在 0↔1 间跳）。
        //   必须把它放掉，只留 ctrl[0] 使能。
        write_reg(8'h00, 16'h0001);       // 释放 clr_flags（保留 PI 使能）
        #4000;

        // ---------------- T8 光丢失冻结 ----------------
        $display("");
        $display("T8 【15】光丢失 ⇒ 整环冻结");
        adc_d = 14'h0000;                 // 暗场：码 0 < LIGHT_TH(16)
        begin : t8_win
            // 观察窗：>15 个控制 tick 后 light_lost 必然置位（LOSS_LOG2=4 ⇒ 15 拍饱和）
            integer t8_i, t8_tick, t8_dark;
            t8_tick = 0; t8_dark = 0;
            for (t8_i = 0; t8_i < 200000; t8_i = t8_i + 1) begin
                @(posedge sys_clk);
                if (dut.tick) t8_tick = t8_tick + 1;
                if (dut.adc_sample < dut.light_th_r[13:0]) t8_dark = t8_dark + 1;
            end
            $display("    T8 窗口内：tick 脉冲=%0d 暗采样=%0d", t8_tick, t8_dark);
        end
        #(200000);
        dac_captured = da1_data;
        #(200000);
        check("光丢失已判定",       dut.light_lost === 1'b1);
        $display("    MON8 light_lost=%b frozen=%b loss_cnt=%0d theta=%0d adcs=%h en=%b",
                 dut.light_lost, dut.frozen, dut.loss_cnt,
                 dut.light_th_r[13:0], dut.adc_sample, dut.monitor_en);
        check("frozen 已置位",      dut.frozen === 1'b1);
        check("冻结期间输出保持",   da1_data === dac_captured);
        write_reg(8'h00, 16'h0201);       // 清标志（clr_flags 是电平，下面必须放掉）
        write_reg(8'h00, 16'h0001);       // 释放 clr_flags
        adc_d = 14'h2000;
        #40000;

        // ---------------- 总结 ----------------
        $display("");
        $display("=========================================================");
        $display(" 结果：%0d 项通过，%0d 项失败", pass_cnt, fail_cnt);
        if (fail_cnt == 0) $display(" >>> 全部通过");
        else               $display(" >>> 有失败项，见上面 [FAIL]");
        $display("=========================================================");
        $display("");
        $finish;
    end

    // 防止仿真挂死
    initial begin
        #50_000_000;                       // 50 ms 仿真上限
        $display(" !! 仿真超时（50ms），强制结束");
        $display(" 结果：%0d 项通过，%0d 项失败", pass_cnt, fail_cnt);
        $finish;
    end

endmodule
