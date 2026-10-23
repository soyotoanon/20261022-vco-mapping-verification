`timescale 1ns / 1ps
// ============================================================================
//  tb_pi_loop.v —— 【13】PI 闭环：把环路真正闭起来跑一遍
//
//  为什么要有这份 tb：前 10 份 tb / 170 项断言**从来没有让环路闭过环**。
//  tb_pi_audit.v 只把 y_code 钉成常数看积分项步进；tb_light_lock.v 里的植物模型
//  算出来的 plant_y 只写不读、根本没接回 DUT，"PI 收敛"其实只断言了"输出不越限"。
//
//  被控对象（植物）：一阶惯性 + 可调正增益
//      target = (out_code × plant_gain) >> 8      plant_gain=256 ⇒ 单位增益
//      y      = y + ((target - y) >>> 3)          时间常数约 8 个控制周期
//
//  ★ 本轮跑出来的第一条真实结论（不是 tb 的 bug，是设计的参数特性）：
//    `pi_ctrl` 的抗积分饱和只认 `i_clamp`，**不认输出轨**。默认 I_CLAMP=0x0020_0000
//    是 2^-9 码单位 ⇒ 积分最多贡献 ±4096 码；再叠上默认 BIAS=0x2000 和比例项，
//    闭环能达到的输出上限只有 0x2000 + 4096 + 256 ≈ 12544 (0x3100)，
//    **够不到 OUT_MAX=0x3FFF**。所以要闭环打到满量程，必须先把 I_CLAMP 调大。
//    [A1] 专门把这个平台期钉下来（integ 恰好停在 i_clamp、i_at_limit=1），
//    [A2] 再把 i_clamp 调大，验证"调大之后能真的收敛到目标"。
//
//  [A1] 默认 i_clamp ⇒ 平台期（积分撞钳、够不到目标）   [A2] 放大 i_clamp ⇒ 真收敛
//  [B]  dir_pol=0 反作用 ⇒ 推到下轨（极性语义，接反了上机就是烧功率）
//  [C]  目标不可达 ⇒ 输出撞上轨 + 积分一路走到 i_clamp（windup 上界）
//  [D]  freeze 冻住输出与积分      [E] set_valid 重装基准码   [F] clr_i 清积分
//  [G]  enable=0 整环停住          [H] Kp≈0 纯积分收敛        [I] ki_shift>22 的钳位
// ============================================================================
module tb_pi_loop;

    reg         sys_clk = 1'b0;
    reg         sys_rst_n = 1'b0;
    reg         tick = 1'b0;
    reg         enable = 1'b1;
    reg         clr_i = 1'b0;
    reg         set_valid = 1'b0;
    reg         freeze = 1'b0;
    reg         dir_pol = 1'b1;
    reg  [13:0] y_code = 14'd0;
    reg  [13:0] sp_code = 14'd8000;
    reg  [13:0] bias_code = 14'd2000;
    reg  [4:0]  kp_shift = 5'd6;
    reg  [5:0]  ki_shift = 6'd12;
    reg  [31:0] i_clamp = 32'h0020_0000;
    reg  [13:0] out_min = 14'd0;
    reg  [13:0] out_max = 14'h3FFF;

    wire [13:0]        out_code;
    wire signed [15:0] err;
    wire signed [31:0] integ;
    wire               i_at_limit;
    wire               out_at_limit;

    integer pass_cnt = 0;
    integer fail_cnt = 0;

    // ---- 植物状态 ------------------------------------------------------------
    //  ★ 用 1/8 码分辨率存 y（y8）：若直接在整数码上写 `y += (target-y)>>>3`，
    //    模型自己就会在差 7 码处停住，把"控制器的静差"和"模型的截断"混在一起。
    reg  [15:0] plant_gain = 16'd256;   // 8.8 定点：256 = 单位增益
    reg signed [20:0] y8 = 21'sd0;      // 实测光强码 × 8

    wire [23:0] plant_prod   = out_code * plant_gain;
    wire [13:0] plant_target = (plant_prod[21:8] > 24'd16383) ? 14'h3FFF : plant_prod[21:8];
    wire [20:0] target8      = {3'd0, plant_target, 3'd0};   // ×8
    wire [13:0] y            = y8[16:3];                     // 整数码

    pi_ctrl dut (
        .sys_clk     (sys_clk),
        .sys_rst_n   (sys_rst_n),
        .tick        (tick),
        .enable      (enable),
        .clr_i       (clr_i),
        .set_valid   (set_valid),
        .y_code      (y_code),
        .sp_code     (sp_code),
        .bias_code   (bias_code),
        .kp_shift    (kp_shift),
        .ki_shift    (ki_shift),
        .i_clamp     (i_clamp),
        .out_min     (out_min),
        .out_max     (out_max),
        .dir_pol     (dir_pol),
        .freeze      (freeze),
        .out_code    (out_code),
        .err         (err),
        .integ       (integ),
        .i_at_limit  (i_at_limit),
        .out_at_limit(out_at_limit)
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

    // ---- 跑一个控制周期：驱动 tick，并按最新 out_code 推进植物 ----------------
    task plant_step;
        reg signed [21:0] ynext;
        begin
            @(negedge sys_clk);
            y_code = y;
            tick   = 1'b1;
            @(negedge sys_clk);
            tick   = 1'b0;
            ynext = y8 + (($signed({1'b0, target8}) - y8) >>> 3);
            if (ynext < 22'sd0)           y8 = 21'sd0;
            else if (ynext > 22'sd131064) y8 = 21'sd131064;   // 16383×8
            else                          y8 = ynext[20:0];
        end
    endtask

    integer k;
    task run_ticks(input integer n);
        begin
            for (k = 0; k < n; k = k + 1) plant_step;
        end
    endtask

    task do_reset;
        begin
            sys_rst_n = 1'b0;
            tick = 1'b0; clr_i = 1'b0; set_valid = 1'b0; freeze = 1'b0;
            enable = 1'b1; dir_pol = 1'b1; kp_shift = 5'd6; ki_shift = 6'd12;
            i_clamp = 32'h0020_0000; out_min = 14'd0; out_max = 14'h3FFF;
            sp_code = 14'd8000; bias_code = 14'd2000; plant_gain = 16'd256;
            y8 = 21'sd0; y_code = 14'd0;
            repeat (4) @(negedge sys_clk);
            sys_rst_n = 1'b1;
            repeat (2) @(negedge sys_clk);
        end
    endtask

    reg [13:0] held_out;
    reg signed [31:0] held_integ;

    initial begin
        $display("===== tb_pi_loop start =====");
        do_reset;

        // ---- [A1] 默认 i_clamp：积分撞钳 ⇒ 输出卡在平台期，够不到目标 -----------
        //  平台期理论值：out = bias(2000) + prop + 4096 = 6096 + (8000-y)/64，y≈out ⇒ 6125
        $display("-- [A1] 默认 I_CLAMP=0x00200000（±4096 码）：sp=8000 够不到 --");
        run_ticks(40000);
        $display("   t=40000 y=%0d out=%0d err=%0d integ=%0d i_at_limit=%0d",
                 y, out_code, err, integ, i_at_limit);
        //  抗饱和判据取的是 i_sum = integ + istep ⇒ 积分停在离 i_clamp **一步之内**，不是正好等于
        check_in("A1 integ 停在 i_clamp 一步之内", integ, 2096152, 2097152);
        check("A1 i_at_limit 置位", {31'd0, i_at_limit}, 32'd1);
        check_in("A1 输出停在平台期", out_code, 5950, 6300);
        check("A1 够不到目标（err 还很大）", (err > 16'sd1500) ? 32'd1 : 32'd0, 32'd1);
        check("A1 没撞输出轨", {31'd0, out_at_limit}, 32'd0);

        // ---- [A2] 放大 i_clamp ⇒ 同一个环真的收敛到零静差 ----------------------
        $display("-- [A2] I_CLAMP=0x01000000（±32768 码）：应收敛到 sp=8000 --");
        i_clamp = 32'h0100_0000;
        run_ticks(40000);
        $display("   t=40000 y=%0d out=%0d err=%0d integ=%0d", y, out_code, err, integ);
        //  ★ 积分步进 istep=(e<<9)>>>ki_shift 的量化 ⇒ 静差死区 = 0..2^(ki_shift-9)-1 码
        //    （ki_shift=12 ⇒ 0..7 码：e<8 时 istep 截成 0，积分不再动）。这是量化特性，
        //    想要更小的静差就把 ki_shift 调小（ki_shift=6 时死区为 0）。
        check_in("A2 静差在积分死区内", err, -7, 7);
        check_in("A2 y≈目标",           y,   7993, 8000);
        check_in("A2 out≈目标",         out_code, 7993, 8000);
        check("A2 未撞 i_clamp", {31'd0, i_at_limit}, 32'd0);
        check("A2 未撞输出轨",   {31'd0, out_at_limit}, 32'd0);

        // ---- [D] freeze：整环冻住（输出与积分都不动）--------------------------
        $display("-- [D] freeze=1：输出与积分必须一动不动 --");
        held_out   = out_code;
        held_integ = integ;
        freeze = 1'b1;
        plant_gain = 16'd512;        // 植物增益突变，正常闭环会跟着动
        run_ticks(3000);
        check("D 输出冻住", {18'd0, out_code}, {18'd0, held_out});
        check("D 积分冻住", integ, held_integ);
        freeze = 1'b0;
        run_ticks(3000);
        check("D 解冻后重新动作", (out_code != held_out) ? 32'd1 : 32'd0, 32'd1);
        plant_gain = 16'd256;

        // ---- [E] set_valid：重装基准码、清积分、清两个到限标志 -----------------
        $display("-- [E] set_valid：out_code 装成 bias_code、integ 清零 --");
        bias_code = 14'd4000;
        @(negedge sys_clk); set_valid = 1'b1;
        @(negedge sys_clk); set_valid = 1'b0;
        check("E out==bias",   {18'd0, out_code}, 32'd4000);
        check("E integ 清零",  integ, 32'sd0);
        check("E 到限标志清零", {30'd0, out_at_limit, i_at_limit}, 32'd0);

        // ---- [F] clr_i：只清积分，输出保持 ------------------------------------
        $display("-- [F] clr_i：integ 清零且输出保持 --");
        run_ticks(3000);
        check("F 积分重新累起来", (integ != 32'sd0) ? 32'd1 : 32'd0, 32'd1);
        held_out = out_code;
        @(negedge sys_clk); clr_i = 1'b1;
        @(negedge sys_clk); clr_i = 1'b0;
        check("F integ 清零", integ, 32'sd0);
        check("F 输出保持",   {18'd0, out_code}, {18'd0, held_out});

        // ---- [G] enable=0：整环停住 ------------------------------------------
        $display("-- [G] enable=0：输出与积分都保持 --");
        run_ticks(3000);
        held_out   = out_code;
        held_integ = integ;
        enable = 1'b0;
        plant_gain = 16'd64;
        run_ticks(3000);
        check("G 输出保持", {18'd0, out_code}, {18'd0, held_out});
        check("G 积分保持", integ, held_integ);
        enable = 1'b1;
        plant_gain = 16'd256;

        // ---- [B] dir_pol=0：同一植物、同一目标，反作用 ⇒ 必然推到下轨 ----------
        $display("-- [B] dir_pol=0（反作用）：应发散到 out_min=0 --");
        do_reset;
        i_clamp = 32'h0100_0000;
        dir_pol = 1'b0;
        run_ticks(40000);
        $display("   t=40000 y=%0d out=%0d err=%0d integ=%0d", y, out_code, err, integ);
        check("B 输出撞下轨 0", {18'd0, out_code}, 32'd0);
        check("B 到限标志置位", {31'd0, out_at_limit}, 32'd1);
        check("B 光强塌到 0",   (y <= 14'd4) ? 32'd1 : 32'd0, 32'd1);
        check("B 误差持续满量程", (err >= 16'sd7990) ? 32'd1 : 32'd0, 32'd1);

        // ---- [C] 目标不可达 + 输出撞上轨 ⇒ 积分 windup 到 i_clamp 为止 ---------
        $display("-- [C] plant_gain=64（最高只能到 4095）、sp=12000 ⇒ 输出撞 0x3FFF --");
        do_reset;
        i_clamp = 32'h0100_0000;
        plant_gain = 16'd64;
        sp_code    = 14'd12000;
        run_ticks(40000);
        $display("   t=40000 y=%0d out=%0d err=%0d integ=%0d i_at_limit=%0d out_at_limit=%0d",
                 y, out_code, err, integ, i_at_limit, out_at_limit);
        check("C 输出撞上轨",   {18'd0, out_code}, 32'd16383);
        check("C 输出到限标志", {31'd0, out_at_limit}, 32'd1);
        // 植物模型是 1/8 码分辨率，收敛后 y8 落在 target8 的 7 个 1/8 码之内
        // ⇒ y 可能是 4094 或 4095（实测 4094），两个都算"卡在可达上限"。
        check_in("C 光强卡在可达上限", y, 14'd4094, 14'd4095);
        // 积分一路涨到 i_clamp 附近（这里没有"按输出轨抗饱和"，只有 i_clamp 钳位）
        check_in("C integ 停在 i_clamp 一步之内", integ, 16776216, 16777216);
        check("C i_at_limit 置位", {31'd0, i_at_limit}, 32'd1);
        run_ticks(5000);
        check_in("C integ 不再增长", integ, 16776216, 16777216);

        // ---- [H] Kp≈0（kp_shift=14）纯积分：仍能收敛到零静差 ------------------
        $display("-- [H] kp_shift=14（Kp≈2^-14）、ki_shift=6：纯积分也应收敛 --");
        do_reset;
        i_clamp  = 32'h0100_0000;
        kp_shift = 5'd14;
        ki_shift = 6'd6;
        run_ticks(4000);
        $display("   t=4000  y=%0d out=%0d err=%0d integ=%0d", y, out_code, err, integ);
        check_near("H 收敛 err≈0", err, 0, 6);
        check_near("H y≈目标",     y,   8000, 6);

        // ---- [I] ki_shift > 22 的钳位：不得把积分器整个打死 -------------------
        //  ki_s 钳到 22 ⇒ istep = (16383<<9)>>>22 = 1（不是 0）。跑 1000 拍应恰好 +1000。
        $display("-- [I] ki_shift=30（>22 被钳到 22）：istep 必须还是 1，不许塌成 0 --");
        do_reset;
        i_clamp  = 32'h1000_0000;
        ki_shift = 6'd30;
        sp_code  = 14'h3FFF;
        bias_code = 14'd0;
        y8 = 21'sd0; y_code = 14'd0;
        run_ticks(1000);
        $display("   t=1000  y=%0d out=%0d err=%0d integ=%0d", y, out_code, err, integ);
        check("I ki_s 钳位后 integ 仍在累加", integ, 32'sd1000);
        check_near("I 误差仍满量程", err, 16383, 600);

        $display("===== tb_pi_loop 结果：%0d 项通过，%0d 项失败 =====", pass_cnt, fail_cnt);
        if (fail_cnt == 0) $display("result: PASS");
        else               $display("result: FAIL");
        $finish;
    end

endmodule
