`timescale 1ns/1ps
// Behavioral FIFO contract used for the loader test only. Quartus builds use
// Intel scfifo. This does not claim to validate vendor CDC or physical I/O.
module scfifo #(parameter lpm_width=49,lpm_numwords=256,lpm_widthu=8,
    lpm_showahead="ON",use_eab="ON",add_ram_output_register="OFF",
    overflow_checking="ON",underflow_checking="ON") (
    input clock,sclr, input [lpm_width-1:0] data, input wrreq,rdreq,
    output [lpm_width-1:0] q, output empty,full);
    reg [lpm_width-1:0] ram[0:lpm_numwords-1];
    integer head=0,tail=0,count=0;
    assign q=ram[head],empty=count==0,full=count==lpm_numwords;
    always @(posedge clock) if(sclr) begin head<=0;tail<=0;count<=0;end
    else begin
        if(wrreq) begin
            if(full) $fatal(1,"FIFO overflow");
            ram[tail]<=data;tail<=(tail+1)%lpm_numwords;
        end
        if(rdreq) begin
            if(empty) $fatal(1,"FIFO underflow");
            head<=(head+1)%lpm_numwords;
        end
        count<=count+wrreq-rdreq;
    end
endmodule

module tb_bios_probe;
    parameter CORRUPT=0;
    reg clk=0,reset=1,bridge_wr=0,allcomplete=0;
    reg [31:0] bridge_addr=0,bridge_data=0;
    wire req_valid,req_write,req_ready,rsp_ready;
    wire [24:0] req_addr;wire [127:0] req_data;wire [15:0] req_be;
    reg rsp_valid=0;reg [127:0] rsp_data=0;
    wire [17:0] loaded_words,checked_words;
    wire [31:0] write_crc,read_crc;
    wire passed,failed,checking;
    psx_bios_probe dut(.clk(clk),.reset(reset),.bridge_wr(bridge_wr),.bridge_addr(bridge_addr),
        .bridge_data(bridge_data),.allcomplete(allcomplete),.req_valid(req_valid),.req_write(req_write),
        .req_ready(req_ready),.req_addr(req_addr),.req_data(req_data),.req_be(req_be),
        .rsp_valid(rsp_valid),.rsp_error(1'b0),.rsp_ready(rsp_ready),.rsp_data(rsp_data),
        .loaded_words(loaded_words),.checked_words(checked_words),.write_crc(write_crc),.read_crc(read_crc),
        .passed(passed),.failed(failed),.checking(checking));
    always #5 clk=~clk;
    reg [31:0] memory[0:131071];
    integer delay_left=0,ticks=0,writes=0,reads=0;
    reg memory_busy=0;
    assign req_ready=!memory_busy && ticks%7!=0;
    always @(posedge clk) begin
        ticks<=ticks+1;
        if(!reset) begin
            if(req_valid && req_ready) begin
                if(req_addr[24:18]!=0 || req_addr[0] || req_be!=16'h000f) $fatal(1,"Memory request contract");
                memory_busy<=1;delay_left<=1+(req_addr%19);
                if(req_write) begin
                    if(req_addr/2!=writes || req_data[31:0]!=(32'ha5c35a3c ^ writes)) $fatal(1,"Loader data/order");
                    memory[req_addr/2]<=req_data[31:0];writes<=writes+1;
                    rsp_data<=0;
                end else begin
                    if(req_addr/2!=reads) $fatal(1,"Readback order");
                    rsp_data<={96'b0,memory[req_addr/2] ^ (CORRUPT && req_addr/2==77777 ? 32'h10000:32'b0)};
                    reads<=reads+1;
                end
            end else if(memory_busy && !rsp_valid) begin
                if(delay_left==0) rsp_valid<=1; else delay_left<=delay_left-1;
            end
            if(rsp_valid && rsp_ready) begin rsp_valid<=0;memory_busy<=0;end
        end
        if(ticks>9000000) $fatal(1,"Probe timeout");
    end
    initial begin
        repeat(5) @(negedge clk);reset=0;
        for(integer i=0;i<131072;i=i+1) begin
            bridge_addr=i*4;bridge_data=32'ha5c35a3c ^ i;bridge_wr=1;
            @(negedge clk);bridge_wr=0;
            repeat(31) @(negedge clk);
        end
        allcomplete=1;@(negedge clk);allcomplete=0;
        wait(checked_words==131072);repeat(3) @(negedge clk);
        if(writes!=131072 || reads!=131072 || loaded_words!=131072) $fatal(1,"Incomplete whole BIOS check");
        if(CORRUPT ? passed || !failed : !passed || failed || write_crc!=read_crc) $fatal(1,"Readback result");
        $display("PASS BIOS PROBE corrupt=%0d writes=%0d reads=%0d crc=%08x ticks=%0d",CORRUPT,writes,reads,write_crc^32'hffffffff,ticks);
        $finish;
    end
endmodule
