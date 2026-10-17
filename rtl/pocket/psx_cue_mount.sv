// SPDX-License-Identifier: MIT
// Play a .cue or .bin picked from the launch list or with "Load disc"
// (docs/pocket-cue-loading.md).
//
// Sits on the APF host clock between core_bridge_cmd's target port and
// psx_cd_stream built for one-file discs (TABLE_OFFSET), and makes a cue and
// its track files look like a one-file disc: DISC_SLOT holding every file
// back to back and TABLE_SLOT holding the 400 track-info words.
//
// The work is done by firmware (rtl/pocket/cue_fw/cue_mount.c, built into
// psx_cue_fw0-3.hex) on SERV, the bit-serial RISC-V core (references/serv, ISC),
// which is far smaller than the same logic as a state machine. This file is
// its 16 KiB RAM and the few registers the firmware needs:
//   - APF target commands: d_* are wired from registers it writes, so they
//     hold while a command runs; reads land in a 512-byte window in RAM (or,
//     for the stream's reads, in the stream's own window).
//   - The path struct for getfile and openfile, also in RAM.
//   - The stream's target request: accepted here (u_ready while the firmware
//     is idle), its fields read by the firmware (the stream holds them until
//     done), finished with a write.
//   - Slot updates: DISC_SLOT picks wait for the firmware, FILE_SLOT's are
//     the firmware's own openfile echoes and are dropped (openfpga-megacd
//     notes the firmware re-sends 0x8A after every core-initiated openfile),
//     others pass to the stream.
//   - The stream's allcomplete, and the sizes its ID/size table lookups see
//     for DISC_SLOT and TABLE_SLOT (table_view; with the Disc menu, for
//     every disc's slots n01 and n02), held (table_grant) while the firmware
//     loads the disc the menu asks for.
//   - Firmware stores to 0xC0000000+n become bridge writes into the stream's
//     window (the generated track table), in clocks APF isn't writing.
//
// Byte order: bridge words carry file bytes little-endian (byte 0 in bits
// 7:0; the top's bridge_endian_little), which is also RISC-V's order.
module psx_cue_mount #(
    parameter [31:0] STRUCT_BRIDGE=32'h2C000000,   // 8 KiB aligned: path struct here, read window at +0x1000
    parameter LIBCRYPT=0,                          // the firmware reads a cue's .sbi into the table's LibCrypt mask
    parameter DEBUG=0,                             // keep the firmware's probe words (dbg_words) for a readout
    // Firmware byte lanes, whole file names (Quartus ignores a $readmemh
    // name built by concatenation).
    // (each with its own keyword: Quartus sizes a bare follower by its
    // first value and kept only ".hex")
    parameter FIRMWARE0="psx_cue_fw0.hex", parameter FIRMWARE1="psx_cue_fw1.hex",
    parameter FIRMWARE2="psx_cue_fw2.hex", parameter FIRMWARE3="psx_cue_fw3.hex"
) (
    input wire clk, reset,
    input wire in_menu,                          // core_bridge_cmd osnotify_inmenu
    input wire allcomplete,                      // APF: every slot loaded
    input wire [1:0] disc_setting,               // the core menu's Disc (number - 1; 0 without --disc-menu)
    output reg s_allcomplete=0,                  // the stream's: after a launch pick is mounted
    // APF bridge
    input wire bridge_wr, bridge_rd,
    input wire [31:0] bridge_addr, bridge_data,
    output wire [31:0] struct_rd_data,           // STRUCT_BRIDGE reads: the word of the previous bridge_rd
    // APF slot updates in; the stream's copy out
    input wire slot_update,
    input wire [15:0] slot_update_id,
    input wire [31:0] slot_update_size,
    output wire s_slot_update,
    output wire [15:0] s_slot_update_id,
    output wire [31:0] s_slot_update_size,
    // The stream's bridge writes: APF's plus the generated table
    output wire s_bridge_wr,
    output wire [31:0] s_bridge_addr, s_bridge_data,
    // The stream's ID/size table view (q one clock after the address)
    input wire [9:0] table_addr,
    input wire [31:0] table_q,
    output wire [31:0] table_view,
    output wire table_grant,                     // low while the firmware loads the disc the menu asks for
    // Upstream target port (the stream): valid held until ready, fields held
    // until done; its bridge window is 4 KiB aligned
    input wire u_read,
    output wire u_ready,
    input wire [15:0] u_id,
    input wire [31:0] u_offset, u_bridge, u_length,
    output reg u_done=1,
    output reg [2:0] u_error=0,
    // Downstream: core_bridge_cmd (a rising edge starts a command; the
    // fields hold until done)
    output reg d_read=0, d_getfile=0, d_openfile=0,
    output wire [15:0] d_id,
    output wire [31:0] d_offset, d_bridge, d_length,
    input wire d_done,
    input wire [2:0] d_error,
    output wire [31:0] param_struct, resp_struct,
    output reg cue_mounted=0,                     // a cue is the mounted disc
    output reg mount_failed=0,                    // the last cue did not mount
    // DEBUG: word 0 {s_allcomplete, cue_mounted, mount_failed, boot_pending,
    // fw_idle, W_DBG+7 (open forms tried 26:16, LibCrypt mask 15:0)}, words 1-7 the firmware's probe words (cue_mount.c W_DBG)
    output wire [255:0] dbg_words
);
    // The firmware's slot numbers (cue_mount.c).
    localparam [15:0] DISC_SLOT=16'd101, TABLE_SLOT=16'd102, FILE_SLOT=16'd103;
    localparam [31:0] CHUNK_BRIDGE=STRUCT_BRIDGE+32'h1000;
    assign param_struct=STRUCT_BRIDGE;
    assign resp_struct=STRUCT_BRIDGE;

    // ---------------- CPU ----------------
    wire [31:0] ib_adr, db_adr, db_dat, db_rdt;
    wire [3:0] db_sel;
    wire ib_cyc, db_we, db_cyc;
    reg ack=0;
    serv_rf_top #(.RESET_PC(32'd0), .WITH_CSR(0), .RESET_STRATEGY("MINI")) cpu (
        .clk(clk), .i_rst(reset), .i_timer_irq(1'b0),
        .o_ibus_adr(ib_adr), .o_ibus_cyc(ib_cyc), .i_ibus_rdt(db_rdt), .i_ibus_ack(ack && ib_cyc),
        .o_dbus_adr(db_adr), .o_dbus_dat(db_dat), .o_dbus_sel(db_sel), .o_dbus_we(db_we), .o_dbus_cyc(db_cyc),
        .i_dbus_rdt(db_rdt), .i_dbus_ack(ack && db_cyc),
        .o_ext_rs1(), .o_ext_rs2(), .o_ext_funct3(), .i_ext_rd(32'd0), .i_ext_ready(1'b0), .o_mdu_valid());
    // SERV fetches and accesses data one at a time.
    wire [31:0] adr=db_cyc ? db_adr : ib_adr;
    wire io=db_cyc && adr[31];
    wire inj_req=io && adr[30] && db_we;
    wire io_wr=io && !adr[30] && !adr[5] && db_we;
    wire dbg_wr=io && !adr[30] && adr[5] && db_we;
    wire upd_wr=io_wr && adr[4:2]==3'd4;
    // An access completes in one clock (RAM and register reads registered);
    // a table write waits for a clock APF isn't writing, a slot update write
    // for one with no APF update to forward.
    wire go=(ib_cyc || db_cyc) && !ack && !(inj_req && bridge_wr) && !(upd_wr && slot_update);
    always @(posedge clk) ack<=!reset && go;

    // ---------------- RAM ----------------
    // 4,096 words: firmware and data; 0x2000 path struct, 0x2200 read
    // window (APF's side: STRUCT_BRIDGE and CHUNK_BRIDGE).
    wire [11:0] a_addr=adr[13:2];
    wire a_we=db_cyc && db_we && !adr[31] && go;
    wire apf_win=bridge_addr[31:13]==STRUCT_BRIDGE[31:13] && bridge_addr[11:9]==3'd0;
    wire [11:0] apf_word={4'b1000,bridge_addr[12],bridge_addr[8:2]};
    // APF samples a read's data before it pulses bridge_rd, so a read
    // returns the word at the previous read's address (as core_bridge_cmd's
    // registers and the memory card slot do); APF reads one word ahead.
    // Following the live address handed APF the struct a word late, and
    // every openfile came back 4 (outside /Assets) on hardware.
    reg [11:0] b_rd_addr=0; reg rd_1=0;
    always @(posedge clk) begin
        rd_1<=bridge_rd;
        if(bridge_rd && !rd_1) b_rd_addr<=apf_word;
    end
    wire [11:0] b_addr=bridge_wr ? apf_word : b_rd_addr;
    wire [31:0] a_q, b_q;
    // One true dual-port RAM per byte lane (byte stores from the CPU).
    wire b_we=bridge_wr && apf_win;
    psx_cue_ram_lane #(.FIRMWARE(FIRMWARE0)) ram0(.clk(clk),
        .a_addr(a_addr),.a_we(a_we && db_sel[0]),.a_d(db_dat[7:0]),.a_q(a_q[7:0]),
        .b_addr(b_addr),.b_we(b_we),.b_d(bridge_data[7:0]),.b_q(b_q[7:0]));
    psx_cue_ram_lane #(.FIRMWARE(FIRMWARE1)) ram1(.clk(clk),
        .a_addr(a_addr),.a_we(a_we && db_sel[1]),.a_d(db_dat[15:8]),.a_q(a_q[15:8]),
        .b_addr(b_addr),.b_we(b_we),.b_d(bridge_data[15:8]),.b_q(b_q[15:8]));
    psx_cue_ram_lane #(.FIRMWARE(FIRMWARE2)) ram2(.clk(clk),
        .a_addr(a_addr),.a_we(a_we && db_sel[2]),.a_d(db_dat[23:16]),.a_q(a_q[23:16]),
        .b_addr(b_addr),.b_we(b_we),.b_d(bridge_data[23:16]),.b_q(b_q[23:16]));
    psx_cue_ram_lane #(.FIRMWARE(FIRMWARE3)) ram3(.clk(clk),
        .a_addr(a_addr),.a_we(a_we && db_sel[3]),.a_d(db_dat[31:24]),.a_q(a_q[31:24]),
        .b_addr(b_addr),.b_we(b_we),.b_d(bridge_data[31:24]),.b_q(b_q[31:24]));
    assign struct_rd_data=b_q;

    // ---------------- registers ----------------
    reg [8:0] fw_id=0; reg [12:0] fw_len=0; reg [31:0] fw_off=0, disc_bytes=0;
    reg use_u=0, view_disc=0, view_table=0, fw_idle=0, accept_any=1;   // picks wait while the firmware starts
    // The menu Disc the firmware's disc was loaded for. With the Disc menu
    // (DISC_STEP 100) every disc slot n01 (table n02) shows the firmware's
    // disc; a new menu Disc holds the stream's table lookups until the
    // firmware has loaded that disc, so the stream mounts the one it asked for.
    reg [1:0] fw_disc=0;
    wire menu_hold=view_disc && disc_setting!=fw_disc;
    assign table_grant=!menu_hold;
    reg cmd_busy=0, cmd_low=0; reg [2:0] cmd_err=0;
    reg u_busy=0, upd_pending=0, boot_pending=0, prev_allcomplete=0;
    reg menu_seen=0;   // the menu was open since the firmware last cleared this
    // I/O reads: SERV holds the address until ack, so the value can be
    // combinational in the ack clock.
    // Slots are below 512 and the stream's reads at most 2,352 bytes.
    wire [31:0] io_q=adr[2] ? u_offset : {LIBCRYPT!=0,u_length[11:0],u_id[8:0],disc_setting,menu_hold,boot_pending,upd_pending,u_busy,cmd_busy,cmd_err};
    assign db_rdt=io ? io_q : a_q;
    assign d_id={7'd0,fw_id};
    assign d_offset=fw_off;
    assign d_length={19'd0,fw_len};
    assign d_bridge=use_u ? u_bridge : CHUNK_BRIDGE;
    assign u_ready=!u_busy && fw_idle && !upd_pending && !reset;

    wire inj=inj_req && go;
    assign s_bridge_wr=bridge_wr || inj;
    assign s_bridge_addr=bridge_wr ? bridge_addr : {u_bridge[31:12],adr[11:0]};
    assign s_bridge_data=bridge_wr ? bridge_data : db_dat;

    // The stream reads an entry's size word only right after its ID word
    // (psx_cd_stream: tsize_word), so the last ID read names the entry.
    reg tv_size=0, tv_disc=0, tv_table=0;
    wire [15:0] tid=table_q[15:0];
    wire [3:0] tv_d={tid==16'd401,tid==16'd301,tid==16'd201,tid==DISC_SLOT};
    wire [3:0] tv_t={tid==16'd402,tid==16'd302,tid==16'd202,tid==TABLE_SLOT};
    always @(posedge clk) begin
        tv_size<=table_addr[0];
        if(!tv_size) begin tv_disc<=|tv_d && view_disc;tv_table<=|tv_t && view_table;end
    end
    assign table_view=tv_size && tv_disc ? disc_bytes : tv_size && tv_table ? 32'd1600 : table_q;

    // Slot updates to the stream: APF's (not DISC_SLOT, FILE_SLOT), or the
    // firmware's DISC_SLOT one (go keeps the two apart).
    wire upd_fw=upd_wr && go;
    assign s_slot_update=!reset && (upd_fw || slot_update && slot_update_id!=DISC_SLOT && slot_update_id!=FILE_SLOT);
    assign s_slot_update_id=upd_fw ? DISC_SLOT : slot_update_id;
    assign s_slot_update_size=upd_fw ? db_dat : slot_update_size;

    always @(posedge clk) begin
        d_read<=0;d_getfile<=0;d_openfile<=0;
        if(reset) begin
            s_allcomplete<=0;cue_mounted<=0;mount_failed<=0;view_disc<=0;view_table<=0;fw_idle<=0;accept_any<=1;
            cmd_busy<=0;u_busy<=0;u_done<=1;u_error<=0;upd_pending<=0;boot_pending<=0;
        end else begin
            // A DISC_SLOT update is a new pick only while the firmware takes
            // any, or after the menu was open (every real pick goes through
            // it; the firmware clears menu_seen after a pick and after its
            // own openfiles).
            prev_allcomplete<=allcomplete;
            if(allcomplete && !prev_allcomplete) boot_pending<=1;
            if(in_menu) menu_seen<=1;
            if(slot_update && slot_update_id==DISC_SLOT && (menu_seen || in_menu || accept_any)) begin
                upd_pending<=1;
            end

            if(u_read && u_ready) begin u_busy<=1;u_done<=0;end

            // Downstream: done drops, then rises.
            if(cmd_busy) begin
                if(!d_done) cmd_low<=1;
                else if(cmd_low) begin cmd_busy<=0;cmd_err<=d_error;end
            end

            if(io_wr && go) case(adr[4:2])
                3'd0: begin   // a command: kind, into the stream's window
                    d_read<=db_dat[1:0]==2'd0;d_getfile<=db_dat[1:0]==2'd1;d_openfile<=db_dat[1:0]==2'd2;
                    use_u<=db_dat[2];cmd_busy<=1;cmd_low<=0;
                end
                3'd1: begin fw_id<=db_dat[8:0];fw_len<=db_dat[28:16];end
                3'd2: fw_off<=db_dat;
                3'd3: begin u_error<=db_dat[2:0];u_done<=1;u_busy<=0;end
                3'd4: ;   // s_slot_update
                3'd5: begin
                    s_allcomplete<=db_dat[0];cue_mounted<=db_dat[1];mount_failed<=db_dat[2];
                    view_disc<=db_dat[3];view_table<=db_dat[4];fw_idle<=db_dat[5];accept_any<=db_dat[6];
                    fw_disc<=db_dat[9:8];
                end
                3'd6: disc_bytes<=db_dat;
                default: begin
                    if(db_dat[5]) upd_pending<=0;
                    if(db_dat[6]) boot_pending<=0;
                    if(db_dat[7] && !in_menu) menu_seen<=0;
                end
            endcase
            if(!allcomplete) s_allcomplete<=0;
        end
    end

    // Probe words: firmware stores to W_DBG+n (0x80000020+4n).
    generate if(DEBUG) begin : probe
        reg [31:0] dbg[0:6];
        reg [26:0] dbg_mask=0;
        integer k;
        initial for(k=0;k<7;k=k+1) dbg[k]=0;
        always @(posedge clk) if(dbg_wr && go) begin
            if(adr[4:2]==3'd7) dbg_mask<=db_dat[26:0]; else dbg[adr[4:2]]<=db_dat;
        end
        assign dbg_words={dbg[6],dbg[5],dbg[4],dbg[3],dbg[2],dbg[1],dbg[0],
                          s_allcomplete,cue_mounted,mount_failed,boot_pending,fw_idle,dbg_mask};
    end else begin : no_probe
        assign dbg_words=256'd0;
    end endgenerate
endmodule

// One byte lane of psx_cue_mount's RAM: 4,096 x 8, two read/write ports,
// loaded with one byte of each firmware word (FIRMWARE, one byte per line).
module psx_cue_ram_lane #(parameter FIRMWARE="psx_cue_fw0.hex") (
    input wire clk,
    input wire [11:0] a_addr, b_addr,
    input wire a_we, b_we,
    input wire [7:0] a_d, b_d,
    output reg [7:0] a_q=0, b_q=0
);
    // no_rw_check: the read-during-write result is never used. A CPU store
    // ignores the read data. APF writes only the windows (0x2000-0x23FF),
    // which the firmware does not touch while a command that fills them is
    // running (it polls an I/O register). APF reads the path struct only
    // during openfile, while the firmware waits. So neither port reads a
    // byte the other is writing in the same clock (Quartus 276003 without it).
    (* ramstyle="M10K, no_rw_check" *) reg [7:0] mem[0:4095];
    initial $readmemh(FIRMWARE, mem);
    always @(posedge clk) begin
        if(a_we) mem[a_addr]<=a_d;
        a_q<=mem[a_addr];
    end
    always @(posedge clk) begin
        if(b_we) mem[b_addr]<=b_d;
        b_q<=mem[b_addr];
    end
endmodule
