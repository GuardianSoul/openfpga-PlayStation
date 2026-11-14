#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Constraint step "port-constraints" (SDC only; docs/hold-constraints-060.md section 3).

Constrains the 20 scaler output ports that RC-g left without any constraint:
  video  scal_vid[11:0], scal_de/hs/vs/skip: DDR (mf_ddio_bidir_12 on the video clock), with
         scal_clk forwarded from the 90 degree video clock (another DDIO): a center-aligned
         source-synchronous bus. Generated clock scal_clk_out; output delay 0 on both edges and
         the AN433 center-aligned false paths (setup rise->fall / fall->rise, hold rise->rise /
         fall->fall). With no external setup/hold assumed, each slack is the eye margin on that
         side; Analogue publishes no scaler numbers, so none are invented.
  audio  scal_auddac, scal_audlrck against the forwarded master clock scal_audmclk_out
         (generated from the audio PLL output), output delay 0. They are fabric registers
         (psx_audio_i2s) that change once per 4 MCLK (divider==1), so the check is a 2-setup /
         2-hold multicycle window (hold relationship −1 MCLK): a same-edge zero hold missed by -0.031 ns on candidate G s6
         (#1393) although nothing changes for 3 more MCLK.

Measured on RC-g s12 (#1338) by a timing-only sign-off (#1362, extra_sdc): video setup +3.459 /
hold +3.431 ns, audio setup +75.626 / hold +0.623 ns (worst corners), release_timing 0 failing
rows. The video sources are DDIO cells, so their numbers do not move with placement; a lost DDIO
or a new fabric stage before a pin shows up as a smaller eye.

Not covered here (section 3): the SDRAM pins (datasheet model rejects CAS 2 at 9.841 ns; the
owner's decision) and the APF bridge pins (no published SPI timing).

Usage: prepare_port_constraints.py SDC --text-only OUT
       prepare_port_constraints.py SOURCE --output OUTPUT [--seed N]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'port-constraints'
MARKER = 'POCKET_PORT_CONSTRAINTS'
ANCHOR = '# --static-host-cuts. pause (0x11000018)'
V0 = 'ic|video_pll|general[0].gpll~PLL_OUTPUT_COUNTER|divclk'
V1 = 'ic|video_pll|general[1].gpll~PLL_OUTPUT_COUNTER|divclk'
A0 = 'ic|audio_pll|mf_audio_pll_inst|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk'
BUS = '[get_ports {scal_vid[*] scal_de scal_hs scal_vs scal_skip}]'

LINES = [
    f'# {MARKER} (scripts/prepare_port_constraints.py): scaler video (center-aligned DDR, 90 degree',
    '# forwarded clock) and audio outputs; zero external setup/hold, so each slack is the eye margin',
    f'create_generated_clock -name scal_clk_out -source [get_pins {{{V1}}}] [get_ports {{scal_clk}}]',
    f'set_output_delay -clock scal_clk_out -max 0 {BUS}',
    f'set_output_delay -clock scal_clk_out -min 0 {BUS}',
    f'set_output_delay -clock scal_clk_out -clock_fall -max 0 -add_delay {BUS}',
    f'set_output_delay -clock scal_clk_out -clock_fall -min 0 -add_delay {BUS}',
    f'set_false_path -setup -rise_from [get_clocks {{{V0}}}] -fall_to [get_clocks scal_clk_out]',
    f'set_false_path -setup -fall_from [get_clocks {{{V0}}}] -rise_to [get_clocks scal_clk_out]',
    f'set_false_path -hold -rise_from [get_clocks {{{V0}}}] -rise_to [get_clocks scal_clk_out]',
    f'set_false_path -hold -fall_from [get_clocks {{{V0}}}] -fall_to [get_clocks scal_clk_out]',
    f'create_generated_clock -name scal_audmclk_out -source [get_pins {{{A0}}}] [get_ports {{scal_audmclk}}]',
    'set_output_delay -clock scal_audmclk_out -max 0 [get_ports {scal_auddac scal_audlrck}]',
    'set_output_delay -clock scal_audmclk_out -min 0 [get_ports {scal_auddac scal_audlrck}]',
    '# psx_audio_i2s changes lrck/dac only on divider==1, once per 4 MCLK: they must settle within 2 MCLK',
    '# with hold relationship -1 MCLK (setup 2, hold 2 after Quartus moves the hold edge with setup; not a cut)',
    f'set_multicycle_path -setup -end 2 -from [get_clocks {{{A0}}}] -to [get_clocks scal_audmclk_out]',
    f'set_multicycle_path -hold -end 2 -from [get_clocks {{{A0}}}] -to [get_clocks scal_audmclk_out]',
    f'# {MARKER}_END',
]


def sdc_text(text):
    if MARKER in text:
        raise ValueError(f'{TAG}: already applied')
    if text.count(ANCHOR) != 1:
        raise ValueError(f'{TAG}: expected 1 match, found {text.count(ANCHOR)}: {ANCHOR}')
    return text.replace(ANCHOR, '\n'.join(LINES) + '\n\n' + ANCHOR)


def prepare(source, output, seed=None):
    import prepare_save_checksum as cs
    return cs.prepare(source, output, seed, {'pocket.sdc': sdc_text})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--text-only', type=Path, metavar='OUT')
    args = parser.parse_args()
    try:
        if args.text_only:
            if args.text_only.resolve() == args.source.resolve():
                raise SystemExit(f'{TAG}: refusing to overwrite the input')
            args.text_only.write_text(sdc_text(args.source.read_text()))
            print(f'{TAG}: wrote {args.text_only}')
        else:
            if not args.output:
                parser.error('SOURCE --output OUTPUT required')
            seed, changed = prepare(args.source, args.output, args.seed)
            print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
            for path, (old, new) in changed.items():
                print(f'{TAG}: patched {path} (sha256 {old[:16]} -> {new[:16]})')
    except ValueError as error:
        raise SystemExit(str(error))
