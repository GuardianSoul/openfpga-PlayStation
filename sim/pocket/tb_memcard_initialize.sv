`timescale 1ns/1ps
module tb_memcard_initialize;
    reg clk=0,reset=1,start=0,memory_ready=1,loads_idle=0,load_failed=0;
    always #6.734 clk=~clk;
    reg [1:0] loaded_images=0;
    wire freeze,busy,ready,failed;
    wire [1:0] mounted,initialized;
    reg freeze_ack=0;
    wire memory_idle;
    wire req_valid,req_write,req_ready,rsp_ready;
    wire [24:0] req_addr;wire [3:0] req_halfwords;
    wire [127:0] req_data;wire [15:0] req_be;
    reg rsp_valid=0,rsp_error=0;
    wire size_request,size_commit,size_done,size_failed;
    wire table_grant,size_busy;
    reg peer_table_request=1;
    wire [1:0] table_grants;
    wire [9:0] shared_table_addr;
    wire shared_table_wren;
    wire [31:0] shared_table_data;
    wire [15:0] size_slot;wire [31:0] size_bytes,expected_size;
    wire [9:0] datatable_addr;wire datatable_wren;
    wire [31:0] datatable_data;reg [31:0] datatable_q=0;
    reg [31:0] table_words[0:63],original_table[0:63];
    reg [7:0] memory[0:1][0:131071];
    reg [7:0] expected_blank[0:131071];
    reg [63:0] stored_data=0;integer byte_address=0,selected_card=0;
    integer ticks=0,freeze_ticks=0,pin_delay=0,latency=0;
    integer writes=0,table_writes=0,checked_bytes=0;
    integer counts[0:1],expected_counts[0:1];
    reg pending=0,hold_final=1,hold_old_ack=0;
    reg [1:0] loaded_at_drain=0;
    integer test_case=0;
    string blank_path;
    assign memory_idle=!pending && !rsp_valid && pin_delay==0;
    assign req_ready=memory_idle && ticks%7!=0;
    psx_memcard_initialize dut(.*);
    psx_dataslot_size size_table(.clk(clk),.reset(reset),.request(size_request),
        .slot_id(size_slot),.size_bytes(size_bytes),.expected_size(expected_size),
        .commit_size(size_commit),.table_grant(table_grant),.busy(size_busy),.done(size_done),.failed(size_failed),
        .datatable_addr(datatable_addr),.datatable_wren(datatable_wren),
        .datatable_data(datatable_data),.datatable_q(datatable_q));
    psx_apf_table_arb table_port(.clk(clk),.reset(reset),
        .request({peer_table_request,size_busy}),.write_enable({1'b0,datatable_wren}),
        .address({10'd40,datatable_addr}),.data({32'b0,datatable_data}),.grant(table_grants),
        .table_address(shared_table_addr),.table_write(shared_table_wren),.table_data(shared_table_data));
    assign table_grant=table_grants[0];
    function automatic [7:0] loaded_byte(input integer card,input integer index);
        return 8'(index*19+(index>>8)*23+card*61+8'h39);
    endfunction
    always @(posedge clk) begin
        ticks<=ticks+1;
        if(hold_old_ack) freeze_ack<=1;
        else if(!freeze) begin freeze_ticks<=0;freeze_ack<=0;end
        else begin freeze_ticks<=freeze_ticks+1;if(freeze_ticks>=27) freeze_ack<=1;end
        if(pin_delay>0) pin_delay<=pin_delay-1;
        if(freeze && !freeze_ack && (req_valid || size_request || mounted))
            $fatal(1,"Initialization bypassed guest drain");
        if(!loads_idle && (req_valid || size_request || mounted))
            $fatal(1,"Initialization bypassed pending APF load");
        if(req_valid && req_ready) begin
            if(!req_write || req_halfwords!=4 || req_be!=16'hff || req_data[127:64]!=0)
                $fatal(1,"Blank packet width/mask mismatch");
            if(req_addr>=25'h80000 && req_addr<25'h90000) begin
                selected_card=0;byte_address=(req_addr-25'h80000)*2;
            end else if(req_addr>=25'h100000 && req_addr<25'h110000) begin
                selected_card=1;byte_address=(req_addr-25'h100000)*2;
            end else $fatal(1,"Blank packet outside logical card window");
            if(loaded_at_drain[selected_card]) $fatal(1,"Blank initialization overwrote loaded card");
            if(byte_address!=counts[selected_card]*8) $fatal(1,"Blank cursor/order mismatch");
            for(integer b=0;b<8;b++) if(req_data[b*8+:8]!==expected_blank[byte_address+b])
                $fatal(1,"Blank generator byte/layout mismatch");
            stored_data=req_data[63:0];pending<=1;latency<=writes%19+2;
        end
        if(pending && !rsp_valid) begin
            if(!(hold_final && byte_address==131064)) begin
                if(latency>0) latency<=latency-1;
                else begin
                    rsp_error<=test_case==4;
                    if(test_case!=4) for(integer b=0;b<8;b++) memory[selected_card][byte_address+b]<=stored_data[b*8+:8];
                    counts[selected_card]++;writes++;rsp_valid<=1;
                end
            end
        end
        if(rsp_valid && rsp_ready) begin pending<=0;rsp_valid<=0;pin_delay<=21;end
        datatable_q<=table_words[shared_table_addr];
        if(shared_table_wren) begin
            if(shared_table_addr!=7 && shared_table_addr!=63) $fatal(1,"Wrong APF table position");
            if(shared_table_data!=131072) $fatal(1,"Wrong APF card byte size");
            table_words[shared_table_addr]<=shared_table_data;table_writes++;
        end
        if(size_request && size_commit) begin
            integer card;
            card=size_slot==16'h11;
            if(size_slot!=16'h10 && size_slot!=16'h11 || size_bytes!=131072)
                $fatal(1,"Size registration ID/length mismatch");
            if(!memory_idle || !freeze_ack || counts[card]!=expected_counts[card])
                $fatal(1,"Size registration before complete physical image");
        end
        if(mounted) begin
            if(mounted!=3 || !ready || failed || !memory_idle || table_writes!=2 ||
                table_words[7]!=131072 || table_words[63]!=131072 ||
                counts[0]!=expected_counts[0] || counts[1]!=expected_counts[1])
                $fatal(1,"Mounted before complete images/table/drain");
        end
        if(ticks>2500000) $fatal(1,"Blank initialization timeout");
    end
    task automatic launch;
        @(negedge clk);start=1;@(negedge clk);start=0;
    endtask
    initial begin
        if($value$plusargs("CASE=%d",test_case)) begin end
        if(!$value$plusargs("BLANK=%s",blank_path)) $fatal(1,"Authored blank reference required");
        $readmemh(blank_path,expected_blank);
        for(integer i=0;i<64;i++) begin
            table_words[i]=32'h55000000+i;original_table[i]=32'h55000000+i;
        end
        table_words[6]=16'h10;table_words[7]=0;
        table_words[62]=16'h11;table_words[63]=0;
        original_table[6]=16'h10;original_table[7]=0;
        original_table[62]=16'h11;original_table[63]=0;
        // Slot IDs16/17 live at table positions3/31, deliberately unlike IDs.
        if(test_case==3) table_words[62]=16'h12;
        if(test_case==5) table_words[6]=32'h00010010;
        loaded_at_drain=(test_case==1 || test_case==10) ? 2'b01:test_case==2 ? 2'b11:2'b00;
        if(loaded_at_drain[0]) begin table_words[7]=131072;original_table[7]=131072;end
        if(loaded_at_drain[1]) begin table_words[63]=131072;original_table[63]=131072;end
        if(test_case==9) table_words[7]=131072;
        if(test_case==10) table_words[7]=0;
        for(integer card=0;card<2;card++) begin
            counts[card]=0;expected_counts[card]=loaded_at_drain[card] ? 0:16384;
            for(integer b=0;b<131072;b++) memory[card][b]=loaded_at_drain[card] ? loaded_byte(card,b):8'h5a;
        end
        repeat(8) @(negedge clk);reset=0;
        if(test_case==7) hold_old_ack=1;
        launch();repeat(60) @(negedge clk);
        if(mounted || writes || table_writes) $fatal(1,"Load drain was skipped");
        if(test_case==7) begin
            if(freeze || !busy) $fatal(1,"Old owner acknowledgement reused");
            hold_old_ack=0;
        end
        loaded_images=loaded_at_drain;loads_idle=1;
        // Requests must be captured while the table is owned by another
        // client, with no lookup progress or write until grant is returned.
        repeat(90) @(negedge clk);
        if(writes || table_writes || mounted) $fatal(1,"Table grant was bypassed");
        peer_table_request=0;
        if(test_case==6) begin
            load_failed=1;repeat(20) @(negedge clk);
            if(!failed || ready || mounted || !freeze) $fatal(1,"Failed load replaced with blank");
        end else if(test_case==8) begin
            wait(req_valid && req_ready);launch();repeat(10) @(negedge clk);
            if(!failed || ready || mounted || !freeze) $fatal(1,"Overlapping initialize accepted");
        end else if(test_case==4) begin
            wait(failed);if(mounted || ready || !freeze || table_writes) $fatal(1,"Failed blank mounted");
        end else if(test_case==3 || test_case==5 || test_case==9 || test_case==10) begin
            hold_final=0;wait(failed);
            if(mounted || ready || !freeze) $fatal(1,"Missing/invalid table accepted");
            if(test_case!=3 && (table_writes || writes)) $fatal(1,"Invalid slot reformatted or registered");
            if(test_case==3 && counts[1]!=0) $fatal(1,"Missing table slot was overwritten");
        end else begin
            if(expected_counts[0]!=0) begin
                wait(pending && selected_card==0 && byte_address==131064);
                repeat(35) @(negedge clk);
                if(mounted || ready || table_writes) $fatal(1,"Last blank packet bypassed");
            end
            hold_final=0;
            if(test_case==3 || test_case==5) begin
                wait(failed);if(mounted || ready || !freeze) $fatal(1,"Missing/invalid table accepted");
                if(test_case==5 && table_writes) $fatal(1,"Invalid 48-bit entry modified");
            end else begin
                wait(ready);repeat(10) @(negedge clk);
                if(mounted!=3 || failed || busy || freeze) $fatal(1,"Initialization completion mismatch");
                for(integer card=0;card<2;card++) for(integer b=0;b<131072;b++) begin
                    if(memory[card][b]!== (loaded_at_drain[card] ? loaded_byte(card,b):expected_blank[b]))
                        $fatal(1,"Initialized/loaded card byte mismatch");
                    checked_bytes++;
                end
                for(integer i=0;i<64;i++) if(i!=7 && i!=63 && table_words[i]!==original_table[i])
                    $fatal(1,"APF registration modified unrelated entry");
                // Reinitialization starts from a fresh four-phase owner return.
                // The initializer must not use a stale ownership acknowledgement.
                repeat(5) @(negedge clk);memory_ready=0;repeat(5) @(negedge clk);
                if(mounted || ready || !failed || !freeze) $fatal(1,"Lost initialized memory stayed mounted");
            end
        end
        $display("PASS MEMCARD INITIALIZE case=%0d words=%0d sizes=%0d bytes=%0d",test_case,writes,table_writes,checked_bytes);$finish;
    end
endmodule
