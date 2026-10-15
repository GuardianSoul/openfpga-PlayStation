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


SOTN_GLUE = '''    // pocket_sotn layout (--sotn-memory): main RAM, BIOS and SPU RAM on MiSTer
    // sdram.sv at clk3x; VRAM on both PSRAMs (vram_psram, clk2x).
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
    parser.add_argument('--weave-setting', action='store_true', help='With --interlace-feature: Pocket menu checkbox "480i weave" (bridge 0x50000000; package --weave-setting)')
    parser.add_argument('--interlace-feature', action='store_true', help='Send APF frame feature bits (interlaced, field) on VS in 480i (needs --scaler-slots)')
    parser.add_argument('--field-invert', action='store_true', help='Flip the APF odd/even field bit (psx_apf_video FIELD_INVERT)')
    parser.add_argument('--slots480', action='store_true', help='480i lines use 480-tall scaler slots 5-7 (package with --slots480)')
    parser.add_argument('--perf-log', action='store_true', help='Per-frame memory-wait counters saved to SD at bridge 0x40000000 (rtl/pocket/psx_perf_log.sv; package with --boot-log, needs --cd-stream)')
    parser.add_argument('--sotn-memory', action='store_true', help='pocket_sotn layout: main RAM, BIOS and SPU RAM on MiSTer sdram.sv at clk3x with the Pocket fixes (references/pocket_sotn/rtl/sdram.sv, rtl/pocket/psx_native_memory.sv); VRAM on both PSRAMs (pocket_sotn vram_psram.vhd)')
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
    parser.add_argument('--gpu-area-reg', action='store_true', help="gpu_poly's drawing-area test of xPos from registers (scripts/prepare_gpu_area_reg.py; cycle-identical, fixes the worst gpu_poly setup path)")
    parser.add_argument('--spu-static-readback', action='store_true', help='SPU static control registers read back from block RAM (scripts/prepare_spu_static_readback.py; needs spu-guest-m10k)')
    parser.add_argument('--main-lean', action='store_true', help='SDRAM-layout main adapter reads its read_data directly instead of two 128-bit copies (scripts/prepare_main_lean.py; b01d26d for the async adapter)')
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
    parser.add_argument('--fit-effort-max', action='store_true', help='Fitter: STANDARD FIT effort and maximum router timing optimization (release builds: closes the small fast-corner hold misses that Auto Fit leaves)')
    parser.add_argument('--static-host-cuts', action='store_true', help='Native release timing: false paths for quasi-static host settings (pause, isPal) and the diagnostic service readbacks of cd_top/pausing; video-out synchronizers kept out of RAM shift registers')
    parser.add_argument('--memcard-slot', action='store_true', help='With --sotn-memory: memory card 1 always present, its image in vram_psram region 1, loaded from and saved to an APF nonvolatile slot (pocket_sotn pocket_memcard.vhd; package --memcard-slot)')
    parser.add_argument('--memcard-format', action='store_true', help='With --memcard-slot: a new or all-zero card file is formatted before the CPU starts, so games without a format option can save (rtl/pocket/pocket_memcard_format.vhd)')
    parser.add_argument('--memcard-lean', action='store_true', help='Memory card buffer without its two 64-bit copy registers (scripts/prepare_memcard_lean.py)')
    parser.add_argument('--pad-combos', action='store_true', help='Select-held combos for L2/R2/L3/R3 on the handheld (rtl/pocket/psx_pocket_pad_combo.sv replaces psx_pocket_pad.sv)')
    parser.add_argument('--digital-pad', action='store_true', help='Digital-pad-only controller port (rtl/pocket/joypad_pad_digital.vhd, lockstep-equal to MiSTer for the Pocket tie-offs)')
    parser.add_argument('--vram-credit-timing', action='store_true', help="Codex's 4ea39ea VRAM BUSY timing repair (scripts/prepare_vram_credit_timing.py)")
    parser.add_argument('--cd-full', action='store_true', help='Restore the CD commands the FFVII-minimum trim dropped (Play, Forward, Backward, GetTN, GetTD, GetQ, VideoCD): CD audio, TOC queries')
    parser.add_argument('--multi-track', type=int, default=0, help='With --cd-stream: discs of up to N track files from an instance JSON (cue slot 100, bins 101..)')
    parser.add_argument('--cd-stream', action='store_true', help='Discs from SD: psx_cd_stream on the APF target/table ports (disc slot 0, BIOS slot 0x20)')
    parser.add_argument('--audio-trace', action='store_true', help='With --lean-trace: header words 4/5/7 count SPU frames, nonzero frames and audio status')
    parser.add_argument('--lean-trace', action='store_true', help='Drop the VRAM/async/vblank counters (saves area)')
    parser.add_argument('--no-rom-log', action='store_true', help='Omit the sampled BIOS-read log (saves area)')
    parser.add_argument('--cache-trace', action='store_true', help='With --boot-trace: the ROM-log words record the first 512 CPU cache fills below RAM 0x10000 instead')
    parser.add_argument('--boot-trace', action='store_true', help='Expose a main-RAM request trace at bridge 0x40000000 (needs --debug-overlay)')
    args = parser.parse_args()
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
        s = sdc_path.read_text()
        for root in {m for m in re.findall(r'"(/[^"]*?)/references/', s) if Path(m) != here}:
            s = s.replace(f'"{root}/references/', f'"{here}/references/')
        sdc_path.write_text(s)
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
        copy.write_text(area_reg_text(Path(polys[0]).read_text()))
        qsf_path.write_text(q.replace(f'"{polys[0]}"', f'"{copy}"'))
    if args.spu_static_readback:
        from prepare_spu_static_readback import static_readback_text
        qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text()
        spus = re.findall(r'"([^"]*/spu\.vhd)"', q)
        if len(spus) != 1: parser.error('--spu-static-readback: expected one spu.vhd in the project')
        copy = out/'spu-static-readback'/'spu.vhd'; copy.parent.mkdir(exist_ok=True)
        copy.write_text(static_readback_text(Path(spus[0]).read_text()))
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
    if args.memorymux_m10k:
        from prepare_memorymux_m10k import prepare_project
        prepare_project(out/'apf_top.qsf', out/'memorymux-m10k')
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
    if args.pad_combos:
        qsf_path = out/'apf_top.qsf'; qsf_text = qsf_path.read_text()
        pads = re.findall(r'"([^"]*/psx_pocket_pad\.sv)"', qsf_text)
        if len(pads) != 1: parser.error('--pad-combos: expected one psx_pocket_pad.sv in the project (release host trim base)')
        combo = Path(__file__).resolve().parents[1]/'rtl/pocket/psx_pocket_pad_combo.sv'
        qsf_path.write_text(qsf_text.replace(f'"{pads[0]}"', f'"{combo}"'))
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
    if args.cd_stream:
        if args.boot_trace: parser.error('--cd-stream uses the data-table port; drop --boot-trace')
        def once(old, new):
            nonlocal text
            if text.count(old) != 1: parser.error('CD stream anchor not found: ' + old.strip()[:60])
            text = text.replace(old, new)
        once('    psx_bios_loader bios_loader (', "    psx_bios_loader #(.BIOS_SLOT_ID(16'h20)) bios_loader (")
        if text.count('bios_slot_id==0 ?') != 2: parser.error('BIOS slot acknowledgement not found')
        text = text.replace('bios_slot_id==0 ?', "bios_slot_id==16'h20 ?")
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
        text = text[:start] + SOTN_GLUE + text[end:]
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
        if args.sdram_ctrl == 'clean':
            # MIT clean-room controller (rtl/memory/psx_sdram_ctrl.sv), same
            # ports as sdram.sv: a project copy of the glue instantiates it.
            controller = f'{root}/rtl/memory/psx_sdram_ctrl.sv'
            glue_text = (root/'rtl/pocket/psx_native_memory.sv').read_text()
            if glue_text.count('    sdram #(.SINGLE_CHIP(1)) controller (') != 1: parser.error('--sdram-ctrl clean: glue instance not found')
            glue = out/'psx_native_memory.sv'
            glue.write_text(glue_text.replace('    sdram #(.SINGLE_CHIP(1)) controller (',
                f'    psx_sdram_ctrl #(.SINGLE_CHIP(1), .CAS_LATENCY({args.sdram_cas}){", .SPU_FIRST(1)" if args.sdram_spu_first else ""}) controller ('))
        else:
            controller, glue = f'{sotn}/rtl/sdram.sv', root/'rtl/pocket/psx_native_memory.sv'
        q = q.replace(old_sdram[0], f'set_global_assignment -name SYSTEMVERILOG_FILE "{controller}" -library work')
        q += (f'set_global_assignment -name SYSTEMVERILOG_FILE "{glue}" -library work\n'
              f'set_global_assignment -name VHDL_FILE "{sotn}/pocket/src/fpga/core/vram_psram.vhd" -library work\n')
        qsf.write_text(q)
        # The MIRLO SDRAM pin constraints name the memory PLL; sdram.sv drives
        # the pins from clk3x (pocket_sotn has no pin constraints either).
        sdc = out/'pocket.sdc'
        sdc.write_text(''.join(l for l in sdc.read_text().splitlines(True) if 'pocket_sdram' not in l)
            # shared_initialized reaches clk_74a only through the mem_ready74
            # synchronizer and the diagnostic status readback (quasi-static).
            + 'set_false_path -from [get_registers {*|native_init_count[*]}] -to [get_registers {*|mem_ready74[0] *io_bridge_peripheral:*|spis_word_tx[*]}]\n')
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
        if args.memcard_format:
            slot = (slot.replace('    pocket_memcard memcard_slot (', '    wire memcard_load_done;\n    pocket_memcard_format memcard_slot (')
                    .replace('.card_write(mc_card_write));', '.card_write(mc_card_write),.load_done(memcard_load_done));'))
        once_card('    vram_psram vram (.clk(clk2x),.reset(!cpu_locked),\n', slot + '    vram_psram vram (.clk(clk2x),.reset(!cpu_locked),\n')
        once_card('    reg memcard1_available=0;\n', "    wire memcard1_available=1'b1;   // --memcard-slot: card 1 always present\n")
        once_card("        if(bridge_wr && bridge_addr==32'h11000108) memcard1_available<=bridge_wr_data[0:0];\n", '')
        mux = "        if (bridge_addr[31:24]==8'hf8) bridge_rd_data=cmd_bridge_rd_data;"
        once_card(mux, mux + "\n        if (bridge_addr[31:17]==15'h1000) bridge_rd_data=memcard_rd_data;   // card slot 0x20000000")
        root = Path(__file__).resolve().parents[1]
        entity = 'pocket_memcard_format' if args.memcard_format else 'pocket_memcard'
        source = root/'rtl/pocket/pocket_memcard_format.vhd' if args.memcard_format else root/'references/pocket_sotn/pocket/src/fpga/core/pocket_memcard.vhd'
        q = (out/'apf_top.qsf').read_text()
        (out/'apf_top.qsf').write_text(q + f'set_global_assignment -name VHDL_FILE "{source}" -library work\n'
            # Keep its synchronizers in flip-flops: Quartus otherwise merges
            # rd_tog_s into a RAM shift register (with the video request
            # sync), where neither the constraint below nor metastability
            # settling applies.
            f'set_instance_assignment -name AUTO_SHIFT_REGISTER_RECOGNITION OFF -to "core_top:ic|{entity}:memcard_slot"\n')
        sdc = out/'pocket.sdc'
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
set_false_path -from [get_registers {*core_top:ic|pause}] -to [get_registers {*|psx_top:component_core|pausing *|psx_top:component_core|pausing~DUPLICATE* *|psx_top:component_core|pausingSS *|psx_top:component_core|pausingSS~DUPLICATE* *|psx_top:component_core|ce *|psx_top:component_core|ce~DUPLICATE*}]
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
        extra = (', .INTERLACE_FEATURE(1)' if args.interlace_feature else '') + (', .SLOT480(1)' if args.slots480 else '') + (', .FIELD_INVERT(1)' if args.field_invert else '')
        text = re.sub(r'psx_apf_video #\(\.STEADY_INTERLACE\((\d)\)\)', r'psx_apf_video #(.STEADY_INTERLACE(\1), .SLOT_SELECT(1)' + extra + ')', text)
    elif args.interlace_feature or args.slots480: parser.error('--interlace-feature/--slots480 need --scaler-slots')
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
    if args.sotn_memory and re.search(r'^\s*(?:sdr_ctrl|psram)\b(?:\s*#\s*\(|\s+\w+\s*\()', text, re.M):
        parser.error('--sotn-memory: core_top still instantiates a MIRLO64 memory controller')
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
    # Synchronizer inputs and held-payload crossings of the Pocket glue.
    sdc = out/'pocket.sdc'; extra = []
    if 'psx_apf_video #(' in text:
        extra.append('# The 480i menu settings cross into the video clock through two-flop synchronizers.\n'
                     'set_false_path -to [get_registers {*|weave_sync[0] *|swap_sync[0]}]\n')
    if args.cd_stream:
        # psx_cd_stream holds every multi-bit value that crosses from before its
        # toggle until the far side answers; the toggles cross through two flops.
        held = ('*|disc_stream|m_valid *|disc_stream|m_end[*] *|disc_stream|m_minutes[*] *|disc_stream|m_seconds[*] '
                '*|disc_stream|m_ntracks[*] *|disc_stream|m_slot[*] *|disc_stream|resp_buf* *|disc_stream|resp_kind[*] '
                '*|disc_stream|req_lba[*] *|disc_stream|sel_slot[*]')
        extra.append('# psx_cd_stream held payloads (one 74.25 MHz period) and toggle synchronizers.\n'
                     f'set_max_delay 13 -from [get_registers {{{held}}}]\n'
                     f'set_min_delay 0 -from [get_registers {{{held}}}]\n'
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
    print(out)


if __name__ == '__main__': main()
