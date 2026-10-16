#!/usr/bin/env python3
"""GTE register readback shadows in block RAM read on the falling clk2x edge.

gte-control-ram keeps the CFC2 control shadow and the MFC2 data shadow
(32 x 32 each) in asynchronous-read MLABs, read with the CPU's register
number i_readRegID. Those two MLABs hold about four LABs. In the core the
read number comes from a clk1x register and the adapter samples the read on
rising clk2x edges, so it changes only on rising edges (clk1x edges are
rising clk2x edges). A block RAM that registers the read number on the
falling edge in between therefore shows, at every rising edge, the word the
asynchronous read showed; a shadow write on a rising edge is visible to the
following falling-edge read, as it was to the asynchronous read. The valid
bits stay in flip-flops. Contract: i_readRegID changes only on rising
clk2x edges. Needs gte-control-ram.
"""
import argparse
from pathlib import Path


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('gte-shadow-m10k: GTERegs source changed near: ' + old.strip().splitlines()[0][:80])
    return text.replace(old, new)


def shadow_m10k_text(text):
    for name in ('ctrlShadow', 'dataShadow'):
        text = _replace(text, f'(* ramstyle = "MLAB, no_rw_check" *) reg [31:0] {name} [0:31];',
                        f'(* ramstyle = "M10K, no_rw_check" *) reg [31:0] {name} [0:31];\n'
                        f'// gte-shadow-m10k: falling-edge read (i_readRegID changes on rising edges only)\n'
                        f'reg [31:0] {name}_q;\n'
                        f'always @(negedge i_clk) {name}_q <= {name}[i_readRegID[4:0]];')
    text = _replace(text, 'wire [31:0] dataReadback = dataValid[i_readRegID[4:0]] ? dataShadow[i_readRegID[4:0]] : 32\'d0;',
                    'wire [31:0] dataReadback = dataValid[i_readRegID[4:0]] ? dataShadow_q : 32\'d0;')
    text = _replace(text, 'wire [31:0] ctrlReadback = ctrlValid[i_readRegID[4:0]] ? ctrlShadow[i_readRegID[4:0]] : 32\'d0;',
                    'wire [31:0] ctrlReadback = ctrlValid[i_readRegID[4:0]] ? ctrlShadow_q : 32\'d0;')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.write_text(shadow_m10k_text(args.source.read_text()))


if __name__ == '__main__':
    main()
