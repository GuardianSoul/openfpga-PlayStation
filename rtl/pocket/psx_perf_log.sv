// SPDX-License-Identifier: MIT
// Per-frame memory-wait counters for Pocket slowdown measurements, saved by
// the Pocket from bridge 0x40000000 as a nonvolatile slot when the core quits.
//
// At each vsync (from the video clock, synchronised to clk1x) the counts of
// the frame that just ended go into a ring of FRAMES records of four words:
//   0: {SPU output samples[9:0], 2'b0, clk1x cycles in the frame [19:0]}
//   1: {main-RAM requests [11:0], clk1x cycles with a main-RAM request
//      outstanding (ram_ena .. ram_done) [19:0]}
//   2: {main-RAM requests [23:12], clk1x cycles with an SPU-RAM request
//      outstanding (spuram_ena .. done) [19:0]} (requests: ram_ena pulses)
//   3: {clk2x cycles VRAM busy / 16 [15:0], clk2x cycles waiting for the
//      first word of a VRAM read / 16 [15:0]}
// Words 0-3 of the file are a header: "PERF", frames recorded (16 bits),
// a status word from the top (boot/memory flags), next record slot. Record r is at word 4 + 4*r. The bridge
// returns each word one read late (scripts/decode_perf_log.py undoes it).
// clk2x counters run free and are sampled at the frame tick from clk1x (the
// clocks come from one PLL), so only deltas are stored.
// STAGES=1 (CPU-synchronous PSRAM builds) doubles the record to eight words
// (FRAMES must then be at most 511) and sets header word 1 bit 31:
//   4: clk3x cycles the main adapter is not idle (stage[0])
//   5: clk3x cycles the PSRAM pair spends on reads (stage[1])
//   6: clk3x cycles the PSRAM pair spends on writes (stage[2])
//   7: reserved (0)
// stage[] is on clk3x, which comes from the same PLL as clk1x.
`default_nettype none
module psx_perf_log #(parameter integer FRAMES=1023, parameter integer STAGES=0) (
    input wire clk1x, clk2x,
    input wire clk3x,
    input wire [2:0] stage,           // clk3x (STAGES=1)
    input wire vsync,                 // any clock; level
    input wire ram_ena, ram_done,     // clk1x
    input wire spu_ena, spu_done,     // clk1x
    input wire sample,                // clk1x, one per SPU output frame
    input wire vram_rd, vram_we, vram_busy, vram_ready, // clk2x
    input wire [31:0] status,         // any clock, diagnostics (header word 2)
    input wire read_clk,
    input wire [12:0] read_index,
    output reg [31:0] read_data
);
    (* ramstyle="M10K" *) reg [31:0] ring[0:4095];

    // clk2x side: free-running 20-bit counts; clk1x stores per-frame deltas.
    reg [19:0] busy2=0, wait2=0;
    reg read_pending=0;
    always @(posedge clk2x) begin
        if (vram_busy && (vram_rd || vram_we)) busy2<=busy2+1'b1;
        if (vram_rd && !vram_busy) read_pending<=1'b1;
        else if (vram_ready) read_pending<=1'b0;
        if (read_pending && !vram_ready) wait2<=wait2+1'b1;
    end

    // 22 bits: a frame has under 2^21 clk3x cycles, so deltas cannot alias.
    reg [21:0] stage3[0:2];
    initial begin stage3[0]=0; stage3[1]=0; stage3[2]=0; end
    always @(posedge clk3x) begin
        if (stage[0]) stage3[0]<=stage3[0]+1'b1;
        if (stage[1]) stage3[1]<=stage3[1]+1'b1;
        if (stage[2]) stage3[2]<=stage3[2]+1'b1;
    end
    reg [21:0] stage_last[0:2];
    initial begin stage_last[0]=0; stage_last[1]=0; stage_last[2]=0; end
    localparam [3:0] PHASES=STAGES!=0 ? 4'd8 : 4'd4;

    // clk1x side. A tick starts four (eight) write cycles (counting pauses for
    // them), then the frame counters restart.
    reg [2:0] vs_s=0;
    always @(posedge clk1x) vs_s<={vs_s[1:0],vsync};
    wire tick=vs_s[1] && !vs_s[2];
    reg ram_pending=0, spu_pending=0;
    reg [19:0] cycles=0, ram_wait=0, spu_wait=0, busy_last=0, wait_last=0;
    reg [9:0] samples=0;
    reg [23:0] ram_reqs=0;
    reg [3:0] phase=0;
    reg [11:0] wr_addr=0;
    reg [9:0] slot=0;
    reg [15:0] recorded=0;
    wire [19:0] busy_delta=busy2-busy_last, wait_delta=wait2-wait_last;
    always @(posedge clk1x) begin
        if (ram_ena) ram_pending<=1'b1; else if (ram_done) ram_pending<=1'b0;
        if (spu_ena) spu_pending<=1'b1; else if (spu_done) spu_pending<=1'b0;
        if (phase!=0) begin
            case (PHASES-phase)
                4'd0: ring[wr_addr]<={samples, 2'b0, cycles};
                4'd1: ring[wr_addr]<={ram_reqs[11:0], ram_wait};
                4'd2: ring[wr_addr]<={ram_reqs[23:12], spu_wait};
                4'd4: begin ring[wr_addr]<={10'b0, stage3[0]-stage_last[0]}; stage_last[0]<=stage3[0]; end
                4'd5: begin ring[wr_addr]<={10'b0, stage3[1]-stage_last[1]}; stage_last[1]<=stage3[1]; end
                4'd6: begin ring[wr_addr]<={10'b0, stage3[2]-stage_last[2]}; stage_last[2]<=stage3[2]; end
                4'd7: ring[wr_addr]<=32'b0;
                default: begin
                    ring[wr_addr]<={busy_delta[19:4], wait_delta[19:4]};
                    busy_last<=busy2; wait_last<=wait2;
                end
            endcase
            wr_addr<=wr_addr+1'b1;
            phase<=phase-1'b1;
            if (phase==1) begin cycles<=0; ram_wait<=0; spu_wait<=0; samples<=0; ram_reqs<=0; end
        end else if (tick) begin
            phase<=PHASES; wr_addr<=STAGES!=0 ? {slot[8:0],3'b000} : {slot,2'b00};
            slot<=slot==FRAMES-1 ? 10'd0 : slot+1'b1;
            recorded<=recorded+1'b1;
        end else begin
            cycles<=cycles+1'b1;
            if (ram_pending || ram_ena) ram_wait<=ram_wait+1'b1;
            if (ram_ena) ram_reqs<=ram_reqs+1'b1;
            if (spu_pending || spu_ena) spu_wait<=spu_wait+1'b1;
            if (sample) samples<=samples+1'b1;
        end
    end

    // bridge side (recorded is static by the time the Pocket saves)
    reg [31:0] q=0;
    reg header=0;
    reg [1:0] hsel=0;
    wire [12:0] ring_index=read_index-13'd4;
    always @(posedge read_clk) begin
        q<=ring[ring_index[11:0]];
        header<=read_index<4;
        hsel<=read_index[1:0];
    end
    always @* begin
        read_data=q;
        if (header) case (hsel)
            2'd0: read_data=32'h50455246;
            2'd1: read_data={STAGES!=0, 15'b0, recorded};
            2'd2: read_data=status;
            default: read_data={22'b0,slot};
        endcase
    end
endmodule
`default_nettype wire
