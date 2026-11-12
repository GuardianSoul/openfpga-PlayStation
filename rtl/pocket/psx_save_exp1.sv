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
//   0x0000-0x0FFF  routine ROM (read; bits 31:0 of ROM_HEX)
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
//   0x180D  write CD_PUT: set tap 7 with the byte; cd_top writes it into the
//           CD data FIFO once per tap (the next other register write ends it)
//   0x180E  write SAVE_MODE ([0]): the routine's stage 2 in SDRAM is reachable
//   0x180F  write CD_FREEZE ([0]): the CD controller stands still
//   (no other device; the SPU savestate port is not used, the
//   save routine restores the SPU by guest register writes)
//   COMMAND bit 2 (with [1] direction) copies the staging page to or from
//           SPU RAM instead of the buffer: 1024 halfwords of the 2 KiB SPU
//           RAM page PAGE_LO, through the SPU's savestate RAM port (only
//           while the SPU is held quiet)
//   0x1810-0x1817  read the GPU's display ranges (GP1 05/06/07 values, which
//           no register reads back): {vDisplayRange[19:0], hDisplayRange[23:0],
//           vramRange[18:0]} little-endian, bit 0 = vramRange[0]
//   0x1818-0x181F  read the CD controller state no register gives back
//           (cd_state, prepare_save_states.py cd tap): stat byte, mode,
//           XA filter file, channel, the last sector read (LBA, 3 bytes) and
//           [0] busy: a command or a second response or a drive interrupt is
//           on its way, or a sector is being copied into the data FIFO (a
//           command from the routine now would change that)
//   0x1818, 0x1819  write SIZE_LO, SIZE_HI ([3:0]): the state's size in 2 KiB
//           pages, which the Pocket copies (savestate_size; 2048 after reset).
//           Taken over by the APF side with FINISH.
//   0x1C00-0x1FFF  second routine ROM (read; 1 KiB, bits 39:32 of ROM_HEX)
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
// APF requests: one at a time; a request while one runs is acked with an
// error at once (the Pocket waits for every ack). A load that arrives while
// the Pocket holds the core in reset (core_held: a wake from Sleep, where the
// Pocket can send the state and the load before it lets the core run, and
// may wait for the answer first) is answered OK at once and runs when the
// CPU takes the trap after the reset; until then other requests get errors.
//
// Aux port: the copy engine is a client in front of the card slot's aux
// requests (up_*). A card access waits while a copy word is in flight and
// vice versa; nothing else changes for the card.
`default_nettype none
module psx_save_exp1 #(
    parameter ROM_HEX = "",     // 1024 x 40 bits: {second ROM byte i, first ROM word i}

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
    // SAVE_MODE (0x180E): the CPU's main RAM address bits 22:21 reach the SDRAM
    // above main RAM (prepare_save_states.py psx_top), where the routine's
    // stage 2 runs; 0 after reset
    output reg         save_mode = 1'b0,
    // CD_FREEZE (0x180F): cd_top's clock enable off (prepare_save_states.py
    // psx_top): no drive event, command or interrupt while the routine copies
    // (the Pocket's sector fetch still completes); 0 after reset
    output reg         cd_freeze = 1'b0,
    // hidden-state taps (clk1x side reads them as levels): the last of the
    // 0x1805-0x180A writes (set_sel 1-6: I_STAT 7:0, I_STAT 10:8, DICR
    // flags, timer 0-2 flags) and its byte, held until the next register
    // write. The target applies it while it is held (each target ORs or sets
    // the same bits again on every clock, so how long does not matter).
    output reg  [2:0]  set_sel = 3'd0,
    output reg  [7:0]  set_data = 8'd0,
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
    input  wire [1:0]  disc,
    // clk_74a: the Pocket holds the core in reset (bridge reset_n low)
    input  wire        core_held,
    // CD controller state (clk1x levels, read as they are; see 0x1818)
    input  wire [63:0] cd_state
);
    assign savestate_supported = 1'b1;
    assign savestate_addr = SAVE_BASE;
    assign savestate_maxloadsize = SAVE_SIZE;

    // ------------------------------------------------------------------
    // APF side (clk_74a): one request at a time
    // ------------------------------------------------------------------
    reg start_d = 1'b0, load_d = 1'b0;
    reg req_tgl_74 = 1'b0, req_load_74 = 1'b0;   // request toggle and its kind (held)
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    reg [1:0] s74_fin = 2'b00;
    reg fin_seen_74 = 1'b0;
    reg held_load_74 = 1'b0;   // a load answered in reset, not run yet
    wire busy_74 = savestate_start_busy || savestate_load_busy || held_load_74;
    always @(posedge clk_74a) begin
        start_d <= savestate_start; load_d <= savestate_load;
        savestate_start_ack <= 1'b0; savestate_load_ack <= 1'b0;
        s74_fin <= {s74_fin[0], fin_tgl_2x};
        if (savestate_start && !start_d) begin
            savestate_start_ack <= 1'b1;   // (a second save while one runs: only acked)
            if (!busy_74) begin
                savestate_start_busy <= 1'b1; savestate_start_ok <= 1'b0; savestate_start_err <= 1'b0;
                req_load_74 <= 1'b0; req_tgl_74 <= !req_tgl_74;
            end else if (!savestate_start_busy) begin
                savestate_start_ok <= 1'b0; savestate_start_err <= 1'b1;
            end
        end else if (savestate_load && !load_d) begin
            savestate_load_ack <= 1'b1;   // (a second load while one runs: only acked)
            if (!busy_74) begin
                savestate_load_busy <= !core_held; held_load_74 <= core_held;
                savestate_load_ok <= core_held; savestate_load_err <= 1'b0;
                req_load_74 <= 1'b1; req_tgl_74 <= !req_tgl_74;
            end else if (!savestate_load_busy && !held_load_74) begin
                savestate_load_ok <= 1'b0; savestate_load_err <= 1'b1;
            end
        end
        if (s74_fin[1] != fin_seen_74) begin
            fin_seen_74 <= s74_fin[1];
            held_load_74 <= 1'b0;
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
    reg [11:0] size_pages = 12'd2048;
    // pages, set by the routine before FINISH and held until the next
    // request (read on clk_74a as it is; SDC max delay)
    assign savestate_size = {9'd0, size_pages, 11'd0};
    wire wr_edge = bus_write && !wr_d;
    wire reg_area = bus_addr[12] && bus_addr[11] && bus_addr[10:6] == 5'd0;
    wire page_area = bus_addr[12] && !bus_addr[11];

    // Both ROMs in one 1024 x 40 ROM (4 M10K in 256 x 40 mode): entry i
    // holds word i of the first ROM (0x0000-0x0FFF) in bits 31:0 and byte i
    // of the second (0x1C00-0x1FFF) in bits 39:32, so the second ROM takes
    // the bits a 32-bit ROM leaves unused. Read every clock; the byte is
    // picked from the current address like the other sources.
    reg [39:0] rom [0:1023];
    initial if (ROM_HEX != "") $readmemh(ROM_HEX, rom);
    reg [39:0] rom_q = 40'd0;
    always @(posedge clk2x) rom_q <= rom[bus_addr[12] ? bus_addr[9:0] : bus_addr[11:2]];
    wire [7:0] rom2_q = rom_q[39:32];
    wire [7:0] rom1_q = rom_q[8*bus_addr[1:0] +: 8];
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
    // the SPU savestate port is unused (constant outputs)
    assign spu_ss_adr = 9'd0;
    assign spu_ss_wdata = 32'd0;
    always @(posedge clk1x) begin spu_ss_rden <= 1'b0; spu_ss_wren <= 1'b0; end
    // register reads: one 16-way byte select over {CD state, GPU ranges}
    // (0x1810-0x181F) and the 0x1800-0x180F registers
    wire [127:0] tap_bytes = {cd_state, 1'b0, gpu_ranges};
    wire [7:0] tap_q = tap_bytes[8*bus_addr[3:0] +: 8];
    always @(*) begin
        if (reg_area && bus_addr[4]) bus_dataRead = tap_q;
        else if (reg_area) case (bus_addr[3:0])
            4'd8:    bus_dataRead = {4'd0, timer_flags[3:0]};
            4'd9:    bus_dataRead = {4'd0, timer_flags[7:4]};
            4'd10:   bus_dataRead = {4'd0, timer_flags[11:8]};
            4'd11:   bus_dataRead = {6'd0, disc};
            4'd12:   bus_dataRead = {7'd0, spu_quiet};
            default: bus_dataRead = {5'd0, want_load, want_save, cp_busy};
        endcase
        else if (bus_addr[12] && bus_addr[11] && bus_addr[10]) bus_dataRead = rom2_q;
        else bus_dataRead = bus_addr[12] ? page_q : rom1_q;
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

    // page writes of the copy engine: a buffer word, two SPU RAM halfwords
    wire buf_store = own_req && aux_done && cp_dir;
    wire sp_store = sp_st == SP_WAIT && sp_done && spuram_rnw && sp_hi;
    assign cp_we = buf_store || sp_store;
    always @(*) cp_wd = cp_st == CP_AUX ? aux_dout : {sp_q1, sp_lo};

    always @(posedge clk2x) begin
        s2x_req <= {s2x_req[0], req_tgl_74};
        sp_done2 <= sp_done1; sp_done3 <= sp_done2;
        wr_d <= bus_write;
        if (reset_2x) begin
            trap <= 1'b0; want_save <= 1'b0; want_load <= 1'b0; set_sel <= 3'd0; retry_wait <= 16'd0; save_mode <= 1'b0; cd_freeze <= 1'b0;
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
            if (wr_edge && reg_area && !bus_addr[4] && bus_addr[3:0] == 4'd14) save_mode <= bus_dataWrite[0];
            if (wr_edge && reg_area && !bus_addr[4] && bus_addr[3:0] == 4'd15) cd_freeze <= bus_dataWrite[0];
            if (wr_edge && reg_area && bus_addr[4] && bus_addr[3:1] == 3'd4) begin
                if (bus_addr[0]) size_pages[11:8] <= bus_dataWrite[3:0];
                else size_pages[7:0] <= bus_dataWrite;
            end
            if (wr_edge && reg_area && !bus_addr[4]) begin
                set_sel <= bus_addr[3:0] >= 4'd5 && bus_addr[3:0] <= 4'd10 ? bus_addr[2:0] - 3'd4 :
                           bus_addr[3:0] == 4'd13 ? 3'd7 : 3'd0;
                set_data <= bus_dataWrite;
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
                4'd12: spu_hold <= bus_dataWrite[0];
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
