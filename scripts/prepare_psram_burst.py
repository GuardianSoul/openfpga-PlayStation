"""Generate a both-die burst adaptation of the pinned MIRLO PSRAM controller.

The original file/PHY are preserved. Both dies receive BCR 0x641F and full
refresh; the burst port selects a die, restarts noncontiguous commands, and
completes a safe masked/read beat before honoring reset during a burst.
"""
import argparse
import json
from pathlib import Path
import re

from prepare_resource_targets import digest
from tool_paths import ROOT

UPSTREAM=ROOT/'references/MIRLO64/rtl/soc/psram.sv'


def burst_text(text):
    def replace(before, after):
        nonlocal text
        if text.count(before)!=1: raise ValueError('Pinned PSRAM contract changed: '+before[:60])
        text=text.replace(before, after)
    replace('module psram #(', 'module psx_psram_burst #(')
    replace('st_t         st;', 'st_t         st = S_INIT;')
    replace('input  wire         z_valid,', 'input  wire         z_valid,\n    input  wire         z_die,               // CE0#/CE1#, both configured for bursts')
    replace('wire  [15:0] cfg_val = cfg_n[1] ? RCR_ALL : cfg_n[0] ? BCR_ASYNC : BCR_BURST;',
            'wire  [15:0] cfg_val = cfg_n[1] ? RCR_ALL : BCR_BURST;')
    replace('wire         take = slot && z_valid && z_we == dir;', '''logic        burst_die;
logic [21:0] next_address;
// Reset drains one masked write/read if still in initial latency. It leaves
// CE# low until a safe end of transfer, even when the source has cleared valid.
wire         take = slot && (rst || (z_valid && z_we == dir &&
                                   z_die == burst_die && z_addr == next_address));''')
    replace('assign z_ready = take;', 'assign z_ready = take && !rst;')
    replace('assign z_rvalid = rsr[2];', 'assign z_rvalid = rsr[2] && !rst;')
    # The public readiness flag is also suppressed while a reset drains pins.
    text=re.sub(r'\bready\b', 'configured', text)
    replace('output logic        configured,', 'output wire         ready,')
    replace('localparam logic [15:0] BCR_BURST', 'logic configured;\nassign ready = configured && !rst;\nlocalparam logic [15:0] BCR_BURST')
    replace('if (rst) begin', 'if (rst && (st == S_IDLE || st == S_INIT)) begin')
    replace('dir <= z_we; nxt <= z_addr[6:0]; stop <= 1\'b0;',
            'dir <= z_we; nxt <= z_addr[6:0]; stop <= 1\'b0;\n            burst_die <= z_die; next_address <= z_addr;')
    replace("o_ce_n <= 2'b10; o_adv_n <= 1'b0; o_we_n <= !z_we;",
            "o_ce_n <= z_die ? 2'b01 : 2'b10; o_adv_n <= 1'b0; o_we_n <= !z_we;")
    replace('o_dq <= z_wdata; o_bm_n <= ~z_wmask;',
            "o_dq <= rst ? 32'b0 : z_wdata; o_bm_n <= rst ? 4'hf : ~z_wmask;\n            next_address <= next_address + 22'd1;")
    replace("if (z_last || nxt == 7'd127) stop <= 1'b1;",
            "if (rst || z_last || nxt == 7'd127) stop <= 1'b1;")
    replace('module psram_phy (', 'module psx_psram_burst_phy (')
    replace('input  wire         clk2x,              // clk doubled, their rising edges together',
            'input  wire         clk2x,              // doubled clock, aligned with sys\n    input  wire         clk_sample,         // doubled clock shifted +1.4 ns')
    replace('logic tg, tg_d;', "logic tg=1'b0, tg_d=1'b0;")
    replace('always_ff @(posedge clk) begin i_dq <= {cram0_dq, cram1_dq}; i_wait <= {cram0_wait, cram1_wait}; end',
            """// Capture old data 1.4 ns after the preceding falling doubled-clock
// edge, within the chip's 2 ns minimum hold window even with zero pad delay.
// The +1.4 ns PLL output gives the pin input additional setup margin. Capture
// only the old-word phase; hold it over the unused half of the sys cycle.
// Transfer at the following unshifted falling doubled-clock edge (6.6 ns
// available), rather than over a half sample-clock cycle. The controller
// consumes the same old word at each sys edge; no native cycle is added.
logic [31:0] pin_dq;
logic [1:0] pin_wait;
always_ff @(negedge clk_sample) if(second) begin pin_dq <= {cram0_dq, cram1_dq}; pin_wait <= {cram0_wait, cram1_wait}; end
always_ff @(negedge clk2x) begin i_dq <= pin_dq; i_wait <= pin_wait; end""")
    return '// Generated MIRLO both-die burst adaptation; preserve Apache-2.0/NOTICE.\n'+text


def prepare(directory):
    directory=Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True)
    out=directory/'psx_psram_burst.sv'; out.write_text(burst_text(UPSTREAM.read_text()))
    metadata=dict(path=str(out), sha256=digest(out), original=str(UPSTREAM), original_sha256=digest(UPSTREAM),
        assignment='SYSTEMVERILOG_FILE', library='work', transform='both-die burst configuration/select, contiguous guard, safe reset drain',
        hardware_verified=False)
    (directory/'manifest.json').write_text(json.dumps(metadata, indent=2)+'\n')
    return metadata


def prepare_clocks(directory):
    directory=Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True)
    original=ROOT/'rtl/memory/psx_mem_clocks.sv'; text=original.read_text()
    replacements={
        'module psx_mem_clocks (':'module psx_burst_mem_clocks (',
        'clk_dram, locked':'clk_dram, clk_sample, locked',
        '.number_of_clocks(3)':'.number_of_clocks(4)',
        '.pll_type("General"), .pll_subtype("General")':
            '.output_clock_frequency3("125.000000 MHz"), .phase_shift3("1400 ps"), .duty_cycle3(50),\n        .pll_type("General"), .pll_subtype("General")',
        '.outclk({clk_dram, clk_mem, clk_sys})':'.outclk({clk_sample, clk_dram, clk_mem, clk_sys})',
    }
    for before,after in replacements.items():
        if text.count(before)!=1: raise ValueError('Memory PLL contract changed: '+before)
        text=text.replace(before,after)
    out=directory/'psx_burst_mem_clocks.sv'; out.write_text('// Shared SDRAM/PSRAM PLL with +1.4 ns PSRAM input sampling output.\n'+text)
    return dict(path=str(out),sha256=digest(out),original=str(original),original_sha256=digest(original),
        assignment='SYSTEMVERILOG_FILE',library='work',transform='fourth shared PLL output: 125 MHz +1.4 ns PSRAM sampling',
        hardware_verified=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'build/sim/psram-burst/rtl')
    args=parser.parse_args(); print(json.dumps(prepare(args.output), indent=2))
