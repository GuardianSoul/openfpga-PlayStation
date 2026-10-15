// SPDX-License-Identifier: MIT
// Boot trace for hardware bring-up, saved by the Pocket as a nonvolatile
// data slot when the core quits (the Pocket reads the slot's bridge range).
// 16 KiB, 4096 little-endian 32-bit words:
//   0..7      the debug overlay's eight words (status, counters)
//   8         ring write index (next entry, 0..2047)
//   9         total main-RAM requests traced
//   10        runs recorded
//   11..15    zero
//   16..2047  the first 2032 runs after reset
//   2048..4095 a ring of the most recent 2048 runs
// A run is consecutive requests of one kind, each 1-16 bytes above the last
// (instruction streams, cache fills, block copies and clears).
// Entry: {length[5:0] (saturating at 63), write, first ram_Adr[24:0]}.
//   4096..6143 data log, 1024 pairs {write, 6'b0, ram_Adr[24:0]}, data:
//             the first nonzero main-RAM writes and main-RAM read
//             completions below 2 MiB, in order, to compare written and
//             read-back values.
//   6144..7167 BIOS read log, 512 pairs {6'b0, 1'b0, ram_Adr[24:0]}, data:
//             the first 512 read completions from BIOS offsets 0x10000-
//             0x1ffff (SDRAM 0x810000-), the source of the kernel copy, to
//             check each copied word's read against the file.
// The bridge returns each word one read late: word n of the saved file is
// word n-1 here (scripts/decode_boot_log.py accounts for it).
`default_nettype none
module psx_boot_trace #(parameter integer ROM_LOG=1, parameter integer CACHE_LOG=0) (
    input wire clk, reset,
    input wire request, write,
    input wire [24:0] address,
    input wire [5:0] frame,
    input wire [31:0] write_data, read_data_in,
    input wire read_done,
    input wire [255:0] header,
    // CACHE_LOG: the ROM-log words instead record the first 512 CPU cache
    // fill writes ({lane mask, request address, data}) for requests below
    // 0x10000 (kernel), on cache_clk (the main adapter's cache strobes).
    input wire cache_clk,
    input wire [3:0] cache_wr,
    input wire [31:0] cache_data,
    input wire read_clk,
    input wire [12:0] read_index,
    output reg [31:0] read_data
);
    (* ramstyle="M10K" *) reg [31:0] first [0:2047];
    (* ramstyle="M10K" *) reg [31:0] recent [0:2047];
    (* ramstyle="M10K" *) reg [63:0] data_log [0:1023];
    reg [10:0] data_count=0;
    (* ramstyle="M10K" *) reg [63:0] rom_log [0:511];
    reg [9:0] rom_count=0;
    reg [24:0] read_address=0;
    wire log_write=request && write && write_data!=0 && address<25'h200000;
    wire log_read=read_done && read_address<25'h200000;
    always @(posedge clk) begin
        if (request && !write) read_address<=address;
        if (reset) data_count<=0;
        else if ((log_write || log_read) && !data_count[10]) begin
            data_log[data_count[9:0]]<=log_write ? {1'b1, 6'b0, address, write_data} : {1'b0, 6'b0, read_address, read_data_in};
            data_count<=data_count+1'b1;
        end
    end
    // One clock writes the ROM log: clk (BIOS window) or cache_clk (fills).
    wire log_clk;
    generate if (CACHE_LOG!=0) assign log_clk=cache_clk; else assign log_clk=clk; endgenerate
    wire rom_event=CACHE_LOG!=0 ? cache_wr!=0 && read_address<25'h10000
                                : ROM_LOG!=0 && read_done && read_address[24:16]==9'h081;
    wire [63:0] rom_entry=CACHE_LOG!=0 ? {cache_wr, 3'b0, read_address, cache_data} : {7'b0, read_address, read_data_in};
    always @(posedge log_clk) begin
        if (reset && CACHE_LOG==0) rom_count<=0;
        else if (rom_event && !rom_count[9]) begin
            rom_log[rom_count[8:0]]<=rom_entry;
            rom_count<=rom_count+1'b1;
        end
    end
    reg [31:0] total=0, runs=0;
    reg [10:0] ring=0, slot_ring=0;
    reg [10:0] slot_first=0;
    reg in_run=0, run_write=0, slot_first_ok=0;
    reg [24:0] run_start=0, run_end=0;
    reg [5:0] run_length=0;
    wire [24:0] step=address-run_end;
    wire extend=in_run && write==run_write && address>run_end && step<=25'd16;
    wire [5:0] length=extend ? (run_length==6'd63 ? 6'd63 : run_length+1'b1) : 6'd1;
    wire [31:0] entry={length, write, extend ? run_start : address};
    wire [10:0] first_slot=extend ? slot_first : 11'd16+runs[10:0];
    wire first_ok=extend ? slot_first_ok : runs<32'd2032;
    wire [10:0] recent_slot=extend ? slot_ring : ring;
    always @(posedge clk) begin
        if (reset) begin total<=0; runs<=0; ring<=0; in_run<=0; end
        else if (request) begin
            total<=total+1'b1;
            if (first_ok) first[first_slot]<=entry;
            recent[recent_slot]<=entry;
            in_run<=1; run_write<=write; run_end<=address; run_length<=length;
            if (!extend) begin
                run_start<=address; runs<=runs+1'b1; ring<=ring+1'b1;
                slot_first<=first_slot; slot_first_ok<=first_ok; slot_ring<=ring;
            end
        end
    end
    // Header words are display/log samples of slowly changing counters.
    (* async_reg="true" *) reg [31:0] total_meta=0, ring_meta=0, runs_meta=0;
    reg [31:0] stored_first=0, stored_recent=0;
    reg [63:0] stored_data=0, stored_rom=0;
    reg [12:0] index_d=0;
    always @(posedge read_clk) begin
        total_meta<=total; ring_meta<={21'b0,ring}; runs_meta<=runs;
        stored_first<=first[read_index[10:0]]; stored_recent<=recent[read_index[10:0]]; index_d<=read_index;
        stored_data<=data_log[read_index[10:1]]; stored_rom<=rom_log[read_index[9:1]];
    end
    always @* begin
        if (index_d[12] && index_d[11]) read_data=index_d[0] ? stored_rom[31:0] : stored_rom[63:32];
        else if (index_d[12]) read_data=index_d[0] ? stored_data[31:0] : stored_data[63:32];
        else if (index_d<13'd8) read_data=header[index_d[2:0]*32 +: 32];
        else if (index_d==13'd8) read_data=ring_meta;
        else if (index_d==13'd9) read_data=total_meta;
        else if (index_d==13'd10) read_data=runs_meta;
        else if (index_d<13'd16) read_data=0;
        else read_data=index_d[11] ? stored_recent : stored_first;
    end
endmodule
`default_nettype wire
