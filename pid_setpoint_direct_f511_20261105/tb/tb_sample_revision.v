`timescale 1ns/1ps
module tb_sample_revision;
reg sys_clk=0; always #10 sys_clk=~sys_clk;
reg sys_rst_n=0,uart_rx=1,adc_otr=0;
reg [13:0] adc_d=0;
wire uart_tx,adc_clk;
wire [13:0] da1_data,da2_data;
integer BIT_NS=8680;
integer pass_cnt=0,fail_cnt=0,n,c1,c2,previous_valid=0;
integer ch1=9000,ch2=10000;
reg [15:0] got;
integer last_sp,last_i;
light_lock_top #(.LOSS_LOG2(4)) dut(.sys_clk(sys_clk),.sys_rst_n(sys_rst_n),
 .adc_d(adc_d),.adc_otr(adc_otr),.adc_clk(adc_clk),
 .da1_data(da1_data),.da2_data(da2_data),.uart_rx(uart_rx),.uart_tx(uart_tx));
// Independent model of multiplexed ADC pins, including the board's format.
always @(adc_clk) begin
 #2; adc_d = ((adc_clk ? ch1 : ch2) ^ 14'h3fff) ^ 14'h2000;
end
task check(input [255:0] name,input [31:0] value,input [31:0] expected);
begin
 if(value !== expected)$fatal(1,"%0s got=%0d expected=%0d",name,value,expected);
 pass_cnt=pass_cnt+1;
end
endtask
always @(posedge sys_clk) if(sys_rst_n) begin
 if(dut.adc_sample_valid && previous_valid)$fatal(1,"sample_valid wider than one clock");
 previous_valid=dut.adc_sample_valid;
end
    reg [7:0] rxbuf [0:63];
    integer   rx_wr = 0;
    integer   rx_rd = 0;

    reg [7:0] mon_byte;
    integer   mon_i;
    always begin
        @(negedge uart_tx);                 // 起始位
        #(BIT_NS * 1.5);                    // 对到 bit0 中点
        for (mon_i = 0; mon_i < 8; mon_i = mon_i + 1) begin
            mon_byte[mon_i] = uart_tx;
            #(BIT_NS);
        end
        rxbuf[rx_wr % 64] = mon_byte;
        rx_wr = rx_wr + 1;
        #(BIT_NS * 0.5);                    // 走完停止位，回到等下一个起始位
    end

    // ---- 串口发一个字节（LSB 先发，8N1）--------------------------------------
    task uart_send_byte(input [7:0] b);
        integer i;
        begin
            uart_rx = 1'b0;                 // 起始位
            #(BIT_NS);
            for (i = 0; i < 8; i = i + 1) begin
                uart_rx = b[i];
                #(BIT_NS);
            end
            uart_rx = 1'b1;                 // 停止位
            #(BIT_NS);
        end
    endtask

    // ---- 从队列取一个字节（带超时，绝不静默死等）-----------------------------
    task uart_recv_byte(output [7:0] b);
        integer guard;
        begin
            guard = 0;
            while ((rx_rd >= rx_wr) && (guard < 100000)) begin
                #(BIT_NS);
                guard = guard + 1;
            end
            if (rx_rd >= rx_wr) begin
                $display("!! 串口接收超时：期望的字节没到（已收 %0d 个）", rx_wr);
                check("串口应答应当收到字节", 32'd1, 32'd0);
                b = 8'h00;
            end else begin
                b = rxbuf[rx_rd % 64];
                rx_rd = rx_rd + 1;
            end
        end
    endtask

    // ---- 真帧写寄存器（8 字节，与 host\llctl.py 一致）------------------------
    task write_reg(input [5:0] a, input [15:0] din);
        begin
            uart_send_byte(8'hC5);
            uart_send_byte(8'h01);
            uart_send_byte({2'b00, a});
            uart_send_byte(din[7:0]);
            uart_send_byte(din[15:8]);
            uart_send_byte(8'h5C);
            uart_send_byte(8'hC5);
            uart_send_byte(8'hC5);
        end
    endtask

    task write_target_and_check(input [15:0] target);
        fork
            write_reg(6'h18, target);
            begin
                wait (dut.setpoint_r == target);
                @(posedge sys_clk);
                while (!dut.adc_sample_valid) @(posedge sys_clk);
                #1;
                check("full target on first valid ADC sample", dut.sp_active, target);
            end
        join
    endtask

    // ---- 真帧读寄存器：发读帧、收 8 字节应答、校验帧格式并取回 16 位值 --------
    task read_reg(input [5:0] a, input [255:0] nm, output [15:0] v);
        reg [7:0] b0, b1, b2, b3, b4, b5, b6, b7;
        begin
            rx_rd = rx_wr;
            uart_send_byte(8'hC5);
            uart_send_byte(8'h02);
            uart_send_byte({2'b00, a});
            uart_send_byte(8'h00);
            uart_send_byte(8'h00);
            uart_send_byte(8'h5C);
            uart_send_byte(8'hC5);
            uart_send_byte(8'hC5);
            uart_recv_byte(b0);
            uart_recv_byte(b1);
            uart_recv_byte(b2);
            uart_recv_byte(b3);
            uart_recv_byte(b4);
            uart_recv_byte(b5);
            uart_recv_byte(b6);
            uart_recv_byte(b7);
            check({nm, " 帧头 C5"}, {24'd0, b0}, 32'h000000C5);
            check({nm, " 命令=02"}, {24'd0, b1}, 32'h00000002);
            check({nm, " 地址回显"}, {26'd0, b2}, {26'd0, {2'b00, a}});
            check({nm, " 尾 5C C5 C5"}, {16'd0, b5, b6, b7}, 32'h005CC5C5);
            v = {b4, b3};
        end
    endtask


initial begin
 #100; @(negedge sys_clk);sys_rst_n=1;
 repeat(30)@(negedge sys_clk);
 check("default channel CH1",dut.feedback_ch_r,0);
 check("boot armed",dut.loop_arm_r,1);
  check("boot Kp C07",dut.kp_shift_r,35072);
  check("boot Ki C07",dut.ki_shift_r,50463);
  check("boot target runtime default",dut.setpoint_r,8476);
  check("boot bias C07",dut.bias_r,8198);
 check("boot positive action",dut.dir_pol_r,1);
 write_reg(6'h2E,0);
 c1=0;c2=0;
 repeat(1000)begin @(negedge sys_clk);if(dut.adc_valid1)c1=c1+1;if(dut.adc_valid2)c2=c2+1;end
 check("one CH1 sample per period",c1,100);check("one CH2 sample per period",c2,100);
 check("normalized CH1",dut.adc_sample1,9000);check("normalized CH2",dut.adc_sample2,10000);
 write_reg(0,2);#2000;
 check("dual passthrough CH1",da1_data,9000);check("dual passthrough CH2",da2_data,10000);
 write_reg(2,1234);write_reg(0,8);#2000;
 check("fixed output CH1",da1_data,1234);check("fixed output CH2",da2_data,1234);
 write_reg(6'h1A,16'h9000);read_reg(6'h1A,"fixed P gain",got);check("fixed P gain",got,16'h9000);
 write_reg(6'h1B,16'hb999);read_reg(6'h1B,"fixed I gain",got);check("fixed I gain",got,16'hb999);
 write_reg(6'h1A,16'h7fff);read_reg(6'h1A,"legacy P clamp",got);check("legacy P clamp",got,31);
 write_reg(6'h1B,16'h7fff);read_reg(6'h1B,"legacy I clamp",got);check("legacy I clamp",got,63);
 write_reg(6'h1A,8);write_reg(6'h1B,12);
 write_reg(6'h18,16'hffff);read_reg(6'h18,"setpoint clamp",got);check("setpoint clamp",got,16383);
 write_reg(6'h18,9000);write_reg(6'h2C,3);write_reg(6'h21,0);
 read_reg(6'h2C,"legacy slew readback",got);check("legacy slew readback",got,3);
 write_reg(0,1);check("unarmed cannot enable",dut.pi_enable,0);
 write_reg(6'h2E,1);check("wrong arm key",dut.loop_arm_r,0);
 write_reg(6'h2E,16'hA501);read_reg(6'h2E,"armed",got);check("arm readback",got,1);
 // Check full upward and downward steps after a legacy slew-register write.
 write_reg(0,16'h11);write_reg(0,1);
 write_target_and_check(12000);
 write_target_and_check(7000);
 // Changing the legacy slow tick never changes PI input strobes.
 write_reg(6'h10,16'hffff);c1=0;
 repeat(1000)begin @(negedge sys_clk);if(dut.u_pi.tick)c1=c1+1;end
 check("PI remains at ADC cadence",c1,100);
 write_reg(6'h2D,1);check("channel change disarms",dut.loop_arm_r,0);
 read_reg(3,"CH2 ADC readback",got);check("online channel CH2",got,10000);
 write_reg(6'h2E,16'hA501);write_reg(6'h20,0);
 check("direction change disarms",dut.loop_arm_r,0);
 // Full-scale and zero raw patterns are normalized exactly once.
 ch1=0;ch2=16383;#2000;
 check("normalized zero",dut.adc_sample1,0);check("normalized full scale",dut.adc_sample2,16383);
 // Loss detector operates on selected samples and clear has priority.
 write_reg(6'h21,100);write_reg(6'h2E,16'hA501);
 ch2=0;#10000;check("loss detected",dut.light_lost,1);check("freeze latched",dut.frozen,1);
 last_i=dut.pi_integ;last_sp=dut.pi_out;#10000;
 check("integral frozen",dut.pi_integ,last_i);check("output frozen",dut.pi_out,last_sp);
 write_reg(0,16'h201);check("clear priority",dut.light_lost,0);
 ch2=10000;write_reg(0,1);#10000;check("clear releases freeze",dut.frozen,0);
 write_reg(6'h2D,0);read_reg(6'h2D,"CH1 selection",got);check("CH1 restored",got,0);
 $display("REVISION_PASS tb_sample_revision checks=%0d",pass_cnt);$finish;
end
initial begin #100000000;$fatal(1,"timeout");end
endmodule
