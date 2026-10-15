// SPDX-License-Identifier: GPL-2.0-only
// Independent bytes for scalar traffic, aligned cache fills, DMA reads/writes.
`timescale 1fs/1fs
module tb_pocket_psram_model;
    parameter integer LANES=1, SHARED_BUS=-1;
    reg clk=0,clk3x=0;
    always #14762550 clk=~clk;
    always #4920850 clk3x=~clk3x;
    reg [26:0] address=0;
    reg request=0,dma=0,cache=0,reading=0;
    reg [1:0] dma_count=0;reg [3:0] mask=15;reg [31:0] input_data=0;
    wire [31:0] output_data,cache_data,dma_data;
    wire done,processed,dma_write,fifo_read;
    wire [3:0] cache_write;wire [7:0] cache_address;
    integer fifo_head=0,fifo_length=0;
    reg [22:0] fifo_address[0:31];reg [31:0] fifo_data[0:31];
    pocket_psram_model #(.MEMORY_BYTES(65536),.DOREFRESH(0),.CD_BUS_STRESS(1),
        .HALFWORDS_PER_TICK(LANES),.SHARED_BUS(SHARED_BUS)) dut(
        .clk(clk),.clk3x(clk3x),.refresh(1'b0),.addr(address),.req(request),.ram_dma(dma),
        .ram_dmacnt(dma_count),.ram_iscache(cache),.rnw(reading),.be(mask),.di(input_data),
        .do32(output_data),.done(done),.cache_wr(cache_write),.cache_data(cache_data),.cache_addr(cache_address),
        .dma_wr(dma_write),.dma_data(dma_data),.reqprocessed(processed),
        .ram_dmafifo_adr(fifo_address[fifo_head%32]),.ram_dmafifo_data(fifo_data[fifo_head%32]),
        .ram_dmafifo_empty(fifo_head>=fifo_length),.ram_dmafifo_read(fifo_read));
    reg [7:0] oracle[0:65535];
    reg [31:0] random_state=32'h43454c4c;
    integer cycles=0,checks=0,scalar=0,caches=0,dmas=0,cache_beats=0;
    reg capture_cache=0;integer expected_base=0;
    always @(posedge clk3x)begin
        cycles++;if(cycles>1200000)$fatal(1,"PSRAM model timeout");
        if(fifo_read)begin
            if(fifo_head>=fifo_length)$fatal(1,"FIFO popped when empty");
            fifo_head<=fifo_head+1;
        end
    end
    function automatic [31:0] next_random;
        begin random_state^=random_state<<13;random_state^=random_state>>17;random_state^=random_state<<5;next_random=random_state;end
    endfunction
    function automatic [31:0] word_at(input integer base);
        begin word_at={oracle[base+3],oracle[base+2],oracle[base+1],oracle[base]};end
    endfunction
    always @(negedge clk3x)if(capture_cache && cache_write!=0)begin
        if(cache_beats>=4 || cache_write!=(1<<cache_beats) || cache_address!=8'(expected_base>>4) ||
            cache_data!==word_at(expected_base+cache_beats*4))$fatal(1,"Cache fill mismatch beat=%0d",cache_beats);
        cache_beats++;checks++;
    end
    task automatic settle;
        begin request=0;capture_cache=0;repeat(8)@(negedge clk);end
    endtask
    task automatic scalar_transfer(input bit writing,input integer base,input [31:0] value,input [3:0] enables);
        begin
            @(negedge clk);while(done)@(negedge clk);
            address=27'(base);input_data=value;mask=enables;reading=!writing;dma=0;cache=0;request=1;
            @(negedge clk);while(!done)@(negedge clk);
            if(writing)for(integer b=0;b<4;b++)if(enables[b])oracle[base+b]=value[b*8+:8];
            if(!writing && output_data!==word_at(base))$fatal(1,"Scalar read mismatch addr=%x got=%x expected=%x",base,output_data,word_at(base));
            checks++;scalar++;settle();
        end
    endtask
    task automatic cache_fill(input integer base);
        begin
            settle();@(negedge clk);
            expected_base=base;cache_beats=0;capture_cache=1;
            address=27'(base);reading=1;dma=0;cache=1;request=1;
            @(negedge clk);while(!done)@(negedge clk);request=0;
            while(cache_beats<4)@(negedge clk3x);
            caches++;settle();
        end
    endtask
    task automatic dma_read(input integer base,input integer length);
        integer received;
        begin
            settle();@(negedge clk);address=27'(base);reading=1;cache=0;dma=1;dma_count=2'(length-1);request=1;
            @(negedge clk);while(!processed)@(negedge clk);
            request=0;address=0;dma_count=0;received=0;
            while(received<length)begin
                if(dma_write)begin
                    if(dma_data!==word_at(base+received*4))$fatal(1,"DMA read mismatch beat=%0d",received);
                    received++;checks++;
                end
                @(negedge clk);
            end
            dmas++;settle();
        end
    endtask
    integer base;reg [31:0] value;reg [3:0] enables;
    initial begin
        foreach(oracle[i])oracle[i]=0;
        repeat(8)@(negedge clk);
        for(integer trial=0;trial<256;trial++)begin
            base=int'(next_random()%16384)*4;value=next_random();enables=4'(trial);
            scalar_transfer(1,base,value,enables);scalar_transfer(0,base,0,0);
        end
        for(integer i=0;i<32;i++)begin
            fifo_address[i]=23'(4096+i*4);fifo_data[i]=next_random();
            for(integer b=0;b<4;b++)oracle[4096+i*4+b]=fifo_data[i][b*8+:8];
        end
        // Publish availability after the array's combinational output has
        // settled; the first word must be valid before the accepting edge.
        @(negedge clk3x);fifo_length=32;
        wait(fifo_head==32);repeat(100)@(negedge clk3x);
        for(integer i=0;i<32;i++)scalar_transfer(0,4096+i*4,0,0);
        for(integer i=0;i<32;i++)begin
            cache_fill(4096+(i%8)*16);dma_read(4096+(i%8)*16,(i%4)+1);
        end
        // Let raw 2x-CD contention arrive and drain on otherwise idle cycles.
        repeat(700000)@(negedge clk3x);
        if(dut.cd_pending!=0 || dut.serviced_bytes<5000)$fatal(1,"CD budget did not drain");
        $display("PASS PSRAM model scalar=%0d cache_fills=%0d dma_bursts=%0d fifo_writes=%0d comparisons=%0d bytes=%0d busy=%0d",scalar,caches,dmas,fifo_head,checks,dut.serviced_bytes,dut.occupied_cycles);
        $finish;
    end
endmodule
