// SPDX-License-Identifier: MIT
// APF loader/readback engine. All ports use clk_74a. The memory endpoint must
// hold response data until accepted and may stall for an arbitrary duration.
module psx_bios_probe (
    input wire clk, reset,
    input wire bridge_wr,
    input wire [31:0] bridge_addr, bridge_data,
    input wire allcomplete,
    output wire req_valid, req_write,
    input wire req_ready,
    output wire [24:0] req_addr,
    output wire [127:0] req_data,
    output wire [15:0] req_be,
    input wire rsp_valid, rsp_error,
    input wire [127:0] rsp_data,
    output wire rsp_ready,
    output reg [17:0] loaded_words = 0,
    output reg [17:0] checked_words = 0,
    output reg [31:0] write_crc = 32'hffffffff,
    output reg [31:0] read_crc = 32'hffffffff,
    output reg passed = 0, failed = 0, checking = 0
);
    function automatic [31:0] crc_word(input [31:0] crc, input [31:0] data);
        reg [31:0] c; integer i;
        begin
            c = crc;
            for (i=0;i<32;i=i+1)
                c = (c >> 1) ^ ((c[0] ^ data[i]) ? 32'hedb88320 : 32'b0);
            crc_word = c;
        end
    endfunction
    wire load = bridge_wr && bridge_addr[31:19] == 0;
    reg prev_write = 0, complete_seen = 0;
    wire push = load && !prev_write;
    wire [48:0] fifo_q;
    wire fifo_empty, fifo_full;
    reg pop;
    scfifo #(.lpm_width(49), .lpm_numwords(256), .lpm_widthu(8),
        .lpm_showahead("ON"), .use_eab("ON"), .add_ram_output_register("OFF"),
        .overflow_checking("ON"), .underflow_checking("ON")) writes (
        .clock(clk), .sclr(reset), .data({bridge_addr[18:2],bridge_data}),
        .wrreq(push && !fifo_full), .rdreq(pop), .q(fifo_q),
        .empty(fifo_empty), .full(fifo_full));
    localparam IDLE=0, ISSUE_WRITE=1, WAIT_WRITE=2, ISSUE_READ=3, WAIT_READ=4, FINISHED=5;
    reg [2:0] state=IDLE;
    reg [16:0] address;
    reg [31:0] data;
    assign req_valid = state == ISSUE_WRITE || state == ISSUE_READ;
    assign req_write = state == ISSUE_WRITE;
    assign req_addr = {7'b0,address,1'b0}; // SDRAM halfword address
    assign req_data = {96'b0,data};
    assign req_be = 16'h000f;
    assign rsp_ready = state == WAIT_WRITE || state == WAIT_READ;
    always @(posedge clk) begin
        prev_write <= bridge_wr; pop <= 0;
        if (allcomplete) complete_seen <= 1;
        if (reset) begin
            state<=IDLE; loaded_words<=0; checked_words<=0;
            write_crc<=32'hffffffff; read_crc<=32'hffffffff;
            passed<=0; failed<=0; checking<=0; prev_write<=0; complete_seen<=0;
        end else begin
            if (push && (fifo_full || checking || passed)) failed<=1;
            case (state)
                IDLE: if (!fifo_empty && !pop) begin
                    address<=fifo_q[48:32]; data<=fifo_q[31:0]; pop<=1;
                    if ({1'b0,fifo_q[48:32]} != loaded_words || loaded_words==131072) failed<=1;
                    state<=ISSUE_WRITE;
                end else if (complete_seen && fifo_empty && loaded_words!=0) begin
                    if (loaded_words!=131072) begin failed<=1; state<=FINISHED; end
                    else begin checking<=1; address<=0; state<=ISSUE_READ; end
                end
                ISSUE_WRITE: if (req_ready) state<=WAIT_WRITE;
                WAIT_WRITE: if (rsp_valid) begin
                    if (rsp_error) failed<=1;
                    write_crc<=crc_word(write_crc,data); loaded_words<=loaded_words+1'b1;
                    state<=IDLE;
                end
                ISSUE_READ: if (req_ready) state<=WAIT_READ;
                WAIT_READ: if (rsp_valid) begin
                    if (rsp_error) failed<=1;
                    read_crc<=crc_word(read_crc,rsp_data[31:0]);
                    checked_words<=checked_words+1'b1;
                    if (address==131071) begin
                        passed<=!failed && !rsp_error && crc_word(read_crc,rsp_data[31:0])==write_crc;
                        if (crc_word(read_crc,rsp_data[31:0])!=write_crc) failed<=1;
                        checking<=0; state<=FINISHED;
                    end else begin address<=address+1'b1; state<=ISSUE_READ; end
                end
            endcase
        end
    end
endmodule
