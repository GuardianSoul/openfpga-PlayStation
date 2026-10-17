#!/usr/bin/env python3
"""Copy a fitted connected Pocket run and apply the early-hardware video fix.

The connected budget tops pass the PSX level HS/VS straight to APF. The APF
scaler wants one-clock HS/VS pulses and zero RGB outside DE (RGB on an HS
with DE low selects scaler slots). This copies the project, repoints its
absolute paths at the copy, registers the video outputs into that form and
leaves the original run untouched. Compile the copy with map/fit/sta/asm,
then package it with package_pocket_early.py.
"""
import argparse
import re
from pathlib import Path
import shutil

OLD = '''    assign video_skip=!video_ce;
    assign video_rgb={video_r,video_g,video_b};
    assign video_de=!hblank && !psx_vblank, video_hs=hsync, video_vs=vsync;
'''
NEW = '''    // APF scaler protocol: one-clock HS/VS pulses, RGB zero outside DE
    // (nonzero RGB on an HS with DE low selects scaler slots).
    reg apf_hs_d=0, apf_vs_d=0, apf_hs=0, apf_vs=0, apf_de=0, apf_skip=0;
    reg [23:0] apf_rgb=0;
    always @(posedge video_rgb_clock) begin
        apf_hs_d<=hsync; apf_vs_d<=vsync;
        apf_hs<=hsync && !apf_hs_d; apf_vs<=vsync && !apf_vs_d;
        apf_de<=!hblank && !psx_vblank;
        apf_skip<=!hblank && !psx_vblank && !video_ce;
        apf_rgb<=(!hblank && !psx_vblank) ? {video_r,video_g,video_b} : 24'h0;
    end
    assign video_skip=apf_skip, video_rgb=apf_rgb;
    assign video_de=apf_de, video_hs=apf_hs, video_vs=apf_vs;
'''


SPU_CH3_DIRECT = """    wire [26:0] ch3_addr=cpu_reset ? l_addr : 27'h1000000+{8'b0,spuram_adr};
    wire [31:0] ch3_din=cpu_reset ? l_data : spuram_dataWrite;
    wire [3:0] ch3_be=cpu_reset ? l_be : spuram_be;
    wire ch3_req=cpu_reset ? l_req : spuram_ena;
    wire ch3_rnw=cpu_reset ? 1'b0 : spuram_rnw;
    assign spuram_done=ch3_ready && !cpu_reset;
    assign spuram_dataRead=ch3_dout;
"""

SPU_CH3_CACHE = """    // --spu-cache: SPU RAM reads through psx_spu_ch3_cache.
    wire c_req, c_rnw, c_done; wire [26:0] c_addr; wire [31:0] c_din, c_q; wire [3:0] c_be;
    psx_spu_ch3_cache spu_cache (.clk1x(clk1x), .reset(init), .discard(cpu_reset),
        .spu_req(spuram_ena && !cpu_reset), .spu_rnw(spuram_rnw), .spu_addr(spuram_adr),
        .spu_data(spuram_dataWrite), .spu_be(spuram_be), .spu_done(c_done), .spu_q(c_q),
        .ch3_req(c_req), .ch3_rnw(c_rnw), .ch3_addr(c_addr), .ch3_din(c_din), .ch3_be(c_be),
        .ch3_ready(ch3_ready && !cpu_reset), .ch3_dout(ch3_dout));
    wire [26:0] ch3_addr=cpu_reset ? l_addr : c_addr;
    wire [31:0] ch3_din=cpu_reset ? l_data : c_din;
    wire [3:0] ch3_be=cpu_reset ? l_be : c_be;
    wire ch3_req=cpu_reset ? l_req : c_req;
    wire ch3_rnw=cpu_reset ? 1'b0 : c_rnw;
    assign spuram_done=c_done && !cpu_reset;
    assign spuram_dataRead=c_q;
"""

OVERLAY = """    // Bring-up readout (psx_debug_overlay): rows top to bottom are
    // 1 status bits, 2 main-RAM requests, 3 main-RAM completions, 4 last
    // main-RAM address, 5 VRAM commands accepted, 6 VRAM read beats,
    // 7 PSX vblanks, 8 PSRAM asynchronous accesses (0 without --vram-async).
    reg [31:0] dbg_ram_req=0, dbg_ram_done=0, dbg_vblanks=0, dbg_vram_cmd=0, dbg_vram_beat=0, dbg_async=0;
    reg [24:0] dbg_ram_addr=0; reg dbg_ena_d=0, dbg_vblank_d=0;
    always @(posedge clk1x) begin
        dbg_ena_d<=ram_ena; dbg_vblank_d<=psx_vblank;
        if (ram_ena && !dbg_ena_d) begin dbg_ram_req<=dbg_ram_req+1'b1; dbg_ram_addr<=ram_Adr; end
        if (ram_done) dbg_ram_done<=dbg_ram_done+1'b1;
        if (psx_vblank && !dbg_vblank_d) dbg_vblanks<=dbg_vblanks+1'b1;
    end
    always @(posedge clk2x) begin
        if ((ddr3_RD || ddr3_WE) && !ddr3_BUSY) dbg_vram_cmd<=dbg_vram_cmd+1'b1;
        if (ddr3_DOUT_READY) dbg_vram_beat<=dbg_vram_beat+1'b1;
    end
    always @(posedge ps_clk) if (DBG_ASYNC_ACK) dbg_async<=dbg_async+1'b1;
    wire [31:0] dbg_status={passed, failed, checking, cpu_boot_reset, cpu_locked, mem_ready74[1], video_interlace, 25'b0};
    wire [23:0] dbg_rgb;
    psx_debug_overlay #(.CELL_W(10), .CELL_H(9)) debug_overlay (.clk(video_rgb_clock), .ce(video_ce), .de(!hblank && !psx_vblank), .vs(vsync),
        .rgb_in({video_r,video_g,video_b}),
        .words({dbg_async, dbg_vblanks, dbg_vram_beat, dbg_vram_cmd, {7'b0,dbg_ram_addr}, dbg_ram_done, dbg_ram_req, dbg_status}),
        .rgb_out(dbg_rgb));
"""

TRACE = """    // Boot trace (psx_boot_trace), saved by the Pocket from bridge 0x40000000.
    wire [31:0] trace_data;
    // A completion belongs to a read when the last request was a read.
    reg trace_read_pending=0;
    always @(posedge clk1x)
        if (ram_ena && !dbg_ena_d) trace_read_pending<=ram_rnw; else if (ram_done) trace_read_pending<=0;
    psx_boot_trace boot_trace (.clk(clk1x), .reset(1'b0), .request(ram_ena && !dbg_ena_d), .write(!ram_rnw),
        .address(ram_Adr), .frame(dbg_vblanks[5:0]),
        .write_data(ram_dataWrite), .read_data_in(ram_dataRead32), .read_done(ram_done && trace_read_pending),
        .header({dbg_async, dbg_vblanks, dbg_vram_beat, dbg_vram_cmd, {7'b0,dbg_ram_addr}, dbg_ram_done, dbg_ram_req, dbg_status}),
        .cache_clk(clk1x), .cache_wr(4'b0), .cache_data(32'b0),
        .read_clk(clk_74a), .read_index(bridge_addr[14:2]), .read_data(trace_data));
"""

VRAM_OLD = """        .z_valid(z_valid),.z_ready(z_ready),.z_we(z_we),.z_last(z_last),.z_addr(z_addr),
        .z_wdata(z_data),.z_wmask(z_mask),.z_rvalid(z_rvalid),.z_rdata(z_read),.z_idle(z_idle),
        .x_valid(1'b0),.x_we(1'b0),.x_addr(22'b0),.x_wdata(32'b0),.x_be(4'b0),.x_ack(),.x_rdata(),
        .cfg(4'h5),.cfg_wr(1'b0),.ready(),"""
VRAM_NEW = """        .z_valid(1'b0),.z_ready(),.z_we(1'b0),.z_last(1'b0),.z_addr(22'b0),
        .z_wdata(32'b0),.z_wmask(4'b0),.z_rvalid(),.z_rdata(),.z_idle(),
        .x_valid(vx_valid),.x_we(vx_we),.x_addr(vx_addr),.x_wdata(vx_wdata),.x_be(vx_be),.x_ack(vx_ack),.x_rdata(vx_rdata),
        .cfg(4'h5),.cfg_wr(1'b0),.ready(vram_ready),"""
VRAM_SHIM = """    // VRAM through MIRLO64's asynchronous X port (die 1): synchronous PSRAM
    // bursts fail on the test Pocket, asynchronous access passes.
    wire vram_ready, vx_valid, vx_we, vx_ack; wire [21:0] vx_addr; wire [31:0] vx_wdata, vx_rdata; wire [3:0] vx_be;
    psx_psram_z2x vram_async (.clk(ps_clk),.reset(!cpu_locked),.ready(vram_ready),
        .z_valid(z_valid),.z_we(z_we),.z_last(z_last),.z_ready(z_ready),.z_addr(z_addr),.z_data(z_data),.z_mask(z_mask),
        .z_rvalid(z_rvalid),.z_read(z_read),.z_idle(z_idle),
        .x_valid(vx_valid),.x_we(vx_we),.x_addr(vx_addr),.x_wdata(vx_wdata),.x_be(vx_be),.x_ack(vx_ack),.x_rdata(vx_rdata));
"""


# PSRAM clock (MHz) and chip-clock phase (ps): 0.6 of the period, as the
# PocketQuake recipe (105 MHz, 5714 ps) that passed on the test Pocket,
# rounded to the 200 ps steps of the 625 MHz VCO.
# Chip clock phase 6600 ps at 104 MHz: chip 1 DQ0 read 0 as 1 now and then
# at 5000 (62 in 16M operations) and 5800 (~6), never at 6600 (probes e50/e66).
PSRAM_CLOCKS = {'104': ('104.166666', '6600'), '78': ('78.125000', '7600')}


def stub_instance(vhdl, label, entity_file, note):
    """Replace a VHDL instance with idle (zero) drives of its outputs."""
    match = re.search(r'   ' + label + r' : entity work\.\w+\n.*?\n   \);\n', vhdl, re.S)
    if not match: raise ValueError('instance not found: ' + label)
    ports = {}
    for name, kind in re.findall(r'^\s*(\w+)\s*:\s*out\s+(std_logic_vector|std_logic|unsigned|signed)', Path(entity_file).read_text(), re.M | re.I):
        ports[name.lower()] = kind.lower()
    lines = ['   -- PSXEarly test build: ' + note]
    for port, signal in re.findall(r'(\w+)\s*=>\s*([^,\n]+?)\s*,?\s*(?:--.*)?\n', match.group(0)):
        kind = ports.get(port.lower())
        if kind is None: continue
        signal = signal.strip()
        if signal.lower() == 'open': continue
        lines.append(f"   {signal} <= {'\'0\'' if kind == 'std_logic' else '(others => \'0\')'};")
    return vhdl[:match.start()] + '\n'.join(lines) + '\n' + vhdl[match.end():]


SOTN_GLUE = '''    // Main RAM, BIOS and SPU RAM on SDRAM at clk3x; VRAM on both PSRAMs
    // (clk2x).
    psx_native_memory native_memory (.clk1x(clk1x),.clk3x(clk3x),.init(!cpu_locked),.cpu_reset(reset),
        .ram_refresh(ram_refresh),.ram_ena(ram_ena),.ram_rnw(ram_rnw),.ram_dma(ram_dma),.ram_cache(ram_cache),
        .ram_adr(ram_Adr),.ram_cntDMA(ram_cntDMA),.ram_dataWrite(ram_dataWrite),.ram_be(ram_be),
        .ram_done(ram_done),.ram_dataRead32(ram_dataRead32),.dma_wr(dma_wr),.dma_data(dma_data),
        .dma_reqprocessed(dma_reqprocessed),.cache_wr(cache_wr),.cache_data(cache_data),.cache_addr(cache_addr),
        .ram_dmafifo_adr(ram_dmafifo_adr),.ram_dmafifo_data(ram_dmafifo_data),
        .ram_dmafifo_empty(ram_dmafifo_empty),.ram_dmafifo_read(ram_dmafifo_read),
        .spuram_ena(spuram_ena),.spuram_rnw(spuram_rnw),.spuram_adr(spuram_Adr),.spuram_dataWrite(spuram_dataWrite),
        .spuram_be(spuram_be),.spuram_done(spuram_done),.spuram_dataRead(spuram_dataRead),
        .load_valid(c_valid),.load_addr(c_addr),.load_halfwords(c_count),.load_data(c_data),.load_be(c_be),
        .load_ready(c_ready),.load_rsp(c_rsp),.load_rsp_ready(c_rsp_ready),
        .dram_dq(dram_dq),.dram_a(dram_a),.dram_dqm(dram_dqm),.dram_ba(dram_ba),.dram_we_n(dram_we_n),
        .dram_ras_n(dram_ras_n),.dram_cas_n(dram_cas_n),.dram_cke(dram_cke),.dram_clk(dram_clk));
    assign c_error=1'b0, c_read=128'b0;
    assign main_fault=1'b0, main_source_idle=1'b1, main_service_idle=1'b1, spu_fault=1'b0, spu_source_idle=1'b1;
    assign shared_fault=1'b0, shared_idle=1'b1, memory_client_reset=1'b0;
    // sdram.sv queues requests during its startup; the loader still waits ~2.6 ms.
    reg [18:0] native_init_count=0;
    always @(posedge clk3x) if (!cpu_locked) native_init_count<=0; else if (!native_init_count[18]) native_init_count<=native_init_count+1'b1;
    assign shared_initialized=native_init_count[18];
    wire vram_fault=1'b0;
    wire [15:0] cram0_dq_out, cram1_dq_out; wire cram0_dq_oe, cram1_dq_oe;
    vram_psram vram (.clk(clk2x),.reset(!cpu_locked),
        .ddr_busy(ddr3_BUSY),.ddr_burstcnt(ddr3_BURSTCNT),.ddr_addr(ddr3_ADDR[27:3]),.ddr_dout(ddr3_DOUT),
        .ddr_dout_ready(ddr3_DOUT_READY),.ddr_rd(ddr3_RD),.ddr_din(ddr3_DIN),.ddr_be(ddr3_BE),.ddr_we(ddr3_WE),
        .aux_req(1'b0),.aux_we(1'b0),.aux_addr(21'b0),.aux_din(32'b0),.aux_dout(),.aux_done(),.card_write(),
        .cram0_a(cram0_a),.cram0_dq_out(cram0_dq_out),.cram0_dq_oe(cram0_dq_oe),.cram0_dq_in(cram0_dq),.cram0_clk(cram0_clk),
        .cram0_adv_n(cram0_adv_n),.cram0_cre(cram0_cre),.cram0_ce0_n(cram0_ce0_n),.cram0_ce1_n(cram0_ce1_n),
        .cram0_oe_n(cram0_oe_n),.cram0_we_n(cram0_we_n),.cram0_ub_n(cram0_ub_n),.cram0_lb_n(cram0_lb_n),
        .cram1_a(cram1_a),.cram1_dq_out(cram1_dq_out),.cram1_dq_oe(cram1_dq_oe),.cram1_dq_in(cram1_dq),.cram1_clk(cram1_clk),
        .cram1_adv_n(cram1_adv_n),.cram1_cre(cram1_cre),.cram1_ce0_n(cram1_ce0_n),.cram1_ce1_n(cram1_ce1_n),
        .cram1_oe_n(cram1_oe_n),.cram1_we_n(cram1_we_n),.cram1_ub_n(cram1_ub_n),.cram1_lb_n(cram1_lb_n));
    assign cram0_dq=cram0_dq_oe ? cram0_dq_out : 16'bz;
    assign cram1_dq=cram1_dq_oe ? cram1_dq_out : 16'bz;
'''


VRAM_HEAD = '    vram_psram vram (.clk(clk2x),.reset(!cpu_locked),\n'
VRAM_HEAD_CLEAN = '    psx_vram_psram vram (.clk(clk2x),.reset(!cpu_locked),.park(vram_park),\n'


MEMCARD_SLOT = '''    // Memory card 1 save slot (--memcard-slot): APF data slot at 0x20000000.
    wire mc_busy, mc_aux_req, mc_aux_we, mc_aux_done, mc_card_write;
    wire [20:0] mc_aux_addr; wire [31:0] mc_aux_din, mc_aux_dout, memcard_rd_data;
    (* async_reg="true" *) reg [1:0] mc_reset2x=2'b11;
    always @(posedge clk2x) mc_reset2x<={mc_reset2x[0],!cpu_locked};
    pocket_memcard memcard_slot (.clk_74a(clk_74a),.bridge_addr(bridge_addr),.bridge_wr(bridge_wr),
        .bridge_wr_data(bridge_wr_data),.bridge_rd(bridge_rd),.bridge_rd_data(memcard_rd_data),.overflow(),
        .clk2x(clk2x),.reset_2x(mc_reset2x[1]),.busy(mc_busy),
        .aux_req(mc_aux_req),.aux_we(mc_aux_we),.aux_addr(mc_aux_addr),.aux_din(mc_aux_din),.aux_dout(mc_aux_dout),
        .aux_done(mc_aux_done),.card_write(mc_card_write));
'''


SWAP_MENU = '''    reg swap_setting=1'b0;
    always @(posedge clk_74a) if (bridge_wr && bridge_addr==32'h50000008) swap_setting<=|bridge_wr_data;
    (* async_reg="true" *) reg [1:0] swap_sync=2'b00;
    always @(posedge video_rgb_clock) swap_sync<={swap_sync[0],swap_setting};
    wire apf_swap=swap_sync[1];
'''


# --shoulder-setting rows (package_pocket_early.py imports them): menu name,
# bridge address and the buttons the row can pick (the first is the default).
SHOULDER_ROWS = (('L Button', 0x5000000C, ('L1', 'L2', 'L3')), ('R Button', 0x50000010, ('R1', 'R2', 'R3')))
# The Select combo rows: what Select+L/R and Select+Y/A send, each as a pair
# (name, address, options, default, pad_combos index of the left key). None
# still holds them back from the game; the defaults are the plain --pad-combos
# layout (L2/R2 and L3/R3).
SHOULDER_COMBOS = (('SELECT + L/R', 0x50000014, ('None', 'L1/R1', 'L2/R2', 'L3/R3'), 2, 0),
                   ('SELECT + Y/A', 0x50000018, ('None', 'L1/R1', 'L2/R2', 'L3/R3'), 3, 2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--vram-async', action='store_true', help='Serve VRAM through the PSRAM asynchronous port')
    parser.add_argument('--debug-overlay', action='store_true', help='Draw boot status counters over the picture')
    parser.add_argument('--sdram-clock-ps', type=int, help='Drive SDRAM_CLK from a 101.6064 MHz PLL output at this phase (ps), not inverted clk3x')
    parser.add_argument('--no-overlay-picture', action='store_true', help='Keep the overlay counters for the trace but do not draw them (saves LABs)')
    parser.add_argument('--stub-mdec', action='store_true', help='Test builds only: replace the MDEC instance with idle outputs (saves area)')
    parser.add_argument('--blank-reset', action='store_true', help='Use psx_apf_video (blank while the CPU is held in reset) without the 480i filter')
    parser.add_argument('--steady-interlace', action='store_true', help='Drop the second field of 480i modes (rtl/pocket/psx_apf_video.sv) so the picture does not bob')
    parser.add_argument('--refresh-audio-bridge', action='store_true', help="Use the repository's current rtl/audio/psx_audio_bridge.sv in the copy")
    parser.add_argument('--spu-cached', action='store_true', help="Test builds: SPUSDRAM=0, MiSTer's cached SPU path over the DDR port (Codex cf22730)")
    parser.add_argument('--stub-memcard-copy', action='store_true', help="Drop MiSTer's memory-card copy engines only (APF slots persist cards); keep the card protocol devices")
    parser.add_argument('--stub-memcards', action='store_true', help='Test builds only: no memory-card devices (saves area)')
    parser.add_argument('--no-memcard2', action='store_true', help='Leave memory card slot 2 empty (2026-10-14: frees ~12 LABs; FFVII saves to slot 1)')
    parser.add_argument('--steady-field', action='store_true', help='480i: scan out the same field lines every frame (private gpu_videoout_async copy) and send every field to APF; no bob, no skipped fields')
    parser.add_argument('--scaler-slots', action='store_true', help='Select the APF scaler slot by line width (psx_apf_video SLOT_SELECT; package_pocket_early.py writes the five slots)')
    parser.add_argument('--sio-fixed', action='store_true', help='SIO registers fixed at reset values (rtl/pocket/sio_fixed.vhd; FFVII never uses SIO)')
    parser.add_argument('--no-field-hold', action='store_true', help="Generated tops: psx_apf_video STEADY_INTERLACE(0) (the skip-hold filter flashes old frames on the Pocket's double-buffered scaler)")
    parser.add_argument('--logo-skip-setting', action='store_true', help='Pocket menu checkbox "Skip BIOS logo" (bridge 0x50000004): psx_bios_patch makes the shell return at once (package --logo-skip-setting)')
    parser.add_argument('--shoulder-setting', action='store_true', help='Pocket menu lists: whether the Pocket\'s L and R press L1/L2/L3 and R1/R2/R3 (bridge 0x5000000C/0x50000010; package --shoulder-setting)')
    parser.add_argument('--weave-setting', action='store_true', help='With --interlace-feature: Pocket menu checkbox "480i weave" (bridge 0x50000000; package --weave-setting)')
    parser.add_argument('--interlace-feature', action='store_true', help='Send APF frame feature bits (interlaced, field) on VS in 480i (needs --scaler-slots)')
    parser.add_argument('--pad-lines', action='store_true', help='With --scaler-slots: continue each line with black pixels up to its scaler slot width (psx_apf_video PAD_LINES); a short line left the rest of the slot stale (stray row, 2026-10-16)')
    parser.add_argument('--vs-at-vblank', action='store_true', help='With --scaler-slots: start the APF frame (VS) when vblank rises instead of on the PSX vsync, which in some modes came after the first active line (stray row below the picture; psx_apf_video VS_AT_VBLANK)')
    parser.add_argument('--pal288', action='store_true', help='With --slots480: scaler slot 7 becomes 320x288 for PAL frames instead of 368x480 (psx_apf_video PAL288; package --pal288)')
    parser.add_argument('--center-lines', action='store_true', help='With --pad-lines: split the black padding before and after each line so a picture narrower than its scaler slot is centred (psx_apf_video CENTER_LINES)')
    parser.add_argument('--slot6-368', action='store_true', help='With --pal288: scaler slot 6 is 368x480 instead of 512x480, so 368-wide 480i (Tekken 3) fills its slot; 512-wide 480i goes padded into 640x480 (psx_apf_video SLOT6_368; package --slot6-368)')
    parser.add_argument('--crop-lines', action='store_true', help='With --scaler-slots (not --wide-lines): a field taller than its slot is cut evenly at top and bottom instead of only at the bottom (psx_apf_video CROP_LINES; PAL pictures sat low)')
    parser.add_argument('--blank-top', nargs='?', const=1, type=int, choices=(1, 2, 3), help='With --scaler-slots (not --wide-lines): the first N (default 1) active lines of each field go out black; part of that top row sometimes held wrong pixels (white specks, flickering blue run; psx_apf_video BLANK_TOP)')
    parser.add_argument('--vs-on-hs', action='store_true', help='With --scaler-slots: VS goes out with the next HS start, so both 480i fields reach the APF scaler with the same VS timing (psx_apf_video / psx_apf_video_wide VS_ON_HS; the GPU puts one field\'s vsync mid-line)')
    parser.add_argument('--lead-line', action='store_true', help='With --scaler-slots: one black line after each VS (psx_apf_video LEAD_LINE, Video test D fixed; psx_apf_video_wide LEAD_LINE with --wide-lines): the APF scaler drops the first line of a frame, which cost the top line and left a stale bottom row (stray row, 2026-10-16)')
    parser.add_argument('--video-test-setting', action='store_true', help='Diagnostic builds only, with --scaler-slots: Settings list "Video test" (bridge 0x5000004C) switching the APF frame start (VS at vblank or PSX vsync, with or without a black lead line; psx_apf_video VIDEO_TEST; package --video-test-setting)')
    parser.add_argument('--wide-lines', action='store_true', help='With --scaler-slots: stretch every line to 640 pixels (rtl/pocket/psx_apf_video_wide.sv) so scaler slots only differ by height: 640x240, 640x480, 640x288 (package --wide-lines)')
    parser.add_argument('--hybrid-slots', action='store_true', help='With --wide-lines: keep native 320/256 (and 512 at 240 lines) slots and stretch only other widths (psx_apf_video_wide HYBRID; package --hybrid-slots)')
    parser.add_argument('--video-probe', action='store_true', help='Diagnostic builds only, with --scaler-slots: psx_apf_video VIDEO_PROBE colours what the core writes (line count and width as bit blocks, last line red, padding magenta)')
    parser.add_argument('--field-invert', action='store_true', help='Flip the APF odd/even field bit (psx_apf_video FIELD_INVERT)')
    parser.add_argument('--slots480', action='store_true', help='480i lines use 480-tall scaler slots 5-7 (package with --slots480)')
    parser.add_argument('--perf-log', action='store_true', help='Per-frame memory-wait counters saved to SD at bridge 0x40000000 (rtl/pocket/psx_perf_log.sv; package with --boot-log, needs --cd-stream)')
    parser.add_argument('--sotn-memory', action='store_true', help='pocket_sotn layout: main RAM, BIOS and SPU RAM on MiSTer sdram.sv at clk3x with the Pocket fixes (references/pocket_sotn/rtl/sdram.sv, rtl/pocket/psx_native_memory.sv); VRAM on both PSRAMs (pocket_sotn vram_psram.vhd)')
    parser.add_argument('--spu-free-slot', action='store_true', help="No fixed ~24-clk1x SDRAM voice slot in spu.vhd (RAM_WAIT as with useSDRAM='0'): each voice waits only for its own SPU RAM reads, so with --spu-cache hits shorten it")
    parser.add_argument('--spu-cache-slim', action='store_true', help='With --spu-cache: the slim word-granular cache (rtl/pocket/psx_spu_ch3_cache_slim.sv, a fraction of the area; make test-spu-ch3-cache)')
    parser.add_argument('--spu-cache', action='store_true', help='With --sdram-ctrl clean: SPU RAM reads through a 64-line clk1x read cache (rtl/pocket/psx_spu_ch3_cache.sv), so most SPU accesses skip SDRAM and its latency spikes (FFVII FMV crackle: AudioProbe SPU deadline misses)')
    parser.add_argument('--sdram-spu-first', action='store_true', help='With --sdram-ctrl clean: serve SPU RAM (ch3) right after refresh, ahead of DMA and CPU, so the SPU keeps its sample deadline (audio underflow on WipEout CD music)')
    parser.add_argument('--sdram-cas', type=int, choices=(2, 3), default=2, help='With --sdram-ctrl clean: CAS latency (the Pocket SDRAM datasheet needs CL3 at 101.6 MHz; same capture window one clk later)')
    parser.add_argument('--sdram-ctrl', choices=('sotn', 'clean'), default='sotn', help="With --sotn-memory: pocket_sotn's sdram.sv (GPL-3.0-or-later) or the MIT clean-room rtl/memory/psx_sdram_ctrl.sv")
    parser.add_argument('--psram-main', action='store_true', help='Main RAM, BIOS and SPU RAM on both PSRAM chips (rtl/pocket/psx_psram_clients.sv, PocketQuake recipe at 104.17 MHz); VRAM alone on SDRAM')
    parser.add_argument('--psram-clients', default='012', help='With --psram-main: which clients use PSRAM (0 main RAM+BIOS, 1 SPU RAM, 2 BIOS loader); the rest stay on SDRAM (bisection)')
    parser.add_argument('--psram-mhz', choices=('104', '78'), default='104', help='PSRAM controller/chip clock from the 625 MHz memory VCO: 104.17 (/6) or 78.125 (/8); the chip clock keeps the probe-proven 0.6-period phase')
    parser.add_argument('--psram-free-clock', action='store_true', help='With --psram-main: free-running chip clock (fails on hardware once writes mix with reads; default gates it to burst reads)')
    parser.add_argument('--psram-write-hold', action='store_true', help='With --psram-main: keep write data driven one cycle past WE# rising (psram.sv releases it on the same edge)')
    parser.add_argument('--psram-write-extra', type=int, default=0, help='With --psram-main: clocks added to each PSRAM write (0-10)')
    parser.add_argument('--psram-write-gap', type=int, default=0, help='With --psram-main: idle clocks after each write packet')
    parser.add_argument('--psram-phase', default=None, help='With --psram-main: chip clock phase in ps (200 ps steps)')
    parser.add_argument('--perf-stages', action='store_true', help='With --perf-log --psram-sync: per-stage clk3x counters (adapter busy, PSRAM read/write) in 8-word records (~100 ALMs)')
    parser.add_argument('--field-swap-setting', action='store_true', help='Menu checkbox that flips the 480i APF field bit at run time (bridge 0x50000008)')
    parser.add_argument('--async-hold-cut', action='store_true', help='Experimental clock-family hold cuts to avoid asynchronous hold padding; WARNING: Quartus Lite also suppresses Gray-bus skew checks on these paths')
    parser.add_argument('--vram-cdc-constraints', action='store_true', help='Apply the guarded VRAM Gray flight/skew and first-stage status constraints before routing; incompatible with --async-hold-cut (Lite suppresses skew checks)')
    parser.add_argument('--psram-sync', action='store_true', help='With --psram-main: PSRAM controller on clk3x (101.6 MHz) and chip clock from the CPU PLL; main RAM through psx_mister_main_sync (no clock crossing)')
    parser.add_argument('--psram-single', action='store_true', help='With --psram-main: chip 0 alone (bring-up fallback; cram1 has not been proven in burst mode)')
    parser.add_argument('--cd-sector-pack', action='store_true', help='Reset-only builds: cd_top sector buffers at 640 words each, 20 M10K instead of 32 (scripts/prepare_cd_sector_pack.py)')
    parser.add_argument('--memctrl-lean', action='store_true', help='Reset-only builds: memctrl with its readback-only bits in falling-edge block RAM (rtl/pocket/memctrl_lean.vhd; make test-memctrl-lean)')
    parser.add_argument('--cd-buffers', type=int, choices=(2, 4, 8), default=2, help='psx_cd_stream sector buffers (NBUF): NBUF-1 sectors prefetched ahead of the drive, to ride out slow SD reads')
    parser.add_argument('--cd-xa-ring-norw', action='store_true', help='cd_xa ring buffers without read-during-write pass-through logic: written only in RESAMPLE_CLEAR/WRITERING, read only in RESAMPLE_READRING')
    parser.add_argument('--spu-cd-queue', action='store_true', help='SPU mixes and captures the CD/XA sample of the tick it is processing, even when it runs late (scripts/prepare_spu_cd_queue.py; identical when on time; FMV crackle)')
    parser.add_argument('--spu-adsr-lean', action='store_true', help='SPU ADSR tables computed from a registered setting instead of 275 flip-flops (scripts/prepare_spu_adsr_lean.py; cycle-identical)')
    parser.add_argument('--spu-engine-m10k', action='store_true', help='SPU reverb engine coefficient copy in falling-edge clk1x block RAM instead of an MLAB (scripts/prepare_spu_engine_m10k.py; needs spu-reverb-table and spu-guest-m10k)')
    parser.add_argument('--gte-shadow-m10k', action='store_true', help='GTE CFC2/MFC2 readback shadows in falling-edge clk2x block RAM instead of MLABs (scripts/prepare_gte_shadow_m10k.py; needs gte-control-ram)')
    parser.add_argument('--gpu-read-late', action='store_true', help='GPU VRAM read setup (remain/next/wrap) one clk2x after the request (scripts/prepare_gpu_read_late.py; request timing unchanged; fixes the worst clk2x path)')
    parser.add_argument('--gpu-dy-ram', action='store_true', help='With --gpu-area-reg: gpu_poly y gradients in a falling-edge block RAM instead of 160 flip-flops (scripts/prepare_gpu_dy_ram.py)')
    parser.add_argument('--gpu-area-reg', action='store_true', help="gpu_poly's drawing-area test of xPos from registers (scripts/prepare_gpu_area_reg.py; cycle-identical, fixes the worst gpu_poly setup path)")
    parser.add_argument('--spu-static-readback', action='store_true', help='SPU static control registers read back from block RAM (scripts/prepare_spu_static_readback.py; needs spu-guest-m10k)')
    parser.add_argument('--main-lean', action='store_true', help='SDRAM-layout main adapter reads its read_data directly instead of two 128-bit copies (scripts/prepare_main_lean.py; b01d26d for the async adapter)')
    parser.add_argument('--memorymux-fifo-ram', choices=('BYPASS', 'CLK2X'), help='Clean-room memorymux write FIFO in M10K, cycle-identical to MiSTer, no CPU change (scripts/prepare_memorymux_fifo_ram.py); replaces --memorymux-m10k')
    parser.add_argument('--vram-ctrl', choices=('sotn', 'clean'), default='clean', help="With --sotn-memory: the MIT clean-room rtl/memory/psx_vram_psram.sv (default; parks the PSRAMs in their power-on mode while the core is held in reset) or pocket_sotn's vram_psram.vhd (sotn, 0.5.2 and older)")
    parser.add_argument('--memcard-ctrl', choices=('sotn', 'clean'), help="With --memcard-slot: the MIT clean-room rtl/pocket/psx_memcard_slot.sv (default with --memcard-format; always formats a card without the MC magic) or pocket_sotn's card slot (default without --memcard-format) or its --memcard-format copy (sotn, 0.5.2 and older)")
    parser.add_argument('--memorymux-m10k', action='store_true', help='Opt-in pinned SOTN falling-edge M10K write FIFO plus short CPU write-request path (scripts/prepare_memorymux_m10k.py)')
    parser.add_argument('--host-config-cdc', action='store_true', help='Coherent region/BIOS-region bridge; replace inherited known family hold cuts with scoped constraints')
    parser.add_argument('--video-config-cdc', action='store_true', help='Ordered video settings/control bridge (adds delivery latency); replace inherited known family hold cuts with scoped constraints')
    parser.add_argument('--apf-blank-cdc', action='store_true', help='Synchronize CPU reset blanking into the free-running APF video clock (adds bounded blanking latency); use scoped constraints')
    parser.add_argument('--video-return-ff', action='store_true', help='Keep reverse video sampling stages in flip-flops instead of shift RAM; preserves RTL timing, adds no reverse-bus exception')
    parser.add_argument('--video-request-cdc', action='store_true', help='Ordered video-to-2x request FIFO (adds delivery latency); reports/dotclock remain separate')
    parser.add_argument('--audio-rom-packed', action='store_true', help='Exact sinc coefficient tap-width packing; same samples, phases and schedule')
    parser.add_argument('--dpram-sdp', action='store_true', help='Specialize explicitly one-writer/one-reader DPRAM callsites to simple dual port M10K; preserve clocks/enables/latency')
    parser.add_argument('--dma-m10k', action='store_true', help='DMA channel tables in falling-edge block RAM instead of MLABs (scripts/prepare_dma_m10k.py; needs dma-chcr-ram)')
    parser.add_argument('--gte-m10k', action='store_true', help='GTE matrix/vector/translation tables in falling-edge clk2x block RAM instead of MLABs (scripts/prepare_gte_m10k.py; adds its multicycle SDC lines)')
    parser.add_argument('--pack-high', action='store_true', help='Fitter: ALM_REGISTER_PACKING_EFFORT HIGH (denser register packing; lets --disc-swap fit at 1,847 LABs)')
    parser.add_argument('--fit-effort-max', action='store_true', help='Fitter: STANDARD FIT effort and maximum router timing optimization (release builds: closes the small fast-corner hold misses that Auto Fit leaves)')
    parser.add_argument('--static-host-cuts', action='store_true', help='Native release timing: false paths for quasi-static host settings (pause, isPal) and the diagnostic service readbacks of cd_top/pausing; video-out synchronizers kept out of RAM shift registers')
    parser.add_argument('--memcard-slot', action='store_true', help='With --sotn-memory: memory card 1 always present, its image in vram_psram region 1, loaded from and saved to an APF nonvolatile slot (pocket_sotn pocket_memcard.vhd; package --memcard-slot)')
    parser.add_argument('--memcard-format', action='store_true', help='With --memcard-slot: a new or all-zero card file is formatted before the CPU starts, so games without a format option can save (rtl/pocket/pocket_memcard_format.vhd)')
    parser.add_argument('--memcard-lean', action='store_true', help='Memory card buffer without its two 64-bit copy registers (scripts/prepare_memcard_lean.py)')
    parser.add_argument('--pad-combos', action='store_true', help='Select-held combos for L2/R2/L3/R3 on the handheld (rtl/pocket/psx_pocket_pad_combo.sv replaces psx_pocket_pad.sv)')
    parser.add_argument('--libcrypt', action='store_true', help='With --cd-stream --multi-track: psx_cd_stream reads the disc\'s .sbi from data slot 99 and passes its LibCrypt mask to cd_top (package --libcrypt)')
    parser.add_argument('--disc-menu', type=int, metavar='DISCS', help='With --disc-swap --multi-track: one game file lists every disc (disc n in slots n01, n02, ...); a core menu "Swap Discs" list (bridge 0x50000044) switches between DISCS discs (2-4), so all discs share one memory card (package --disc-menu)')
    parser.add_argument('--boot-waits-disc', action='store_true', help='With --cd-stream: hold the CPU in reset until the first disc mount reaches cd_top (or 1.8 s after Reset Exit), so a slow first launch cannot boot the BIOS menu')
    parser.add_argument('--disc-swap', action='store_true', help='With --cd-stream: wire psx_disc_swap/psx_disc_select so loading another disc at run time opens the lid, remounts and closes it (without it a reloaded disc slot serves 0xAA fill forever)')
    parser.add_argument('--cd-slow-pause', action='store_true', help='MiSTer\'s default "Pause when CD slow": cd_top pauses the guest while a sector fetch from the SD card runs late (pauseOnCDSlow=1 in the PSX wrapper)')
    parser.add_argument('--trigger-buttons', action='store_true', help='A docked analog controller\'s triggers past 25%% also press L2/R2 (cont1_trig), for pads whose L2/R2 are analog triggers')
    parser.add_argument('--ntsc-fixed-vblank', action='store_true', help='NTSC pictures always span the fixed 240-line TV window (MiSTer "Fixed VBlank" for NTSC only): a game with a shorter display range gets black lines instead of fewer lines, which the APF scaler left stale (CTR logo)')
    parser.add_argument('--dualshock', action='store_true', help='Pad 1 is a DualShock (rtl/pocket/joypad_pad_dualshock.vhd, lockstep-equal to the pinned MiSTer joypad_pad) with a docked controller\'s sticks; menu checkbox "DualShock" (bridge 0x50000040, on by default) falls back to a digital pad (package --dualshock)')
    parser.add_argument('--stick-settings', action='store_true', help='With --dualshock --pad-combos --shoulder-setting: menu lists "D-pad" and "SELECT + D-pad" (D-pad, left or right stick) and stick options for "SELECT + A/B/X/Y" (scripts/prepare_pocket_stick_settings.py, rtl/pocket/psx_pocket_stick_map.sv; package --stick-settings)')
    parser.add_argument('--digital-pad', action='store_true', help='Digital-pad-only controller port (rtl/pocket/joypad_pad_digital.vhd, lockstep-equal to MiSTer for the Pocket tie-offs)')
    parser.add_argument('--vram-credit-timing', action='store_true', help="Codex's 4ea39ea VRAM BUSY timing repair (scripts/prepare_vram_credit_timing.py)")
    parser.add_argument('--cd-full', action='store_true', help='Restore the CD commands the FFVII-minimum trim dropped (Play, Forward, Backward, GetTN, GetTD, GetQ, VideoCD): CD audio, TOC queries')
    parser.add_argument('--cue-libcrypt', action='store_true', help='With --cue-mount: the reader also reads the .sbi next to a cue (same name) into the track table\'s LibCrypt mask')
    parser.add_argument('--cue-mount', action='store_true', help='With --one-file-disc: a .cue (and its track files) or a .bin picked with "Load disc" (slot 101) plays without a game file; rtl/pocket/psx_cue_mount.sv reads the cue with APF getfile/openfile and presents the tracks as one-file disc (package --cue-mount)')
    parser.add_argument('--one-file-disc', action='store_true', help='With --multi-track: each disc is one .bin of all its tracks (slot 101, disc n in n01) plus a 1,600-byte track table file in the next slot (with the LibCrypt mask, so no --libcrypt), so any track count fits; replaces the per-track file table (MAX_TRACKS 1, TABLE_OFFSET 1; package --one-file-disc, make_pocket_instances.py merges the tracks)')
    parser.add_argument('--multi-track', type=int, default=0, help='With --cd-stream: discs of up to N track files from an instance JSON (cue slot 100, bins 101..)')
    parser.add_argument('--bios-names', action='store_true', help='With --cd-stream: the BIOS loads from any slot 0x20-0x2f, so the Chip32 BIOS check can take other BIOS file names (package --bios-names)')
    parser.add_argument('--cd-stream', action='store_true', help='Discs from SD: psx_cd_stream on the APF target/table ports (disc slot 0, BIOS slot 0x20)')
    parser.add_argument('--audio-trace', action='store_true', help='With --lean-trace: header words 4/5/7 count SPU frames, nonzero frames and audio status')
    parser.add_argument('--lean-trace', action='store_true', help='Drop the VRAM/async/vblank counters (saves area)')
    parser.add_argument('--no-rom-log', action='store_true', help='Omit the sampled BIOS-read log (saves area)')
    parser.add_argument('--cache-trace', action='store_true', help='With --boot-trace: the ROM-log words record the first 512 CPU cache fills below RAM 0x10000 instead')
    parser.add_argument('--boot-trace', action='store_true', help='Expose a main-RAM request trace at bridge 0x40000000 (needs --debug-overlay)')
    args = parser.parse_args()
    if args.spu_cache and not (args.sotn_memory and args.sdram_ctrl == 'clean'): parser.error('--spu-cache needs --sotn-memory --sdram-ctrl clean')
    if args.spu_cache_slim and not args.spu_cache: parser.error('--spu-cache-slim needs --spu-cache')
    if args.async_hold_cut and (args.host_config_cdc or args.video_config_cdc or args.apf_blank_cdc or args.video_return_ff or args.video_request_cdc):
        parser.error('Configuration CDC requires scoped timing/skew checks; cannot request --async-hold-cut')
    if args.vram_cdc_constraints and args.async_hold_cut:
        parser.error('--vram-cdc-constraints cannot use --async-hold-cut: Quartus Lite suppresses Gray skew checks')
    run = args.run.resolve(); out = args.output.resolve()
    if out.exists(): parser.error(f'{out} exists')
    shutil.copytree(run, out, ignore=shutil.ignore_patterns('db', 'incremental_db', 'output_files'))
    for name in ('apf_top.qsf', 'core_top.sv'):
        path = out/name; path.write_text(path.read_text().replace(str(run), str(out)))
    # A run prepared in another checkout (or worktree) names that checkout's
    # rtl/ and references/: use this checkout's, so the pinned-path options
    # below match and rtl/ edits made here are the ones compiled.
    here = Path(__file__).resolve().parents[1]
    qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
    other = {m for m in re.findall(r'"(/[^"]*?)/(?:references/PSX_MiSTer|rtl/pocket)/', q) if Path(m) != here}
    for root in other:
        q = q.replace(f'"{root}/rtl/', f'"{here}/rtl/').replace(f'"{root}/references/', f'"{here}/references/')
    if other: qsf_path.write_text(q); print('Sources repointed to', here, 'from', ', '.join(sorted(other)))
    sdc_path = out/'pocket.sdc'
    if sdc_path.exists():   # its `source` of the APF constraints names a checkout too
        from prepare_pocket_audio_stream import refresh_audio_scope
        s = sdc_path.read_text()
        for root in {m for m in re.findall(r'"(/[^"]*?)/references/', s) if Path(m) != here}:
            s = s.replace(f'"{root}/references/', f'"{here}/references/')
        # Copied bases can predate the exact binary-MSB replica resolver.
        # Refresh only its recognized body; keep every crossing limit/cut.
        sdc_path.write_text(refresh_audio_scope(s))
    if args.sotn_memory:
        # MIRLO64's SDRAM and PSRAM controllers (Apache-2.0, workspace only)
        # are not instantiated in the Native layout: keep them out of the
        # compile inputs (release source bundle).
        q = qsf_path.read_text()
        q = re.sub(r'(?m)^set_global_assignment -name SYSTEMVERILOG_FILE "[^"]*/references/MIRLO64/rtl/soc/(?:sdr_ctrl|psram)\.sv".*\n', '', q)
        qsf_path.write_text(q)
    if args.refresh_audio_bridge:
        bridge = out/'pocket-audio/psx_audio_bridge.sv'
        if not bridge.is_file(): parser.error('Run has no copied audio bridge')
        shutil.copyfile(Path(__file__).resolve().parents[1]/'rtl/audio/psx_audio_bridge.sv', bridge)
    core = out/'core_top.sv'; text = core.read_text()
    # Generators from 2026-10-14 already emit the APF video registers.
    if text.count(OLD) != 1 and text.count('assign video_skip=apf_skip, video_rgb=apf_rgb;') != 1:
        parser.error('Expected pass-through video assignments not found')
    text = text.replace(OLD, NEW)
    if args.boot_trace and not args.debug_overlay: parser.error('--boot-trace needs --debug-overlay')
    if args.debug_overlay:
        rgb = "        apf_rgb<=(!hblank && !psx_vblank) ? {video_r,video_g,video_b} : 24'h0;"
        if text.count(rgb) == 1:
            text = text.replace(rgb, "        apf_rgb<=(!hblank && !psx_vblank) ? dbg_rgb : 24'h0;")
        elif text.count('        .rgb_in({video_r,video_g,video_b}),') == 1:   # psx_apf_video formatter
            text = text.replace('        .rgb_in({video_r,video_g,video_b}),', '        .rgb_in(dbg_rgb),')
        else: parser.error('APF video block not found')
        anchor = '    // APF scaler protocol:'
        overlay = OVERLAY.replace('DBG_ASYNC_ACK', 'vx_ack' if args.vram_async else "1'b0")
        if args.lean_trace:
            for block in ('''    always @(posedge clk2x) begin
        if ((ddr3_RD || ddr3_WE) && !ddr3_BUSY) dbg_vram_cmd<=dbg_vram_cmd+1'b1;
        if (ddr3_DOUT_READY) dbg_vram_beat<=dbg_vram_beat+1'b1;
    end
''', "    always @(posedge ps_clk) if (" + ('vx_ack' if args.vram_async else "1'b0") + ") dbg_async<=dbg_async+1'b1;\n"):
                if overlay.count(block) != 1: parser.error('Overlay counter block not found')
                overlay = overlay.replace(block, '')
        if args.audio_trace:
            if not args.lean_trace: parser.error('--audio-trace reuses --lean-trace counters')
            # Bridges from 008418c report eight status bits; older ones do not.
            if text.count('.fault(audio_fault),') == 1 and 'output wire [7:0] status' in (out/'pocket-audio/psx_audio_bridge.sv').read_text():
                text = text.replace('.fault(audio_fault),', '.fault(audio_fault),.status(audio_status),')
                text = text.replace('    wire audio_fault;', '    wire audio_fault;\n    wire [7:0] audio_status;', 1)
                status = 'audio_status'
            else:
                status = "8'b0"
            marker = '    wire [31:0] dbg_status='
            if overlay.count(marker) != 1: parser.error('Overlay status word not found')
            overlay = overlay.replace(marker, '''    // Audio trace: word 4 SPU output frames, word 5 nonzero frames,
    // word 7 {audio_fault, sound_running, audio_locked, audio_reset_async, 28'b0}.
    always @(posedge clk1x) begin
        if (sound_out_valid) dbg_vram_cmd<=dbg_vram_cmd+1'b1;
        if (sound_out_valid && (sound_out_left!=0 || sound_out_right!=0)) dbg_vram_beat<=dbg_vram_beat+1'b1;
        dbg_async<={audio_fault, sound_running, audio_locked, audio_reset_async, 20'b0, AUDIO_STATUS};
    end
'''.replace('AUDIO_STATUS', status) + marker, 1)
        if args.no_overlay_picture:
            start = overlay.index('    psx_debug_overlay #(')
            overlay = overlay[:start] + "    assign dbg_rgb={video_r,video_g,video_b};\n"
        text = text.replace(anchor, overlay+anchor)
        qsf = out/'apf_top.qsf'
        qsf.write_text(qsf.read_text()+f'set_global_assignment -name SYSTEMVERILOG_FILE "{Path(__file__).resolve().parents[1]}/rtl/pocket/psx_debug_overlay.sv" -library work\n')
        if args.boot_trace:
            trace = TRACE.replace('psx_boot_trace boot_trace', 'psx_boot_trace #(.ROM_LOG(0)) boot_trace') if args.no_rom_log else TRACE
            if args.cache_trace:
                trace = trace.replace('psx_boot_trace boot_trace', 'psx_boot_trace #(.CACHE_LOG(1)) boot_trace').replace(
                    ".cache_clk(clk1x), .cache_wr(4'b0), .cache_data(32'b0),", '.cache_clk(clk3x), .cache_wr(cache_wr), .cache_data(cache_data),')
            text = text.replace(anchor, trace+anchor)
            mux = "        if (bridge_addr[31:24]==8'hf8) bridge_rd_data=cmd_bridge_rd_data;"
            if text.count(mux) != 1: parser.error('Bridge read mux not found')
            text = text.replace(mux, mux+"\n        if (bridge_addr[31:24]==8'h40) bridge_rd_data=trace_data;")
            # The Pocket saves a nonvolatile slot with the size in the core's
            # data table; with no file yet that is 0. Slot index 1's size
            # word is entry 3 (pairs of id, size).
            table = "        .datatable_addr('0),\n        .datatable_wren('0),\n        .datatable_data('0),"
            if text.count(table) != 1: parser.error('Data table port not found')
            text = text.replace(table, "        .datatable_addr(10'd3),\n        .datatable_wren(1'b1),\n        .datatable_data(32'd28672),")
            qsf.write_text(qsf.read_text()+f'set_global_assignment -name SYSTEMVERILOG_FILE "{Path(__file__).resolve().parents[1]}/rtl/pocket/psx_boot_trace.sv" -library work\n')
            sdc = out/'pocket.sdc'
            sdc.write_text(sdc.read_text()+'set_false_path -to [get_registers {*|boot_trace|total_meta[*] *|boot_trace|ring_meta[*] *|boot_trace|stored_*[*]}]\n')
        sdc = out/'pocket.sdc'
        sdc.write_text(sdc.read_text()+'set_false_path -to [get_registers {*|debug_overlay|words_meta[*]}]\n')
    if args.vram_async:
        if text.count(VRAM_OLD) != 1: parser.error('VRAM controller instance not found')
        anchor = '    psram vram_controller ('
        if text.count(anchor) != 1: parser.error('VRAM controller anchor not found')
        text = text.replace(VRAM_OLD, VRAM_NEW).replace(anchor, VRAM_SHIM+anchor)
        qsf = out/'apf_top.qsf'
        qsf.write_text(qsf.read_text()+f'set_global_assignment -name SYSTEMVERILOG_FILE "{Path(__file__).resolve().parents[1]}/rtl/pocket/psx_psram_z2x.sv" -library work\n')
    if args.sdram_clock_ps is not None:
        pll_old = '''.output_clock_frequency2("101.606400 MHz"), .phase_shift2("0 ps"), .duty_cycle2(50)) cpu_pll ('''
        if text.count(pll_old) != 1 or text.count('.outclk({clk3x,clk2x,clk1x}));') != 1: parser.error('CPU PLL not found')
        text = text.replace(pll_old, f'''.output_clock_frequency2("101.606400 MHz"), .phase_shift2("0 ps"), .duty_cycle2(50),
        .output_clock_frequency3("101.606400 MHz"), .phase_shift3("{args.sdram_clock_ps} ps"), .duty_cycle3(50)) cpu_pll (''')
        text = text.replace('.number_of_clocks(3), .pll_type("General"), .pll_subtype("General"),\n        .output_clock_frequency0("33.868800 MHz")',
                            '.number_of_clocks(4), .pll_type("General"), .pll_subtype("General"),\n        .output_clock_frequency0("33.868800 MHz")')
        if '.number_of_clocks(4)' not in text: parser.error('CPU PLL clock count not found')
        text = text.replace('.outclk({clk3x,clk2x,clk1x}));', '.outclk({sdram_clock_out,clk3x,clk2x,clk1x}));')
        text = text.replace('    wire cpu_locked;', '    wire cpu_locked;\n    wire sdram_clock_out;', 1)
        if text.count('    sdram main_memory (') != 1: parser.error('SDRAM instance not found')
        text = text.replace('    sdram main_memory (', '    sdram main_memory (.clk_out(sdram_clock_out), ')
        sdram = out/'sdram.sv'; s = sdram.read_text()
        old = '''	.datain_h(1'b0),
	.datain_l(1'b1),
	.outclock(clk),'''
        if s.count(old) != 1 or s.count('	output             SDRAM_CLK,') != 1: parser.error('SDRAM clock output not found')
        s = s.replace(old, '''	.datain_h(1'b1),
	.datain_l(1'b0),
	.outclock(clk_out),''')
        s = s.replace('	output             SDRAM_CLK,', '	output             SDRAM_CLK,\n	input              clk_out,     // PSXEarly: phase-selected SDRAM clock source', 1)
        sdram.write_text(s)
    if args.vram_credit_timing:
        import json
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from prepare_vram_credit_timing import prepare as prepare_credit, conservative_text, timing_text
        from prepare_resource_targets import digest
        from measure_pocket_seeds import inputs as assigned_inputs
        selected = [s for s in assigned_inputs(out) if Path(s['path']).name == 'psx_vram_stream.sv']
        if len(selected) != 1: parser.error('Expected one assigned psx_vram_stream.sv')
        original = Path(selected[0]['path'])
        base = (Path(__file__).resolve().parents[1]/'rtl/pocket/psx_vram_stream.sv').read_text()
        selected_text = original.read_text()
        forms = {'base': base, 'conservative': conservative_text(base), 'reassociated': timing_text(base)}
        profile = next((name for name, value in forms.items() if value == selected_text), None)
        if profile is None: parser.error('Unrecognized VRAM frontend; refusing to overwrite other adaptations')
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        if q.count(f'"{original}"') != 1: parser.error('Expected one quoted VRAM source assignment')
        if digest(original) != selected[0]['sha256']: parser.error('Assigned VRAM source changed during preparation')
        repaired = prepare_credit(out/'vram-credit-timing')
        qsf_path.write_text(q.replace(f'"{original}"', f'"{repaired["path"]}"'))
        (out/'vram-credit-preparation.json').write_text(json.dumps(dict(
            selected_before=selected[0], selected_profile=profile, generated=repaired,
            copied_parent=str(run), preparer_sha256=digest(Path(__file__))), indent=2)+'\n')
    if args.spu_cached:
        wrappers = [Path(m) for m in re.findall(r'"([^"]*psx_mister_psx_top_resource\.vhd)"', (out/'apf_top.qsf').read_text())]
        if len(wrappers) != 1: parser.error('Expected one PSX wrapper in the project')
        w = wrappers[0].read_text()
        if w.count("SPUSDRAM => '1',") != 1: parser.error('SPUSDRAM selection not found')
        wrappers[0].write_text(w.replace("SPUSDRAM => '1',", "SPUSDRAM => '0',  -- PSXEarly --spu-cached"))
    if args.stub_memcard_copy and not args.stub_memcards:
        refs = Path(__file__).resolve().parents[1]/'references/PSX_MiSTer/rtl'
        tops = [Path(m) for m in re.findall(r'"([^"]*/psx_top\.vhd)"', (out/'apf_top.qsf').read_text())]
        if len(tops) != 1: parser.error('Expected one psx_top.vhd in the project')
        v = tops[0].read_text()
        for label in ('imemcard1', 'imemcard2'):
            v = stub_instance(v, label, refs/'memcard.vhd', 'MiSTer memory-card copy engine omitted (APF slots persist cards)')
        tops[0].write_text(v)
    if args.stub_memcards:
        refs = Path(__file__).resolve().parents[1]/'references/PSX_MiSTer/rtl'
        qsf_path = out/'apf_top.qsf'; qsf_text = qsf_path.read_text()
        tops = [Path(m) for m in re.findall(r'"([^"]*/psx_top\.vhd)"', qsf_text)]
        if len(tops) != 1: parser.error('Expected one psx_top.vhd in the project')
        v = tops[0].read_text()
        for label in ('imemcard1', 'imemcard2'):
            # Newer generated tops already drop the MiSTer copy engines.
            if f'   {label} : entity' in v or f'   {label}: entity' in v:
                v = stub_instance(v, label, refs/'memcard.vhd', 'memory card device omitted')
        tops[0].write_text(v)
        joypad = out/'joypad_nocards.vhd'
        j = (refs/'joypad.vhd').read_text()
        for label in ('ijoypad_mem1', 'ijoypad_mem2'):
            j = stub_instance(j, label, refs/'joypad_mem.vhd', 'memory card slot omitted')
        joypad.write_text(j)
        if qsf_text.count(f'"{refs}/joypad.vhd"') != 1: parser.error('joypad.vhd not in project')
        qsf_path.write_text(qsf_text.replace(f'"{refs}/joypad.vhd"', f'"{joypad}"'))
    if args.no_memcard2 and not args.stub_memcards:
        refs = Path(__file__).resolve().parents[1]/'references/PSX_MiSTer/rtl'
        qsf_path = out/'apf_top.qsf'; qsf_text = qsf_path.read_text()
        if qsf_text.count(f'"{refs}/joypad.vhd"') != 1: parser.error('joypad.vhd not in project')
        joypad = out/'joypad_card1.vhd'
        joypad.write_text(stub_instance((refs/'joypad.vhd').read_text(), 'ijoypad_mem2', refs/'joypad_mem.vhd', 'memory card slot 2 empty'))
        qsf_path.write_text(qsf_text.replace(f'"{refs}/joypad.vhd"', f'"{joypad}"'))
    if args.steady_field:
        if args.steady_interlace: parser.error('--steady-field replaces --steady-interlace')
        root = Path(__file__).resolve().parents[1]
        qsf_path = out/'apf_top.qsf'; qsf_text = qsf_path.read_text()
        pinned = root/'references/PSX_MiSTer/rtl/gpu_videoout_async.vhd'
        if qsf_text.count(f'"{pinned}"') != 1: parser.error('gpu_videoout_async.vhd not in project')
        old = '               activeLineLSBMuxed          := videoout_reports.activeLineLSB;\n'
        v = pinned.read_text()
        if v.count(old) != 1: parser.error('gpu_videoout_async line select changed')
        # Fetch only the vramRange(10)-relative even lines in 480i. GPUSTAT
        # field/odd-line reports are unchanged, so games see the real field.
        v = v.replace(old, '               activeLineLSBMuxed          := videoout_settings.vramRange(10); -- PSXEarly --steady-field\n')
        copy = out/'gpu_videoout_async_steady.vhd'; copy.write_text(v)
        qsf_path.write_text(qsf_text.replace(f'"{pinned}"', f'"{copy}"'))
        if text.count('psx_apf_video #(.STEADY_INTERLACE(1))') == 1:
            text = text.replace('psx_apf_video #(.STEADY_INTERLACE(1))', 'psx_apf_video #(.STEADY_INTERLACE(0))')
    if args.sio_fixed:
        root = Path(__file__).resolve().parents[1]
        qsf_path = out/'apf_top.qsf'; qsf_text = qsf_path.read_text()
        pinned = f'"{root}/references/PSX_MiSTer/rtl/sio.vhd"'
        if qsf_text.count(pinned) != 1: parser.error('sio.vhd not in project')
        qsf_path.write_text(qsf_text.replace(pinned, f'"{root}/rtl/pocket/sio_fixed.vhd"'))
    if args.gpu_area_reg:
        from prepare_gpu_area_reg import area_reg_text
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        polys = re.findall(r'"([^"]*/gpu_poly\.vhd)"', q)
        if len(polys) != 1: parser.error('--gpu-area-reg: expected one gpu_poly.vhd in the project')
        copy = out/'gpu-area-reg'/'gpu_poly.vhd'; copy.parent.mkdir(exist_ok=True)
        poly_text = area_reg_text(Path(polys[0]).read_text())
        if args.gpu_dy_ram:
            from prepare_gpu_dy_ram import dy_ram_text
            poly_text = dy_ram_text(poly_text)
        copy.write_text(poly_text)
        qsf_path.write_text(q.replace(f'"{polys[0]}"', f'"{copy}"'))
    if args.memctrl_lean:
        import json as json_mod
        manifest = json_mod.loads((out/'manifest.json').read_text()) if (out/'manifest.json').exists() else {}
        if not manifest.get('reset_only'): parser.error('--memctrl-lean needs a reset-only (no savestate) run')
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        mcs = re.findall(r'"([^"]*/memctrl\.vhd)"', q)
        if len(mcs) != 1: parser.error('--memctrl-lean: expected one memctrl.vhd in the project')
        lean = Path(__file__).resolve().parents[1]/'rtl/pocket/memctrl_lean.vhd'
        qsf_path.write_text(q.replace(f'"{mcs[0]}"', f'"{lean}"'))
    if args.cd_xa_ring_norw:
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        xas = re.findall(r'"([^"]*/cd_xa\.vhd)"', q)
        if len(xas) != 1: parser.error('--cd-xa-ring-norw: expected one cd_xa.vhd in the project')
        anchor = '   signal XaRingBufferR       : tXaRingBuffer;\n'
        xa = Path(xas[0]).read_text()
        if xa.count(anchor) != 1: parser.error('--cd-xa-ring-norw: ring buffer declaration not found')
        # One state machine writes the rings (RESAMPLE_CLEAR, RESAMPLE_WRITERING)
        # and reads them (RESAMPLE_READRING): never in the same cycle.
        xa = xa.replace(anchor, anchor + '   attribute ramstyle : string;\n'
                        '   attribute ramstyle of XaRingBufferL : signal is "no_rw_check";\n'
                        '   attribute ramstyle of XaRingBufferR : signal is "no_rw_check";\n')
        copy = out/'cd-xa-ring-norw'/'cd_xa.vhd'; copy.parent.mkdir(exist_ok=True); copy.write_text(xa)
        qsf_path.write_text(q.replace(f'"{xas[0]}"', f'"{copy}"'))
    if args.gte_shadow_m10k:
        from prepare_gte_shadow_m10k import shadow_m10k_text
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        regs = re.findall(r'"([^"]*/GTERegs\.sv)"', q)
        if len(regs) != 1: parser.error('--gte-shadow-m10k: expected one GTERegs.sv in the project')
        if 'ctrlShadow' not in Path(regs[0]).read_text(): parser.error('--gte-shadow-m10k needs gte-control-ram')
        copy = out/'gte-shadow-m10k'/'GTERegs.sv'; copy.parent.mkdir(exist_ok=True)
        copy.write_text(shadow_m10k_text(Path(regs[0]).read_text()))
        qsf_path.write_text(q.replace(f'"{regs[0]}"', f'"{copy}"'))
    if args.gpu_read_late:
        from prepare_gpu_read_late import read_late_text
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        gpus = re.findall(r'"([^"]*/gpu\.vhd)"', q)
        if len(gpus) != 1: parser.error('--gpu-read-late: expected one gpu.vhd in the project')
        copy = out/'gpu-read-late'/'gpu.vhd'; copy.parent.mkdir(exist_ok=True)
        copy.write_text(read_late_text(Path(gpus[0]).read_text()))
        qsf_path.write_text(q.replace(f'"{gpus[0]}"', f'"{copy}"'))
    if args.spu_static_readback:
        from prepare_spu_static_readback import static_readback_text
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        spus = re.findall(r'"([^"]*/spu\.vhd)"', q)
        if len(spus) != 1: parser.error('--spu-static-readback: expected one spu.vhd in the project')
        copy = out/'spu-static-readback'/'spu.vhd'; copy.parent.mkdir(exist_ok=True)
        copy.write_text(static_readback_text(Path(spus[0]).read_text()))
        qsf_path.write_text(q.replace(f'"{spus[0]}"', f'"{copy}"'))
    if args.spu_adsr_lean:
        from prepare_spu_adsr_lean import adsr_lean_text
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        spus = re.findall(r'"([^"]*/spu\.vhd)"', q)
        if len(spus) != 1: parser.error('--spu-adsr-lean: expected one spu.vhd in the project')
        copy = out/'spu-adsr-lean'/'spu.vhd'; copy.parent.mkdir(exist_ok=True)
        copy.write_text(adsr_lean_text(Path(spus[0]).read_text()))
        qsf_path.write_text(q.replace(f'"{spus[0]}"', f'"{copy}"'))
    if args.spu_engine_m10k:
        from prepare_spu_engine_m10k import engine_m10k_text
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        spus = re.findall(r'"([^"]*/spu\.vhd)"', q)
        if len(spus) != 1: parser.error('--spu-engine-m10k: expected one spu.vhd in the project')
        copy = out/'spu-engine-m10k'/'spu.vhd'; copy.parent.mkdir(exist_ok=True)
        copy.write_text(engine_m10k_text(Path(spus[0]).read_text()))
        qsf_path.write_text(q.replace(f'"{spus[0]}"', f'"{copy}"'))
    if args.spu_cd_queue:
        from prepare_spu_cd_queue import cd_queue_text
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        spus = re.findall(r'"([^"]*/spu\.vhd)"', q)
        if len(spus) != 1: parser.error('--spu-cd-queue: expected one spu.vhd in the project')
        copy = out/'spu-cd-queue'/'spu.vhd'; copy.parent.mkdir(exist_ok=True)
        copy.write_text(cd_queue_text(Path(spus[0]).read_text()))
        qsf_path.write_text(q.replace(f'"{spus[0]}"', f'"{copy}"'))
    if args.spu_free_slot:
        # spu.vhd with useSDRAM='1' holds each voice in RAM_WAIT until
        # voiceCounter >= 23 (a fixed ~24-clk1x slot); free it as useSDRAM='0'
        # does, so cache hits shorten a voice instead of idling.
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        spus = re.findall(r'"([^"]*/spu\.vhd)"', q)
        if len(spus) != 1: parser.error('--spu-free-slot: expected one spu.vhd in the project')
        ram_wait = "                  if (voiceCounter >= 23 or useSDRAM = '0') then"
        spu_text = Path(spus[0]).read_text()
        if spu_text.count(ram_wait) != 1: parser.error('--spu-free-slot: RAM_WAIT slot not found')
        copy = out/'spu-free-slot'/'spu.vhd'; copy.parent.mkdir(exist_ok=True)
        copy.write_text(spu_text.replace(ram_wait, "                  if (true) then   -- --spu-free-slot: no fixed SDRAM voice slot (as useSDRAM='0')"))
        qsf_path.write_text(q.replace(f'"{spus[0]}"', f'"{copy}"'))
    if args.main_lean:
        if args.psram_sync: parser.error('--main-lean is for the asynchronous adapter; the sync adapter already has it')
        from prepare_main_lean import main_lean_text
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        adapters = re.findall(r'"([^"]*/psx_mister_main_adapter\.sv)"', q)
        if len(adapters) != 1: parser.error('--main-lean: expected one psx_mister_main_adapter.sv in the project')
        copy = out/'main-lean'/'psx_mister_main_adapter.sv'; copy.parent.mkdir(exist_ok=True)
        copy.write_text(main_lean_text(Path(adapters[0]).read_text()))
        qsf_path.write_text(q.replace(f'"{adapters[0]}"', f'"{copy}"'))
    if args.memcard_ctrl is None: args.memcard_ctrl = 'clean' if args.memcard_format else 'sotn'
    if args.memorymux_m10k and args.memorymux_fifo_ram:
        parser.error('--memorymux-fifo-ram replaces --memorymux-m10k; pick one')
    if args.memorymux_m10k:
        from prepare_memorymux_m10k import prepare_project
        prepare_project(out/'apf_top.qsf', out/'memorymux-m10k')
    if args.memorymux_fifo_ram:
        from prepare_memorymux_fifo_ram import prepare_project as fifo_ram_project
        fifo_ram_project(out/'apf_top.qsf', out/'memorymux-fifo-ram', args.memorymux_fifo_ram)
    if args.dma_m10k:
        from prepare_dma_m10k import dma_m10k_text
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        dmas = re.findall(r'"([^"]*/dma\.vhd)"', q)
        if len(dmas) != 1: parser.error('--dma-m10k: expected one dma.vhd in the project')
        copy = out/'dma-m10k'/'dma.vhd'; copy.parent.mkdir(exist_ok=True)
        copy.write_text(dma_m10k_text(Path(dmas[0]).read_text()))
        qsf_path.write_text(q.replace(f'"{dmas[0]}"', f'"{copy}"'))
    if args.gte_m10k:
        from prepare_gte_m10k import gte_m10k_text, SDC as GTE_M10K_SDC
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        paths = re.findall(r'"([^"]*/GTEComputePath\.sv)"', q)
        if len(paths) != 1: parser.error('--gte-m10k: expected one GTEComputePath.sv in the project')
        if 'gte-multicycle' not in q: parser.error('--gte-m10k needs gte-multicycle (strict alternate-edge enable)')
        copy = out/'gte-m10k'/'GTEComputePath.sv'; copy.parent.mkdir(exist_ok=True)
        copy.write_text(gte_m10k_text(Path(paths[0]).read_text()))
        qsf_path.write_text(q.replace(f'"{paths[0]}"', f'"{copy}"'))
        sdc = out/'pocket.sdc'; sdc.write_text(sdc.read_text() + '\n' + GTE_M10K_SDC)
    if args.memcard_format and not args.memcard_slot:
        parser.error('--memcard-format needs --memcard-slot')
    if args.memcard_lean:
        if args.stub_memcards: parser.error('--memcard-lean needs the memory card devices')
        from prepare_memcard_lean import memcard_lean_text
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        pinned = re.findall(r'"([^"]*/references/PSX_MiSTer/rtl/joypad_mem\.vhd)"', q)
        if len(pinned) != 1: parser.error('--memcard-lean: joypad_mem.vhd not in project')
        copy = out/'joypad_mem_lean.vhd'
        copy.write_text(memcard_lean_text(Path(pinned[0]).read_text()))
        qsf_path.write_text(q.replace(f'"{pinned[0]}"', f'"{copy}"'))
    if args.digital_pad:
        root = Path(__file__).resolve().parents[1]
        qsf_path = out/'apf_top.qsf'; qsf_text = qsf_path.read_text()
        pinned = f'"{root}/references/PSX_MiSTer/rtl/joypad_pad.vhd"'
        if qsf_text.count(pinned) != 1: parser.error('joypad_pad.vhd not in project')
        qsf_path.write_text(qsf_text.replace(pinned, f'"{root}/rtl/pocket/joypad_pad_digital.vhd"'))
    if args.ntsc_fixed_vblank and not args.pal288:
        # The fixed window applies to NTSC only: PAL keeps its display range
        # (the 240-line scaler slots have no 288-line PAL window). With
        # --pal288 the 288-line slots exist, so PAL gets MiSTer's fixed
        # 288-line window too and always fills its slot.
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        gpus = re.findall(r'"([^"]*/gpu\.vhd)"', q)
        if len(gpus) != 1: parser.error('--ntsc-fixed-vblank: expected one gpu.vhd in the project')
        gpu_text = Path(gpus[0]).read_text()
        anchor = '   videoout_settings.fixedVBlank             <= fixedVBlank;'
        if gpu_text.count(anchor) != 1: parser.error('--ntsc-fixed-vblank: fixedVBlank setting not found in gpu.vhd')
        copy = out/'ntsc-fixed-vblank'/'gpu.vhd'; copy.parent.mkdir(exist_ok=True)
        copy.write_text(gpu_text.replace(anchor, '   videoout_settings.fixedVBlank             <= fixedVBlank and not GPUSTAT_PalVideoMode;  -- PSXEarly --ntsc-fixed-vblank'))
        qsf_path.write_text(q.replace(f'"{gpus[0]}"', f'"{copy}"'))
    if args.dualshock or args.cd_slow_pause or args.ntsc_fixed_vblank:
        # Edits to the PSX wrapper go into one copy in the output directory.
        qsf_path = out/'apf_top.qsf'; qsf_text = qsf_path.read_text()
        wrappers = re.findall(r'"([^"]*/psx_mister_psx_top_resource\.vhd)"', qsf_text)
        if len(wrappers) != 1: parser.error('PSX wrapper edits: expected one PSX wrapper in the project')
        wrapper = Path(wrappers[0]).read_text()
        if args.ntsc_fixed_vblank:
            if wrapper.count("fixedVBlank => '0',") != 1: parser.error('--ntsc-fixed-vblank: fixedVBlank tie-off not found')
            wrapper = wrapper.replace("fixedVBlank => '0',", "fixedVBlank => '1',  -- PSXEarly --ntsc-fixed-vblank (NTSC only, gpu.vhd)")
        if args.cd_slow_pause:
            if wrapper.count("pauseOnCDSlow => '0',") != 1: parser.error('--cd-slow-pause: pauseOnCDSlow tie-off not found')
            wrapper = wrapper.replace("pauseOnCDSlow => '0',", "pauseOnCDSlow => '1',  -- PSXEarly --cd-slow-pause (MiSTer default)")
        copy = out/'psx_mister_psx_top_resource.vhd'
        if Path(wrappers[0]).resolve() == copy.resolve(): parser.error('PSX wrapper already in the output directory')
        copy.write_text(wrapper)
        qsf_path.write_text(qsf_text.replace(f'"{wrappers[0]}"', f'"{copy}"'))
    if args.dualshock:
        if args.digital_pad: parser.error('--dualshock replaces --digital-pad')
        from prepare_pocket_dualshock_pad import dualshock_wrapper_text, DUALSHOCK_TOP, DUALSHOCK_SDC
        copy = out/'psx_mister_psx_top_resource.vhd'
        try: copy.write_text(dualshock_wrapper_text(copy.read_text()))
        except ValueError as error: parser.error('--dualshock: ' + str(error))
        root = Path(__file__).resolve().parents[1]
        qsf_path = out/'apf_top.qsf'; qsf_text = qsf_path.read_text()
        # rtl/pocket/joypad_pad_dualshock.vhd: MiSTer's pad cut to one
        # DualShock port (lockstep-equal under the Pocket tie-offs, make
        # test-joypad-dualshock-lean), about 500 ALUTs smaller in map.
        pinned = f'"{root}/references/PSX_MiSTer/rtl/joypad_pad.vhd"'
        if qsf_text.count(pinned) != 1: parser.error('--dualshock: joypad_pad.vhd not in project')
        qsf_text = qsf_text.replace(pinned, f'"{root}/rtl/pocket/joypad_pad_dualshock.vhd"')
        qsf_path.write_text(qsf_text + f'set_global_assignment -name SYSTEMVERILOG_FILE "{root}/rtl/pocket/psx_pocket_analog.sv" -library work\n')
        for old, new in DUALSHOCK_TOP:
            if text.count(old) != 1: parser.error('--dualshock: core_top anchor not found: ' + old.strip()[:60])
            text = text.replace(old, new)
        sdc = out/'pocket.sdc'; sdc.write_text(sdc.read_text() + DUALSHOCK_SDC)
    if args.pad_combos:
        qsf_path = out/'apf_top.qsf'; qsf_text = qsf_path.read_text()
        pads = re.findall(r'"([^"]*/psx_pocket_pad\.sv)"', qsf_text)
        if len(pads) != 1: parser.error('--pad-combos: expected one psx_pocket_pad.sv in the project (release host trim base)')
        combo = Path(__file__).resolve().parents[1]/'rtl/pocket/psx_pocket_pad_combo.sv'
        qsf_path.write_text(qsf_text.replace(f'"{pads[0]}"', f'"{combo}"'))
        inst = '.apf_keys(cont1_key[15:0]),.buttons(pocket_pad_buttons));'
        decl = '    wire [15:0] pocket_pad_buttons;\n'
        if text.count(inst) != 1 or text.count(decl) != 1: parser.error('--pad-combos: pad instance not found')
        text = text.replace(inst, '.apf_keys(cont1_key[15:0]),.combo_enable(pad_combo_enable),.dpad_combo_enable(pad_dpad_combo_enable),\n'
                            '        .buttons(pocket_pad_buttons),.combos(pad_combos),.dpad_combos(pad_dpad_combos),.docked(pad_combo_docked));')
        text = text.replace(decl, decl + "    wire [5:0] pad_combo_enable, pad_combos;   // L, R, Y, A, X, B\n    wire [3:0] pad_combo_docked, pad_dpad_combos;\n"
                            "    wire pad_dpad_combo_enable;\n"
                            "    assign pad_combo_enable=6'b001111;   // Select+L/R/Y/A = L2/R2/L3/R3\n"
                            "    assign pad_dpad_combo_enable=1'b0;   // Select+D-pad: --stick-settings\n")
    if args.stub_mdec:
        qsf_text = (out/'apf_top.qsf').read_text()
        tops = [Path(m) for m in re.findall(r'"([^"]*/psx_top\.vhd)"', qsf_text)]
        if len(tops) != 1: parser.error('Expected one psx_top.vhd in the project')
        vhdl = tops[0].read_text()
        match = re.search(r'   imdec : entity work\.mdec\n.*?\n   \);\n', vhdl, re.S)
        if not match: parser.error('MDEC instance not found')
        stub = '''   -- PSXEarly test build: MDEC omitted (movies off) to fit the device.
   bus_mdec_dataRead    <= (others => '0');
   mdec_dmaWriteRequest <= '0';
   mdec_dmaReadRequest  <= '0';
   DMA_MDEC_read        <= (others => '0');
   SS_DataRead_MDEC     <= (others => '0');
   SS_Idle_mdec         <= '1';
'''
        tops[0].write_text(vhdl[:match.start()] + stub + vhdl[match.end():])
    if args.cd_full:
        # The FFVII-minimum CD trim (prepare_cd_ffvii.py) is the cd-arithmetic
        # copy minus seven commands; point the project back at that copy.
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        trimmed = re.findall(r'"([^"]*/cd-ffvii/cd_top\.vhd)"', q)
        if len(trimmed) != 1: parser.error('--cd-full: trimmed cd_top.vhd not in the project')
        full = Path(trimmed[0]).parent.parent/'cd-arithmetic'/'cd_top.vhd'
        from prepare_cd_ffvii import cd_ffvii_text
        if not full.exists() or cd_ffvii_text(full.read_text()) != Path(trimmed[0]).read_text():
            parser.error('--cd-full: cd-arithmetic copy does not match the trim')
        qsf_path.write_text(q.replace(f'"{trimmed[0]}"', f'"{full}"'))
    if args.cd_sector_pack:
        import json as json_mod
        manifest = json_mod.loads((out/'manifest.json').read_text()) if (out/'manifest.json').exists() else {}
        if not manifest.get('reset_only'): parser.error('--cd-sector-pack needs a reset-only (no savestate) run')
        from prepare_cd_sector_pack import sector_pack_text
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        tops = re.findall(r'"([^"]*/cd_top\.vhd)"', q)
        if len(tops) != 1: parser.error('--cd-sector-pack: expected one cd_top.vhd in the project')
        copy = out/'cd-sector-pack'/'cd_top.vhd'; copy.parent.mkdir(exist_ok=True)
        copy.write_text(sector_pack_text(Path(tops[0]).read_text()))
        qsf_path.write_text(q.replace(f'"{tops[0]}"', f'"{copy}"'))
    if args.bios_names and not args.cd_stream: parser.error('--bios-names needs --cd-stream (BIOS slots from 0x20)')
    if args.cd_stream:
        if args.boot_trace: parser.error('--cd-stream uses the data-table port; drop --boot-trace')
        def once(old, new):
            nonlocal text
            if text.count(old) != 1: parser.error('CD stream anchor not found: ' + old.strip()[:60])
            text = text.replace(old, new)
        once('    psx_bios_loader bios_loader (', "    psx_bios_loader #(.BIOS_SLOT_ID(16'h20)) bios_loader (")
        if text.count('bios_slot_id==0 ?') != 2: parser.error('BIOS slot acknowledgement not found')
        text = text.replace('bios_slot_id==0 ?', "bios_slot_id==16'h20 ?")
        if args.bios_names:
            # The Chip32 BIOS check loadf's one of slots 0x20-0x2f, one per
            # accepted file name (build_chip32_loader.BIOS_NAMES).
            once("psx_bios_loader #(.BIOS_SLOT_ID(16'h20)) bios_loader (",
                 "psx_bios_loader #(.BIOS_SLOT_ID(16'h20), .BIOS_SLOT_MASK(16'hfff0)) bios_loader (")
            text = text.replace("bios_slot_id==16'h20 ?", "bios_slot_id[15:4]==12'h002 ?")
            # Older runs compile a release-boot copy of the loader without
            # BIOS_SLOT_MASK: compile the repository's current one instead.
            qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
            loaders = re.findall(r'"([^"]*/psx_bios_loader\.sv)"', q)
            if len(loaders) != 1: parser.error('--bios-names: expected one psx_bios_loader.sv in the project')
            copy = out/'bios-names'/'psx_bios_loader.sv'; copy.parent.mkdir(exist_ok=True)
            shutil.copyfile(Path(__file__).resolve().parents[1]/'rtl/pocket/psx_bios_loader.sv', copy)
            qsf_path.write_text(q.replace(f'"{loaders[0]}"', f'"{copy}"'))
        once("""        .dataslot_update(),
        .dataslot_update_id(),
        .dataslot_update_size(),""", """        .dataslot_update(cd_slot_update),
        .dataslot_update_id(cd_slot_update_id),
        .dataslot_update_size(cd_slot_update_size),""")
        once("""        .target_dataslot_read('0),
        .target_dataslot_write('0),
        .target_dataslot_getfile('0),
        .target_dataslot_openfile('0),
        .target_dataslot_ack(),
        .target_dataslot_done(),
        .target_dataslot_err(),
        .target_dataslot_id('0),
        .target_dataslot_slotoffset('0),
        .target_dataslot_bridgeaddr('0),
        .target_dataslot_length('0),""", """        .target_dataslot_read(cd_target_read),
        .target_dataslot_write(1'b0),
        .target_dataslot_getfile(1'b0),
        .target_dataslot_openfile(1'b0),
        .target_dataslot_ack(),
        .target_dataslot_done(cd_target_done),
        .target_dataslot_err(cd_target_error),
        .target_dataslot_id(cd_target_id),
        .target_dataslot_slotoffset(cd_target_offset),
        .target_dataslot_bridgeaddr(cd_target_bridge),
        .target_dataslot_length(cd_target_length),""")
        once("""        .datatable_addr('0),
        .datatable_wren('0),
        .datatable_data('0),
        .datatable_q()""", """        .datatable_addr(cd_table_addr),
        .datatable_wren(1'b0),
        .datatable_data(32'b0),
        .datatable_q(cd_table_q)""")
        for decl in ('    reg hasCD=0;\n', '    reg LIDopen=0;\n', '    reg [31:0] trackinfo_data=0;\n', '    reg [8:0] trackinfo_addr=0;\n',
                     '    reg trackinfo_write=0;\n', '    reg cd_hps_ack=0;\n', '    reg cd_hps_write=0;\n', '    reg [15:0] cd_hps_data=0;\n'):
            once(decl, decl.replace('reg ', 'wire ').replace('=0;', ';'))
        for name in ('hasCD', 'LIDopen', 'trackinfo_data', 'trackinfo_addr', 'trackinfo_write', 'cd_hps_ack', 'cd_hps_write', 'cd_hps_data'):
            text, n = re.subn(r"\n        if\(bridge_wr && bridge_addr==32'h[0-9a-f]+\) " + name + r"<=bridge_wr_data\[[0-9:]+\];", '', text)
            if n != 1: parser.error('Bridge register for ' + name + ' not found')
        once('    // APF scaler protocol:', """    // Discs from the SD card (psx_cd_stream): disc slot 0, read by APF
    // target commands into bridge windows at 0x24000000; lid closed.
    wire cd_slot_update, cd_target_read, cd_target_done, cd_host_busy, cd_read_error;
    wire cd_disc_change_done, cd_disc_pending, cd_stream_idle, cd_table_active;
    wire [15:0] cd_slot_update_id, cd_target_id; wire [31:0] cd_slot_update_size, cd_table_q;
    wire [31:0] cd_target_offset, cd_target_bridge, cd_target_length; wire [2:0] cd_target_error;
    wire [9:0] cd_table_addr; wire [7:0] cd_disc_generation; wire [19:0] cd_sector_count;
    wire cd_stream_reset=!power_reset_n || !cpu_locked;
    assign LIDopen=1'b0;
    psx_cd_stream disc_stream (.host_clk(clk_74a), .host_reset(cd_stream_reset), .bridge_wr(bridge_wr),
        .bridge_addr(bridge_addr), .bridge_data(bridge_wr_data), .allcomplete(dataslot_allcomplete),
        .slot_update(cd_slot_update), .slot_update_id(cd_slot_update_id), .slot_update_size(cd_slot_update_size),
        .table_active(cd_table_active), .table_grant(1'b1), .table_addr(cd_table_addr), .table_q(cd_table_q),
        .target_read(cd_target_read), .target_ready(1'b1), .target_id(cd_target_id), .target_offset(cd_target_offset),
        .target_bridge(cd_target_bridge), .target_length(cd_target_length), .target_done(cd_target_done),
        .target_error(cd_target_error), .host_busy(cd_host_busy), .read_error(cd_read_error),
        .clk(clk1x), .reset(cd_stream_reset), .host_region(2'd2), .disc_slot_id(16'h0), .disc_select_strobe(1'b0),
        .disc_change_done(cd_disc_change_done), .has_disc(hasCD), .disc_pending(cd_disc_pending),
        .cd_hps_req(cd_hps_req), .cd_hps_lba(cd_hps_lba), .cd_hps_ack(cd_hps_ack), .cd_hps_write(cd_hps_write),
        .cd_hps_data(cd_hps_data), .disc_generation(cd_disc_generation), .sector_count(cd_sector_count),
        .stream_idle(cd_stream_idle), .trackinfo_addr(trackinfo_addr), .trackinfo_data(trackinfo_data),
        .trackinfo_write(trackinfo_write));
    // APF scaler protocol:""")
        qsf_path = out/'apf_top.qsf'
        qsf_path.write_text(qsf_path.read_text()+f'set_global_assignment -name SYSTEMVERILOG_FILE "{Path(__file__).resolve().parents[1]}/rtl/pocket/psx_cd_stream.sv" -library work\n')
    if args.steady_interlace or args.blank_reset:
        block = re.search(r'    reg apf_hs_d=0, apf_vs_d=0, apf_hs=0, apf_vs=0, apf_de=0, apf_skip=0;\n    reg \[23:0\] apf_rgb=0;\n    always @\(posedge video_rgb_clock\) begin\n.*?        apf_rgb<=\(!hblank && !psx_vblank\) \? (.*?) : 24\'h0;\n    end\n', text, re.S)
        if not block: parser.error('APF video block not found')
        text = text[:block.start()] + f'''    wire apf_hs, apf_vs, apf_de, apf_skip; wire [23:0] apf_rgb;
    psx_apf_video #(.STEADY_INTERLACE({int(args.steady_interlace)})) apf_video (.clk(video_rgb_clock), .ce(video_ce),
        .hsync(hsync), .vsync(vsync), .hblank(hblank), .vblank(psx_vblank), .interlace(video_interlace), .blank(cpu_boot_reset),
        .rgb_in({block.group(1)}), .hs(apf_hs), .vs(apf_vs), .de(apf_de), .skip(apf_skip), .rgb(apf_rgb));
''' + text[block.end():]
        qsf = out/'apf_top.qsf'
        qsf.write_text(qsf.read_text()+f'set_global_assignment -name SYSTEMVERILOG_FILE "{Path(__file__).resolve().parents[1]}/rtl/pocket/psx_apf_video.sv" -library work\n')
    if args.no_field_hold:
        if text.count('psx_apf_video #(.STEADY_INTERLACE(1))') != 1: parser.error('--no-field-hold: no STEADY_INTERLACE(1) instance')
        text = text.replace('psx_apf_video #(.STEADY_INTERLACE(1))', 'psx_apf_video #(.STEADY_INTERLACE(0))')
    if args.sotn_memory:
        if args.psram_main or args.vram_async or args.sdram_clock_ps is not None or args.vram_credit_timing:
            parser.error('--sotn-memory replaces the memory fabric: no --psram-main/--vram-async/--sdram-clock-ps/--vram-credit-timing')
        root = Path(__file__).resolve().parents[1]
        sotn = root/'references/pocket_sotn'
        start = text.index('    psx_mister_main_adapter main_commands (')
        end_anchor = '        .dram_dq(dram_dq),.dram_clk(dram_clk));\n'
        end = text.index(end_anchor, start) + len(end_anchor)
        if text.count('    psx_sdram_shared memory (') != 1 or not (start < text.index('    psx_sdram_shared memory (') < end):
            parser.error('--sotn-memory: memory fabric block not found')
        glue_block = SOTN_GLUE
        if args.vram_ctrl == 'clean':
            # Park the PSRAMs (clock stopped, power-on BCR) while APF holds the
            # core in reset, so the next core finds them asynchronous; the
            # card slot's aux accesses still work while parked.
            glue_block = glue_block.replace(VRAM_HEAD, "    wire vram_park=!reset_n;   // synchronised inside psx_vram_psram\n" + VRAM_HEAD_CLEAN)
        text = text[:start] + glue_block + text[end:]
        cram_idle = [f"    assign cram{i}_a=0,cram{i}_dq=16'bz,cram{i}_clk=0,\n        cram{i}_adv_n=1,cram{i}_cre=0,cram{i}_ce0_n=1,cram{i}_ce1_n=1,\n"
                     f"        cram{i}_oe_n=1,cram{i}_we_n=1,cram{i}_ub_n=1,cram{i}_lb_n=1;\n" for i in (0, 1)]
        cdc = '.src_clk(clk_74a), .dst_clk(mem_clk), .reset_async(!power_reset_n || mem_reset),'
        for o in cram_idle + [cdc]:
            if text.count(o) != 1: parser.error('--sotn-memory anchor not found: ' + o[:60])
            text = text.replace(o, '' if o in cram_idle else
                                '.src_clk(clk_74a), .dst_clk(clk1x), .reset_async(!power_reset_n || !cpu_locked),')
        qsf = out/'apf_top.qsf'; q = qsf.read_text()
        old_sdram = [l for l in q.splitlines() if l.endswith('/sdram.sv" -library work')]
        if len(old_sdram) != 1: parser.error('--sotn-memory: expected one sdram.sv in the project')
        if args.sdram_cas != 2 and args.sdram_ctrl != 'clean': parser.error('--sdram-cas needs --sdram-ctrl clean')
        if args.sdram_spu_first and args.sdram_ctrl != 'clean': parser.error('--sdram-spu-first needs --sdram-ctrl clean')
        if args.spu_cache and args.sdram_ctrl != 'clean': parser.error('--spu-cache needs --sdram-ctrl clean')
        if args.sdram_ctrl == 'clean':
            # MIT clean-room controller (rtl/memory/psx_sdram_ctrl.sv), same
            # ports as sdram.sv: a project copy of the glue instantiates it.
            controller = f'{root}/rtl/memory/psx_sdram_ctrl.sv'
            glue_text = (root/'rtl/pocket/psx_native_memory.sv').read_text()
            if glue_text.count('    sdram #(.SINGLE_CHIP(1)) controller (') != 1: parser.error('--sdram-ctrl clean: glue instance not found')
            glue = out/'psx_native_memory.sv'
            glue_text = glue_text.replace('    sdram #(.SINGLE_CHIP(1)) controller (',
                f'    psx_sdram_ctrl #(.SINGLE_CHIP(1), .CAS_LATENCY({args.sdram_cas}){", .SPU_FIRST(1)" if args.sdram_spu_first else ""}) controller (')
            if args.spu_cache:
                if glue_text.count(SPU_CH3_DIRECT) != 1: parser.error('--spu-cache: SPU ch3 wiring not found')
                glue_text = glue_text.replace(SPU_CH3_DIRECT, SPU_CH3_CACHE)
                cache_source = root/'rtl/pocket/psx_spu_ch3_cache.sv'
                if args.spu_cache_slim:
                    # Same module name and file name, so audits find one psx_spu_ch3_cache.sv.
                    cache_source = out/'spu-cache-slim'/'psx_spu_ch3_cache.sv'; cache_source.parent.mkdir(exist_ok=True)
                    shutil.copyfile(root/'rtl/pocket/psx_spu_ch3_cache_slim.sv', cache_source)
                q += f'set_global_assignment -name SYSTEMVERILOG_FILE "{cache_source}" -library work\n'
            glue.write_text(glue_text)
        else:
            controller, glue = f'{sotn}/rtl/sdram.sv', root/'rtl/pocket/psx_native_memory.sv'
        q = q.replace(old_sdram[0], f'set_global_assignment -name SYSTEMVERILOG_FILE "{controller}" -library work')
        vram_source = (f'set_global_assignment -name SYSTEMVERILOG_FILE "{root}/rtl/memory/psx_vram_psram.sv" -library work\n'
                       if args.vram_ctrl == 'clean' else
                       f'set_global_assignment -name VHDL_FILE "{sotn}/pocket/src/fpga/core/vram_psram.vhd" -library work\n')
        q += f'set_global_assignment -name SYSTEMVERILOG_FILE "{glue}" -library work\n' + vram_source
        qsf.write_text(q)
        # The MIRLO SDRAM pin constraints name the memory PLL; sdram.sv drives
        # the pins from clk3x (pocket_sotn has no pin constraints either).
        sdc = out/'pocket.sdc'
        sdc.write_text(''.join(l for l in sdc.read_text().splitlines(True) if 'pocket_sdram' not in l)
            # shared_initialized reaches clk_74a only through the mem_ready74
            # synchronizer and the diagnostic status readback (quasi-static).
            + 'set_false_path -from [get_registers {*|native_init_count[*]}] -to [get_registers {*|mem_ready74[0] *io_bridge_peripheral:*|spis_word_tx[*]}]\n'
            # --vram-ctrl clean: park (APF reset_n, clk_74a) enters clk2x
            # through the two-flop park_m -> park_s synchroniser.
            + ('set_false_path -to [get_registers {*|psx_vram_psram:vram|park_m}]\n' if args.vram_ctrl == 'clean' else ''))
    if args.memcard_slot:
        # Memory card 1 as pocket_sotn keeps it: the image lives in vram_psram
        # region 1 (the card's DDR3 window), APF writes the save file there
        # through pocket_memcard while the core is held in reset and reads it
        # back through the same block when the core quits. MiSTer's HPS
        # mount/load/save registers stay idle and the card is always present.
        if not args.sotn_memory: parser.error('--memcard-slot needs --sotn-memory (vram_psram aux port)')
        if args.stub_memcards: parser.error('--memcard-slot needs the memory card devices')
        def once_card(o, n):
            nonlocal text
            if text.count(o) != 1: parser.error('--memcard-slot anchor not found: ' + o.strip()[:70])
            text = text.replace(o, n)
        once_card('        .aux_req(1\'b0),.aux_we(1\'b0),.aux_addr(21\'b0),.aux_din(32\'b0),.aux_dout(),.aux_done(),.card_write(),\n',
                  '        .aux_req(mc_aux_req),.aux_we(mc_aux_we),.aux_addr(mc_aux_addr),.aux_din(mc_aux_din),.aux_dout(mc_aux_dout),\n'
                  '        .aux_done(mc_aux_done),.card_write(mc_card_write),\n')
        slot = MEMCARD_SLOT
        if args.memcard_ctrl == 'clean' and not args.memcard_format:
            parser.error('--memcard-ctrl clean always formats cards without the magic: add --memcard-format')
        if args.memcard_format:
            card_entity = 'psx_memcard_slot' if args.memcard_ctrl == 'clean' else 'pocket_memcard_format'
            slot = (slot.replace('    pocket_memcard memcard_slot (', f'    wire memcard_load_done;\n    {card_entity} memcard_slot (')
                    .replace('.card_write(mc_card_write));', '.card_write(mc_card_write),.load_done(memcard_load_done));'))
        head = VRAM_HEAD_CLEAN if args.vram_ctrl == 'clean' else VRAM_HEAD
        once_card(head, slot + head)
        once_card('    reg memcard1_available=0;\n', "    wire memcard1_available=1'b1;   // --memcard-slot: card 1 always present\n")
        once_card("        if(bridge_wr && bridge_addr==32'h11000108) memcard1_available<=bridge_wr_data[0:0];\n", '')
        mux = "        if (bridge_addr[31:24]==8'hf8) bridge_rd_data=cmd_bridge_rd_data;"
        once_card(mux, mux + "\n        if (bridge_addr[31:17]==15'h1000) bridge_rd_data=memcard_rd_data;   // card slot 0x20000000")
        root = Path(__file__).resolve().parents[1]
        if args.memcard_ctrl == 'clean':
            entity, source, kind = 'psx_memcard_slot', root/'rtl/pocket/psx_memcard_slot.sv', 'SYSTEMVERILOG_FILE'
        else:
            entity = 'pocket_memcard_format' if args.memcard_format else 'pocket_memcard'
            source = root/'rtl/pocket/pocket_memcard_format.vhd' if args.memcard_format else root/'references/pocket_sotn/pocket/src/fpga/core/pocket_memcard.vhd'
            kind = 'VHDL_FILE'
        q = (out/'apf_top.qsf').read_text()
        (out/'apf_top.qsf').write_text(q + f'set_global_assignment -name {kind} "{source}" -library work\n'
            # Keep its synchronizers in flip-flops: Quartus otherwise merges
            # rd_tog_s into a RAM shift register (with the video request
            # sync), where neither the constraint below nor metastability
            # settling applies.
            f'set_instance_assignment -name AUTO_SHIFT_REGISTER_RECOGNITION OFF -to "core_top:ic|{entity}:memcard_slot"\n')
        sdc = out/'pocket.sdc'
        if args.memcard_ctrl == 'clean':
            # psx_memcard_slot's crossing list (its header): two-flop
            # synchroniser first stages, and bundled words that are held from
            # before their toggle until the far side acknowledges, so they need
            # a bounded flight time but no same-edge hold relation.
            card = '*|psx_memcard_slot:memcard_slot|'
            vram = '*|psx_vram_psram:vram|*' if args.vram_ctrl == 'clean' else '*|vram_psram:vram|*'
            # Only the clk2x-side destinations: same-domain clk_74a paths keep
            # their ordinary setup/hold checks.
            # Every clk2x register of the slot that reads the held request
            # words (they are used only two synchroniser edges after the
            # toggle, or while the request is still outstanding).
            dest_2x = ' '.join([card + r for r in ('aux_req', 'busy', 'checked', 'fmt_active', 'fmt_idx[*]', 'own',
                                                   'ready_seen', 'req_seen_2x', 'take_tgl_2x', 'w0_magic', 'w0_seen')] + [vram])
            bundle_74 = ' '.join(card + r for r in ('req_we_74', 'req_addr_74[*]', 'req_data_74[*]', 'ready_tgl_74*'))
            sdc.write_text(sdc.read_text() + '\n# psx_memcard_slot: synchroniser first stages and held request/response words.\n'
                'set_false_path -to [get_registers {' + ' '.join(card + r for r in
                    ('s2x_req[0]', 's2x_ready[0]', 's2x_rack[0]', 's74_rst[0]', 's74_take[0]')) + ' *|mc_idle74[0]}]\n'
                'set_false_path -from [get_registers {*|boot_ready_card}] -to [get_registers {*boot_gate*|reset_pipe[*]}]\n'
                f'set_max_delay 13 -from [get_registers {{{bundle_74}}}] -to [get_registers {{{dest_2x}}}]\n'
                f'set_min_delay -13 -from [get_registers {{{bundle_74}}}] -to [get_registers {{{dest_2x}}}]\n'
                # A read answer is the VRAM controller's held aux_dout, loaded
                # one clk2x before take_tgl_2x toggles.
                f'set_max_delay 13 -from [get_registers {{{vram}}}] -to [get_registers {{{card}bridge_rd_data[*]}}]\n'
                f'set_min_delay -13 -from [get_registers {{{vram}}}] -to [get_registers {{{card}bridge_rd_data[*]}}]\n'
                # load_done is a clk_74a level synchronised by s2x_ready.
                )
        else:
          sdc.write_text(sdc.read_text() + ('\n# pocket_memcard: toggle synchronizers and held request/response words.\n'
            'set_false_path -to [get_registers {*|pocket_memcard:*|rd_tog_s[0] *|pocket_memcard:*|rd_ack_s[0] *|mc_idle74[0]}]\n'
            # boot_ready_card (a clk_74a register) only asserts the boot gate's
            # reset synchronizer asynchronously; its release is synchronized.
            'set_false_path -from [get_registers {*|boot_ready_card}] -to [get_registers {*boot_gate*|reset_pipe[*]}]\n'
            'set_max_delay 13 -from [get_registers {*|pocket_memcard:*|rd_word[*] *|pocket_memcard:*|rd_data[*]}]\n'
            'set_min_delay 0 -from [get_registers {*|pocket_memcard:*|rd_word[*] *|pocket_memcard:*|rd_data[*]}]\n'
            # --memcard-format: the boot gate's ready level enters clk2x through done_s.
            + ('set_false_path -to [get_registers {*|pocket_memcard:*|done_s[0]}]\n' if args.memcard_format else ''))
            .replace('pocket_memcard:', entity + ':'))
    if args.static_host_cuts:
        # gpu_videoout_async's _s1/_s2 synchronizers stay in flip-flops:
        # Quartus otherwise maps videoout_request_s1 into an altshift_taps
        # RAM shift register, which is no synchronizer.
        q = (out/'apf_top.qsf').read_text()
        (out/'apf_top.qsf').write_text(q + 'set_instance_assignment -name AUTO_SHIFT_REGISTER_RECOGNITION OFF -to '
            '"core_top:ic|psx_mister_psx_top_resource:psx|psx_top:component_core|gpu:igpu|gpu_videoout:igpu_videoout|gpu_videoout_async:igpu_videoout_async"\n')
        sdc = out/'pocket.sdc'
        sdc.write_text(sdc.read_text() + '''
# --static-host-cuts. pause (0x11000018) and isPal (0x11000154) are written
# only by the MiSTer-style host service, which the Pocket never drives: both
# stay at their reset value. cd_top's HPS request/LBA/region/reset outputs
# and pausing reach the APF bridge only through the diagnostic service
# readback mux (0x12000xxx), which host tools poll and the core never reads.
set_false_path -from [get_registers {*core_top:ic|pause}] -to [get_registers {*|psx_top:component_core|pausing *|psx_top:component_core|pausing~DUPLICATE* *|psx_top:component_core|pausingSS *|psx_top:component_core|pausingSS~DUPLICATE* *|psx_top:component_core|ce *|psx_top:component_core|ce~DUPLICATE* *|psx_top:component_core|cpuPaused *|psx_top:component_core|cpuPaused~DUPLICATE*}]
set_false_path -from [get_registers {*core_top:ic|isPal}] -to [get_registers {*|gpu:igpu|GPUSTAT_PalVideoMode *|gpu:igpu|GPUSTAT_PalVideoMode~DUPLICATE*}]
# The bridge read word: io_bridge_peripheral latches pmp_addr with the last
# address byte and loads spis_word_tx from the read mux four clk_74a edges
# later (ST_READ_0, read_cnt 0..3), with pmp_addr unchanged in between.
set_multicycle_path -setup 4 -from [get_registers {*io_bridge_peripheral:ibs|pmp_addr[*] *io_bridge_peripheral:ibs|pmp_addr[*]~DUPLICATE*}] -to [get_registers {*io_bridge_peripheral:ibs|spis_word_tx[*]}]
set_multicycle_path -hold 3 -from [get_registers {*io_bridge_peripheral:ibs|pmp_addr[*] *io_bridge_peripheral:ibs|pmp_addr[*]~DUPLICATE*}] -to [get_registers {*io_bridge_peripheral:ibs|spis_word_tx[*]}]
set_false_path -from [get_registers {*|cd_top:icd_top|cd_hps_lba[*] *|cd_top:icd_top|cd_hps_lba[*]~DUPLICATE* *|cd_top:icd_top|cd_hps_req* *|cd_top:icd_top|region_out[*] *|cd_top:icd_top|region_out[*]~DUPLICATE* *|cd_top:icd_top|resetFromCD* *|psx_top:component_core|pausing*}] -to [get_registers {*io_bridge_peripheral:ibs|spis_word_tx[*]}]
''')
    if args.psram_main:
        root = Path(__file__).resolve().parents[1]
        clocks = out/'psx_mem_clocks.sv'; c = clocks.read_text()
        for o, n in () if args.psram_sync else (('.number_of_clocks(3),', '.number_of_clocks(5),'),
                     ('output wire clk_sys, clk_mem, clk_dram, locked', 'output wire clk_sys, clk_mem, clk_dram, clk_psram, clk_psram_chip, locked'),
                     ('.output_clock_frequency2("125.000000 MHz"), .phase_shift2("4000 ps"), .duty_cycle2(50),',
                      '.output_clock_frequency2("125.000000 MHz"), .phase_shift2("4000 ps"), .duty_cycle2(50),\n'
                      '        // PSRAM controller and free-running chip clock (PocketQuake recipe: 105 MHz, 5714 ps).\n'
                      f'        .output_clock_frequency3("{PSRAM_CLOCKS[args.psram_mhz][0]} MHz"), .phase_shift3("0 ps"), .duty_cycle3(50),\n'
                      f'        .output_clock_frequency4("{PSRAM_CLOCKS[args.psram_mhz][0]} MHz"), .phase_shift4("{args.psram_phase or PSRAM_CLOCKS[args.psram_mhz][1]} ps"), .duty_cycle4(50),'),
                     ('.outclk({clk_dram, clk_mem, clk_sys})', '.outclk({clk_psram_chip, clk_psram, clk_dram, clk_mem, clk_sys})')):
            if c.count(o) != 1: parser.error('--psram-main: memory clock anchor not found: ' + o)
            c = c.replace(o, n)
        clocks.write_text(c)
        def once_text(o, n):
            nonlocal text
            if text.count(o) != 1: parser.error('--psram-main: anchor not found: ' + o[:80])
            text = text.replace(o, n)
        once_text('    wire mem_clk, mem_reset;\n', '    wire mem_clk, mem_reset, psram_clk, psram_chip_clk, psram_initialized, psram_idle, psram_clk_en;\n'
                  '    wire [7:0] psram_debug; wire [3:0] main_debug; wire ps_main_fill; wire [31:0] ps_fill_data;\n')
        if args.psram_sync:
            # CPU-synchronous PSRAM: the controller runs on clk3x and the chip
            # clock is a phased fourth output of the CPU PLL (same VCO), so the
            # main adapter needs no crossing (psx_mister_main_sync).
            pll = re.search(r'altera_pll #\(.*?\) cpu_pll \(', text, re.S)
            if not pll or pll.group().count('.number_of_clocks(3)') != 1 or pll.group().count('.output_clock_frequency2("101.606400 MHz"), .phase_shift2("0 ps"), .duty_cycle2(50))') != 1:
                parser.error('--psram-sync: cpu_pll anchor not found')
            block = pll.group().replace('.number_of_clocks(3)', '.number_of_clocks(4)').replace(
                '.output_clock_frequency2("101.606400 MHz"), .phase_shift2("0 ps"), .duty_cycle2(50))',
                '.output_clock_frequency2("101.606400 MHz"), .phase_shift2("0 ps"), .duty_cycle2(50),\n'
                f'        .output_clock_frequency3("101.606400 MHz"), .phase_shift3("{args.psram_phase or 8000} ps"), .duty_cycle3(50))')
            text = text[:pll.start()] + block + text[pll.end():]
            once_text('.outclk({clk3x,clk2x,clk1x}));', '.outclk({psram_chip_clk,clk3x,clk2x,clk1x}));\n    assign psram_clk=clk3x;')
        else:
            once_text('.clk_sys(mem_clk),.clk_mem(mem_fast_clk),.clk_dram(mem_forwarded_clk),.locked(memory_locked));',
                      '.clk_sys(mem_clk),.clk_mem(mem_fast_clk),.clk_dram(mem_forwarded_clk),\n'
                      '        .clk_psram(psram_clk),.clk_psram_chip(psram_chip_clk),.locked(memory_locked));')
        mask = [str(c) in args.psram_clients for c in range(3)]
        if mask[0]: once_text('.clk1x(clk1x),.clk3x(clk3x),.mem_clk(mem_clk),', '.clk1x(clk1x),.clk3x(clk3x),.mem_clk(psram_clk),')
        if args.psram_sync:
            if not mask[0]: parser.error('--psram-sync needs main RAM (client 0) on PSRAM')
            once_text('    psx_mister_main_adapter main_commands (', '    psx_mister_main_sync #(.STREAM_CACHE(1),.POST_WRITES(1)) main_commands (\n'
                      '        .debug(main_debug),.p_fill_valid(ps_main_fill),.p_fill_data(ps_fill_data),')
            qsf = out/'apf_top.qsf'
            qsf.write_text(qsf.read_text()+f'set_global_assignment -name SYSTEMVERILOG_FILE "{root}/rtl/pocket/psx_mister_main_sync.sv" -library work\n')
        if mask[1]: once_text('        .clk1x(clk1x),.mem_clk(mem_clk),.reset_async(memory_transport_reset),\n        .core_ce(memory_service_ce),.discard_returns(reset),\n        .request(spuram_ena)',
                  '        .clk1x(clk1x),.mem_clk(psram_clk),.reset_async(memory_transport_reset),\n        .core_ce(memory_service_ce),.discard_returns(reset),\n        .request(spuram_ena)')
        if mask[2]: once_text('.src_clk(clk_74a), .dst_clk(mem_clk), .reset_async(!power_reset_n || mem_reset),',
                  '.src_clk(clk_74a), .dst_clk(psram_clk), .reset_async(!power_reset_n || mem_reset),')
        once_text('    assign mem_initialized=shared_initialized;', '    assign mem_initialized=shared_initialized && psram_initialized;')
        start = text.index('    genvar client;\n')
        end = text.index('    end endgenerate\n', start) + len('    end endgenerate\n')
        # Clients outside the mask keep their SDRAM fronts, renamed onto sd_*.
        gen = text[start:end]
        for o, n in (('assign packet_ready[client]=front_ready && shared_initialized;', 'assign sd_ready[client]=front_ready && shared_initialized;'),
                     ('.req_valid(packet_valid[client] && shared_initialized)', '.req_valid(packet_valid[client] && shared_initialized && !PSRAM_MASK[client])'),
                     ('.rsp_valid(packet_return[client])', '.rsp_valid(sd_return[client])'),
                     ('.rsp_rdata(packet_read[client])', '.rsp_rdata(sd_read[client])'),
                     ('.rsp_error(packet_error[client])', '.rsp_error(sd_error[client])'),
                     ('.fill_valid(scalar_fill_valid),.fill_word(scalar_fill_word),.fill_data(scalar_fill_data)', '.fill_valid(sd_fill_valid),.fill_word(sd_fill_word),.fill_data(sd_fill_data)')):
            gen = gen.replace(o, n)
        mask_bits = "3'b" + ''.join('1' if mask[c] else '0' for c in (2, 1, 0))
        text = text[:start] + gen.replace('PSRAM_MASK', 'psram_mask') + f"""    localparam [2:0] psram_mask={mask_bits};
""" + """    // Main RAM + BIOS (0), SPU RAM (1) and the BIOS loader (2) on the paired
    // PSRAM chips; the SDRAM serves VRAM alone (client 3).
    (* async_reg="true" *) reg [2:0] psram_reset_pipe=3'b111;
    always @(posedge psram_clk or posedge memory_transport_reset)
        if (memory_transport_reset) psram_reset_pipe<=3'b111; else psram_reset_pipe<={psram_reset_pipe[1:0],1'b0};
    wire [2:0] psram_ready, ps_return, ps_error, sd_ready, sd_return, sd_error;
    wire [127:0] ps_read; wire [2:0][127:0] sd_read;
    wire ps_fill_valid, sd_fill_valid; wire [1:0] ps_fill_word, sd_fill_word; wire [31:0] sd_fill_data;
    genvar pc;
    generate for (pc=0;pc<3;pc=pc+1) begin: psram_route
        assign packet_ready[pc]=psram_mask[pc] ? psram_ready[pc] && psram_initialized : sd_ready[pc];
        assign packet_return[pc]=psram_mask[pc] ? ps_return[pc] : sd_return[pc];
        assign packet_error[pc]=psram_mask[pc] ? ps_error[pc] : sd_error[pc];
        assign packet_read[pc]=psram_mask[pc] ? ps_read : sd_read[pc];
    end endgenerate
    psx_psram_clients #(.PAIRED(PAIRED_FLAG), .CLOCK_MHZ(PSRAM_MHZ)TIMING_FLAG) psram_clients (.clk(psram_clk), .reset(psram_reset_pipe[2]), .initialized(psram_initialized), .idle(psram_idle),
        .p_valid(packet_valid & psram_mask & {3{psram_initialized}}), .p_write(packet_write), .p_ready(psram_ready), .p_addr(packet_addr),
        .p_halfwords(packet_count), .p_wdata(packet_data), .p_byte_en(packet_mask),
        .r_valid(ps_return), .r_error(ps_error), .r_ready(packet_return_ready & psram_mask), .r_rdata(ps_read),
        .fill_valid(ps_fill_valid), .main_fill_valid(ps_main_fill), .fill_word(ps_fill_word), .fill_data(ps_fill_data),
        .cram0_a(cram0_a), .cram1_a(cram1_a), .cram0_dq(cram0_dq), .cram1_dq(cram1_dq),
        .cram0_wait(cram0_wait), .cram1_wait(cram1_wait), .cram0_adv_n(cram0_adv_n), .cram1_adv_n(cram1_adv_n),
        .cram0_cre(cram0_cre), .cram1_cre(cram1_cre), .cram0_ce0_n(cram0_ce0_n), .cram0_ce1_n(cram0_ce1_n),
        .cram1_ce0_n(cram1_ce0_n), .cram1_ce1_n(cram1_ce1_n), .cram0_oe_n(cram0_oe_n), .cram1_oe_n(cram1_oe_n),
        .cram0_we_n(cram0_we_n), .cram1_we_n(cram1_we_n), .cram0_ub_n(cram0_ub_n), .cram0_lb_n(cram0_lb_n),
        .cram1_ub_n(cram1_ub_n), .cram1_lb_n(cram1_lb_n), .chip_clk_en(psram_clk_en), .debug(psram_debug));
    assign scalar_fill_valid=psram_mask[1] ? ps_fill_valid : sd_fill_valid;
    assign scalar_fill_word=psram_mask[1] ? ps_fill_word : sd_fill_word;
    assign scalar_fill_data=psram_mask[1] ? ps_fill_data : sd_fill_data;
CHIP_CLOCK_PINS""" + text[end:]
        text = text.replace('CHIP_CLOCK_PINS', '    assign cram0_clk=psram_chip_clk, cram1_clk=psram_chip_clk;\n' if args.psram_free_clock else
            '''    // The chip clock pulses only during burst reads: asynchronous writes in
    // synchronous mode need CLK held LOW (else they start burst writes).
    altddio_out #(.width(2), .intended_device_family("Cyclone V"), .lpm_type("altddio_out"), .power_up_high("OFF"),
        .extend_oe_disable("OFF"), .invert_output("OFF"), .oe_reg("UNREGISTERED")) psram_chip_ddio (
        .datain_h({2{psram_clk_en}}), .datain_l(2'b0), .outclock(psram_chip_clk), .dataout({cram1_clk, cram0_clk}),
        .aclr(1'b0), .aset(1'b0), .oe(1'b1), .outclocken(1'b1), .sclr(1'b0), .sset(1'b0));
''')

        text = text.replace('PAIRED_FLAG', '0' if args.psram_single else '1').replace('PSRAM_MHZ', '101.6064' if args.psram_sync else PSRAM_CLOCKS[args.psram_mhz][0])
        text = text.replace('TIMING_FLAG', f', .WRITE_EXTRA({args.psram_write_extra}), .WRITE_GAP({args.psram_write_gap})')
        if 'wire scalar_fill_valid' not in text:   # older tops: SPU reads use the response data
            text = text.replace('''    assign scalar_fill_valid=psram_mask[1] ? ps_fill_valid : sd_fill_valid;
    assign scalar_fill_word=psram_mask[1] ? ps_fill_word : sd_fill_word;
    assign scalar_fill_data=psram_mask[1] ? ps_fill_data : sd_fill_data;
''', '')
        pins = re.search(r'    assign cram0_a=0,cram0_dq=16.bz,cram0_clk=0,\n.*?cram1_lb_n=1;\n', text, re.S)
        if not pins: parser.error('--psram-main: PSRAM pin tie-offs not found')
        text = text[:pins.start()] + text[pins.end():]
        # The BIOS loader commits one word per packet; through the PSRAM's
        # slower writes and the clock crossing that falls behind the APF
        # bridge and overflows its FIFO. Commit up to four words a packet.
        loaders = [Path(m) for m in re.findall(r'"([^"]*psx_bios_loader\.sv)"', (out/'apf_top.qsf').read_text())]
        if len(loaders) != 1: parser.error('--psram-main: BIOS loader not found')
        lt = loaders[0].read_text()
        for o, n in (
            ('    reg [16:0] address=0;\n    reg [31:0] data=0;\n',
             '    reg [16:0] address=0;\n    reg [127:0] data=0;\n    reg [2:0] gathered=0;\n'),
            ("    assign req_addr={7'b0,address,1'b0}; // normalized halfword address\n    assign req_data={96'b0,data};\n    assign req_be=16'h000f;\n",
             "    assign req_addr={7'b0,address,1'b0}; // normalized halfword address\n    assign req_data=data;\n"
             "    assign req_be=gathered==3'd4 ? 16'hffff : gathered==3'd3 ? 16'h0fff : gathered==3'd2 ? 16'h00ff : 16'h000f;\n"
             "    assign req_halfwords={gathered,1'b0};\n"),
            ('                MEM_IDLE: if(!fifo_empty && !pop) begin\n                    address<=committed_words[16:0];data<=fifo_q;pop<=1;mem_state<=MEM_ISSUE;\n                end\n',
             '                // Gather up to four consecutive words (each pop settles a cycle).\n'
             '                MEM_IDLE: if(!pop) begin\n'
             '                    if(!fifo_empty && gathered!=3\'d4) begin\n'
             '                        if(gathered==0) address<=committed_words[16:0];\n'
             '                        data[gathered*32+:32]<=fifo_q;gathered<=gathered+1\'b1;pop<=1;\n'
             '                    end else if(gathered!=0) mem_state<=MEM_ISSUE;\n'
             '                end\n'),
            ("                    committed_words<=committed_words+1'b1;mem_state<=MEM_IDLE;\n",
             "                    committed_words<=committed_words+gathered;gathered<=0;data<=0;mem_state<=MEM_IDLE;\n"),
            ("            received_words<=0;committed_words<=0;ready<=0;failed<=0;\n",
             "            received_words<=0;committed_words<=0;ready<=0;failed<=0;gathered<=0;\n"),
            ('            if(complete_seen && fifo_empty && !pop && mem_state==MEM_IDLE &&\n',
             '            if(complete_seen && fifo_empty && !pop && mem_state==MEM_IDLE && gathered==0 &&\n'),
            ('    output wire [24:0] req_addr, output wire [127:0] req_data,\n',
             '    output wire [24:0] req_addr, output wire [127:0] req_data, output wire [3:0] req_halfwords,\n')):
            if lt.count(o) != 1: parser.error('--psram-main: BIOS loader anchor not found: ' + o[:60])
            lt = lt.replace(o, n)
        batched = out/'psx_bios_loader_batched.sv'; batched.write_text(lt)
        qsf = out/'apf_top.qsf'; qsf.write_text(qsf.read_text().replace(f'"{loaders[0]}"', f'"{batched}"'))
        once_text('.req_data(load_data),.req_be(load_be),', '.req_data(load_data),.req_be(load_be),.req_halfwords(load_halfwords),')
        once_text('    wire [15:0] load_be;\n', '    wire [15:0] load_be;\n    wire [3:0] load_halfwords;\n')
        once_text(".s_halfwords(4'd2), .s_wdata(load_data),", ".s_halfwords(load_halfwords), .s_wdata(load_data),")
        # MIRLO64's controller is also called psram: rename PocketQuake's copy.
        from pq_psram import renamed_psram
        (out/'psx_pq_psram.sv').write_text(renamed_psram(hold=args.psram_write_hold))
        qsf = out/'apf_top.qsf'
        qsf.write_text(qsf.read_text() + ''.join(f'set_global_assignment -name SYSTEMVERILOG_FILE "{f}" -library work\n' for f in (
            out/'psx_pq_psram.sv', root/'rtl/pocket/psx_psram_pair.sv', root/'rtl/pocket/psx_psram_clients.sv')))
        # psram_initialized reaches clk_74a only through mem_ready74[0], whose
        # first stage is already a false-path endpoint; no wider exception.
        # PocketQuake's PSRAM I/O constraints (ap_core.qsf, core_constraints.sdc,
        # as in the passing quake probe) for both chips: IOB input capture and
        # chip-clock-relative pin delays with the 3/2 capture multicycle.
        qsf = out/'apf_top.qsf'; sdc = out/'pocket.sdc'
        qsf.write_text(qsf.read_text() + ''.join(
            f'set_instance_assignment -name FAST_INPUT_REGISTER ON -to {c}_{s}\n'
            for c in ('cram0', 'cram1') for s in [f'dq[{i}]' for i in range(16)] + ['wait']))
        lines = ['set psram_chip {ic|cpu_pll|general[3].gpll~PLL_OUTPUT_COUNTER|divclk}' if args.psram_sync else
                 'set psram_chip {ic|memory_clocks|pll|general[4].gpll~PLL_OUTPUT_COUNTER|divclk}']
        for c, n in (('cram0', 'chip0'), ('cram1', 'chip1')):
            outs = f'{{{c}_a[*] {c}_dq[*] {c}_adv_n {c}_cre {c}_ce0_n {c}_ce1_n {c}_oe_n {c}_we_n {c}_ub_n {c}_lb_n}}'
            ins = f'{{{c}_dq[*] {c}_wait}}'
            cap = f'{{*|pair|{n}|cram_dq_r[*] *|pair|{n}|cram_wait_r}}'
            lines += [f'create_generated_clock -name {c}_clk_out -source [get_pins $psram_chip] [get_ports {{{c}_clk}}]',
                      f'set_output_delay -clock {c}_clk_out -max 3.0 -add_delay [get_ports {outs}]',
                      f'set_output_delay -clock {c}_clk_out -min -0.5 -add_delay [get_ports {outs}]',
                      f'set_input_delay -clock {c}_clk_out -max 6.5 -add_delay [get_ports {ins}]',
                      f'set_input_delay -clock {c}_clk_out -min 1.0 -add_delay [get_ports {ins}]',
                      f'set_multicycle_path -setup 3 -from [get_clocks {c}_clk_out] -to [get_registers {cap}]',
                      f'set_multicycle_path -hold 2 -from [get_clocks {c}_clk_out] -to [get_registers {cap}]']
        sdc.write_text(sdc.read_text() + '\n'.join(lines) + '\n')
    if args.perf_log:
        if not args.cd_stream or args.boot_trace: parser.error('--perf-log needs --cd-stream and no --boot-trace')
        inst = '    psx_apf_video #('
        if text.count(inst) != 1: parser.error('--perf-log: APF video instance not found')
        text = text.replace(inst, '''    // Per-frame memory-wait counters (psx_perf_log), saved from 0x40000000.
    wire [31:0] perf_data;
    psx_perf_log PERF_PARAMSperf_log (.clk1x(clk1x), .clk2x(clk2x), .clk3x(PERF_CLK3X), .stage(PERF_STAGE), .vsync(vsync),
        .ram_ena(ram_ena), .ram_done(ram_done), .spu_ena(spuram_ena), .spu_done(spuram_done), .sample(sound_out_valid),
        .vram_rd(ddr3_RD), .vram_we(ddr3_WE), .vram_busy(ddr3_BUSY), .vram_ready(ddr3_DOUT_READY),
        .status({passed, failed, checking, mem_ready74[1], PSRAM_INIT, shared_initialized, 8'b0, loaded_words}),
        .read_clk(clk_74a), .read_index(bridge_addr[14:2]), .read_data(perf_data));
''' + inst)
        mux = "        if (bridge_addr[31:24]==8'hf8) bridge_rd_data=cmd_bridge_rd_data;"
        if text.count(mux) != 1: parser.error('Bridge read mux not found')
        text = text.replace(mux, mux+"\n        if (bridge_addr[31:24]==8'h40) bridge_rd_data=perf_data;")
        # Nonvolatile slot 1's size word (data-table entry 3) is rewritten
        # whenever the disc streamer is not using the table port.
        for old_t, new_t in (('.datatable_addr(cd_table_addr),', '.datatable_addr(cd_table_active ? cd_table_addr : 10\'d3),'),
                             ('.datatable_wren(1\'b0),\n        .datatable_data(32\'b0),', '.datatable_wren(!cd_table_active),\n        .datatable_data(32\'d16400),'),
                             ('.table_grant(1\'b1)', '.table_grant(cd_table_grant)')):
            if text.count(old_t) != 1: parser.error('--perf-log: data-table wiring not found: ' + old_t)
            text = text.replace(old_t, new_t)
        text = text.replace('PSRAM_INIT', 'psram_initialized' if args.psram_main else "1'b1")
        # Sync PSRAM builds: per-stage clk3x counters (adapter busy, pair
        # reading, pair writing) in 8-word records, 511 frames.
        pair_state = 'psram_debug[7:4]'
        stages = args.psram_sync and args.perf_stages
        if args.perf_stages and not args.psram_sync: parser.error('--perf-stages needs --psram-sync')
        text = text.replace('PERF_PARAMS', '#(.FRAMES(511), .STAGES(1)) ' if stages else '')
        text = text.replace('PERF_CLK3X', 'clk3x' if stages else "1'b0")
        text = text.replace('PERF_STAGE', f"{{{pair_state}==4'd4 || {pair_state}==4'd5 || {pair_state}==4'd10, "
                            f"{pair_state}==4'd6 || {pair_state}==4'd7, main_debug[3:1]!=3'd0}}" if stages else "3'b0")
        if args.psram_main:
            # Stuck-state readout in the status word's spare byte: {main
            # adapter 3x state (sync adapter only), pair state, arbiter busy},
            # double-registered into the bridge clock (read when the log is saved).
            once_text("shared_initialized, 8'b0, loaded_words}", "shared_initialized, psram_debug_sync, loaded_words}")
            once_text('    psx_perf_log ', """    (* async_reg="true" *) reg [7:0] psram_debug_meta=0, psram_debug_sync=0;
    always @(posedge clk_74a) begin
        psram_debug_meta<={MAIN_STATE, psram_debug[7:4], psram_debug[3]};
        psram_debug_sync<=psram_debug_meta;
    end
    psx_perf_log """.replace('MAIN_STATE', 'main_debug[3:1]' if args.psram_sync else "3'b0"))
            sdc = out/'pocket.sdc'
            sdc.write_text(sdc.read_text() + 'set_false_path -to [get_registers {*|psram_debug_meta[*]}]\n')
        cd_inst = '    psx_cd_stream disc_stream ('
        if text.count(cd_inst) != 1: parser.error('--perf-log: disc streamer instance not found')
        text = text.replace(cd_inst, '''    reg cd_table_grant=0;
    always @(posedge clk_74a) cd_table_grant<=cd_table_active;
''' + cd_inst, 1)
        qsf = out/'apf_top.qsf'
        qsf.write_text(qsf.read_text()+f'set_global_assignment -name SYSTEMVERILOG_FILE "{Path(__file__).resolve().parents[1]}/rtl/pocket/psx_perf_log.sv" -library work\n')
        sdc = out/'pocket.sdc'
        sdc.write_text(sdc.read_text()+'set_false_path -from [get_registers {*|perf_log|recorded[*] *|perf_log|slot[*]}]\nset_false_path -from [get_registers {*|perf_log|ring*}] -to [get_registers {*|perf_log|q[*]}]\nset_false_path -from [get_registers {*|perf_log|ring_rtl*}]\n')
    if args.vram_cdc_constraints:
        if text.count('psx_vram_stream') != 1:
            parser.error('--vram-cdc-constraints needs one psx_vram_stream instance')
        sdc = out/'pocket.sdc'
        scope = (Path(__file__).resolve().parent/'pocket_vram_scope.tcl').read_text()
        saved = sdc.read_text()
        if re.search(r'^\s*set_false_path\s+-hold\s+-from\s+\[get_clocks', saved, re.M):
            parser.error('Source SDC already contains clock hold cuts; use a parent without them for Gray qualification')
        if 'proc pocket_vram_gray_source' in saved:
            if saved.count(scope) != 1:
                parser.error('Existing VRAM scope differs from the reviewed constraint helper')
        else:
            sdc.write_text(saved+'\n'+scope)
    if args.async_hold_cut:
        # Experimental: Lite also suppresses set_max_skew on these paths.
        # Retained setup/max-delay reports do not qualify Gray-bus skew.
        # Clocks within one PLL (including 1x/2x/3x and the chip clock) stay timed.
        families = ['ic|cpu_pll|*', 'ic|memory_clocks|*', 'ic|video_pll|*', 'ic|audio_pll|*', 'clk_74a', 'clk_74b', 'bridge_spiclk']
        sdc = out/'pocket.sdc'
        sdc.write_text(sdc.read_text()+''.join(f'set_false_path -hold -from [get_clocks {{{a}}}] -to [get_clocks {{{b}}}]\n'
                                               for a in families for b in families if a != b))
    if args.scaler_slots:
        found = re.findall(r'psx_apf_video #\(\.STEADY_INTERLACE\((\d)\)\)', text)
        if len(found) != 1: parser.error('--scaler-slots needs one psx_apf_video instance')
        extra = (', .INTERLACE_FEATURE(1)' if args.interlace_feature else '') + (', .SLOT480(1)' if args.slots480 else '') + (', .FIELD_INVERT(1)' if args.field_invert else '') + (', .PAD_LINES(1)' if args.pad_lines else '') + (', .VIDEO_PROBE(1)' if args.video_probe else '') + (', .VS_AT_VBLANK(1)' if args.vs_at_vblank else '') + (', .PAL288(1)' if args.pal288 else '') + (', .CENTER_LINES(1)' if args.center_lines else '') + (', .VIDEO_TEST(1)' if args.video_test_setting else '') + (', .LEAD_LINE(1)' if args.lead_line else '') + (', .SLOT6_368(1)' if args.slot6_368 else '') + (', .VS_ON_HS(1)' if args.vs_on_hs else '') + (', .CROP_LINES(1)' if args.crop_lines else '') + (f', .BLANK_TOP({args.blank_top})' if args.blank_top else '')
        text = re.sub(r'psx_apf_video #\(\.STEADY_INTERLACE\((\d)\)\)', r'psx_apf_video #(.STEADY_INTERLACE(\1), .SLOT_SELECT(1)' + extra + ')', text)
    elif args.interlace_feature or args.slots480: parser.error('--interlace-feature/--slots480 need --scaler-slots')
    if args.lead_line and (args.video_test_setting or args.vs_at_vblank or not args.scaler_slots):
        parser.error('--lead-line needs --scaler-slots, without --video-test-setting or --vs-at-vblank')
    if args.blank_top and (args.wide_lines or not args.scaler_slots):
        parser.error('--blank-top needs --scaler-slots, without --wide-lines')
    if args.crop_lines and (args.wide_lines or not args.scaler_slots):
        parser.error('--crop-lines needs --scaler-slots, without --wide-lines')
    if args.vs_on_hs and (args.vs_at_vblank or not args.scaler_slots):
        parser.error('--vs-on-hs needs --scaler-slots, without --vs-at-vblank')
    if args.slot6_368 and not (args.pal288 and args.slots480 and args.scaler_slots and not args.wide_lines):
        parser.error('--slot6-368 needs --scaler-slots --slots480 --pal288, without --wide-lines')
    if args.multi_track:
        # Multi-track discs (psx_cd_stream MAX_TRACKS): the PC Engine CD core's
        # Pocket convention, cue in slot 100 and track bins in 101, 102, ...
        # from an instance JSON (package_pocket_early.py --multi-track).
        if not args.cd_stream: parser.error('--multi-track needs --cd-stream')
        inst = '    psx_cd_stream disc_stream ('
        if text.count(inst) != 1: parser.error('--multi-track: disc streamer instance not found')
        text = text.replace(inst, f"    psx_cd_stream #(.DEFAULT_SLOT(16'd101), .MAX_TRACKS({args.multi_track}), .TRACK_STRIDE(16'd1)) disc_stream (", 1)
        if text.count(".disc_slot_id(16'h0)") != 1: parser.error('--multi-track: disc slot selection not found')
        text = text.replace(".disc_slot_id(16'h0)", ".disc_slot_id(16'd101)", 1)
    if args.shoulder_setting:
        # Menu lists pick what the Pocket's L and R press (L1/L2/L3 and
        # R1/R2/R3) and what Select+L/R and Select+Y/A press (none, L1/R1,
        # L2/R2, L3/R3). A docked pad's own L2/R2/L3/R3 pass through. Quasi-static levels, synchronized into
        # clk1x. APF list values can arrive in any byte of the write (as with
        # the Disc list), so the value is taken from whichever byte holds it.
        if not args.pad_combos: parser.error('--shoulder-setting needs --pad-combos')
        bits = {'L1': 8, 'R1': 9, 'L2': 10, 'R2': 11, 'L3': 12, 'R3': 13}
        pad = {k: f'    wire joypad1_Key{k}=pocket_pad_buttons[{b}];\n' for k, b in bits.items()}
        for line in pad.values():
            if text.count(line) != 1: parser.error('--shoulder-setting: pad wire not found (release host trim base): ' + line.strip())
        first = min(text.index(line) for line in pad.values())
        decl = ['    // Shoulder settings (interact.json): rows 0/1 = L/R Button (0-2 = L1/L2/L3,',
                '    // R1/R2/R3), rows 2/3 = Select + L/R, Y/A (0 none, 1-3 = L1/R1, L2/R2, L3/R3).']
        rows = [(name, address, 0) for name, address, _ in SHOULDER_ROWS] + [(c[0], c[1], c[3]) for c in SHOULDER_COMBOS]
        for n, (name, address, value) in enumerate(rows):
            decl.append(f"    reg [1:0] shoulder_set{n}=2'd{value};   // {name}")
            decl.append(f"    always @(posedge clk_74a) if (bridge_wr && bridge_addr==32'h{address:08x}) shoulder_set{n}<=bridge_wr_data[1:0]|bridge_wr_data[9:8]|bridge_wr_data[17:16]|bridge_wr_data[25:24];")
            decl.append(f"    (* async_reg=\"true\" *) reg [1:0] shoulder_meta{n}=2'd{value}, shoulder_sel{n}=2'd{value};")
            decl.append(f"    always @(posedge clk1x) begin shoulder_meta{n}<=shoulder_set{n}; shoulder_sel{n}<=shoulder_meta{n}; end")
        text = text[:first] + '\n'.join(decl) + '\n' + text[first:]
        for n, (name, address, targets) in enumerate(SHOULDER_ROWS):
            src = f'pocket_pad_buttons[{bits[targets[0]]}]'
            for value, key in enumerate(targets):
                # Each key's own source is only a docked controller's bit
                # (none for L1/R1); pocket_pad_buttons[10-13] also carry the
                # Select combos, which the combo rows route instead.
                own = ['', f'pad_combo_docked[{n}] | ', f'pad_combo_docked[{2+n}] | '][value]
                combos = ''.join(f" | (shoulder_sel{len(SHOULDER_ROWS)+c}==2'd{value+1} & pad_combos[{row[4]+n}])"
                                 for c, row in enumerate(SHOULDER_COMBOS))
                text = text.replace(pad[key], f"    wire joypad1_Key{key}={own}(shoulder_sel{n}==2'd{value} & {src}){combos};\n")
    if args.stick_settings:
        if not (args.dualshock and args.pad_combos and args.shoulder_setting):
            parser.error('--stick-settings needs --dualshock --pad-combos --shoulder-setting')
        from prepare_pocket_stick_settings import stick_settings_text
        try: text = stick_settings_text(text)
        except ValueError as error: parser.error('--stick-settings: ' + str(error))
        root = Path(__file__).resolve().parents[1]
        qsf_path = out/'apf_top.qsf'; qsf_text = qsf_path.read_text()
        # The DualShock in digital mode also presses the D-pad from the left
        # stick (joypad_pad_dualshock STICK_DPAD, DuckStation's default), so a
        # D-pad mapped to the left stick still works in digital-mode games.
        lean = f'"{root}/rtl/pocket/joypad_pad_dualshock.vhd"'
        if qsf_text.count(lean) != 1: parser.error('--stick-settings: joypad_pad_dualshock.vhd not in project')
        pad_text = (root/'rtl/pocket/joypad_pad_dualshock.vhd').read_text()
        if pad_text.count('STICK_DPAD : boolean := false;') != 1: parser.error('--stick-settings: STICK_DPAD generic not found')
        (out/'joypad_pad_dualshock.vhd').write_text(pad_text.replace('STICK_DPAD : boolean := false;', 'STICK_DPAD : boolean := true;'))
        qsf_path.write_text(qsf_text.replace(lean, f'"{out/"joypad_pad_dualshock.vhd"}"')
                            + f'set_global_assignment -name SYSTEMVERILOG_FILE "{root}/rtl/pocket/psx_pocket_stick_map.sv" -library work\n')
    if args.logo_skip_setting:
        # Single-word loaders (no --psram-main batching): give the loader a
        # halfword count of 2 so psx_bios_patch can sit in front of its CDC.
        if '.req_halfwords(load_halfwords)' not in text:
            for o, n in (('.req_data(load_data),.req_be(load_be),.rsp_valid(load_rsp),',
                          '.req_data(load_data),.req_be(load_be),.req_halfwords(load_halfwords),.rsp_valid(load_rsp),'),
                         (".s_halfwords(4'd2), .s_wdata(load_data),", ".s_halfwords(load_halfwords), .s_wdata(load_data),"),
                         ('    wire [15:0] load_be;\n', '    wire [15:0] load_be;\n    wire [3:0] load_halfwords;\n')):
                if text.count(o) != 1: parser.error('--logo-skip-setting: single-word loader anchor not found: ' + o[:60])
                text = text.replace(o, n)
            single_word_loader = True
        else: single_word_loader = False
        # psx_bios_patch between the loader and its memory port; it gates only
        # the CPU release and the CPU-side ownership handover (passed still
        # reports APF setup done, before Reset Exit).
        for o, n in (('.req_valid(load_valid),.req_ready(load_ready),.req_write(load_write),.req_addr(load_addr),',
                      '.req_valid(ldr_valid),.req_ready(ldr_ready),.req_write(ldr_write),.req_addr(ldr_addr),'),
                     ('.req_data(load_data),.req_be(load_be),.req_halfwords(load_halfwords),.rsp_valid(load_rsp),.rsp_ready(load_rsp_ready),',
                      '.req_data(ldr_data),.req_be(ldr_be),.req_halfwords(ldr_halfwords),.rsp_valid(ldr_rsp),.rsp_ready(ldr_rsp_ready),'),
                     ('        .rsp_error(load_error),.received_words(bios_received_words),', '        .rsp_error(ldr_error),.received_words(bios_received_words),'),
                     ('.host_run(reset_n),.bios_ready(passed),', '.host_run(reset_n),.bios_ready(bios_go),'),
                     ('bios_ready_meta<=passed;', 'bios_ready_meta<=bios_go;')):
            if text.count(o) != 1: parser.error('--logo-skip-setting: anchor not found: ' + o[:60])
            text = text.replace(o, n)
        inst = '    psx_boot_gate boot_gate ('
        if text.count(inst) != 1: parser.error('--logo-skip-setting: boot gate not found')
        text = text.replace(inst, """    // "Skip BIOS logo" (interact.json, bridge 0x50000004 bit 0, written before
    // Reset Exit): patch the shell entry after loading, before the CPU runs.
    reg logo_skip=1'b0;
    always @(posedge clk_74a) if (bridge_wr && bridge_addr==32'h50000004) logo_skip<=|bridge_wr_data;   // any nonzero word: the Pocket may not put a check value in bit 0
    reg setting_seen=1'b0;   // diagnostic: any bridge write to 0x5xxxxxxx (interact settings)
    always @(posedge clk_74a) if (bridge_wr && bridge_addr[31:28]==4'h5) setting_seen<=1'b1;
    wire ldr_valid, ldr_ready, ldr_write, ldr_rsp, ldr_rsp_ready, ldr_error, bios_go, bios_patched;
    wire [24:0] ldr_addr; wire [127:0] ldr_data; wire [15:0] ldr_be; wire [3:0] ldr_halfwords;
    psx_bios_patch bios_patch (.clk(clk_74a), .reset(!power_reset_n), .loader_ready(passed), .host_run(reset_n),
        .skip(logo_skip), .bios_ready(bios_go), .patched(bios_patched),
        .l_valid(ldr_valid), .l_write(ldr_write), .l_ready(ldr_ready), .l_addr(ldr_addr), .l_halfwords(ldr_halfwords),
        .l_data(ldr_data), .l_be(ldr_be), .l_rsp(ldr_rsp), .l_error(ldr_error), .l_rsp_ready(ldr_rsp_ready),
        .m_valid(load_valid), .m_write(load_write), .m_ready(load_ready), .m_addr(load_addr), .m_halfwords(load_halfwords),
        .m_data(load_data), .m_be(load_be), .m_rsp(load_rsp), .m_error(load_error), .m_rsp_ready(load_rsp_ready));
""" + inst, 1)
        qsf = out/'apf_top.qsf'
        # Diagnostic: with a perf log and a free status byte (no --psram-main),
        # the saved log shows {skip setting, patch written, CPU released, any
        # settings write (0x5xxxxxxx) seen, 4'b1011 marker} as last seen
        # (decode_perf_log.py prints it).
        if "shared_initialized, 8'b0, loaded_words}" in text:
            text = text.replace("shared_initialized, 8'b0, loaded_words}",
                                "shared_initialized, {logo_skip, bios_patched, bios_go, setting_seen, 4'b1011}, loaded_words}", 1)
        # bios_ready is a register in psx_bios_patch; like the loader's ready it
        # releases psx_boot_gate's asynchronous reset into a 3-stage pipe.
        sdc = out/'pocket.sdc'
        sdc.write_text(sdc.read_text()+'set_false_path -from [get_registers {*|bios_patch|bios_ready}] -to [get_registers {*|*boot_gate*|reset_pipe[*]}]\n')
        if single_word_loader:
            text = text.replace('.req_halfwords(ldr_halfwords),', '', 1)
            text = text.replace("wire [24:0] ldr_addr; wire [127:0] ldr_data; wire [15:0] ldr_be; wire [3:0] ldr_halfwords;",
                                "wire [24:0] ldr_addr; wire [127:0] ldr_data; wire [15:0] ldr_be; wire [3:0] ldr_halfwords=4'd2;", 1)
        qsf.write_text(qsf.read_text()+f'set_global_assignment -name SYSTEMVERILOG_FILE "{Path(__file__).resolve().parents[1]}/rtl/pocket/psx_bios_patch.sv" -library work\n')
    # psx_apf_video's weave input (480i woven frames): tied on, or the Pocket
    # menu setting with --weave-setting. Every instance must connect it.
    if 'psx_apf_video #(' in text:
        if text.count('psx_apf_video #(') != 1 or text.count('.blank(cpu_boot_reset),') != 1:
            parser.error('Expected one psx_apf_video instance with .blank(cpu_boot_reset)')
        menu_weave = """    // 480i weave setting (interact.json "480i weave", bridge 0x50000000 bit 0;
    // on by default and until the Pocket writes the saved value).
    reg weave_setting=1'b1;
    always @(posedge clk_74a) if (bridge_wr && bridge_addr==32'h50000000) weave_setting<=|bridge_wr_data;
    (* async_reg="true" *) reg [1:0] weave_sync=2'b11;
    always @(posedge video_rgb_clock) weave_sync<={weave_sync[0],weave_setting};
    wire apf_weave=weave_sync[1];
"""
        fixed_weave = "    wire apf_weave=1'b1;\n"
        if '.weave(' in text:
            if text.count('.weave(apf_weave)') != 1 or text.count('wire apf_weave=') != 1:
                parser.error('Unsupported existing weave wiring')
            if fixed_weave in text:
                if args.weave_setting: text = text.replace(fixed_weave, menu_weave, 1)
            elif text.count(menu_weave) != 1:
                parser.error('Unsupported existing weave setting')
        else:
            if 'apf_weave' in text: parser.error('Weave signal exists without its APF connection')
            text = text.replace('.blank(cpu_boot_reset),', '.blank(cpu_boot_reset), .weave(apf_weave),', 1)
            start = text.rindex('\n', 0, text.index('psx_apf_video #(')) + 1
            text = text[:start] + (menu_weave if args.weave_setting else fixed_weave) + text[start:]
        # 480i field order: --field-swap-setting adds a menu checkbox ("Swap
        # 480i fields", bridge 0x50000008 bit 0, off by default) that flips the
        # APF odd/even bit at run time; tied off otherwise. Neither order made
        # the BIOS 480i screens steady on the test Pocket (2026-10-15).
        if '.field_swap(' not in text:
            at = text.index('\n', text.index('wire apf_weave=')) + 1
            text = text[:at] + (SWAP_MENU if args.field_swap_setting else "    wire apf_swap=1'b0;\n") + text[at:]
            text = text.replace('.weave(apf_weave),', '.weave(apf_weave), .field_swap(apf_swap),', 1)
            if text.count('.field_swap(apf_swap)') != 1: parser.error('field swap connection failed')
        if args.video_test_setting:
            # Diagnostic "Video test" list (bridge 0x5000004C, value from any byte).
            at = text.index('\n', text.index('wire apf_weave=')) + 1
            text = text[:at] + """    reg [1:0] video_test_set=2'd0;
    always @(posedge clk_74a) if (bridge_wr && bridge_addr==32'h5000004C) video_test_set<=bridge_wr_data[1:0]|bridge_wr_data[9:8]|bridge_wr_data[17:16]|bridge_wr_data[25:24];
    (* async_reg="true" *) reg [1:0] video_test_meta=2'd0, video_test_sync=2'd0;
    always @(posedge video_rgb_clock) begin video_test_meta<=video_test_set; video_test_sync<=video_test_meta; end
""" + text[at:]
            text = text.replace('.field_swap(apf_swap),', '.field_swap(apf_swap), .video_test(video_test_sync),', 1)
            if text.count('.video_test(video_test_sync)') != 1: parser.error('video test connection failed')
    elif args.weave_setting: parser.error('--weave-setting: no psx_apf_video instance')
    # Older Native copies used the original single-word module interface.
    # They now compile against the packet-aware module in this checkout.
    if 'psx_native_memory native_memory' in text:
        old_native = '.load_valid(c_valid),.load_addr(c_addr),.load_data(c_data[31:0]),.load_be(c_be[3:0]),'
        new_native = '.load_valid(c_valid),.load_addr(c_addr),.load_halfwords(c_count),.load_data(c_data),.load_be(c_be),'
        if old_native in text:
            if text.count(old_native)!=1: parser.error('Ambiguous old Native loader wiring')
            text = text.replace(old_native,new_native,1)
        elif text.count(new_native)!=1:
            parser.error('Unsupported Native loader wiring; reconcile the packet interface before compiling')
    if args.libcrypt:
        if not (args.cd_stream and args.multi_track): parser.error('--libcrypt needs --cd-stream --multi-track')
        found = re.findall(r"psx_cd_stream #\(\.DEFAULT_SLOT\(16'd101\), \.MAX_TRACKS\(\d+\), \.TRACK_STRIDE\(16'd1\)\)", text)
        if len(found) != 1: parser.error('--libcrypt: multi-track psx_cd_stream parameters not found')
        text = text.replace(found[0], found[0][:-1] + ", .LIBCRYPT_SLOT(16'd99))")
    if args.trigger_buttons:
        # Docked pads with analog triggers report them in cont1_trig; whether
        # the dock also sets the L2/R2 key bits is not documented. OR a
        # trigger past 0x40 into those bits (types 2 and 3, docked pads).
        # The key word already crosses as independent levels (pad_input).
        old = '.apf_keys(cont1_key[15:0]),'
        if text.count(old) != 1: parser.error('--trigger-buttons: pad key input not found')
        text = text.replace(old, '.apf_keys(pad_keys),')
        anchor = '    psx_pocket_pad pad_input ('
        if text.count(anchor) != 1: parser.error('--trigger-buttons: pad instance not found')
        text = text.replace(anchor, '''    // --trigger-buttons: analog triggers of a docked pad also press L2/R2.
    wire pad_docked=cont1_key[31:28]==4'h2 || cont1_key[31:28]==4'h3;
    wire [15:0] pad_keys=cont1_key[15:0] | {4'b0, pad_docked && cont1_trig[15:8]>8'h40, pad_docked && cont1_trig[7:0]>8'h40, 10'b0};
''' + anchor)
    if args.boot_waits_disc:
        if not args.cd_stream: parser.error('--boot-waits-disc needs --cd-stream')
        from prepare_pocket_disc_swap import boot_wait_text, BOOT_WAIT_SDC
        try: text = boot_wait_text(text)
        except ValueError as error: parser.error('--boot-waits-disc: ' + str(error))
        sdc = out/'pocket.sdc'; sdc.write_text(sdc.read_text() + BOOT_WAIT_SDC)
    if args.disc_menu and not args.disc_swap: parser.error('--disc-menu needs --disc-swap')
    if args.disc_swap:
        if not args.cd_stream: parser.error('--disc-swap needs --cd-stream')
        from prepare_pocket_disc_swap import disc_swap_text
        try: text = disc_swap_text(text)
        except ValueError as error: parser.error('--disc-swap: ' + str(error))
        if args.disc_menu:
            if not args.multi_track or not 2 <= args.disc_menu <= 4: parser.error('--disc-menu needs --multi-track and 2-4 discs')
            from prepare_pocket_disc_swap import disc_menu_text, MENU_SDC
            try: text = disc_menu_text(text, args.disc_menu)
            except ValueError as error: parser.error('--disc-menu: ' + str(error))
            sdc = out/'pocket.sdc'; sdc.write_text(sdc.read_text() + MENU_SDC)
        root = Path(__file__).resolve().parents[1]
        qsf_path = out/'apf_top.qsf'
        qsf_path.write_text(qsf_path.read_text() + ''.join(
            f'set_global_assignment -name SYSTEMVERILOG_FILE "{root}/rtl/pocket/{name}" -library work\n'
            for name in ('psx_disc_swap.sv', 'psx_disc_menu.sv' if args.disc_menu else 'psx_disc_select.sv')))
    if args.one_file_disc:
        # One .bin per disc plus its track table file (psx_cd_stream
        # TABLE_OFFSET): the per-track file table and search are not built.
        if not (args.cd_stream and args.multi_track): parser.error('--one-file-disc needs --cd-stream --multi-track')
        # LibCrypt comes with the table (its mask in word 3), not from slot 99.
        if args.libcrypt: parser.error('--one-file-disc takes the LibCrypt mask from the track table; drop --libcrypt')
        found = re.findall(r"psx_cd_stream #\((\.DEFAULT_SLOT\(16'd101\), \.MAX_TRACKS\(\d+\)(, [^()]*\([^()]*\))*)\) disc_stream \(", text)
        if len(found) != 1: parser.error('--one-file-disc: multi-track psx_cd_stream parameters not found')
        params = re.sub(r"\.MAX_TRACKS\(\d+\)", '.MAX_TRACKS(1)', found[0][0])
        text = text.replace(f"psx_cd_stream #({found[0][0]}) disc_stream (", f"psx_cd_stream #({params}, .TABLE_OFFSET(16'd1)) disc_stream (", 1)
    if args.cue_mount:
        # Load disc takes a .cue: psx_cue_mount sits between the APF target
        # port and psx_cd_stream (docs/pocket-cue-loading.md).
        if not args.one_file_disc: parser.error('--cue-mount needs --one-file-disc')
    if args.cue_libcrypt and not args.cue_mount: parser.error('--cue-libcrypt needs --cue-mount')
    if args.cue_mount:
        def cue_once(old, new):
            nonlocal text
            if text.count(old) != 1: parser.error('--cue-mount anchor not found: ' + old.strip()[:70])
            text = text.replace(old, new)
        for port, wire in (('target_dataslot_read', 'cd_target_read'), ('target_dataslot_getfile', "1'b0"),
                           ('target_dataslot_openfile', "1'b0"), ('target_dataslot_done', 'cd_target_done'),
                           ('target_dataslot_err', 'cd_target_error'), ('target_dataslot_id', 'cd_target_id'),
                           ('target_dataslot_slotoffset', 'cd_target_offset'), ('target_dataslot_bridgeaddr', 'cd_target_bridge'),
                           ('target_dataslot_length', 'cd_target_length'), ('dataslot_update', 'cd_slot_update'),
                           ('dataslot_update_id', 'cd_slot_update_id'), ('dataslot_update_size', 'cd_slot_update_size')):
            cue_once(f'        .{port}({wire}),', f'        .{port}(cue_apf_{port}),')
        cue_once('        .osnotify_inmenu(),', '        .osnotify_inmenu(cue_in_menu),')
        for port in ('target_buffer_param_struct', 'target_buffer_resp_struct'):
            cue_once(f"        .{port}('0),", f'        .{port}(cue_apf_{port}),')
        cue_once("disc_stream (.host_clk(clk_74a), .host_reset(cd_stream_reset), .bridge_wr(bridge_wr),\n        .bridge_addr(bridge_addr), .bridge_data(bridge_wr_data),",
                 "disc_stream (.host_clk(clk_74a), .host_reset(cd_stream_reset), .bridge_wr(cue_s_bridge_wr),\n        .bridge_addr(cue_s_bridge_addr), .bridge_data(cue_s_bridge_data),")
        cue_once('.table_q(cd_table_q)', '.table_q(cue_table_view)')
        # A .cue/.bin picked from the launch list is mounted before the stream
        # sees allcomplete (it settles on its first mount).
        cue_once('.bridge_data(cue_s_bridge_data), .allcomplete(dataslot_allcomplete),', '.bridge_data(cue_s_bridge_data), .allcomplete(cue_s_allcomplete),')
        cue_once(".target_ready(1'b1)", '.target_ready(cue_u_ready)')
        mux = "        if (bridge_addr[31:24]==8'hf8) bridge_rd_data=cmd_bridge_rd_data;"
        cue_once(mux, mux + "\n        if (bridge_addr[31:24]==8'h2c) bridge_rd_data=cue_struct_rd;   // --cue-mount path struct")
        cue_once('    // APF scaler protocol:', """    // --cue-mount: a .cue or .bin picked from the launch list (slot 0) or
    // with Load disc becomes the one-file disc.
    wire cue_apf_target_dataslot_read, cue_apf_target_dataslot_getfile, cue_apf_target_dataslot_openfile;
    wire cue_apf_target_dataslot_done, cue_apf_dataslot_update, cue_s_bridge_wr, cue_u_ready, cue_mounted, cue_mount_failed, cue_in_menu;
    wire cue_s_allcomplete, cue_table_grant;
    wire [2:0] cue_apf_target_dataslot_err; wire [15:0] cue_apf_target_dataslot_id, cue_apf_dataslot_update_id;
    wire [31:0] cue_apf_target_dataslot_slotoffset, cue_apf_target_dataslot_bridgeaddr, cue_apf_target_dataslot_length;
    wire [31:0] cue_apf_dataslot_update_size, cue_apf_target_buffer_param_struct, cue_apf_target_buffer_resp_struct;
    wire [31:0] cue_s_bridge_addr, cue_s_bridge_data, cue_table_view, cue_struct_rd;
    psx_cue_mount #(.FIRMWARE0("CUE_FIRMWARE0"), .FIRMWARE1("CUE_FIRMWARE1"), .FIRMWARE2("CUE_FIRMWARE2"), .FIRMWARE3("CUE_FIRMWARE3")) cue_mount (.clk(clk_74a), .reset(cd_stream_reset), .in_menu(cue_in_menu),
        .allcomplete(dataslot_allcomplete), .s_allcomplete(cue_s_allcomplete), .disc_setting(CUE_DISC_SETTING),
        .bridge_wr(bridge_wr), .bridge_addr(bridge_addr), .bridge_data(bridge_wr_data), .struct_rd_data(cue_struct_rd),
        .slot_update(cue_apf_dataslot_update), .slot_update_id(cue_apf_dataslot_update_id), .slot_update_size(cue_apf_dataslot_update_size),
        .s_slot_update(cd_slot_update), .s_slot_update_id(cd_slot_update_id), .s_slot_update_size(cd_slot_update_size),
        .s_bridge_wr(cue_s_bridge_wr), .s_bridge_addr(cue_s_bridge_addr), .s_bridge_data(cue_s_bridge_data),
        .table_addr(cd_table_addr), .table_q(cd_table_q), .table_view(cue_table_view), .table_grant(cue_table_grant),
        .u_read(cd_target_read), .u_ready(cue_u_ready), .u_id(cd_target_id), .u_offset(cd_target_offset),
        .u_bridge(cd_target_bridge), .u_length(cd_target_length), .u_done(cd_target_done), .u_error(cd_target_error),
        .d_read(cue_apf_target_dataslot_read), .d_getfile(cue_apf_target_dataslot_getfile), .d_openfile(cue_apf_target_dataslot_openfile),
        .d_id(cue_apf_target_dataslot_id), .d_offset(cue_apf_target_dataslot_slotoffset), .d_bridge(cue_apf_target_dataslot_bridgeaddr),
        .d_length(cue_apf_target_dataslot_length), .d_done(cue_apf_target_dataslot_done), .d_error(cue_apf_target_dataslot_err),
        .param_struct(cue_apf_target_buffer_param_struct), .resp_struct(cue_apf_target_buffer_resp_struct),
        .cue_mounted(cue_mounted), .mount_failed(cue_mount_failed));
    // APF scaler protocol:""")
        # The firmware (rtl/pocket/cue_fw, built into psx_cue_fw0-3.hex) runs
        # on SERV (references/serv, ISC); serv_compdec.v (Apache-2.0) is for
        # the compressed ISA, which is off.
        repo = Path(__file__).resolve().parents[1]
        serv = repo/'references/serv/rtl'
        if not (serv/'serv_rf_top.v').is_file(): parser.error('--cue-mount: references/serv is missing (git submodule update --init references/serv)')
        # The firmware hex sits in the project, named relative to it (as
        # measure_spu_scalar_full_top.inputs() looks up HDL-named ROMs).
        # With the Disc menu, the reader also loads a cue's "(Disc n)" siblings
        # (its reg is declared with psx_disc_menu, before the stream).
        text = text.replace('CUE_DISC_SETTING', 'disc_setting' if args.disc_menu else "2'd0", 1)
        if args.cue_libcrypt: text = text.replace('psx_cue_mount #(.FIRMWARE0(', 'psx_cue_mount #(.LIBCRYPT(1), .FIRMWARE0(', 1)
        for lane in range(4):
            shutil.copyfile(repo/f'rtl/pocket/psx_cue_fw{lane}.hex', out/f'psx_cue_fw{lane}.hex')
            text = text.replace(f'"CUE_FIRMWARE{lane}"', f'"psx_cue_fw{lane}.hex"', 1)
        qsf_path = out/'apf_top.qsf'
        qsf_path.write_text(qsf_path.read_text() + ''.join(f'set_global_assignment -name VERILOG_FILE "{p}" -library work\n'
            for p in sorted(serv.glob('serv_*.v')) if p.name not in ('serv_compdec.v', 'serv_synth_wrapper.v'))
            + f'set_global_assignment -name SYSTEMVERILOG_FILE "{repo}/rtl/pocket/psx_cue_mount.sv" -library work\n')
    if args.sotn_memory and re.search(r'^\s*(?:sdr_ctrl|psram)\b(?:\s*#\s*\(|\s+\w+\s*\()', text, re.M):
        parser.error('--sotn-memory: core_top still instantiates a MIRLO64 memory controller')
    if args.wide_lines:
        # Every line stretched to 640 pixels (psx_apf_video_wide): the scaler
        # slots only differ by height (package --wide-lines writes them).
        if not args.scaler_slots: parser.error('--wide-lines needs --scaler-slots')
        start = text.find('psx_apf_video #('); end = text.find(') apf_video (', start)
        if start < 0 or end < 0 or text.count('psx_apf_video #(') != 1: parser.error('--wide-lines: psx_apf_video instance not found')
        params = (['.INTERLACE_FEATURE(1)'] if args.interlace_feature else []) + (['.FIELD_INVERT(1)'] if args.field_invert else []) + (['.PAL288(1)'] if args.pal288 else []) + (['.HYBRID(1)'] if args.hybrid_slots else []) + (['.LEAD_LINE(1)'] if args.lead_line else []) + (['.VS_ON_HS(1)'] if args.vs_on_hs else [])
        text = text[:start] + 'psx_apf_video_wide #(' + ', '.join(params or ['.PAL288(0)']) + text[end:]
        qsf_path = out/'apf_top.qsf'
        qsf_path.write_text(qsf_path.read_text() + f'set_global_assignment -name SYSTEMVERILOG_FILE "{Path(__file__).resolve().parents[1]}/rtl/pocket/psx_apf_video_wide.sv" -library work\n')
    if args.cd_buffers != 2:
        # More sectors read ahead of the drive (psx_cd_stream NBUF), so a slow
        # SD read does not starve XA audio during FMVs.
        if not args.cd_stream: parser.error('--cd-buffers needs --cd-stream')
        if text.count(') disc_stream (') != 1: parser.error('--cd-buffers: psx_cd_stream instance not found')
        if 'psx_cd_stream #(' in text: text = text.replace(') disc_stream (', f', .NBUF({args.cd_buffers})) disc_stream (', 1)
        else: text = text.replace('psx_cd_stream disc_stream (', f'psx_cd_stream #(.NBUF({args.cd_buffers})) disc_stream (', 1)
    core.write_text(text)
    if args.memcard_slot:
        # APF saves a nonvolatile slot with the size the core writes into its
        # data table (pairs of id, size in data.json order). package
        # --memcard-slot lists the card second (entry 3) and the boot log, if
        # any, third (entry 5); both are rewritten while the disc streamer is
        # not reading the table.
        card_size = "32'd131072"
        if text.count(".datatable_addr(cd_table_active ? cd_table_addr : 10'd3),") == 1:
            for o, n in ((".datatable_addr(cd_table_active ? cd_table_addr : 10'd3),",
                          ".datatable_addr(cd_table_active ? cd_table_addr : table_pick ? 10'd5 : 10'd3),"),
                         (".datatable_data(32'd16400),", f".datatable_data(table_pick ? 32'd16400 : {card_size}),")):
                if text.count(o) != 1: parser.error('--memcard-slot: data-table wiring not found: ' + o)
                text = text.replace(o, n)
        else:
            for o, n in (('.datatable_addr(cd_table_addr),', ".datatable_addr(cd_table_active ? cd_table_addr : 10'd3),"),
                         (".datatable_wren(1'b0),\n        .datatable_data(32'b0),", f".datatable_wren(!cd_table_active),\n        .datatable_data({card_size}),"),
                         (".table_grant(1'b1)", '.table_grant(cd_table_grant)')):
                if text.count(o) != 1: parser.error('--memcard-slot: data-table wiring not found: ' + o)
                text = text.replace(o, n)
            if 'reg cd_table_grant' not in text:
                anchor = '    psx_cd_stream #('
                if text.count(anchor) != 1: parser.error('--memcard-slot: psx_cd_stream instance not found')
                text = text.replace(anchor, '    reg cd_table_grant=0;\n    always @(posedge clk_74a) cd_table_grant<=cd_table_active;\n' + anchor)
        # The CPU starts only after the slot's last words reached PSRAM.
        ready = re.findall(r'\.host_run\(reset_n\),\.bios_ready\(([^()]+)\),', text)
        if len(ready) != 1: parser.error('--memcard-slot: boot gate bios_ready not found')
        text = text.replace(f'.host_run(reset_n),.bios_ready({ready[0]}),', '.host_run(reset_n),.bios_ready(boot_ready_card),')
        gate = '    psx_boot_gate boot_gate (\n'
        if text.count(gate) != 1: parser.error('--memcard-slot: boot gate not found')
        text = text.replace(gate, f'''    (* async_reg="true" *) reg [1:0] mc_idle74=0;
    reg boot_ready_card=0;
    always @(posedge clk_74a) begin mc_idle74<={{mc_idle74[0],!mc_busy}}; boot_ready_card<=({ready[0]}) && mc_idle74[1]; end
''' + gate)
        if args.memcard_format:
            # The card is checked (and a new one formatted) once the BIOS and
            # every slot are loaded; the CPU then still waits for !mc_busy.
            if text.count('    wire memcard_load_done;\n') != 1: parser.error('--memcard-format: card slot wiring not found')
            text = text.replace(gate, f'    assign memcard_load_done=({ready[0]});\n' + gate)
        anchor = '    reg [31:0] service_read;\n'
        if text.count(anchor) != 1: parser.error('--memcard-slot: service read anchor not found')
        text = text.replace(anchor, "    reg table_pick=1'b0;\n    always @(posedge clk_74a) table_pick<=!table_pick;\n" + anchor)
        core.write_text(text)
    if args.cue_mount:
        # The stream's table lookups also wait while the reader loads the disc
        # the Disc menu asks for (after --memcard-slot's grant wiring).
        grants = re.findall(r"\.table_grant\(([^()]*)\), \.table_addr\(cd_table_addr\)", text)
        if len(grants) != 1: parser.error('--cue-mount: stream table_grant not found')
        new = 'cue_table_grant' if grants[0] == "1'b1" else f'cue_table_grant && {grants[0]}'
        text = text.replace(f'.table_grant({grants[0]}), .table_addr(cd_table_addr)', f'.table_grant({new}), .table_addr(cd_table_addr)', 1)
        core.write_text(text)
    # Synchronizer inputs and held-payload crossings of the Pocket glue.
    sdc = out/'pocket.sdc'; extra = []
    if args.shoulder_setting:
        extra.append('# The shoulder menu settings cross into clk1x through two flops.\n'
                     'set_false_path -to [get_registers {*|shoulder_meta*}]\n')
    if 'psx_apf_video #(' in text:
        extra.append('# The 480i menu settings cross into the video clock through two-flop synchronizers.\n'
                     'set_false_path -to [get_registers {*|weave_sync[0] *|swap_sync[0] *|video_test_meta[*]}]\n')
    if args.cd_stream:
        # psx_cd_stream holds every multi-bit value that crosses from before its
        # toggle until the far side answers; the toggles cross through two flops.
        # The far side captures a payload two synchronizer stages after its
        # toggle, so it is stable around capture: hold gets one period of slack.
        held = ('*|disc_stream|m_valid *|disc_stream|m_end[*] *|disc_stream|m_minutes[*] *|disc_stream|m_seconds[*] '
                '*|disc_stream|m_ntracks[*] *|disc_stream|m_slot[*] *|disc_stream|resp_buf* *|disc_stream|resp_kind[*] '
                '*|disc_stream|req_lba[*] *|disc_stream|sel_slot[*] *|disc_stream|m_present[*]'
                + (' *|disc_stream|m_lc_bytes[*]' if args.libcrypt else '')
                # --one-file-disc: "track table file in the buffer", held like m_valid.
                + (' *|disc_stream|m_tb' if args.one_file_disc else ''))
        extra.append('# psx_cd_stream held payloads (one 74.25 MHz period) and toggle synchronizers.\n'
                     f'set_max_delay 13 -from [get_registers {{{held}}}]\n'
                     f'set_min_delay -13 -from [get_registers {{{held}}}]\n'
                     'set_false_path -to [get_registers {*|disc_stream|h_req_s[0] *|disc_stream|h_done_s[0] *|disc_stream|h_sel_s[0] '
                     '*|disc_stream|h_mdone_s[0] *|disc_stream|c_resp_s[0] *|disc_stream|c_mount_s[0] *|disc_stream|c_selack_s[0] '
                     '*|disc_stream|c_pending_s[0]}]\n')
    if extra: sdc.write_text(sdc.read_text() + '\n' + ''.join(extra))
    if args.host_config_cdc or args.video_config_cdc or args.apf_blank_cdc:
        from prepare_pocket_config_cdc import prepare_project
        prepare_project(out, host=args.host_config_cdc, video=args.video_config_cdc, blank=args.apf_blank_cdc)
    if args.video_return_ff:
        from prepare_video_return_ff import prepare_project as prepare_return_ff
        prepare_return_ff(out)
    if args.video_request_cdc:
        from prepare_video_request_cdc import prepare_project as prepare_request_cdc
        prepare_request_cdc(out)
    if args.audio_rom_packed:
        from prepare_audio_rom_packed import prepare_project as prepare_audio_packed
        prepare_audio_packed(out)
    if args.dpram_sdp:
        from prepare_dpram_sdp import prepare_project as prepare_sdp
        prepare_sdp(out)
    # Quartus Lite's fitter is mostly serial; more threads still shave its
    # parallel phases (the generators pin 4). Early builds run 2-3 at a time.
    qsf = out/'apf_top.qsf'
    qsf.write_text(re.sub(r'set_global_assignment -name NUM_PARALLEL_PROCESSORS \d+', 'set_global_assignment -name NUM_PARALLEL_PROCESSORS 8', qsf.read_text()))
    if args.fit_effort_max:
        qsf.write_text(qsf.read_text() + 'set_global_assignment -name FITTER_EFFORT "STANDARD FIT"\n'
                       'set_global_assignment -name ROUTER_TIMING_OPTIMIZATION_LEVEL MAXIMUM\n')
    if args.pack_high:
        qsf.write_text(qsf.read_text() + 'set_global_assignment -name ALM_REGISTER_PACKING_EFFORT HIGH\n')
    print(out)


if __name__ == '__main__': main()
