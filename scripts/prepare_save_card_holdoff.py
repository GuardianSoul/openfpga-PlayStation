#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Card slot step "card-holdoff" (RTL; docs/save-states-finish/checksum-card.md).

The PSRAM controller parks (core held in reset) and unparks (core released) on psx_vram_psram's
`park` input; core_top drove it straight from !reset_n. A park takes about 0.7 us and an unpark about
1.3 us (configuration writes), and an aux access of the card slot waits through it. The APF bridge
has no wait: a read's answer must be in bridge_rd_data when the NEXT transaction samples it (at the
fastest 88 clk_74a = 1.185 us later), and a strobe that finds the slot's one request register still
taken is dropped (sticky overflow). The card-chain probe (card-chain branch) saw 2 wrong answers of
274 reads at the fastest spacing across 12 transitions.

The hold-off: psx_memcard_slot takes the park request (!reset_n, clk_74a) and passes it to the
controller only after its bridge windows (card 0x20000000, save 0x30000000) have had no strobe for
2^HOLD_BITS - 1 clk_74a (HOLD_BITS 16: 0.88 ms). While the Pocket moves card or state words, a
reset change waits until the transfer has paused; the transition then runs while nothing is on the
slot. Nothing else changes: the save device's core_held still follows reset_n at once, the slot
itself never waits, and a park request that is withdrawn before the quiet time (reset pulsed during a
transfer) never reaches the controller.

  slot:     parameter HOLD_BITS (16); input park_req (clk_74a level); output reg park (clk_74a,
            1 at configuration as !reset_n is); quiet_74, a HOLD_BITS counter cleared by a window
            strobe, saturating; park <= park_req when it is saturated.
  core_top: vram_park comes from the slot: .park_req(!reset_n), .park(vram_park).
  SDC:      none new. park is a clk_74a register into psx_vram_psram's synchroniser park_m, which
            pocket.sdc already false-paths (its source was reset_n's clk_74a register before).

What it does not cover: a transfer that STARTS less than about 1.5 us after a reset change, with
the slot quiet before (the residual window of the probe; the Pocket sends a reset command, then polls
its status, so its next data transaction is several transactions later). The bench shows that case
separately (checksum-card.md).

Cost expected: HOLD_BITS + 1 registers, a HOLD_BITS incrementer with a saturation detect (about 10
ALMs at 16 bits). No M10K.

Check: scripts/run_save_card_holdoff.py (card chain bench with the probe as a gating case, Icarus).
Usage: prepare_save_card_holdoff.py SLOT.sv --text-only OUT          (one psx_memcard_slot.sv)
       prepare_save_card_holdoff.py SOURCE --output OUTPUT [--seed N] (frozen project snapshot)
"""
import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'card-holdoff'
HOLD_BITS = 16


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected 1 match, found {text.count(old)}: {old.strip()[:80]}')
    return text.replace(old, new)


PARAM_OLD = "    parameter [31:0] SAVE_BASE = 32'h0,   // save-state window (0 = none)\n"
PARAM_NEW = PARAM_OLD + f"    parameter integer HOLD_BITS = {HOLD_BITS},   // {TAG}: park hold-off, 2^HOLD_BITS - 1 quiet clk_74a\n"
PORT_OLD = "    input  wire        load_done\n);\n"
PORT_NEW = f'''\
    input  wire        load_done,
    // {TAG}: the controller's park request (clk_74a level, !reset_n) and what the controller gets:
    // park_req once the bridge windows have been quiet for 2^HOLD_BITS - 1 clk_74a
    input  wire        park_req,
    output reg         park = 1'b1
);
'''
LOGIC_OLD = "    wire [31:0] wr_word = (BRIDGE_LITTLE != 0) ^ (FAULT == 1) ? bridge_wr_data : swap32(bridge_wr_data);\n"
LOGIC = f'''\
    // {TAG}: a park or unpark of the PSRAM controller makes a card access wait about 0.7 / 1.3 us,
    // longer than the bridge allows; it starts only while no card or save word is moving
    reg [HOLD_BITS-1:0] quiet_74 = {{HOLD_BITS{{1'b0}}}};
    always @(posedge clk_74a) begin
        if (wr_strobe || rd_strobe) quiet_74 <= {{HOLD_BITS{{1'b0}}}};
        else if (!(&quiet_74)) quiet_74 <= quiet_74 + 1'b1;
        if (&quiet_74) park <= park_req;
    end
'''
LOGIC_NEW = LOGIC_OLD + LOGIC
PAIRS = ((PARAM_OLD, PARAM_NEW), (PORT_OLD, PORT_NEW), (LOGIC_OLD, LOGIC_NEW))

TOP_PARK_OLD = "    wire vram_park=!reset_n;   // synchronised inside psx_vram_psram\n"
TOP_PARK_NEW = f"    wire vram_park;   // {TAG}: !reset_n through psx_memcard_slot's hold-off (clk_74a); synchronised inside psx_vram_psram\n"
TOP_SLOT_OLD = ".aux_done(mc_aux_done),.card_write(mc_card_write),.load_done(memcard_load_done));\n"
TOP_SLOT_NEW = f".aux_done(mc_aux_done),.card_write(mc_card_write),.load_done(memcard_load_done),\n        .park_req(!reset_n),.park(vram_park));   // {TAG}\n"
TOP_PAIRS = ((TOP_PARK_OLD, TOP_PARK_NEW), (TOP_SLOT_OLD, TOP_SLOT_NEW))

# Planted faults: (old, new) on the transformed slot text. Each must be rejected by the bench.
FAULTS = {
    # no hold-off: park follows the request at once (the slot before the step, through the new port)
    'no_hold': [("        if (&quiet_74) park <= park_req;\n", "        park <= park_req;\n")],
    # reads do not count as traffic (a read-out or card read-back is not protected)
    'writes_only': [("        if (wr_strobe || rd_strobe) quiet_74 <=", "        if (wr_strobe) quiet_74 <=")],
    # the quiet time far shorter than the bridge spacing (31 clk_74a)
    'short_hold': [("        if (&quiet_74) park <= park_req;\n", "        if (&quiet_74[4:0]) park <= park_req;\n")],
    # the controller never gets the request
    'stuck': [("        if (&quiet_74) park <= park_req;\n", "        if (&quiet_74) park <= park;\n")],
    # the counter does not saturate: it wraps, and the request passes for one clock in 2^HOLD_BITS
    'wraps': [("        else if (!(&quiet_74)) quiet_74 <= quiet_74 + 1'b1;\n", "        else quiet_74 <= quiet_74 + 1'b1;\n")],
}


def slot_text(text):
    """psx_memcard_slot.sv (a project's pinned copy) with the park hold-off."""
    if TAG in text or 'park_req' in text:
        raise ValueError(f'{TAG}: already applied')
    for old, new in PAIRS:
        text = _once(text, old, new)
    return text


def restore(text):
    for old, new in reversed(PAIRS):
        text = _once(text, new, old)
    return text


def top_text(text):
    if TAG in text:
        raise ValueError(f'{TAG}: core_top already has the step')
    for old, new in TOP_PAIRS:
        text = _once(text, old, new)
    return text


def fault_text(text, fault):
    for old, new in FAULTS[fault]:
        text = _once(text, old, new)
    return text


def prepare(source, output, seed=None):
    import prepare_save_checksum as cs
    return cs.prepare(source, output, seed, {'psx_memcard_slot.sv': slot_text, 'core_top.sv': top_text})


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
            args.text_only.write_text(slot_text(args.source.read_text()))
            print(f'{TAG}: wrote {args.text_only} (sha256 {sha(args.text_only.read_text())})')
        else:
            if not args.output:
                parser.error('SOURCE --output OUTPUT required')
            seed, changed = prepare(args.source, args.output, args.seed)
            print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
            for path, (old, new) in changed.items():
                print(f'{TAG}: patched {path} (sha256 {old[:16]} -> {new[:16]})')
            print(f'{TAG}: {HOLD_BITS} + 1 registers in psx_memcard_slot; core_top: vram_park through the slot; no SDC')
    except ValueError as error:
        raise SystemExit(str(error))
