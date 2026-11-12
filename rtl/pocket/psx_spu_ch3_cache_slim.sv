// SPDX-License-Identifier: MIT
// Slim SPU RAM read cache on psx_sdram_ctrl ch3 (Native layout), all in
// clk1x (prepare --spu-cache --spu-cache-slim). Same ports as
// psx_spu_ch3_cache.sv (hardware FFVII intro: 0 SPU deadline misses with
// --spu-free-slot) for a fraction of its area, for the Native release recipe:
// - direct mapped, 2^SET_BITS lines of four 32-bit words (one ADPCM block);
// - word granular: a read miss reads only its own word (one ch3 access, as
//   the direct path), answers, and marks that word present; nothing is
//   filled in the background. The tag and the four present bits share one
//   MLAB entry, so the only flip-flops per line are its valid bits;
// - a read hit answers in two clk1x;
// - writes go to ch3 as on the direct path (done when ch3 is) and update a
//   present cached word; there is no posted-write path.
// An ADPCM block is a header halfword and seven data halfwords read in
// order, the header every tick, so after one ch3 read per word the voice
// hits until it moves to the next block.
// spu_ram holds address, data and direction from its request until done
// (spu.vhd waits in the request's state), so only the request is latched.
// VOICE_SETS=1 (prepare_spu_voice_sets.py): a voice's reads use its voice
// number as the set, so voices never evict each other; other reads use set
// 31 (voices are 0-23), and every write clears the cache (a written word may
// sit in any voice's set). spu.vhd reads each voice's block header every
// tick: with the address hash, two voices on one set evicted each other's
// header and data every tick and the SPU fell behind (tb_spu_budget).
// VOICE_SETS=2: the same sets, but a write clears only the sets that may
// hold its block (one SPU RAM write per sample kept VOICE_SETS=1 at 7 % hits:
// docs/spu-voice-sets.md). owners[] has one entry per bucket (1 KiB of SPU
// RAM: the address's top OWN_BITS bits) with one bit per set number modulo 16
// (voices 16-23 share a bit with voices 0-7). A fill of a set below 31 sets
// its bit in its block's bucket; a write clears the valid bits of its
// bucket's sets and then the bucket. So such a valid line's bit is always set
// in its block's bucket, whatever owners[] held before (after a reset, or
// stale bits of lines since refilled: those only clear more than needed). A
// bucket is a region, not a hash: a voice marks only the regions it plays, so
// writes elsewhere (a stream buffer, a new sample, the reverb area) leave its
// line alone however long it has played. Set 31 is not in the table: a write
// always looks at set 31 and clears it when it holds the written block. There
// is no update in place. Needs SET_BITS=5.
// POST_WRITE=1 (with VOICE_SETS=2): a write is answered one clock after it is
// taken, when its lines are cleared, and ch3 finishes it while the SPU goes
// on; the next request waits until ch3 has answered, so reads and writes
// reach SPU RAM in the SPU's order. spu.vhd writes up to 24 transfer
// halfwords per sample, each held for the whole ch3 write before.
// CHEAT=1 (prepare --cheats): a second requester for main RAM halfwords
// and bytes (the cheat firmware in psx_cue_mount). It is taken only when no
// SPU request is pending, holds its fields until ch_done, and is answered
// at once (ch_q 0) during a guest reset, when ch3 belongs to the BIOS loader.
`default_nettype none
module psx_spu_ch3_cache #(parameter integer SET_BITS = 5, parameter CHEAT = 0, parameter VOICE_SETS = 0, parameter POST_WRITE = 0) (
    input wire clk1x, reset, discard,
    input wire spu_req, spu_rnw,
    input wire [4:0] spu_voice, input wire spu_isvoice,   // VOICE_SETS: the request's voice (spu_isvoice with spu_req)
    input wire [18:0] spu_addr, input wire [31:0] spu_data, input wire [3:0] spu_be,
    output reg spu_done=0, output reg [31:0] spu_q=0,
    output reg ch3_req=0, output reg ch3_rnw=0,
    output reg [26:0] ch3_addr=0, output reg [31:0] ch3_din=0, output reg [3:0] ch3_be=0,
    input wire ch3_ready, input wire [31:0] ch3_dout,
    output reg stat_access=0, output reg stat_hit=0,  // one-clock pulses per SPU read / read hit
    // CHEAT: main RAM byte address, a byte (else a halfword) access
    input wire ch_req, ch_rnw, ch_byte, input wire [20:0] ch_addr, input wire [15:0] ch_data,
    output reg ch_done=0, output reg [15:0] ch_q=0
);
    localparam integer LINES = 1 << SET_BITS;
    (* ramstyle="MLAB, no_rw_check" *) reg [18:0] tags [0:LINES-1];   // {tag, present words}
    (* ramstyle="M10K, no_rw_check" *) reg [3:0][7:0] lines [0:4*LINES-1];
    reg [LINES-1:0] valid=0;

    // VOICE_SETS: spu_isvoice comes with spu_req only; the voice number holds
    // until done (spu.vhd waits in the voice's state)
    reg isvoice_q=0;
    always @(posedge clk1x) if (spu_req) isvoice_q<=spu_isvoice;
    wire isvoice=spu_req ? spu_isvoice : isvoice_q;
    wire [SET_BITS-1:0] set_hash=spu_addr[4+:SET_BITS]^spu_addr[4+SET_BITS+:SET_BITS]^(spu_addr[18:4]>>(2*SET_BITS));
    wire [SET_BITS-1:0] set_a=VOICE_SETS==0 ? set_hash : isvoice && (VOICE_SETS!=2 || spu_rnw) ? spu_voice[SET_BITS-1:0] : {SET_BITS{1'b1}};
    wire [18:0] entry=tags[set_a];
    wire line_ok=valid[set_a] && entry[18:4]==spu_addr[18:4];
    wire [3:0] word_bit=4'b0001<<spu_addr[3:2];
    wire hit=line_ok && |(entry[3:0] & word_bit);

    localparam E_IDLE=0, E_HIT=1, E_DEMAND=2, E_WRITE=3, E_REDO=4, E_CHEAT=5;
    reg [2:0] eng=E_IDLE;
    reg pend=0, redo=0, ch_pend=0;
    localparam POST = POST_WRITE!=0 && VOICE_SETS==2;
    reg wr_ack=0;   // POST: the write in E_WRITE has been answered
    wire wr_done=eng==E_WRITE && (POST ? !wr_ack : ch3_ready);   // answer the write and clear its lines now

    // Data RAM: one write port (read-miss words, write updates) and one
    // read port that reads the request's word every clock, so a hit's word
    // is out in E_HIT. Writes happen when ch3 answers, so E_IDLE never sees
    // a word being written.
    reg [3:0][7:0] q;
    wire fill_we=ch3_ready && eng==E_DEMAND && !discard;
    wire upd_we=ch3_ready && eng==E_WRITE && !discard && hit && VOICE_SETS!=2;
    wire [3:0] upd_be=spu_addr[1] ? {spu_be[1:0],2'b00} : {2'b00,spu_be[1:0]};
    wire fill_d=fill_we || VOICE_SETS==2;   // VOICE_SETS=2 has no update: the data are ch3's, without a select
    always @(posedge clk1x) begin
        if (fill_we || upd_we) begin
            if (fill_we || upd_be[0]) lines[{set_a,spu_addr[3:2]}][0]<=fill_d ? ch3_dout[7:0]   : spu_data[7:0];
            if (fill_we || upd_be[1]) lines[{set_a,spu_addr[3:2]}][1]<=fill_d ? ch3_dout[15:8]  : spu_data[15:8];
            if (fill_we || upd_be[2]) lines[{set_a,spu_addr[3:2]}][2]<=fill_d ? ch3_dout[23:16] : spu_data[7:0];
            if (fill_we || upd_be[3]) lines[{set_a,spu_addr[3:2]}][3]<=fill_d ? ch3_dout[31:24] : spu_data[15:8];
        end
        q<=lines[{set_a,spu_addr[3:2]}];
    end

    // VOICE_SETS=2. owners[] is read every clock at the request's bucket: the
    // address holds from the request, and ch3 answers at least one clock
    // after the request is taken, so own_q is the bucket's entry when a fill
    // or a write completes. Nothing uses own_q on the clock after a write to
    // owners[] (the next request is taken later), so the value read during a
    // write does not matter.
    localparam integer OWN_BITS = 9;
    wire [31:0] own_sets;
    generate if (VOICE_SETS==2) begin : gown
        (* ramstyle="M10K, no_rw_check" *) reg [15:0] owners [0:(1<<OWN_BITS)-1];
        reg [15:0] own_q;
        wire [OWN_BITS-1:0] bucket=spu_addr[18-:OWN_BITS];
        wire [15:0] own_bit=16'd1<<set_a[3:0];
        wire own_fill=ch3_ready && eng==E_DEMAND && !discard && !reset && !(&set_a);
        wire own_wr=wr_done && !discard && !reset;
        integer oi;
        initial for (oi=0; oi<(1<<OWN_BITS); oi=oi+1) owners[oi]=16'd0;
        always @(posedge clk1x) begin
            if (own_fill || own_wr) owners[bucket]<=own_wr ? 16'd0 : own_q | own_bit;
            own_q<=owners[bucket];
        end
        assign own_sets={line_ok, own_q[14:0], own_q};   // line_ok: a write's set_a is 31
    end else begin : gnoown
        assign own_sets=32'd0;
    end endgenerate

    wire [26:0] base=27'h1000000;
    wire take=spu_req || pend;

    always @(posedge clk1x) begin
        spu_done<=0; ch3_req<=0; stat_access<=0; stat_hit<=0; ch_done<=0;
        if (spu_req) pend<=1;   // cleared below when the request is taken
        if (CHEAT!=0 && ch_req) ch_pend<=1;
        if (reset) begin
            valid<=0; pend<=0; redo<=0; ch_pend<=0; eng<=E_IDLE;
        end else if (discard) begin
            // Guest reset: the BIOS loader owns ch3 and ch3_ready is gated
            // off, so nothing may wait for a ch3 answer. Reads are dropped;
            // a write ch3 had already taken is issued again after the reset
            // (same address and data: idempotent).
            valid<=0; pend<=0; eng<=E_IDLE;
            if (eng==E_WRITE || eng==E_REDO) redo<=1;
            if (CHEAT!=0 && (ch_pend || eng==E_CHEAT)) begin ch_pend<=0; ch_q<=0; ch_done<=1; end
        end else case (eng)
            E_IDLE:
                if (redo) begin
                    redo<=0; eng<=E_REDO; ch3_req<=1; ch3_rnw<=0;
                end else if (take) begin
                    pend<=0;
                    if (!spu_rnw) begin
                        eng<=E_WRITE; ch3_req<=1; ch3_rnw<=0; wr_ack<=0;
                        ch3_addr<=base+{8'b0,spu_addr}; ch3_din<={16'b0,spu_data[15:0]}; ch3_be<={2'b00,spu_be[1:0]};
                    end else begin
                        stat_access<=1;
                        if (hit) begin
                            eng<=E_HIT; stat_hit<=1;
                        end else begin
                            eng<=E_DEMAND; ch3_req<=1; ch3_rnw<=1; ch3_be<=4'hf;
                            ch3_addr<=base+{8'b0,spu_addr[18:2],2'b00};
                        end
                    end
                end else if (CHEAT!=0 && ch_pend) begin
                    // A halfword read returns it in ch3_dout[15:0]; a byte
                    // goes to both lanes of its halfword.
                    ch_pend<=0; eng<=E_CHEAT; ch3_req<=1; ch3_rnw<=ch_rnw;
                    ch3_addr<={6'b0,ch_addr[20:1],1'b0};
                    ch3_din<={16'b0, ch_byte ? {2{ch_data[7:0]}} : ch_data};
                    ch3_be<=!ch_byte ? 4'b0011 : ch_addr[0] ? 4'b0010 : 4'b0001;
                end
            E_HIT: begin
                spu_q<={16'b0, spu_addr[1] ? q[3:2] : q[1:0]}; spu_done<=1; eng<=E_IDLE;
            end
            E_DEMAND: if (ch3_ready) begin
                spu_q<={16'b0, spu_addr[1] ? ch3_dout[31:16] : ch3_dout[15:0]}; spu_done<=1; eng<=E_IDLE;
                // this word is now present; a new or invalid line keeps only it
                tags[set_a]<={spu_addr[18:4], (line_ok ? entry[3:0] : 4'b0000) | word_bit};
                valid[set_a]<=1;
            end
            E_WRITE: begin
                if (wr_done) begin
                    spu_q<=0; spu_done<=1; wr_ack<=1;
                    if (VOICE_SETS==1) valid<=0;
                    if (VOICE_SETS==2) valid<=valid & ~own_sets[LINES-1:0];
                end
                if (ch3_ready) eng<=E_IDLE;
            end
            E_REDO: if (ch3_ready) eng<=E_IDLE;
            E_CHEAT: if (ch3_ready) begin ch_q<=ch3_dout[15:0]; ch_done<=1; eng<=E_IDLE; end
            default: eng<=E_IDLE;
        endcase
    end
endmodule
`default_nettype wire
