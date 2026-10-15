`timescale 1ns/1ps
// Drop-in replacement for sim/reference/psx_disc_host.sv: the same ports, but
// sectors and the track table come from rtl/pocket/psx_cd_stream.sv behind
// the pinned APF command handler. A simple APF firmware model (74.25 MHz
// bridge clock) answers target dataslot reads from +disc_file. Every track
// table word the loader writes is compared with +disc_metadata, which
// scripts/psx_disc_host.py produced. No SD firmware or bridge PHY timing.
module psx_disc_host(input clk,input start,
    input sector_request,input [31:0] physical_lba,
    output sector_ack,sector_write,output [15:0] sector_data,
    output [8:0] track_addr,output [31:0] track_data,
    output track_write,output mounted,
    output integer sector_count=0);
    reg hclk=0;
    always #6.734 hclk=~hclk;
    string image_path,metadata_path;
    integer image_file,sectors=0,index_sector=0;
    reg [31:0] metadata[0:399];
    reg host_reset=1,core_reset=1;
    reg bridge_wr=0,bridge_rd=0;reg [31:0] bridge_addr=0,bridge_data=0;
    wire [31:0] command_data,table_q,update_size,target_offset,target_bridge,target_length;
    wire allcomplete,slot_update,table_active,target_read,target_done,host_busy,read_error;
    wire [15:0] update_id,target_id;wire [9:0] table_addr;wire [2:0] target_error;
    wire change_done,pending,idle;wire [7:0] generation;wire [19:0] count;
    psx_cd_stream loader(.host_clk(hclk),.host_reset(host_reset),.bridge_wr(bridge_wr),.bridge_addr(bridge_addr),
        .bridge_data(bridge_data),.allcomplete(allcomplete),.slot_update(slot_update),.slot_update_id(update_id),
        .slot_update_size(update_size),.table_active(table_active),.table_grant(1'b1),.table_addr(table_addr),.table_q(table_q),
        .target_read(target_read),.target_ready(1'b1),.target_id(target_id),.target_offset(target_offset),
        .target_bridge(target_bridge),.target_length(target_length),.target_done(target_done),
        .target_error(target_error),.host_busy(host_busy),.read_error(read_error),
        .clk(clk),.reset(core_reset),.host_region(2'd2),.disc_slot_id(16'h0),.disc_select_strobe(1'b0),
        .disc_change_done(change_done),.has_disc(mounted),.disc_pending(pending),
        .cd_hps_req(sector_request),.cd_hps_lba(physical_lba),.cd_hps_ack(sector_ack),
        .cd_hps_write(sector_write),.cd_hps_data(sector_data),.disc_generation(generation),
        .sector_count(count),.stream_idle(idle),
        .trackinfo_addr(track_addr),.trackinfo_data(track_data),.trackinfo_write(track_write));
    core_bridge_cmd commands(.clk(hclk),.bridge_endian_little(1'b1),.bridge_addr(bridge_addr),
        .bridge_rd(bridge_rd),.bridge_rd_data(command_data),.bridge_wr(bridge_wr),.bridge_wr_data(bridge_data),
        .status_boot_done(1'b1),.status_setup_done(1'b0),.status_running(1'b0),
        .dataslot_requestread_ack(1'b0),.dataslot_requestread_ok(1'b0),
        .dataslot_requestwrite_ack(1'b0),.dataslot_requestwrite_ok(1'b0),
        .dataslot_update(slot_update),.dataslot_update_id(update_id),.dataslot_update_size(update_size),
        .dataslot_allcomplete(allcomplete),
        .savestate_supported(1'b0),.savestate_addr(0),.savestate_size(0),.savestate_maxloadsize(0),
        .savestate_start_ack(0),.savestate_start_busy(0),.savestate_start_ok(0),.savestate_start_err(0),
        .savestate_load_ack(0),.savestate_load_busy(0),.savestate_load_ok(0),.savestate_load_err(0),
        .target_dataslot_read(target_read),.target_dataslot_write(1'b0),.target_dataslot_getfile(1'b0),
        .target_dataslot_openfile(1'b0),.target_dataslot_id(target_id),.target_dataslot_slotoffset(target_offset),
        .target_dataslot_bridgeaddr(target_bridge),.target_dataslot_length(target_length),
        .target_dataslot_done(target_done),.target_dataslot_err(target_error),
        .target_buffer_param_struct(0),.target_buffer_resp_struct(0),
        .datatable_addr(table_addr),.datatable_wren(1'b0),.datatable_data(0),.datatable_q(table_q));
    function automatic [31:0] swap(input [31:0] x);return {x[7:0],x[15:8],x[23:16],x[31:24]};endfunction
    task automatic write_bus(input [31:0] addr,input [31:0] data);
        @(negedge hclk);bridge_addr=addr;bridge_data=data;
        @(negedge hclk);bridge_wr=1;
        @(negedge hclk);bridge_wr=0;
    endtask
    task automatic read_bus(input [31:0] addr,output [31:0] data);
        @(negedge hclk);bridge_addr=addr;bridge_rd=1;
        repeat(4) @(negedge hclk);
        data=swap(command_data);bridge_rd=0;
    endtask
    task automatic host_command(input [15:0] command);
        reg [31:0] result;result=0;
        write_bus(32'hf8000000,swap({16'h434d,command}));
        for(integer n=0;n<1000 && result[31:16]!==16'h4f4b;n++) read_bus(32'hf8000000,result);
        if(result[31:16]!=16'h4f4b) $fatal(1,"APF host command %h",command);
    endtask
    // Same request log as the reference host (disc-requests.csv).
    integer trace;reg prev_request=0;
    initial trace=$fopen("disc-requests.csv","w");
    always @(posedge clk) begin
        prev_request<=sector_request;
        if(sector_request && !prev_request) begin $fwrite(trace,"%d,%d\n",sector_count,physical_lba);$fflush(trace);end
        if(track_write && track_data!==metadata[track_addr])
            $fatal(1,"Loader track word %0d=%h expected %h",track_addr,track_data,metadata[track_addr]);
        if(sector_ack && !$past(sector_ack)) sector_count++;
        if(read_error) $fatal(1,"Unexpected APF read error");
    end
    initial begin
        reg [31:0] status,id,offset,addr,length,word;integer b0,b1,b2,b3;
        if(!$value$plusargs("disc_file=%s",image_path) ||
           !$value$plusargs("disc_metadata=%s",metadata_path) ||
           !$value$plusargs("disc_sectors=%d",sectors)) $fatal(1,"Disc host configuration missing");
        void'($value$plusargs("disc_index=%d",index_sector));
        if(index_sector!=0) $fatal(1,"APF loader fixture covers INDEX 01 at file sector 0 only");
        image_file=$fopen(image_path,"rb");
        if(!image_file) $fatal(1,"Cannot open disc image");
        $readmemh(metadata_path,metadata);
        wait(start);
        @(negedge clk);core_reset=0;@(negedge hclk);host_reset=0;
        // Slot 0 (the disc) is deliberately not the first table entry.
        write_bus(32'hf8002000,swap(32'h20));write_bus(32'hf8002004,swap(32'd524288));
        write_bus(32'hf8002008,swap(32'h0));write_bus(32'hf800200c,swap(sectors*2352));
        host_command(16'h8f);
        forever begin
            // Poll only while a command can be pending: idle polling at
            // 74.25 MHz would dominate long connected simulations.
            if(!target_read && !host_busy) @(posedge target_read or posedge host_busy);
            read_bus(32'hf8001000,status);
            if(status[31:16]==16'h636d) begin
                if(status[15:0]!=16'h0180) $fatal(1,"APF target opcode %h",status[15:0]);
                read_bus(32'hf8001020,id);read_bus(32'hf8001024,offset);
                read_bus(32'hf8001028,addr);read_bus(32'hf800102c,length);
                if(id!=0 || length!=2352 || offset+2352>sectors*2352) $fatal(1,"APF read shape");
                write_bus(32'hf8001000,swap(32'h62750000));
                if($fseek(image_file,offset,0)!=0) $fatal(1,"Disc seek failed");
                for(integer n=0;n<588;n++) begin
                    b0=$fgetc(image_file);b1=$fgetc(image_file);b2=$fgetc(image_file);b3=$fgetc(image_file);
                    if(b3<0) $fatal(1,"Disc image truncated");
                    write_bus(addr+n*4,{b3[7:0],b2[7:0],b1[7:0],b0[7:0]});
                end
                write_bus(32'hf8001000,swap(32'h6f6b0000));
            end
        end
    end
    final begin $fclose(image_file);$fclose(trace);end
endmodule
