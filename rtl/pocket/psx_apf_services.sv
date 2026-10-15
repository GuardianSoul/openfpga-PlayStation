// SPDX-License-Identifier: MIT
// Actual CD and card clients sharing the pinned APF handler's target/table
// ports. Card packets retain logical DDR-window halfword addresses; the caller
// supplies the chosen backing fabric and a REAL guest/source/pin drain lease.
// initialize_request is an explicit initial-files-finished event, never every
// 008F or runtime disc reload. reset/cd_reset are COMMON TRANSPORT resets;
// guest reset must not cancel either client's active APF transaction.
// Whole-card automatic OS reads and their suspension coordinator are separate.
module psx_apf_services #(
    parameter [15:0] CD_SLOT=16'h0,CARD1_SLOT=16'h10,CARD2_SLOT=16'h11,
    parameter integer CD_BUFFERS=2
) (
    input wire clk,reset,cd_clk,cd_reset,
    input wire memory_ready,memory_idle,initialize_request,
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
    output wire target_read,target_write,output wire [15:0] target_id,
    output wire [31:0] target_offset,target_bridge,target_length,
    input wire target_done,input wire [2:0] target_error,
    output wire table_active,output wire [9:0] datatable_addr,
    output wire datatable_wren,output wire [31:0] datatable_data,input wire [31:0] datatable_q,
    input wire slot_update,input wire [15:0] slot_update_id,input wire [31:0] slot_update_size,
    input wire [1:0] host_region,input wire [15:0] disc_slot_id,input wire disc_select_strobe,
    output wire disc_change_done,has_disc,disc_pending,
    output wire [7:0] disc_generation,output wire [19:0] sector_count,
    output wire cd_host_busy,stream_idle,cd_read_error,
    input wire cd_hps_req,input wire [31:0] cd_hps_lba,
    output wire cd_hps_ack,cd_hps_write,output wire [15:0] cd_hps_data,
    output wire [8:0] trackinfo_addr,output wire [31:0] trackinfo_data,output wire trackinfo_write,
    output wire target_idle,protocol_error
);
    wire [1:0] client_valid,client_ready,client_busy,client_done,client_issued;
    wire [1:0][2:0] client_error;
    wire [1:0][15:0] client_id;
    wire [1:0][31:0] client_offset,client_bridge,client_length;
    wire [1:0] table_request,table_write,table_grant;
    wire [1:0][9:0] table_addr;
    wire [1:0][31:0] table_data;
    assign target_idle=!(|client_busy);
    assign table_active=|table_request;
    psx_apf_target_arb #(.CLIENTS(2)) targets (
        .clk(clk),.reset(reset),.p_valid(client_valid),.p_write(2'b10),
        .p_ready(client_ready),.p_busy(client_busy),.p_done(client_done),.p_issued(client_issued),
        .p_error(client_error),.p_id(client_id),.p_offset(client_offset),
        .p_bridge(client_bridge),.p_length(client_length),
        .target_read(target_read),.target_write(target_write),.target_id(target_id),
        .target_offset(target_offset),.target_bridge(target_bridge),.target_length(target_length),
        .target_done(target_done),.target_error(target_error),.protocol_error(protocol_error));
    psx_apf_table_arb #(.CLIENTS(2)) tables (
        .clk(clk),.reset(reset),.request(table_request),.write_enable(table_write),
        .address(table_addr),.data(table_data),.grant(table_grant),
        .table_address(datatable_addr),.table_write(datatable_wren),.table_data(datatable_data));
    assign table_write[0]=1'b0,table_data[0]=32'b0;
    psx_cd_stream #(.DEFAULT_SLOT(CD_SLOT),.NBUF(CD_BUFFERS)) disc (
        .host_clk(clk),.host_reset(reset),.bridge_wr(bridge_wr),.bridge_addr(bridge_addr),.bridge_data(bridge_data),
        .allcomplete(allcomplete),.slot_update(slot_update),.slot_update_id(slot_update_id),.slot_update_size(slot_update_size),
        .table_active(table_request[0]),.table_grant(table_grant[0]),.table_addr(table_addr[0]),.table_q(datatable_q),
        .target_read(client_valid[0]),.target_ready(client_ready[0]),.target_done(client_done[0]),.target_error(client_error[0]),
        .target_id(client_id[0]),.target_offset(client_offset[0]),.target_bridge(client_bridge[0]),.target_length(client_length[0]),
        .host_busy(cd_host_busy),.read_error(cd_read_error),
        .clk(cd_clk),.reset(cd_reset),.host_region(host_region),.disc_slot_id(disc_slot_id),.disc_select_strobe(disc_select_strobe),
        .disc_change_done(disc_change_done),.has_disc(has_disc),.disc_pending(disc_pending),.disc_generation(disc_generation),
        .sector_count(sector_count),.stream_idle(stream_idle),
        .cd_hps_req(cd_hps_req),.cd_hps_lba(cd_hps_lba),.cd_hps_ack(cd_hps_ack),.cd_hps_write(cd_hps_write),.cd_hps_data(cd_hps_data),
        .trackinfo_addr(trackinfo_addr),.trackinfo_data(trackinfo_data),.trackinfo_write(trackinfo_write));
    psx_memcard_service #(.CARD1_SLOT(CARD1_SLOT),.CARD2_SLOT(CARD2_SLOT)) cards (
        .clk(clk),.reset(reset),.memory_ready(memory_ready),.memory_idle(memory_idle),.initialize_request(initialize_request),
        .slot_request(slot_request),.slot_id(slot_id),.slot_size(slot_size),.slot_ack(slot_ack),.slot_ok(slot_ok),
        .allcomplete(allcomplete),.bridge_wr(bridge_wr),.bridge_addr(bridge_addr),.bridge_data(bridge_data),
        .staging_data(staging_data),.staging_selected(staging_selected),
        .flush_request(flush_request),.flush_card(flush_card),.guest_dirty(guest_dirty),
        .image_valid(image_valid),.dirty(dirty),.mounted(mounted),.freeze(freeze),.freeze_ack(freeze_ack),
        .busy(busy),.ready(ready),.failed(failed),.flush_done(flush_done),
        .req_valid(req_valid),.req_write(req_write),.req_ready(req_ready),.req_addr(req_addr),.req_halfwords(req_halfwords),
        .req_data(req_data),.req_be(req_be),.rsp_valid(rsp_valid),.rsp_error(rsp_error),.rsp_data(rsp_data),.rsp_ready(rsp_ready),
        .target_write(client_valid[1]),.target_ready(client_ready[1]),.target_done(client_done[1]),.target_error(client_error[1]),
        .target_id(client_id[1]),.target_offset(client_offset[1]),.target_bridge(client_bridge[1]),.target_length(client_length[1]),
        .table_active(table_request[1]),.table_grant(table_grant[1]),.datatable_addr(table_addr[1]),
        .datatable_wren(table_write[1]),.datatable_data(table_data[1]),.datatable_q(datatable_q));
endmodule
