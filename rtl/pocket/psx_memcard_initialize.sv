// SPDX-License-Identifier: MIT
// Complete absent-card initialization after APF allcomplete. Caller supplies
// committed loaded_images, load failure, and quiescence of the load service.
// Missing cards use Claude's checked 64-bit formatted blank generator.
// Guest stays frozen until BOTH images and their APF table sizes are committed.
// All inputs/outputs use host clk; freeze_ack and memory_idle need the actual
// guest-source/CDC/arbiter/controller/pin drain contract, independent of CE.
// Common transport reset is mandatory, not the guest's reset alone.
module psx_memcard_initialize #(
    parameter [15:0] CARD1_SLOT=16'h10,CARD2_SLOT=16'h11
) (
    input wire clk,reset,start,memory_ready,loads_idle,load_failed,
    input wire [1:0] loaded_images,
    output wire freeze,input wire freeze_ack,memory_idle,
    output wire busy,output reg ready=0,failed=0,
    output wire [1:0] mounted,output reg [1:0] initialized=0,
    output wire req_valid,req_write,input wire req_ready,
    output wire [24:0] req_addr,output wire [3:0] req_halfwords,
    output wire [127:0] req_data,output wire [15:0] req_be,
    input wire rsp_valid,rsp_error,output wire rsp_ready,
    output wire size_request,size_commit,output wire [15:0] size_slot,
    output wire [31:0] size_bytes,expected_size,input wire size_done,size_failed
);
    localparam IDLE=0,DRAIN_LOADS=1,SELECT_CARD=2,ISSUE=3,WAIT_RESPONSE=4,
        DRAIN_MEMORY=5,SIZE_REQUEST=6,SIZE_WAIT=7,NEXT_CARD=8,FINISHED=9,ERROR_HOLD=10,VALIDATE_REQUEST=11,VALIDATE_WAIT=12;
    reg [3:0] state=IDLE;
    reg prev_start=0,pending_start=0,card=0;
    reg [1:0] loaded=0;
    reg [13:0] index=0;
    wire [63:0] blank_word;
    psx_memcard_blank blank(.word_index(index),.word(blank_word));
    assign busy=pending_start || (state!=IDLE && state!=FINISHED);
    assign freeze=state!=IDLE && state!=FINISHED;
    assign mounted=ready && !failed ? initialized : 2'b00;
    assign req_valid=state==ISSUE;
    assign req_write=1'b1;
    // Normalized halfword addresses: BYTE window base + 8*word_index.
    assign req_addr=(card ? 25'h0100000:25'h0080000)+{9'b0,index,2'b0};
    assign req_halfwords=4;
    assign req_data={64'b0,blank_word};
    assign req_be=16'h00ff;
    assign rsp_ready=state==WAIT_RESPONSE;
    assign size_request=state==SIZE_REQUEST || state==VALIDATE_REQUEST;
    assign size_commit=state==SIZE_REQUEST || state==SIZE_WAIT;
    assign expected_size=loaded[card] ? 131072:0;
    assign size_slot=card ? CARD2_SLOT:CARD1_SLOT;
    assign size_bytes=131072;
    always @(posedge clk) begin
        prev_start<=start;
        if(reset) begin
            state<=IDLE;prev_start<=0;pending_start<=0;card<=0;loaded<=0;index<=0;
            ready<=0;failed<=0;initialized<=0;
        end else begin
            if(start && !prev_start) begin
                pending_start<=1;ready<=0;initialized<=0;
            end
            case(state)
                IDLE,FINISHED: if(pending_start && !freeze_ack) begin
                    pending_start<=0;card<=0;index<=0;failed<=0;state<=DRAIN_LOADS;
                end
                DRAIN_LOADS: if(loads_idle && freeze_ack && memory_ready && memory_idle) begin
                    // Read valid bits AFTER the last load has physically drained.
                    loaded<=loaded_images;state<=SELECT_CARD;
                end
                SELECT_CARD: begin
                    index<=0;
                    state<=VALIDATE_REQUEST;
                end
                VALIDATE_REQUEST: state<=VALIDATE_WAIT;
                VALIDATE_WAIT: if(size_done) begin
                    if(size_failed) begin failed<=1;state<=ERROR_HOLD;end
                    else if(loaded[card]) state<=DRAIN_MEMORY;
                    else state<=ISSUE;
                end
                ISSUE: if(req_ready) state<=WAIT_RESPONSE;
                WAIT_RESPONSE: if(rsp_valid) begin
                    if(rsp_error) begin failed<=1;state<=ERROR_HOLD;end
                    else if(index==16383) state<=DRAIN_MEMORY;
                    else begin index<=index+1'b1;state<=ISSUE;end
                end
                DRAIN_MEMORY: if(memory_idle && freeze_ack) state<=SIZE_REQUEST;
                SIZE_REQUEST: state<=SIZE_WAIT;
                SIZE_WAIT: if(size_done) begin
                    if(size_failed) begin failed<=1;state<=ERROR_HOLD;end
                    else begin initialized[card]<=1;state<=NEXT_CARD;end
                end
                NEXT_CARD: if(card) begin ready<=1;state<=FINISHED;end
                    else begin card<=1;state<=SELECT_CARD;end
                ERROR_HOLD: begin end // Common reset/recovery required.
                default: begin failed<=1;state<=ERROR_HOLD;end
            endcase
            if(load_failed && state!=IDLE && state!=FINISHED ||
                !memory_ready && state!=IDLE && state!=FINISHED && state!=DRAIN_LOADS) begin
                ready<=0;failed<=1;state<=ERROR_HOLD;
            end
            if(ready && !memory_ready) begin
                ready<=0;failed<=1;initialized<=0;state<=ERROR_HOLD;
            end
            if(start && !prev_start && state!=IDLE && state!=FINISHED) begin
                ready<=0;failed<=1;initialized<=0;pending_start<=0;state<=ERROR_HOLD;
            end
        end
    end
endmodule
