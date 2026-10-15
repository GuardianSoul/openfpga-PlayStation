// SPDX-License-Identifier: MIT
// Host-clock composition of complete file loads, absent-card initialization,
// APF size validation/registration and explicit staged write-back. The caller
// supplies actual memory/source/pin drain and the reserved APF table A-port.
// initialize_request is the initial all-files-complete event, not every
// runtime disc reload. Transport and APF command arbitration survive guest reset.
module psx_memcard_service #(
    parameter [15:0] CARD1_SLOT=16'h10,CARD2_SLOT=16'h11,
    parameter [31:0] CARD1_BRIDGE=32'h21000000,CARD2_BRIDGE=32'h22000000,
    parameter [31:0] STAGING_BRIDGE=32'h23000000
) (
    input wire clk,reset,memory_ready,memory_idle,initialize_request,
    input wire slot_request,input wire [15:0] slot_id,input wire [31:0] slot_size,
    output wire slot_ack,slot_ok,
    input wire allcomplete,bridge_wr,input wire [31:0] bridge_addr,bridge_data,
    output wire [31:0] staging_data,output wire staging_selected,
    input wire flush_request,flush_card,input wire [1:0] guest_dirty,
    output wire [1:0] image_valid,dirty,mounted,
    output wire freeze,input wire freeze_ack,
    output wire busy,ready,failed,flush_done,
    output wire req_valid,req_write,input wire req_ready,
    output wire [24:0] req_addr,output wire [3:0] req_halfwords,
    output wire [127:0] req_data,output wire [15:0] req_be,
    input wire rsp_valid,rsp_error,input wire [127:0] rsp_data,output wire rsp_ready,
    output wire target_write,output wire [15:0] target_id,
    output wire [31:0] target_offset,target_bridge,target_length,
    input wire target_ready,target_done,input wire [2:0] target_error,
    output wire table_active,input wire table_grant,
    output wire [9:0] datatable_addr,output wire datatable_wren,
    output wire [31:0] datatable_data,input wire [31:0] datatable_q
);
    wire transfer_busy,transfer_freeze,transfer_failed;
    wire init_busy,init_freeze,init_ready,init_failed;
    wire [1:0] initialized;
    reg adopted=0;
    wire adopting=init_ready && !init_failed && !adopted;
    wire commit=adopting && freeze_ack && memory_idle && !transfer_busy;
    // Keep the SAME ownership lease through the initializer's completed sizes
    // and the transfer engine's valid-bit adoption; there is no unfrozen gap.
    assign freeze=transfer_freeze || init_freeze || adopting;
    // A host save error leaves the resident image intact and dirty for retry.
    // Load/backing-memory errors clear validity and cannot remain mounted.
    assign ready=init_ready && adopted && !init_failed &&
        !(transfer_failed && transfer_busy) && memory_ready && image_valid==2'b11;
    assign mounted=ready ? initialized:2'b00;
    assign busy=transfer_busy || init_busy || adopting;
    assign failed=transfer_failed || init_failed;
    always @(posedge clk) begin
        if(reset) adopted<=0;
        else if(!init_ready) adopted<=0;
        else if((image_valid & initialized)==initialized && initialized==2'b11) adopted<=1;
    end
    wire t_valid,t_write,t_rsp_ready,i_valid,i_write,i_rsp_ready;
    wire [24:0] t_addr,i_addr;
    wire [3:0] t_halfwords,i_halfwords;
    wire [127:0] t_data,i_data;
    wire [15:0] t_be,i_be;
    // Init first waits for transfer idle plus physical drain. Its lease ends
    // only after its final response/drain, so responses cannot change owner.
    wire initializer_owns=init_freeze && !transfer_busy;
    assign req_valid=initializer_owns ? i_valid:t_valid;
    assign req_write=initializer_owns ? i_write:t_write;
    assign req_addr=initializer_owns ? i_addr:t_addr;
    assign req_halfwords=initializer_owns ? i_halfwords:t_halfwords;
    assign req_data=initializer_owns ? i_data:t_data;
    assign req_be=initializer_owns ? i_be:t_be;
    assign rsp_ready=initializer_owns ? i_rsp_ready:t_rsp_ready;
    psx_memcard_transfer #(.CARD1_SLOT(CARD1_SLOT),.CARD2_SLOT(CARD2_SLOT),
        .CARD1_BRIDGE(CARD1_BRIDGE),.CARD2_BRIDGE(CARD2_BRIDGE),.STAGING_BRIDGE(STAGING_BRIDGE),
        .EXTERNAL_INITIALIZATION(1),.TARGET_BACKPRESSURE(1)) transfer (
        .clk(clk),.reset(reset),.memory_ready(memory_ready),
        .slot_request(slot_request),.slot_id(slot_id),.slot_size(slot_size),.slot_ack(slot_ack),.slot_ok(slot_ok),
        .allcomplete(allcomplete),.bridge_wr(bridge_wr),.bridge_addr(bridge_addr),.bridge_data(bridge_data),
        .staging_data(staging_data),.staging_selected(staging_selected),
        .flush_request(flush_request && mounted[flush_card]),.flush_card(flush_card),.guest_dirty(guest_dirty),
        .image_valid(image_valid),.dirty(dirty),.freeze(transfer_freeze),.freeze_ack(freeze_ack),
        .busy(transfer_busy),.failed(transfer_failed),.flush_done(flush_done),
        .req_valid(t_valid),.req_write(t_write),.req_ready(req_ready && !initializer_owns),
        .req_addr(t_addr),.req_halfwords(t_halfwords),.req_data(t_data),.req_be(t_be),
        .rsp_valid(rsp_valid && !initializer_owns),.rsp_error(rsp_error),.rsp_data(rsp_data),.rsp_ready(t_rsp_ready),
        .target_write(target_write),.target_id(target_id),.target_offset(target_offset),
        .target_bridge(target_bridge),.target_length(target_length),.target_ready(target_ready),
        .target_done(target_done),.target_error(target_error),
        .initialization_commit(commit),.initialized_images(initialized));
    wire size_request,size_commit,size_done,size_failed;
    wire [15:0] size_slot;wire [31:0] size_bytes,expected_size;
    psx_memcard_initialize #(.CARD1_SLOT(CARD1_SLOT),.CARD2_SLOT(CARD2_SLOT)) initializer (
        .clk(clk),.reset(reset),.start(initialize_request),.memory_ready(memory_ready),
        .loads_idle(!transfer_busy),.load_failed(transfer_failed),.loaded_images(image_valid),
        .freeze(init_freeze),.freeze_ack(freeze_ack),.memory_idle(memory_idle),
        .busy(init_busy),.ready(init_ready),.failed(init_failed),.mounted(),.initialized(initialized),
        .req_valid(i_valid),.req_write(i_write),.req_ready(req_ready && initializer_owns),
        .req_addr(i_addr),.req_halfwords(i_halfwords),.req_data(i_data),.req_be(i_be),
        .rsp_valid(rsp_valid && initializer_owns),.rsp_error(rsp_error),.rsp_ready(i_rsp_ready),
        .size_request(size_request),.size_commit(size_commit),.size_slot(size_slot),
        .size_bytes(size_bytes),.expected_size(expected_size),.size_done(size_done),.size_failed(size_failed));
    psx_dataslot_size sizes(.clk(clk),.reset(reset),.request(size_request),.commit_size(size_commit),
        .table_grant(table_grant),.slot_id(size_slot),.size_bytes(size_bytes),.expected_size(expected_size),
        .busy(table_active),.done(size_done),.failed(size_failed),.datatable_addr(datatable_addr),
        .datatable_wren(datatable_wren),.datatable_data(datatable_data),.datatable_q(datatable_q));
endmodule
