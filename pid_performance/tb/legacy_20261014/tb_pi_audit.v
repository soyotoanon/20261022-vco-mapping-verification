`timescale 1ns / 1ps
// ============================================================================
//  tb_pi_audit.v —— pi_ctrl 积分项【回归测试】
//  （原为一次性审计脚本，2026-10 自查后转正：现在锁死**修好后**的行为）
//
//  背景（自查发现并修掉的一个 CRITICAL bug）：
//      原实现（rtl\pi_ctrl.v）：
//          wire signed [31:0] istep = e32 >>> (ki_shift + 9);
//      上位机默认 ki_shift = 12（KI_SHIFT_DEFAULT / reg_file 6'h1B）⇒ 右移 21 位，
//      而误差是 14 位码域差、|e| ≤ 16383 < 2^14。后果：
//          - 正误差：e >>> 21 = 0        ⇒ **积分在正方向完全不动**
//          - 负误差：e >>> 21 = -1       ⇒ 每拍固定 -1，与误差大小无关
//      ⇒ 不是"增益太小"，而是符号不对称、步长与误差大小完全解耦，闭环不可能正常工作。
//      修法：istep 改为 (e32 <<< 9) >>> ki_shift，并在输出端用 i_out = integ >>> 9
//      还原 2^-9 标度（原来把 integ 按满标度直接加到输出上，等效增益差了 512 倍）。
//
//  本 tb 覆盖：
//      [A] 单拍积分步长 istep 的符号与幅值表（含 ki_shift 上限钳位）
//      [B] 正/负误差长跑，积分必须**对称**累加
//      [C] i_clamp 饱和 + i_at_limit 上报
//      [D] set_valid：清零积分并把输出装订到 bias_code
//      [E] clr_i：只清积分，不动输出
//      [F] freeze：冻结时输出保持
//
//  注意：sp_code/y_code 都是 14 位，所以合法误差范围是 [-8192, 8191]。
//  本 tb 只测 |e| ≤ 8000，避免 14 位溢出造成"假失败"。
// ============================================================================
module tb_pi_audit;

    reg sys_clk   = 1'b0;
    reg sys_rst_n = 1'b0;
    reg tick      = 1'b0;
    reg enable    = 1'b1;
    reg clr_i     = 1'b0;
    reg set_valid = 1'b0;
    reg [13:0] y_code    = 14'h2000;
    reg [13:0] sp_code   = 14'h2000;
    reg [13:0] bias_code = 14'h2000;
    reg [4:0]  kp_shift  = 5'd6;
    reg [5:0]  ki_shift  = 6'd12;
    reg [31:0] i_clamp   = 32'h0020_0000;
    reg [13:0] out_min   = 14'h0000;
    reg [13:0] out_max   = 14'h3FFF;
    reg dir_pol  = 1'b1;
    reg freeze   = 1'b0;

    wire [13:0]        out_code;
    wire signed [15:0] err;
    wire signed [31:0] integ;
    wire               i_at_limit;
    wire               out_at_limit;

    integer n_pass = 0;
    integer n_fail = 0;
    integer i;
    integer integ_pos;
    integer integ_neg;

    pi_ctrl dut (
        .sys_clk      (sys_clk),
        .sys_rst_n    (sys_rst_n),
        .tick         (tick),
        .enable       (enable),
        .clr_i        (clr_i),
        .set_valid    (set_valid),
        .y_code       (y_code),
        .sp_code      (sp_code),
        .bias_code    (bias_code),
        .kp_shift     (kp_shift),
        .ki_shift     (ki_shift),
        .i_clamp      (i_clamp),
        .out_min      (out_min),
        .out_max      (out_max),
        .dir_pol      (dir_pol),
        .freeze       (freeze),
        .out_code     (out_code),
        .err          (err),
        .integ        (integ),
        .i_at_limit   (i_at_limit),
        .out_at_limit (out_at_limit)
    );

    always #10 sys_clk = ~sys_clk;          // 周期 20 ns = 50 MHz

    task check(input [8*48-1:0] name, input cond);
        begin
            if (cond) begin
                n_pass = n_pass + 1;
                $display("  [PASS] %0s", name);
            end else begin
                n_fail = n_fail + 1;
                $display("  [FAIL] %0s", name);
            end
        end
    endtask

    task one_tick;
        begin
            @(negedge sys_clk); tick = 1'b1;
            @(posedge sys_clk); #1;
            @(negedge sys_clk); tick = 1'b0;
            @(posedge sys_clk);
        end
    endtask

    // 设定误差（y 固定中点），不跑时钟，直接看组合出来的 e_wide / istep
    task set_err(input integer e);
        begin
            sp_code = 14'd8192 + e;
            y_code  = 14'd8192;
            #2;
        end
    endtask

    // 单拍步长检查：给出期望值
    task chk_istep(input integer e, input integer expect);
        begin
            set_err(e);
            $display("    e=%0d  e_wide=%0d  istep=%0d  (期望 %0d)",
                     e, dut.e_wide, dut.istep, expect);
            check("istep 符合 (e<<<9)>>>ki_shift", dut.istep == expect);
            @(posedge sys_clk);
        end
    endtask

    // 清积分（并跳过一拍让时序块落地）
    task clear_integ;
        begin
            clr_i = 1'b1; @(posedge sys_clk); @(negedge sys_clk);
            clr_i = 1'b0; @(posedge sys_clk);
        end
    endtask

    initial begin
        $display("========== tb_pi_audit: pi_ctrl 积分项回归 ==========");
        $display("参数：ki_shift=%0d  kp_shift=%0d  i_clamp=%0d  dir_pol=%0d",
                 ki_shift, kp_shift, i_clamp, dir_pol);
        $display("istep 期望公式：(e <<< 9) >>> ki_shift = e * 512 / 4096 = e/8");

        sys_rst_n = 1'b0;
        repeat (4) @(posedge sys_clk);
        sys_rst_n = 1'b1;
        repeat (2) @(posedge sys_clk);

        // ---------------- [A] 单拍步长表 ----------------
        $display("");
        $display("[A] 单拍积分步长 istep（这是原 bug 的核心：正误差曾恒为 0）");
        chk_istep(    1,     0);
        chk_istep(    8,     1);
        chk_istep(  100,    12);
        chk_istep( 1000,   125);
        chk_istep( 4000,   500);
        chk_istep( 8000,  1000);
        chk_istep(   -1,    -1);
        chk_istep(   -8,    -1);
        chk_istep( -100,   -13);
        chk_istep(-1000,  -125);
        chk_istep(-4000,  -500);
        chk_istep(-8000, -1000);

        // ---------------- [B] 双向累加对称性 ----------------
        $display("");
        $display("[B] 正/负误差各跑 5000 拍，积分必须对称累加（istep=125 ⇒ ±625000）");
        clear_integ;
        set_err(1000);
        for (i = 0; i < 5000; i = i + 1) one_tick;
        integ_pos = integ;
        $display("    正误差 +1000：integ=%0d  out_code=%0d", integ, out_code);
        check("正误差积分在累加（原 bug 下恒 0）", integ_pos > 600000);
        check("正误差积分量级正确 (±5%)", integ_pos > 593750 && integ_pos < 656250);

        clear_integ;
        set_err(-1000);
        for (i = 0; i < 5000; i = i + 1) one_tick;
        integ_neg = integ;
        $display("    负误差 -1000：integ=%0d  out_code=%0d", integ, out_code);
        check("负误差积分在累加", integ_neg < -600000);
        check("正负积分对称 (|差| < 1%)", (integ_pos + integ_neg) > -6250 &&
                                          (integ_pos + integ_neg) <  6250);

        // ---------------- [C] i_clamp 饱和 ----------------
        $display("");
        $display("[C] i_clamp 饱和：设 i_clamp=1048576，长跑应停住并报 i_at_limit");
        clear_integ;
        i_clamp = 32'd1048576;
        set_err(1000);
        for (i = 0; i < 20000; i = i + 1) one_tick;
        $display("    integ=%0d  i_at_limit=%0d", integ, i_at_limit);
        check("积分被 i_clamp 限幅", integ > 32'd1040000 && integ < 32'd1050000);
        check("i_at_limit 已上报", i_at_limit === 1'b1);
        i_clamp = 32'h0020_0000;

        // ---------------- [D] set_valid 装订 ----------------
        $display("");
        $display("[D] set_valid：清零积分并把输出装订到 bias_code=0x2000");
        clear_integ;
        set_err(1000);
        for (i = 0; i < 200; i = i + 1) one_tick;
        $display("    装订前：integ=%0d  out_code=%0h", integ, out_code);
        check("装订前积分非 0", integ != 0);
        @(negedge sys_clk); set_valid = 1'b1;
        @(posedge sys_clk); #1;
        @(negedge sys_clk); set_valid = 1'b0;
        @(posedge sys_clk);
        $display("    装订后：integ=%0d  out_code=%0h", integ, out_code);
        check("set_valid 清零积分", integ == 0);
        check("set_valid 把输出装订到 bias_code", out_code === bias_code);

        // ---------------- [E] clr_i 只清积分 ----------------
        $display("");
        $display("[E] clr_i：只清积分，输出保持不变");
        set_err(1000);
        for (i = 0; i < 200; i = i + 1) one_tick;
        begin : e_block
            reg [13:0] out_before;
            out_before = out_code;
            clear_integ;
            $display("    清积分前 out_code=%0h，清后 out_code=%0h", out_before, out_code);
            check("clr_i 清零积分", integ == 0);
            check("clr_i 不动输出", out_code === out_before);
        end

        // ---------------- [F] freeze 保持输出 ----------------
        $display("");
        $display("[F] freeze：冻结时输出保持不随误差变化");
        clear_integ;
        set_err(1000);
        for (i = 0; i < 200; i = i + 1) one_tick;
        begin : f_block
            reg [13:0] out_frozen;
            out_frozen = out_code;
            freeze = 1'b1;
            set_err(-4000);              // 误差反号，若没冻结输出会跑掉
            for (i = 0; i < 200; i = i + 1) one_tick;
            $display("    冻结前 out_code=%0h，反号误差跑 200 拍后 out_code=%0h",
                     out_frozen, out_code);
            check("freeze 期间输出保持", out_code === out_frozen);
            check("freeze 期间积分保持", integ != 0);
            freeze = 1'b0;
        end

        // ---------------- 汇总 ----------------
        $display("");
        $display("========== 结果：%0d 通过 / %0d 失败 ==========", n_pass, n_fail);
        if (n_fail == 0) $display(" >>> 全部通过");
        else             $display(" >>> 存在失败项，见上面 [FAIL] 行");
        $display("======================================================");
        $finish;
    end

    initial begin
        #20_000_000;
        $display("!! TIMEOUT");
        $finish;
    end

endmodule
