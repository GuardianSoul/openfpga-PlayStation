`timescale 1ns/1ps
`default_nettype none

// Functional BL2/32-bit DFI memory model with cycle-level command checks.
// This is not an electrical model of the SDRAM chip or its DDR pin PHY.
module sdr_command_model #(
    parameter integer RL = 5,
    parameter integer T_RCD = 2, T_RP = 2, T_RAS = 4, T_WR = 2,
    parameter integer T_R2W = 3, T_W2R = 2, T_RFC = 6, T_MRD = 3
) (
    input wire clk, rst,
    input wire [1:0] ras_n, cas_n, we_n,
    input wire [25:0] address,
    input wire [3:0] bank,
    input wire cke,
    input wire wrdata_en, rddata_en,
    input wire [31:0] wrdata,
    input wire [3:0] wrdata_mask,
    output wire [31:0] rddata,
    output wire rddata_valid
);
    reg [31:0] memory [0:16383];
    reg [31:0] read_pipe [0:RL-1];
    reg [RL-1:0] valid_pipe;
    reg [3:0] opened;
    reg [12:0] rows [0:3];
    integer cycle = 0, refreshes = 0, reads = 0, writes = 0;
    integer act_at [0:3], pre_at [0:3], write_at [0:3];
    integer refresh_at, mode_at, read_at, any_write_at;
    wire [2:0] command = {ras_n[0], cas_n[0], we_n[0]};
    wire [12:0] a = address[12:0];
    wire [1:0] b = bank[1:0];
    wire [23:0] word_address = {rows[b], b, a[9:1]};

    function automatic integer slot(input reg [23:0] word_addr);
        case (word_addr[23:12])
            12'h000: slot = integer'(word_addr[11:0]);
            12'h080: slot = 4096 + integer'(word_addr[11:0]);
            12'h400: slot = 8192 + integer'(word_addr[11:0]);
            12'hFFF: slot = 12288 + integer'(word_addr[11:0]);
            default: begin slot = 0; $fatal(1, "Unexpected SDRAM address %h", word_addr); end
        endcase
    endfunction
    function automatic [23:0] physical_word(input integer index);
        case (index / 4096)
            0: physical_word = 24'(index);
            1: physical_word = 24'h080000 + 24'(index % 4096);
            2: physical_word = 24'h400000 + 24'(index % 4096);
            3: physical_word = 24'hFFF000 + 24'(index % 4096);
        endcase
    endfunction
    function automatic [7:0] initial_byte(input integer byte_address);
        initial_byte = 8'(byte_address * 17 + (byte_address >> 8) * 23 + 'h5A);
    endfunction
    initial begin
        for (integer i = 0; i < 16384; i = i + 1)
            for (integer lane = 0; lane < 4; lane = lane + 1)
                memory[i][lane*8 +: 8] = initial_byte(integer'(physical_word(i))*4+lane);
    end
    assign rddata = read_pipe[RL-1];
    assign rddata_valid = valid_pipe[RL-1];

    always @(posedge clk) begin
        if (rst) begin
            cycle = 0; opened = 0;
            refresh_at = -1000; mode_at = -1000; read_at = -1000; any_write_at = -1000;
            refreshes = 0; reads = 0; writes = 0;
            valid_pipe <= 0;
            for (integer i = 0; i < RL; i = i + 1) read_pipe[i] <= 0;
            for (integer i = 0; i < 4; i = i + 1) begin
                act_at[i] = -1000; pre_at[i] = -1000; write_at[i] = -1000; rows[i] = 0;
            end
        end else begin
            cycle = cycle + 1;
            valid_pipe <= {valid_pipe[RL-2:0], rddata_en};
            for (integer i = 1; i < RL; i = i + 1) read_pipe[i] <= read_pipe[i-1];
            if ({ras_n[1], cas_n[1], we_n[1]} !== 3'b111)
                $fatal(1, "This model requires AHEAD=0 (phase 1 NOP)");
            if (wrdata_en !== (command == 3'b100) || rddata_en !== (command == 3'b101))
                $fatal(1, "DFI data-enable/command mismatch");
            if (cke) case (command)
                3'b111: ;
                3'b011: begin // ACTIVATE
                    if (opened[b] || cycle-pre_at[b] < T_RP || cycle-refresh_at < T_RFC || cycle-mode_at < T_MRD)
                        $fatal(1, "ACTIVATE state/timing violation");
                    opened[b] = 1; rows[b] = a; act_at[b] = cycle;
                end
                3'b010: begin // PRECHARGE, one bank or all
                    for (integer i = 0; i < 4; i = i + 1) if (a[10] || i == integer'(b)) begin
                        if (opened[i] && (cycle-act_at[i] < T_RAS || cycle-write_at[i] < T_WR))
                            $fatal(1, "PRECHARGE timing violation");
                        opened[i] = 0; pre_at[i] = cycle;
                    end
                end
                3'b001: begin // AUTO REFRESH
                    if (opened != 0 || cycle-refresh_at < T_RFC)
                        $fatal(1, "REFRESH state/timing violation");
                    for (integer i = 0; i < 4; i = i + 1)
                        if (cycle-pre_at[i] < T_RP) $fatal(1, "REFRESH before precharge recovery");
                    refresh_at = cycle; refreshes = refreshes + 1;
                end
                3'b000: begin // MODE REGISTER, CL3, BL2
                    if (opened != 0 || a[2:0] != 1 || a[6:4] != 3 || cycle-refresh_at < T_RFC)
                        $fatal(1, "Invalid MODE REGISTER command");
                    mode_at = cycle;
                end
                3'b101, 3'b100: begin // READ / WRITE
                    if (!opened[b] || cycle-act_at[b] < T_RCD || a[0] || a[10] ||
                        cycle-refresh_at < T_RFC || cycle-mode_at < T_MRD)
                        $fatal(1, "READ/WRITE state/timing violation");
                    if (command == 3'b101) begin
                        if (cycle-any_write_at < T_W2R) $fatal(1, "WRITE-to-READ timing violation");
                        read_pipe[0] <= memory[slot(word_address)];
                        read_at = cycle; reads = reads + 1;
                    end else begin
                        if (cycle-read_at < T_R2W) $fatal(1, "READ-to-WRITE timing violation");
                        for (integer lane = 0; lane < 4; lane = lane + 1)
                            if (!wrdata_mask[lane]) memory[slot(word_address)][lane*8 +: 8] <= wrdata[lane*8 +: 8];
                        write_at[b] = cycle; any_write_at = cycle; writes = writes + 1;
                    end
                end
                default: $fatal(1, "Unsupported SDRAM command");
            endcase
        end
    end
endmodule
`default_nettype wire
