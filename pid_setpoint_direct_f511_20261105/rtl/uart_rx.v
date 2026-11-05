`timescale 1ns / 1ps
// ============================================================================
//  uart_rx.v —— 串口接收（【05】的一部分）
//
//  8N1、LSB 先出、波特率运行时可改，带两拍输入同步。
//  baud_div = SYS_CLK_HZ / 波特率。50MHz @115200 ⇒ 434。
//  收满一字节后 byte_vld 出一个 sys_clk 宽的脉冲。
// ============================================================================
module uart_rx #(
    parameter integer DIV_W = 16
) (
    input  wire                 sys_clk,
    input  wire                 sys_rst_n,
    input  wire [DIV_W-1:0]     baud_div,
    input  wire                 rx_pin,
    output reg  [7:0]           rx_byte,
    output reg                  rx_vld
);

    localparam integer S_IDLE  = 0;
    localparam integer S_START = 1;
    localparam integer S_DATA  = 2;
    localparam integer S_STOP  = 3;

    reg [1:0]           rx_sync;
    reg [2:0]           state;
    reg [DIV_W-1:0]     baud_cnt;
    reg [2:0]           bit_idx;
    reg [7:0]           sh;

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            rx_sync  <= 2'b11;
            state    <= S_IDLE;
            baud_cnt <= {DIV_W{1'b0}};
            bit_idx  <= 3'd0;
            sh       <= 8'd0;
            rx_vld   <= 1'b0;
            rx_byte  <= 8'd0;
        end else begin
            rx_sync <= {rx_sync[0], rx_pin};
            rx_vld  <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (rx_sync[1] == 1'b0) begin
                        baud_cnt <= {DIV_W{1'b0}};
                        state    <= S_START;
                    end
                end
                S_START: begin                    // 半位后复查，滤掉毛刺
                    if (baud_cnt >= (baud_div >> 1)) begin
                        baud_cnt <= {DIV_W{1'b0}};
                        if (rx_sync[1] == 1'b0) begin
                            bit_idx <= 3'd0;
                            state   <= S_DATA;
                        end else begin
                            state   <= S_IDLE;
                        end
                    end else begin
                        baud_cnt <= baud_cnt + 1'b1;
                    end
                end
                S_DATA: begin
                    if (baud_cnt >= baud_div - 1'b1) begin
                        baud_cnt    <= {DIV_W{1'b0}};
                        sh[bit_idx] <= rx_sync[1];
                        if (bit_idx == 3'd7) state <= S_STOP;
                        else                 bit_idx <= bit_idx + 1'b1;
                    end else begin
                        baud_cnt <= baud_cnt + 1'b1;
                    end
                end
                S_STOP: begin
                    if (baud_cnt >= baud_div - 1'b1) begin
                        baud_cnt <= {DIV_W{1'b0}};
                        rx_byte  <= sh;
                        rx_vld   <= 1'b1;
                        state    <= S_IDLE;
                    end else begin
                        baud_cnt <= baud_cnt + 1'b1;
                    end
                end
                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
