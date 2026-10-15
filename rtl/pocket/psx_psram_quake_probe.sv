// SPDX-License-Identifier: MIT
// Hardware check of PocketQuake's PSRAM recipe (thinkelastic/PocketQuake,
// agg23-derived psram.sv): BCR 641F on both dies, asynchronous writes, then
// synchronous WAIT-gated burst reads of 64 halfwords. One chip (cram0).
// Each die gets 512 halfwords from 0 (crossing three 128-word rows) and 256
// at the die's end; everything is written before anything is read.
`default_nettype none
module psx_psram_quake_probe #(parameter integer T_INIT=16000) (
    input wire clk, reset,
    output reg bank_sel, write_en, sync_burst_en, config_en,
    output reg [21:0] addr,
    output reg [15:0] data_in,
    output wire [5:0] sync_burst_len,
    output wire [15:0] config_data,
    input wire read_avail, busy,
    input wire [15:0] data_out,
    output reg passed, failed,
    output reg [15:0] checked,
    output reg [22:0] failed_address,
    output reg [15:0] failed_expected, failed_actual
);
    assign sync_burst_len=6'd63;
    assign config_data=16'h641F;
    function automatic [15:0] pattern(input [22:0] a);
        pattern=16'h5ac3 ^ a[15:0] ^ {a[6:0],a[22:14]} ^ {a[22],15'b0};
    endfunction
    // Index 0..767 per die: 0..511 from 0, 512..767 from 0x3fff00.
    function automatic [21:0] at(input [9:0] i);
        at=i<10'd512 ? {12'b0,i} : 22'h3fff00+{14'b0,i[7:0]};
    endfunction
    localparam [3:0] INIT=0, CFG=1, CFG_WAIT=2, WRITE=3, WRITE_WAIT=4, READ=5, READ_DATA=6, DONE=7;
    reg [3:0] state;
    reg [15:0] timer;
    reg [9:0] index, expect_index;
    reg die, saw_busy;
    reg [5:0] remaining;
    wire [22:0] expect_addr={die,at(expect_index)};
    always @(posedge clk) begin
        write_en<=0; sync_burst_en<=0; config_en<=0;
        if (reset) begin
            state<=INIT; timer<=0; index<=0; expect_index<=0; die<=0; saw_busy<=0;
            bank_sel<=0; addr<=0; data_in<=0; passed<=0; failed<=0; checked<=0;
            failed_address<=0; failed_expected<=0; failed_actual<=0;
        end else case (state)
            INIT: begin timer<=timer+1'b1; if (timer==T_INIT[15:0]) state<=CFG; end
            CFG: if (!busy) begin bank_sel<=die; config_en<=1; saw_busy<=0; state<=CFG_WAIT; end
            CFG_WAIT: begin
                if (busy) saw_busy<=1;
                if (saw_busy && !busy) begin
                    if (die) begin die<=0; index<=0; state<=WRITE; end
                    else begin die<=1; state<=CFG; end
                end
            end
            WRITE: if (!busy) begin
                bank_sel<=die; addr<=at(index); data_in<=pattern({die,at(index)});
                write_en<=1; saw_busy<=0; state<=WRITE_WAIT;
            end
            WRITE_WAIT: begin
                if (busy) saw_busy<=1;
                if (saw_busy && !busy) begin
                    if (index==10'd767) begin
                        index<=0;
                        if (die) begin die<=0; expect_index<=0; state<=READ; end
                        else begin die<=1; state<=WRITE; end
                    end else begin index<=index+1'b1; state<=WRITE; end
                end
            end
            READ: if (!busy) begin
                bank_sel<=die; addr<=at(index); sync_burst_en<=1; remaining<=6'd63;
                saw_busy<=0; timer<=0; state<=READ_DATA;
            end
            READ_DATA: begin
                timer<=timer+1'b1;
                if (&timer) begin failed<=1; failed_address<=expect_addr; state<=DONE; end
                if (read_avail) begin
                    if (data_out!==pattern(expect_addr)) begin
                        failed<=1; failed_address<=expect_addr;
                        failed_expected<=pattern(expect_addr); failed_actual<=data_out; state<=DONE;
                    end else begin
                        checked<=checked+1'b1; expect_index<=expect_index+1'b1;
                        if (remaining==0) begin
                            if (expect_index==10'd767) begin
                                if (die) begin passed<=1; state<=DONE; end
                                else begin die<=1; index<=0; expect_index<=0; state<=READ; end
                            end else begin index<=expect_index+1'b1; state<=READ; end
                        end else remaining<=remaining-1'b1;
                    end
                end
            end
            default: ;
        endcase
    end
endmodule
`default_nettype wire
