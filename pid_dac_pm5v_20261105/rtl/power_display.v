`timescale 1ns/1ps
`include "power_calibration_20261020.vh"
// Added 2026-10-20. Independent observer; never writes PI/DAC/setpoint state.
// PI code y = ~(raw ^ 0x2000); calibration code C = 16383-y.
// P in 0.0001 mW units = ((C_Q16 - ZERO_Q16) * GAIN_Q20) / 2^36.
module power_display #(
    parameter integer AVG_LOG2=20,
    parameter integer SCAN_DIV=50000,
    parameter integer STALE_CYCLES=50000000
)(
    input wire clk, input wire rst_n,
    input wire [13:0] pi_code, input wire sample_valid,
    input wire feedback_channel, input wire otr,
    input wire cfg_we, input wire [5:0] cfg_addr, input wire [15:0] cfg_data,
    output reg [5:0] seg_sel_n,
    output reg [7:0] seg_n,
    output reg [19:0] power_units,
    output reg display_valid
);
    // Shadow words + atomic apply prevent half-written coefficients on the display.
    reg [15:0] shadow [0:6];
    reg [15:0] control;
    reg signed [31:0] zero_q16, gain_q20;
    reg [15:0] code_min,code_max;
    wire apply_cfg=cfg_we && cfg_addr==6'h37 && cfg_data==16'ha501;
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            control<=16'h0001;
            zero_q16<=`POWER_ZERO_Q16;
            gain_q20<=`POWER_GAIN_Q20;
            code_min<=`POWER_CODE_MIN;
            code_max<=`POWER_CODE_MAX;
            shadow[0]<=16'h0001;
            shadow[1]<=`POWER_ZERO_Q16 & 16'hffff;
            shadow[2]<=(`POWER_ZERO_Q16 >> 16) & 16'hffff;
            shadow[3]<=`POWER_GAIN_Q20 & 16'hffff;
            shadow[4]<=(`POWER_GAIN_Q20 >> 16) & 16'hffff;
            shadow[5]<=`POWER_CODE_MIN;
            shadow[6]<=`POWER_CODE_MAX;
        end else begin
            if(cfg_we && cfg_addr>=6'h30 && cfg_addr<=6'h36)
                shadow[cfg_addr-6'h30]<=cfg_data;
            if(apply_cfg) begin
                control<=shadow[0];
                zero_q16<={shadow[2],shadow[1]};
                gain_q20<={shadow[4],shadow[3]};
                code_min<=shadow[5]; code_max<=shadow[6];
            end
        end
    end
    reg [AVG_LOG2-1:0] count;
    reg [AVG_LOG2+13:0] sum;
    wire [13:0] calibration_code=14'h3fff-pi_code;
    wire [AVG_LOG2+13:0] total=sum+calibration_code;
    wire [63:0] extended_total={{(50-AVG_LOG2){1'b0}},total};
    wire [31:0] mean_q16=(extended_total << 16) >> AVG_LOG2;
    reg signed [31:0] delta;
    reg signed [63:0] product;
    reg [1:0] stage;
    reg range_ok;
    reg [31:0] stale;
    reg feedback_d;
    wire signed [63:0] rounded=product>=0 ?
        ((product+64'sd34359738368) >>> 36) :
        -(((-product)+64'sd34359738368) >>> 36);
    wire usable=control[0] && feedback_channel==control[1] && !otr && gain_q20!=0;
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            count<=0;sum<=0;delta<=0;product<=0;stage<=0;
            power_units<=0;display_valid<=0;range_ok<=0;stale<=0;feedback_d<=0;
        end else begin
            feedback_d<=feedback_channel;
            if(sample_valid) stale<=0;
            else if(stale<STALE_CYCLES) stale<=stale+1;
            if(apply_cfg || !usable || feedback_d!=feedback_channel || (!sample_valid && stale>=STALE_CYCLES)) begin
                count<=0;sum<=0;stage<=0;display_valid<=0;
            end else begin
                if(sample_valid) begin
                    if(&count) begin
                        count<=0;sum<=0;
                        delta<=$signed(mean_q16)-zero_q16;
                        range_ok<=mean_q16>={code_min,16'b0} && mean_q16<={code_max,16'b0};
                        stage<=1;
                    end else begin count<=count+1'b1;sum<=total;end
                end
                if(stage==1) begin product<=delta*gain_q20;stage<=2;end
                if(stage==2) begin
                    stage<=0;
                    // Small negative fitted power is displayed as zero; no wraparound.
                    display_valid<=range_ok && rounded>=-2000 && rounded<=999999;
                    power_units<=rounded<0 ? 0 : rounded>999999 ? 999999 : rounded[19:0];
                end
            end
        end
    end
    // Sequential binary-to-BCD (double dabble), latched at the end of a scan.
    reg [43:0] bcd_shift,bcd_adjust;
    reg [23:0] bcd_ready,bcd_frame;
    reg [4:0] bcd_count;
    reg bcd_busy, frame_valid;
    integer k;
    always @(*) begin
        bcd_adjust=bcd_shift;
        for(k=0;k<6;k=k+1)
            if(bcd_shift[20+k*4 +:4]>=5)
                bcd_adjust[20+k*4 +:4]=bcd_shift[20+k*4 +:4]+3;
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin bcd_shift<=0;bcd_ready<=0;bcd_count<=0;bcd_busy<=0;end
        else if(!bcd_busy) begin
            bcd_shift<={24'd0,power_units};bcd_count<=0;bcd_busy<=1;
        end else begin
            bcd_shift<=bcd_adjust<<1;
            if(bcd_count==19) begin bcd_ready<=bcd_adjust[42:19];bcd_busy<=0;end
            else bcd_count<=bcd_count+1;
        end
    end
    reg [31:0] scan_count;
    reg [2:0] digit;
    reg [3:0] numeral;
    reg [6:0] segments;
    always @(*) begin
        numeral=(bcd_frame >> (digit*4)) & 4'hf;
        case(numeral)
            0:segments=7'b1000000;1:segments=7'b1111001;
            2:segments=7'b0100100;3:segments=7'b0110000;
            4:segments=7'b0011001;5:segments=7'b0010010;
            6:segments=7'b0000010;7:segments=7'b1111000;
            8:segments=7'b0000000;9:segments=7'b0010000;
            default:segments=7'b0111111;
        endcase
        seg_sel_n=6'b111111;seg_n=8'hff;
        if(scan_count>1) begin
            seg_sel_n=~(6'b000001<<digit);
            seg_n=frame_valid && display_valid ? {(digit!=4),segments} : 8'b10111111;
        end
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin scan_count<=0;digit<=0;bcd_frame<=0;frame_valid<=0;end
        else if(scan_count==SCAN_DIV-1) begin
            scan_count<=0;
            if(digit==5) begin digit<=0;bcd_frame<=bcd_ready;frame_valid<=display_valid;end
            else digit<=digit+1;
        end else scan_count<=scan_count+1;
    end
endmodule
