// SPDX-License-Identifier: GPL-2.0-only
// Independent byte oracle, payload capture and refresh-collision checks.
`timescale 1ns/1ps
module tb_pocket_vram_model;
    parameter integer MEM_HZ=125000000, CORE_HZ=67737600, LANES=1;
    reg clk=0; always #5 clk=~clk;
    reg [7:0] count=1, mask=255;
    reg [28:0] address=0;
    reg [63:0] input_data=0;
    reg read=0,write=0;
    wire busy,ready;wire [63:0] output_data;
    pocket_vram_model #(.MEM_HZ(MEM_HZ),.CORE_HZ(CORE_HZ),.HALFWORDS_PER_TICK(LANES),
        .MEMORY_WORDS(4096),.REFRESH_TICKS(32)) dut(
        .clk(clk),.busy(busy),.burstcnt(count),.addr(address),.dout(output_data),
        .ready(ready),.rd(read),.din(input_data),.be(mask),.we(write));
    reg [7:0] oracle[0:16383];
    reg [31:0] random_state=32'h5652414d;
    integer checks=0,transfers=0,cycles=0;
    reg [63:0] bytes_expected=0;
    always @(posedge clk) begin
        cycles++;
        if(cycles>500000) $fatal(1,"VRAM model timeout");
    end
    function automatic [31:0] next_random;
        begin
            random_state^=random_state<<13;random_state^=random_state>>17;random_state^=random_state<<5;
            next_random=random_state;
        end
    endfunction
    task automatic request(input bit writing,input integer base,input integer words,
        input reg [63:0] value,input reg [7:0] enables);
        integer returned,timeout;
        reg [63:0] expected_value;
        begin
            @(negedge clk);while(busy) @(negedge clk);
            address=29'(base);count=8'(words);input_data=value;mask=enables;
            write=writing;read=!writing;
            @(negedge clk);
            if(!busy) $fatal(1,"Request was not accepted");
            write=0;read=0;
            // Corrupt the incoming payload after acceptance. Only its captured
            // copy may be used for a later beat or completion.
            address=0;count=1;input_data=~value;mask=~enables;
            returned=0;timeout=0;
            if(writing)
                for(integer i=0;i<words;i++)for(integer lane=0;lane<8;lane++)
                    if(enables[lane])oracle[base*8+i*8+lane]=value[lane*8+:8];
            while(busy) begin
                if(ready) begin
                    if(writing || returned>=words) $fatal(1,"Unexpected read beat");
                    expected_value=0;
                    for(integer lane=0;lane<8;lane++)expected_value[lane*8+:8]=oracle[base*8+returned*8+lane];
                    if(output_data!==expected_value)$fatal(1,"VRAM byte mismatch transaction=%0d beat=%0d got=%x expected=%x",transfers,returned,output_data,expected_value);
                    returned++;checks++;
                end
                @(negedge clk);timeout++;
                if(timeout>10000)$fatal(1,"Transaction or queued refresh did not complete");
            end
            if(!writing && returned!=words)$fatal(1,"Lost read beat");
            // A burst cannot consume more x16/x32 bus bytes than the available
            // physical memory ticks, even with ideal row/refresh scheduling.
            if(64'(timeout+1)*MEM_HZ*LANES < 64'(words)*4*CORE_HZ)
                $fatal(1,"Memory bus bandwidth budget violated");
            transfers++;bytes_expected+=64'(words)*8;
        end
    endtask
    integer base,length;
    reg [63:0] value;
    initial begin
        foreach(oracle[i])oracle[i]=0;
        repeat(5)@(negedge clk);
        // Exhaust byte enables and exercise lengths and a 1 KiB row boundary.
        for(integer m=0;m<256;m++)begin
            value={next_random(),next_random()};
            request(1,127,4,value,8'(m));request(0,127,4,0,0);
        end
        for(integer trial=0;trial<256;trial++)begin
            length=1+int'(next_random()%32);base=int'(next_random()%(2048-length));
            value={next_random(),next_random()};
            request(1,base,length,value,8'(next_random()));request(0,base,length,0,0);
        end
        request(1,126,255,64'h0123456789abcdef,255);request(0,126,255,0,0);
        if(dut.serviced_bytes!=bytes_expected || dut.requests!=transfers || dut.refreshes<100 || dut.row_misses<100)
            $fatal(1,"Missing service/refresh/row coverage");
        $display("PASS VRAM model transfers=%0d read_beats=%0d bytes=%0d refresh=%0d rows=%0d mem_hz=%0d lanes=%0d cycles=%0d",transfers,checks,dut.serviced_bytes,dut.refreshes,dut.row_misses,MEM_HZ,LANES,cycles);
        $finish;
    end
endmodule
