`timescale 1ns / 1ps
// ============================================================================
//  uart_tx.v —— 串口发送（【05】的一部分）
//
//  8N1、LSB 先出。tx_ready 为高时给 tx_byte + tx_vld 一拍即发。
//  busy 期间 tx_ready 拉低，上层据此做反压。
// ============================================================================
module uart_tx #(
    parameter integer DIV_W = 16
) (
    input  wire                 sys_clk,
    input  wire                 sys_rst_n,
    input  wire [DIV_W-1:0]     baud_div,
    input  wire [7:0]           tx_byte,
    input  wire                 tx_vld,
    output reg                  tx_pin,
    output wire                 tx_ready
);

    localparam integer S_IDLE  = 0;
    localparam integer S_START = 1;
    localparam integer S_DATA  = 2;
    localparam integer S_STOP  = 3;

    reg [2:0]           state;
    reg [DIV_W-1:0]     baud_cnt;
    reg [2:0]           bit_idx;
    reg [7:0]           sh;

    assign tx_ready = (state == S_IDLE);

    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            state    <= S_IDLE;
            baud_cnt <= {DIV_W{1'b0}};
            bit_idx  <= 3'd0;
            sh       <= 8'd0;
            tx_pin   <= 1'b1;
        end else begin
            case (state)
                S_IDLE: begin
                    tx_pin <= 1'b1;
                    if (tx_vld) begin
                        sh       <= tx_byte;
                        baud_cnt <= {DIV_W{1'b0}};
                        state    <= S_START;
                    end
                end
                S_START: begin
                    tx_pin <= 1'b0;
                    if (baud_cnt >= baud_div - 1'b1) begin
                        baud_cnt <= {DIV_W{1'b0}};
                        bit_idx  <= 3'd0;
                        state    <= S_DATA;
                    end else begin
                        baud_cnt <= baud_cnt + 1'b1;
                    end
                end
                S_DATA: begin
                    tx_pin <= sh[bit_idx];
                    if (baud_cnt >= baud_div - 1'b1) begin
                        baud_cnt <= {DIV_W{1'b0}};
                        if (bit_idx == 3'd7) state <= S_STOP;
                        else                 bit_idx <= bit_idx + 1'b1;
                    end else begin
                        baud_cnt <= baud_cnt + 1'b1;
                    end
                end
                S_STOP: begin
                    tx_pin <= 1'b1;
                    if (baud_cnt >= baud_div - 1'b1) begin
                        baud_cnt <= {DIV_W{1'b0}};
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
