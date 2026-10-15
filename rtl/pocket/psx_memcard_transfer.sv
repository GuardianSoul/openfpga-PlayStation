// SPDX-License-Identifier: MIT
// Host-clock card image transfer engine. Packet addresses are LOGICAL DDR3
// halfword addresses; the backing fabric chooses SDRAM/PSRAM. No HPS copies.
// freeze_ack must mean guest sources, CDC queues, responses AND physical pins
// have drained. It must stay asserted while freeze is high, independent of CE.
// reset is the common transport reset, never the guest's APF reset alone.
module psx_memcard_transfer #(
    parameter [15:0] CARD1_SLOT=16'h10, CARD2_SLOT=16'h11,
    parameter [31:0] CARD1_BRIDGE=32'h21000000, CARD2_BRIDGE=32'h22000000,
    parameter [31:0] STAGING_BRIDGE=32'h23000000,
    parameter EXTERNAL_INITIALIZATION=0, TARGET_BACKPRESSURE=0
) (
    input wire clk, reset, memory_ready,
    input wire slot_request, input wire [15:0] slot_id,
    input wire [31:0] slot_size, output wire slot_ack, slot_ok,
    input wire allcomplete, bridge_wr,
    input wire [31:0] bridge_addr, bridge_data,
    output reg [31:0] staging_data=0, output wire staging_selected,
    input wire flush_request, input wire flush_card,
    input wire [1:0] guest_dirty,
    output reg [1:0] image_valid=0, dirty=0,
    output wire freeze, input wire freeze_ack,
    output wire busy, output reg failed=0, flush_done=0,
    output wire req_valid, req_write, input wire req_ready,
    output wire [24:0] req_addr, output wire [3:0] req_halfwords,
    output wire [127:0] req_data, output wire [15:0] req_be,
    input wire rsp_valid, rsp_error, input wire [127:0] rsp_data,
    output wire rsp_ready,
    output reg target_write=0, output wire [15:0] target_id,
    output wire [31:0] target_offset, target_bridge, target_length,
    input wire target_done, input wire [2:0] target_error,
    // Enabled only by the service wrapper. Commit follows complete physical
    // image initialization AND APF size-table registration, while owned.
    input wire initialization_commit, input wire [1:0] initialized_images,
    input wire target_ready
);
    localparam IDLE=0, FREEZE_LOAD=1, ACK_LOAD=2, LOAD=3,
        FREEZE_SAVE=4, READ_ISSUE=5, READ_WAIT=6, PUBLISH=7,
        WAIT_COMMAND=8, NEXT_CHUNK=9, ERROR_HOLD=10;
    localparam MEM_IDLE=0, MEM_ISSUE=1, MEM_WAIT=2;
    reg [3:0] state=IDLE;
    reg [1:0] write_state=MEM_IDLE;
    reg card=0, good_size=0, prev_write=0, prev_flush=0;
    reg complete_seen=0, clear_fifo=0, pop=0, done_low_seen=0;
    reg pending_flush=0,pending_card=0;
    reg [15:0] received=0, committed=0;
    reg [14:0] write_index=0;
    reg [31:0] write_data=0;
    reg [7:0] chunk=0;
    reg [6:0] word_index=0;
    (* ramstyle="M10K" *) reg [31:0] staging[0:127];
    wire loading=state==ACK_LOAD || state==LOAD;
    wire selected_slot=slot_request && (slot_id==CARD1_SLOT || slot_id==CARD2_SLOT);
    wire [31:0] card_bridge=card ? CARD2_BRIDGE : CARD1_BRIDGE;
    wire card_range=bridge_addr>=card_bridge && bridge_addr<card_bridge+32'd131072;
    wire push=bridge_wr && !prev_write && card_range;
    wire fifo_empty, fifo_full;
    wire [31:0] fifo_q;
    wire accept_word=push && loading && good_size && !complete_seen && !failed &&
        !fifo_full && received<16'd32768 && bridge_addr==card_bridge+{14'b0,received,2'b0};
    scfifo #(.lpm_width(32),.lpm_numwords(256),.lpm_widthu(8),
        .lpm_showahead("ON"),.use_eab("ON"),.add_ram_output_register("OFF"),
        .overflow_checking("ON"),.underflow_checking("ON")) incoming (
        .clock(clk),.sclr(reset || clear_fifo),.data(bridge_data),
        .wrreq(accept_word),.rdreq(pop),.q(fifo_q),.empty(fifo_empty),.full(fifo_full));
    assign busy=state!=IDLE;
    assign freeze=state!=IDLE;
    assign slot_ack=selected_slot && state==ACK_LOAD;
    assign slot_ok=good_size && !failed;
    assign staging_selected=bridge_addr>=STAGING_BRIDGE && bridge_addr<STAGING_BRIDGE+512;
    wire reading=state==READ_ISSUE || state==READ_WAIT;
    wire [24:0] card_halfword=card ? 25'h0100000 : 25'h0080000;
    assign req_valid=reading ? state==READ_ISSUE : write_state==MEM_ISSUE;
    assign req_write=!reading;
    assign req_addr=card_halfword+(reading ? {9'b0,chunk,word_index,1'b0} : {9'b0,write_index,1'b0});
    assign req_halfwords=2;
    assign req_data={96'b0,write_data};
    assign req_be=16'h000f;
    assign rsp_ready=reading ? state==READ_WAIT : write_state==MEM_WAIT;
    assign target_id=card ? CARD2_SLOT : CARD1_SLOT;
    assign target_offset={15'b0,chunk,9'b0};
    assign target_bridge=STAGING_BRIDGE;
    assign target_length=512;

    always @(posedge clk) begin
        // Synchronous BRAM read. APF's address-to-data delay exceeds one clock.
        staging_data<=staging[bridge_addr[8:2]];
        prev_write<=bridge_wr;prev_flush<=flush_request;
        target_write<=0;flush_done<=0;clear_fifo<=0;pop<=0;
        if(reset) begin
            state<=IDLE;write_state<=MEM_IDLE;image_valid<=0;dirty<=0;
            failed<=0;received<=0;committed<=0;complete_seen<=0;
            prev_write<=0;prev_flush<=0;clear_fifo<=0;pop<=0;
            card<=0;good_size<=0;chunk<=0;word_index<=0;done_low_seen<=0;
            pending_flush<=0;pending_card<=0;
            target_write<=0;flush_done<=0;
        end else begin
            dirty<=dirty | guest_dirty;
            if(flush_request && !prev_flush) begin pending_flush<=1;pending_card<=flush_card;end
            if(state!=IDLE && !memory_ready) begin failed<=1;image_valid[card]<=0;state<=ERROR_HOLD;end
            if(push && !accept_word) begin failed<=1;image_valid[card]<=0;state<=ERROR_HOLD;end
            if(accept_word) received<=received+1'b1;
            if(allcomplete && loading) complete_seen<=1;
            if(loading) case(write_state)
                MEM_IDLE: if(!fifo_empty && !pop) begin
                    write_data<=fifo_q;write_index<=committed[14:0];pop<=1;write_state<=MEM_ISSUE;
                end
                MEM_ISSUE: if(req_ready) write_state<=MEM_WAIT;
                MEM_WAIT: if(rsp_valid) begin
                    if(rsp_error) begin failed<=1;image_valid[card]<=0;state<=ERROR_HOLD;end
                    committed<=committed+1'b1;write_state<=MEM_IDLE;
                end
            endcase
            case(state)
                IDLE: begin
                    // Finish ownership return before requesting it again. An
                    // old ACK cannot authorize a new load or snapshot.
                    if(selected_slot && !freeze_ack) begin
                        card<=slot_id==CARD2_SLOT;good_size<=slot_size==131072;
                        image_valid[slot_id==CARD2_SLOT]<=0;failed<=0;
                        received<=0;committed<=0;complete_seen<=0;clear_fifo<=1;
                        state<=FREEZE_LOAD;
                    end else if(pending_flush && !freeze_ack) begin
                        pending_flush<=0;flush_done<=0;failed<=0;card<=pending_card;
                        if(image_valid[pending_card]) begin chunk<=0;word_index<=0;state<=FREEZE_SAVE;end
                        else begin failed<=1;flush_done<=1;end
                    end
                end
                FREEZE_LOAD: if(freeze_ack && memory_ready) state<=ACK_LOAD;
                ACK_LOAD: if(!slot_request) begin
                    if(good_size) state<=LOAD;else begin failed<=1;state<=IDLE;end
                end
                LOAD: if(fifo_empty && !pop && write_state==MEM_IDLE) begin
                    if(received==32768 && committed==32768 && !push && !failed) begin
                        image_valid[card]<=1;dirty[card]<=guest_dirty[card];state<=IDLE;
                    end else if(complete_seen) begin failed<=1;state<=ERROR_HOLD;end
                end
                FREEZE_SAVE: if(freeze_ack && memory_ready) state<=READ_ISSUE;
                READ_ISSUE: if(req_ready) state<=READ_WAIT;
                READ_WAIT: if(rsp_valid) begin
                    if(rsp_error) begin failed<=1;flush_done<=1;image_valid[card]<=0;state<=ERROR_HOLD;end
                    else begin
                        staging[word_index]<=rsp_data[31:0];
                        if(word_index==127) state<=PUBLISH;
                        else begin word_index<=word_index+1'b1;state<=READ_ISSUE;end
                    end
                end
                PUBLISH: if(!TARGET_BACKPRESSURE || target_ready) begin
                    target_write<=1;done_low_seen<=0;state<=WAIT_COMMAND;
                end
                WAIT_COMMAND: begin
                    // done is sticky from the preceding APF command. The new
                    // command must first clear it; never reuse stale success.
                    if(!target_done) done_low_seen<=1;
                    if(done_low_seen && target_done) begin
                        if(target_error!=0) begin failed<=1;flush_done<=1;state<=IDLE;end
                        else state<=NEXT_CHUNK;
                    end
                end
                NEXT_CHUNK: if(chunk==255) begin
                    dirty[card]<=guest_dirty[card];flush_done<=1;state<=IDLE;
                end else begin chunk<=chunk+1'b1;word_index<=0;state<=READ_ISSUE;end
                ERROR_HOLD: begin
                    // Transport/short-load errors cannot safely resume a guest
                    // against a partly overwritten image. Common reset required.
                end
            endcase
            // Error priority over normal completion and next-state updates.
            if(state!=IDLE && !memory_ready || push && !accept_word ||
                rsp_valid && rsp_ready && rsp_error) begin
                failed<=1;image_valid[card]<=0;state<=ERROR_HOLD;
            end
            if(EXTERNAL_INITIALIZATION && initialization_commit) begin
                if(state==IDLE && write_state==MEM_IDLE && !selected_slot && !push && !failed &&
                    freeze_ack && memory_ready && initialized_images!=0) begin
                    // A freshly formatted image needs its first export even
                    // if the guest has not written it yet. Loaded images keep
                    // their existing validity and dirty state.
                    image_valid<=image_valid | initialized_images;
                    dirty<=dirty | guest_dirty | (initialized_images & ~image_valid);
                end else begin
                    image_valid<=0;failed<=1;state<=ERROR_HOLD;
                end
            end
        end
    end
endmodule
