`timescale 1ns/1ps
`default_nettype none

// One normalized PSX/Pocket transaction onto a 32-bit ready/valid memory port.
// The frontend uses physical 16-bit word addresses and 1..8 halfword payloads.
// Address mapping, client arbitration and clock crossing are outside this block.
// PIPELINE_READS=1 issues a read's words back to back and collects the data in
// order; the memory port must then accept several outstanding reads.
module psx_mem_bridge #(parameter bit PIPELINE_READS = 0) (
    input wire clk,
    input wire rst,
    input wire req_valid,
    output wire req_ready,
    input wire req_write,
    input wire [24:0] req_addr,
    input wire [3:0] req_halfwords,
    input wire [127:0] req_wdata,
    input wire [15:0] req_byte_en,
    output wire rsp_valid,
    input wire rsp_ready,
    output reg [127:0] rsp_rdata,
    output reg rsp_error,
    output wire mem_valid,
    input wire mem_ready,
    output wire mem_write,
    output wire [23:0] mem_addr,
    output reg [31:0] mem_wdata,
    output reg [3:0] mem_byte_en,
    input wire mem_rvalid,
    input wire [31:0] mem_rdata
);
    localparam [1:0] IDLE = 0, SEND = 1, READ_WAIT = 2, RESPONSE = 3;
    reg [1:0] state;
    reg write_q, offset_q;
    reg [23:0] base_q;
    reg [3:0] halfwords_q;
    reg [127:0] data_q;
    reg [15:0] enables_q;
    reg [2:0] word_index, word_count, read_index;
    wire [25:0] last_halfword = {1'b0, req_addr} + {22'b0, req_halfwords} - 26'd1;
    wire invalid = req_halfwords == 0 || req_halfwords > 8 || last_halfword[25];
    wire [4:0] span = {1'b0, req_halfwords} + {4'b0, req_addr[0]} + 5'd1;

    assign req_ready = !rst && state == IDLE;
    assign rsp_valid = !rst && state == RESPONSE;
    assign mem_valid = !rst && state == SEND;
    assign mem_write = write_q;
    assign mem_addr = base_q + {21'b0, word_index};

    // The first transfer may start in the upper half of a 32-bit memory word.
    // Payload bytes are always relative to the first requested halfword.
    always @* begin
        mem_wdata = 0;
        mem_byte_en = 0;
        for (integer lane = 0; lane < 2; lane = lane + 1) begin : pack
            integer payload;
            payload = integer'(word_index) * 2 + lane - integer'(offset_q);
            if (payload >= 0 && payload < integer'(halfwords_q)) begin
                mem_wdata[lane*16 +: 16] = data_q[payload*16 +: 16];
                mem_byte_en[lane*2 +: 2] = enables_q[payload*2 +: 2];
            end
        end
    end

    always @(posedge clk) begin
        if (rst) begin
            state <= IDLE;
            write_q <= 0; offset_q <= 0; base_q <= 0; halfwords_q <= 0;
            data_q <= 0; enables_q <= 0; word_index <= 0; word_count <= 0; read_index <= 0;
            rsp_rdata <= 0; rsp_error <= 0;
        end else begin
            case (state)
                IDLE: if (req_valid) begin
                    write_q <= req_write;
                    offset_q <= req_addr[0]; base_q <= req_addr[24:1];
                    halfwords_q <= req_halfwords;
                    data_q <= req_wdata; enables_q <= req_byte_en;
                    word_index <= 0; word_count <= span[3:1]; read_index <= 0;
                    rsp_rdata <= 0; rsp_error <= invalid;
                    state <= invalid ? RESPONSE : SEND;
                end
                SEND: if (mem_ready) begin
                    if (word_index + 3'd1 == word_count) state <= write_q ? RESPONSE : READ_WAIT;
                    else if (!write_q && !PIPELINE_READS) state <= READ_WAIT;
                    if (word_index + 3'd1 != word_count) word_index <= word_index + 3'd1;
                end
                RESPONSE: if (rsp_ready) state <= IDLE;
                default: ;
            endcase
            // Read data arrives in issue order, possibly while later words are
            // still being sent (pipelined) or one word at a time (default).
            if (!write_q && (state == SEND || state == READ_WAIT) && mem_rvalid) begin
                for (integer lane = 0; lane < 2; lane = lane + 1) begin : unpack
                    integer payload;
                    payload = integer'(read_index) * 2 + lane - integer'(offset_q);
                    if (payload >= 0 && payload < integer'(halfwords_q))
                        rsp_rdata[payload*16 +: 16] <= mem_rdata[lane*16 +: 16];
                end
                read_index <= read_index + 3'd1;
                if (read_index + 3'd1 == word_count) state <= RESPONSE;
                else if (!PIPELINE_READS) state <= SEND;
            end
        end
    end
endmodule
`default_nettype wire
