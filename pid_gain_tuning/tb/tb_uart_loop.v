`timescale 1ns / 1ps
// ============================================================================
//  tb_uart_loop.v —— 串口**端到端**闭环：上位机帧进 → 读应答/数据流帧出
//
//  为什么要有它：tb_light_lock 只打印 rx_vld/reg_we，从不解析 FPGA 发出的字节；
//  tb_reg_probe 绕过串口直写寄存器。于是"FPGA→PC"这条线**从来没有被测过**，
//  第二轮的两处读通路缺陷（读地址没驱动 reg_addr、应答字节序反了）和第三轮的
//  「仲裁器把 uart_tx 的 2 拍 ready 当成 2 个字节发」都是靠这条线才看得见。
//
//  被测对象（与 light_lock_top 里的接法一致）：
//    uart_proto(内含 uart_rx) → log_arbiter → uart_tx → 线上字节
//  本 tb 自己在 tx_pin 上做一次 UART 解码，按**线上顺序**逐字节断言。
//
//  baud_div = 16 ⇒ 每 bit 16 个 sys_clk = 320 ns。
//  （原来用 4 太短：uart_rx 固定 2 拍同步 + 半位复查的开销相对位时间太大，第 8 个字节
//    会被当毛刺丢掉，是 tb 自己的问题，不是 DUT 的。真实 434 拍下这点开销可忽略。）
// ============================================================================
module tb_uart_loop;

    localparam integer BIT_NS  = 320;          // 1 bit = 16 × 20 ns（位时间 = baud_div 拍，不是 baud_div+1）
    localparam [15:0]  BAUD    = 16'd16;

    reg         sys_clk = 1'b0;
    reg         sys_rst_n = 1'b0;
    reg         rx_pin = 1'b1;
    reg         tick = 1'b0;
    reg         stream_en = 1'b0;
    reg  [15:0] log_every = 16'd0;
    reg  [15:0] snap_adc = 16'd0;

    wire        reg_we;
    wire [5:0]  reg_addr;
    wire [15:0] reg_wdata;
    wire [15:0] reg_rdata;
    wire        resp_req;
    wire [63:0] resp_data;
    wire        resp_ack;
    wire [7:0]  tx_byte;
    wire        tx_vld;
    wire        tx_pin;
    wire        tx_ready;

    integer pass_cnt = 0;
    integer fail_cnt = 0;
    integer i;

    // ---- 假寄存器堆 ---------------------------------------------------------
    reg [15:0] mem [0:63];
    integer    we_cnt = 0;
    assign reg_rdata = mem[reg_addr];

    always @(posedge sys_clk) begin
        if (reg_we) begin
            we_cnt = we_cnt + 1;
            mem[reg_addr] <= reg_wdata;                 // 假寄存器堆：真的写进去
        end
    end

    uart_proto #(.DIV_W(16)) u_proto (
        .sys_clk(sys_clk), .sys_rst_n(sys_rst_n), .baud_div(BAUD), .rx_pin(rx_pin),
        .reg_we(reg_we), .reg_addr(reg_addr), .reg_wdata(reg_wdata),
        .reg_rdata(reg_rdata),
        .resp_req(resp_req), .resp_data(resp_data), .resp_ack(resp_ack)
    );

    log_arbiter u_arb (
        .sys_clk(sys_clk), .sys_rst_n(sys_rst_n), .tick(tick),
        .stream_en(stream_en), .log_every(log_every), .snap_adc(snap_adc),
        .tx_ready(tx_ready), .resp_req(resp_req), .resp_data(resp_data),
        .tx_byte(tx_byte), .tx_vld(tx_vld), .resp_ack(resp_ack)
    );

    uart_tx #(.DIV_W(16)) u_tx (
        .sys_clk(sys_clk), .sys_rst_n(sys_rst_n), .baud_div(BAUD),
        .tx_byte(tx_byte), .tx_vld(tx_vld),
        .tx_pin(tx_pin), .tx_ready(tx_ready)
    );

    always #10 sys_clk = ~sys_clk;

    // ---- 线上收字节（在 tx_pin 上做 UART 解码）-----------------------------
    reg [7:0] wire_bytes [0:31];
    integer   wire_n   = 0;
    integer   di;
    reg [7:0] tmpb;
    localparam integer BIT_CYC = BIT_NS/20;      // 4

    // 时钟驱动的 UART 解码（与 uart_rx 同构：半位复查 + 每 bit 采样，不会漂）
    reg [2:0] dstate = 3'd0;
    reg [3:0] dcnt   = 4'd0;

    always @(posedge sys_clk) begin
        case (dstate)
            3'd0: if (tx_pin == 1'b0) begin dstate <= 3'd1; dcnt <= 4'd0; end
            3'd1: if (dcnt >= BIT_CYC/2) begin
                      if (tx_pin == 1'b0) begin dstate <= 3'd2; di <= 0; end
                      else                      dstate <= 3'd0;
                      dcnt <= 4'd0;
                  end else dcnt <= dcnt + 1'b1;
            3'd2: if (dcnt >= BIT_CYC-1) begin
                      tmpb[di] <= tx_pin;
                      dcnt <= 4'd0;
                      if (di == 7) dstate <= 3'd3;
                      else         di <= di + 1'b1;
                  end else dcnt <= dcnt + 1'b1;
            3'd3: if (dcnt >= BIT_CYC-1) begin
                      if (tx_pin !== 1'b1) $display("WARN t=%0t: 停止位不是高电平", $time);
                      wire_bytes[wire_n] <= tmpb;
                      $display("WIRE t=%0t byte[%0d]=0x%02h", $time, wire_n, tmpb);
                      wire_n <= wire_n + 1;
                      dstate <= 3'd0; dcnt <= 4'd0;
                  end else dcnt <= dcnt + 1'b1;
            default: dstate <= 3'd0;
        endcase
    end

    // 仲裁器每次拉 tx_vld 都记一笔，用于对照"发了几次"与"线上几个字节"
    always @(posedge sys_clk) if (tx_vld)
        $display("TX_PULSE t=%0t byte=0x%02h ready=%b active=%b cnt=%0d",
                 $time, tx_byte, tx_ready, u_arb.active, u_arb.cnt);

    // 应答握手与帧启动的时序
    reg arb_d = 1'b0;
    always @(posedge sys_clk) begin
        if (u_proto.rx_vld)
            $display("RXB   t=%0t byte=0x%02h idx=%0d", $time, u_proto.rx_byte, u_proto.byte_idx);
        if (resp_ack)  $display("ACK   t=%0t", $time);
        if (u_arb.active && !arb_d)
            $display("START t=%0t pending=%b", $time, u_arb.pending);
        arb_d <= u_arb.active;
    end

    // ---- 往 rx_pin 发一个字节（8N1，LSB 先出）------------------------------
    task send_byte(input [7:0] b);
        integer k;
        begin
            rx_pin = 1'b0;                        // start
            #(BIT_NS);
            for (k = 0; k < 8; k = k + 1) begin
                rx_pin = b[k];
                #(BIT_NS);
            end
            rx_pin = 1'b1;                        // stop
            #(BIT_NS);
        end
    endtask

    task send_frame(input [7:0] b0, b1, b2, b3, b4, b5, b6, b7);
        begin
            send_byte(b0); send_byte(b1); send_byte(b2); send_byte(b3);
            send_byte(b4); send_byte(b5); send_byte(b6); send_byte(b7);
        end
    endtask

    task check(input [255:0] name, input [31:0] got, input [31:0] exp);
        begin
            if (got === exp) begin
                pass_cnt = pass_cnt + 1;
                $display("PASS %0s: 0x%h", name, got);
            end else begin
                fail_cnt = fail_cnt + 1;
                $display("FAIL %0s: got 0x%h, expected 0x%h", name, got, exp);
            end
        end
    endtask

    // 等线上字节数达到 n（最多等 timeout 拍）
    task wait_bytes(input integer n, input integer timeout);
        integer t;
        begin
            t = 0;
            while (wire_n < n && t < timeout) begin
                @(negedge sys_clk); t = t + 1;
            end
            if (wire_n < n) $display("WARN: 等 %0d 字节超时，只收到 %0d 个", n, wire_n);
        end
    endtask

    initial begin : main
        $display("===== tb_uart_loop start =====");
        for (i = 0; i < 64; i = i + 1) mem[i] = 16'h0000;
        mem[6'h10] = 16'hABCD;
        wire_n = 0;

        sys_rst_n = 1'b0;
        repeat (6) @(negedge sys_clk);
        sys_rst_n = 1'b1;
        repeat (4) @(negedge sys_clk);

        // ================= [W] 写帧：C5 01 10 34 12 5C C5 C5 ==================
        send_frame(8'hC5, 8'h01, 8'h10, 8'h34, 8'h12, 8'h5C, 8'hC5, 8'hC5);
        repeat (30) @(negedge sys_clk);
        check("W mem[0x10]=0x1234", {16'd0, mem[6'h10]}, 32'h0000_1234);
        check("W reg_we 只来一次", {31'd0, (we_cnt == 1)}, 32'd1);
        check("W 写命令不发应答", {31'd0, (wire_n == 0)}, 32'd1);

        // ================= [R] 读帧：C5 02 10 00 00 5C C5 C5 ==================
        //  期望线上正好 8 字节：C5 02 10 34 12 5C C5 C5
        send_frame(8'hC5, 8'h02, 8'h10, 8'h00, 8'h00, 8'h5C, 8'hC5, 8'hC5);
        wait_bytes(8, 4000);
        repeat (20) @(negedge sys_clk);
        check("R 收到 8 字节", {31'd0, (wire_n == 8)}, 32'd1);
        if (wire_n >= 8) begin
            check("R b0=SYNC0",  {24'd0, wire_bytes[0]}, 32'hC5);
            check("R b1=CMD_R",  {24'd0, wire_bytes[1]}, 32'h02);
            check("R b2=addr10", {24'd0, wire_bytes[2]}, 32'h10);
            check("R b3=dataL",  {24'd0, wire_bytes[3]}, 32'h34);
            check("R b4=dataH",  {24'd0, wire_bytes[4]}, 32'h12);
            check("R b5=0x5C",   {24'd0, wire_bytes[5]}, 32'h5C);
            check("R b6=SYNC0",  {24'd0, wire_bytes[6]}, 32'hC5);
            check("R b7=SYNC0",  {24'd0, wire_bytes[7]}, 32'hC5);
        end

        // ================= [S] 数据流帧：C5 03 03 34 12 5C C5 C5 =============
        wire_n = 0;
        snap_adc  = 16'h1234;
        log_every = 16'd2;
        stream_en = 1'b1;
        @(negedge sys_clk); tick = 1'b1;
        @(negedge sys_clk); tick = 1'b0;
        @(negedge sys_clk); tick = 1'b1;
        @(negedge sys_clk); tick = 1'b0;
        stream_en = 1'b0;
        wait_bytes(8, 4000);
        repeat (20) @(negedge sys_clk);
        check("S 收到 8 字节", {31'd0, (wire_n == 8)}, 32'd1);
        if (wire_n >= 8) begin
            check("S b0=SYNC0",  {24'd0, wire_bytes[0]}, 32'hC5);
            check("S b1=0x03",   {24'd0, wire_bytes[1]}, 32'h03);
            check("S b2=0x03",   {24'd0, wire_bytes[2]}, 32'h03);
            check("S b3=adcL",   {24'd0, wire_bytes[3]}, 32'h34);
            check("S b4=adcH",   {24'd0, wire_bytes[4]}, 32'h12);
            check("S b5=0x5C",   {24'd0, wire_bytes[5]}, 32'h5C);
            check("S b6=SYNC0",  {24'd0, wire_bytes[6]}, 32'hC5);
            check("S b7=SYNC0",  {24'd0, wire_bytes[7]}, 32'hC5);
        end

        // ================= [P] 仲裁优先：数据流待发时来读命令 =================
        //  两帧都要完整出现（各 8 字节），且数据流在前、读应答在后。
        wire_n = 0;
        mem[6'h10] = 16'hBEEF;
        log_every = 16'd2;
        stream_en = 1'b1;
        @(negedge sys_clk); tick = 1'b1;
        @(negedge sys_clk); tick = 1'b0;
        @(negedge sys_clk); tick = 1'b1;
        @(negedge sys_clk); tick = 1'b0;
        stream_en = 1'b0;
        send_frame(8'hC5, 8'h02, 8'h10, 8'h00, 8'h00, 8'h5C, 8'hC5, 8'hC5);
        wait_bytes(16, 8000);
        repeat (30) @(negedge sys_clk);
        check("P 共 16 字节", {31'd0, (wire_n == 16)}, 32'd1);
        if (wire_n >= 16) begin
            check("P 第1帧是数据流", {24'd0, wire_bytes[1]}, 32'h03);
            check("P 第2帧是读应答", {24'd0, wire_bytes[9]}, 32'h02);
            check("P 读回应答 addr", {24'd0, wire_bytes[10]}, 32'h10);
            check("P 读回应答 dataL", {24'd0, wire_bytes[11]}, 32'hEF);
            check("P 读回应答 dataH", {24'd0, wire_bytes[12]}, 32'hBE);
        end

        // ================= [T] 尾字节错 ⇒ 整帧丢弃（不得写寄存器）============
        we_cnt = 0;
        mem[6'h10] = 16'h0001;
        send_frame(8'hC5, 8'h01, 8'h10, 8'h99, 8'h88, 8'h5C, 8'h00, 8'hC5);
        repeat (40) @(negedge sys_clk);
        check("T 尾字节错不写寄存器", {31'd0, (mem[6'h10] == 16'h0001)}, 32'd1);

        $display("===== tb_uart_loop 结果：%0d 项通过，%0d 项失败 =====", pass_cnt, fail_cnt);
        if (fail_cnt == 0) $display("result: PASS");
        else               $display("result: FAIL");
        $finish;
    end

endmodule
