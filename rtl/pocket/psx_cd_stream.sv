// SPDX-License-Identifier: MIT
// CD sectors from an APF data slot (a raw MODE2/2352 .bin on the SD card).
//
// Replaces MiSTer's HPS sector service for the pinned cd_top raw-sector and
// track-info ports. Protocol and metadata layout follow
// sim/reference/psx_disc_host.sv and scripts/psx_disc_host.py: physical LBAs
// 0-149 are a zero pregap, LBA 150 is file sector INDEX_SECTOR, LBAs past the
// image end return 0xAA fill. One data track; the size must be a whole number
// of 2,352-byte sectors. No CUE/CHD parsing or SBI.
//
// MAX_TRACKS>1 (multi-track discs, e.g. one data track and CD-audio tracks):
// track k is the slot disc_slot + k*TRACK_STRIDE (redump's per-track .bin
// files, listed in an APF instance JSON). The disc is the files back to back:
// track 1 starts at physical LBA 150 (its pregap is not in the file), and
// every later file starts with its own 150-sector pregap (INDEX 00), so its
// INDEX 01 is 150 sectors into the file. Tracks 2+ are audio. The mount
// looks up each track slot until one is absent or empty; every file must be
// whole sectors. The track table follows the MiSTer host layout (record k:
// start LBA of the pregap, end LBA, {audio, INDEX 01 MM:SS}). A fetch finds
// its track by a short sequential search before the offset multiply.
// MAX_TRACKS=1 is the original single-track loader.
//
// TABLE_OFFSET!=0 (one-file discs, any track count): the disc is one .bin
// with every track back to back as on the disc (redump's track files
// concatenated), so physical LBA 150+s is file sector s, and the slot
// disc_slot+TABLE_OFFSET holds the disc's 400 track-info words (1,600 bytes,
// little-endian, scripts/psx_disc_host.py layout) written by
// make_pocket_instances.py from the cue. Each mount reads that file into the
// last sector buffer and replays its words instead of the generated ones;
// word 3 is the core's region with the file's LibCrypt mask (bits 15:0, from
// the .sbi), so a one-file build needs no LIBCRYPT_SLOT, whose second held
// buffer could leave none for a read racing the mount. Without a 1,600-byte
// file the generated single-track table is used. Meant with MAX_TRACKS=1
// and LIBCRYPT_SLOT=0.
//
// Host side (APF bridge clock): each sector is one APF target_dataslot_read
// (opcode 0x180) of 2,352 bytes into a per-buffer bridge window at
// STAGING_BRIDGE + buffer*4096. Up to NBUF buffers; free buffers prefetch the
// following sectors so a sequential read is usually already resident when
// cd_top asks for it. A seek drops prefetched sectors. The command port is
// held valid until target_ready (the shared target-port arbiter); done is
// only trusted after acceptance, once it has dropped for this command, so a
// command still running from before a transport reset cannot complete ours
// and its staging writes are ignored.
//
// Core side (CD clock): replays a sector exactly like the reference host
// (ack, 2 idle clocks, 1,176 contiguous halfword writes, write low, 2 idle
// clocks, ack low) and writes the 400 track-info words on every mount.
//
// Disc selection (docs/pocket-disc-swap.md): the first mount comes from APF
// allcomplete (size from the APF ID/size table) or from an APF 0x8A slot
// update if nothing is mounted yet. A 0x8A for the mounted slot before the
// guest has asked for any sector since that mount remounts it at once (the
// game cannot have seen the old disc). Later 0x8A updates for the disc slot
// (the user loaded the next disc's file) raise disc_pending; the swap sequencer
// opens the lid and strobes disc_select_strobe with disc_slot_id. The loader
// then lets in-flight APF reads and the sector being replayed finish, drops
// every buffered sector, writes the 400 track-info words and pulses
// disc_change_done; later sectors come from the new file. Between the reload
// and the swap the slot already holds the new file, so sectors are served as
// 0xAA fill and an APF read in flight is discarded. A strobe without a
// pending update looks the size up in the table. has_disc is high while the
// mounted slot holds at least one whole sector.
//
// Crossings: single-bit toggles through two flops; every multi-bit value
// that crosses is held stable from before its toggle until the far side
// answers. reset and host_reset are the common transport reset and must
// overlap; the guest reset is not an input. Replays always run to completion
// (cd_top ignores writes outside a fetch, as with the HPS host).
module psx_cd_stream #(
    parameter [15:0] DEFAULT_SLOT=16'h0,
    parameter [31:0] STAGING_BRIDGE=32'h24000000,
    parameter integer NBUF=2,
    parameter [19:0] INDEX_SECTOR=20'd0,    // file sector of CUE INDEX 01
    parameter integer MAX_TRACKS=1,
    parameter [15:0] TRACK_STRIDE=16'h0100,  // slot ID step between track files
    parameter [15:0] LIBCRYPT_SLOT=16'h0,    // slot of the disc's .sbi file (0: no LibCrypt)
    parameter [15:0] DISC_STEP=16'h0,        // multi-disc game files: disc n starts at DEFAULT_SLOT+(n-1)*DISC_STEP (0: off)
    parameter [15:0] TABLE_OFFSET=16'h0      // one-file discs: track table file in slot disc+TABLE_OFFSET (0: off)
) (
    input wire host_clk, host_reset,
    input wire bridge_wr,
    input wire [31:0] bridge_addr, bridge_data,
    input wire allcomplete,
    input wire slot_update,
    input wire [15:0] slot_update_id,
    input wire [31:0] slot_update_size,
    output wire table_active,                // table A-port lease request
    input wire table_grant,                  // advance lookups only while granted
    output wire [9:0] table_addr,
    input wire [31:0] table_q,
    output reg target_read=0,            // valid; held until target_ready
    input wire target_ready,
    output wire [15:0] target_id,
    output wire [31:0] target_offset,
    output wire [31:0] target_bridge, target_length,
    input wire target_done,
    input wire [2:0] target_error,
    output wire host_busy,
    output reg read_error=0,

    input wire clk, reset,
    input wire [1:0] host_region,
    input wire [15:0] disc_slot_id,
    input wire disc_select_strobe,
    output reg disc_change_done=0, has_disc=0,
    output reg guest_idle=1,             // no sector requested since the last mount (core clock)
    output reg disc_settled=0,           // the first mount (disc or no disc) has reached the core
    output reg [3:0] discs_present=0,    // DISC_STEP: which of discs 1-4 the APF table lists (core clock, after a mount)
    output wire disc_pending,
    input wire cd_hps_req,
    input wire [31:0] cd_hps_lba,
    output reg cd_hps_ack=0, cd_hps_write=0,
    output reg [15:0] cd_hps_data=0,
    output reg [7:0] disc_generation=0,      // completed mounts
    output wire [19:0] sector_count,         // data sectors of the mounted disc
    output wire stream_idle,
    output reg [8:0] trackinfo_addr=0,
    output wire [31:0] trackinfo_data,
    output reg trackinfo_write=0
);
    localparam integer SB=$clog2(NBUF);
    localparam integer TB=MAX_TRACKS>1 ? $clog2(MAX_TRACKS) : 1;
    localparam integer WORDS=588;
    localparam [19:0] PREGAP=20'd150;
    localparam [1:0] K_DATA=0, K_ZERO=1, K_FILL=2;
    localparam [1:0] B_EMPTY=0, B_FILLING=1, B_READY=2, B_SERVING=3;

    (* ramstyle="M10K" *) reg [31:0] sectors[0:NBUF*WORDS-1];

    // ---------------- core -> host requests (held stable) ----------------
    reg req_t=0, done_t=0, sel_t=0, mount_done_t=0;
    reg [19:0] req_lba=0;
    reg [15:0] sel_slot=DEFAULT_SLOT;
    // ---------------- host -> core answers (held stable) -----------------
    reg resp_t=0, mount_t=0, sel_ack_t=0;
    reg [15:0] m_slot=DEFAULT_SLOT;
    reg [SB-1:0] resp_buf=0;
    reg [1:0] resp_kind=0;
    reg m_valid=0;
    reg [19:0] m_end=0;
    reg [7:0] m_minutes=0, m_seconds=0;
    reg [TB:0] m_ntracks=1;
    reg [11:0] m_lc_bytes=0;          // bytes of the .sbi in buffer 0 (0: none)
    reg m_tb=0;                       // TABLE_OFFSET: track table file in buffer NBUF-1
    reg [3:0] m_present=0;            // DISC_STEP: discs found in the APF table
    // Track table (MAX_TRACKS>1), one block RAM entry per track-info word:
    // entry {k,w} holds word w of record k (0: first physical LBA of file k,
    // 1: its last LBA, 2: MM:SS of its INDEX 01). Track 0's first LBA is
    // PREGAP and its MM:SS 00:02, so those two entries are never read.
    // Written only while mounting, before the mount toggle, and not again
    // until the core acknowledges it (mount_waiting). The host reads first
    // LBAs for the fetch search, the core whole records for its track table.
    localparam integer TE=MAX_TRACKS>1 ? MAX_TRACKS*4 : 4;
    (* ramstyle="M10K" *) reg [19:0] t_rec[0:TE-1];
    reg t_we=0;
    reg [TB+1:0] t_waddr=0;
    reg [19:0] t_wdata=0;
    always @(posedge host_clk) if(MAX_TRACKS>1 && t_we) t_rec[t_waddr]<=t_wdata;

    // ============================ host side =============================
    reg [2:0] h_req_s=0, h_done_s=0, h_sel_s=0, h_mdone_s=0;
    always @(posedge host_clk) begin
        h_req_s<={h_req_s[1:0],req_t};
        h_done_s<={h_done_s[1:0],done_t};
        h_sel_s<={h_sel_s[1:0],sel_t};
        h_mdone_s<={h_mdone_s[1:0],mount_done_t};
    end
    wire h_req_edge=h_req_s[2]^h_req_s[1];
    wire h_done_edge=h_done_s[2]^h_done_s[1];
    wire h_sel_edge=h_sel_s[2]^h_sel_s[1];
    wire h_mdone_edge=h_mdone_s[2]^h_mdone_s[1];

    reg [1:0] bstate[0:NBUF-1];
    reg [19:0] btag[0:NBUF-1];
    reg [1:0] bkind[0:NBUF-1];
    reg bstale[0:NBUF-1];

    reg [15:0] cur_slot=DEFAULT_SLOT, next_slot=DEFAULT_SLOT;
    reg disc_valid=0;
    reg [19:0] disc_end=0;
    reg dem_valid=0, ahead_ok=0, cmd_busy=0, done_low_seen=0, accepted=0;
    wire done_window=accepted;
    reg [19:0] dem_lba=0, next_fetch=0;
    reg [SB-1:0] cmd_buf=0, serve_buf=0;
    reg serving=0;
    reg prev_complete=0, prev_write=0;
    reg mount_pending=0, mount_waiting=0, have_size=0, mounted_once=0, pending_file=0;
    // The guest asked for a sector since the last mount started. Until it
    // has, a 0x8A for the mounted slot cannot be a disc change the game
    // could notice, so it remounts at once instead of waiting for a swap.
    reg read_since_mount=0;
    // LibCrypt (LIBCRYPT_SLOT!=0): after the tracks, the mount looks the
    // .sbi slot up and reads the file (at most one sector) into buffer 0;
    // the core side turns it into cd_top's 16-bit LibCrypt mask.
    // TABLE_OFFSET: the same lookup and read fetch the track table file
    // first (side_tb), into the last buffer.
    reg lc_done=0, lc_cmd=0, tb_done=0, tb_ok=0, side_tb=0;
    reg [11:0] lc_bytes=0;
    localparam [SB-1:0] TB_BUF=NBUF-1;
    wire [15:0] side_slot=side_tb ? cur_slot+TABLE_OFFSET : LIBCRYPT_SLOT;
    reg [31:0] pending_size=0;

    // Mount sequencer: table lookup, then one subtraction per sector that
    // also steps the end LBA and its BCD minutes/seconds (about 4 ms for a
    // full disc at 74.25 MHz). No divider or multiplier.
    localparam [31:0] INDEX_BYTES=INDEX_SECTOR*32'd2352;
    localparam M_IDLE=0, M_LOOK_ID=1, M_LOOK_SIZE=2, M_COUNT=3, M_PUBLISH=4, M_LOOK_LC=5, M_READ_LC=6, M_PROBE=7;
    reg probe_done=0;
    reg [3:0] present=0, probe_hit=0;
    reg [2:0] mstate=M_IDLE;
    reg [4:0] tindex=0;
    reg [1:0] twait=0;
    reg tsize_word=0, too_long=0;
    reg [31:0] remaining=0;
    reg [6:0] frames=0;
    // Track file: the track being counted while mounting; otherwise the
    // fetch's file, held from the search until the next fetch starts. A
    // mount starts only with no APF command in flight.
    reg [TB-1:0] f_k=0;
    wire [TB-1:0] mtrack=f_k;
    wire [15:0] fetch_slot=cur_slot+TRACK_STRIDE*f_k, mslot=fetch_slot;
    // Sectors counted in the track so far (saturating: only "reached its
    // INDEX 01, 150 in" matters).
    reg [7:0] track_sectors=0;
    // Fetch track search (MAX_TRACKS>1)
    reg seek_busy=0, f_wait=0;
    reg [19:0] f_lba=0, f_base=0, f_q=0;
    // First LBA of file f_k+1, one clock after f_k settles.
    wire [TB-1:0] f_next=f_k+1'b1;
    always @(posedge host_clk) f_q<=t_rec[{f_next,2'b00}];
    assign table_active=mstate==M_LOOK_ID || mstate==M_LOOK_SIZE || mstate==M_LOOK_LC || mstate==M_PROBE;
    assign table_addr={4'b0,tindex,tsize_word};
    wire [32:0] remaining_next={1'b0,remaining}-33'd2352;

    // Fetch offset (lba-150+INDEX)*2352: one DSP multiplier on the file
    // sector, held through the command. The command still waits 20 clocks,
    // as with the old sequential multiply.
    reg mul_busy=0;
    reg [19:0] mul_x=0;
    reg [4:0] mul_n=0;
    assign target_offset=mul_x*32'd2352;

    function automatic [SB+9:0] base(input [SB-1:0] buffer);
        integer k;
        begin
            base=0;
            for(k=0;k<SB;k=k+1) if(buffer[k]) base=base+(WORDS<<k);
        end
    endfunction

    function automatic [7:0] bcd_inc(input [7:0] value);
        bcd_inc=value[3:0]==9 ? {value[7:4]+4'd1,4'd0} : {value[7:4],value[3:0]+4'd1};
    endfunction

    integer i;
    wire lc_hold=LIBCRYPT_SLOT!=0 && mount_waiting && m_lc_bytes!=0;
    wire tb_hold=TABLE_OFFSET!=0 && mount_waiting && m_tb;
    reg found_ready, found_filling, found_free;
    reg [SB-1:0] ready_buf, free_buf;
    always @* begin
        found_ready=0;found_filling=0;found_free=0;ready_buf=0;free_buf=0;
        for(i=NBUF-1;i>=0;i=i-1) begin
            if(bstate[i]==B_READY && btag[i]==dem_lba) begin found_ready=1;ready_buf=i[SB-1:0];end
            if(bstate[i]==B_FILLING && !bstale[i] && btag[i]==dem_lba) found_filling=1;
            // With LibCrypt, buffer 0 holds the .sbi until the core has
            // parsed it (mount acknowledged); other buffers keep serving.
            if(bstate[i]==B_EMPTY && !(i==0 && lc_hold) && !(i==NBUF-1 && tb_hold)) begin found_free=1;free_buf=i[SB-1:0];end
        end
    end

    wire bridge_push=bridge_wr && !prev_write;
    wire in_window=bridge_addr[31:12]=={STAGING_BRIDGE[31:12+SB],cmd_buf} &&
        bridge_addr[11:2]<WORDS && bridge_addr[1:0]==0;
    wire accept_word=bridge_push && cmd_busy && accepted && in_window;
    wire [SB+9:0] write_index=base(cmd_buf)+bridge_addr[11:2];
    always @(posedge host_clk) if(accept_word) sectors[write_index]<=bridge_data;

    assign target_id=lc_cmd ? side_slot : MAX_TRACKS>1 ? fetch_slot : cur_slot;
    assign target_bridge=STAGING_BRIDGE+{cmd_buf,12'h000};
    assign target_length=lc_cmd ? (side_tb ? 32'd1600 : {20'b0,lc_bytes}) : 32'd2352;
    assign host_busy=cmd_busy || target_read;

    wire mount_idle=!cmd_busy && !serving && !dem_valid && !mount_waiting;
    // A demand that is neither resident, in flight, nor the next sector the
    // fetcher will fill into a free buffer is a seek.
    wire dem_miss=dem_valid && !found_ready && !found_filling &&
        !(ahead_ok && next_fetch==dem_lba && found_free);
    wire [19:0] fetch_offset=next_fetch-PREGAP+INDEX_SECTOR;

    always @(posedge host_clk) begin
        prev_write<=bridge_wr;
        prev_complete<=allcomplete;
        if(host_reset) begin
            for(i=0;i<NBUF;i=i+1) begin bstate[i]<=B_EMPTY;btag[i]<=0;bkind[i]<=0;bstale[i]<=0;end
            cur_slot<=DEFAULT_SLOT;next_slot<=DEFAULT_SLOT;disc_valid<=0;disc_end<=0;
            dem_valid<=0;ahead_ok<=0;cmd_busy<=0;done_low_seen<=0;accepted<=0;dem_lba<=0;next_fetch<=0;
            cmd_buf<=0;serve_buf<=0;serving<=0;target_read<=0;read_error<=0;
            mount_pending<=0;mount_waiting<=0;have_size<=0;pending_size<=0;mounted_once<=0;pending_file<=0;read_since_mount<=0;lc_done<=0;lc_cmd<=0;lc_bytes<=0;m_lc_bytes<=0;tb_done<=0;tb_ok<=0;side_tb<=0;m_tb<=0;probe_done<=0;present<=0;m_present<=0;
            mstate<=M_IDLE;tindex<=0;twait<=0;tsize_word<=0;too_long<=0;remaining<=0;frames<=0;
            mul_busy<=0;mul_x<=0;mul_n<=0;
            track_sectors<=0;seek_busy<=0;f_wait<=0;f_k<=0;f_lba<=0;f_base<=0;m_ntracks<=1;t_we<=0;
            resp_t<=0;resp_buf<=0;resp_kind<=0;mount_t<=0;sel_ack_t<=0;m_slot<=DEFAULT_SLOT;m_valid<=0;m_end<=0;m_minutes<=0;m_seconds<=0;
            prev_write<=0;prev_complete<=0;
        end else begin
            t_we<=0;
            // Mount triggers. A newer trigger replaces an unstarted one.
            if(h_sel_edge) begin
                next_slot<=sel_slot;sel_ack_t<=~sel_ack_t;mount_pending<=1;
                have_size<=pending_file && sel_slot==next_slot;
            end else if(slot_update && slot_update_id==next_slot) begin
                pending_size<=slot_update_size;
                if(mounted_once && read_since_mount) begin
                    // The slot now holds the next file: nothing more comes
                    // from it under the old track table.
                    pending_file<=1;disc_valid<=0;
                    for(i=0;i<NBUF;i=i+1) begin
                        if(bstate[i]==B_READY) bstate[i]<=B_EMPTY;
                        if(bstate[i]==B_FILLING) bstale[i]<=1;
                    end
                end
                else begin mount_pending<=1;have_size<=1;end
            end else if(allcomplete && !prev_complete && !mounted_once) begin
                // Initial load only: a runtime reload must go through
                // disc_pending and the swap, never remount by itself.
                mount_pending<=1;have_size<=0;
            end
            if(h_mdone_edge) mount_waiting<=0;

            if(h_req_edge) begin dem_valid<=1;dem_lba<=req_lba;read_since_mount<=1;end
            if(h_done_edge) begin serving<=0;bstate[serve_buf]<=B_EMPTY;end

            // APF command completion. The pinned handler keeps done high from
            // the previous command; wait for it to drop first.
            if(cmd_busy) begin
                if(target_read && target_ready) begin target_read<=0;accepted<=1;end
                if(done_window && !target_done) done_low_seen<=1;
                else if(done_window && done_low_seen && target_done) begin
                    cmd_busy<=0;accepted<=0;
                    bstate[cmd_buf]<=bstale[cmd_buf] ? B_EMPTY : B_READY;
                    bstale[cmd_buf]<=0;
                    bkind[cmd_buf]<=target_error!=0 ? K_FILL : K_DATA;
                    lc_cmd<=0;
                    if(target_error!=0) begin if(lc_cmd && side_tb) tb_ok<=0; else if(lc_cmd) lc_bytes<=0; else read_error<=1; end
                end
            end

            if(mstate!=M_IDLE) begin
                case(mstate)
                    M_LOOK_ID: begin
                        // A later file starts where the previous one ended.
                        if(mtrack!=0) begin t_we<=1;t_waddr<={mtrack,2'd0};t_wdata<=disc_end;end
                        if(!table_grant) twait<=0;
                        else if(twait!=2) twait<=twait+1'b1;
                        else if(table_q[15:0]==mslot) begin tsize_word<=1;twait<=0;mstate<=M_LOOK_SIZE;end
                        else if(tindex==31) begin
                            // absent: invalid for track 1, the end of the disc after it
                            if(mtrack==0) begin remaining<=32'd1;mstate<=M_COUNT;end
                            else mstate<=M_PUBLISH;
                        end
                        else begin tindex<=tindex+1'b1;twait<=0;end
                    end
                    M_LOOK_SIZE: if(!table_grant) twait<=0;
                        else if(twait!=2) twait<=twait+1'b1;
                        else if(mtrack!=0 && table_q==0) mstate<=M_PUBLISH;   // empty track slot: disc ends
                        else if(mtrack!=0) begin remaining<=table_q;mstate<=M_COUNT;end
                        else begin remaining<=table_q-INDEX_BYTES;too_long<=table_q<INDEX_BYTES;mstate<=M_COUNT;end
                    M_COUNT: if(!remaining_next[32] && !too_long) begin
                            // One more whole sector: end LBA, then MM:SS of end/75.
                            remaining<=remaining_next[31:0];disc_end<=disc_end+1'b1;
                            if(track_sectors!=8'hff) track_sectors<=track_sectors+1'b1;
                            if(mtrack!=0 && track_sectors==PREGAP[7:0]) begin t_we<=1;t_waddr<={mtrack,2'd2};t_wdata<={4'b0,m_minutes,m_seconds};end
                            if(disc_end==20'd524288 || m_minutes==8'h99 && m_seconds==8'h59 && frames==74) too_long<=1;
                            if(frames==74) begin
                                frames<=0;
                                if(m_seconds==8'h59) begin m_seconds<=0;m_minutes<=bcd_inc(m_minutes);end
                                else m_seconds<=bcd_inc(m_seconds);
                            end else frames<=frames+1'b1;
                        end else begin
                            // Whole sectors only, at least one, end within 19-bit LBAs;
                            // a later track also needs data past its pregap.
                            if(remaining!=0 || too_long || (mtrack==0 ? disc_end==PREGAP : track_sectors<=PREGAP[7:0])) begin
                                disc_valid<=0;mstate<=M_PUBLISH;
                            end else if(MAX_TRACKS>1 && mtrack!=MAX_TRACKS-1) begin
                                // Next track file: look its slot up. The
                                // lookup records the file's first LBA.
                                disc_valid<=1;m_ntracks<=mtrack+1'b1;f_k<=f_k+1'b1;track_sectors<=0;
                                t_we<=1;t_waddr<={mtrack,2'd1};t_wdata<=disc_end-1'b1;
                                tindex<=0;twait<=0;tsize_word<=0;mstate<=M_LOOK_ID;
                            end else begin
                                disc_valid<=1;m_ntracks<=mtrack+1'b1;mstate<=M_PUBLISH;
                                t_we<=1;t_waddr<={mtrack,2'd1};t_wdata<=disc_end-1'b1;
                            end
                        end
                    M_PUBLISH: if(DISC_STEP!=0 && !probe_done) begin
                        probe_done<=1;present<=0;tindex<=0;twait<=0;tsize_word<=0;mstate<=M_PROBE;
                    end else if(TABLE_OFFSET!=0 && disc_valid && !tb_done) begin
                        tb_done<=1;tb_ok<=0;side_tb<=1;tindex<=0;twait<=0;tsize_word<=0;mstate<=M_LOOK_LC;
                    end else if(LIBCRYPT_SLOT!=0 && disc_valid && !lc_done) begin
                        lc_done<=1;lc_bytes<=0;side_tb<=0;tindex<=0;twait<=0;tsize_word<=0;mstate<=M_LOOK_LC;
                    end else begin
                        m_valid<=disc_valid;m_end<=disc_end;m_slot<=cur_slot;
                        m_lc_bytes<=disc_valid ? lc_bytes : 12'd0;
                        m_tb<=disc_valid && tb_ok;
                        m_present<=present;
                        if(disc_valid) mounted_once<=1;
                        mount_t<=~mount_t;mount_waiting<=1;mstate<=M_IDLE;
                    end
                    // The track table (side_tb: exactly 400 words) or the .sbi
                    // (header plus 14-byte entries, one sector at most).
                    M_LOOK_LC: if(!table_grant) twait<=0;
                        else if(twait!=2) twait<=twait+1'b1;
                        else if(!tsize_word) begin
                            if(table_q[15:0]==side_slot) begin tsize_word<=1;twait<=0;end
                            else if(tindex==31) mstate<=M_PUBLISH;
                            else begin tindex<=tindex+1'b1;twait<=0;end
                        end else if(side_tb ? table_q==32'd1600 : table_q>32'd4 && table_q<=32'd2352) begin
                            if(side_tb) begin tb_ok<=1;cmd_buf<=TB_BUF;bstale[TB_BUF]<=1;end
                            else begin lc_bytes<=table_q[11:0];cmd_buf<=0;bstale[0]<=1;end
                            lc_cmd<=1;cmd_busy<=1;done_low_seen<=0;accepted<=0;mul_x<=0;target_read<=1;
                            mstate<=M_READ_LC;
                        end else mstate<=M_PUBLISH;
                    M_READ_LC: if(!cmd_busy) mstate<=M_PUBLISH;
                    // Which discs of a multi-disc game file have a file: the
                    // first track slot in the table with a nonzero size. APF
                    // lists every slot data.json declares, size 0 when the
                    // game file has none (Disc 4 for FFVII on hardware).
                    M_PROBE: if(!table_grant) twait<=0;
                        else if(twait!=2) twait<=twait+1'b1;
                        else if(!tsize_word) begin
                            probe_hit[0]<=table_q[15:0]==DEFAULT_SLOT;
                            probe_hit[1]<=table_q[15:0]==DEFAULT_SLOT+DISC_STEP;
                            probe_hit[2]<=table_q[15:0]==DEFAULT_SLOT+DISC_STEP+DISC_STEP;
                            probe_hit[3]<=table_q[15:0]==DEFAULT_SLOT+DISC_STEP+DISC_STEP+DISC_STEP;
                            tsize_word<=1;twait<=0;
                        end else begin
                            if(table_q!=0) present<=present|probe_hit;
                            tsize_word<=0;twait<=0;
                            if(tindex==31) mstate<=M_PUBLISH;
                            else tindex<=tindex+1'b1;
                        end
                    default: mstate<=M_IDLE;
                endcase
            end else if(mount_pending && mount_idle) begin
                // Starting a mount: drop every buffered sector of the old disc.
                for(i=0;i<NBUF;i=i+1) begin bstate[i]<=B_EMPTY;bstale[i]<=0;end
                ahead_ok<=0;mount_pending<=0;cur_slot<=next_slot;disc_valid<=0;read_since_mount<=h_req_edge;lc_done<=0;tb_done<=0;probe_done<=0;
                // m_ntracks counts whole tracks from here; the core reads
                // it only after the publish, and no fetch runs while mounting.
                f_k<=0;m_ntracks<=0;track_sectors<=0;
                if(have_size) pending_file<=0;
                tindex<=0;twait<=0;tsize_word<=0;too_long<=0;
                // Counters start at the physical LBA 150 = 00:02:00.
                disc_end<=PREGAP;frames<=0;m_seconds<=8'h02;m_minutes<=8'h00;
                if(have_size) begin
                    remaining<=pending_size-INDEX_BYTES;too_long<=pending_size<INDEX_BYTES;mstate<=M_COUNT;
                end else mstate<=M_LOOK_ID;
            end else if(dem_valid) begin
                if(found_ready) begin
                    bstate[ready_buf]<=B_SERVING;serve_buf<=ready_buf;serving<=1;
                    resp_buf<=ready_buf;resp_kind<=bkind[ready_buf];resp_t<=~resp_t;
                    dem_valid<=0;
                end else if(dem_miss) begin
                    // Seek: anything resident or in flight is for other LBAs.
                    for(i=0;i<NBUF;i=i+1) begin
                        if(bstate[i]==B_READY) bstate[i]<=B_EMPTY;
                        if(bstate[i]==B_FILLING) bstale[i]<=1;
                    end
                    next_fetch<=dem_lba;ahead_ok<=1;
                end
            end

            // Fill a free buffer with the next sector. Pregap and past-end
            // sectors need no APF command.
            if(mstate==M_IDLE && !(mount_pending && mount_idle) && ahead_ok && found_free &&
               !cmd_busy && !dem_miss && next_fetch!=20'hfffff) begin
                btag[free_buf]<=next_fetch;bstale[free_buf]<=0;next_fetch<=next_fetch+1'b1;
                if(next_fetch<PREGAP) begin bstate[free_buf]<=B_READY;bkind[free_buf]<=K_ZERO;end
                else if(!disc_valid || next_fetch>=disc_end) begin bstate[free_buf]<=B_READY;bkind[free_buf]<=K_FILL;end
                else begin
                    bstate[free_buf]<=B_FILLING;cmd_buf<=free_buf;cmd_busy<=1;done_low_seen<=0;accepted<=0;
                    if(MAX_TRACKS>1) begin seek_busy<=1;f_wait<=0;f_k<=0;f_lba<=next_fetch;f_base<=PREGAP;end
                    else begin mul_busy<=1;mul_x<=fetch_offset;mul_n<=0;end
                end
            end
            // Track search: the last track whose file starts at or before the LBA.
            // f_q is the first LBA of file f_k+1 on every second clock.
            if(seek_busy) begin
                f_wait<=!f_wait;
                if(!f_wait) ;
                else if({1'b0,f_k}+1'b1<m_ntracks && f_lba>=f_q) begin f_k<=f_k+1'b1;f_base<=f_q;end
                else begin
                    seek_busy<=0;mul_busy<=1;mul_n<=0;
                    mul_x<=f_lba-f_base+(f_k==0 ? INDEX_SECTOR : 20'd0);
                end
            end
            if(mul_busy) begin
                mul_n<=mul_n+1'b1;
                if(mul_n==19) begin mul_busy<=0;target_read<=1;end
            end
        end
    end

    // ============================ core side =============================
    reg [2:0] c_resp_s=0, c_mount_s=0, c_selack_s=0;
    always @(posedge clk) begin
        c_resp_s<={c_resp_s[1:0],resp_t};
        c_mount_s<={c_mount_s[1:0],mount_t};
        c_selack_s<={c_selack_s[1:0],sel_ack_t};
    end
    wire c_selack_edge=c_selack_s[2]^c_selack_s[1];
    reg [1:0] c_pending_s=0;
    always @(posedge clk) c_pending_s<={c_pending_s[0],pending_file};
    assign disc_pending=c_pending_s[1];
    assign sector_count=has_disc ? m_end-PREGAP : 20'd0;
    wire c_resp_edge=c_resp_s[2]^c_resp_s[1];
    wire c_mount_edge=c_mount_s[2]^c_mount_s[1];

    localparam C_IDLE=0, C_WAIT=1, C_GAP=2, C_DATA=3, C_TAIL=4, C_TRACK=5, C_TRACK_LOW=6,
        C_LC_ADDR=7, C_LC_NEXT=8, C_LC_FIRST=9, C_LC_CHECK=10, C_LC_SCAN=11;
    reg [3:0] cstate=C_IDLE;
    // LibCrypt mask from the .sbi in buffer 0, as MiSTer's HPS computes it
    // (Main_MiSTer support/psx/psx.cpp libCryptMask): 4-byte header, then
    // 14-byte entries whose first three bytes are a BCD MM:SS:FF; bit 15-m
    // is set when an entry names libCryptSectors[m] (absolute MSF).
    localparam [24*16-1:0] LC_MSF={24'h030805, 24'h030956, 24'h031310, 24'h031429,
        24'h031524, 24'h031849, 24'h032056, 24'h032155, 24'h032317, 24'h032412,
        24'h032503, 24'h032828, 24'h033219, 24'h033356, 24'h033451, 24'h033542};
    reg [15:0] lc_key=0;
    reg [11:0] lc_b=0;               // byte offset of the entry being checked
    reg [23:0] lc_msf=0;
    reg [3:0] lc_m=0;                // one LibCrypt address compared per clock
    reg mount_seen=0, sel_pend=0, sel_inflight=0;
    reg [15:0] sel_next=DEFAULT_SLOT;
    // A mount answers the latest selection only once that selection reached
    // the host and the published slot matches it.
    wire selection_current=!sel_pend && !sel_inflight && m_slot==sel_slot;
    reg [1:0] gap=0, kind=0;
    reg [10:0] halfword=0;
    reg [8:0] track_index=0;
    reg [SB+9:0] rd_addr=0;
    reg [31:0] q=0;
    assign stream_idle=cstate==C_IDLE && !cd_hps_req && !mount_seen;
    always @(posedge clk) q<=sectors[rd_addr];

    // Header (record 0), then record k = track k: pregap start, end, {audio,
    // INDEX 01 MM:SS}. The track count is BCD in [15:8], binary in [7:0].
    wire [7:0] count_bin={{(7-TB){1'b0}},m_ntracks};
    wire [7:0] count_bcd=count_bin>=8'd10 ? count_bin+8'd6 : count_bin;
    // Table entry of the word at track_index, read one clock ahead: C_TRACK
    // holds track_index for a clock before trackinfo_write rises.
    wire [6:0] c_k=track_index[8:2]-1'b1;
    reg [19:0] c_q=0;
    always @(posedge clk) c_q<=t_rec[{c_k[TB-1:0],track_index[1:0]}];
    // Record index[8:2]: 0 the header, k+1 track k (zero past m_ntracks).
    function automatic [31:0] track_word(input [8:0] index, input [19:0] entry);
        reg first;
        begin
            first=index[8:2]==1;
            if(index[8:2]==0) case(index[1:0])
                0: track_word={16'b0,count_bcd,count_bin};
                1: track_word={12'b0,m_end};
                2: track_word={16'b0,m_minutes,m_seconds};
                default: track_word={14'b0,host_region,lc_key};
            endcase
            else if(index[8:2]>{{(6-TB){1'b0}},m_ntracks}) track_word=0;
            else case(index[1:0])
                0: track_word={12'b0,first ? 20'd0 : entry};
                1: track_word={12'b0,MAX_TRACKS>1 ? entry : m_end-1'b1};
                2: track_word={15'b0,!first,first ? 16'h0002 : entry[15:0]};
                default: track_word=0;
            endcase
        end
    endfunction
    // Same-clock data, valid with trackinfo_write.
    // TABLE_OFFSET: the file's words from buffer NBUF-1 (q, addressed one
    // state ahead); word 3 takes the region from the core, the LibCrypt mask
    // from the file.
    assign trackinfo_data=TABLE_OFFSET==0 || !m_tb ? track_word(trackinfo_addr,c_q) :
        trackinfo_addr==3 ? {14'b0,host_region,q[15:0]} : q;

    always @(posedge clk) begin
        disc_change_done<=0;
        if(c_mount_edge) mount_seen<=1;
        if(reset) begin
            cstate<=C_IDLE;mount_seen<=0;gap<=0;kind<=0;halfword<=0;track_index<=0;rd_addr<=0;
            req_t<=0;done_t<=0;sel_t<=0;mount_done_t<=0;req_lba<=0;sel_slot<=DEFAULT_SLOT;
            sel_pend<=0;sel_inflight<=0;sel_next<=DEFAULT_SLOT;
            has_disc<=0;cd_hps_ack<=0;cd_hps_write<=0;cd_hps_data<=0;disc_generation<=0;guest_idle<=1;disc_settled<=0;discs_present<=0;
            trackinfo_addr<=0;trackinfo_write<=0;
        end else begin
            // Disc selection: one selection crosses at a time; sel_slot is
            // held until the host acknowledges it. A newer strobe waits.
            if(c_selack_edge) sel_inflight<=0;
            if(sel_pend && !sel_inflight) begin
                sel_slot<=sel_next;sel_t<=~sel_t;sel_inflight<=1;sel_pend<=0;
            end
            if(disc_select_strobe) begin sel_next<=disc_slot_id;sel_pend<=1;end
            case(cstate)
                C_IDLE: if(mount_seen || c_mount_edge) begin
                        mount_seen<=0;track_index<=0;guest_idle<=1;
                        lc_key<=0;lc_b<=12'd4;discs_present<=m_present;
                        if(!m_valid) disc_settled<=1;   // no disc: settled now; a disc settles after its table
                        rd_addr<=base(NBUF-1);   // track table file words (TABLE_OFFSET)
                        if(m_valid) cstate<=LIBCRYPT_SLOT!=0 && m_lc_bytes!=0 ? C_LC_ADDR : C_TRACK;
                        else begin has_disc<=0;mount_done_t<=~mount_done_t;disc_change_done<=selection_current;end
                    end else if(cd_hps_req && !cd_hps_ack) begin
                        req_lba<=|cd_hps_lba[31:20] ? 20'hffffe : cd_hps_lba[19:0];
                        req_t<=~req_t;cstate<=C_WAIT;guest_idle<=0;
                    end
                C_WAIT: if(c_resp_edge) begin
                        kind<=resp_kind;rd_addr<=base(resp_buf);
                        cd_hps_ack<=1;gap<=0;halfword<=0;cstate<=C_GAP;
                    end
                C_GAP: cstate<=C_DATA;
                C_DATA: begin
                    cd_hps_write<=1;
                    cd_hps_data<=kind==K_DATA ? (halfword[0] ? q[31:16] : q[15:0]) :
                        kind==K_ZERO ? 16'h0000 : 16'haaaa;
                    if(!halfword[0]) rd_addr<=rd_addr+1'b1;
                    if(halfword==1175) begin gap<=0;cstate<=C_TAIL;end
                    halfword<=halfword+1'b1;
                end
                C_TAIL: begin
                    cd_hps_write<=0;
                    if(gap==2) begin cd_hps_ack<=0;done_t<=~done_t;cstate<=C_IDLE;end
                    else gap<=gap+1'b1;
                end
                C_TRACK: begin
                    trackinfo_addr<=track_index;trackinfo_write<=1;cstate<=C_TRACK_LOW;
                end
                C_TRACK_LOW: begin
                    trackinfo_write<=0;
                    if(track_index==399) begin
                        has_disc<=1;disc_settled<=1;mount_done_t<=~mount_done_t;disc_change_done<=selection_current;cstate<=C_IDLE;
                        disc_generation<=disc_generation+1'b1;
                    end else begin track_index<=track_index+1'b1;rd_addr<=rd_addr+1'b1;cstate<=C_TRACK;end
                end
                // One entry per pass: read its word and the next, take the
                // MSF from byte lane 0 or 2, then step 14 bytes.
                C_LC_ADDR: if({1'b0,lc_b}+13'd3>{1'b0,m_lc_bytes}) begin rd_addr<=base(NBUF-1);cstate<=C_TRACK;end
                    else begin rd_addr<=base(0)+lc_b[11:2];cstate<=C_LC_NEXT;end
                C_LC_NEXT: begin rd_addr<=rd_addr+1'b1;cstate<=C_LC_FIRST;end
                // MM:SS:FF from byte lane 0 of the first word, or lanes 2-3
                // of the first word and lane 0 of the next.
                C_LC_FIRST: begin lc_msf<=lc_b[1] ? {q[23:16],q[31:24],8'h00} : q[23:0];cstate<=C_LC_CHECK;end
                C_LC_CHECK: begin
                    if(lc_b[1]) lc_msf[7:0]<=q[7:0]; else lc_msf<={lc_msf[7:0],lc_msf[15:8],lc_msf[23:16]};
                    lc_m<=0;cstate<=C_LC_SCAN;
                end
                C_LC_SCAN: begin
                    if(lc_msf==LC_MSF[24*(15-lc_m)+:24]) lc_key[15-lc_m]<=1'b1;
                    lc_m<=lc_m+1'b1;
                    if(&lc_m) begin lc_b<=lc_b+12'd14;cstate<=C_LC_ADDR;end
                end
                default: cstate<=C_IDLE;
            endcase
        end
    end
endmodule
