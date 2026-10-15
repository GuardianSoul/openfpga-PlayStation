`timescale 1ps/1ps
// Exercises the pinned command handler at its registered bridge boundary.
// Serial bridge PHY, data-table RAM and real CPU execution are outside scope.
module mf_datatable(input [9:0] address_a,address_b,input clock_a,clock_b,
    input [31:0] data_a,data_b,input wren_a,wren_b,output [31:0] q_a,q_b);
    assign q_a=0,q_b=0;
endmodule

module tb_apf_bios_boot;
    parameter EARLY_SETUP=0, LITTLE=1;
    reg clk=0,cpu_clk=0,reset=1,memory_ready=0,cpu_locked=0;
    always #6734 clk=~clk;
    always #14763 cpu_clk=~cpu_clk;
    reg [31:0] bridge_addr=0,bridge_wr_data=0;
    reg bridge_wr=0,bridge_rd=0;
    wire [31:0] bridge_rd_data;
    wire host_run,slot_request,slot_ack,slot_ok,allcomplete;
    wire [15:0] slot_id;
    wire [31:0] slot_size;
    wire ready,failed,busy,freeze_toggle,freeze_ack,cpu_reset;
    wire req_valid,req_write,rsp_ready;
    wire [24:0] req_addr;
    wire [127:0] req_data;
    wire [15:0] req_be;
    wire [17:0] received_words,committed_words;
    reg rsp_valid=0,mem_busy=0,hold_final=0;
    integer latency=0,ticks=0,writes=0,ready_commands=0,guest_edges=0;
    wire req_ready=!mem_busy && ticks%11>2;
    psx_bios_loader loader(.clk(clk),.reset(reset),.memory_ready(memory_ready),
        .slot_request(slot_request),.slot_id(slot_id),.slot_size(slot_size),
        .slot_ack(slot_ack),.slot_ok(slot_ok),.allcomplete(allcomplete),
        .bridge_wr(bridge_wr),.bridge_addr(bridge_addr),.bridge_data(bridge_wr_data),
        .freeze_toggle(freeze_toggle),.freeze_ack(freeze_ack),
        .req_valid(req_valid),.req_write(req_write),.req_ready(req_ready),
        .req_addr(req_addr),.req_data(req_data),.req_be(req_be),
        .rsp_valid(rsp_valid),.rsp_error(1'b0),.rsp_ready(rsp_ready),
        .received_words(received_words),.committed_words(committed_words),
        .ready(ready),.failed(failed),.busy(busy));
    psx_boot_gate gate(.host_clk(clk),.cpu_clk(cpu_clk),.reset_async(reset),
        .cpu_locked(cpu_locked),.host_run(host_run),.bios_ready(ready),
        .freeze_toggle(freeze_toggle),.freeze_ack(freeze_ack),.cpu_reset(cpu_reset));
    core_bridge_cmd commands(.clk(clk),.reset_n(host_run),
        .bridge_endian_little(LITTLE!=0),.bridge_addr(bridge_addr),
        .bridge_rd(bridge_rd),.bridge_rd_data(bridge_rd_data),
        .bridge_wr(bridge_wr),.bridge_wr_data(bridge_wr_data),
        .status_boot_done(memory_ready),.status_setup_done(EARLY_SETUP ? memory_ready:ready),
        .status_running(host_run && ready),
        .dataslot_requestwrite(slot_request),.dataslot_requestwrite_id(slot_id),
        .dataslot_requestwrite_size(slot_size),
        .dataslot_requestwrite_ack(slot_id==0 ? slot_ack:memory_ready),
        .dataslot_requestwrite_ok(slot_id==0 ? slot_ok:1'b1),
        .dataslot_allcomplete(allcomplete),
        .dataslot_requestread_ack(1'b0),.dataslot_requestread_ok(1'b0),
        .savestate_supported(1'b0),.savestate_addr(32'b0),.savestate_size(32'b0),
        .savestate_maxloadsize(32'b0),.savestate_start_ack(1'b0),.savestate_start_busy(1'b0),
        .savestate_start_ok(1'b0),.savestate_start_err(1'b0),.savestate_load_ack(1'b0),
        .savestate_load_busy(1'b0),.savestate_load_ok(1'b0),.savestate_load_err(1'b0),
        .target_dataslot_read(1'b0),.target_dataslot_write(1'b0),
        .target_dataslot_getfile(1'b0),.target_dataslot_openfile(1'b0),
        .target_dataslot_id(16'b0),.target_dataslot_slotoffset(32'b0),
        .target_dataslot_bridgeaddr(32'b0),.target_dataslot_length(32'b0),
        .target_buffer_param_struct(32'b0),.target_buffer_resp_struct(32'b0),
        .datatable_addr(10'b0),.datatable_wren(1'b0),.datatable_data(32'b0));
    function automatic [31:0] swap(input [31:0] x);
        swap={x[7:0],x[15:8],x[23:16],x[31:24]};
    endfunction
    // File byte order is constructed independently of command-word swapping.
    function automatic [7:0] file_byte(input integer a);
        case(a)
            0:file_byte=8'h01;1:file_byte=8'h80;2:file_byte=8'h1a;3:file_byte=8'h3c;
            4:file_byte=8'h08;5:file_byte=8'h00;6:file_byte=8'h40;7:file_byte=8'h03;
            default:file_byte=(a*37+91)&255;
        endcase
    endfunction
    function automatic [31:0] file_word(input integer index);
        file_word={file_byte(index*4+3),file_byte(index*4+2),file_byte(index*4+1),file_byte(index*4)};
    endfunction
    task automatic write_bus(input [31:0] a,d);
        @(negedge clk);bridge_addr=a;bridge_wr_data=d;
        // The real bridge registers data before raising its write strobe.
        @(negedge clk);bridge_wr=1;
        @(negedge clk);bridge_wr=0;
    endtask
    task automatic read_bus(input [31:0] a,output [31:0] d);
        @(negedge clk);bridge_addr=a;bridge_rd=1;
        repeat(4) @(negedge clk);
        d=swap(bridge_rd_data);bridge_rd=0;
    endtask
    task automatic command(input [15:0] code,input [15:0] expected);
        reg [31:0] result;
        integer count;
        write_bus(32'hf8000000,swap({16'h434d,code}));
        count=0;result=0;
        while(result[31:16]!=16'h4f4b && count<300) begin
            read_bus(32'hf8000000,result);count++;
        end
        if(result!={16'h4f4b,expected}) $fatal(1,"APF command result code=%04x result=%08x",code,result);
    endtask
    task automatic open_slot(input [15:0] id,input [31:0] size,input [15:0] expected);
        write_bus(32'hf8000020,swap({16'b0,id}));
        write_bus(32'hf8000024,swap(size));
        command(16'h0082,expected);
        repeat(5) @(negedge clk);
    endtask
    always @(posedge cpu_clk) if(!cpu_reset) begin
        if(!host_run || !ready || failed) $fatal(1,"APF released CPU before BIOS complete");
        guest_edges<=guest_edges+1;
    end
    always @(posedge clk) begin
        ticks<=ticks+1;
        if(commands.target_0==32'h636d0140 && ready_commands==0) begin
            if(!ready || committed_words!=131072 || mem_busy)
                $fatal(1,"Premature APF Ready to Run");
            ready_commands<=ready_commands+1;
        end
        if(req_valid && req_ready) begin
            if(!cpu_reset || req_addr!=writes*2 || !req_write || req_be!=15 ||
                req_data!={96'b0,file_word(writes)}) $fatal(1,"APF BIOS memory word/order");
            writes<=writes+1;mem_busy<=1;latency<=writes%5+1;
        end
        if(mem_busy && !rsp_valid) begin
            if(!(hold_final && writes==131072)) begin
                if(latency==0) rsp_valid<=1;else latency<=latency-1;
            end
        end
        if(rsp_valid && rsp_ready) begin rsp_valid<=0;mem_busy<=0;end
        if(ticks>4000000) $fatal(1,"APF boot timeout");
    end
    initial begin
        repeat(8) @(negedge clk);reset=0;memory_ready=1;cpu_locked=1;
        command(16'h0010,0);command(16'h0000,2);
        open_slot(16'h0007,32'd1024,0);
        if(busy || freeze_toggle || ready) $fatal(1,"Other APF slot started BIOS load");
        open_slot(0,524288,0);
        if(slot_size!=524288 || slot_id!=0) $fatal(1,"APF slot endian/size mismatch");
        hold_final=1;
        for(integer i=0;i<131072;i++) begin
            write_bus(i*4,file_word(i));repeat(13) @(negedge clk);
        end
        command(16'h008f,0);
        repeat(20) @(negedge clk);
        if(ready || ready_commands || !cpu_reset) $fatal(1,"APF allcomplete bypassed last write");
        hold_final=0;wait(ready_commands==1);
        // Acknowledge the target's Ready to Run through the endian-aware bus.
        write_bus(32'hf8001000,swap(32'h6f6b0000));
        if(!cpu_reset) $fatal(1,"CPU ran without host Reset Exit");
        command(16'h0011,0);wait(!cpu_reset);
        repeat(6) @(negedge cpu_clk);
        // Runtime reload: reject wrong-size BIOS and freeze the already running CPU.
        open_slot(0,524284,2);
        if(!failed || ready || !cpu_reset || guest_edges==0 || writes!=131072)
            $fatal(1,"APF malformed reload not frozen");
        $display("PASS APF BIOS boot words=%0d ready_commands=%0d guest_edges=%0d",writes,ready_commands,guest_edges);
        $finish;
    end
endmodule
