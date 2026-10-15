// SPDX-License-Identifier: GPL-2.0-only
// CPU-synchronous variant of psx_mister_main_adapter (same ports and
// behaviour). clk1x and clk3x come from one PLL with aligned edges (3:1), so
// the 1x<->3x mailbox toggles are sampled directly instead of through
// two-flop synchronizers (timing analysis covers the related clocks), and the
// packet port runs on clk3x itself: the memory backend is clocked from clk3x,
// so psx_mem_cdc and its two crossings are gone. mem_clk is unused.
// STREAM_CACHE=1: cache-line words are written to the CPU cache as the
// backend's fill stream delivers them (p_fill_*, in packet order) instead of
// in four CACHE cycles after the response, and the final response is
// returned on the edge it arrives (no CACHE_DRAIN/RETURN states).
// POST_WRITES=1: a CPU scalar write is acknowledged to the 1x side when the
// 3x side takes it, then performed like a posted DMA-FIFO write. The 3x side
// is serial, so any later request still sees the write in memory.
module psx_mister_main_sync #(parameter integer STREAM_CACHE=0, parameter integer POST_WRITES=0) (
    input wire clk1x,clk3x,mem_clk,reset_async,core_ce,discard_returns,
    input wire ram_ena,ram_rnw,ram_dma,ram_cache,
    input wire [24:0] ram_adr,input wire [1:0] ram_cntDMA,
    input wire [31:0] ram_dataWrite,input wire [3:0] ram_be,
    output wire ram_done,output reg [31:0] ram_dataRead32,
    output wire dma_wr,output wire [31:0] dma_data,output wire dma_reqprocessed,
    output reg [3:0] cache_wr,output reg [31:0] cache_data,output reg [7:0] cache_addr,
    input wire [22:0] ram_dmafifo_adr,input wire [31:0] ram_dmafifo_data,
    input wire ram_dmafifo_empty,output reg ram_dmafifo_read,
    output wire source_idle,output wire service_idle,output wire fault,
    output wire p_valid,p_write,input wire p_ready,
    output wire [24:0] p_addr,output wire [3:0] p_halfwords,
    output wire [127:0] p_wdata,output wire [15:0] p_byte_en,
    input wire p_rsp_valid,p_rsp_error,input wire [127:0] p_rsp_rdata,
    output wire p_rsp_ready,
    input wire p_fill_valid,input wire [31:0] p_fill_data,   // STREAM_CACHE only
    output wire [3:0] debug        // {3x service state, request pending}
);
    (* async_reg="true" *) reg [2:0] reset1,reset3;
    always @(posedge clk1x or posedge reset_async)
        if(reset_async) reset1<=7; else reset1<={reset1[1:0],1'b0};
    always @(posedge clk3x or posedge reset_async)
        if(reset_async) reset3<=7; else reset3<={reset3[1:0],1'b0};

    // One pending source mailbox in addition to the active 3x transaction.
    // ACK is issued only when 3x takes ownership, allowing one next DMA request.
    reg request_toggle,request_ack,request_ack_seen;
    reg [65:0] request_payload; // dma,cache,rnw,count,byte address,data,mask
    wire [1:0] request_sync,request_ack_sync;
    reg response_toggle,response_ack;
    // dma,count,error,zero. The read data is not copied: the 1x side reads
    // read_data (3x) directly, which holds still until the response is
    // acknowledged (3x waits for that ack before taking any new work).
    reg [4:0] response_payload;
    wire [1:0] response_sync,response_ack_sync;
    // Direct samples (same-PLL clocks); [1] is the value the original read
    // after its synchronizer.
    assign request_sync={2{request_toggle}}, request_ack_sync={2{request_ack}};
    assign response_sync={2{response_toggle}}, response_ack_sync={2{response_ack}};
    reg source_fault,service_fault,dma_ack_pending;
    localparam R_IDLE=0,R_SCALAR=1,R_DMA=2;
    reg [1:0] return_state,return_index,return_count;
    reg return_zero;
    reg [127:0] read_data;
    assign ram_done=!reset1[2] && return_state==R_SCALAR && core_ce && !discard_returns;
    assign dma_wr=!reset1[2] && return_state==R_DMA && core_ce && !discard_returns;
    assign dma_data=return_zero ? 32'b0:read_data[return_index*32+:32];
    assign dma_reqprocessed=!reset1[2] && dma_ack_pending && core_ce && !discard_returns;
    assign source_idle=!reset1[2] && request_toggle==request_ack_sync[1] &&
        response_sync[1]==response_ack && return_state==R_IDLE && !dma_ack_pending;
    assign fault=source_fault || service_fault;

    always @(posedge clk1x or posedge reset_async) begin
        if(reset_async) begin
            request_toggle<=0;request_payload<=0;request_ack_seen<=0;
            response_ack<=0;return_state<=R_IDLE;
            return_index<=0;return_count<=0;return_zero<=0;
            ram_dataRead32<=0;dma_ack_pending<=0;source_fault<=0;
        end else if(reset1[2]) begin
            request_toggle<=0;request_payload<=0;request_ack_seen<=0;
            response_ack<=0;return_state<=R_IDLE;
            return_index<=0;return_count<=0;return_zero<=0;
            ram_dataRead32<=0;dma_ack_pending<=0;source_fault<=0;
        end else begin
            if(core_ce || discard_returns) dma_ack_pending<=0;
            // The payload remains immutable until acknowledgement crosses back.
            if(core_ce && ram_ena) begin
                if(request_toggle!=request_ack_sync[1]) source_fault<=1;
                else begin
                    request_payload<={ram_dma,ram_cache,ram_rnw,ram_cntDMA,ram_adr,ram_dataWrite,ram_be};
                    request_toggle<=!request_toggle;
                end
            end
            request_ack_seen<=request_ack_sync[1];
            if(request_ack_sync[1]!=request_ack_seen && request_payload[65] && !discard_returns) dma_ack_pending<=1;
            case(return_state)
                R_IDLE: if(response_sync[1]!=response_ack) begin
                    return_zero<=response_payload[0];
                    return_count<=response_payload[3:2];return_index<=0;
                    ram_dataRead32<=response_payload[0] ? 32'b0:read_data[31:0];
                    return_state<=response_payload[4] ? R_DMA:R_SCALAR;
                    if(response_payload[1]) source_fault<=1;
                end
                R_SCALAR,R_DMA: if(discard_returns || core_ce) begin
                    if(discard_returns || return_state==R_SCALAR || return_index==return_count) begin
                        response_ack<=response_sync[1];return_state<=R_IDLE;
                    end else return_index<=return_index+1'b1;
                end
                default: return_state<=R_IDLE;
            endcase
        end
    end

    localparam IDLE=0,ISSUE=1,WAIT=2,CACHE=3,CACHE_DRAIN=4,RETURN=5;
    reg [2:0] state;
    reg write,cache,dma,posted,error,early_acked;
    reg [1:0] dma_count,cache_index;
    reg [3:0] words_left,words_done,packet_words;
    reg [24:0] byte_address;
    reg [31:0] write_data;
    reg [3:0] write_mask,cache_mask;
    wire s_ready,s_rsp_valid,s_rsp_error,src_reset,dst_reset;
    wire [127:0] s_rsp_rdata;
    // Stock SDRAM increments only column bits, wrapping within a 1-KiB row.
    // Normal upstream DMA count selection avoids crossing it; preserve even
    // that boundary behavior here rather than silently incrementing the row.
    wire [8:0] row_words=9'd256-{1'b0,byte_address[9:2]};
    wire [3:0] next_words=row_words<{5'b0,words_left} ? row_words[3:0]:words_left;
    wire [24:0] physical_address=byte_address[24:23]==2'b01 ?
        25'h840000+{7'b0,byte_address[18:1]} : {5'b0,byte_address[20:1]};
    wire bad_address=byte_address[24:23]>2'b01 || byte_address[1:0]!=0;
    wire normalized_valid=state==ISSUE && !bad_address;
    assign service_idle=!reset3[2] && state==IDLE && request_sync[1]==request_ack &&
        response_toggle==response_ack_sync[1] && ram_dmafifo_empty && !ram_dmafifo_read;
    // (No crossing buffer: in IDLE nothing is in flight, and the backend's
    // ready only rises during a handshake.)
    assign debug={state, request_sync[1]!=request_ack};
    // Packet port on clk3x: no crossing.
    assign src_reset=reset3[2], dst_reset=reset3[2];
    assign p_valid=normalized_valid, p_write=write, s_ready=p_ready;
    assign p_addr=physical_address, p_halfwords={next_words[2:0],1'b0};
    assign p_wdata={96'b0,write_data}, p_byte_en={12'b0,write_mask};
    assign s_rsp_valid=p_rsp_valid, p_rsp_ready=state==WAIT;
    assign s_rsp_rdata=p_rsp_rdata, s_rsp_error=p_rsp_error;

    always @(posedge clk3x or posedge reset_async) begin
        if(reset_async) begin
            request_ack<=0;response_toggle<=0;early_acked<=0;
            response_payload<=0;state<=IDLE;write<=0;cache<=0;dma<=0;posted<=0;error<=0;
            dma_count<=0;cache_index<=0;words_left<=0;words_done<=0;packet_words<=0;
            byte_address<=0;write_data<=0;write_mask<=0;cache_mask<=0;read_data<=0;
            cache_wr<=0;cache_data<=0;cache_addr<=0;ram_dmafifo_read<=0;service_fault<=0;
        end else if(reset3[2]) begin
            request_ack<=0;response_toggle<=0;early_acked<=0;
            response_payload<=0;state<=IDLE;write<=0;cache<=0;dma<=0;posted<=0;error<=0;
            dma_count<=0;cache_index<=0;words_left<=0;words_done<=0;packet_words<=0;
            byte_address<=0;write_data<=0;write_mask<=0;cache_mask<=0;read_data<=0;
            cache_wr<=0;cache_data<=0;cache_addr<=0;ram_dmafifo_read<=0;service_fault<=0;
        end else begin
            cache_wr<=0;ram_dmafifo_read<=0;
            case(state)
                IDLE: begin
                    error<=0;words_done<=0;early_acked<=0;
                    if(response_toggle==response_ack_sync[1] && !src_reset) begin
                        // Preserve upstream posted-write priority over CPU/DMA reads.
                        if(!ram_dmafifo_empty) begin
                            posted<=1;write<=1;cache<=0;dma<=0;words_left<=1;
                            byte_address<={4'b0,ram_dmafifo_adr[20:0]};
                            write_data<=ram_dmafifo_data;write_mask<=4'hf;
                            ram_dmafifo_read<=1;state<=ISSUE;
                        end else if(request_sync[1]!=request_ack) begin
                            // Scalar write (rnw=0, no DMA/cache): acknowledge now.
                            posted<=POST_WRITES!=0 && !request_payload[63] && !request_payload[65] && !request_payload[64];
                            if(POST_WRITES!=0 && !request_payload[63] && !request_payload[65] && !request_payload[64]) begin
                                early_acked<=1;
                                response_payload<={1'b0,request_payload[62:61],1'b0,1'b1};
                                response_toggle<=!response_toggle;
                            end
                            request_ack<=request_sync[1];
                            {dma,cache,write,dma_count,byte_address,write_data,write_mask}<=request_payload;
                            write<=!request_payload[63];
                            words_left<=request_payload[64] ? 4'd4 :
                                request_payload[65] ? {2'b0,request_payload[62:61]}+4'd1:4'd1;
                            cache_addr<=request_payload[47:40];
                            cache_mask<=4'b0001<<request_payload[39:38];cache_index<=0;
                            state<=ISSUE;
                        end
                    end
                end
                ISSUE: if(bad_address) begin
                    // An early-acknowledged write already returned its response.
                    error<=1;service_fault<=1;state<=early_acked ? IDLE:RETURN;
                end else if(s_ready) begin packet_words<=next_words;state<=WAIT;end
                WAIT: begin
                    if(STREAM_CACHE!=0 && cache && !write && p_fill_valid) begin
                        cache_data<=p_fill_data;cache_wr<=cache_mask;
                        cache_mask<=cache_mask<<1;cache_index<=cache_index+1'b1;
                    end
                    if(s_rsp_valid) begin
                    if(s_rsp_error) begin error<=1;service_fault<=1;end
                    for(integer i=0;i<4;i++) if(i<packet_words)
                        read_data[(words_done+i)*32+:32]<=s_rsp_error ? 32'b0:s_rsp_rdata[i*32+:32];
                    if(words_left>packet_words) begin
                        words_left<=words_left-packet_words;words_done<=words_done+packet_words;
                        byte_address<={byte_address[24:10],10'b0};state<=ISSUE;
                    end else if(posted) state<=IDLE;
                    else if(STREAM_CACHE!=0) begin
                        // read_data is written on this same edge; the 1x side
                        // samples it at a later edge. The last streamed cache
                        // strobe was registered at least one edge earlier.
                        response_payload<={dma,dma_count,error || s_rsp_error,write || error || s_rsp_error};
                        response_toggle<=!response_toggle;state<=IDLE;
                    end
                    else state<=cache && !write && !error && !s_rsp_error ? CACHE:RETURN;
                    end
                end
                CACHE: begin
                    cache_data<=read_data[cache_index*32+:32];cache_wr<=cache_mask;
                    cache_mask<=cache_mask<<1;cache_index<=cache_index+1'b1;
                    if(cache_index==3) state<=CACHE_DRAIN;
                end
                // The last cache strobe is consumed on this edge before return.
                CACHE_DRAIN: state<=RETURN;
                RETURN: begin
                    response_payload<={dma,dma_count,error,write || error};
                    response_toggle<=!response_toggle;state<=IDLE;
                end
                default: begin state<=IDLE;service_fault<=1;end
            endcase
        end
    end
endmodule
