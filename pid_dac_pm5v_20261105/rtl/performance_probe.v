`timescale 1ns/1ps
// Observer plus output injection. 8192 uniformly timed Q14.2 ADC samples.
// Three-stage CIC decimation; host uses f <= output_fs/10 and compensates droop.
// No configuration register changes the ADC/control sampling clock.
module performance_probe #(
    parameter AW=13
)(
    input wire clk, rst_n, sample_valid,
    input wire [13:0] sample, control_code, out_min, out_max,
    input wire fault, active,
    input wire we, input wire [5:0] addr, input wire [15:0] wdata,
    output reg [15:0] rdata,
    output wire hold_loop,
    output wire [13:0] output_code,
    output wire injection_clipped
);
    reg [1:0] control;
    reg [13:0] amplitude, hold_code;
    reg [31:0] phase_inc, phase;
    reg [3:0] decim_log;
    reg [AW-1:0] rd_index, wr_index;
    reg busy, done, clip_seen, fault_seen;
    reg [15:0] sequence_id;
    reg [15:0] decim_count;
    reg [2:0] warmup;
    reg [63:0] int1,int2,int3,comb1,comb2,comb3;
    reg [63:0] comb_result;
    reg filter_ready;
    reg [AW:0] count;
    (* ram_style="block" *) reg [15:0] memory [0:(1<<AW)-1];
    reg [15:0] rd_data;
    always @(posedge clk) rd_data <= memory[rd_index];

    // LUT generated as exact full-cycle sin(2*pi*k/256), signed Q15.
    function signed [15:0] sine;
      input [7:0] index;
      begin
        case(index)
          `include "performance_sine.vh"
          default: sine=0;
        endcase
      end
    endfunction
    wire signed [15:0] sv=sine(phase[31:24]);
    reg signed [30:0] product;
    reg signed [16:0] delta;
    always @(posedge clk or negedge rst_n)
      if (!rst_n) begin product<=0;delta<=0;end
      else begin
        product <= sv*$signed({1'b0,amplitude});
        delta <= product >>> 15;
      end
    assign hold_loop=control[1] && active;
    wire [13:0] base=hold_loop?hold_code:control_code;
    wire signed [17:0] sum=$signed({1'b0,base})+
                              ((control[0] && active)?delta:18'sd0);
    wire [13:0] high=out_max<out_min?out_min:out_max;
    // Normal feedback bypasses the injection adder and second clamp.
    // During a bounds write, retain the original combinational clamp until
    // pi_ctrl has applied the new bounds at the next sys_clk edge.
    reg [13:0] previous_min, previous_max;
    always @(posedge clk or negedge rst_n)
      if (!rst_n) begin previous_min<=0; previous_max<=16383; end
      else begin previous_min<=out_min; previous_max<=out_max; end
    wire limits_changed=(out_min!=previous_min)||(out_max!=previous_max);
    wire test_path=active && ((|control)||limits_changed);
    assign injection_clipped=active && (sum<$signed({1'b0,out_min}) || sum>$signed({1'b0,high}));
    assign output_code=!test_path ? control_code :
                       sum<$signed({1'b0,out_min}) ? out_min :
                       sum>$signed({1'b0,high}) ? high : sum[13:0];
    wire [63:0] d1=int3-comb1;
    wire [63:0] d2=d1-comb2;
    wire [63:0] d3=d2-comb3;
    wire [15:0] ratio=16'd1<<decim_log;
    wire [5:0] shift=decim_log*3;
    wire [63:0] scaled=comb_result>>shift;
    wire start=we && addr==6'h34 && wdata[0];
    wire abort_capture=we && addr==6'h34 && wdata[1];
    always @(posedge clk) begin
      if (!rst_n) begin
        control<=0;amplitude<=0;hold_code<=8192;phase_inc<=0;phase<=0;
        decim_log<=0;rd_index<=0;wr_index<=0;busy<=0;done<=0;
        clip_seen<=0;fault_seen<=0;sequence_id<=0;count<=0;
        int1<=0;int2<=0;int3<=0;comb1<=0;comb2<=0;comb3<=0;
        decim_count<=0;warmup<=0;comb_result<=0;filter_ready<=0;
      end else begin
        if (we) case(addr)
          6'h30: begin
            control<=wdata[1:0];
            if(wdata[1]&&!control[1]) hold_code<=control_code;
            if(wdata[2]) begin clip_seen<=0;fault_seen<=0;end
            if(!wdata[0]) phase<=0;
          end
          6'h31: amplitude<=wdata>16383?14'd16383:wdata[13:0];
          6'h32: phase_inc[15:0]<=wdata;
          6'h33: phase_inc[31:16]<=wdata;
          6'h35: if(!busy) decim_log<=wdata>14?4'd14:wdata[3:0];
          6'h37: rd_index<=wdata[AW-1:0];
          default: begin end
        endcase
        if(sample_valid && control[0] && active) phase<=phase+phase_inc;
        if(injection_clipped) clip_seen<=1;
        if(fault && busy) fault_seen<=1;
        filter_ready<=0;
        if(start) begin
          busy<=1;done<=0;wr_index<=0;count<=0;
          sequence_id<=sequence_id+1'b1;
          int1<=0;int2<=0;int3<=0;comb1<=0;comb2<=0;comb3<=0;
          decim_count<=0;warmup<=0;filter_ready<=0;
          clip_seen<=0;fault_seen<=0;
        end else if(abort_capture) begin busy<=0;done<=0;end
        else if(busy) begin
          if(sample_valid) begin
            int1<=int1+{48'd0,sample,2'b00};
            int2<=int2+int1;int3<=int3+int2;
            if(decim_count==ratio-1'b1) begin
              decim_count<=0;
              comb1<=int3;comb2<=d1;comb3<=d2;
              comb_result<=d3;
              if(warmup<5) warmup<=warmup+1'b1;
              else filter_ready<=1;
            end else decim_count<=decim_count+1'b1;
          end
          if(filter_ready) begin
            count<=count+1'b1;
            if(wr_index=={AW{1'b1}}) begin busy<=0;done<=1;end
            else wr_index<=wr_index+1'b1;
          end
        end
      end
    end
    // Keep RAM write separate from asynchronous reset logic for BRAM inference.
    always @(posedge clk)
      if(busy && filter_ready && !start && !abort_capture)
        memory[wr_index]<=scaled[15:0];
    always @(*) begin
      case(addr)
        6'h30:rdata={14'd0,control};
        6'h31:rdata={2'd0,amplitude};
        6'h32:rdata=phase_inc[15:0];
        6'h33:rdata=phase_inc[31:16];
        6'h34:rdata=16'hF510; // nominal +/-5 V DAC output guard; capture writes unchanged
        6'h35:rdata={12'd0,decim_log};
        6'h36:rdata={12'd0,fault_seen,clip_seen,done,busy};
        6'h37:rdata=rd_index;
        6'h3B:rdata=rd_data;
        6'h3C:rdata={2'd0,hold_code};
        6'h3D:rdata=sequence_id;
        6'h3E:rdata=count;
        6'h3F:rdata=16'hA624;
        default:rdata=0;
      endcase
    end
endmodule
