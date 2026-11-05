// ============================================================================
//  sin_lut.vh —— 共用的 Q15 正弦基准（四分之一波表 + 象限折返）
//
//  被 dac_sig_gen.v（【04】）和 step_gen.v（【17】【19】）用 `include 引入。
//  依赖：**调用模块里必须已经定义 `localparam integer PHASE_W`**（相位位宽），
//        这两个函数按 PHASE_W 取相位最高 8 位做象限折返。
//
//    qsin(idx)         idx 0..63 ⇒ sin(idx × 90° / 64) × 2^15（0..32767）
//    sincos(is_sin, p) PHASE_W 位整圈相位 ⇒ ±32767 的 Q15 值
//
//  为什么单独放一个文件（2026-10-14 第二轮自查）：
//    step_gen 原来自己用 5 级流水的四次多项式近似正弦
//    （1.5878t − 0.6460t² + 0.0797t³ − 0.0033t⁴）。那个多项式**不是奇函数**
//    （含 t²、t⁴ 偶次项），于是正半周峰值 +2086、负半周峰值 −4746，
//    输出正弦严重不对称 —— 而且它压根没有 testbench，一直没被发现。
//    现在两边共用这一张表：精确、对称、且只有一个基准，不会再各写一套。
//    表值 = round(sin(idx × 90/64 度) × 32767)。
// ============================================================================

    function [15:0] qsin;                // idx 0..63 => Q15 正弦值
        input [5:0] idx;
        begin
            case (idx)
                6'd0: qsin = 16'd0;     6'd1: qsin = 16'd804;   6'd2: qsin = 16'd1608;
                6'd3: qsin = 16'd2410;  6'd4: qsin = 16'd3212;  6'd5: qsin = 16'd4011;
                6'd6: qsin = 16'd4808;  6'd7: qsin = 16'd5602;  6'd8: qsin = 16'd6393;
                6'd9: qsin = 16'd7179;  6'd10: qsin = 16'd7962; 6'd11: qsin = 16'd8739;
                6'd12: qsin = 16'd9512; 6'd13: qsin = 16'd10278; 6'd14: qsin = 16'd11039;
                6'd15: qsin = 16'd11793; 6'd16: qsin = 16'd12539; 6'd17: qsin = 16'd13279;
                6'd18: qsin = 16'd14010; 6'd19: qsin = 16'd14732; 6'd20: qsin = 16'd15446;
                6'd21: qsin = 16'd16151; 6'd22: qsin = 16'd16846; 6'd23: qsin = 16'd17530;
                6'd24: qsin = 16'd18204; 6'd25: qsin = 16'd18868; 6'd26: qsin = 16'd19519;
                6'd27: qsin = 16'd20159; 6'd28: qsin = 16'd20787; 6'd29: qsin = 16'd21403;
                6'd30: qsin = 16'd22005; 6'd31: qsin = 16'd22594; 6'd32: qsin = 16'd23170;
                6'd33: qsin = 16'd23731; 6'd34: qsin = 16'd24279; 6'd35: qsin = 16'd24811;
                6'd36: qsin = 16'd25329; 6'd37: qsin = 16'd25832; 6'd38: qsin = 16'd26319;
                6'd39: qsin = 16'd26790; 6'd40: qsin = 16'd27245; 6'd41: qsin = 16'd27683;
                6'd42: qsin = 16'd28105; 6'd43: qsin = 16'd28510; 6'd44: qsin = 16'd28898;
                6'd45: qsin = 16'd29268; 6'd46: qsin = 16'd29621; 6'd47: qsin = 16'd29956;
                6'd48: qsin = 16'd30273; 6'd49: qsin = 16'd30571; 6'd50: qsin = 16'd30852;
                6'd51: qsin = 16'd31113; 6'd52: qsin = 16'd31356; 6'd53: qsin = 16'd31580;
                6'd54: qsin = 16'd31785; 6'd55: qsin = 16'd31971; 6'd56: qsin = 16'd32137;
                6'd57: qsin = 16'd32285; 6'd58: qsin = 16'd32412; 6'd59: qsin = 16'd32521;
                6'd60: qsin = 16'd32609; 6'd61: qsin = 16'd32678; 6'd62: qsin = 16'd32728;
                6'd63: qsin = 16'd32757;
                default: qsin = 16'd0;
            endcase
        end
    endfunction

    // 四象限折返：ph8 = 相位最高 8 位（0..255）⇒ 0°..360°
    //   ph8<64   : 第一象限  sin=+qsin(ph8)      cos=+qsin(63-ph8)
    //   64..127  : 第二象限  sin=+qsin(127-ph8)  cos=-qsin(ph8-64)
    //   128..191 : 第三象限  sin=-qsin(ph8-128)  cos=-qsin(191-ph8)
    //   192..255 : 第四象限  sin=-qsin(255-ph8)  cos=+qsin(ph8-192)
    // 返回值 = 真值 × 2^15。调用处乘 amp 后要 >>>15 才是码值偏移。
    // ★ 用 wire 做中间量、比较用 8 位无符号：Verilog 里给 reg 赋负值会让
    //   "reg >= 192" 这种比较按有符号解释而失效。
    function signed [15:0] sincos;
        input               is_sin;
        input [PHASE_W-1:0] p;
        reg [7:0] ph8;
        reg [7:0] idx;
        reg       neg;
        begin
            ph8 = p[PHASE_W-1:PHASE_W-8];       // 8 位相位 => 0..255
            idx = 8'd0;
            neg = 1'b0;
            if (ph8 < 8'd64) begin
                idx = is_sin ? ph8 : (8'd63 - ph8);
                neg = 1'b0;
            end else if (ph8 < 8'd128) begin
                idx = is_sin ? (8'd127 - ph8) : (ph8 - 8'd64);
                neg = is_sin ? 1'b0 : 1'b1;
            end else if (ph8 < 8'd192) begin
                idx = is_sin ? (ph8 - 8'd128) : (8'd191 - ph8);
                neg = 1'b1;
            end else begin
                idx = is_sin ? (8'd255 - ph8) : (ph8 - 8'd192);
                neg = is_sin ? 1'b1 : 1'b0;
            end
            sincos = neg ? -$signed({1'b0, qsin(idx[5:0])})
                         :  $signed({1'b0, qsin(idx[5:0])});
        end
    endfunction
