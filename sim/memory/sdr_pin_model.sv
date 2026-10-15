`timescale 1ns/1ps
`default_nettype none

// BL2, CL3 pin-level functional model. Delays/checks use ns. There is no reset
// pin: host reset leaves memory and chip state intact until reinitialization.
module sdr_pin_model #(
    parameter real T_AC = 2.5,
    parameter real T_AC_SECOND = T_AC,
    parameter real T_HZ = 2.5,
    parameter integer EDGE_REGIONS = 0
) (
    input wire clk, cke,
    input wire ras_n, cas_n, we_n,
    input wire [12:0] address,
    input wire [1:0] bank, dm,
    input wire host_oe,
    inout wire [15:0] dq
);
    reg [15:0] memory [0:32767 + EDGE_REGIONS*24576];
    reg [3:0] opened = 0;
    reg [12:0] row [0:3];
    realtime act_at [0:3], pre_at [0:3], write_at [0:3];
    realtime ref_at = -1000, act_any = -1000, mode_at = -1000;
    realtime last_clock = -1000;
    reg [3:0] read_valid = 0;
    reg [15:0] read_data [0:3];
    reg [1:0] read_bank [0:3];
    reg [3:0] read_second = 0;
    reg driving = 0;
    reg [15:0] out_data;
    reg second_write = 0;
    reg [24:0] second_address;
    reg [1:0] second_bank;
    integer reads = 0, writes = 0, refreshes = 0;
    wire [2:0] command = {ras_n, cas_n, we_n};
    wire [24:0] physical = {row[bank], bank, address[9:0]};
    assign dq = driving ? out_data : 16'bz;
    always @* if (host_oe && driving) $fatal(1, "SDRAM DQ output drivers overlap");

    function automatic integer slot(input reg [24:0] a);
        case (a[24:13])
            12'h000: slot = integer'(a[12:0]);
            12'h080: slot = 8192 + integer'(a[12:0]);
            12'h400: slot = 16384 + integer'(a[12:0]);
            12'hFFF: slot = 24576 + integer'(a[12:0]);
            12'h07F, 12'h0BF, 12'h41F: begin
                if (!EDGE_REGIONS) $fatal(1, "Edge fixture disabled");
                case (a[24:13])
                    12'h07F: slot = 32768 + integer'(a[12:0]);
                    12'h0BF: slot = 40960 + integer'(a[12:0]);
                    12'h41F: slot = 49152 + integer'(a[12:0]);
                endcase
            end
            default: begin slot = 0; $fatal(1, "Unexpected SDRAM pin address %h", a); end
        endcase
    endfunction
    function automatic [24:0] fixture_address(input integer index);
        case (index / 8192)
            0: fixture_address = 25'(index);
            1: fixture_address = 25'h100000 + 25'(index % 8192);
            2: fixture_address = 25'h800000 + 25'(index % 8192);
            3: fixture_address = 25'h1FFE000 + 25'(index % 8192);
            4: fixture_address = 25'h0FE000 + 25'(index % 8192);
            5: fixture_address = 25'h17E000 + 25'(index % 8192);
            6: fixture_address = 25'h83E000 + 25'(index % 8192);
        endcase
    endfunction
    function automatic [7:0] initial_byte(input integer a);
        initial_byte = 8'(a*17 + (a >> 8)*23 + 'h5A);
    endfunction
    task automatic store(input reg [24:0] a);
        begin
            if (driving || (^dq === 1'bx)) $fatal(1, "Invalid or contended write data");
            for (integer lane = 0; lane < 2; lane = lane + 1)
                if (!dm[lane]) memory[slot(a)][lane*8 +: 8] = dq[lane*8 +: 8];
        end
    endtask
    initial begin
        for (integer i = 0; i < 32768 + EDGE_REGIONS*24576; i = i + 1)
            memory[i] = {initial_byte(integer'(fixture_address(i))*2+1), initial_byte(integer'(fixture_address(i))*2)};
        for (integer b = 0; b < 4; b = b + 1) begin
            row[b] = 0; act_at[b] = -1000; pre_at[b] = -1000; write_at[b] = -1000;
        end
    end
    always @(posedge clk) begin
        if ($realtime-last_clock < 6.0) $fatal(1, "SDRAM clock is too fast for CL3");
        last_clock = $realtime;
        if (cke) begin
            if (read_valid[0]) begin
                if (read_second[0]) begin
                    out_data <= #(T_AC_SECOND) read_data[0]; driving <= #(T_AC_SECOND) 1;
                end else begin
                    out_data <= #(T_AC) read_data[0]; driving <= #(T_AC) 1;
                end
            end else driving <= #(T_HZ) 0;
            for (integer i = 0; i < 3; i = i + 1) begin
                read_valid[i] = read_valid[i+1]; read_data[i] = read_data[i+1]; read_bank[i] = read_bank[i+1];
                read_second[i] = read_second[i+1];
            end
            read_valid[3] = 0;
            if (second_write) begin
                store(second_address); write_at[second_bank] = $realtime; second_write = 0;
            end
            if (command != 3'b111 && $realtime-ref_at < 80.0)
                $fatal(1, "Pin command inside tRFC");
            case (command)
                3'b111: ;
                3'b011: begin
                    if (opened[bank] || $realtime-pre_at[bank] < 18.0 || $realtime-act_any < 12.0 ||
                        $realtime-act_at[bank] < 60.0 || $realtime-mode_at < 16.0)
                        $fatal(1, "Pin ACTIVATE state/timing violation");
                    opened[bank] = 1; row[bank] = address; act_at[bank] = $realtime; act_any = $realtime;
                end
                3'b010: begin
                    for (integer b = 0; b < 4; b = b + 1) if (address[10] || b == integer'(bank)) begin
                        if (opened[b] && ($realtime-act_at[b] < 48.0 || $realtime-write_at[b] < 15.0))
                            $fatal(1, "Pin PRECHARGE timing violation");
                        opened[b] = 0; pre_at[b] = $realtime;
                    end
                end
                3'b001: begin
                    if (opened != 0) $fatal(1, "Pin REFRESH with open bank");
                    for (integer b = 0; b < 4; b = b + 1)
                        if ($realtime-pre_at[b] < 18.0) $fatal(1, "Pin REFRESH inside tRP");
                    ref_at = $realtime; refreshes = refreshes + 1;
                end
                3'b000: begin
                    if (opened != 0 || address[2:0] != 1 || address[6:4] != 3)
                        $fatal(1, "Pin MODE REGISTER mismatch");
                    mode_at = $realtime;
                end
                3'b101, 3'b100: begin
                    if (!opened[bank] || $realtime-act_at[bank] < 18.0 || address[0] || address[10])
                        $fatal(1, "Pin READ/WRITE state/timing violation");
                    if (command == 3'b100) begin
                        store(physical); second_write = 1;
                        second_address = physical ^ 25'b1; second_bank = bank;
                        writes = writes + 1;
                    end else begin
                        read_valid[2] = 1; read_data[2] = memory[slot(physical)]; read_bank[2] = bank; read_second[2] = 0;
                        read_valid[3] = 1; read_data[3] = memory[slot(physical ^ 25'b1)]; read_bank[3] = bank; read_second[3] = 1;
                        reads = reads + 1;
                    end
                end
                default: $fatal(1, "Unknown pin command");
            endcase
        end
    end
endmodule
`default_nettype wire
