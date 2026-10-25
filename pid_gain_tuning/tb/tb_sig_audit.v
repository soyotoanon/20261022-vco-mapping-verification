`timescale 1ns / 1ps
// ============================================================================
//  tb_sig_audit.v —— dac_sig_gen 幅度口径**回归**（第四轮补断言版）
//
//  ★ 第四轮自查发现：本文件原来**一条断言都没有**（只有 $display 打印 mn/mx），
//    却被算进"9 份带判定 tb"里 ⇒ 实测 0 失败根本不能说明这四类波形是对的。
//    同时文件头引用的行号（:234/:197-198/:210/:246）在第二轮修完之后**全部失效**。
//    现在改成真断言：每种波形都按 RTL 的幅度口径算期望值再比。
//
//  当前（第二轮修完）`rtl/dac_sig_gen.v:135-136` 的幅度口径：
//      amp_norm  = min(amp, 0x3FFF - offset)        // 上不越 14 位顶
//      amp_norm2 = min(amp_norm, offset)            // 下不越 0
//  四种波形的期望（off = offset, a = amp）：
//      方波  : max = off+amp_norm,  min = off-amp_norm2           （:140-141）
//      锯齿  : min = off,           max = off+amp_norm2-…         （:149-151，相位量化见 tol）
//      三角  : max = off+amp_norm-…, min = off-amp_norm2          （:161-168，相位量化见 tol）
//      正弦  : off ± (32767*amp_norm2)>>15，取整误差 ±2            （:184-187）
//      阶跃  : 前 4 拍 = off，之后 = off+amp_norm（**不是**输出 amp）（:197）
//  ★ 锯齿/三角的峰顶取不到 `off+amp_norm2` 的精确值：相位每拍跳 `2^20/period` 分度，
//    极值点恰好落在回卷那一拍上 ⇒ 实测峰值最多低 `span/period`（三角是 `2·span/period`，
//    因为它取 phase[18:5]、每拍跳 512）。这是**相位量化的固有性质**，不是 RTL 缺陷；
//    tb 按 `span/per` 给容差，真正的回归牙齿是下面两条单调性断言。
//  关键回归点：**幅度必须随 amp 单调增**（第二轮修之前正弦 amp≥0x2000 时被当成
//  负数 ⇒ amp 越大摆幅越小；锯齿则完全无视 amp）。
// ============================================================================
module tb_sig_audit;

    reg         sys_clk = 1'b0;
    reg         sys_rst_n = 1'b0;
    reg         tick = 1'b0;
    reg         enable = 1'b1;
    reg         update = 1'b0;
    reg  [2:0]  mode = 3'd0;
    reg  [13:0] fixed_code = 14'h2000;
    reg  [13:0] amp = 14'h0400;
    reg  [13:0] offset = 14'h2000;
    reg  [15:0] period = 16'd64;

    wire [13:0] code;

    dac_sig_gen dut (
        .sys_clk (sys_clk), .sys_rst_n (sys_rst_n), .tick (tick),
        .enable (enable), .update (update), .mode (mode),
        .fixed_code (fixed_code), .amp (amp), .offset (offset),
        .period (period), .code (code)
    );

    always #10 sys_clk = ~sys_clk;      // 50 MHz

    integer pass_cnt = 0;
    integer fail_cnt = 0;
    integer check_idx = 0;

    // ★ 名字位宽给 512bit：256bit(=32 字节) 时中文名会被左截断，FAIL 行看不全。
    // ★ 每条都带 ASCII 序号，控制台乱码时也能靠序号定位是哪一条断言。
    task check(input integer cond, input [511:0] nm);
        begin
            check_idx = check_idx + 1;
            if (cond) begin $display("  [PASS] #%0d %0s", check_idx, nm); pass_cnt = pass_cnt + 1; end
            else      begin $display("  [FAIL] #%0d %0s", check_idx, nm); fail_cnt = fail_cnt + 1; end
        end
    endtask

    // 一个控制周期脉冲 + 在"时钟沿之后、下一个沿之前"取一次稳定样本。
    // ★ 采样点很容易搞错：`code` 是每拍无条件 `code <= sig_out`，所以
    //   在新相位生效的那个 posedge 上，NBA 还没落地、读到的还是旧值；
    //   必须等到该沿之后的 negedge 再采，才拿到本拍的新码。
    // ★ 采样变量必须是 integer（有符号）。写成 `reg [13:0] samp` 时，
    //   `samp > mx` 里 samp 无符号 ⇒ 整个比较按**无符号**做，`mx = -1`
    //   会被当成 0xFFFFFFFF，条件永远为假，最大值恒为 -1（本次就踩了）。
    task tick_and_sample(output integer s);
        begin
            @(negedge sys_clk); tick = 1'b1;
            @(posedge sys_clk);
            @(negedge sys_clk); tick = 1'b0; #1;
            s = code;
            @(posedge sys_clk);
        end
    endtask

    integer k;
    integer mn;
    integer mx;
    integer samp;   // 必须是有符号，否则 `samp > mx` 退化成无符号比较
    integer last_mn, last_mx;

    // 走一遍波形并检查期望值
    task run_case(input [2:0] md, input [13:0] a, input [13:0] off,
                  input [15:0] per, input integer nticks, input [255:0] tag);
        integer an, an2, pk, tol;
        begin
            mode = md; amp = a; offset = off; period = per;
            update = 1'b1;
            @(posedge sys_clk); @(negedge sys_clk);
            update = 1'b0;
            @(posedge sys_clk);
            mn = 100000; mx = -1;
            for (k = 0; k < nticks; k = k + 1) begin
                tick_and_sample(samp);
                if (samp < mn) mn = samp;
                if (samp > mx) mx = samp;
            end
            last_mn = mn; last_mx = mx;
            // RTL 的幅度口径（整数算，和中途截位无关）
            an  = (a > (16383 - off)) ? (16383 - off) : a;
            an2 = (an > off) ? off : an;
            pk  = (32767 * an2) >> 15;
            $display("  mode=%0d amp=0x%04h off=0x%04h per=%0d => [%0d, %0d]  (amp_norm=%0d amp_norm2=%0d pk=%0d)",
                     md, a, off, per, mn, mx, an, an2, pk);

            check((mn >= 0) && (mx <= 16383), {tag, " 不越 14 位范围"});
            case (md)
                3'd1: begin     // 方波：精确 ±
                    check(mx == off + an,  {tag, " 方波高电平 = off+amp_norm"});
                    check(mn == off - an2, {tag, " 方波低电平 = off-amp_norm2"});
                end
                3'd3: begin     // 锯齿：offset 爬到 offset+amp_norm2-…
                    check(mn == off, {tag, " 锯齿起点 = offset"});
                    // ★ 离散相位：phase[19:6] 每拍跳 2^20/per >> 6，极值点恰好落在回卷那一拍上
                    //   ⇒ 采到的峰值最多低 span/per。这是相位量化的固有性质，不是缺陷；
                    //   容差按 span/per 给（period 越大越紧），真正的回归牙齿是下面的单调性检查。
                    tol = (an2 / per) + 2;
                    check((mx <= off + an2) && (mx >= off + an2 - tol),
                          {tag, " 锯齿峰值 ≈ off+amp_norm2（容差 ≤ span/per）"});
                end
                3'd4: begin     // 三角：顶 off+amp_norm（相位量化，见 tol）、底 off-amp_norm2（精确）
                    tol = (2 * (an + an2) / per) + 2;
                    check((mx <= off + an) && (mx >= off + an - tol),
                          {tag, " 三角顶 ≈ off+amp_norm（容差 ≤ 2·span/per）"});
                    check(mn == off - an2, {tag, " 三角底 = off-amp_norm2"});
                end
                3'd5: begin     // 正弦：off ± (32767*amp_norm2)>>15，允许 ±2 取整
                    check((mx <= off + pk + 1) && (mx >= off + pk - 2),
                          {tag, " 正弦峰 ≈ off+pk（幅度线性）"});
                    // ★ 又踩了同一个坑（见 :64-66 的警告）：`off - pk - 2` 里 off 是无符号 14bit、
                    //   pk 是有符号 integer ⇒ 整个表达式按**无符号**算，off<pk+2 时（off=0x0800=2048、
                    //   pk=2047）结果是 -1 → 0xFFFFFFFF，`mn >= 0xFFFFFFFF` 恒假。
                    //   ⇒ 改写成只做正数加法的等价形式：mn >= off-pk-2 ⟺ mn+pk+2 >= off。
                    check((mn + pk + 2 >= off) && (mn + pk - 1 <= off),
                          {tag, " 正弦谷 ≈ off-pk（没有反相/压扁）"});
                end
                3'd2: begin     // 阶跃：先 offset 再 offset+amp_norm
                    check(mn == off,            {tag, " 阶跃低电平 = offset"});
                    check(mx == off + an,       {tag, " 阶跃高电平 = off+amp_norm（不是 amp 本身）"});
                end
                default: ;
            endcase
        end
    endtask

    integer span_lo, span_mid, span_hi;

    initial begin
        $display("=== tb_sig_audit: dac_sig_gen 幅度口径回归 ===");
        sys_rst_n = 1'b0;
        repeat (4) @(posedge sys_clk);
        sys_rst_n = 1'b1;
        repeat (2) @(posedge sys_clk);

        $display("[1] 正弦：amp 从 0x0400 扫到 0x3FFF（重点：amp>=0x2000 不能被当负数）");
        run_case(3'd5, 14'h0400, 14'h2000, 16'd64, 200, "SINE amp=0400 ");
        span_lo = last_mx - last_mn;
        run_case(3'd5, 14'h1000, 14'h2000, 16'd64, 200, "SINE amp=1000 ");
        span_mid = last_mx - last_mn;
        run_case(3'd5, 14'h1FFF, 14'h2000, 16'd64, 200, "SINE amp=1FFF ");
        run_case(3'd5, 14'h2000, 14'h2000, 16'd64, 200, "SINE amp=2000 ");
        run_case(3'd5, 14'h3000, 14'h2000, 16'd64, 200, "SINE amp=3000 ");
        run_case(3'd5, 14'h3FFF, 14'h2000, 16'd64, 200, "SINE amp=3FFF ");
        span_hi = last_mx - last_mn;
        check((span_mid > span_lo) && (span_hi > span_mid),
              "正弦摆幅随 amp 单调增（旧 bug 是反的：amp 越大越扁）");

        $display("[2] 方波");
        run_case(3'd1, 14'h0400, 14'h2000, 16'd64, 200, "SQUARE amp=0400");
        run_case(3'd1, 14'h2000, 14'h2000, 16'd64, 200, "SQUARE amp=2000");
        run_case(3'd1, 14'h3FFF, 14'h2000, 16'd64, 200, "SQUARE amp=3FFF");

        $display("[3] 锯齿：改 amp 波形必须跟着变（旧 bug 是完全无视 amp）");
        run_case(3'd3, 14'h0400, 14'h2000, 16'd64, 200, "RAMP amp=0400  ");
        span_lo = last_mx - last_mn;
        run_case(3'd3, 14'h1000, 14'h2000, 16'd64, 200, "RAMP amp=1000  ");
        span_mid = last_mx - last_mn;
        run_case(3'd3, 14'h3FFF, 14'h2000, 16'd64, 200, "RAMP amp=3FFF  ");
        span_hi = last_mx - last_mn;
        check((span_mid > span_lo) && (span_hi > span_mid),
              "锯齿摆幅随 amp 单调增（旧 bug 是恒定 0x3FFF-offset）");

        $display("[4] 三角");
        run_case(3'd4, 14'h0400, 14'h2000, 16'd64, 200, "TRI  amp=0400 ");
        run_case(3'd4, 14'h3FFF, 14'h2000, 16'd64, 200, "TRI  amp=3FFF ");

        $display("[5] 阶跃（默认 amp==offset 时旧实现是一条直线）");
        run_case(3'd2, 14'h2000, 14'h2000, 16'd64, 200, "STEP amp=2000 ");
        run_case(3'd2, 14'h3000, 14'h2000, 16'd64, 200, "STEP amp=3000 ");

        $display("[6] 偏置靠边：幅度应被夹住且绝不越界");
        run_case(3'd1, 14'h3FFF, 14'h0800, 16'd64, 200, "SQUARE off=0800");
        run_case(3'd5, 14'h3FFF, 14'h0800, 16'd64, 200, "SINE   off=0800");
        run_case(3'd4, 14'h3FFF, 14'h3F00, 16'd64, 200, "TRI    off=3F00");

        $display("");
        $display("========== 结果：%0d 通过 / %0d 失败 ==========", pass_cnt, fail_cnt);
        if (fail_cnt != 0) $display("RESULT: FAIL");
        else                $display("RESULT: PASS");
        $finish;
    end

    initial begin
        #40_000_000;
        $display(" !! 超时");
        $display("RESULT: FAIL");
        $finish;
    end

endmodule
