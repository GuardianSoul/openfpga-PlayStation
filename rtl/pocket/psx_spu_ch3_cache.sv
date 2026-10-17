// SPDX-License-Identifier: MIT
// SPU RAM read cache on psx_sdram_ctrl ch3 (Native layout), all in clk1x
// (prepare --spu-cache, with --sotn-memory --sdram-ctrl clean).
// AudioProbe tied the FFVII FMV crackle to SPU deadline misses: spu.vhd
// (useSDRAM='1') gives each voice a fixed slot of about 24 clk1x (RAM_WAIT,
// voiceCounter >= 23) for its header read, sample read and one transfer
// access, and a tick overruns when ch3 latency pushes a slot over.
// A read hit answers in two clk1x. A read miss reads only the word it needs
// (one ch3 access, as the direct path) and answers when it returns; the rest
// of the 8-halfword line follows in the background, next word first, and a
// new miss takes the next ch3 access ahead of queued fill words. Writes are
// posted: done the next clk1x, written to ch3 ahead of fills, and a cached
// or filling copy is updated when ch3 takes them; reads wait while a posted
// write to their set is pending. 64 lines: 2 ways x 32 sets (the set/tag
// split of psx_spu_sdram_adapter, docs/pocket-spu-stream-buffer.md).
// The first version (fill the whole line before answering, invalidate on
// write) made hardware deadline misses worse (+1,161 vs +42 over the FFVII
// intro): a 4-read miss overran the voice slot. make test-spu-ch3-cache.
`default_nettype none
module psx_spu_ch3_cache (
    input wire clk1x, reset, discard,
    input wire spu_req, spu_rnw,
    input wire [18:0] spu_addr, input wire [31:0] spu_data, input wire [3:0] spu_be,
    output reg spu_done=0, output reg [31:0] spu_q=0,
    output reg ch3_req=0, output reg ch3_rnw=0,
    output reg [26:0] ch3_addr=0, output reg [31:0] ch3_din=0, output reg [3:0] ch3_be=0,
    input wire ch3_ready, input wire [31:0] ch3_dout,
    output reg stat_access=0, output reg stat_hit=0   // one-clock pulses per SPU access / read hit
);
    // ---------------------------------------------------------- tags
    (* ramstyle="MLAB, no_rw_check" *) reg [14:0] tag0 [0:31];
    (* ramstyle="MLAB, no_rw_check" *) reg [14:0] tag1 [0:31];
    reg [31:0] valid0=0, valid1=0, victim=0;
    (* ramstyle="M10K, no_rw_check" *) reg [3:0][7:0] lines [0:255];

    // request held while it waits
    reg pend=0, p_rnw=1; reg [18:0] p_addr=0; reg [15:0] p_data=0; reg [1:0] p_be=0;
    wire [18:0] a=pend ? p_addr : spu_addr;
    wire [4:0] set_a=a[8:4]^a[13:9]^a[18:14];
    wire h0=valid0[set_a] && tag0[set_a]==a[18:4];
    wire h1=valid1[set_a] && tag1[set_a]==a[18:4];

    // line being filled (background)
    reg f_on=0, f_way=0; reg [4:0] f_set=0; reg [14:0] f_tag=0; reg [3:0] f_have=0; reg [1:0] f_next=0;
    wire f_line=f_on && f_set==set_a && f_tag==a[18:4];
    wire f_word=f_line && f_have[a[3:2]];

    // posted write
    reg w_on=0; reg [18:0] w_addr=0; reg [15:0] w_data=0; reg [1:0] w_be=0;
    reg w_hit=0, w_way=0;   // cached copy at accept (fills wait behind the write)
    wire [4:0] w_set=w_addr[8:4]^w_addr[13:9]^w_addr[18:14];

    // ch3 engine
    localparam E_IDLE=0, E_DEMAND=1, E_FILL=2, E_WRITE=3;
    reg [1:0] eng=E_IDLE; reg [1:0] e_word=0;

    // read path
    localparam R_IDLE=0, R_HIT=1, R_MISS=2;
    reg [1:0] rd=R_IDLE; reg [2:0] r_lane=0;
    reg [3:0][7:0] q;

    // data RAM write port: fill words and write updates
    reg l_we=0; reg [7:0] l_a=0; reg [31:0] l_d=0; reg [3:0] l_be=0;

    wire take=spu_req || pend;
    // A pending write to the set goes first; a data-RAM write in flight
    // (fill word or write update) lands before a read uses the RAM.
    wire read_ok=!(w_on && w_set==set_a) && !l_we;
    // The data RAM reads the lookup's own word every clock, so a hit's word
    // is out of the M10K when R_HIT runs.
    wire [7:0] raddr=(h0 || h1) ? {h1,set_a,a[3:2]} : {f_way,f_set,a[3:2]};
    // One simple dual-port M10K with byte enables (Quartus template form).
    always @(posedge clk1x) begin
        if (l_we) begin
            if (l_be[0]) lines[l_a][0]<=l_d[7:0];
            if (l_be[1]) lines[l_a][1]<=l_d[15:8];
            if (l_be[2]) lines[l_a][2]<=l_d[23:16];
            if (l_be[3]) lines[l_a][3]<=l_d[31:24];
        end
        q<=lines[raddr];
    end
    wire [26:0] base=27'h1000000;

    always @(posedge clk1x) begin
        spu_done<=0; ch3_req<=0; l_we<=0; stat_access<=0; stat_hit<=0;
        if (reset) begin
            valid0<=0; valid1<=0; pend<=0; f_on<=0; w_on<=0; eng<=E_IDLE; rd<=R_IDLE;
        end else begin
            // ---- SPU side
            if (spu_req && rd==R_IDLE && !pend) begin
                pend<=1; p_rnw<=spu_rnw; p_addr<=spu_addr; p_data<=spu_data[15:0]; p_be<=spu_be[1:0];
            end
            if (rd==R_IDLE && take) begin
                if (!(pend ? p_rnw : spu_rnw)) begin
                    if (!w_on) begin   // post it
                        w_on<=1; w_addr<=a; w_data<=pend ? p_data : spu_data[15:0]; w_be<=pend ? p_be : spu_be[1:0];
                        w_hit<=h0 || h1; w_way<=h1;
                        pend<=0; spu_q<=0; spu_done<=1;
                    end
                end else if (read_ok) begin
                    r_lane<=a[3:1];
                    stat_access<=1;
                    if (h0 || h1) begin
                        victim[set_a]<=h1; rd<=R_HIT; pend<=0; stat_hit<=1;
                    end else if (f_word) begin
                        rd<=R_HIT; pend<=0; stat_hit<=1;
                    end else begin
                        rd<=R_MISS; pend<=0;
                        p_addr<=a;   // keep the address for the engine
                    end
                end
            end
            if (rd==R_HIT) begin
                spu_q<={16'b0, r_lane[0] ? q[3:2] : q[1:0]}; spu_done<=1; rd<=R_IDLE;
            end

            // ---- ch3 engine
            case (eng)
            E_IDLE: if (!discard) begin   // the BIOS loader owns ch3 during reset
                if (rd==R_MISS) begin
                    // demand word; the line it belongs to becomes the fill line
                    eng<=E_DEMAND; ch3_req<=1; ch3_rnw<=1; ch3_be<=4'b1111;
                    ch3_addr<=base+{8'b0,p_addr[18:2],2'b00};
                    if (!(f_on && f_set==set_a && f_tag==p_addr[18:4])) begin
                        f_on<=1; f_set<=set_a; f_tag<=p_addr[18:4]; f_have<=0;
                        f_way<=valid0[set_a] && (!valid1[set_a] || !victim[set_a]);
                        if (valid0[set_a] && (!valid1[set_a] || !victim[set_a])) valid1[set_a]<=0; else valid0[set_a]<=0;
                    end
                    e_word<=p_addr[3:2];
                end else if (w_on) begin
                    eng<=E_WRITE; ch3_req<=1; ch3_rnw<=0;
                    ch3_addr<=base+{8'b0,w_addr}; ch3_din<={16'b0,w_data}; ch3_be<={2'b00,w_be};
                end else if (f_on) begin
                    if (f_have==4'hf) begin
                        f_on<=0;
                        if (f_way) begin tag1[f_set]<=f_tag; valid1[f_set]<=1; end
                        else begin tag0[f_set]<=f_tag; valid0[f_set]<=1; end
                        victim[f_set]<=f_way;
                    end else begin
                        // next missing word after the last one taken
                        e_word<=!f_have[f_next] ? f_next : !f_have[f_next+2'd1] ? f_next+2'd1 :
                                !f_have[f_next+2'd2] ? f_next+2'd2 : f_next+2'd3;
                        eng<=E_FILL; ch3_req<=1; ch3_rnw<=1; ch3_be<=4'b1111;
                        ch3_addr<=base+{8'b0,f_tag,(!f_have[f_next] ? f_next : !f_have[f_next+2'd1] ? f_next+2'd1 :
                                !f_have[f_next+2'd2] ? f_next+2'd2 : f_next+2'd3),2'b00};
                    end
                end
            end
            E_DEMAND: if (ch3_ready) begin
                spu_q<={16'b0, r_lane[0] ? ch3_dout[31:16] : ch3_dout[15:0]}; spu_done<=1; rd<=R_IDLE;
                l_we<=1; l_a<={f_way,f_set,e_word}; l_d<=ch3_dout; l_be<=4'hf;
                f_have[e_word]<=1; f_next<=e_word+2'd1; eng<=E_IDLE;
            end
            E_FILL: if (ch3_ready) begin
                l_we<=1; l_a<={f_way,f_set,e_word}; l_d<=ch3_dout; l_be<=4'hf;
                f_have[e_word]<=1; f_next<=e_word+2'd1; eng<=E_IDLE;
            end
            E_WRITE: if (ch3_ready) begin
                w_on<=0; eng<=E_IDLE;
                // keep a cached or filling copy current
                if (w_hit) begin
                    l_we<=1; l_a<={w_way,w_set,w_addr[3:2]}; l_d<={2{w_data}}; l_be<=w_addr[1] ? {w_be,2'b00} : {2'b00,w_be};
                end else if (f_on && f_set==w_set && f_tag==w_addr[18:4] && f_have[w_addr[3:2]]) begin
                    l_we<=1; l_a<={f_way,w_set,w_addr[3:2]}; l_d<={2{w_data}}; l_be<=w_addr[1] ? {w_be,2'b00} : {2'b00,w_be};
                end
            end
            endcase
            // Guest reset (the BIOS loader owns ch3, ch3_ready is gated off):
            // drop reads and fills, so nothing waits for a ch3 answer that
            // never comes (issue #17). An acknowledged posted write survives:
            // it stays queued (reissued if ch3 had taken it) and lands after
            // the reset, before any later SPU access to its set.
            if (discard) begin
                valid0<=0; valid1<=0; f_on<=0; pend<=0; rd<=R_IDLE; eng<=E_IDLE; w_hit<=0;
            end
        end
    end
endmodule
`default_nettype wire
