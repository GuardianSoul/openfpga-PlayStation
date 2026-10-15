// SPDX-License-Identifier: MIT
// Release BIOS writer. BRIDGE words are already little-endian (APF
// bridge_endian_little=1). The separate diagnostic retains CRC/readback.
module psx_bios_loader #(
    parameter [15:0] BIOS_SLOT_ID=16'd0
) (
    input wire clk, reset, memory_ready,
    input wire slot_request, input wire [15:0] slot_id,
    input wire [31:0] slot_size, output wire slot_ack, slot_ok,
    input wire allcomplete, bridge_wr,
    input wire [31:0] bridge_addr, bridge_data,
    output reg freeze_toggle=0, input wire freeze_ack,
    output wire req_valid, req_write, input wire req_ready,
    output wire [24:0] req_addr, output wire [127:0] req_data,
    output wire [15:0] req_be,
    input wire rsp_valid, rsp_error, output wire rsp_ready,
    output reg [17:0] received_words=0, committed_words=0,
    output reg ready=0, failed=0, output wire busy
);
    localparam [17:0] BIOS_WORDS=18'd131072;
    localparam IDLE=0, FREEZE=1, ACK_SLOT=2, ACTIVE=3, FINISHED=4;
    localparam MEM_IDLE=0, MEM_ISSUE=1, MEM_WAIT=2;
    reg [2:0] session=IDLE;
    reg [1:0] mem_state=MEM_IDLE;
    reg prev_slot_request=0, prev_write=0;
    reg good_size=0, complete_seen=0, reject_busy=0;
    reg fifo_clear=0, pop=0;
    wire is_bios_slot=slot_request && slot_id==BIOS_SLOT_ID;
    wire begin_slot=is_bios_slot && !prev_slot_request;
    wire load_range=bridge_addr[31:19]==0;
    wire push=bridge_wr && !prev_write && load_range;
    wire accepting=(session==ACK_SLOT || session==ACTIVE) && good_size && !complete_seen;
    wire fifo_empty, fifo_full;
    wire [31:0] fifo_q;
    wire accept_word=push && accepting && !failed && !fifo_full &&
        received_words<BIOS_WORDS && bridge_addr[1:0]==0 &&
        {1'b0,bridge_addr[18:2]}==received_words;
    scfifo #(.lpm_width(32),.lpm_numwords(256),.lpm_widthu(8),
        .lpm_showahead("ON"),.use_eab("ON"),.add_ram_output_register("OFF"),
        .overflow_checking("ON"),.underflow_checking("ON")) writes (
        .clock(clk),.sclr(reset || fifo_clear),.data(bridge_data),
        .wrreq(accept_word),.rdreq(pop),.q(fifo_q),.empty(fifo_empty),.full(fifo_full));

    reg [16:0] address=0;
    reg [31:0] data=0;
    assign busy=session==FREEZE || session==ACK_SLOT || session==ACTIVE;
    assign slot_ack=is_bios_slot && (reject_busy || session==ACK_SLOT);
    assign slot_ok=good_size && !reject_busy && !failed;
    assign req_valid=mem_state==MEM_ISSUE;
    assign req_write=1'b1;
    assign req_addr={7'b0,address,1'b0}; // normalized halfword address
    assign req_data={96'b0,data};
    assign req_be=16'h000f;
    assign rsp_ready=mem_state==MEM_WAIT;

    always @(posedge clk) begin
        prev_slot_request<=is_bios_slot;prev_write<=bridge_wr;
        fifo_clear<=0;pop<=0;
        if(reset) begin
            session<=IDLE;mem_state<=MEM_IDLE;freeze_toggle<=0;
            received_words<=0;committed_words<=0;ready<=0;failed<=0;
            prev_slot_request<=0;prev_write<=0;good_size<=0;complete_seen<=0;
            reject_busy<=0;fifo_clear<=0;pop<=0;
        end else begin
            if(!slot_request) reject_busy<=0;
            if(begin_slot) begin
                if(busy || mem_state!=MEM_IDLE || !fifo_empty || pop) begin
                    reject_busy<=1;failed<=1;ready<=0;
                end else begin
                    session<=FREEZE;freeze_toggle<=!freeze_toggle;
                    good_size<=slot_size==32'd524288;
                    received_words<=0;committed_words<=0;ready<=0;
                    failed<=slot_size!=32'd524288;complete_seen<=0;fifo_clear<=1;
                end
            end
            if(session==FREEZE && freeze_ack==freeze_toggle && memory_ready)
                session<=ACK_SLOT;
            if(session==ACK_SLOT && !slot_request)
                session<=good_size && !failed ? ACTIVE : FINISHED;
            if(allcomplete && (session==ACK_SLOT || session==ACTIVE)) complete_seen<=1;
            if(accept_word) received_words<=received_words+1'b1;
            if(push && !accept_word) begin failed<=1;ready<=0;end
            // A lost backing-memory initialization invalidates the session.
            // Starting a fresh session may wait for memory to initialize.
            if(!begin_slot && !memory_ready && (session==ACK_SLOT || session==ACTIVE || ready)) begin
                failed<=1;ready<=0;
            end

            case(mem_state)
                MEM_IDLE: if(!fifo_empty && !pop) begin
                    address<=committed_words[16:0];data<=fifo_q;pop<=1;mem_state<=MEM_ISSUE;
                end
                MEM_ISSUE: if(req_ready) mem_state<=MEM_WAIT;
                MEM_WAIT: if(rsp_valid) begin
                    if(rsp_error) begin failed<=1;ready<=0;end
                    committed_words<=committed_words+1'b1;mem_state<=MEM_IDLE;
                end
            endcase
            if(complete_seen && fifo_empty && !pop && mem_state==MEM_IDLE &&
                (session==ACK_SLOT || session==ACTIVE)) begin
                session<=FINISHED;
                if(received_words==BIOS_WORDS && committed_words==BIOS_WORDS && !failed &&
                    !push && !begin_slot && memory_ready && !reject_busy) ready<=1;
                else begin failed<=1;ready<=0;end
            end
        end
    end
endmodule
