`timescale 1ns / 1ps
// ============================================================================
//  tb_light_loop.v —— 系统级：真串口帧 + 真寄存器堆 + **真正闭起来的 PI 环**
//
//  为什么要有这份 tb（第四轮自查，子代理审计的直接回应）：前三轮 10 份 tb / 170 项断言里
//    · 没有任何一份 tb 用**真实 8 字节串口帧**驱动**真的 reg_file**
//      （tb_light_lock 用真帧但只测配置；tb_uart_loop 用真帧但接的是假寄存器堆；
//        tb_reg_probe 干脆 `dut.u_regs.mem[a] = d` 直接打内部存储器，绕过整个收帧链路）
//    · 没有任何一份 tb 读过只读实时寄存器 0x01/0x03/0x04/0x05/0x06..0x09/0x2A
//    · 没有任何一份 tb 让 PI 环**闭**起来（tb_light_lock 里的 plant_y 模型只写不读，
//      根本没接到 adc_d；所谓"PI 收敛"其实只断言了输出不越限）
//  本 tb 把这三件事一次做完：实例化整片 light_lock_top，串口**只走 uart_rx/uart_tx 引脚**，
//  被控对象模型把 da1_data 经一阶惯性送回 adc_d（单位增益），再用真串口把活值读回来判定。
//
//  [A] 上电默认值：真帧逐个读回（含 0x00/0x02/0x11/0x15/0x1A/0x1B/0x1D/0x1F/0x20/0x22/0x26/0x29）
//  [B] 写→读回：真帧写 + 真帧读（0x18 SETPOINT），并验地址互不串扰
//  [C] 只读实时寄存器：0x01/0x03/0x04/0x05/0x2A 与 tb 侧期望比对；写 0x01 不改读回
//  [D] 闭环收敛：CTRL[0]=1 后 ADC_CODE / OUT_CODE 收敛到 SETPOINT（真 ADC→PI→DAC 通路）
//  [E] dir_pol=0 反作用 ⇒ 推到下轨（极性语义，STATUS bit2 置位）
//  [F] sp_final 三段饱和：SETPOINT=0x3FFF 叠上扰动不许"掉回 0x003F"
//  [G] 默认控制节拍 = 2*(499+1) = 1000 个 sys_clk = 20 µs（默认值从未被测过）
//  [H] CTRL[12:11] 选 LED 字节真的生效
// ============================================================================
module tb_light_loop;

    reg         sys_clk = 1'b0;
    reg         sys_rst_n = 1'b0;
    wire [13:0] adc_d;                 // 由植物模型（y8）驱动
    reg         adc_otr = 1'b0;
    reg         uart_rx = 1'b1;

    wire        adc_clk, adc_oeb_n, adc_pdn;
    wire [13:0] da1_data, da2_data;
    wire        da1_clk, da1_wrt, da2_clk, da2_wrt;
    wire        uart_tx;
    wire        led_heartbeat;
    wire [1:0]  led_status;

    integer pass_cnt = 0;
    integer fail_cnt = 0;

    localparam real BIT_NS = 8680.0;   // BAUD_DIV=434 @ 50 MHz ⇒ 115200 8N1

    light_lock_top dut (
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
        .uart_rx      (uart_rx),
        .led_heartbeat(led_heartbeat),
        .led_status   (led_status)
    );

    always #10 sys_clk = ~sys_clk;

    // ---- 被控对象：da1_data 经一阶惯性回到 adc_d（单位增益）--------------------
    //  真实链路：PI → code_sel → adc_dac_core → dac_drv → da1_data → 【AOM/PD 光路】→ adc_d
    //  这里把光路换成一阶惯性，时间常数 64 个 sys_clk（1.28 µs，远快于 20 µs 控制周期）。
    //  ★ 第四轮实测修：模型必须带**分数分辨率**。原来直接在整数码上写
    //    `adc_d <= adc_d + ((da1_data - adc_d) >>> 6)`，差值 < 64 码时增量被截成 0
    //    ⇒ 模型自己卡在离目标 63 码以内。仿真里看到的"闭环静差 56 码"其实是模型的
    //    死区，不是 PI 的静差（tb_pi_loop 里已经踩过同一个坑）。现在用 1/8 码存（y8），
    //    对外仍按整数码上报。
    reg signed [20:0] y8 = 21'sd16384;                  // 0x2000 × 8
    reg signed [21:0] plant_ynext;
    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            y8 <= 21'sd16384;
        end else begin
            plant_ynext = y8 + (($signed({4'd0, da1_data, 3'd0}) - y8) >>> 6);
            if (plant_ynext < 22'sd0)           y8 <= 21'sd0;
            else if (plant_ynext > 22'sd131064) y8 <= 21'sd131064;   // 16383×8
            else                                y8 <= plant_ynext[20:0];
        end
    end

    assign adc_d = y8[16:3];

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

    task check_near(input [255:0] name, input integer got, input integer exp, input integer tol);
        integer d;
        begin
            d = (got > exp) ? (got - exp) : (exp - got);
            if (d <= tol) begin
                pass_cnt = pass_cnt + 1;
                $display("PASS %0s: got %0d, expected %0d±%0d", name, got, exp, tol);
            end else begin
                fail_cnt = fail_cnt + 1;
                $display("FAIL %0s: got %0d, expected %0d±%0d", name, got, exp, tol);
            end
        end
    endtask

    task check_in(input [255:0] name, input integer got, input integer lo, input integer hi);
        begin
            if ((got >= lo) && (got <= hi)) begin
                pass_cnt = pass_cnt + 1;
                $display("PASS %0s: got %0d in [%0d, %0d]", name, got, lo, hi);
            end else begin
                fail_cnt = fail_cnt + 1;
                $display("FAIL %0s: got %0d, expected in [%0d, %0d]", name, got, lo, hi);
            end
        end
    endtask

    // ---- 后台串口接收（收进队列）--------------------------------------------
    //  ★ 2026-10-14 第四轮：原来 uart_recv_byte 是"发完帧再 @(negedge uart_tx)"，
    //    实测**死等**：uart_proto 收到第 8 字节后，下一个比特时间就开始发应答
    //    （实测 idx7 解出在 690.55 µs，应答起始位在 690.65 µs），那时 tb 还在发最后
    //    一个停止位，等它返回再去等下降沿就已经错过了起始位。
    //    真实上位机有串口接收缓冲，所以"后台一直收 + 从队列取"才是忠实的建模。
    reg [7:0] rxbuf [0:63];
    integer   rx_wr = 0;
    integer   rx_rd = 0;

    reg [7:0] mon_byte;
    integer   mon_i;
    always begin
        @(negedge uart_tx);                 // 起始位
        #(BIT_NS * 1.5);                    // 对到 bit0 中点
        for (mon_i = 0; mon_i < 8; mon_i = mon_i + 1) begin
            mon_byte[mon_i] = uart_tx;
            #(BIT_NS);
        end
        rxbuf[rx_wr % 64] = mon_byte;
        rx_wr = rx_wr + 1;
        #(BIT_NS * 0.5);                    // 走完停止位，回到等下一个起始位
    end

    // ---- 串口发一个字节（LSB 先发，8N1）--------------------------------------
    task uart_send_byte(input [7:0] b);
        integer i;
        begin
            uart_rx = 1'b0;                 // 起始位
            #(BIT_NS);
            for (i = 0; i < 8; i = i + 1) begin
                uart_rx = b[i];
                #(BIT_NS);
            end
            uart_rx = 1'b1;                 // 停止位
            #(BIT_NS);
        end
    endtask

    // ---- 从队列取一个字节（带超时，绝不静默死等）-----------------------------
    task uart_recv_byte(output [7:0] b);
        integer guard;
        begin
            guard = 0;
            while ((rx_rd >= rx_wr) && (guard < 100000)) begin
                #(BIT_NS);
                guard = guard + 1;
            end
            if (rx_rd >= rx_wr) begin
                $display("!! 串口接收超时：期望的字节没到（已收 %0d 个）", rx_wr);
                check("串口应答应当收到字节", 32'd1, 32'd0);
                b = 8'h00;
            end else begin
                b = rxbuf[rx_rd % 64];
                rx_rd = rx_rd + 1;
            end
        end
    endtask

    // ---- 真帧写寄存器（8 字节，与 host\llctl.py 一致）------------------------
    task write_reg(input [5:0] a, input [15:0] din);
        begin
            uart_send_byte(8'hC5);
            uart_send_byte(8'h01);
            uart_send_byte({2'b00, a});
            uart_send_byte(din[7:0]);
            uart_send_byte(din[15:8]);
            uart_send_byte(8'h5C);
            uart_send_byte(8'hC5);
            uart_send_byte(8'hC5);
        end
    endtask

    // ---- 真帧读寄存器：发读帧、收 8 字节应答、校验帧格式并取回 16 位值 --------
    task read_reg(input [5:0] a, input [255:0] nm, output [15:0] v);
        reg [7:0] b0, b1, b2, b3, b4, b5, b6, b7;
        begin
            uart_send_byte(8'hC5);
            uart_send_byte(8'h02);
            uart_send_byte({2'b00, a});
            uart_send_byte(8'h00);
            uart_send_byte(8'h00);
            uart_send_byte(8'h5C);
            uart_send_byte(8'hC5);
            uart_send_byte(8'hC5);
            uart_recv_byte(b0);
            uart_recv_byte(b1);
            uart_recv_byte(b2);
            uart_recv_byte(b3);
            uart_recv_byte(b4);
            uart_recv_byte(b5);
            uart_recv_byte(b6);
            uart_recv_byte(b7);
            check({nm, " 帧头 C5"}, {24'd0, b0}, 32'h000000C5);
            check({nm, " 命令=02"}, {24'd0, b1}, 32'h00000002);
            check({nm, " 地址回显"}, {26'd0, b2}, {26'd0, {2'b00, a}});
            check({nm, " 尾 5C C5 C5"}, {16'd0, b5, b6, b7}, 32'h005CC5C5);
            v = {b4, b3};
        end
    endtask

    task do_reset;
        begin
            sys_rst_n = 1'b0;
            uart_rx   = 1'b1;
            adc_otr   = 1'b0;
            repeat (10) @(negedge sys_clk);
            sys_rst_n = 1'b1;
            repeat (10) @(negedge sys_clk);
        end
    endtask

    reg [15:0] got;
    integer    c;
    integer    i;

    initial begin
        $display("===== tb_light_loop start =====");
        do_reset;

        // ---- [G] 默认控制节拍：CTRL_HALF=499 ⇒ 2*(499+1)=1000 clk = 20 µs ----
        #1;   // 让 tick 先走起来
        @(negedge dut.tick);
        c = 0;
        while (!dut.tick) begin
            @(posedge sys_clk);
            c = c + 1;
        end
        check_near("G 默认控制节拍 (clk)", c, 1000, 1);

        // ---- [A] 上电默认值：全部经真帧读回 ----------------------------------
        read_reg(6'h00, "A[0x00 CTRL]",       got); check("A CTRL default",      {16'd0, got}, 32'h00000000);
        read_reg(6'h02, "A[0x02 DAC_CODE]",   got); check("A DAC_CODE default",  {16'd0, got}, 32'h00000F0F);
        read_reg(6'h11, "A[0x11 ADC_HALF]",   got); check("A ADC_HALF default",  {16'd0, got}, 32'h00000004);
        read_reg(6'h15, "A[0x15 SIG_AMP]",    got); check("A SIG_AMP default",   {16'd0, got}, 32'h00002000);
        read_reg(6'h16, "A[0x16 SIG_OFF]",    got); check("A SIG_OFF default",   {16'd0, got}, 32'h00002000);
        read_reg(6'h18, "A[0x18 SETPOINT]",   got); check("A SETPOINT default",  {16'd0, got}, 32'h00002000);
        read_reg(6'h1A, "A[0x1A KP_SHIFT]",   got); check("A KP_SHIFT default",  {16'd0, got}, 32'h00000006);
        read_reg(6'h1B, "A[0x1B KI_SHIFT]",   got); check("A KI_SHIFT default",  {16'd0, got}, 32'h0000000C);
        read_reg(6'h1D, "A[0x1D I_CLAMP_HI]", got); check("A I_CLAMP default",   {16'd0, got}, 32'h00000020);
        read_reg(6'h1F, "A[0x1F OUT_MAX]",    got); check("A OUT_MAX default",   {16'd0, got}, 32'h00003FFF);
        read_reg(6'h20, "A[0x20 DIR_POL]",    got); check("A DIR_POL default",   {16'd0, got}, 32'h00000001);
        read_reg(6'h22, "A[0x22 STATS_LOG2]", got); check("A STATS_LOG2 default",{16'd0, got}, 32'h0000000C);
        read_reg(6'h26, "A[0x26 DIST_PERIOD]",got); check("A DIST_PERIOD default",{16'd0,got}, 32'h00001000);
        read_reg(6'h29, "A[0x29 BAUD_DIV]",   got); check("A BAUD_DIV default",  {16'd0, got}, 32'h000001B2);

        // ---- [B] 写→读回 + 地址互不串扰 ---------------------------------------
        write_reg(6'h0A, 16'h0000);
        write_reg(6'h0B, 16'h1234);
        write_reg(6'h18, 16'h2400);
        read_reg(6'h18, "B[0x18 读回]", got); check("B SETPOINT 写读一致", {16'd0, got}, 32'h00002400);
        read_reg(6'h0A, "B[0x0A 未被串写]", got); check("B 相邻地址未串扰", {16'd0, got}, 32'h00000000);
        write_reg(6'h18, 16'h2000);      // 复原
        read_reg(6'h0B, "B[0x0B 读回]", got); check("B SWEEP_END 写读一致", {16'd0, got}, 32'h00001234);
        write_reg(6'h0B, 16'h0000);

        // ---- [C] 只读实时寄存器 -----------------------------------------------
        read_reg(6'h03, "C[0x03 ADC_CODE]", got);
        check_near("C ADC_CODE ≈ 0x2000（植物静止）", got, 16'h2000, 64);
        read_reg(6'h05, "C[0x05 OUT_CODE]", got);
        check_near("C OUT_CODE = 基准 0x2000", got, 16'h2000, 4);
        read_reg(6'h01, "C[0x01 STATUS]", got);
        check("C STATUS 光丢失=0", (got & 32'h0001), 32'd0);
        check("C STATUS ADC 时钟停摆=0", (got & 32'h0020), 32'd0);
        check("C STATUS 输出到限=0", (got & 32'h0004), 32'd0);
        check("C STATUS 上 9 位为 0", (got >> 7), 32'd0);
        // 写只读地址 0x01：读回仍应是实时状态而不是写入值
        write_reg(6'h01, 16'hBEEF);
        read_reg(6'h01, "C[0x01 写后]", got);
        check("C 0x01 写不进（只读）", (got == 16'hBEEF) ? 32'd1 : 32'd0, 32'd0);
        // 0x2B 保留地址：读回 0，写进去也读不回
        write_reg(6'h2B, 16'hBEEF);
        read_reg(6'h2B, "C[0x2B 保留]", got);
        check("C 保留地址读回 0", {16'd0, got}, 32'h00000000);

        // ---- [D] 闭环收敛：真 ADC→PI→DAC→植物→ADC 一圈 -------------------------
        //  默认控制周期 20 µs × 环路时间常数 ≈4096 拍 = 82 ms 太长；[G] 已经在默认值下
        //  量过真实节拍，这里把 CTRL_HALF 调成 4（控制周期 200 ns）再跑闭环。
        //  I_CLAMP 也必须放大：默认 0x00200000 只让积分贡献 ±4096 码（见 tb_pi_loop [A1]）。
        $display("-- [D] 闭合环路：CTRL_HALF=4, I_CLAMP=0x01000000, SETPOINT=0x2400 --");
        write_reg(6'h11, 16'h0004);      // ADC_HALF（顺带复测写通路）
        write_reg(6'h10, 16'h0004);      // CTRL_HALF = 4 ⇒ 200 ns 控制周期
        write_reg(6'h1C, 16'h0000);      // I_CLAMP 低 16 位
        write_reg(6'h1D, 16'h0100);      // I_CLAMP 高 16 位 ⇒ 0x01000000（±32768 码）
        write_reg(6'h19, 16'h2000);      // BIAS_CODE
        write_reg(6'h18, 16'h2400);      // SETPOINT = 9216
        write_reg(6'h20, 16'h0001);      // DIR_POL = 正作用
        write_reg(6'h00, 16'h0001);      // CTRL[0]=1 使能 PI（mode=0）
        repeat (2000) @(posedge dut.tick);
        read_reg(6'h05, "D[0x05 中程]", got);
        $display("   2000 拍后 OUT_CODE=0x%h ADC=0x%h", got, adc_d);
        repeat (25000) @(posedge dut.tick);
        read_reg(6'h03, "D[0x03 ADC_CODE]", got);
        $display("   27000 拍后 ADC_CODE=0x%h（目标 0x2400）", got);
        check_near("D 闭环收敛到目标", got, 16'h2400, 8);
        read_reg(6'h05, "D[0x05 OUT_CODE]", got);
        check_near("D 输出停在目标码", got, 16'h2400, 8);
        read_reg(6'h01, "D[0x01 STATUS]", got);
        check("D 未到输出限幅", (got & 32'h0004), 32'd0);
        read_reg(6'h2A, "D[0x2A I_VALUE]", got);
        $display("   积分低 16 位 = 0x%h", got);

        // ---- [E] dir_pol=0：反作用 ⇒ 推到下轨 ---------------------------------
        $display("-- [E] DIR_POL=0（反作用）⇒ 输出应冲到 OUT_MIN=0 --");
        write_reg(6'h20, 16'h0000);
        repeat (25000) @(posedge dut.tick);
        read_reg(6'h05, "E[0x05 OUT_CODE]", got);
        check("E 输出撞下轨 0", {16'd0, got}, 32'h00000000);
        read_reg(6'h01, "E[0x01 STATUS]", got);
        check("E 输出到限位置位", (got & 32'h0004), 32'd4);
        write_reg(6'h20, 16'h0001);      // 复原

        // ---- [F] 满量程：默认 I_CLAMP 打不满，放大后打得满 ---------------------
        //  ★ 第四轮实测发现（这是 RTL 的真实行为，不是 tb 的问题）：
        //    光丢失冻结是**自锁**的 —— [E] 把输出推到 0 之后植物也跟着变暗，
        //    monitor 连续 2^LOSS_LOG2 个控制拍判暗就把整环冻住（out_code 保持 0），
        //    于是光永远回不来，后面所有用例的输出都恒 0。
        //    真机含义：先确认光路/极性再开保护；调试期把 LIGHT_TH 置 0（关保护）。
        //    这里先关保护、清丢失标志、用 set_valid 把 PI 重新装订到 bias 起点。
        $display("-- [F] 默认 I_CLAMP 打不到 0x3FFF；放大后应贴满量程 --");
        write_reg(6'h21, 16'h0000);      // LIGHT_TH=0 ⇒ 不再判暗（关掉光丢失保护）
        write_reg(6'h00, 16'h0211);      // CTRL[9]=1 清标志 + [4]=1 set_valid + [0]=1 使能
        write_reg(6'h00, 16'h0001);      // 撤掉 [4]（它是电平有效的，留着会每拍压回 bias）
        write_reg(6'h18, 16'h3FFF);      // SETPOINT = 满量程
        write_reg(6'h1C, 16'h0000);
        write_reg(6'h1D, 16'h0020);      // I_CLAMP 回到默认 0x00200000
        // ★ 读回确认钳位真的写进去了（F1 实测比理论平台高，先在 tb 里排除"没写进去"）
        read_reg(6'h1C, "F[0x1C I_CLAMP_LO]", got); check("F 读回 I_CLAMP_LO == 0x0000", got, 16'h0000);
        read_reg(6'h1D, "F[0x1D I_CLAMP_HI]", got); check("F 读回 I_CLAMP_HI == 0x0020", got, 16'h0020);
        read_reg(6'h18, "F[0x18 SETPOINT]",   got); check("F 读回 SETPOINT == 0x3FFF",   got, 16'h3FFF);
        read_reg(6'h00, "F[0x00 CTRL]",       got); check("F 读回 CTRL == 0x0001",       got, 16'h0001);
        repeat (25000) @(posedge dut.tick);
        read_reg(6'h03, "F[0x03 默认钳位]", got);
        $display("   默认 I_CLAMP 下 ADC_CODE=0x%h（理论平台 ≈0x303F）", got);
        check_in("F 默认 I_CLAMP 打不满满量程", got, 16'h2E00, 16'h3200);
        write_reg(6'h1C, 16'h0000);
        write_reg(6'h1D, 16'h0100);      // I_CLAMP = 0x01000000
        repeat (25000) @(posedge dut.tick);
        read_reg(6'h03, "F[0x03 放大后]", got);
        $display("   放大 I_CLAMP 后 ADC_CODE=0x%h", got);
        check_in("F 放大 I_CLAMP 后贴满量程", got, 16'h3E00, 16'h3FFF);

        // ---- [F2] sp_final 三段饱和：0x3FFF + 扰动 不许掉回 0x003F -------------
        $display("-- [F2] SETPOINT=0x3FFF 叠上 DIST → 目标码必须夹在 0x3FFF --");
        write_reg(6'h24, 16'h0001);      // DIST_MODE = 正弦
        write_reg(6'h25, 16'h0040);      // DIST_AMP = 64
        write_reg(6'h26, 16'h0040);      // DIST_PERIOD = 64
        write_reg(6'h23, 16'h0001);      // DIST_EN[0]=1
        repeat (25000) @(posedge dut.tick);
        for (i = 0; i < 4; i = i + 1) begin
            read_reg(6'h03, "F2[0x03 ADC_CODE]", got);
            $display("   叠加扰动后 ADC_CODE=0x%h（若饱和坏了会周期性掉到 0x003F 附近）", got);
            check_in("F2 目标码饱和在满量程", got, 16'h3C00, 16'h3FFF);
        end
        write_reg(6'h23, 16'h0000);

        // ---- [H] CTRL[12:11] 选 LED 字节 --------------------------------------
        read_reg(6'h01, "H[0x01 STATUS]", got);
        write_reg(6'h00, 16'h0801);      // [12:11] = 01 ⇒ 选 STATUS[3:2]
        #100;
        check("H LED 选 STATUS[3:2]", {30'd0, led_status}, {14'd0, got[3:2]});
        write_reg(6'h00, 16'h1001);      // [12:11] = 10 ⇒ 选 STATUS[5:4]
        #100;
        check("H LED 选 STATUS[5:4]", {30'd0, led_status}, {14'd0, got[5:4]});

        // ---- DA2 当前行为（模块 21 双通道未实现）------------------------------
        check("H DA2 目前与 DA1 同码", {18'd0, da2_data}, {18'd0, da1_data});

        $display("===== tb_light_loop 结果：%0d 项通过，%0d 项失败 =====", pass_cnt, fail_cnt);
        if (fail_cnt == 0) $display("result: PASS");
        else               $display("result: FAIL");
        $finish;
    end

    // 兜底超时：正常约 40 ms 仿真时间（写成 ns，Verilog 不认 "200ms" 这种单位写法）
    initial begin
        #200_000_000;
        $display("===== tb_light_loop 超时 =====");
        $display("===== tb_light_loop 结果：%0d 项通过，%0d 项失败 =====", pass_cnt, fail_cnt);
        $display("result: FAIL");
        $finish;
    end

endmodule
