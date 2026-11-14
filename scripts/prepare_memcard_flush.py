#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Card step "card-flush" (RTL; public issue #18, docs/public-issues.md).

APF writes the nonvolatile Memory Card 1 slot (id 16, bridge 0x20000000, 128 KiB) back to SD only
when the core quits from the menu; a crash or power loss drops every save made since the core
started. This step adds psx_memcard_flush (rtl/pocket/psx_memcard_flush.sv, appended to core_top.sv
as a second module, so the project's source list does not change): after a guest card write and
3.6 s without another, it sends one APF target data slot write of the whole card. APF reads the card
through psx_memcard_slot's window, as at quit.

  core_top: core_bridge_cmd's target command port goes through the flush block; psx_cue_mount's
            commands pass straight through and wait only while a flush is in flight (their done is
            held low until their own command's done falls). card_write (clk2x, the VRAM
            controller's guest card-write pulse) marks the card dirty. enable = memcard_load_done &&
            reset_n; hold = save_cd_freeze || pause. The save device's cue_busy includes the flush.
  SDC:      false paths into the flush block's wsync[0], en_s[0] and hold_s[0] (level/toggle
            synchronizers).

Open before it ships (docs/public-issues.md): a hardware check that a target write to a
nonvolatile slot reaches the file on SD.

Check: scripts/run_memcard_flush.py (Icarus, Laptop; 7 planted mutations).
Usage: prepare_memcard_flush.py SOURCE --output OUTPUT [--seed N]   (frozen project snapshot)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_paths import ROOT

TAG = 'card-flush'
FLUSH = ROOT / 'rtl/pocket/psx_memcard_flush.sv'


def _once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected 1 match, found {text.count(old)}: {old.strip()[:80]}')
    return text.replace(old, new)


INSTANCE = '''    psx_memcard_flush memcard_flush (.clk(clk_74a),.clk2x(clk2x),.card_write(mc_card_write),   // card-flush (#18)
        .enable(memcard_load_done && reset_n),.hold(save_cd_freeze || pause),
        .c_read(cue_apf_target_dataslot_read),.c_getfile(cue_apf_target_dataslot_getfile),.c_openfile(cue_apf_target_dataslot_openfile),
        .c_id(cue_apf_target_dataslot_id),.c_offset(cue_apf_target_dataslot_slotoffset),.c_bridge(cue_apf_target_dataslot_bridgeaddr),
        .c_length(cue_apf_target_dataslot_length),.c_busy(cue_cmd_busy),.c_done(fl_c_done),
        .t_read(fl_t_read),.t_write(fl_t_write),.t_getfile(fl_t_getfile),.t_openfile(fl_t_openfile),
        .t_id(fl_t_id),.t_offset(fl_t_offset),.t_bridge(fl_t_bridge),.t_length(fl_t_length),
        .t_done(cue_apf_target_dataslot_done),.t_error(cue_apf_target_dataslot_err),.busy(fl_busy),
        .dirty(),.last_error(),.flushes());
'''
CUE_END = '        .cue_mounted(cue_mounted), .mount_failed(cue_mount_failed), .cur_disc_o(cue_cur_disc));\n'
PAIRS = (
    ('    core_bridge_cmd commands (\n',
     f'    // {TAG} (#18): the target command port, shared by psx_cue_mount and psx_memcard_flush\n'
     '    wire fl_t_read, fl_t_write, fl_t_getfile, fl_t_openfile, fl_c_done, fl_busy;\n'
     '    wire [15:0] fl_t_id; wire [31:0] fl_t_offset, fl_t_bridge, fl_t_length;\n'
     '    core_bridge_cmd commands (\n'),
    ("        .target_dataslot_read(cue_apf_target_dataslot_read),\n"
     "        .target_dataslot_write(1'b0),\n"
     "        .target_dataslot_getfile(cue_apf_target_dataslot_getfile),\n"
     "        .target_dataslot_openfile(cue_apf_target_dataslot_openfile),\n",
     "        .target_dataslot_read(fl_t_read),\n"
     "        .target_dataslot_write(fl_t_write),\n"
     "        .target_dataslot_getfile(fl_t_getfile),\n"
     "        .target_dataslot_openfile(fl_t_openfile),\n"),
    ("        .target_dataslot_id(cue_apf_target_dataslot_id),\n"
     "        .target_dataslot_slotoffset(cue_apf_target_dataslot_slotoffset),\n"
     "        .target_dataslot_bridgeaddr(cue_apf_target_dataslot_bridgeaddr),\n"
     "        .target_dataslot_length(cue_apf_target_dataslot_length),\n",
     "        .target_dataslot_id(fl_t_id),\n"
     "        .target_dataslot_slotoffset(fl_t_offset),\n"
     "        .target_dataslot_bridgeaddr(fl_t_bridge),\n"
     "        .target_dataslot_length(fl_t_length),\n"),
    ('.d_done(cue_apf_target_dataslot_done), .d_error(cue_apf_target_dataslot_err)',
     '.d_done(fl_c_done), .d_error(cue_apf_target_dataslot_err)'),
    ('.cd_freeze(save_cd_freeze), .cue_busy(cue_cmd_busy),',
     '.cd_freeze(save_cd_freeze), .cue_busy(cue_cmd_busy || fl_busy),'),
    (CUE_END, CUE_END + INSTANCE),
)
SDC = (f'\n# {TAG} (#18): psx_memcard_flush level/toggle synchronizers\n'
       'set_false_path -to [get_registers {*|memcard_flush|wsync[0] *|memcard_flush|en_s[0] *|memcard_flush|hold_s[0]}]\n')


def top_text(text):
    if TAG in text:
        raise ValueError(f'{TAG}: core_top already has the step')
    for old, new in PAIRS:
        text = _once(text, old, new)
    return text.rstrip('\n') + '\n\n' + FLUSH.read_text()


def sdc_text(text):
    if 'memcard_flush' in text:
        raise ValueError(f'{TAG}: pocket.sdc already has the step')
    return text.rstrip('\n') + '\n' + SDC


def prepare(source, output, seed=None):
    import prepare_save_checksum as cs
    return cs.prepare(source, output, seed, {'core_top.sv': top_text, 'pocket.sdc': sdc_text})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int)
    args = parser.parse_args()
    try:
        seed, changed = prepare(args.source, args.output, args.seed)
    except ValueError as error:
        raise SystemExit(str(error))
    print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
    for path, (old, new) in changed.items():
        print(f'{TAG}: patched {path} (sha256 {old[:16]} -> {new[:16]})')
