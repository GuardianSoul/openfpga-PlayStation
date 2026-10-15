// SPDX-License-Identifier: GPL-2.0-only
// Two simultaneous cache fills must consume one paired physical bus.
`timescale 1fs/1fs
module tb_pocket_psram_bus;
    parameter integer SHARED_BUS=0;
    reg clk=0,clk3x=0;
    always #14762550 clk=~clk;
    always #4920850 clk3x=~clk3x;
    reg [1:0] request=0;
    wire [1:0] done;
    wire [3:0] cache_write[0:1];
    wire [31:0] cache_data[0:1];
    integer cycles=0,start_cycle=0,completed[0:1],beats[0:1];
    localparam REQUIRED=(17*64'd101606400+61875000-1)/61875000;
    for(genvar i=0;i<2;i++)begin:clients
        pocket_psram_model #(.MEMORY_BYTES(64),.DOREFRESH(0),
            .HALFWORDS_PER_TICK(2),.SHARED_BUS(SHARED_BUS)) memory(
            .clk(clk),.clk3x(clk3x),.refresh(1'b0),.addr(27'b0),
            .req(request[i]),.ram_dma(1'b0),.ram_dmacnt(2'b0),
            .ram_iscache(1'b1),.rnw(1'b1),.be(4'b0),.di(32'b0),
            .do32(),.done(done[i]),.cache_wr(cache_write[i]),
            .cache_data(cache_data[i]),.cache_addr(),.dma_wr(),
            .dma_data(),.reqprocessed(),.ram_dmafifo_adr(23'b0),
            .ram_dmafifo_data(32'b0),.ram_dmafifo_empty(1'b1),.ram_dmafifo_read());
    end
    always @(posedge clk3x)begin
        cycles++;
        if(cycles>300)$fatal(1,"Shared PSRAM bus timeout");
    end
    always @(negedge clk3x)begin
        for(integer i=0;i<2;i++)if(cache_write[i]!=0)begin
            if(cache_write[i]!=(1<<beats[i]) || cache_data[i]!=0)
                $fatal(1,"Shared bus cache beat mismatch");
            beats[i]++;
        end
    end
    always @(negedge clk)for(integer i=0;i<2;i++)if(done[i] && completed[i]==0)begin
        completed[i]=cycles;request[i]=0;
    end
    integer first_cycle,last_cycle;
    initial begin
        for(integer i=0;i<2;i++)begin completed[i]=0;beats[i]=0;end
        repeat(5)@(negedge clk);
        start_cycle=cycles;request=3;
        wait(completed[0]>0 && completed[1]>0);
        wait(beats[0]==4 && beats[1]==4);
        first_cycle=completed[0]<completed[1]?completed[0]:completed[1];
        last_cycle=completed[0]>completed[1]?completed[0]:completed[1];
        // Two packets each cost 7 first-access + 6 CDC + 4 x32 payload ticks.
        // Completion sampling may differ by up to one clk1x (three clk3x).
        if(last_cycle-start_cycle<2*REQUIRED || last_cycle-first_cycle<REQUIRED-3)
            $fatal(1,"Shared PSRAM bus budget violated first=%0d last=%0d",first_cycle-start_cycle,last_cycle-start_cycle);
        $display("PASS shared PSRAM bus fills=2 read_beats=8 first_ticks=%0d last_ticks=%0d minimum_packet_ticks=%0d",
            first_cycle-start_cycle,last_cycle-start_cycle,REQUIRED);
        $finish;
    end
endmodule
