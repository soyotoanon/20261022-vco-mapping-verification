`timescale 1ns / 1ps
// ============================================================================
//  log_arbiter.v —— 【08 同步数据采集】【18 扰动响应数据记录】的发送仲裁
//
//  为什么要有它：读命令的应答和连续数据流会同一条串口，两个都在 pi_ctrl
//  更新那一刻想发就会撞车。这里做单点仲裁，优先级：读应答 > 连续上传。
//
//  快照帧（8 字节，与 uart_proto.v 同格式）：
//    [0]=0xC5 [1]=0x03 [2]=0x03 [3]=adcL [4]=adcH [5]=0x5C [6]=0xC5 [7]=0xC5
//  只带 ADC 码是故意的：误差和输出码都是 ADC 码的确定性函数，
//  上位机拿 ADC 曲线就能反算，省掉一半带宽 ⇒ 采样率翻倍。
//  想要更多量就降低上传频率、改用读寄存器一条条取。
// ============================================================================
module log_arbiter (
    input  wire        sys_clk,
    input  wire        sys_rst_n,
    input  wire        tick,          // 控制周期脉冲
    input  wire        stream_en,
    input  wire [15:0] log_every,
    input  wire [15:0] snap_adc,
    input  wire        tx_ready,
    input  wire        resp_req,      // 读应答请求
    input  wire [63:0] resp_data,     // 读应答 8 字节（高位先发）
    output reg  [7:0]  tx_byte,
    output reg         tx_vld,
    output reg         resp_ack
);

    localparam [7:0] SYNC0 = 8'hC5;
    localparam [7:0] SYNC1 = 8'h5C;

    reg        active;
    reg [63:0] sh;
    reg [3:0]  cnt;
    reg [15:0] div_cnt;
    reg        pending;
    reg        inflight;

    wire fire = stream_en && ((log_every == 16'd0) || (div_cnt >= log_every - 1'b1));

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            active   <= 1'b0;
            sh       <= 64'd0;
            cnt      <= 4'd0;
            div_cnt  <= 16'd0;
            pending  <= 1'b0;
            inflight <= 1'b0;
            tx_vld   <= 1'b0;
            tx_byte  <= 8'd0;
            resp_ack <= 1'b0;
        end else begin
            tx_vld   <= 1'b0;
            resp_ack <= 1'b0;

            // 控制周期抽样计数
            if (tick) begin
                if (fire) begin
                    div_cnt <= 16'd0;
                    pending <= 1'b1;
                end else begin
                    div_cnt <= div_cnt + 1'b1;
                end
            end

            // ---- 启动一帧：读应答优先 ----------------------------------------
            if (tx_ready && !active && !inflight) begin
                if (resp_req) begin
                    sh       <= resp_data;
                    cnt      <= 4'd8;
                    active   <= 1'b1;
                    resp_ack <= 1'b1;
                end else if (pending) begin
                    sh      <= { SYNC0, 8'h03, 8'h03,
                                 snap_adc[7:0], snap_adc[15:8], SYNC1, SYNC0, SYNC0 };
                    cnt     <= 4'd8;
                    active  <= 1'b1;
                    pending <= 1'b0;
                end
            end

            // ---- 逐字节发出 ---------------------------------------------------
            if (active && tx_ready && !inflight) begin
                // ★ 2026-10-14 第二轮审查修复：必须**最高字节先发**。
                //   原写法先发 sh[7:0]（最低字节），线上顺序与 resp_data / 快照帧的拼接顺序、
                //   与 uart_proto.v 文件头、本文件头、以及 host\llctl.py 的解析
                //   （raw[i+3] | raw[i+4]<<8）全都相反 ⇒ 读应答实际变成
                //   C5 C5 5C dataH dataL addr cmd C5，上位机把 dataH/dataL 拼反。
                // ★ 2026-10-14 第三轮审查修复：还必须是**每个字节只前进一次**。
                //   uart_tx 的 tx_ready = (state==S_IDLE)，它要到下一个时钟沿才装字节，
                //   所以同一字节的 tx_ready 会连续两拍为 1；只判电平会让 sh/cnt 每字节走两步，
                //   实测线上只剩 C5 10 AB C5 四个字节（隔一个取一个）。
                //   inflight 记住"本字节已发出、等 uart_tx 离开 S_IDLE"，tx_ready 落下时才清。
                tx_byte  <= sh[63:56];
                tx_vld   <= 1'b1;
                sh       <= {sh[55:0], 8'h00};
                cnt      <= cnt - 1'b1;
                inflight <= 1'b1;
                if (cnt == 4'd1) active <= 1'b0;
            end
            if (!tx_ready) inflight <= 1'b0;   // uart_tx 已离开 S_IDLE ⇒ 这个字节被收下了
        end
    end

endmodule
