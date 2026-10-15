#!/usr/bin/env python3
"""Memory timing sweep probes for the real Pocket (PSXSweep).

PSXDiagProbe on the test Pocket (2026-10-14): both controllers initialize and
all BIOS words load, but the BIOS SDRAM CRC differs and PSRAM word 1 reads
back word 0's data. This builds the diagnostic probe with:

  * a PSRAM latency sweep: the controller's bring-up cfg (write adjustment
    [1:0], read adjustment [3:2]; MIRLO64's runtime tries these too) runs the
    full PSRAM test for all 16 values and shows which pass;
  * an SDRAM variant: `mirlo` uses MIRLO64's unmodified sdr_phy and its
    hardware-used clocks (62.5 / 125 / 125 MHz at 4 ns); `slow` keeps the
    burst probe's 31.25 / 62.5 MHz clocks and the retimed falling-edge PHY.

Both variants are packaged as two cores of one platform, sharing one BIOS.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

from build_pocket_diag_probe import prepare as prepare_diag
from prepare_resource_targets import digest
from pq_psram import renamed_psram
from tool_paths import ROOT, find_tool

SWEEP = '''
    // PSRAM latency sweep: run the whole probe once per controller cfg.
    reg [3:0] sweep_cfg=0;
    reg sweep_cfg_wr=0, sweep_probe_reset=1, sweep_done=0;
    reg [7:0] sweep_hold=0;
    reg [15:0] sweep_pass=0, sweep_fail=0;
    reg [22:0] sweep_addr=0;
    reg [31:0] sweep_expected=0, sweep_actual=0;
    always @(posedge burst_clk1x) begin
        sweep_cfg_wr<=0;
        if (ps_reset[2]) begin
            sweep_cfg<=0; sweep_probe_reset<=1; sweep_hold<=0; sweep_done<=0;
            sweep_pass<=0; sweep_fail<=0;
        end else if (!sweep_done) begin
            if (sweep_probe_reset) begin
                sweep_hold<=sweep_hold+1'b1;
                if (sweep_hold==8'd1) sweep_cfg_wr<=1;
                if (&sweep_hold) sweep_probe_reset<=0;
            end else if (ps_passed || ps_failed) begin
                sweep_pass[sweep_cfg]<=ps_passed; sweep_fail[sweep_cfg]<=ps_failed;
                if (sweep_cfg==4'd4) begin
                    sweep_addr<=ps_failed_addr; sweep_expected<=ps_failed_expected; sweep_actual<=ps_failed_actual;
                end
                sweep_probe_reset<=1; sweep_hold<=0;
                if (sweep_cfg==4'd15) sweep_done<=1; else sweep_cfg<=sweep_cfg+1'b1;
            end
        end
    end
'''

SWEEP64 = '''
    // PSRAM latency x chip-clock phase sweep: 16 cfg values for each of four
    // chip clock rising-edge positions (8, 12, 16, 20 ns after clk's edge).
    reg [5:0] sweep_cfg=0;
    reg sweep_cfg_wr=0, sweep_probe_reset=1, sweep_done=0;
    reg [7:0] sweep_hold=0;
    reg [63:0] sweep_pass=0, sweep_fail=0;
    always @(posedge burst_clk1x) begin
        sweep_cfg_wr<=0;
        if (ps_reset[2]) begin
            sweep_cfg<=0; sweep_probe_reset<=1; sweep_hold<=0; sweep_done<=0;
            sweep_pass<=0; sweep_fail<=0;
        end else if (!sweep_done) begin
            if (sweep_probe_reset) begin
                sweep_hold<=sweep_hold+1'b1;
                if (sweep_hold==8'd1) sweep_cfg_wr<=1;
                if (&sweep_hold) sweep_probe_reset<=0;
            end else if (ps_passed || ps_failed) begin
                sweep_pass[sweep_cfg]<=ps_passed; sweep_fail[sweep_cfg]<=ps_failed;
                sweep_probe_reset<=1; sweep_hold<=0;
                if (&sweep_cfg) sweep_done<=1; else sweep_cfg<=sweep_cfg+1'b1;
            end
        end
    end
'''

CLOCKS = {
    'mirlo': {'"31.250000 MHz"': '"62.500000 MHz"',
              '.output_clock_frequency1("62.500000 MHz")': '.output_clock_frequency1("125.000000 MHz")',
              '.output_clock_frequency2("62.500000 MHz"), .phase_shift2("8000 ps")':
                  '.output_clock_frequency2("125.000000 MHz"), .phase_shift2("4000 ps")'},
    'slow': {},
}
CLOCKS['upstream'] = CLOCKS['mirlo']   # plus MIRLO64's unmodified psram/psram_phy
CLOCKS['async'] = CLOCKS['mirlo']      # upstream, plus an asynchronous die-1 baseline first
CLOCKS['bcr'] = CLOCKS['mirlo']        # async, then read back both dies' BCRs
CLOCKS['phase'] = CLOCKS['mirlo']      # upstream with a runtime chip-clock phase select
CLOCKS['quake'] = CLOCKS['mirlo']      # cram0 on PocketQuake's hardware-used PSRAM recipe
CLOCKS['pairstress'] = CLOCKS['mirlo'] # both chips via psx_psram_pair + console-like stress
CLOCKS['singlestress'] = CLOCKS['mirlo'] # chip 0 alone via psx_psram_pair PAIRED=0 + stress
# Reads-only stress over an address-pattern fill (a wrong word names its source):
CLOCKS['pairread'] = CLOCKS['mirlo']    # both chips, chip clock 5800 ps
CLOCKS['pairreadp2'] = CLOCKS['mirlo']  # both chips, chip clock 2000 ps (Codex's capture review)
CLOCKS['singleread'] = CLOCKS['mirlo']  # chip 0 alone, 5800 ps
# Reads plus one kind of write (both chips, 5800 ps): full-mask word, masked
# word, halfword, write-then-immediate-read.
MIXES = {'mixfull': 2, 'mixmask': 3, 'mixhalf': 4, 'mixchain': 5}
for _name in MIXES: CLOCKS[_name] = CLOCKS['mirlo']
# The same stress probes with the chip clock gated (DDIO, running only during
# burst reads), so asynchronous writes see CLK held LOW as the CellularRAM
# requires in synchronous mode.
GATED = {'gpairstress': 'pairstress', 'gsinglestress': 'singlestress', 'gmixfull': 'mixfull', 'gmixchain': 'mixchain',
         'gpairread': 'pairread'}
# Gated, and the write data held one cycle past WE# rising (psram.sv drops it
# on the same edge); 16M operations each.
HOLD = {'hpairstress': 'pairstress', 'hmixfull': 'mixfull', 'hmixchain': 'mixchain',
        'rpairstress': 'pairstress', 'rmixfull': 'mixfull', 'spairstress': 'pairstress', 'smixfull': 'mixfull',
        'tpairstress': 'pairstress', 'tmixfull': 'mixfull',
        'dmixfull': 'mixfull', 'qmixfull': 'mixfull', 'e50mixfull': 'mixfull', 'e66mixfull': 'mixfull',
        'e74mixfull': 'mixfull', 'e82mixfull': 'mixfull', 'e90mixfull': 'mixfull'}
# Chip-1 DQ0 reads 0 as 1 now and then (read glitch, data stored right):
# d*/q* write BCR drive strength full (640F) / quarter (642F) instead of half;
# e50/e66 move the chip clock phase to 5000 / 6600 ps (from 5800).
BCRS = {'dmixfull': "16'h640F", 'qmixfull': "16'h642F"}
PHASES = {'pairreadp2': 2000, 'e50mixfull': 5000, 'e66mixfull': 6600, 'e74mixfull': 7400, 'e82mixfull': 8200,
          'e90mixfull': 9000}
# r*/s*: the overlay's read-CRC row shows the re-read of the first failing
# word; s* also stretch the asynchronous write timing (psram.sv sized for 150 MHz).
REREAD = {'rpairstress', 'rmixfull', 'spairstress', 'smixfull', 'tpairstress', 'tmixfull',
          'dmixfull', 'qmixfull', 'e50mixfull', 'e66mixfull', 'e74mixfull', 'e82mixfull', 'e90mixfull'}
# (write extra clocks, idle clocks after a write): s* stretch writes 8 clocks,
# t* leave writes alone and idle 8 clocks after them.
SLOW = {'spairstress': (8, 0), 'smixfull': (8, 0), 'tpairstress': (0, 8), 'tmixfull': (0, 8)}
GATED.update(HOLD)
LONG = {'gpairread', *HOLD}
for _name in GATED: CLOCKS[_name] = CLOCKS['mirlo']

QUAKE_SIGNALS = ('a', 'dq', 'wait', 'clk', 'adv_n', 'cre', 'ce0_n', 'ce1_n', 'oe_n', 'we_n', 'ub_n', 'lb_n')
QUAKE_BODY = '''
    // cram0 on PocketQuake's recipe: free-running 105 MHz chip clock at
    // 5714 ps, asynchronous writes, WAIT-gated synchronous burst reads.
    wire [21:16] q0_a; wire [15:0] q0_dq; wire q0_wait=1'b0;
    wire q0_clk, q0_adv_n, q0_cre, q0_ce0_n, q0_ce1_n, q0_oe_n, q0_we_n, q0_ub_n, q0_lb_n;
    wire quake_clk, quake_locked;
    altera_pll #(.fractional_vco_multiplier("true"), .reference_clock_frequency("74.25 MHz"),
        .operation_mode("normal"), .number_of_clocks(2), .pll_type("General"), .pll_subtype("General"),
        .output_clock_frequency0("105.000000 MHz"), .phase_shift0("0 ps"), .duty_cycle0(50),
        .output_clock_frequency1("105.000000 MHz"), .phase_shift1("5714 ps"), .duty_cycle1(50)) quake_pll (
        .refclk(clk_74b), .rst(!power_reset_n), .fbclk(1'b0), .fboutclk(), .locked(quake_locked),
        .outclk({cram0_clk, quake_clk}));
    (* async_reg="true" *) reg [2:0] quake_reset=7;
    always @(posedge quake_clk or negedge quake_locked)
        if (!quake_locked) quake_reset<=7; else quake_reset<={quake_reset[1:0],1'b0};
    wire q_bank, q_write, q_burst, q_config, q_avail, q_busy, quake_passed, quake_failed;
    wire [21:0] q_addr; wire [15:0] q_wdata, q_rdata, q_config_data, quake_checked, quake_expected, quake_actual;
    wire [5:0] q_len; wire [22:0] quake_addr;
    psram #(.CLOCK_SPEED(105.0)) quake_psram (.clk(quake_clk), .bank_sel(q_bank), .addr(q_addr),
        .write_en(q_write), .data_in(q_wdata), .write_high_byte(1'b1), .write_low_byte(1'b1),
        .read_en(1'b0), .sync_burst_en(q_burst), .sync_burst_len(q_len),
        .config_en(q_config), .config_data(q_config_data), .read_avail(q_avail), .data_out(q_rdata), .busy(q_busy),
        .dbg_wait_seen(), .dbg_wait_cycles(), .dbg_burst_count(), .dbg_stale_count(),
        .cram_a(cram0_a), .cram_dq(cram0_dq), .cram_wait(cram0_wait), .cram_clk(),
        .cram_adv_n(cram0_adv_n), .cram_cre(cram0_cre), .cram_ce0_n(cram0_ce0_n), .cram_ce1_n(cram0_ce1_n),
        .cram_oe_n(cram0_oe_n), .cram_we_n(cram0_we_n), .cram_ub_n(cram0_ub_n), .cram_lb_n(cram0_lb_n));
    psx_psram_quake_probe quake_probe (.clk(quake_clk), .reset(quake_reset[2]),
        .bank_sel(q_bank), .write_en(q_write), .sync_burst_en(q_burst), .config_en(q_config),
        .addr(q_addr), .data_in(q_wdata), .sync_burst_len(q_len), .config_data(q_config_data),
        .read_avail(q_avail), .busy(q_busy), .data_out(q_rdata), .passed(quake_passed), .failed(quake_failed),
        .checked(quake_checked), .failed_address(quake_addr), .failed_expected(quake_expected), .failed_actual(quake_actual));
endmodule
'''


STRESS_BODY = '''
    // PSRAM stress (psx_psram_stress) through psx_psram_pair on PocketQuake's
    // recipe at the full core's clock (104.17 MHz, chip clock at 5800 ps).
    wire stress_clk, stress_locked;
    altera_pll #(.fractional_vco_multiplier("true"), .reference_clock_frequency("74.25 MHz"),
        .operation_mode("normal"), .number_of_clocks(2), .pll_type("General"), .pll_subtype("General"),
        .output_clock_frequency0("104.166666 MHz"), .phase_shift0("0 ps"), .duty_cycle0(50),
        .output_clock_frequency1("104.166666 MHz"), .phase_shift1("CHIP_PHASE ps"), .duty_cycle1(50)) stress_pll (
        .refclk(clk_74b), .rst(!power_reset_n), .fbclk(1'b0), .fboutclk(), .locked(stress_locked),
        .outclk({stress_chip_clk, stress_clk}));
    wire stress_chip_clk, s_clk_en;
CHIP_CLOCK_PINS    (* async_reg="true" *) reg [2:0] stress_reset=7;
    always @(posedge stress_clk or negedge stress_locked)
        if (!stress_locked) stress_reset<=7; else stress_reset<={stress_reset[1:0],1'b0};
    wire s_init, s_valid, s_write, s_ready, s_rsp, s_rsp_ready, s_error, stress_done;
    wire [24:0] s_addr, stress_fail_addr; wire [3:0] s_half, stress_fail_op; wire [127:0] s_wdata, s_rdata; wire [15:0] s_be;
    wire [31:0] stress_ops, stress_fails, stress_expected, stress_actual;
    psx_psram_pair #(.PAIRED(PAIRED_FLAG)TIMING_FLAG) stress_pair (.clk(stress_clk), .reset(stress_reset[2]), .initialized(s_init),
        .req_valid(s_valid), .req_write(s_write), .req_ready(s_ready), .req_addr(s_addr), .req_halfwords(s_half),
        .req_wdata(s_wdata), .req_byte_en(s_be), .rsp_valid(s_rsp), .rsp_ready(s_rsp_ready), .rsp_rdata(s_rdata), .rsp_error(s_error),
        .fill_valid(), .fill_word(), .fill_data(), .idle(), .chip_clk_en(s_clk_en),
        .cram0_a(cram0_a), .cram1_a(cram1_a), .cram0_dq(cram0_dq), .cram1_dq(cram1_dq), .cram0_wait(cram0_wait), .cram1_wait(cram1_wait),
        .cram0_adv_n(cram0_adv_n), .cram1_adv_n(cram1_adv_n), .cram0_cre(cram0_cre), .cram1_cre(cram1_cre),
        .cram0_ce0_n(cram0_ce0_n), .cram0_ce1_n(cram0_ce1_n), .cram1_ce0_n(cram1_ce0_n), .cram1_ce1_n(cram1_ce1_n),
        .cram0_oe_n(cram0_oe_n), .cram1_oe_n(cram1_oe_n), .cram0_we_n(cram0_we_n), .cram1_we_n(cram1_we_n),
        .cram0_ub_n(cram0_ub_n), .cram0_lb_n(cram0_lb_n), .cram1_ub_n(cram1_ub_n), .cram1_lb_n(cram1_lb_n));
    psx_psram_stress #(.OPS(OPS_FLAG), .READS_ONLY(READS_ONLY_FLAG), .MIX(MIX_FLAG)) stress (.clk(stress_clk), .reset(stress_reset[2]), .initialized(s_init),
        .req_valid(s_valid), .req_write(s_write), .req_ready(s_ready), .req_addr(s_addr), .req_halfwords(s_half),
        .req_wdata(s_wdata), .req_byte_en(s_be), .rsp_valid(s_rsp), .rsp_ready(s_rsp_ready), .rsp_rdata(s_rdata), .rsp_error(s_error),
        .done(stress_done), .ops(stress_ops), .fails(stress_fails), .fail_op(stress_fail_op), .fail_addr(stress_fail_addr),
        .fail_expected(stress_expected), .fail_actual(stress_actual), .fail_reread(stress_reread));
    wire [31:0] stress_reread;
    // Results for the nonvolatile slot at 0x40000000: the Pocket saves them to
    // Saves/psxsweep/common/result-<variant>.bin when the core quits.
    reg [31:0] result_word_r;
    always @* case (bridge_addr[5:2])
        4'd0: result_word_r=32'h52585350;   // "PSXR"
        4'd1: result_word_r={31'b0, stress_done};
        4'd2: result_word_r=stress_ops;
        4'd3: result_word_r=stress_fails;
        4'd4: result_word_r={3'b0, stress_fail_op, stress_fail_addr};
        4'd5: result_word_r=stress_expected;
        4'd6: result_word_r=stress_actual;
        4'd7: result_word_r=stress_reread;
        default: result_word_r=32'h0;
    endcase
    assign result_word=result_word_r;
endmodule
'''


def replace_once(text, old, new):
    if text.count(old) != 1: raise ValueError('Expected exactly one: '+old)
    return text.replace(old, new)


FREE_CLOCK = '    assign cram0_clk=stress_chip_clk, cram1_clk=stress_chip_clk;\n'
GATED_CLOCK = '''    // Chip clock pulses only while chip_clk_en (burst reads); LOW otherwise.
    altddio_out #(.width(2), .intended_device_family("Cyclone V"), .lpm_type("altddio_out"), .power_up_high("OFF"),
        .extend_oe_disable("OFF"), .invert_output("OFF"), .oe_reg("UNREGISTERED")) stress_chip_ddio (
        .datain_h({2{s_clk_en}}), .datain_l(2'b0), .outclock(stress_chip_clk), .dataout({cram1_clk, cram0_clk}),
        .aclr(1'b0), .aset(1'b0), .oe(1'b1), .outclocken(1'b1), .sclr(1'b0), .sset(1'b0));
'''


STRESS_VARIANTS = ('pairstress', 'singlestress', 'pairread', 'pairreadp2', 'singleread', *MIXES)


def prepare(directory, sdram):
    gated = sdram in GATED; base = GATED.get(sdram, sdram)
    manifest = prepare_diag(directory)
    clocks = directory/'burst-rtl/psx_burst_mem_clocks.sv'; text = clocks.read_text()
    for old, new in CLOCKS[sdram].items(): text = replace_once(text, old, new)
    clocks.write_text(text)
    if sdram in ('mirlo', 'upstream', 'async', 'bcr', 'phase', 'quake'):
        upstream = (ROOT/'references/MIRLO64/rtl/soc/sdr_phy.sv').read_text()
        phy = directory/'retimed-sdram/psx_sdr_phy.sv'
        phy.write_text('// Unmodified MIRLO64 c0712c0 rtl/soc/sdr_phy.sv, module renamed.\n'
            + replace_once(upstream, 'module sdr_phy (', 'module psx_sdr_phy ('))
    core = directory/'core_top.sv'; text = core.read_text()
    text = replace_once(text, ".cfg(4'h5), .cfg_wr(1'b0)", '.cfg(sweep_cfg[3:0]), .cfg_wr(sweep_cfg_wr)')
    text = replace_once(text, 'psx_psram_probe psram_probe (.clk(burst_clk1x), .reset(ps_reset[2]),',
        'psx_psram_probe psram_probe (.clk(burst_clk1x), .reset(ps_reset[2] || sweep_probe_reset),')
    text = replace_once(text, 'ps_flags_sync[2], bios_failed}', 'sweep_done && sweep_pass==0, bios_failed}')
    text = replace_once(text, 'ps_flags_sync[1], bios_passed}', '|sweep_pass, bios_passed}')
    if sdram == 'phase':
        text = replace_once(text, ".words({ps_failed_actual_sync, ps_failed_expected_sync, {9'b0,ps_failed_addr_sync}, {14'b0,ps_checked_sync},",
            ".words({sweep_fail[63:32], sweep_fail[31:0], sweep_pass[63:32], sweep_pass[31:0],")
        core.write_text(replace_once(text, 'endmodule\n', SWEEP64+'endmodule\n'))
    else:
        text = replace_once(text, ".words({ps_failed_actual_sync, ps_failed_expected_sync, {9'b0,ps_failed_addr_sync}, {14'b0,ps_checked_sync},",
            ".words({sweep_actual, sweep_expected, {9'b0,sweep_addr}, {sweep_pass, sweep_fail},")
        core.write_text(replace_once(text, 'endmodule\n', SWEEP+'endmodule\n'))
    if sdram in ('upstream', 'async', 'bcr', 'phase'):
        # MIRLO64's own controller and PHY: die 0 bursts only (die 1 is its
        # asynchronous port), so the probe runs its two die-0 windows.
        text = core.read_text()
        text = replace_once(text, 'psx_psram_burst psram_ctrl (', 'psram psram_ctrl (')
        text = replace_once(text, '.z_valid(ps_valid), .z_die(ps_die),', '.z_valid(ps_valid),')
        text = replace_once(text, 'psx_psram_burst_phy psram_phy (.clk(burst_clk1x), .clk2x(burst_clk2x), .clk_sample(burst_sample),',
            'psram_phy psram_phy (.clk(burst_clk1x), .clk2x(burst_clk2x),')
        core.write_text(text)
        probe_src = ROOT/'rtl/pocket/psx_psram_probe.sv'; probe = directory/'psx_psram_probe_die0.sv'
        body = probe_src.read_text()
        if body.count('if(region==3)') != 3: raise ValueError('Probe region sequencing changed')
        probe.write_text(body.replace('if(region==3)', 'if(region==1)'))
        if sdram in ('async', 'bcr'):
            text = core.read_text()
            text = replace_once(text, ".x_valid(1'b0), .x_we(1'b0), .x_addr(22'b0), .x_wdata(32'b0), .x_be(4'b0),\n        .x_ack(), .x_rdata(),",
                ".x_valid(async_valid), .x_we(async_we), .x_addr(async_addr), .x_wdata(async_wdata), .x_be(4'hf),\n        .x_ack(async_ack), .x_rdata(async_rdata),")
            text = replace_once(text, 'end else if (!sweep_done) begin', 'end else if (!sweep_done && (async_passed || async_failed)) begin')
            text = replace_once(text, '.status_failed({ps_wait_error,', '.status_failed({async_failed,')
            text = replace_once(text, '.status_passed({ps_initialized,', '.status_passed({async_passed,')
            text = replace_once(text, ".words({sweep_actual, sweep_expected, {9'b0,sweep_addr},",
                ".words({async_actual, async_expected, {async_checked[9:0],async_fail_addr},")
            text = replace_once(text, 'endmodule\n', '''    wire async_valid, async_we, async_ack, async_passed, async_failed;
    wire [21:0] async_addr, async_fail_addr;
    wire [31:0] async_wdata, async_rdata, async_expected, async_actual;
    wire [15:0] async_checked;
    psx_psram_async_probe async_probe (.clk(burst_clk1x), .reset(ps_reset[2]), .ready(ps_initialized),
        .x_valid(async_valid), .x_we(async_we), .x_addr(async_addr), .x_wdata(async_wdata),
        .x_ack(async_ack), .x_rdata(async_rdata), .passed(async_passed), .failed(async_failed),
        .checked(async_checked), .failed_address(async_fail_addr),
        .failed_expected(async_expected), .failed_actual(async_actual));
endmodule
''')
            core.write_text(text)
        psram_src = ROOT/'references/MIRLO64/rtl/soc/psram.sv'
        if sdram == 'bcr':
            # Configuration register reads through the X port (CRE high,
            # A[19:18]=10), die 0 then die 1, after the asynchronous test.
            body = psram_src.read_text()
            body = replace_once(body, '    input  wire  [3:0]  x_be,\n', '    input  wire  [3:0]  x_be,\n    input  wire         x_reg, x_reg_die0,\n')
            body = replace_once(body, '''            o_ce_n <= 2'b01; o_adv_n <= 1'b0;
            o_a <= x_addr[21:16];''', '''            o_ce_n <= x_reg_die0 ? 2'b10 : 2'b01; o_adv_n <= 1'b0; o_cre <= x_reg;
            o_a <= x_reg ? A_BCR : x_addr[21:16];''')
            psram_src = directory/'psram_bcr_read.sv'; psram_src.write_text('// MIRLO64 psram.sv plus X-port register reads (probe only).\n'+body)
            text = core.read_text()
            text = replace_once(text, ".x_valid(async_valid), .x_we(async_we), .x_addr(async_addr), .x_wdata(async_wdata), .x_be(4'hf),",
                ".x_valid(bcr_active ? bcr_valid : async_valid), .x_we(!bcr_active && async_we), .x_addr(async_addr), .x_wdata(async_wdata), .x_be(4'hf), .x_reg(bcr_active), .x_reg_die0(bcr_step==0),")
            text = replace_once(text, 'end else if (!sweep_done && (async_passed || async_failed)) begin', 'end else if (!sweep_done && bcr_done) begin')
            text = replace_once(text, ".words({async_actual, async_expected,", ".words({bcr1, bcr0,")
            text = replace_once(text, 'endmodule\n', '''    wire bcr_active=async_passed || async_failed;
    reg bcr_step=0, bcr_valid=0, bcr_done=0;
    reg [31:0] bcr0=0, bcr1=0;
    always @(posedge burst_clk1x) begin
        if (ps_reset[2]) begin bcr_step<=0; bcr_valid<=0; bcr_done<=0; end
        else if (bcr_active && !bcr_done) begin
            if (!bcr_valid) bcr_valid<=1;
            else if (async_ack) begin
                bcr_valid<=0;
                if (!bcr_step) bcr0<=async_rdata; else begin bcr1<=async_rdata; bcr_done<=1; end
                bcr_step<=1;
            end
        end
    end
endmodule
''')
            core.write_text(text)
        if sdram == 'phase':
            body = psram_src.read_text()
            body = replace_once(body, '    input  wire         clk_en,\n', '    input  wire         clk_en,\n    input  wire  [1:0]  phase_sel,          // chip clock rises 8/12/16/20 ns after clk (12: upstream)\n')
            body = replace_once(body, '''wire ck_h = clk_en && second;
wire ck_l = clk_en && !second;''', '''// Probe-only phase select. The pulse covers quarter slots {p, p+1} counted
// from the enabling cycle's start, p = 2 + phase_sel; slots 4-6 lie in the
// next cycle. phase_sel 1 reproduces the upstream clock exactly.
logic en_prev;
always_ff @(posedge clk) en_prev <= clk_en;
wire [4:0] pulse = 5'b00011 << phase_sel;     // slots 2..6
wire ck_h = second ? clk_en && pulse[2] : (clk_en && pulse[0]) || (en_prev && pulse[4]);
wire ck_l = second ? clk_en && pulse[3] : clk_en && pulse[1];''')
            psram_src = directory/'psram_phase.sv'; psram_src.write_text('// MIRLO64 psram.sv plus a chip-clock phase select (probe only).\n'+body)
            text = core.read_text()
            text = replace_once(text, '.o_bm_n(ps_o_bm_n), .clk_en(ps_clk_en),', '.o_bm_n(ps_o_bm_n), .clk_en(ps_clk_en), .phase_sel(sweep_cfg[5:4]),')
            core.write_text(text)
        qsf = directory/'apf_top.qsf'
        extra = f'set_global_assignment -name SYSTEMVERILOG_FILE "{ROOT}/rtl/pocket/psx_psram_async_probe.sv" -library work\n' if sdram in ('async', 'bcr') else ''
        qsf.write_text(replace_once(qsf.read_text(), f'"{probe_src}"', f'"{probe}"') + extra
            + f'set_global_assignment -name SYSTEMVERILOG_FILE "{psram_src}" -library work\n')
    if base in STRESS_VARIANTS:
        text = core.read_text()
        # Detach the probe's own PSRAM controller and phy from the pins.
        for c in ('cram0', 'cram1'):
            for s in QUAKE_SIGNALS:
                text = re.sub(r'\.' + c + '_' + s + r'\(' + c + '_' + s + r'\)', '.' + c + '_' + s + '()', text)
        text = replace_once(text, 'sweep_done && sweep_pass==0, bios_failed}', 'stress_done && stress_fails!=0, bios_failed}')
        text = replace_once(text, '|sweep_pass, bios_passed}', 'stress_done && stress_fails==0, bios_passed}')
        text = replace_once(text, ".words({sweep_actual, sweep_expected, {9'b0,sweep_addr}, {sweep_pass, sweep_fail},",
            ".words({stress_actual, stress_expected, {3'b0,stress_fail_op,stress_fail_addr}, stress_fails,")
        if sdram in REREAD: text = replace_once(text, "stress_fails, read_crc ^ 32'hffffffff,", "stress_fails, stress_reread,")
        # Result slot (data.json's second slot: its size is data-table entry 3).
        text = replace_once(text, "        .datatable_addr('0),\n        .datatable_wren('0),\n        .datatable_data('0),",
            "        .datatable_addr(10'd3),\n        .datatable_wren(1'b1),\n        .datatable_data(32'd64),")
        text = replace_once(text, "        if (bridge_addr[31:24]==8'hf8) bridge_rd_data=cmd_bridge_rd_data;\n        else case (bridge_addr)",
            "        if (bridge_addr[31:24]==8'hf8) bridge_rd_data=cmd_bridge_rd_data;\n        else if (bridge_addr[31:24]==8'h40) bridge_rd_data=result_word;\n        else case (bridge_addr)")
        text = replace_once(text, "    wire bios_passed, bios_failed, bios_checking;\n", "    wire [31:0] result_word;\n    wire bios_passed, bios_failed, bios_checking;\n")
        text = text.replace('    // Probe words', '')
        body = STRESS_BODY.replace('PAIRED_FLAG', '0' if base.startswith('single') else '1')
        extra, gap = SLOW.get(sdram, (0, 0))
        body = body.replace('TIMING_FLAG', f', .WRITE_EXTRA({extra}), .WRITE_GAP({gap})' + (f', .BCR({BCRS[sdram]})' if sdram in BCRS else ''))
        body = body.replace('CHIP_CLOCK_PINS', GATED_CLOCK if gated else FREE_CLOCK)
        body = body.replace('MIX_FLAG', str(MIXES.get(base, 0))).replace('OPS_FLAG', "32'd16000000" if sdram in LONG else "32'd2000000")
        body = body.replace('READS_ONLY_FLAG', '1' if 'read' in base or base in MIXES else '0').replace('CHIP_PHASE', str(PHASES.get(sdram, PHASES.get(base, 5800))))
        core.write_text(replace_once(text, 'endmodule\n', body))
        renamed = directory/'psx_pq_psram.sv'
        renamed.write_text(renamed_psram(hold=sdram in HOLD))
        sdc = directory/'pocket.sdc'; s = sdc.read_text()
        # The probe's own PSRAM controller is detached, so its doubled clock is pruned.
        s = s.replace('if {[get_collection_size [get_pins $ps_sys2x]] != 1} {error "Missing PSRAM doubled clock"}', '')
        start = s.index('foreach c {cram0 cram1} {'); end = s.index('\n}\n', start) + 3
        lines = ['set stress_chip {ic|stress_pll|general[1].gpll~PLL_OUTPUT_COUNTER|divclk}']
        for c, n in (('cram0', 'chip0'), ('cram1', 'chip1')):
            outs = f'{{{c}_a[*] {c}_dq[*] {c}_adv_n {c}_cre {c}_ce0_n {c}_ce1_n {c}_oe_n {c}_we_n {c}_ub_n {c}_lb_n}}'
            cap = f'{{*|stress_pair|{n}|cram_dq_r[*] *|stress_pair|{n}|cram_wait_r}}'
            lines += [f'create_generated_clock -name {c}_clk_out -source [get_pins $stress_chip] [get_ports {{{c}_clk}}]',
                      f'set_output_delay -clock {c}_clk_out -max 3.0 -add_delay [get_ports {outs}]',
                      f'set_output_delay -clock {c}_clk_out -min -0.5 -add_delay [get_ports {outs}]',
                      f'set_input_delay -clock {c}_clk_out -max 6.5 -add_delay [get_ports {{{c}_dq[*] {c}_wait}}]',
                      f'set_input_delay -clock {c}_clk_out -min 1.0 -add_delay [get_ports {{{c}_dq[*] {c}_wait}}]',
                      f'set_multicycle_path -setup 3 -from [get_clocks {c}_clk_out] -to [get_registers {cap}]',
                      f'set_multicycle_path -hold 2 -from [get_clocks {c}_clk_out] -to [get_registers {cap}]']
        sdc.write_text(s[:start] + '\n'.join(lines) + '\n' + s[end:])
        qsf = directory/'apf_top.qsf'
        qsf.write_text(qsf.read_text() + ''.join(f'set_global_assignment -name SYSTEMVERILOG_FILE "{f}" -library work\n'
            for f in (renamed, ROOT/'rtl/pocket/psx_psram_pair.sv', ROOT/'rtl/pocket/psx_psram_stress.sv'))
            + ''.join(f'set_instance_assignment -name FAST_INPUT_REGISTER ON -to {c}_{s}\n'
                      for c in ('cram0', 'cram1') for s in [f'dq[{i}]' for i in range(16)] + ['wait']))
    if sdram == 'quake':
        text = core.read_text()
        for s in QUAKE_SIGNALS:
            text = replace_once(text, f'.cram0_{s}(cram0_{s})', f'.cram0_{s}(q0_{s})')
        text = replace_once(text, 'sweep_done && sweep_pass==0, bios_failed}', 'quake_failed, bios_failed}')
        text = replace_once(text, '|sweep_pass, bios_passed}', 'quake_passed, bios_passed}')
        text = replace_once(text, ".words({sweep_actual, sweep_expected, {9'b0,sweep_addr}, {sweep_pass, sweep_fail},",
            ".words({{16'b0,quake_actual}, {16'b0,quake_expected}, {9'b0,quake_addr}, {quake_checked,16'b0},")
        core.write_text(replace_once(text, 'endmodule\n', QUAKE_BODY))
        sdc = directory/'pocket.sdc'
        sdc.write_text(replace_once(sdc.read_text(), 'foreach c {cram0 cram1} {', 'foreach c {cram1} {') + '''
# cram0: PocketQuake's constraints (core_constraints.sdc), retargeted.
set quake_chip {ic|quake_pll|general[1].gpll~PLL_OUTPUT_COUNTER|divclk}
create_generated_clock -name cram0_clk_out -source [get_pins $quake_chip] [get_ports {cram0_clk}]
set q0_outs [get_ports {cram0_a[*] cram0_dq[*] cram0_adv_n cram0_cre cram0_ce0_n cram0_ce1_n cram0_oe_n cram0_we_n cram0_ub_n cram0_lb_n}]
set_output_delay -clock cram0_clk_out -max 3.0 -add_delay $q0_outs
set_output_delay -clock cram0_clk_out -min -0.5 -add_delay $q0_outs
set_input_delay -clock cram0_clk_out -max 6.5 -add_delay [get_ports {cram0_dq[*] cram0_wait}]
set_input_delay -clock cram0_clk_out -min 1.0 -add_delay [get_ports {cram0_dq[*] cram0_wait}]
set_multicycle_path -setup 3 -from [get_clocks cram0_clk_out] -to [get_registers {*|quake_psram|cram_dq_r[*] *|quake_psram|cram_wait_r}]
set_multicycle_path -hold 2 -from [get_clocks cram0_clk_out] -to [get_registers {*|quake_psram|cram_dq_r[*] *|quake_psram|cram_wait_r}]
''')
        qsf = directory/'apf_top.qsf'
        qsf.write_text(qsf.read_text()
            + f'set_global_assignment -name SYSTEMVERILOG_FILE "{ROOT}/references/PocketQuake/src/fpga/core/psram.sv" -library work\n'
            + f'set_global_assignment -name SYSTEMVERILOG_FILE "{ROOT}/rtl/pocket/psx_psram_quake_probe.sv" -library work\n')
    rehash = {str(core), str(clocks), str(directory/'retimed-sdram/psx_sdr_phy.sv')}
    sources = [dict(s, sha256=digest(Path(s['path']))) if s['path'] in rehash else s for s in manifest['sources']]
    sdc = directory/'pocket.sdc'
    if sdram in ('mirlo', 'upstream', 'async', 'bcr', 'phase', 'quake'):
        # Identical 125 MHz outputs merge into one counter (general[1]).
        sdc.write_text(replace_once(sdc.read_text(), 'general[5].gpll', 'general[1].gpll'))
    sdc.write_text(sdc.read_text()+'set_false_path -to [get_registers {*|picture|words_meta[*]}]\n')
    manifest.update(kind='bios-psram-sweep-probe', sdram_variant=sdram, sources=sources,
        project_hashes={n: digest(directory/n) for n in ('apf_top.qsf', 'apf_top.qpf', 'pocket.sdc', 'apf/build_id.mif')})
    (directory/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


def compile_project(directory):
    binary = Path(find_tool('quartus_sh')).parent
    with (directory/'quartus.log').open('w') as log:
        for tool in ('quartus_map', 'quartus_fit', 'quartus_sta', 'quartus_asm'):
            result = subprocess.run([str(binary/tool), 'apf_top'], cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
            print(tool, result.returncode, flush=True)
            if result.returncode: raise SystemExit(result.returncode)


def package(projects, out):
    if out.exists(): shutil.rmtree(out)
    sd = out/'SD'; report = {}
    table = bytes(int(f'{b:08b}'[::-1], 2) for b in range(256))
    for sdram, project in projects.items():
        fit = (project/'output_files/apf_top.fit.summary').read_text()
        if 'Fitter Status : Successful' not in fit or 'Total virtual pins : 0' not in fit:
            raise SystemExit(f'{sdram}: successful physical fit required')
        timing = (project/'output_files/apf_top.sta.summary').read_text()
        name = 'PSXSweep' + sdram.capitalize()
        core = sd/'Cores'/f'GuardianSoul.{name}'; core.mkdir(parents=True)
        (core/'bitstream.rbf_r').write_bytes((project/'output_files/apf_top.rbf').read_bytes().translate(table))
        def write(file, value): (core/file).write_text(json.dumps(value, indent=2)+'\n')
        write('core.json', {'core': {'magic': 'APF_VER_1', 'metadata': {
            'platform_ids': ['psxsweep'], 'shortname': name,
            'description': f'Memory timing sweep, SDRAM {sdram}', 'author': 'GuardianSoul',
            'url': 'https://github.com/GuardianSoul/openfpga-PlayStation', 'version': '0.1.0', 'date_release': '2026-10-14'},
            'framework': {'target_product': 'Analogue Pocket', 'version_required': '1.1', 'sleep_supported': False,
                'dock': {'supported': True, 'analog_output': False}, 'hardware': {'link_port': False, 'cartridge_adapter': -1}},
            'cores': [{'name': 'default', 'id': 0, 'filename': 'bitstream.rbf_r'}]}})
        write('data.json', {'data': {'magic': 'APF_VER_1', 'data_slots': [{
            'name': 'BIOS', 'id': '0x20', 'required': True, 'parameters': '0x008', 'deferload': False,
            'filename': 'bios.bin', 'extensions': ['bin'], 'size_exact': 524288, 'address': '0x00000000'}]
            + ([{'name': 'Result', 'id': 1, 'required': False, 'parameters': '0x00', 'nonvolatile': True,
                 'filename': f'result-{sdram}.bin', 'extensions': ['bin'], 'size_maximum': 64, 'address': '0x40000000'}]
               if GATED.get(sdram, sdram) in STRESS_VARIANTS else [])}})
        write('video.json', {'video': {'magic': 'APF_VER_1', 'scaler_modes': [{
            'width': 640, 'height': 480, 'aspect_w': 4, 'aspect_h': 3, 'rotation': 0, 'mirror': 0}]}})
        for key, field in [('input', 'controllers'), ('interact', 'variables'), ('variants', 'variant_list')]:
            body = {'magic': 'APF_VER_1', field: []}
            if key == 'interact': body['messages'] = []
            write(key+'.json', {key: body})
        write('audio.json', {'audio': {'magic': 'APF_VER_1'}})
        (core/'info.txt').write_text(f'Memory timing sweep ({sdram} SDRAM). Photograph the screen.\n')
        notices = core/'licenses'; notices.mkdir()
        shutil.copyfile(ROOT/'THIRD_PARTY_NOTICES.md', notices/'THIRD_PARTY_NOTICES.md')
        shutil.copyfile(ROOT/'references/MIRLO64/LICENSE', notices/'MIRLO64-LICENSE.txt')
        shutil.copyfile(ROOT/'licenses/LiteX-BSD-2-Clause.txt', notices/'LiteX-BSD-2-Clause.txt')
        report[name] = dict(project=str(project), rbf_sha256=digest(project/'output_files/apf_top.rbf'),
            slacks={k: float(v) for k, v in re.findall(r'Type\s*:\s*(.*?)\nSlack\s*:\s*(-?[\d.]+)', timing)})
    assets = sd/'Assets/psxsweep/common'; assets.mkdir(parents=True)
    (assets/'PLACE-BIOS-HERE.txt').write_text('Place your own 524288-byte PSX BIOS dump here as bios.bin.\n')
    (sd/'Platforms').mkdir()
    (sd/'Platforms/psxsweep.json').write_text(json.dumps({'platform': {
        'category': 'Utility', 'name': 'PSX Memory Timing Sweep', 'year': 2026, 'manufacturer': 'GuardianSoul'}}, indent=2)+'\n')
    (out/'build-report.json').write_text(json.dumps(report, indent=2)+'\n')
    with zipfile.ZipFile(out/'PSXSweep.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(sd.rglob('*')):
            if path.is_file(): archive.write(path, path.relative_to(sd))
        archive.write(ROOT/'docs/pocket-sweep-probe.md', 'PSXSweep-INSTALL.md')
        archive.write(out/'build-report.json', 'PSXSweep-build-report.json')
    return out/'PSXSweep.zip'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'build/pocket/sweep-probe')
    parser.add_argument('--variants', nargs='+', default=['mirlo', 'upstream'], choices=sorted(CLOCKS))
    parser.add_argument('--package-only', nargs='*', type=Path, help='Package existing projects (same order as --variants)')
    args = parser.parse_args()
    out = args.output.resolve()
    if args.package_only:
        print(package(dict(zip(args.variants, [p.resolve() for p in args.package_only])), out/'package')); return
    projects = {}
    for sdram in args.variants:
        runs = out/'runs'; runs.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-')+sdram+'-', dir=runs))
        prepare(directory, sdram); print(directory, flush=True)
        compile_project(directory); projects[sdram] = directory
    print(package(projects, out/'package'))


if __name__ == '__main__': main()
