`timescale 1ps/1ps
module tb_bios_loader #(
    // Per-region BIOS slots: the Chip32 loader loadf's one of 0x20-0x2f.
    parameter [15:0] LOADER_ID=16'd0, LOADER_MASK=16'hffff
);
    integer CASE=0,SLOT=0;
    reg host_clk=0,cpu_clk=0,cpu_clock_on=1;
    always #6734 host_clk=~host_clk;
    always #14763 if(cpu_clock_on) cpu_clk=~cpu_clk;
    reg reset=1,memory_ready=0,cpu_locked=0,host_run=0;
    reg slot_request=0,allcomplete=0,bridge_wr=0;
    reg [15:0] slot_id=0;
    reg [31:0] slot_size=524288,bridge_addr=0,bridge_data=0;
    wire slot_ack,slot_ok,freeze_toggle,freeze_ack,cpu_reset;
    wire req_valid,req_write,req_ready,rsp_ready,ready,failed,busy;
    wire [24:0] req_addr;
    wire [127:0] req_data;
    wire [15:0] req_be;
    wire [17:0] received_words,committed_words;
    reg rsp_valid=0,rsp_error=0;
    psx_bios_loader #(.BIOS_SLOT_ID(LOADER_ID),.BIOS_SLOT_MASK(LOADER_MASK)) loader(.*,.clk(host_clk));
    psx_boot_gate gate(.host_clk(host_clk),.cpu_clk(cpu_clk),.reset_async(reset),
        .cpu_locked(cpu_locked),.host_run(host_run),.bios_ready(ready),
        .freeze_toggle(freeze_toggle),.cpu_reset(cpu_reset),.freeze_ack(freeze_ack));
    integer ticks=0,writes=0,epoch=0,reset_edges=0,guest_edges=0;
    integer transactions=0,stalled_requests=0,held_responses=0,freeze_waits=0;
    reg mem_busy=0,force_stall=0,hold_response=0;
    integer latency=0;
    reg last_stalled=0;
    reg [152:0] stalled_payload;
    assign req_ready=!mem_busy && !force_stall && ticks%7!=0 && ticks%113>=15;
    function automatic [31:0] word_at(input integer index,input integer bank);
        case(index)
            0:word_at=32'h3c1a8001; // little-endian file bytes 01 80 1a 3c
            1:word_at=32'h03400008;
            2:word_at=32'h00000000;
            default:word_at=32'ha5c35a3c ^ index ^ (bank*32'h17389542);
        endcase
    endfunction
    always @(posedge cpu_clk) begin
        if(reset || cpu_reset) reset_edges<=reset_edges+1;
        else begin
            if(!ready || failed || !host_run || !cpu_locked)
                $fatal(1,"CPU ran before complete valid BIOS");
            guest_edges<=guest_edges+1;
        end
    end
    always @(posedge host_clk) begin
        ticks<=ticks+1;
        if(reset) begin mem_busy<=0;rsp_valid<=0;rsp_error<=0;last_stalled<=0;end
        else begin
            if(req_valid && !req_ready) begin
                stalled_requests<=stalled_requests+1;
                if(last_stalled && stalled_payload!={req_addr,req_data})
                    $fatal(1,"BIOS request changed under backpressure");
                stalled_payload<={req_addr,req_data};last_stalled<=1;
            end else last_stalled<=0;
            if(req_valid && req_ready) begin
                if(!cpu_reset || reset_edges<2) $fatal(1,"ROM write before CPU freeze");
                if(req_addr!=writes*2 || !req_write || req_be!=16'h000f || req_data[127:32]!=0)
                    $fatal(1,"BIOS write address/mask/order");
                if(req_data[31:0]!==word_at(writes,epoch))
                    $fatal(1,"BIOS little-endian data mismatch index=%0d",writes);
                writes<=writes+1;transactions<=transactions+1;mem_busy<=1;
                latency<=1+(writes%5);rsp_error<=CASE==6 && writes==7;
            end
            if(mem_busy && !rsp_valid) begin
                if(hold_response) held_responses<=held_responses+1;
                else if(latency==0) rsp_valid<=1;
                else latency<=latency-1;
            end
            if(rsp_valid && rsp_ready) begin rsp_valid<=0;rsp_error<=0;mem_busy<=0;end
            if(ready && (failed || received_words!=131072 || committed_words!=131072 || busy || mem_busy))
                $fatal(1,"BIOS ready before final acknowledged write");
        end
        if(ticks>7000000) $fatal(1,"BIOS loader timeout");
    end
    task automatic source_word(input integer index);
        begin
            @(negedge host_clk);bridge_addr=index*4;bridge_data=word_at(index,epoch);bridge_wr=1;
            @(negedge host_clk);bridge_wr=0;
        end
    endtask
    task automatic open_bios(input integer size);
        integer before_freeze;
        begin
            before_freeze=transactions;
            @(negedge host_clk);slot_id=SLOT;slot_size=size;slot_request=1;
            while(!slot_ack) begin
                @(negedge host_clk);freeze_waits++;
                if(transactions!=before_freeze) $fatal(1,"Writes before APF slot acknowledgement");
            end
            if(size==524288 && !slot_ok) $fatal(1,"Valid BIOS slot rejected");
            if(size!=524288 && slot_ok) $fatal(1,"Wrong BIOS size accepted");
            @(negedge host_clk);slot_request=0;
            repeat(2) @(negedge host_clk);
        end
    endtask
    task automatic seal;
        begin @(negedge host_clk);allcomplete=1;@(negedge host_clk);allcomplete=0;end
    endtask
    task automatic expect_failure;
        begin
            repeat(30) @(negedge host_clk);
            if(!failed || ready || !cpu_reset) $fatal(1,"Malformed load released CPU");
            $display("PASS BIOS loader rejected case=%0d writes=%0d",CASE,writes);
            $finish;
            // The simulator finishes at the next scheduler boundary. Prevent the
            // caller from continuing into its success assertions meanwhile.
            #1;
            $fatal(1,"Simulation continued after finish");
        end
    endtask
    initial begin
        if($value$plusargs("CASE=%d",CASE)) begin end
        if($value$plusargs("SLOT=%d",SLOT)) begin end
        repeat(5) @(negedge host_clk);reset=0;memory_ready=1;cpu_locked=1;host_run=1;
        if(CASE==14) begin
            // Another slot's write request (a memory card, the disc) is not
            // the BIOS: no acknowledgement, no freeze, then a normal load.
            @(negedge host_clk);slot_id=SLOT^16'h0030;slot_size=524288;slot_request=1;
            repeat(50) begin
                @(negedge host_clk);
                if(slot_ack || busy || failed) $fatal(1,"Foreign slot acknowledged");
            end
            @(negedge host_clk);slot_request=0;
            repeat(2) @(negedge host_clk);
        end
        if(CASE==1) begin open_bios(524284);expect_failure();end
        if(CASE==10) begin
            @(negedge cpu_clk);cpu_clock_on=0;
            @(negedge host_clk);slot_request=1;
            repeat(30) begin @(negedge host_clk);freeze_waits++;end
            if(slot_ack || transactions || ready) $fatal(1,"Stopped CPU clock bypassed freeze");
            cpu_clock_on=1;
            wait(slot_ack);@(negedge host_clk);slot_request=0;
            repeat(2) @(negedge host_clk);
        end else open_bios(524288);
        if(CASE==2) begin source_word(0);seal();expect_failure();end
        if(CASE==3) begin source_word(1);expect_failure();end
        if(CASE==4) begin
            @(negedge host_clk);bridge_addr=1;bridge_data=word_at(0,epoch);bridge_wr=1;
            @(negedge host_clk);bridge_wr=0;expect_failure();
        end
        if(CASE==5) begin
            force_stall=1;
            for(integer i=0;i<300;i++) source_word(i);
            expect_failure();
        end
        if(CASE==7) begin
            source_word(0);repeat(5) @(negedge host_clk);
            @(negedge host_clk);slot_request=1;
            repeat(5) @(negedge host_clk);
            if(!slot_ack || slot_ok) $fatal(1,"Busy reload not rejected");
            @(negedge host_clk);slot_request=0;expect_failure();
        end
        if(CASE==8) begin
            memory_ready=0;expect_failure();
        end
        if(CASE==9) begin
            source_word(0);wait(mem_busy);
            @(negedge cpu_clk);cpu_clock_on=0;
            @(negedge host_clk);reset=1;
            repeat(5) @(negedge host_clk);
            if(!cpu_reset || ready) $fatal(1,"Stopped-clock reset failed");
            cpu_clock_on=1;writes=0;
            @(negedge host_clk);reset=0;
            open_bios(524288);
        end
        for(integer pass=0;pass<(CASE==0 ? 2:1);pass++) begin
            if(pass>0) begin epoch=1;writes=0;open_bios(524288);end
            for(integer i=0;i<131072;i++) begin
                if(i==131040) hold_response=1;
                source_word(i);
                // Other asset/diagnostic writes do not enter the BIOS FIFO.
                if(i==512) begin
                    @(negedge host_clk);bridge_addr=32'h20000000;bridge_wr=1;
                    @(negedge host_clk);bridge_wr=0;
                end
                repeat(14) @(negedge host_clk);
                if(CASE==6 && failed) expect_failure();
            end
            seal();repeat(40) @(negedge host_clk);
            if(ready || !cpu_reset) $fatal(1,"Pending final writes bypassed boot barrier");
            hold_response=0;
            if(CASE==12 || CASE==13) begin
                wait(loader.complete_seen && loader.fifo_empty && !loader.pop &&
                    loader.mem_state==0 && committed_words==131072);
                @(negedge host_clk);
                if(CASE==12) begin bridge_addr=0;bridge_data=0;bridge_wr=1;end
                else memory_ready=0;
                @(negedge host_clk);bridge_wr=0;
                expect_failure();
            end
            wait(ready || failed);
            if(failed || writes!=131072) $fatal(1,"Complete BIOS load failed");
            wait(!cpu_reset);repeat(5) @(negedge cpu_clk);
        end
        if(CASE==11) begin source_word(0);expect_failure();end
        if(stalled_requests==0 || held_responses==0 || freeze_waits<2 || guest_edges==0)
            $fatal(1,"BIOS loader coverage missing");
        $display("PASS BIOS loader case=%0d transactions=%0d stalls=%0d held=%0d freeze_waits=%0d guest_edges=%0d",
            CASE,transactions,stalled_requests,held_responses,freeze_waits,guest_edges);
        $finish;
    end
endmodule
