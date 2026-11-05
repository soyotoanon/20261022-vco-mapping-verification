`timescale 1ns / 1ps
// ============================================================================
//  uart_proto.v —— 上位机 ↔ FPGA 的帧协议（【05 串口参数设置与数据回传】）
//                 同时承担【16 PI 参数在线调整】的写入通道
//
//  帧格式（定长 8 字节，收发同构）：
//    [0] 0xC5  同步头
//    [1] cmd   0x01=写寄存器  0x02=读寄存器  0x03=数据流（FPGA→PC）
//    [2] addr  寄存器地址
//    [3] dataL 低字节
//    [4] dataH 高字节
//    [5] 0x5C  固定尾
//    [6] 0xC5  固定尾
//    [7] 0xC5  固定尾
//
//  写：直接生效，无回复（省带宽）。
//  读：本模块只负责"认出读命令并给出应答内容"，
//      实际发送交给 log_arbiter.v 仲裁（避免和连续数据流撞车）。
//
//  为什么把发送拆出去：数据流和读应答走同一条串口，必须有单点仲裁。
//  见 log_arbiter.v 头部注释。
// ============================================================================
module uart_proto #(
    parameter integer DIV_W = 16
) (
    input  wire                 sys_clk,
    input  wire                 sys_rst_n,
    input  wire [DIV_W-1:0]     baud_div,
    input  wire                 rx_pin,
    // 寄存器接口
    output reg                  reg_we,
    output reg  [5:0]           reg_addr,
    output reg  [15:0]          reg_wdata,
    input  wire [15:0]          reg_rdata,
    // 读应答（交给 log_arbiter 发）
    output reg                  resp_req,
    output wire [63:0]          resp_data,
    input  wire                 resp_ack
);

    localparam [7:0] SYNC0 = 8'hC5;
    localparam [7:0] SYNC1 = 8'h5C;
    localparam [7:0] CMD_W = 8'h01;
    localparam [7:0] CMD_R = 8'h02;

    wire [7:0] rx_byte;
    wire       rx_vld;

    uart_rx #(.DIV_W(DIV_W)) u_rx (
        .sys_clk(sys_clk), .sys_rst_n(sys_rst_n), .baud_div(baud_div),
        .rx_pin(rx_pin), .rx_byte(rx_byte), .rx_vld(rx_vld)
    );

    // ---- 收帧 ---------------------------------------------------------------
    // ★ 2026-10-14 第四轮审查修复：原来是 `reg [7:0] frame [0:7];` 按 byte_idx 变址写入。
    //   综合报告 `WARNING: [Synth 8-7137] Register frame_reg[1]/[2] in module uart_proto
    //   has both Set and reset with same priority. This may cause simulation mismatches.`
    //   —— 它意味着综合器给这两个寄存器同时推了异步置位与异步复位，仿真与硬件行为**可能不同**。
    //   而 `Synth 8-6014` 同时报告 frame_reg[0]/[3]/[4]/[5]/[6]/[7] 全被删掉：除命令与地址
    //   之外没有任何人读它们。所以这里改成两个显式寄存器（命令、地址），并给上复位
    //   —— 变址写入与未复位导致的置位/复位纠缠一并消失。
    reg [7:0]  frame_cmd;
    reg [5:0]  frame_addr;
    reg [2:0]  byte_idx;
    reg [15:0] latched_rdata;
    reg [5:0]  latched_addr;
    reg [7:0]  latched_cmd;
    reg [7:0]  latched_dataL;      // frame[3]：写数据低字节
    reg [7:0]  latched_dataH;      // frame[4]：写数据高字节

    // ★ 2026-10-14 第三轮审查新增：待提交的写命令。
    //   第 5 字节 0x5C 收到时只把内容记在这里，等第 6/7 字节的 0xC5 也校验通过
    //   才真正驱动 reg_we/reg_addr/reg_wdata。原实现当场就写寄存器，
    //   而第 6/7 字节从不校验 ⇒ 一帧被打断（少发/多插字节）也会照写，写错寄存器。
    reg        pend_we;
    reg [5:0]  pend_addr;
    reg [15:0] pend_wdata;

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            byte_idx     <= 3'd0;
            reg_we       <= 1'b0;
            reg_addr     <= 6'd0;
            reg_wdata    <= 16'd0;
            latched_dataL <= 8'd0;
            latched_dataH <= 8'd0;
            latched_rdata <= 16'd0;
            latched_addr  <= 6'd0;
            latched_cmd   <= 8'h00;
            frame_cmd     <= 8'h00;
            frame_addr    <= 6'd0;
            pend_we       <= 1'b0;
            pend_addr     <= 6'd0;
            pend_wdata    <= 16'd0;
        end else begin
            reg_we <= 1'b0;

            if (rx_vld) begin
                if (byte_idx == 3'd0) begin
                    // 等同步头
                    if (rx_byte == SYNC0) begin
                        byte_idx <= 3'd1;
                    end
                end else begin
                    byte_idx <= byte_idx + 1'b1;

                    // 只留真正会被读的两个字节：命令（第 1 字节）与地址（第 2 字节）
                    if (byte_idx == 3'd1) frame_cmd  <= rx_byte;
                    if (byte_idx == 3'd2) frame_addr <= rx_byte[5:0];

                    // frame[3]/frame[4] 就是数据低/高字节，必须在这里先锁存下来。
                    // 不能等到 byte_idx==5 再取：那一刻 rx_byte 已经是尾字节 0x5C 了。
                    if (byte_idx == 3'd3) latched_dataL <= rx_byte;
                    if (byte_idx == 3'd4) latched_dataH <= rx_byte;

                    if (byte_idx == 3'd5) begin
                        if (rx_byte == SYNC1) begin
                            if (frame_cmd == CMD_W) begin
                                // ★ 第三轮：只记下待写内容，等第 6/7 字节校验通过再提交
                                pend_we    <= 1'b1;
                                pend_addr  <= frame_addr;
                                pend_wdata <= {latched_dataH, latched_dataL};
                            end else if (frame_cmd == CMD_R) begin
                                // ★ 2026-10-14 第二轮审查修复：读命令也必须把地址送到寄存器堆。
                                //   原实现只锁存 latched_addr，reg_addr 仍停在上一次**写**操作的
                                //   地址上 ⇒ 读回的是别人寄存器里的值，而应答帧里的 addr 字节
                                //   却是本次请求的地址（主机看到"地址对、数据错"）。
                                reg_addr     <= frame_addr;
                                latched_addr <= frame_addr;
                                latched_cmd  <= frame_cmd;
                            end
                        end else begin
                            byte_idx <= 3'd0;                  // 第 5 字节错，丢帧重来
                            pend_we  <= 1'b0;
                        end
                    end

                    if (byte_idx == 3'd6) begin
                        latched_rdata <= reg_rdata;            // 组合读已按 addr 选好
                        // ★ 第三轮：第 7 字节（帧尾第二个 0xC5）也必须校验
                        if (rx_byte != SYNC0) begin
                            byte_idx <= 3'd0;                  // 丢帧，绝不写寄存器
                            pend_we  <= 1'b0;
                        end
                    end

                    if (byte_idx == 3'd7) begin
                        // 帧尾两个 0xC5 都验过 ⇒ 才允许提交写命令
                        if (rx_byte == SYNC0) begin
                            if (pend_we) begin
                                reg_we    <= 1'b1;
                                reg_addr  <= pend_addr;
                                reg_wdata <= pend_wdata;
                            end
                        end else begin
                            byte_idx <= 3'd0;                  // 第 8 字节错，丢帧不写
                        end
                        pend_we <= 1'b0;
                    end
                end
            end
        end
    end

    // ---- 应答内容 -----------------------------------------------------------
    assign resp_data = { SYNC0, latched_cmd, {2'b00, latched_addr},
                         latched_rdata[7:0], latched_rdata[15:8], SYNC1, SYNC0, SYNC0 };

    // ---- 应答请求：每收到一个完整读帧只发一次应答 ---------------------------
    // ★ 2026-10-14 第三轮审查修复：原判据是
    //     if (resp_pend == 0 && latched_cmd == CMD_R && latched_rdata == reg_rdata
    //         && byte_idx == 3'd7) resp_pend <= 1'b1;
    //   它会退化成"byte_idx==7 且上电后读过一次"：
    //     · latched_cmd 只在读命令分支赋值（写帧永不更新）⇒ 读过一次后恒为 0x02；
    //     · latched_rdata 在 byte_idx==6 无条件刷新成当时的 reg_rdata ⇒ 到 7 必然相等；
    //     · byte_idx 在第 8 字节到下一帧首字节之间一直停在 7。
    //   于是 resp_ack 清零 resp_pend 后下一拍立刻重新置位，仲裁器以读应答最高优先级
    //   无限重发同一帧（实测线上出现两帧相同应答），而且写帧也会被回一帧带旧地址的
    //   陈旧应答。现在改成一次性触发：完整读帧（idx7 尾字节校验通过）到达时 armed 置 1，
    //   仲裁器收下（resp_ack）后同时清 armed。
    reg resp_armed;
    reg resp_pend;

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            resp_armed <= 1'b0;
            resp_pend  <= 1'b0;
            resp_req   <= 1'b0;
        end else begin
            // 本帧命令用 frame_cmd（当前帧的内容），不能用陈旧的 latched_cmd
            if (rx_vld && byte_idx == 3'd7 && rx_byte == SYNC0 && frame_cmd == CMD_R)
                resp_armed <= 1'b1;

            if (resp_armed && resp_pend == 1'b0) resp_pend <= 1'b1;

            resp_req <= resp_pend;
            if (resp_ack) begin
                resp_pend  <= 1'b0;
                resp_armed <= 1'b0;
            end
        end
    end

endmodule
