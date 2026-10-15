// SPDX-License-Identifier: MIT
// One normalized 1..8-halfword packet to the paired, both-die MIRLO burst
// controller. All ports use clk. Physical halfword space is 32 MiB; die bit
// is address[23]. Completion includes the controller's read/write pin drain.
module psx_psram_packet (
    input wire clk,reset,initialized,
    input wire req_valid,req_write,output wire req_ready,
    input wire [24:0] req_addr,input wire [3:0] req_halfwords,
    input wire [127:0] req_wdata,input wire [15:0] req_byte_en,
    output wire rsp_valid,input wire rsp_ready,
    output reg [127:0] rsp_rdata=0,output reg rsp_error=0,
    output wire idle,output reg fault=0,
    output wire z_valid,z_we,z_die,z_last,input wire z_ready,z_rvalid,z_idle,wait_error,
    output wire [21:0] z_addr,output wire [31:0] z_wdata,
    output wire [3:0] z_wmask,input wire [31:0] z_rdata
);
    localparam IDLE=0,SEND=1,DRAIN=2,RESPONSE=3;
    reg [1:0] state=IDLE;
    reg write=0,odd=0;
    reg [22:0] first_word=0;
    reg [2:0] words=0,sent=0,received=0;
    reg [3:0] halfwords=0;
    reg [159:0] write_buffer=0,read_buffer=0;
    reg [19:0] byte_mask=0;
    wire [25:0] end_halfword={1'b0,req_addr}+{22'b0,req_halfwords};
    wire valid_packet=req_halfwords>=1 && req_halfwords<=8 &&
        !req_addr[24] && end_halfword<=26'h1000000;
    wire [22:0] current_word=first_word+{20'b0,sent};
    assign req_ready=!reset && initialized && state==IDLE;
    assign rsp_valid=!reset && state==RESPONSE;
    assign idle=!reset && initialized && state==IDLE && z_idle && !z_rvalid;
    assign z_valid=!reset && state==SEND;
    assign z_we=write;
    assign z_die=current_word[22];
    assign z_addr=current_word[21:0];
    // Explicit row termination also separates the two dies.
    assign z_last=sent+3'd1==words || current_word[6:0]==7'd127;
    assign z_wdata=write_buffer[sent*32+:32];
    assign z_wmask=byte_mask[sent*4+:4];

    always @(posedge clk) begin
        if(reset) begin
            state<=IDLE;write<=0;odd<=0;first_word<=0;words<=0;sent<=0;received<=0;
            halfwords<=0;write_buffer<=0;read_buffer<=0;byte_mask<=0;
            rsp_rdata<=0;rsp_error<=0;fault<=0;
        end else begin
            if(wait_error && (state==SEND || state==DRAIN)) begin rsp_error<=1;fault<=1;end
            case(state)
                IDLE: if(req_valid && req_ready) begin
                    rsp_error<=!valid_packet;rsp_rdata<=0;
                    if(!valid_packet) state<=RESPONSE;
                    else begin
                        state<=SEND;write<=req_write;odd<=req_addr[0];
                        first_word<=req_addr[23:1];halfwords<=req_halfwords;
                        words<=({2'b0,req_halfwords}+{5'b0,req_addr[0]}+6'd1)>>1;
                        sent<=0;received<=0;read_buffer<=0;
                        write_buffer<={32'b0,req_wdata} << (req_addr[0] ? 16:0);
                        byte_mask<=({4'b0,req_byte_en} &
                            (20'hfffff >> (20-({1'b0,req_halfwords}<<1)))) << (req_addr[0] ? 2:0);
                    end
                end
                SEND: if(z_ready) begin
                    sent<=sent+1'b1;
                    if(sent+3'd1==words) state<=DRAIN;
                end
                DRAIN: if(z_idle && !z_rvalid && (write || received==words)) begin
                    if(!write && !rsp_error && !wait_error && initialized) rsp_rdata<=(read_buffer >> (odd ? 16:0)) &
                        (128'hffffffffffffffffffffffffffffffff >> (128-({4'b0,halfwords}<<4)));
                    else rsp_rdata<=0;
                    state<=RESPONSE;
                end
                RESPONSE: if(rsp_ready) state<=IDLE;
            endcase
            if(z_rvalid) begin
                if(!write && (state==SEND || state==DRAIN) && received<words) begin
                    read_buffer[received*32+:32]<=z_rdata;received<=received+1'b1;
                end else begin fault<=1;rsp_error<=1;end
            end
            if(!initialized && state!=IDLE) begin rsp_error<=1;fault<=1;end
        end
    end
endmodule
