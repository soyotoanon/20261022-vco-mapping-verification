`timescale 1ns / 1ps
`include "hw_params.vh"
// ============================================================================
//  light_lock_top.v —— 光强锁定主工程顶层
//
//  把 01~21 全部功能装进一个可烧录、可在线调参、带保护与数据回传的 bit。
//
//  运行模式（真实实现：`wire [1:0] mode = ctrl[3:2]` **只用来选 DAC 的数据源**；
//            "直通"是独立的 ctrl[1]，PI 使能 = ctrl[0] && (mode==2'd0) && !passthrough）：
//    code_sel 2'd0 → pi_out       ：PI 闭环输出（r=SETPOINT，y=ADC，u→DAC）   【12/13/14/17/19】
//    code_sel 2'd1 → sig_code     ：dac_sig_gen 输出测试波形                  【04】
//    code_sel 2'd2 → dac_code_cfg ：DAC_CODE 固定码输出（自检用）             【02】
//    code_sel 2'd3 → sweep_code   ：sweep_gen 扫 AOM 工作点，同时统计上传      【07/11】
module light_lock_top #(
    parameter integer LOSS_LOG2 = 8      // 光丢失判定窗口 = 2^LOSS_LOG2 个控制周期
) (
    input  wire        sys_clk,        // R4，50 MHz
    input  wire        sys_rst_n,      // U2，低有效
    // AD9248
    input  wire [13:0] adc_d,          // J2-1..14
    input  wire        adc_otr,        // J2-17
    output wire        adc_clk,        // J2-15
    output wire        adc_oeb_n,      // J2-16
    output wire        adc_pdn,        // J2-18
    // AD9767 端口 1 / 2
    output wire [13:0] da1_data,       // J2-19..32
    output wire        da1_clk,        // J2-33
    output wire        da1_wrt,        // J2-34
    output wire [13:0] da2_data,       // J2-35..48
    output wire        da2_clk,        // J2-49
    output wire        da2_wrt,        // J2-50
    // 串口（板载 Type-C 串口，不用飞线：FPGA 收 uart_rxd=U5，FPGA 发 uart_txd=T6，
    //       115200 8N1，BAUD_DIV=434。★ 审查修正：原注释的 "J3-23 / J3-4" 是过期方案）
    output wire        uart_tx,
    input  wire        uart_rx,
    // 状态灯（板载 LED，方便不接示波器时看死活）
    output wire        led_heartbeat,
    output wire [5:0] seg_sel_n,
    output wire [7:0] seg_n,
    output wire [1:0]  led_status
);

    // ---------------------------------------------------------------- 寄存器
    wire [15:0] ctrl, dac_code_cfg, sweep_start, sweep_end;
    wire [15:0] sweep_step_lo, sweep_step_hi, sweep_ctl, sweep_hold;
    wire [23:0] sweep_hold_w = {8'd0, sweep_hold};
    wire [15:0] ctrl_half, adc_half_cfg;
    wire [15:0] sig_en_r, sig_mode_r, sig_fixed_r, sig_amp_r, sig_off_r, sig_period_r;
    wire [15:0] setpoint_r, bias_r, kp_shift_r, ki_shift_r;
    wire [15:0] i_clamp_lo, i_clamp_hi, out_min_r, out_max_r, dir_pol_r;
    wire [15:0] light_th_r, stats_log2_r;
    wire [15:0] sp_slew_r, feedback_ch_r, loop_arm_r;
    wire [15:0] dist_en_r, dist_mode_r, dist_amp_r, dist_period_r;
    wire [15:0] log_every_r, stream_en_r, baud_div_r;

    wire [15:0] live_adc_w, live_err_w, live_out_w, live_mean, live_var;
    wire [15:0] live_min, live_max, live_status;
    wire [31:0] live_integ;

    wire reg_we;
    wire [5:0]  reg_addr;
    wire [15:0] reg_wdata, reg_rdata, base_rdata, perf_rdata;
    assign reg_rdata = ((reg_addr >= 6'h30 && reg_addr <= 6'h37) || reg_addr >= 6'h3B) ? perf_rdata : base_rdata;
    wire resp_req, resp_ack;
    wire [63:0] resp_data;
    wire [7:0]  tx_byte;
    wire        tx_vld, tx_ready;

    // ------------------------------------------------------------ 控制周期
    wire        tick;
    wire [15:0] adc_half    = adc_half_cfg[15:0];
    wire [24:0] ctrl_half_w = {9'd0, ctrl_half};

    ctrl_tick #(.CNT_W(25)) u_tick (
        .sys_clk   (sys_clk),
        .sys_rst_n (sys_rst_n),
        .half      (ctrl_half_w),
        .tick      (tick)
    );

    // -------------------------------------------------------------- ADC→DAC
    wire passthrough = ctrl[1];
    wire [13:0] pi_out;
    wire [13:0] sig_code;
    wire [13:0] sweep_code;
    wire        sweep_done;
    wire [15:0] sweep_steps;
    wire [13:0] adc_sample, adc_sample1, adc_sample2;
    wire adc_valid1, adc_valid2;
    wire adc_sample_valid, otr_sticky, adc_clk_alive;
    wire [1:0] mode = ctrl[3:2];
    assign adc_sample = feedback_ch_r[0] ? adc_sample2 : adc_sample1;
    assign adc_sample_valid = feedback_ch_r[0] ? adc_valid2 : adc_valid1;
    wire pi_enable = ctrl[0] && mode == 0 && !passthrough && loop_arm_r[0];
    wire [15:0] otr_count;
    wire [13:0] dac_code_now;


    // 输出码选择（组合，无 inout）
    reg [13:0] code_sel;
    always @(*) begin
        if (passthrough)                     code_sel = adc_sample;
        else case (mode)
            2'd1:    code_sel = sig_code;
            2'd2:    code_sel = dac_code_cfg[13:0];
            2'd3:    code_sel = sweep_code;
            default: code_sel = pi_out;
        endcase
    end

    adc_dac_core #(.HALF_W(16)) u_core (
        .sys_clk          (sys_clk),
        .sys_rst_n        (sys_rst_n),
        .passthrough      (passthrough),          // dual-channel passthrough is selected inside the core
        .adc_half         (adc_half),
        .sel_code         (code_sel),
        .adc_d_pin        (adc_d),
        .adc_otr_pin      (adc_otr),
        .adc_clk          (adc_clk),
        .adc_oeb_n        (adc_oeb_n),
        .adc_pdn          (adc_pdn),
        .da1_data         (da1_data),
        .da1_clk          (da1_clk),
        .da1_wrt          (da1_wrt),
        .da2_data         (da2_data),
        .da2_clk          (da2_clk),
        .da2_wrt          (da2_wrt),
        .adc_sample       (adc_sample1),
        .adc_sample_valid (adc_valid1),
        .adc_sample2 (adc_sample2), .adc_sample2_valid (adc_valid2),
        .otr_sticky       (otr_sticky),
        .otr_count        (otr_count),
        .adc_clk_alive    (adc_clk_alive),
        .dac_code_now     (dac_code_now)
    );

    // 2026-10-20: independent calibrated power display, units mW.
    // Synchronize the physical overrange flag for this observer.
    (* ASYNC_REG="TRUE" *) reg [1:0] display_otr_sync;
    always @(posedge sys_clk or negedge sys_rst_n)
        if (!sys_rst_n) display_otr_sync <= 0;
        else display_otr_sync <= {display_otr_sync[0],adc_otr};
    wire [23:0] displayed_bcd;
    wire displayed_valid;
    power_display_piecewise u_power_display (
        .clk(sys_clk), .rst_n(sys_rst_n),
        .pi_code(adc_sample), .sample_valid(adc_sample_valid),
        .feedback_channel(feedback_ch_r[0]), .otr(display_otr_sync[1]),
        .seg_sel_n(seg_sel_n), .seg_n(seg_n), .power_units(), .display_valid(),
        .display_bcd(displayed_bcd), .display_frame_valid(displayed_valid)
    );

    // ---------------------------------------------------------------- 扰动
    wire signed [15:0] dist_delta;

    step_gen u_dist (
        .sys_clk   (sys_clk),
        .sys_rst_n (sys_rst_n),
        .tick      (tick),
        .enable    (dist_en_r[0]),
        .update    (dist_en_r[1]),
        .mode      (dist_mode_r[2:0]),
        .amp       (dist_amp_r),
        .period    (dist_period_r),
        .delta     (dist_delta)
    );

    // r[n] = clamp(SETPOINT + delta, 0, 0x3FFF)
    wire signed [16:0] sp_sum = $signed({1'b0, setpoint_r[13:0]})
                              + $signed({dist_delta[15], dist_delta});
    wire [13:0] sp_final = (sp_sum[16] == 1'b1)       ? 14'h0000 :
                           (sp_sum[15:0] > 16'd16383) ? 14'h3FFF :
                                                        sp_sum[13:0];

    // ------------------------------------------------------------- PI 闭环
    // （监视器相关线网必须先声明，Verilog 不允许实例端口隐式新建后再显式声明）
    wire               frozen, light_lost, otr_seen, out_sat_seen;
    wire               i_at_limit, out_at_limit;
    wire signed [15:0] pi_err;
    wire signed [31:0] pi_integ;
    wire [15:0]        loss_cnt;

    wire perf_hold, perf_clip;
    reg [13:0] sp_active;
    wire [13:0] sp_step = sp_slew_r[13:0] == 0 ? 14'd1 : sp_slew_r[13:0];
    wire [13:0] sp_gap = sp_final > sp_active ? sp_final-sp_active : sp_active-sp_final;
    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) sp_active <= 14'h2000;
        else if (adc_sample_valid) begin
            if (!pi_enable || ctrl[4]) sp_active <= adc_sample;
            else if (!frozen && !perf_hold) begin
                if (sp_gap <= sp_step) sp_active <= sp_final;
                else if (sp_final > sp_active) sp_active <= sp_active + sp_step;
                else sp_active <= sp_active - sp_step;
            end
        end
    end
    reg pi_enable_d;
    always @(posedge sys_clk or negedge sys_rst_n)
        if (!sys_rst_n) pi_enable_d <= 0;
        else pi_enable_d <= pi_enable;
    wire [13:0] pi_out_c;
    wire [13:0] perf_output;
    performance_probe u_performance (
      .clk(sys_clk),.rst_n(sys_rst_n),.sample_valid(adc_sample_valid),
      .sample(adc_sample),.control_code(pi_out_c),.out_min(out_min_r[13:0]),.out_max(out_max_r[13:0]),
      .fault(frozen || out_at_limit || display_otr_sync[1]),.active(pi_enable),
      .we(reg_we),.addr(reg_addr),.wdata(reg_wdata),.rdata(perf_rdata),
      .hold_loop(perf_hold),.output_code(perf_output),.injection_clipped(perf_clip));
    pi_ctrl u_pi (
        .sys_clk      (sys_clk),
        .sys_rst_n    (sys_rst_n),
        .tick         (adc_sample_valid),
        .enable       (pi_enable),
        .clr_i        (ctrl[8]),
        .set_valid    (ctrl[4] || (pi_enable && !pi_enable_d)),
        .y_code       (adc_sample),
        .sp_code      (sp_active),
        .bias_code    (bias_r[13:0]),
        .kp_shift     (kp_shift_r),
        .ki_shift     (ki_shift_r),
        .i_clamp      ({i_clamp_hi, i_clamp_lo}),
        .out_min      (out_min_r[13:0]),
        .out_max      (out_max_r[13:0]),
        .dir_pol      (dir_pol_r[0]),
        .freeze       (frozen || perf_hold),
        .out_code     (pi_out_c),
        .err          (pi_err),
        .integ        (pi_integ),
        .i_at_limit   (i_at_limit),
        .out_at_limit (out_at_limit)
    );

    assign pi_out = perf_output;

    // ------------------------------------------------------------ 保护监视
    wire monitor_en = pi_enable;

    monitor #(.LOSS_LOG2(LOSS_LOG2)) u_mon (
        .sys_clk      (sys_clk),
        .sys_rst_n    (sys_rst_n),
        .tick         (adc_sample_valid),
        .enable       (monitor_en),
        .adc_code     (adc_sample),
        .light_th     (light_th_r),
        .otr_sticky   (otr_sticky),
        .i_at_limit   (i_at_limit),
        .out_at_limit (out_at_limit),
        .clr_flags    (ctrl[9]),
        .frozen       (frozen),
        .light_lost   (light_lost),
        .otr_seen     (otr_seen),
        .out_sat_seen (out_sat_seen),
        .loss_cnt     (loss_cnt)
    );

    // --------------------------------------------------------------- 统计
    wire [15:0] stats_mean, stats_var;
    // u_stats 的 min/max 是 14 位端口，先用独立 14 位线接进来，再零扩展到 16 位上送
    // （不要直接把 live_min[13:0] 接到端口上：那样 16 位变量的高 2 位就没驱动了）
    wire [13:0] stats_min, stats_max;

    adc_stats #(.LOG2_W(5), .SUM_W(46)) u_stats (
        .sys_clk   (sys_clk),
        .sys_rst_n (sys_rst_n),
        .tick      (adc_sample_valid),
        .enable    (ctrl[0]),
        .clr       (ctrl[10]),          // ★ 第二轮修复：CTRL[10] 清统计原来是不接任何东西的死位
        .log2_n    (stats_log2_r[4:0]),
        .sample    (adc_sample),
        .sum_out   (),
        .var_out   (),
        .mean      (stats_mean),
        .var_q4    (stats_var),
        .min_code  (stats_min),
        .max_code  (stats_max)
    );

    // ------------------------------------------------------- 信号发生器(04)
    dac_sig_gen u_sig (
        .sys_clk    (sys_clk),
        .sys_rst_n  (sys_rst_n),
        .tick       (tick),
        .enable     (sig_en_r[0]),
        .update     (sig_en_r[1]),
        .mode       (sig_mode_r[2:0]),
        .fixed_code (sig_fixed_r[13:0]),
        .amp        (sig_amp_r[13:0]),
        .offset     (sig_off_r[13:0]),
        .period     (sig_period_r),
        .code       (sig_code)
    );

    // ---------------------------------------------------- 自动扫描(07/11)
    sweep_gen #(.HOLD_W(24)) u_sweep (
        .sys_clk    (sys_clk),
        .sys_rst_n  (sys_rst_n),
        .tick       (tick),
        .enable     (sweep_ctl[0]),
        .start      (sweep_ctl[2]),
        .start_code (sweep_start[13:0]),
        .end_code   (sweep_end[13:0]),
        .step_code  ({sweep_step_hi[7:0], sweep_step_lo}),
        .hold       (sweep_hold_w),
        .cont       (sweep_ctl[1]),
        .code       (sweep_code),
        .done       (sweep_done),
        .step_count (sweep_steps)
    );

    // ------------------------------------------------------------ 状态与灯
    assign live_adc_w  = {2'b00, adc_sample};
    assign live_err_w  = pi_err;
    assign live_out_w  = {2'b00, dac_code_now};
    assign live_integ  = pi_integ;
    assign live_mean   = stats_mean;
    assign live_var    = stats_var;
    assign live_min    = {2'b00, stats_min};
    assign live_max    = {2'b00, stats_max};
    assign live_status = {9'b0, sweep_done, !adc_clk_alive, frozen,
                          i_at_limit, out_at_limit, otr_seen, light_lost};

    reg [23:0] heartbeat;
    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) heartbeat <= 24'd0;
        else if (tick)  heartbeat <= heartbeat + 1'b1;
    end
    assign led_heartbeat = heartbeat[15];
    // ---- led_status：由 CTRL[12:11] 选 STATUS 的哪一个字节 ------------------
    wire [1:0] led_sel = ctrl[12:11];
    assign led_status    = (led_sel == 2'd0) ? live_status[1:0] :
                           (led_sel == 2'd1) ? live_status[3:2] :
                           (led_sel == 2'd2) ? live_status[5:4] :
                                               live_status[7:6];

    // ------------------------------------------------------------ 串口链路
    uart_proto #(.DIV_W(16)) u_proto (
        .sys_clk   (sys_clk),
        .sys_rst_n (sys_rst_n),
        .baud_div  (baud_div_r),
        .rx_pin    (uart_rx),
        .reg_we    (reg_we),
        .reg_addr  (reg_addr),
        .reg_wdata (reg_wdata),
        .reg_rdata (reg_rdata),
        .resp_req  (resp_req),
        .resp_data (resp_data),
        .resp_ack  (resp_ack)
    );

    log_arbiter u_log (
        .sys_clk   (sys_clk),
        .sys_rst_n (sys_rst_n),
        .tick      (tick),
        .stream_en (stream_en_r[0]),
        .log_every (log_every_r),
        .snap_adc  (live_adc_w),
        .tx_ready  (tx_ready),
        .resp_req  (resp_req),
        .resp_data (resp_data),
        .tx_byte   (tx_byte),
        .tx_vld    (tx_vld),
        .resp_ack  (resp_ack)
    );

    uart_tx #(.DIV_W(16)) u_utx (
        .sys_clk  (sys_clk),
        .sys_rst_n(sys_rst_n),
        .baud_div (baud_div_r),
        .tx_byte  (tx_byte),
        .tx_vld   (tx_vld),
        .tx_pin   (uart_tx),
        .tx_ready (tx_ready)
    );

    // ------------------------------------------------------------- 寄存器堆
    reg_file u_regs (
        .sys_clk       (sys_clk),
        .sys_rst_n     (sys_rst_n),
        .we            (reg_we),
        .addr          (reg_addr),
        .wdata         (reg_wdata),
        .rdata         (base_rdata),
        .live_display_bcd(displayed_bcd), .live_display_valid(displayed_valid),
        .live_adc      (live_adc_w),
        .live_err      (live_err_w),
        .live_out      (live_out_w),
        .live_integ    (live_integ),
        .live_mean     (live_mean),
        .live_var      (live_var),
        .live_min      (live_min),
        .live_max      (live_max),
        .live_status   (live_status),
        .ctrl          (ctrl),
        .dac_code      (dac_code_cfg),
        .sweep_start   (sweep_start),
        .sweep_end     (sweep_end),
        .sweep_step_lo (sweep_step_lo),
        .sweep_step_hi (sweep_step_hi),
        .sweep_hold    (sweep_hold),
        .sweep_ctl     (sweep_ctl),
        .ctrl_half     (ctrl_half),
        .adc_half      (adc_half_cfg),
        .sig_en        (sig_en_r),
        .sig_mode      (sig_mode_r),
        .sig_fixed     (sig_fixed_r),
        .sig_amp       (sig_amp_r),
        .sig_off       (sig_off_r),
        .sig_period    (sig_period_r),
        .setpoint      (setpoint_r),
        .bias_code     (bias_r),
        .kp_shift      (kp_shift_r),
        .ki_shift      (ki_shift_r),
        .i_clamp_lo    (i_clamp_lo),
        .i_clamp_hi    (i_clamp_hi),
        .out_min       (out_min_r),
        .out_max       (out_max_r),
        .dir_pol       (dir_pol_r),
        .light_th      (light_th_r),
        .stats_log2    (stats_log2_r),
        .dist_en       (dist_en_r),
        .dist_mode     (dist_mode_r),
        .dist_amp      (dist_amp_r),
        .dist_period   (dist_period_r),
        .log_every     (log_every_r),
        .stream_en     (stream_en_r),
        .sp_slew (sp_slew_r), .feedback_ch (feedback_ch_r), .loop_arm (loop_arm_r),
        .baud_div      (baud_div_r)
    );

    // ------------------------------------------------ 【06】延迟测试标记(ILA)
    // 只在 ILA_ENABLE 打开时综合进去，默认构建不含调试核。
`ifdef ILA_ENABLE
    wire marker_adc, marker_dac;
    dbg_marker u_dbg (
        .sys_clk    (sys_clk),
        .sys_rst_n  (sys_rst_n),
        .adc_clk    (adc_clk),
        .dac_update (adc_sample_valid),
        .marker_adc (marker_adc),
        .marker_dac (marker_dac)
    );

    ila_light_lock u_ila (
        .clk        (sys_clk),
        .probe0     (adc_sample),
        .probe1     ({2'b00, dac_code_now}),
        .probe2     (live_err_w),
        .probe3     ({31'b0, marker_adc}),
        .probe4     ({31'b0, marker_dac}),
        .probe5     ({2'b00, sp_final}),
        .probe6     ({16'b0, sweep_steps}),
        .probe7     (live_status)
    );
`endif

    wire _unused = |{otr_count, loss_cnt, sweep_ctl[15:3], 1'b0};

endmodule
