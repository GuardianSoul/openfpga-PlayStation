// SPDX-License-Identifier: MIT
// APF 0080 automatic nonvolatile readback of a whole 128 KiB card, using a
// bounded prefetch ring rather than a whole-image BRAM. All ports use host clk.
// APF captures the PREVIOUS response before bridge_rd kicks the next request.
// The caller must stop launching other APF transfers while host_suspend is high
// and drain issued commands/loads/exports before asserting services_idle.
// freeze_ack and memory_idle include source/CDC/return/controller/PIN drain.
// Common transport reset only; guest reset must not cancel this operation.
module psx_memcard_os_read #(
    parameter integer BUFFER_AW=8,
    parameter [15:0] CARD1_SLOT=16'h10,CARD2_SLOT=16'h11,
    parameter [31:0] CARD1_BRIDGE=32'h21000000,CARD2_BRIDGE=32'h22000000
) (
    input wire clk,reset,memory_ready,memory_idle,services_idle,
    input wire slot_request,input wire [15:0] slot_id,
    input wire [1:0] image_valid, input wire allcomplete,
    output wire slot_ack,slot_ok,host_suspend,freeze,
    input wire freeze_ack,
    output wire busy,output reg failed=0,done=0,
    input wire bridge_rd,input wire [31:0] bridge_addr,
    output wire bridge_selected,output reg [31:0] bridge_data=0,
    output wire req_valid,req_write,input wire req_ready,
    output wire [24:0] req_addr,output wire [3:0] req_halfwords,
    output wire [127:0] req_data,output wire [15:0] req_be,
    input wire rsp_valid,rsp_error,input wire [127:0] rsp_data,
    output wire rsp_ready
);
    localparam integer DEPTH=1<<BUFFER_AW,WORDS=32768;
    localparam IDLE=0,WAIT_FREEZE=1,PRIME=2,ACKNOWLEDGE=3,STREAM=4,
        COMPLETE=5,DRAIN=6,ERROR_HOLD=7,ACK_REJECT=8;
    localparam M_IDLE=0,M_REQUEST=1,M_WAIT=2,M_COPY=3;
    reg [3:0] state=IDLE;
    reg [1:0] memory_state=M_IDLE,copy_index=0;
    reg pending=0,card=0,pending_card=0,prev_request=0,prev_rd=0,prev_complete=0;
    reg finish_seen=0;
    reg [15:0] filled=0,consumed=0;
    (* ramstyle="M10K" *) reg [31:0] buffer_words[0:DEPTH-1];
    wire selected_slot=slot_request && (slot_id==CARD1_SLOT || slot_id==CARD2_SLOT);
    wire [31:0] card_bridge=card ? CARD2_BRIDGE:CARD1_BRIDGE;
    wire [16:0] buffered_words={1'b0,filled}-{1'b0,consumed};
    wire read_strobe=bridge_rd && !prev_rd;
    // Keep the output mapped for the final pipeline dummy read one word past
    // the image. The value itself changes only on an actual data read strobe.
    assign bridge_selected=state!=IDLE && state!=ACK_REJECT && bridge_addr[31:24]==card_bridge[31:24];
    wire consume=read_strobe && bridge_selected && state==STREAM &&
        bridge_addr==card_bridge+{14'b0,consumed,2'b0} && buffered_words!=0;
    assign busy=pending || state!=IDLE;
    assign host_suspend=pending || state!=IDLE;
    assign freeze=state!=IDLE && state!=ACK_REJECT;
    assign slot_ack=selected_slot && (state==ACKNOWLEDGE || state==ACK_REJECT || state==ERROR_HOLD);
    assign slot_ok=state==ACKNOWLEDGE && !failed;
    assign req_valid=memory_state==M_REQUEST;
    assign req_write=1'b0;
    assign req_addr=(card ? 25'h0100000:25'h0080000)+{8'b0,filled,1'b0};
    assign req_halfwords=8;
    assign req_data=0;
    assign req_be=16'hffff;
    // RSP data remains held by the backend until all four words have entered
    // the single-write-port ring. No extra 128-bit response register is needed.
    assign rsp_ready=memory_state==M_WAIT && rsp_valid && rsp_error ||
        memory_state==M_COPY && copy_index==3;
    always @(posedge clk) begin
        prev_request<=slot_request;prev_rd<=bridge_rd;prev_complete<=allcomplete;done<=0;
        if(reset) begin
            state<=IDLE;memory_state<=M_IDLE;copy_index<=0;pending<=0;card<=0;pending_card<=0;
            prev_request<=0;prev_rd<=0;prev_complete<=0;finish_seen<=0;
            filled<=0;consumed<=0;failed<=0;done<=0;bridge_data<=0;
        end else begin
            if(selected_slot && !prev_request && !pending &&
                (state==IDLE || state==COMPLETE || state==DRAIN)) begin
                pending<=1;pending_card<=slot_id==CARD2_SLOT;
            end
            case(memory_state)
                M_IDLE: if((state==PRIME || state==STREAM) && filled<WORDS && buffered_words<=DEPTH-4)
                    memory_state<=M_REQUEST;
                M_REQUEST: if(req_ready) memory_state<=M_WAIT;
                M_WAIT: if(rsp_valid) begin
                    if(rsp_error) begin memory_state<=M_IDLE;failed<=1;state<=ERROR_HOLD;end
                    else begin copy_index<=0;memory_state<=M_COPY;end
                end
                M_COPY: begin
                    buffer_words[filled[BUFFER_AW-1:0]]<=rsp_data[copy_index*32+:32];
                    filled<=filled+1'b1;
                    if(copy_index==3) memory_state<=M_IDLE;
                    else copy_index<=copy_index+1'b1;
                end
            endcase
            if(consume) begin
                bridge_data<=buffer_words[consumed[BUFFER_AW-1:0]];
                consumed<=consumed+1'b1;
                if(consumed==WORDS-1) state<=COMPLETE;
            end
            case(state)
                IDLE: if(pending && services_idle && !freeze_ack && memory_state==M_IDLE) begin
                    pending<=0;card<=pending_card;filled<=0;consumed<=0;finish_seen<=0;
                    if(image_valid[pending_card] && memory_ready) begin failed<=0;state<=WAIT_FREEZE;end
                    else begin failed<=1;state<=ACK_REJECT;end
                end
                WAIT_FREEZE: if(freeze_ack && memory_ready && memory_idle && services_idle) state<=PRIME;
                PRIME: if(filled>=DEPTH && memory_state==M_IDLE && memory_idle) state<=ACKNOWLEDGE;
                ACKNOWLEDGE: if(!slot_request) state<=STREAM;
                COMPLETE: if(finish_seen || allcomplete && !prev_complete) state<=DRAIN;
                    else if(pending && services_idle && memory_idle && memory_state==M_IDLE) begin
                        // APF may read both nonvolatile slots before its single
                        // 008F. The first file is fully delivered; retain the
                        // lease, prime the next file, and leave last data held.
                        pending<=0;card<=pending_card;filled<=0;consumed<=0;finish_seen<=0;
                        if(image_valid[pending_card] && memory_ready) begin failed<=0;state<=PRIME;end
                        else begin failed<=1;state<=ERROR_HOLD;end
                    end
                DRAIN: if(memory_state==M_IDLE && memory_idle) begin state<=IDLE;done<=1;end
                ACK_REJECT: if(!slot_request) state<=IDLE;
                ERROR_HOLD: begin end
            endcase
            if(allcomplete && !prev_complete && state!=IDLE && state!=ACK_REJECT) begin
                finish_seen<=1;
                if(consumed!=WORDS) begin failed<=1;state<=ERROR_HOLD;end
            end
            // Retain ownership on underflow/invalid accesses. APF has no per-
            // word backpressure; a timing envelope must pass before packaging.
            if(read_strobe && bridge_selected && state==STREAM && !consume) begin
                failed<=1;state<=ERROR_HOLD;bridge_data<=0;
            end
            if(state!=IDLE && state!=ACK_REJECT &&
                (!memory_ready || !image_valid[card] || state!=WAIT_FREEZE && !freeze_ack)) begin
                failed<=1;state<=ERROR_HOLD;
            end
        end
    end
`ifndef SYNTHESIS
    initial if(BUFFER_AW<7 || BUFFER_AW>10) $fatal(1,"Invalid card readback buffer size");
`endif
endmodule
