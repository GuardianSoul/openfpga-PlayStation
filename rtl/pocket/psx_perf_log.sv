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
// VIDEO=1 (--video-log) makes the record nine words and sets header word 1 bits
// 30 and 29: 4-7 are psx_apf_video_wide's LOG words W4-W7 of the last finished
// video frame, 8 is {frame number [19:0], 10'b0, alternating, heartbeat}. A
// frame is recorded only when its video words (all but the content hash) differ
// from both of the two frames before it (a picture that alternates between two states stores both,
// then stops), and every 256th frame as a heartbeat, so the ring keeps a whole
// session's screens (BIOS through game) instead of its last few seconds.
// "alternating" is set when the words or the hash differ from the frame before;
// on a heartbeat record that means the picture is still changing every frame.
`default_nettype none
module psx_perf_log #(parameter integer FRAMES=1023, parameter integer STAGES=0,
                       parameter integer VIDEO=0, parameter integer TAG=0) (
    input wire clk1x, clk2x,
    input wire clk3x,
    input wire [2:0] stage,           // clk3x (STAGES=1)
    input wire vsync,                 // any clock; level
    input wire ram_ena, ram_done,     // clk1x
    input wire spu_ena, spu_done,     // clk1x
    input wire sample,                // clk1x, one per SPU output frame
    input wire vram_rd, vram_we, vram_busy, vram_ready, // clk2x
    input wire [31:0] status,         // any clock, diagnostics (header word 2)
    // VIDEO!=0: four per-frame words from the APF video module (psx_apf_video_wide
    // LOG), latched at its own vs_edge, and video_seq, which toggles with them. The
    // words come through two clk1x stages (prepare --video-log); they are taken
    // only once video_seq has passed four, so they have settled by then, and stay
    // put for the rest of the frame.
    input wire video_seq,             // any clock; level
    input wire [31:0] video_w4, video_w5, video_w6, video_w7,
    input wire read_clk,
    input wire [12:0] read_index,
    output reg [31:0] read_data
);
    localparam [3:0] PHASES=STAGES!=0 ? 4'd8 : VIDEO!=0 ? 4'd9 : 4'd4;
    localparam integer RING_WORDS=PHASES*FRAMES+4;
    localparam [9:0] FRAMES_M1=FRAMES-1;
    // Sized so the ring only holds what is actually addressed (the 0.5.5 fit is
    // short of RAM blocks): --video-log's 9 words x 227 records + 4 is 2047 words,
    // eight 256x40 M10K blocks.
    (* ramstyle="M10K" *) reg [31:0] ring[0:RING_WORDS-1];

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
    // VIDEO: the last finished video frame's words and the record decision
    (* async_reg="true" *) reg [3:0] vq=0;
    always @(posedge clk1x) vq<={vq[2:0],video_seq};
    reg vseen=0;   // video_seq as last taken; a take waits for no record being written
    wire vtake=VIDEO!=0 && vq[3]!=vseen && phase==0;
    reg [31:0] v4=0, v5=0, v6=0, v7=0;
    reg [15:0] sig1=0, sig2=0;
    reg [9:0] hash1=0;
    reg keep=1'b1, alt=0, rec_alt=0, rec_beat=0;
    reg [19:0] frame_no=0, rec_frame=0;
    // the content hash (W4 21:12) is left out: moving pictures would store every
    // frame (probe4 gameplay filled the ring and lost the BIOS records)
    wire [31:0] w4_geo=video_w4&~32'h003FF000;
    wire [15:0] sig=w4_geo[31:16]^w4_geo[15:0]^video_w5[31:16]^video_w5[15:0]
                   ^video_w6[31:16]^video_w6[15:0]^video_w7[31:16]^{video_w7[15:0]};
    wire [19:0] busy_delta=busy2-busy_last, wait_delta=wait2-wait_last;
    always @(posedge clk1x) begin
        if (ram_ena) ram_pending<=1'b1; else if (ram_done) ram_pending<=1'b0;
        if (spu_ena) spu_pending<=1'b1; else if (spu_done) spu_pending<=1'b0;
        if (phase!=0) begin
            case (PHASES-phase)
                4'd0: ring[wr_addr]<={samples, 2'b0, cycles};
                4'd1: ring[wr_addr]<={ram_reqs[11:0], ram_wait};
                4'd2: ring[wr_addr]<={ram_reqs[23:12], spu_wait};
                // probe2 wrote zeros here: Verilog case takes the FIRST matching item, so the
                // three VIDEO items added under these had no effect - with STAGES=0 the
                // first item's if(STAGES!=0) was false and nothing was written at all,
                // leaving the M10K's power-on zero in the ring (hardware log 2026-10-21).
                // VIDEO!=0: geometry of the frame that just ended (psx_apf_video_wide LOG).
                4'd4: if (STAGES!=0) begin ring[wr_addr]<={10'b0, stage3[0]-stage_last[0]}; stage_last[0]<=stage3[0]; end else if (VIDEO!=0) ring[wr_addr]<=v4;
                4'd5: if (STAGES!=0) begin ring[wr_addr]<={10'b0, stage3[1]-stage_last[1]}; stage_last[1]<=stage3[1]; end else if (VIDEO!=0) ring[wr_addr]<=v5;
                4'd6: if (STAGES!=0) begin ring[wr_addr]<={10'b0, stage3[2]-stage_last[2]}; stage_last[2]<=stage3[2]; end else if (VIDEO!=0) ring[wr_addr]<=v6;
                4'd7: if (STAGES!=0) ring[wr_addr]<=32'b0; else if (VIDEO!=0) ring[wr_addr]<=v7;
                4'd8: if (VIDEO!=0) ring[wr_addr]<={rec_frame, 10'b0, rec_alt, rec_beat};
                default: begin
                    ring[wr_addr]<={busy_delta[19:4], wait_delta[19:4]};
                    busy_last<=busy2; wait_last<=wait2;
                end
            endcase
            wr_addr<=wr_addr+1'b1;
            phase<=phase-1'b1;
            if (phase==1) begin cycles<=0; ram_wait<=0; spu_wait<=0; samples<=0; ram_reqs<=0; end
        end else if (tick && (VIDEO==0 || keep || frame_no[7:0]==8'd0)) begin
            phase<=PHASES; wr_addr<=slot*PHASES;
            slot<=slot==FRAMES-1 ? 10'd0 : slot+1'b1;
            recorded<=recorded+1'b1;
            frame_no<=frame_no+1'b1; rec_frame<=frame_no; rec_alt<=alt; rec_beat<=!keep; keep<=0;
        end else if (tick) begin   // VIDEO: an unchanged frame is counted, not stored
            frame_no<=frame_no+1'b1;
            cycles<=0; ram_wait<=0; spu_wait<=0; samples<=0; ram_reqs<=0;
            busy_last<=busy2; wait_last<=wait2;
        end else begin
            cycles<=cycles+1'b1;
            if (ram_pending || ram_ena) ram_wait<=ram_wait+1'b1;
            if (ram_ena) ram_reqs<=ram_reqs+1'b1;
            if (spu_pending || spu_ena) spu_wait<=spu_wait+1'b1;
            if (sample) samples<=samples+1'b1;
        end
        // VIDEO: take the finished frame's words; a frame unlike both of the two
        // before it is kept for the next record. This follows the tick logic, so a
        // take in the tick's clock still sets keep.
        if (vtake) begin
            vseen<=vq[3]; v4<=video_w4; v5<=video_w5; v6<=video_w6; v7<=video_w7;
            if (sig!=sig1 && sig!=sig2) keep<=1'b1;
            alt<=sig!=sig1 || video_w4[21:12]!=hash1;
            hash1<=video_w4[21:12];
            sig2<=sig1; sig1<=sig;
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
            2'd1: read_data={STAGES!=0, VIDEO!=0, VIDEO!=0, 13'b0, recorded};
            2'd2: read_data=status;
            // word 3: tag (29:20), FRAMES-1 (19:10), slot (9:0) - the reader must not
            // have to guess the ring size: --video-log builds use a shorter ring.
            default: read_data={2'b10, TAG[9:0], FRAMES_M1[9:0], slot};
        endcase
    end
endmodule
`default_nettype wire
