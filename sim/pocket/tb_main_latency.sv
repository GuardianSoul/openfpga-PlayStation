// SPDX-License-Identifier: MIT
`timescale 1ns/1ps
// Synthetic normalized-packet backend for isolating adapter/CDC overhead.
// EXTRA is additional response delay, not a model of either memory chip.
// The minimum request->response handshake costs one memory-clock period.
module main_latency_backend #(parameter EXTRA=0, INIT=64) (
    input clk, reset,
    output reg initialized=0,
    input valid, write,
    output ready,
    input [24:0] address,
    input [3:0] halfwords,
    input [127:0] wdata,
    input [15:0] byte_en,
    output reg rsp_valid=0,
    input rsp_ready,
    output reg [127:0] rdata=0,
    output idle
);
    reg [15:0] memory[0:32767];
    integer startup=0, remaining=0;
    reg pending=0, fault_delay=0;
    initial begin
        for(integer i=0;i<32768;i++) memory[i]=0;
        fault_delay=$test$plusargs("FAULT_DELAY");
    end
    function automatic integer slot(input [24:0] a);
        if(a<25'h004000) slot=int'(a);
        else if(a>=25'h840000 && a<25'h844000) slot=16384+int'(a-25'h840000);
        else begin slot=0;$fatal(1,"Latency backend outside authored windows: %h",a);end
    endfunction
    assign ready=initialized && !reset && !pending && !rsp_valid;
    assign idle=initialized && !reset && !pending && !rsp_valid;
    always @(posedge clk) begin
        if(reset) begin
            initialized<=0;startup<=0;pending<=0;rsp_valid<=0;rdata<=0;
        end else if(!initialized) begin
            startup<=startup+1;
            if(startup==INIT-1) initialized<=1;
        end else begin
            if(rsp_valid && rsp_ready) rsp_valid<=0;
            if(pending) begin
                if(remaining==0) begin pending<=0;rsp_valid<=1;end
                else remaining<=remaining-1;
            end
            if(valid && ready) begin
                if(halfwords<1 || halfwords>8) $fatal(1,"Latency backend packet length");
                rdata<=0;
                for(integer i=0;i<8;i++) if(i<halfwords) begin
                    if(write) begin
                        if(byte_en[2*i]) memory[slot(address+25'(i))][7:0]<=wdata[16*i+:8];
                        if(byte_en[2*i+1]) memory[slot(address+25'(i))][15:8]<=wdata[16*i+8+:8];
                    end else rdata[16*i+:16]<=memory[slot(address+25'(i))];
                end
                if(EXTRA==0 && !fault_delay) rsp_valid<=1;
                else begin pending<=1;remaining<=EXTRA+int'(fault_delay)-1;end
            end
        end
    end
endmodule

// Simulation-only taps on actual handshake/state events, not predicted times.
module main_latency_monitor (
    input clk1x, clk3x, mem_clk, reset, measure,
    input source_request, scalar_return, dma_return, dma_ack,
    input service_take, packet_take, cdc_return, response_toggle,
    input backend_take, backend_return,
    input [3:0] cache_wr
);
    integer csv, index=-1;
    initial begin
        csv=$fopen("latency.csv","w");
        if(!csv) $fatal(1,"Cannot open main-latency event file");
        $fwrite(csv,"index,event,time_ps\n");
    end
    task automatic event_row(input string name);
        if(index<0) $fatal(1,"Latency event before native request");
        $fwrite(csv,"%0d,%s,%.0f\n",index,name,$realtime*1000.0);
    endtask
    always @(posedge clk1x) if(measure && !reset) begin
        if(source_request) begin index++;event_row("SOURCE");end
        if(scalar_return) event_row("SCALAR");
        if(dma_return) event_row("DMA");
        if(dma_ack) event_row("ACK");
    end
    always @(posedge clk3x) if(measure && !reset) begin
        if(service_take) event_row("SERVICE");
        if(packet_take) event_row("PACKET");
        if(cdc_return) event_row("CDC_RETURN");
        if(cache_wr!=0) event_row("CACHE");
    end
    // Observe actual publication, independent of the service FSM's state.
    always @(response_toggle) if(measure && !reset) event_row("PUBLISH");
    always @(posedge mem_clk) if(measure && !reset) begin
        if(backend_take) event_row("BACKEND");
        if(backend_return) event_row("BACKEND_RETURN");
    end
    final $fclose(csv);
endmodule
