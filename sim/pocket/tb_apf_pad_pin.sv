`timescale 1ps/1ps
// Independent pulse-width peripheral and public-port oracle for APF PAD.
module tb_apf_pad_pin #(
    parameter integer RX_SEED = 'h0fff00,
    parameter integer POLL_SEED = 'hff00,
    parameter integer HEART_SEED = 'h7ff00,
    parameter integer PHASE_PS = 1,
    parameter integer BIT_PS = 808080,
    parameter integer INPUT_FALL_PS = 0,
    parameter integer INPUT_RISE_PS = 0,
    parameter integer OUTPUT_FALL_PS = 0,
    parameter integer OUTPUT_RISE_PS = 0,
    parameter integer JITTER_PS = 0,
    parameter integer TURNAROUND_PS = 1616160,
    parameter integer HEART_TARGET = 1,
    parameter integer RESET_SECOND_REPLY = 0,
    parameter integer DROP_SECOND_REPLY = 0
);
    localparam integer CORE_PS = 13468;
    localparam integer CORE_BIT_PS = 60*CORE_PS;
    reg clk = 0, reset_n = 0;
    always #(CORE_PS/2) clk = ~clk;
    tri1 pin;
    reg pull_low = 0;
    reg peer_driver = 1'bz, peer_line = 1;
    // Transport updates keep both edges, including the fault waveforms.
    always @(pull_low)
        peer_driver <= #(pull_low ? INPUT_FALL_PS : INPUT_RISE_PS) pull_low ? 1'b0 : 1'bz;
    assign pin = peer_driver;
    always @(pin)
        peer_line <= #(pin === 1'b0 ? OUTPUT_FALL_PS : OUTPUT_RISE_PS) pin;

    wire [31:0] key [0:3], joy [0:3];
    wire [15:0] trigger [0:3];
    wire timed_out;
    io_pad_controller #(.SIM_RX_SEED(RX_SEED), .SIM_POLL_SEED(POLL_SEED), .SIM_HEART_SEED(HEART_SEED)) dut (
        .clk(clk), .reset_n(reset_n), .pad_1wire(pin), .rx_timed_out(timed_out),
        .cont1_key(key[0]), .cont2_key(key[1]), .cont3_key(key[2]), .cont4_key(key[3]),
        .cont1_joy(joy[0]), .cont2_joy(joy[1]), .cont3_joy(joy[2]), .cont4_joy(joy[3]),
        .cont1_trig(trigger[0]), .cont2_trig(trigger[1]), .cont3_trig(trigger[2]), .cont4_trig(trigger[3])
    );
    reg active = 0;
    reg [31:0] expected [0:11];
    reg [11:0] valid = 0;
    integer polls = 0, reports = 0, hearts = 0, aborts = 0, drops = 0;
    integer resets = 0, timeout_events = 0, checked_words = 0;
    integer sent_bits = 0, decoded_bits = 0;
    reg last_timeout = 0;
    time min_core_low = 64'h7fff_ffff_ffff_ffff, max_core_low = 0;
    time last_heart = 0, max_heart_gap = 0;

    function automatic [31:0] payload(input integer frame, input integer index);
        reg [31:0] w;
        begin
            w = 32'ha13bc5d7 ^ (32'h13579bdf*(index+1)) ^ (32'h02468ace*frame);
            if (index%3 == 0) w[31:28] = index == 0 ? 1 : 3;
            payload = w;
        end
    endfunction
    task automatic check_ports;
        integer i;
        reg [31:0] got, want;
        begin
            for (i=0; i<12; i=i+1) if (valid[i]) begin
                case (i%3)
                    0: got = key[i/3];
                    1: got = joy[i/3];
                    2: got = {16'b0, trigger[i/3]};
                endcase
                want = i%3 == 2 ? {16'b0, expected[i][15:0]} : expected[i];
                if (got !== want) $fatal(1, "PAD_REPORT index=%0d got=%h expected=%h", i, got, want);
            end
        end
    endtask
    always @(pin) if (active && pin === 1'bx) $fatal(1, "PAD_CONTENTION resolved pin is X");
    always @(negedge clk) if (active) begin
        if (timed_out && !last_timeout) begin
            timeout_events = timeout_events + 1;
            if (!DROP_SECOND_REPLY || timeout_events != 1) $fatal(1, "PAD_TIMEOUT unexpected receiver timeout");
            check_ports();
        end
        last_timeout = timed_out;
    end

    task automatic receive_command(output reg [31:0] value);
        integer i;
        time fall_time, low_time;
        reg bit_value;
        begin
            value = 0;
            for (i=0; i<32; i=i+1) begin
                @(negedge peer_line);
                if (peer_line !== 1'b0) $fatal(1, "PAD_COMMAND invalid falling edge");
                fall_time = $time;
                @(posedge peer_line);
                if (peer_line !== 1'b1) $fatal(1, "PAD_COMMAND invalid rising edge");
                low_time = $time-fall_time;
                bit_value = low_time < CORE_BIT_PS/2;
                if ((bit_value && (low_time < CORE_BIT_PS/3-80000 || low_time > CORE_BIT_PS/3+80000)) ||
                    (!bit_value && (low_time < 2*CORE_BIT_PS/3-80000 || low_time > 2*CORE_BIT_PS/3+80000)))
                    $fatal(1, "PAD_COMMAND invalid low width=%0d", low_time);
                if (low_time < min_core_low) min_core_low = low_time;
                if (low_time > max_core_low) max_core_low = low_time;
                value = {value[30:0], bit_value};
                decoded_bits = decoded_bits + 1;
            end
        end
    endtask
    task automatic send_report(input integer frame);
        integer word_index, b;
        time low_time;
        reg [31:0] w;
        begin : report_body
            #(TURNAROUND_PS+PHASE_PS);
            check_ports();
            for (word_index=0; word_index<12; word_index=word_index+1) begin
                w = payload(frame, word_index);
                for (b=31; b>=0; b=b-1) begin
                    if (RESET_SECOND_REPLY && frame == 2 && word_index == 0 && b == 23) begin
                        reset_n = 0;
                        pull_low = 0;
                        repeat (8) @(negedge clk);
                        reset_n = 1;
                        repeat (8) @(negedge clk);
                        check_ports();
                        aborts = aborts+1;
                        resets = resets+1;
                        disable report_body;
                    end
                    low_time = w[b] ? BIT_PS/3 : 2*BIT_PS/3;
                    if (b%2) low_time = low_time+JITTER_PS;
                    else low_time = low_time-JITTER_PS;
                    pull_low = 1;
                    #low_time;
                    pull_low = 0;
                    #(BIT_PS-low_time);
                    sent_bits = sent_bits+1;
                end
                #(8*CORE_PS);
                expected[word_index] = w;
                valid[word_index] = 1;
                check_ports();
                checked_words = checked_words+1;
            end
            reports = reports+1;
        end
    endtask

    reg [31:0] command;
    initial begin
        repeat (8) @(negedge clk);
        reset_n = 1;
        repeat (8) @(negedge clk);
        active = 1;
        while (reports < 2 || hearts < HEART_TARGET) begin
            receive_command(command);
            if (command === 32'h4a10000c) begin
                polls = polls+1;
                if (DROP_SECOND_REPLY && polls == 2) begin
                    drops = drops+1;
                    check_ports();
                end else send_report(polls);
            end else if (command === 32'h4afe0000) begin
                hearts = hearts+1;
                if (last_heart != 0) begin
                    if ($time-last_heart > max_heart_gap) max_heart_gap = $time-last_heart;
                    // Applies only between complete commands after startup.
                    if (!RESET_SECOND_REPLY && !DROP_SECOND_REPLY && $time-last_heart > 8000000000)
                        $fatal(1, "PAD_HEARTBEAT excessive gap");
                end
                last_heart = $time;
            end else $fatal(1, "PAD_COMMAND unexpected word=%h", command);
        end
        #(2*CORE_BIT_PS);
        check_ports();
        if (valid != 12'hfff || reports < 2 || checked_words != reports*12 ||
            polls != reports+aborts+drops || resets != RESET_SECOND_REPLY ||
            aborts != RESET_SECOND_REPLY || drops != DROP_SECOND_REPLY ||
            timeout_events != DROP_SECOND_REPLY || decoded_bits != 32*(polls+hearts) ||
            sent_bits != 384*reports+8*aborts)
            $fatal(1, "PAD_COVERAGE incomplete exchange");
        $display("PASS APF_PAD polls=%0d reports=%0d hearts=%0d aborts=%0d drops=%0d resets=%0d timeouts=%0d words=%0d decoded_bits=%0d sent_bits=%0d min_core_low_ps=%0d max_core_low_ps=%0d max_heart_gap_ps=%0d", polls,reports,hearts,aborts,drops,resets,timeout_events,checked_words,decoded_bits,sent_bits,min_core_low,max_core_low,max_heart_gap);
        $finish;
    end
    initial begin
        #80000000000;
        $fatal(1, "PAD_DEADLINE no complete poll/heartbeat recovery");
    end
endmodule
