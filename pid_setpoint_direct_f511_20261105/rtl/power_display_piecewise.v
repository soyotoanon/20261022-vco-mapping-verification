`timescale 1ns/1ps
// Live calibrated single-sample display, refreshed at 10 Hz; independent of PI/DAC.
module power_display_piecewise #(
 parameter integer UPDATE_CYCLES=5000000, SCAN_DIV=50000, STALE_CYCLES=50000000
)(
 input wire clk,rst_n,
 input wire [13:0] pi_code,
 input wire sample_valid, feedback_channel, otr,
 output reg [5:0] seg_sel_n,
 output reg [7:0] seg_n,
 output reg [19:0] power_units,
 output reg display_valid,
 output wire [23:0] display_bcd,
 output wire display_frame_valid
);
`include "piecewise_nodes_20261020.vh"
 reg [31:0] update_count;
 wire [13:0] calibration_code=14'h3fff-pi_code;
 wire [31:0] sample_q16={2'b00,calibration_code,16'b0};
 reg [31:0] sample_latched;
 reg [4:0] segment;
 integer j;
 always @(*) begin
   segment=0;
   for(j=1;j<30;j=j+1)
     if(sample_latched>=knot_q16(j)) segment=j;
 end
 reg signed [31:0] delta,gain,base_units;
 reg signed [63:0] product;
 reg [2:0] stage;
 reg range_ok;
 reg [31:0] stale;
 reg feedback_d;
 wire signed [63:0] correction=product>=0 ?
   ((product+64'sd34359738368)>>>36):
   -(((-product)+64'sd34359738368)>>>36);
 wire signed [63:0] rounded=correction+{{32{base_units[31]}},base_units};
 always @(posedge clk or negedge rst_n) begin
   if(!rst_n) begin
     update_count<=0;sample_latched<=0;delta<=0;gain<=0;base_units<=0;
     product<=0;stage<=0;range_ok<=0;stale<=0;feedback_d<=0;
     power_units<=0;display_valid<=0;
   end else begin
     feedback_d<=feedback_channel;
     if(sample_valid) stale<=0;
     else if(stale<STALE_CYCLES) stale<=stale+1;
     if(otr || feedback_channel || feedback_d!=feedback_channel ||
        (!sample_valid && stale>=STALE_CYCLES)) begin
       update_count<=0;stage<=0;display_valid<=0;
     end else begin
       if(update_count!=0) update_count<=update_count-1'b1;
       // Take one fresh sample when the display interval expires. No averaging.
       if(sample_valid && update_count==0 && stage==0) begin
         sample_latched<=sample_q16;
         range_ok<=sample_q16>=CODE_LOW_Q16 && sample_q16<=CODE_HIGH_Q16;
         stage<=1;
         update_count<=UPDATE_CYCLES-1;
       end
       if(stage==1) begin
         delta<=$signed(sample_latched)-$signed(knot_q16(segment));
         gain<=slope_q20(segment);base_units<=node_units(segment);stage<=2;
       end
       if(stage==2) begin product<=delta*gain;stage<=3;end
       if(stage==3) begin
         stage<=0;
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
    assign display_bcd=bcd_frame;
    assign display_frame_valid=frame_valid && display_valid;
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
