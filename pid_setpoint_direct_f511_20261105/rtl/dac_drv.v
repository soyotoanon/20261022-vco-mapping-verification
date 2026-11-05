`timescale 1ns / 1ps
`include "hw_params.vh"
// AD9767 dual-port: data -> WRT rising -> CLK rising -> idle.
// Separate WRT and CLK by 20 ns at 50 MHz. Hold data across both latches.
// At 5 MS/s every request completes before the next sample. Faster than
// 12.5 MS/s requests coalesce to the latest code (there is no FIFO).
module dac_drv (
    input wire sys_clk, sys_rst_n,
    input wire [13:0] data1, data2,
    output reg [13:0] da1_data,
    output wire da1_clk, da1_wrt,
    output reg [13:0] da2_data,
    output wire da2_clk, da2_wrt,
    output reg [13:0] dac_snapshot
);
    reg [1:0] phase;
    reg wrt_r, clk_r, initial_write;
    // Guard both physical DAC channels after every source/mode selection,
    // including independent dual-channel passthrough. Compare bounded values
    // so out-of-range requests do not cause repeated writes at a rail.
    wire [13:0] bounded1 = data1 < `DAC_VCO_MIN ? `DAC_VCO_MIN :
                           data1 > `DAC_VCO_MAX ? `DAC_VCO_MAX : data1;
    wire [13:0] bounded2 = data2 < `DAC_VCO_MIN ? `DAC_VCO_MIN :
                           data2 > `DAC_VCO_MAX ? `DAC_VCO_MAX : data2;
    assign da1_clk=clk_r;
    assign da2_clk=clk_r;
    assign da1_wrt=wrt_r;
    assign da2_wrt=wrt_r;
    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            da1_data<=`DAC_CODE_MID; da2_data<=`DAC_CODE_MID;
            dac_snapshot<=`DAC_CODE_MID;
            phase<=0; wrt_r<=0; clk_r<=0; initial_write<=1;
        end else begin
            case (phase)
                0: begin
                    wrt_r<=0; clk_r<=0;
                    if (initial_write || bounded1!=da1_data || bounded2!=da2_data) begin
                        da1_data<=bounded1; da2_data<=bounded2;
                        initial_write<=0; phase<=1;
                    end
                end
                1: begin wrt_r<=1; phase<=2; end
                2: begin
                    wrt_r<=0; clk_r<=1;
                    dac_snapshot<=da1_data; phase<=3;
                end
                3: begin clk_r<=0; phase<=0; end
            endcase
        end
    end
endmodule
