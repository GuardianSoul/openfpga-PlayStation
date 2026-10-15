// SPDX-License-Identifier: MIT
`timescale 1ns/1ps
// Actual native adapter/CDC with a deliberately stalled packet target.
// Pin timing is covered separately by the connected shared-SDRAM fixture.
module tb_main_reset_transport;
    parameter real HALF1=15, HALF3=5, PHASE3=0, HALFMEM=8;
    reg clk1x=0,clk3x=0,mem_clk=0,reset_async=1;
    always #(HALF1) clk1x=!clk1x;
    initial begin #(PHASE3);forever #(HALF3) clk3x=!clk3x;end
    always #(HALFMEM) mem_clk=!mem_clk;
    reg reset_request=0,core_ce=1,ram_ena=0,ram_rnw=1,ram_dma=0,ram_cache=0;
    reg [24:0] ram_adr=0;reg [1:0] ram_cntDMA=0;
    reg fifo_empty=1;
    wire held,source_idle,service_idle,fault,ram_done,dma_wr,dma_ack,fifo_read;
    wire [31:0] ram_data,dma_data,cache_data;wire [3:0] cache_wr;wire [7:0] cache_addr;
    wire p_valid,p_write,p_ready,p_rsp_ready;wire [24:0] p_addr;
    wire [3:0] p_count;wire [127:0] p_wdata;wire [15:0] p_be;
    reg rsp_valid=0;reg [127:0] rsp_data=0;
    reg permit_request=1,permit_reply=1,busy=0;
    integer cycles=0,cache_words=0,accepted=0,committed=0;
    reg [31:0] memory[0:255];
    reg job_write;reg [24:0] job_addr;reg [3:0] job_count;
    reg [127:0] job_data;reg [15:0] job_be;
    assign p_ready=!busy && permit_request;
    psx_main_reset_fence fence(.clk1x(clk1x),.clk3x(clk3x),.reset_async(reset_async),
        .reset_request(reset_request),.source_idle(source_idle),.service_idle(service_idle),.hold_reset(held));
    psx_mister_main_adapter adapter(.clk1x(clk1x),.clk3x(clk3x),.mem_clk(mem_clk),
        .reset_async(reset_async),.core_ce(core_ce),.discard_returns(held),
        .ram_ena(ram_ena),.ram_rnw(ram_rnw),.ram_dma(ram_dma),.ram_cache(ram_cache),
        .ram_adr(ram_adr),.ram_cntDMA(ram_cntDMA),.ram_dataWrite(32'h98765432),.ram_be(4'hf),
        .ram_done(ram_done),.ram_dataRead32(ram_data),.dma_wr(dma_wr),.dma_data(dma_data),
        .dma_reqprocessed(dma_ack),.cache_wr(cache_wr),.cache_data(cache_data),.cache_addr(cache_addr),
        .ram_dmafifo_adr(23'h100),.ram_dmafifo_data(32'hcafebabe),.ram_dmafifo_empty(fifo_empty),
        .ram_dmafifo_read(fifo_read),.source_idle(source_idle),.service_idle(service_idle),.fault(fault),
        .p_valid(p_valid),.p_write(p_write),.p_ready(p_ready),.p_addr(p_addr),.p_halfwords(p_count),
        .p_wdata(p_wdata),.p_byte_en(p_be),.p_rsp_valid(rsp_valid),.p_rsp_error(1'b0),
        .p_rsp_rdata(rsp_data),.p_rsp_ready(p_rsp_ready));
    always @(posedge mem_clk) begin
        if(p_valid && p_ready) begin
            busy<=1;job_write<=p_write;job_addr<=p_addr;job_count<=p_count;
            job_data<=p_wdata;job_be<=p_be;accepted++;
        end
        if(busy && !rsp_valid && permit_reply) begin
            for(integer i=0;i<4;i++) if(i<job_count/2) begin
                if(job_write) begin
                    for(integer b=0;b<4;b++) if(job_be[4*i+b])
                        memory[job_addr/2+i][8*b+:8]<=job_data[32*i+8*b+:8];
                end
                rsp_data[32*i+:32]<=memory[job_addr/2+i];
            end
            rsp_valid<=1;committed++;
        end
        if(rsp_valid && p_rsp_ready) begin rsp_valid<=0;busy<=0;end
    end
    always @(posedge clk3x) begin
        if(fifo_read) fifo_empty<=1;
        if(cache_wr) begin
            if(cache_wr!==(4'b1<<cache_words) || cache_addr!==8'h4 ||
               cache_data!==(32'hf00d0000 | (16+cache_words)))
                $fatal(1,"Reset transport corrupted old cache fill");
            cache_words++;
        end
    end
    always @(posedge clk1x) begin
        cycles++;
        if(fault) $fatal(1,"Reset transport raised adapter fault");
        if(held && (ram_done || dma_wr || dma_ack))
            $fatal(1,"Reset transport leaked an old consumer response");
    end
    task cpu_wait(input integer n);repeat(n) @(negedge clk1x);endtask
    task launch(input bit dma,input bit cache,input integer count,input integer address);
        @(negedge clk1x);ram_dma=dma;ram_cache=cache;ram_cntDMA=2'(count);ram_adr=25'(address);ram_ena=1;
        @(negedge clk1x);ram_ena=0;
    endtask
    task pulse_reset;
        @(negedge clk1x);reset_request=1;
        @(negedge clk1x);reset_request=0;core_ce=0;
    endtask
    task check_held;
        if(!held) $fatal(1,"Reset transport restarted before accepted work drained");
    endtask
    integer before_jobs,before_commit;
    initial begin
        for(integer i=0;i<256;i++) memory[i]=32'hf00d0000|i;
        #100;reset_async=0;cpu_wait(12);
        // Four DMA lengths, a cache fill, a scalar write and a posted write.
        for(integer mode=0;mode<7;mode++) begin
            wait(source_idle && service_idle);cpu_wait(3);
            before_jobs=accepted;before_commit=committed;cache_words=0;
            permit_request=0;permit_reply=0;
            if(mode==6) begin
                @(negedge clk3x);fifo_empty=0;
            end else begin
                ram_rnw=mode!=5;
                launch(mode<4,mode==4,mode<4 ? mode:0,64);
            end
            wait(p_valid);pulse_reset();cpu_wait(12);check_held();
            if(accepted!=before_jobs) $fatal(1,"Reset transport target accepted while gated");
            @(negedge mem_clk);permit_request=1;
            wait(busy);cpu_wait(12);check_held();pulse_reset();cpu_wait(4);check_held();
            if(committed!=before_commit) $fatal(1,"Reset transport reported a stalled commit");
            @(negedge mem_clk);permit_reply=1;
            wait(!held);
            if(!source_idle || !service_idle || committed!=before_commit+1 || accepted!=before_jobs+1)
                $fatal(1,"Reset transport released without exact old-job drain");
            if(mode==4 && cache_words!=4) $fatal(1,"Reset transport released before final cache strobe");
            if(mode==5 && memory[16]!==32'h98765432) $fatal(1,"Reset transport lost accepted scalar write");
            if(mode==6 && memory[64]!==32'hcafebabe) $fatal(1,"Reset transport lost accepted posted write");
            // A new fetch must receive only its own word after reset. CE was
            // off during the entire old-return drain and fence handshake.
            core_ce=1;ram_rnw=1;cpu_wait(4);
            if(ram_done || dma_wr || dma_ack) $fatal(1,"Reset transport delivered an old response after restart");
            launch(0,0,0,128);wait(ram_done);
            if(ram_data!==32'hf00d0020) $fatal(1,"Reset transport confused new fetch with old data");
            cpu_wait(2);wait(source_idle && service_idle);
            $display("RESET_TRANSPORT_CASE mode=%0d accepted=%0d committed=%0d",mode,accepted,committed);
        end
        $display("PASS MAIN RESET TRANSPORT cycles=%0d cases=7",cycles);$finish;
    end
    initial begin #1000000;$fatal(1,"Reset transport deadline");end
endmodule
