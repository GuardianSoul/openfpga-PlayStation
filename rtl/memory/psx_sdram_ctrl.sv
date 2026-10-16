// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// SDRAM controller for the Pocket PSX core, written from a behavioural
// specification of the port contract the MiSTer PSX core expects (same ports
// as MiSTer's sdram.sv, so it is a drop-in for psx_native_memory). The old
// controller is GPL-3.0-or-later and cannot share a bitstream with the
// GPL-2.0-only GTE.
//
// One access at a time on a single 16-bit SDR chip (AS4C32M16 class: 4 banks,
// 8192 rows, 1024 columns), CAS latency 2 or 3 (CAS_LATENCY), burst 2, single-location writes,
// clocked by clk3x (clk) with the chip clock inverted. Requests arrive from
// clk1x (clk_base) registers as one-clk1x pulses; ready/data for clk1x
// consumers are presented so they appear at the first clk1x edge after the
// event and last exactly one clk1x cycle.
//
// Byte address map: [9:1] column A8..A0, [25] column A9, [22:10] row,
// [24:23] bank, [26] chip (only with SINGLE_CHIP=0).
//
// Every access opens one row and closes it with auto-precharge on its last
// READ/WRITE, so nothing ever touches a precharging bank. After an access the
// next ACTIVE/REFRESH waits: 12 clk after a single read, 13 after a 4-word
// read, 4 after the last WRITE, 9 after a REFRESH (tRFC 80 ns).
`default_nettype none
module psx_sdram_ctrl #(
    parameter SINGLE_CHIP = 1,
    parameter integer POWERUP_CYCLES = 20480,   // >= 200 us at 101.6 MHz
    parameter integer REFRESH_CYCLES = 780,     // 8192 per 64 ms
    parameter integer CHAIN_LIMIT = 32,         // dmafifo words per row opening
    // CAS latency: 2 (as built so far) or 3. The AS4C32M16MSA-6 datasheet
    // allows CL2 only at tCK >= 12 ns; at 101.6 MHz (9.84 ns) it needs CL3.
    // CL3 delays every read beat by one clk, the same capture window, one
    // clk later (the read tags pass through one extra stage). That clk moves
    // read completion past a clk1x hand-off edge: reads, cache fills, DMA and
    // ch3 reads each take one clk1x (29.5 ns) longer; writes do not.
    parameter integer CAS_LATENCY = 2,
    // SPU_FIRST: a pending ch3 (SPU RAM) request is served right after
    // refresh, ahead of the DMA FIFO, ch1 and ch2, and ends a DMA FIFO write
    // chain. The SPU has a hard 768-clk1x sample deadline and waits for each
    // access; served last, a DMA burst or CPU traffic can starve it (audio
    // underflow seen on hardware, WipEout CD music). SPU RAM shares no
    // addresses with main RAM, so no ordering between channels changes.
    parameter integer SPU_FIRST = 0
) (
    input  wire         init,
    input  wire         clk,
    input  wire         clk_base,
    input  wire         SDRAM_EN,
    inout  wire [15:0]  SDRAM_DQ,
    output wire [12:0]  SDRAM_A,
    output wire         SDRAM_DQML,
    output wire         SDRAM_DQMH,
    output wire [1:0]   SDRAM_BA,
    output wire         SDRAM_nCS,
    output wire         SDRAM_nWE,
    output wire         SDRAM_nRAS,
    output wire         SDRAM_nCAS,
    output wire         SDRAM_CKE,
    output wire         SDRAM_CLK,
    input  wire         refreshForce,
    output reg          ram_idle = 0,
    input  wire [26:0]  ch1_addr,
    output reg  [127:0] ch1_dout = 0,
    output reg  [31:0]  ch1_dout32 = 0,
    input  wire [15:0]  ch1_din,
    input  wire         ch1_req,
    input  wire         ch1_rnw,
    input  wire         ch1_dma,
    input  wire [1:0]   ch1_cntDMA,
    input  wire         ch1_cache,
    output reg          ch1_ready = 0,
    output reg  [3:0]   cache_wr = 0,
    output reg  [31:0]  cache_data = 0,
    output reg  [7:0]   cache_addr = 0,
    output reg          dma_wr = 0,
    output reg          dma_reqprocessed = 0,
    output reg  [31:0]  dma_data = 0,
    input  wire [26:0]  ch2_addr,
    output reg  [31:0]  ch2_dout = 0,
    input  wire [31:0]  ch2_din,
    input  wire         ch2_req,
    input  wire         ch2_rnw,
    input  wire [3:0]   ch2_be,
    output reg          ch2_ready = 0,
    input  wire [26:0]  ch3_addr,
    output reg  [31:0]  ch3_dout = 0,
    input  wire [31:0]  ch3_din,
    input  wire         ch3_req,
    input  wire         ch3_rnw,
    input  wire [3:0]   ch3_be,
    output wire         ch3_ready,
    input  wire [26:0]  dmafifo_adr,
    input  wire [31:0]  dmafifo_data,
    input  wire         dmafifo_empty,
    output reg          dmafifo_read = 0
);
    // ---------------------------------------------------------------- pins
    localparam [2:0] C_NOP = 3'b111, C_ACT = 3'b011, C_READ = 3'b101, C_WRITE = 3'b100,
                     C_PRE = 3'b010, C_REF = 3'b001, C_MRS = 3'b000;
    (* useioff = 1 *) reg [2:0]  cmd = C_NOP;
    (* useioff = 1 *) reg [12:0] a = 0;
    (* useioff = 1 *) reg [1:0]  ba = 0;
    (* useioff = 1 *) reg [1:0]  dqm = 0;
    (* useioff = 1 *) reg        ncs = 0;
    (* useioff = 1 *) reg [15:0] dq_out = 0;
    (* useioff = 1 *) reg        dq_oe = 0;
    (* useioff = 1 *) reg [15:0] dq_in = 0;

    assign {SDRAM_nRAS, SDRAM_nCAS, SDRAM_nWE} = SDRAM_EN ? cmd : 3'bzzz;
    assign SDRAM_A = SDRAM_EN ? a : 13'bz;
    assign SDRAM_BA = SDRAM_EN ? ba : 2'bz;
    assign {SDRAM_DQMH, SDRAM_DQML} = SDRAM_EN ? dqm : 2'bzz;
    assign SDRAM_nCS = SDRAM_EN ? (SINGLE_CHIP ? 1'b0 : ncs) : 1'bz;
    assign SDRAM_DQ = (SDRAM_EN && dq_oe) ? dq_out : 16'bz;
    assign SDRAM_CKE = 1'b1;

    // Chip clock = inverted clk from a DDR output cell.
    altddio_out #(
        .extend_oe_disable("OFF"), .intended_device_family("Cyclone V"), .invert_output("OFF"),
        .lpm_hint("UNUSED"), .lpm_type("altddio_out"), .oe_reg("UNREGISTERED"),
        .power_up_high("OFF"), .width(1)
    ) clock_out (
        .datain_h(1'b0), .datain_l(1'b1), .outclock(clk), .dataout(SDRAM_CLK),
        .aclr(1'b0), .aset(1'b0), .oe(SDRAM_EN), .outclocken(1'b1), .sclr(1'b0), .sset(1'b0));

    always @(posedge clk) dq_in <= SDRAM_DQ;

    // ------------------------------------------------------- clk1x phase
    // base_toggle flips on every clk1x edge. At a clk edge, after_base is
    // high when the previous clk edge was a clk1x edge; before_base is high
    // one edge later, i.e. right before the next clk1x edge. Outputs read by
    // clk1x logic change only on before_base edges, so each lasts exactly one
    // clk1x cycle and is seen at the next clk1x edge.
    reg base_toggle = 0, base_seen = 0, before_base = 0;
    always @(posedge clk_base) base_toggle <= ~base_toggle;
    wire after_base = base_toggle ^ base_seen;

    // ------------------------------------------------------------ requests
    reg pend1 = 0, pend2 = 0, pend3 = 0, pend3_new = 0;
    // With CL3 a clk1x request starts one clk after its capture on the
    // after_base edge (pend1/pend2), so no clk1x request line reaches the pin
    // registers in one clk (clk3x timing). Its data still makes the same
    // before_base edge: the bench's CL3 minimum latencies are unchanged and
    // the means move by under 0.1 clk1x. With CL2 that clk would cost a clk1x
    // cycle, so CL2 keeps starting on the capture edge.
    localparam LATE_START = CAS_LATENCY == 3;
    wire req1 = pend1 || (!LATE_START && after_base && ch1_req);
    wire req2 = pend2 || (!LATE_START && after_base && ch2_req);
    wire req3 = pend3;           // ch3 is taken one clk after capture
    // Row address of the access an idle edge would start, in the same
    // priority. It is put on the pins on every idle edge: with a NOP or
    // REFRESH command the chip ignores the address, so the pin registers
    // need not wait for the refresh and init decisions (clk3x timing).
    // ch1_req / ch2_req select without after_base (CL2): a request still
    // high on a later edge of its clk1x cycle was taken (the controller is
    // busy for at least five edges) or is pending, so on an idle edge it
    // selects the same channel as req1 / req2.
    // With LATE_START (CL3) the controller also works from registered copies
    // of the DMA FIFO head and of a captured ch3 request, so the FIFO's RAM
    // output and the ch3 source mux do not reach the pin registers in one
    // clk either. The head register pops the FIFO itself (one word per two
    // clks at most, ahead of the 4-clk chained write slots).
    reg        head_v = 0;
    reg [26:0] head_adr = 0;
    reg [31:0] head_data = 0;
    reg [26:0] ch3_adr_q = 0;
    reg [26:0] ch1_adr_q = 0, ch2_adr_q = 0;   // address captured with pend1 / pend2
    reg [31:0] ch3_din_q = 0;
    reg [3:0]  ch3_be_q = 0;
    reg        ch3_rnw_q = 0;
    wire        fifo_v    = LATE_START ? head_v : !dmafifo_empty;
    wire [26:0] fifo_adr  = LATE_START ? head_adr : dmafifo_adr;
    wire [31:0] fifo_data = LATE_START ? head_data : dmafifo_data;
    wire [26:0] ch3_a     = LATE_START ? ch3_adr_q : ch3_addr;
    wire        spu_first = SPU_FIRST != 0 && pend3;
    wire [26:0] ch1_a     = LATE_START ? ch1_adr_q : ch1_addr;
    wire [26:0] ch2_a     = LATE_START ? ch2_adr_q : ch2_addr;
    wire [26:0] idle_addr = spu_first ? ch3_a : fifo_v ? fifo_adr : (pend1 || (!LATE_START && ch1_req)) ? ch1_a
                          : (pend2 || (!LATE_START && ch2_req)) ? ch2_a : ch3_a;

    // ------------------------------------------------------------- refresh
    reg [10:0] ref_timer = REFRESH_CYCLES[10:0];
    reg [3:0]  ref_owed = 0;
    reg        force_d = 0, force_pend = 0;
    wire force_rise = refreshForce && !force_d;

    // ------------------------------------------------------ init sequence
    reg [14:0] power_wait = 0;
    reg        power_done = 0;   // power_wait reached POWERUP_CYCLES (registered: clk3x timing)
    reg [5:0]  init_step = 0;
    reg        ready = 0;

    // -------------------------------------------------------- access state
    localparam [3:0] OP_IDLE = 0, OP_READ1 = 1, OP_READ4 = 2, OP_WRITE = 3, OP_WRITE1 = 4, OP_REFRESH = 5;
    // Read result kinds
    localparam [2:0] K_CH1 = 1, K_CH2 = 2, K_CH3 = 3, K_CACHE = 4, K_DMA = 5;
    reg [3:0]  op = OP_IDLE;
    reg [3:0]  step = 0;
    reg [3:0]  last = 0;                  // step at which op returns to idle
    reg [2:0]  kind = 0;
    reg [26:0] addr = 0;
    reg [31:0] wdata = 0;
    reg [3:0]  wbe = 0;
    reg        from_fifo = 0;
    reg [5:0]  chain = 0;
    reg [1:0]  cnt = 0;
    reg        ch3_write = 0;
    wire       busy = op != OP_IDLE;

    wire [12:0] row = addr[22:10];
    wire [1:0]  bank = addr[24:23];
    function [12:0] column(input [26:0] at, input [8:0] add, input autopre);
        reg [8:0] low;
        begin
            low = at[9:1] + add;
            column = {2'b00, autopre, at[25], low};
        end
    endfunction

    // Read capture pipeline: a READ issued at edge e has beat 0 in dq_in
    // after e+CAS_LATENCY+1 and beat 1 one edge later; the word is complete
    // at edge e+CAS_LATENCY+3. For CL3 the tags wait one edge in q_*.
    reg        q_v = 0;
    reg [2:0]  q_kind = 0;
    reg [1:0]  q_index = 0, q_first = 0, q_cnt = 0;
    reg [7:0]  q_line = 0;
    reg [4:0]  rd_v = 0;
    reg [2:0]  rd_kind [0:4];
    reg [1:0]  rd_index [0:4];
    reg [1:0]  rd_first [0:4];
    reg [7:0]  rd_line [0:4];
    reg [1:0]  rd_cnt [0:4];
    reg [15:0] low_half = 0;
    integer i;
    initial for (i = 0; i < 5; i = i + 1) begin
        rd_kind[i] = 0; rd_index[i] = 0; rd_first[i] = 0; rd_line[i] = 0; rd_cnt[i] = 0;
    end
    wire        word_done = rd_v[4];
    wire [31:0] word = {dq_in, low_half};
    wire [2:0]  wk = rd_kind[4];
    wire [1:0]  wj = rd_index[4];

    // clk1x events waiting for the next before_base edge
    reg p_ch1 = 0, p_ch2 = 0, p_ch3 = 0, p_dmarp = 0;
    reg [31:0] ch1_word = 0, ch2_word = 0;
    reg [2:0]  ch3_delay = 0;             // ch3 read ready 4 clk after the data

    // clk1x-facing values, copied into clk1x registers like the core's own
    // memory interface expects; a ch3 write's ready skips that register.
    reg x_ch1_ready = 0, x_ch2_ready = 0, x_dma_reqprocessed = 0, x_dma_wr = 0;
    reg x_ch3_read = 0, x_ch3_write = 0, p_ch3w = 0, ch3_read_ready = 0;
    reg [31:0] x_ch1_dout32 = 0, x_ch2_dout = 0, x_dma_data = 0;
    always @(posedge clk_base) begin
        ch1_ready <= x_ch1_ready; ch1_dout32 <= x_ch1_dout32;
        ch2_ready <= x_ch2_ready; ch2_dout <= x_ch2_dout;
        dma_reqprocessed <= x_dma_reqprocessed; dma_wr <= x_dma_wr; dma_data <= x_dma_data;
        ch3_read_ready <= x_ch3_read;
    end
    assign ch3_ready = ch3_read_ready || x_ch3_write;

    // DMA words for the clk1x stream
    // At most a READ4's words wait here (they drain one per clk1x), so the
    // ring never holds eight and a write never hits the word being read:
    // no read-during-write pass-through logic (simulation checks it).
    (* ramstyle = "MLAB, no_rw_check" *) reg [31:0] dma_q [0:7];
    reg [2:0]  dma_w = 0, dma_r = 0;
    reg [3:0]  dma_n = 0;
    initial for (i = 0; i < 8; i = i + 1) dma_q[i] = 0;

    // Same-edge views of events raised by the read pipeline
    wire ev_ch1_word  = word_done && (wk == K_CH1 || (wk == K_CACHE && wj == 0));
    // A cache fill is ready once its second word is in the cache RAM.
    reg  cache_second = 0;
    wire ev_ch1_ready = (word_done && wk == K_CH1) || cache_second;
    wire ev_ch2       = word_done && wk == K_CH2;
    wire ev_dma_word  = word_done && wk == K_DMA && wj <= rd_cnt[4];
    wire ev_ch3       = ch3_delay == 1;

    always @(posedge clk) begin
        reg took1, took2, start_ch1w, start_ch2, start_dmarp, start_ch3w, take_fifo;
        reg [3:0] chosen;
        took1 = 0; took2 = 0; start_ch1w = 0; start_ch2 = 0; start_dmarp = 0; start_ch3w = 0; take_fifo = 0;
        chosen = OP_IDLE;
        if (SDRAM_EN) begin
            base_seen <= base_toggle;
            before_base <= after_base;
            force_d <= refreshForce;
            if (force_rise) force_pend <= 1;

            // ch3 requests become eligible one clk after capture.
            if (after_base && ch3_req) begin
                pend3_new <= 1;
                ch3_adr_q <= ch3_addr; ch3_din_q <= ch3_din; ch3_be_q <= ch3_be; ch3_rnw_q <= ch3_rnw;
            end
            if (pend3_new) begin pend3 <= 1; pend3_new <= 0; end

            // Refresh interval
            if (ref_timer == 0) begin
                ref_timer <= REFRESH_CYCLES[10:0] - 1'b1;
                if (ref_owed != 4'd8) ref_owed <= ref_owed + 1'b1;
            end else ref_timer <= ref_timer - 1'b1;

            // Defaults for one-cycle pin and strobe outputs
            cmd <= C_NOP;
            dq_oe <= 0;
            dqm <= 2'b00;
            dmafifo_read <= 0;
            cache_wr <= 0;

            // ---------------------------------------------- read results
            rd_v <= {rd_v[3:0], CAS_LATENCY == 3 ? q_v : 1'b0};
            for (i = 4; i > 0; i = i - 1) begin
                rd_kind[i] <= rd_kind[i-1]; rd_index[i] <= rd_index[i-1]; rd_first[i] <= rd_first[i-1];
                rd_line[i] <= rd_line[i-1]; rd_cnt[i] <= rd_cnt[i-1];
            end
            rd_kind[0] <= CAS_LATENCY == 3 ? q_kind : 3'd0;
            if (CAS_LATENCY == 3) begin
                rd_index[0] <= q_index; rd_first[0] <= q_first; rd_line[0] <= q_line; rd_cnt[0] <= q_cnt;
                q_v <= 0;
            end
            if (rd_v[3]) low_half <= dq_in;
            if (ch3_delay != 0) ch3_delay <= ch3_delay - 1'b1;
            cache_second <= word_done && wk == K_CACHE && wj == 1;
            if (word_done) begin
                ch1_dout[wj*32 +: 32] <= word;
                case (wk)
                    K_CH1: ch1_word <= word;
                    K_CH2: ch2_word <= word;
                    K_CH3: begin ch3_dout <= word; ch3_delay <= 4; end
                    K_CACHE: begin
                        if (wj == 0) ch1_word <= word;
                        if ({1'b0, rd_first[4]} + wj <= 3) begin
                            cache_wr <= 4'b0001 << (rd_first[4] + wj);
                            cache_data <= word;
                            cache_addr <= rd_line[4];
                        end
                    end
                    default: ;
                endcase
            end

            // --------------------------------------------------- accesses
            if (init) begin
                ready <= 0; power_wait <= 0; power_done <= 0; init_step <= 0;
                op <= OP_IDLE; rd_v <= 0; q_v <= 0; ch3_delay <= 0;
            end else if (!ready) begin
                if (!power_done) begin
                    power_wait <= power_wait + 1'b1;
                    if (power_wait == POWERUP_CYCLES[14:0] - 1'b1) power_done <= 1;
                end else begin
                    init_step <= init_step + 1'b1;
                    case (init_step)
                        0:  begin cmd <= C_PRE; a <= 13'h0400; end            // all banks
                        3:  cmd <= C_REF;
                        13: cmd <= C_REF;
                        23: begin cmd <= C_MRS; a <= CAS_LATENCY == 3 ? 13'h0231 : 13'h0221; ba <= 2'b00; end // BL2, CL2/3, single writes
                        27: begin ready <= 1; ref_timer <= REFRESH_CYCLES[10:0]; ref_owed <= 0; end
                        default: ;
                    endcase
                end
            end else if (busy) begin
                step <= step + 1'b1;
                if (step == last) op <= OP_IDLE;
                case (op)
                    OP_READ1: if (step == 2) begin
                        cmd <= C_READ; a <= column(addr, 9'd0, 1'b1); ba <= bank;
                        if (CAS_LATENCY == 3) begin q_v <= 1; q_kind <= kind; q_index <= 0; end
                        else begin rd_v[0] <= 1; rd_kind[0] <= kind; rd_index[0] <= 0; end
                    end
                    OP_READ4: if (step == 2 || step == 4 || step == 6 || step == 8) begin
                        cmd <= C_READ; a <= column(addr, {step[3:1] - 3'd1, 1'b0}, step == 8); ba <= bank;
                        if (CAS_LATENCY == 3) begin
                            q_v <= 1; q_kind <= kind; q_index <= step[3:1] - 3'd1;
                            q_first <= addr[3:2]; q_line <= addr[11:4]; q_cnt <= cnt;
                        end else begin
                            rd_v[0] <= 1; rd_kind[0] <= kind; rd_index[0] <= step[3:1] - 3'd1;
                            rd_first[0] <= addr[3:2]; rd_line[0] <= addr[11:4]; rd_cnt[0] <= cnt;
                        end
                    end
                    OP_WRITE1: if (step == 2) begin
                        cmd <= C_WRITE; a <= column(addr, 9'd0, 1'b1); ba <= bank;
                        dq_out <= wdata[15:0]; dq_oe <= 1; dqm <= 2'b00;
                        start_ch1w = 1;
                    end
                    OP_WRITE: if (step == 2) begin
                        cmd <= C_WRITE; a <= column(addr, 9'd0, 1'b0); ba <= bank;
                        dq_out <= wdata[15:0]; dq_oe <= 1; dqm <= ~wbe[1:0];
                    end else if (step == 3) begin
                        // Second halfword. A waiting FIFO word in the same row
                        // is chained instead of closing the row.
                        cmd <= C_WRITE; ba <= bank;
                        dq_out <= wdata[31:16]; dq_oe <= 1; dqm <= ~wbe[3:2];
                        if (ch3_write) start_ch3w = 1;
                        if (from_fifo && fifo_v && fifo_adr[24:10] == addr[24:10]
                            && chain != CHAIN_LIMIT[5:0] && ref_owed == 0 && !force_pend && !force_rise && !spu_first) begin
                            a <= {2'b00, 1'b0, addr[25], addr[9:2], 1'b1};
                            addr <= fifo_adr; wdata <= fifo_data; wbe <= 4'b1111;
                            take_fifo = 1; chain <= chain + 1'b1;
                            step <= 2; op <= OP_WRITE;
                        end else a <= {2'b00, 1'b1, addr[25], addr[9:2], 1'b1};
                    end
                    default: ;
                endcase
            end else begin
                // Idle: refresh, then dmafifo, ch1, ch2, ch3 (SPU_FIRST: ch3 right after refresh).
                a <= idle_addr[22:10]; ba <= idle_addr[24:23]; ncs <= idle_addr[26];
                if (ref_owed != 0 || force_pend || force_rise) begin
                    chosen = OP_REFRESH;
                    cmd <= C_REF;
                    if (force_pend || force_rise) begin
                        force_pend <= 0;
                        if (ref_owed == 0) ref_timer <= REFRESH_CYCLES[10:0];
                        else ref_owed <= ref_owed - 1'b1 + (ref_timer == 0);
                    end else ref_owed <= ref_owed - 1'b1 + (ref_timer == 0 && ref_owed != 4'd8);
                    last <= 8;
                end else if (spu_first) begin
                    pend3 <= 0;
                    addr <= ch3_a; wdata <= LATE_START ? ch3_din_q : ch3_din; wbe <= LATE_START ? ch3_be_q : ch3_be;
                    cmd <= C_ACT;
                    if (LATE_START ? ch3_rnw_q : ch3_rnw) begin chosen = OP_READ1; kind <= K_CH3; last <= 11; end
                    else begin chosen = OP_WRITE; from_fifo <= 0; ch3_write <= 1; last <= 6; end
                end else if (fifo_v) begin
                    chosen = OP_WRITE;
                    addr <= fifo_adr; wdata <= fifo_data; wbe <= 4'b1111;
                    from_fifo <= 1; ch3_write <= 0; chain <= 0; take_fifo = 1; last <= 6;
                    cmd <= C_ACT;
                end else if (req1) begin
                    took1 = 1; pend1 <= 0;
                    addr <= ch1_a; cnt <= ch1_cntDMA;
                    cmd <= C_ACT;
                    if (!ch1_rnw) begin
                        chosen = OP_WRITE1; wdata <= {16'b0, ch1_din}; last <= 5;
                    end else if (ch1_dma) begin
                        chosen = OP_READ4; kind <= K_DMA; last <= 12; start_dmarp = 1;
                    end else if (ch1_cache) begin
                        chosen = OP_READ4; kind <= K_CACHE; last <= 12;
                    end else begin
                        chosen = OP_READ1; kind <= K_CH1; last <= 11;
                    end
                end else if (req2) begin
                    took2 = 1; pend2 <= 0;
                    addr <= ch2_a; wdata <= ch2_din; wbe <= ch2_be;
                    cmd <= C_ACT;
                    if (ch2_rnw) begin chosen = OP_READ1; kind <= K_CH2; last <= 11; end
                    else begin
                        chosen = OP_WRITE; from_fifo <= 0; ch3_write <= 0; last <= 6;
                        start_ch2 = 1;                      // posted write
                    end
                end else if (req3) begin
                    pend3 <= 0;
                    addr <= ch3_a; wdata <= LATE_START ? ch3_din_q : ch3_din; wbe <= LATE_START ? ch3_be_q : ch3_be;
                    cmd <= C_ACT;
                    if (LATE_START ? ch3_rnw_q : ch3_rnw) begin chosen = OP_READ1; kind <= K_CH3; last <= 11; end
                    else begin chosen = OP_WRITE; from_fifo <= 0; ch3_write <= 1; last <= 6; end
                end
                if (chosen != OP_IDLE) begin op <= chosen; step <= 1; end
            end

            // DMA FIFO: pop the word taken (CL2), or keep the head register
            // filled (LATE_START); dmafifo_read pops on the next edge, so no
            // word is loaded while a pop is still pending.
            if (!LATE_START) begin
                if (take_fifo) dmafifo_read <= 1;
            end else begin
                if (take_fifo) head_v <= 0;
                if ((!head_v || take_fifo) && !dmafifo_empty && !dmafifo_read) begin
                    head_v <= 1; head_adr <= dmafifo_adr; head_data <= dmafifo_data; dmafifo_read <= 1;
                end
            end

            // Requests not started this edge stay pending.
            if (after_base && ch1_req && !took1) begin pend1 <= 1; ch1_adr_q <= ch1_addr; end
            if (after_base && ch2_req && !took2) begin pend2 <= 1; ch2_adr_q <= ch2_addr; end

            // ---------------------------------------- clk1x-facing outputs
            // Events wait for the next before_base edge; events of this very
            // edge are forwarded so nothing is a clk1x cycle late.
            if (before_base) begin
                x_ch1_ready <= p_ch1 || ev_ch1_ready || start_ch1w;
                if (p_ch1 || ev_ch1_ready) x_ch1_dout32 <= ev_ch1_word ? word : ch1_word;
                x_ch2_ready <= p_ch2 || ev_ch2 || start_ch2;
                if (ev_ch2) x_ch2_dout <= word;
                else if (p_ch2) x_ch2_dout <= ch2_word;
                x_ch3_read <= p_ch3 || ev_ch3;
                x_ch3_write <= p_ch3w || start_ch3w;
                p_ch3w <= 0;
                x_dma_reqprocessed <= p_dmarp || start_dmarp;
                p_ch1 <= 0; p_ch2 <= 0; p_ch3 <= 0; p_dmarp <= 0;
                // DMA stream: one word per clk1x cycle, oldest first
                // synthesis translate_off
                if (dma_n >= 7) $error("psx_sdram_ctrl: DMA ring nearly full (%0d)", dma_n);
                // synthesis translate_on
                if (dma_n != 0) begin
                    x_dma_wr <= 1; x_dma_data <= dma_q[dma_r]; dma_r <= dma_r + 1'b1;
                    dma_n <= dma_n - 1'b1 + ev_dma_word;
                    if (ev_dma_word) begin dma_q[dma_w] <= word; dma_w <= dma_w + 1'b1; end
                end else if (ev_dma_word) begin
                    x_dma_wr <= 1; x_dma_data <= word;
                end else x_dma_wr <= 0;
            end else begin
                if (ev_ch1_ready || start_ch1w) p_ch1 <= 1;
                if (ev_ch2 || start_ch2) p_ch2 <= 1;
                if (ev_ch3) p_ch3 <= 1;
                if (start_ch3w) p_ch3w <= 1;
                if (start_dmarp) p_dmarp <= 1;
                if (ev_dma_word) begin
                    dma_q[dma_w] <= word; dma_w <= dma_w + 1'b1; dma_n <= dma_n + 1'b1;
                end
            end

            ram_idle <= ready && !busy && !pend1 && !pend2 && !pend3 && !pend3_new && !ch3_req
                        && ref_owed == 0 && ref_timer > 16 && dmafifo_empty && !head_v;
        end
    end
endmodule
`default_nettype wire
