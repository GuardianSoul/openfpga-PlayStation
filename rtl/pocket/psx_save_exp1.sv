// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// Pocket save states, CPU-assisted (notes/save-states-design.md, design B):
// a small device on the PlayStation's expansion region 1 (EXP1, 0x1F000000,
// the address a cheat cartridge uses) plus the APF save/load handshake.
//
// On an APF save or load request the block raises `trap` until the save
// routine acknowledges it. The CPU (prepare --save-states) takes the trap
// like an interrupt, only with interrupts enabled, and runs the routine in
// ROM here. The routine moves the machine state through a 2 KiB staging page
// and asks this block to copy the page to or from the 4 MiB save buffer
// (PSRAM die 1 through psx_vram_psram's aux port, SAVE_AUX), the same
// buffer the Pocket reads and writes over the bridge (psx_memcard_slot
// SAVE_BASE). When it is done it reports OK or an error, which the
// handshake passes to the Pocket.
//
// EXP1 map (ext bus address, 13 bits; byte accesses, 8-bit bus):
//   0x0000-0x0FFF  routine ROM (read; ROM_HEX)
//   0x1000-0x17FF  staging page (read/write)
//   0x1800  read STATUS: [0] copy busy, [1] save requested, [2] load requested
//           write COMMAND: [0] start a copy, [1] direction (0 page -> buffer,
//           1 buffer -> page); the page number is PAGE_LO/PAGE_HI
//   0x1801  write PAGE_LO, 0x1802 write PAGE_HI ([2:0]): 2 KiB page of the buffer
//   0x1803  write FINISH: [0] OK, [1] error (ends the request), [2] retry:
//           the machine is not at a quiet point (a DMA or GPU command is
//           running); trap falls and rises again about 1 ms later (the
//           handler counts the retries and ends a request with an error
//           after about 1.5 s)
//   0x1804  write ACK: the routine is running; trap falls
//   0x1805  write SET_IRQ_LO, 0x1806 SET_IRQ_HI ([2:0]): I_STAT bits to set
//   0x1807  write SET_DMA ([6:0]): DICR flag bits (24-30) to set
//   0x1808-0x180A  timer 0-2 hidden flags, {irqDone, T_MODE[12:10]}:
//           read the live value (no side effect), write the value to set
//   0x180B  read DISC: the disc in the drive - 1 (0-3)
//           write SET_GO: [0] while 1 the set values are applied (IRQ and
//           DMA bits ORed in, timer flags forced); the routine writes 1, then 0
//   0x180C  write SPU_HOLD ([0]): stop the SPU at a quiet point (no new
//           sample); read [0] the SPU is quiet (voice loop idle)
//   0x180D, 0x180E  write SS_ADR (9 bits): SPU savestate port address,
//           which is also the staging page word the port word goes through
//   0x180F  write SS_CMD: [0] read the port word into page word SS_ADR,
//           [1] write page word SS_ADR to the port; read [0] busy
//           (the SPU's own savestate port, prepare_save_states.py spu tap)
//   COMMAND bit 2 (with [1] direction) copies the staging page to or from
//           SPU RAM instead of the buffer: 1024 halfwords of the 2 KiB SPU
//           RAM page PAGE_LO, through the SPU's savestate RAM port (only
//           while the SPU is held quiet)
//   0x1810-0x1817  read the GPU's display ranges (GP1 05/06/07 values, which
//           no register reads back): {vDisplayRange[19:0], hDisplayRange[23:0],
//           vramRange[18:0]} little-endian, bit 0 = vramRange[0]
// The set/flag taps are state no game can write (or read without clearing):
// prepare_save_states.py wires them into irq.vhd, dma.vhd and timer.vhd.
// Reads are combinational from the current address (no read side effects),
// so the bus timing of memorymux's EXT state machine is always met: data
// follows the address one clk2x later.
//
// Clocks: the bus side and the copy engine run on clk2x; the bus signals
// are clk1x registers of memorymux (clk1x and clk2x share the PLL, so they
// are sampled directly; strobes are edge-detected). The APF side is clk_74a
// with two-flop synchronisers on toggles both ways.
//
// Aux port: the copy engine is a client in front of the card slot's aux
// requests (up_*). A card access waits while a copy word is in flight and
// vice versa; nothing else changes for the card.
`default_nettype none
module psx_save_exp1 #(
    parameter ROM_HEX = "",
    parameter [31:0] SAVE_BASE = 32'h3000_0000,
    parameter [31:0] SAVE_SIZE = 32'h0040_0000
) (
    input  wire        clk2x,
    input  wire        reset_2x,
    // EXP1 bus (memorymux, clk1x registers)
    input  wire [12:0] bus_addr,
    input  wire        bus_write,
    input  wire [7:0]  bus_dataWrite,
    output reg  [7:0]  bus_dataRead = 8'd0,
    output reg         trap = 1'b0,
    // hidden-state taps (clk1x side reads them as levels)
    output reg         set_go = 1'b0,
    output reg  [10:0] set_irq = 11'd0,
    output reg  [6:0]  set_dma = 7'd0,
    output reg  [11:0] set_timer = 12'd0,
    input  wire [11:0] timer_flags,
    input  wire [62:0] gpu_ranges,
    // SPU tap (clk1x side: strobes are one clk1x cycle)
    input  wire        clk1x,
    output reg         spu_hold = 1'b0,
    input  wire        spu_quiet,
    output wire [8:0]  spu_ss_adr,
    output wire [31:0] spu_ss_wdata,
    output reg         spu_ss_rden = 1'b0,
    output reg         spu_ss_wren = 1'b0,
    input  wire [31:0] spu_ss_rdata,
    output reg         spuram_req = 1'b0,
    output reg         spuram_rnw = 1'b1,
    output wire [18:0] spuram_adr,
    output wire [15:0] spuram_wdata,
    input  wire [15:0] spuram_rdata,
    input  wire        spuram_done,
    // aux port toward psx_vram_psram
    output wire        aux_req,
    output wire        aux_we,
    output wire [20:0] aux_addr,
    output wire [31:0] aux_din,
    input  wire [31:0] aux_dout,
    input  wire        aux_done,
    // the card slot's aux requests
    input  wire        up_req,
    input  wire        up_we,
    input  wire [20:0] up_addr,
    input  wire [31:0] up_din,
    output wire [31:0] up_dout,
    output wire        up_done,
    // APF (clk_74a)
    input  wire        clk_74a,
    output wire        savestate_supported,
    output wire [31:0] savestate_addr,
    output wire [31:0] savestate_size,
    output wire [31:0] savestate_maxloadsize,
    input  wire        savestate_start,
    output reg         savestate_start_ack = 1'b0,
    output reg         savestate_start_busy = 1'b0,
    output reg         savestate_start_ok = 1'b0,
    output reg         savestate_start_err = 1'b0,
    input  wire        savestate_load,
    output reg         savestate_load_ack = 1'b0,
    output reg         savestate_load_busy = 1'b0,
    output reg         savestate_load_ok = 1'b0,
    output reg         savestate_load_err = 1'b0,
    // the disc in the drive - 1 (multi-disc game files), kept in the state's
    // magic word so a state of another disc does not load
    input  wire [1:0]  disc
);
    assign savestate_supported = 1'b1;
    assign savestate_addr = SAVE_BASE;
    assign savestate_size = SAVE_SIZE;
    assign savestate_maxloadsize = SAVE_SIZE;

    // ------------------------------------------------------------------
    // APF side (clk_74a): one request at a time
    // ------------------------------------------------------------------
    reg start_d = 1'b0, load_d = 1'b0;
    reg req_tgl_74 = 1'b0, req_load_74 = 1'b0;   // request toggle and its kind (held)
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    reg [1:0] s74_fin = 2'b00;
    reg fin_seen_74 = 1'b0;
    wire busy_74 = savestate_start_busy || savestate_load_busy;
    always @(posedge clk_74a) begin
        start_d <= savestate_start; load_d <= savestate_load;
        savestate_start_ack <= 1'b0; savestate_load_ack <= 1'b0;
        s74_fin <= {s74_fin[0], fin_tgl_2x};
        if (savestate_start && !start_d && !busy_74) begin
            savestate_start_ack <= 1'b1; savestate_start_busy <= 1'b1;
            savestate_start_ok <= 1'b0; savestate_start_err <= 1'b0;
            req_load_74 <= 1'b0; req_tgl_74 <= !req_tgl_74;
        end else if (savestate_load && !load_d && !busy_74) begin
            savestate_load_ack <= 1'b1; savestate_load_busy <= 1'b1;
            savestate_load_ok <= 1'b0; savestate_load_err <= 1'b0;
            req_load_74 <= 1'b1; req_tgl_74 <= !req_tgl_74;
        end
        if (s74_fin[1] != fin_seen_74) begin
            fin_seen_74 <= s74_fin[1];
            if (savestate_start_busy) begin
                savestate_start_busy <= 1'b0; savestate_start_ok <= fin_ok_2x; savestate_start_err <= !fin_ok_2x;
            end
            if (savestate_load_busy) begin
                savestate_load_busy <= 1'b0; savestate_load_ok <= fin_ok_2x; savestate_load_err <= !fin_ok_2x;
            end
        end
    end

    // ------------------------------------------------------------------
    // clk2x: request state, bus registers
    // ------------------------------------------------------------------
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    reg [1:0] s2x_req = 2'b00;
    reg req_seen_2x = 1'b0, fin_tgl_2x = 1'b0, fin_ok_2x = 1'b0;
    reg want_save = 1'b0, want_load = 1'b0;
    reg wr_d = 1'b0;
    reg [15:0] retry_wait = 16'd0;
    reg [7:0] page_lo = 8'd0; reg [2:0] page_hi = 3'd0;
    wire wr_edge = bus_write && !wr_d;
    wire reg_area = bus_addr[12] && bus_addr[11];
    wire page_area = bus_addr[12] && !bus_addr[11];

    // ROM (8-bit, read every clock) and staging page: 8-bit on the bus
    // side, 32-bit on the copy engine's side (word k = bytes 4k..4k+3)
    reg [7:0] rom [0:4095];
    initial if (ROM_HEX != "") $readmemh(ROM_HEX, rom);
    reg [7:0] rom_q = 8'd0;
    always @(posedge clk2x) rom_q <= rom[bus_addr[11:0]];
    wire [7:0] page_q;
    // page word cp_word, valid from the second clk2x edge after it is set:
    // aux_req and spuram_req are registered, so a request started on the
    // edge that sets cp_word reaches its consumer when cp_q is valid
    wire [31:0] cp_q;
    reg [8:0] cp_word = 9'd0;      // copy word, SPU RAM word, SPU port address
    wire cp_we;
    reg [31:0] cp_wd;
    // The page as the MiSTer dpram_dif (altsyncram): two behavioural write
    // ports were built from registers (2026-10-18 Fit).
    dpram_dif #(.addr_width_a(11), .data_width_a(8), .addr_width_b(9), .data_width_b(32)) page (
        .clock_a(clk2x), .clken_a(1'b1), .address_a(bus_addr[10:0]), .data_a(bus_dataWrite),
        .wren_a(wr_edge && page_area), .q_a(page_q),
        .clock_b(clk2x), .clken_b(1'b1), .address_b(cp_word), .data_b(cp_wd), .wren_b(cp_we), .q_b(cp_q));

    reg cp_busy = 1'b0;
    wire [63:0] gpu_bytes = {1'b0, gpu_ranges};
    // SPU savestate port: a command toggles to clk1x, which pulses the strobe
    // for one clk1x cycle and toggles back (clk1x and clk2x share the PLL);
    // read data goes into the page word when the toggle is back
    reg ss_tgl = 1'b0, ss_wr = 1'b0, ss_seen1 = 1'b0, ss_ack1 = 1'b0, ss_ack2 = 1'b0, ss_ack3 = 1'b0;
    wire ss_busy = ss_tgl != ss_ack3;
    wire ss_store = ss_ack2 != ss_ack3 && !ss_wr;
    // The SPU's context RAMs sample this address on the falling clk1x edge:
    // hand it over in a clk1x register (cp_word is set by SS_ADR writes
    // several bus cycles before the SS_CMD write that pulses the strobe).
    reg [8:0] ss_adr_1x = 9'd0;
    always @(posedge clk1x) ss_adr_1x <= cp_word;
    assign spu_ss_adr = ss_adr_1x;
    assign spu_ss_wdata = cp_q;
    always @(posedge clk1x) begin
        spu_ss_rden <= 1'b0; spu_ss_wren <= 1'b0;
        if (ss_tgl != ss_seen1) begin
            ss_seen1 <= ss_tgl;
            if (ss_wr) spu_ss_wren <= 1'b1; else spu_ss_rden <= 1'b1;
        end
        ss_ack1 <= ss_seen1;   // the strobe's clk1x edge has passed
    end
    always @(*) begin
        if (reg_area && bus_addr[4]) bus_dataRead = gpu_bytes[8*bus_addr[2:0] +: 8];
        else if (reg_area) case (bus_addr[3:0])
            4'd8:    bus_dataRead = {4'd0, timer_flags[3:0]};
            4'd9:    bus_dataRead = {4'd0, timer_flags[7:4]};
            4'd10:   bus_dataRead = {4'd0, timer_flags[11:8]};
            4'd11:   bus_dataRead = {6'd0, disc};
            4'd12:   bus_dataRead = {7'd0, spu_quiet};
            4'd15:   bus_dataRead = {7'd0, ss_busy};
            default: bus_dataRead = {5'd0, want_load, want_save, cp_busy};
        endcase
        else bus_dataRead = bus_addr[12] ? page_q : rom_q;
    end

    // ------------------------------------------------------------------
    // copy engine: 512 words between the page and the save buffer
    // ------------------------------------------------------------------
    reg cp_dir = 1'b0;             // 0 page -> buffer, 1 buffer -> page
    reg [1:0] cp_st = 2'd0;
    reg own_req = 1'b0;            // a copy word is on the aux port
    reg up_go = 1'b0;              // a card access is on the aux port
    localparam CP_IDLE = 2'd0, CP_AUX = 2'd2;
    // SPU RAM mode: the two halfwords of each word through the SPU's RAM port
    reg sp_hi = 1'b0;
    reg sp_tgl = 1'b0, sp_seen1 = 1'b0, sp_pend = 1'b0, sp_done1 = 1'b0, sp_done2 = 1'b0, sp_done3 = 1'b0;
    reg [15:0] sp_q1 = 16'd0, sp_lo = 16'd0;
    reg [1:0] sp_st = 2'd0;
    localparam SP_WAIT = 2'd2;
    wire sp_done = sp_done3 != sp_done2;
    assign spuram_adr = {page_lo, cp_word, sp_hi, 1'b0};
    assign spuram_wdata = sp_hi ? cp_q[31:16] : cp_q[15:0];
    always @(posedge clk1x) begin
        spuram_req <= 1'b0;
        if (sp_tgl != sp_seen1) begin sp_seen1 <= sp_tgl; spuram_req <= 1'b1; sp_pend <= 1'b1; end
        // done also pulses for the SPU's own accesses: count only ours
        if (spuram_done && sp_pend) begin sp_q1 <= spuram_rdata; sp_done1 <= !sp_done1; sp_pend <= 1'b0; end
    end
    // the card goes first when both want the port
    wire up_start  = up_req && !up_go && !own_req;
    wire own_start = cp_st == CP_AUX && !own_req && !up_go && !up_start;

    assign aux_req  = own_req || up_go;
    assign aux_we   = own_req ? !cp_dir : up_we;
    assign aux_addr = own_req ? {1'b1, page_hi, page_lo, cp_word} : up_addr;
    assign aux_din  = own_req ? cp_q : up_din;
    assign up_dout  = aux_dout;
    assign up_done  = up_go && aux_done;

    // page writes of the copy engine: a buffer word, two SPU RAM halfwords,
    // an SPU port word
    wire buf_store = own_req && aux_done && cp_dir;
    wire sp_store = sp_st == SP_WAIT && sp_done && spuram_rnw && sp_hi;
    assign cp_we = buf_store || sp_store || ss_store;
    always @(*) begin
        if (cp_st == CP_AUX) cp_wd = aux_dout;
        else if (sp_st != CP_IDLE) cp_wd = {sp_q1, sp_lo};
        else cp_wd = spu_ss_rdata;
    end

    always @(posedge clk2x) begin
        s2x_req <= {s2x_req[0], req_tgl_74};
        ss_ack2 <= ss_ack1; ss_ack3 <= ss_ack2;
        sp_done2 <= sp_done1; sp_done3 <= sp_done2;
        wr_d <= bus_write;
        if (reset_2x) begin
            trap <= 1'b0; want_save <= 1'b0; want_load <= 1'b0; set_go <= 1'b0; retry_wait <= 16'd0;
            cp_busy <= 1'b0; cp_st <= CP_IDLE; own_req <= 1'b0; up_go <= 1'b0;
            spu_hold <= 1'b0; sp_st <= CP_IDLE;
        end else begin
            // a new APF request
            if (s2x_req[1] != req_seen_2x) begin
                req_seen_2x <= s2x_req[1];
                want_load <= req_load_74; want_save <= !req_load_74; trap <= 1'b1;
            end
            if (retry_wait != 16'd0) begin
                retry_wait <= retry_wait - 1'b1;
                if (retry_wait == 16'd1) trap <= 1'b1;
            end
            if (wr_edge && reg_area && !bus_addr[4]) case (bus_addr[3:0])
                4'd0: if (bus_dataWrite[0] && !cp_busy) begin
                          cp_busy <= 1'b1; cp_dir <= bus_dataWrite[1]; cp_word <= 9'd0; sp_hi <= 1'b0;
                          if (bus_dataWrite[2]) begin
                              sp_st <= SP_WAIT; spuram_rnw <= bus_dataWrite[1]; sp_tgl <= !sp_tgl;
                          end else cp_st <= CP_AUX;
                      end
                4'd1: page_lo <= bus_dataWrite;
                4'd2: page_hi <= bus_dataWrite[2:0];
                4'd3: if (bus_dataWrite[2]) begin
                          trap <= 1'b0; retry_wait <= 16'hFFFF;
                      end else begin
                          fin_ok_2x <= bus_dataWrite[0] && !bus_dataWrite[1];
                          fin_tgl_2x <= !fin_tgl_2x; want_save <= 1'b0; want_load <= 1'b0; trap <= 1'b0;
                      end
                4'd4: trap <= 1'b0;
                4'd5: set_irq[7:0] <= bus_dataWrite;
                4'd6: set_irq[10:8] <= bus_dataWrite[2:0];
                4'd7: set_dma <= bus_dataWrite[6:0];
                4'd8: set_timer[3:0] <= bus_dataWrite[3:0];
                4'd9: set_timer[7:4] <= bus_dataWrite[3:0];
                4'd10: set_timer[11:8] <= bus_dataWrite[3:0];
                4'd11: set_go <= bus_dataWrite[0];
                4'd12: spu_hold <= bus_dataWrite[0];
                4'd13: cp_word[7:0] <= bus_dataWrite;
                4'd14: cp_word[8] <= bus_dataWrite[0];
                4'd15: if (!ss_busy && bus_dataWrite[1:0] != 2'd0) begin ss_wr <= bus_dataWrite[1]; ss_tgl <= !ss_tgl; end
                default: ;
            endcase

            // SPU RAM mode of the copy engine
            case (sp_st)
                SP_WAIT: if (sp_done) begin
                    if (spuram_rnw && !sp_hi) sp_lo <= sp_q1;
                    if (!sp_hi) begin sp_hi <= 1'b1; sp_tgl <= !sp_tgl; end
                    else if (cp_word == 9'd511) begin sp_st <= CP_IDLE; cp_busy <= 1'b0; end
                    else begin
                        cp_word <= cp_word + 1'b1; sp_hi <= 1'b0; sp_tgl <= !sp_tgl;
                    end
                end
                default: ;
            endcase

            if (up_go) begin if (aux_done) up_go <= 1'b0; end
            else if (up_start) up_go <= 1'b1;
            if (own_start) own_req <= 1'b1;

            case (cp_st)
                CP_AUX: if (own_req && aux_done) begin
                    own_req <= 1'b0;
                    if (cp_word == 9'd511) begin cp_st <= CP_IDLE; cp_busy <= 1'b0; end
                    else cp_word <= cp_word + 1'b1;
                end
                default: ;
            endcase
        end
    end
endmodule
`default_nettype wire
