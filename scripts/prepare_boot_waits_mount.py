#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""core_top: --boot-waits-disc's 14.5 s timer waits while the cue reader mounts.

--boot-waits-disc holds the CPU in reset until the first disc mount reaches
cd_top, or 14.46 s (2^30 clk_74a cycles) after Reset Exit. A cue whose track
files are opened one by one can take longer than that to mount (Street
Fighter Collection, 72 files; Tomb Raider II, 61): the CPU then left reset
with no disc and the BIOS booted to its menu (0.5.5 and 0.5.6 alike).

The cue reader's launch mount is running while APF's allcomplete is up and
the reader has not released its own (psx_cue_mount s_allcomplete: the
firmware sets it when the launch mount ends, mounted or failed). After 14.46 s
the timer now waits for that, so a slow mount still boots its disc; with no
mount running (no game, APF still loading, a plain .bin) it ends as before.
2^32 cycles (57.8 s) is the cap, so a hung mount cannot hold the CPU forever.
The counter grows from 30 to 32 bits; no other state.
Usage: prepare_boot_waits_mount.py PROJECT_OR_core_top.sv (after
prepare_pocket_early.py with --boot-waits-disc --cue-mount)
"""
import re
import sys
from pathlib import Path

OLD_REG = '    reg [29:0] disc_wait=0; reg disc_wait_done=0;\n'
OLD_ALWAYS = '''    always @(posedge clk_74a)
        if (!power_reset_n) begin disc_wait<=0; disc_wait_done<=0; end
        else if (reset_n && !disc_wait_done) begin disc_wait<=disc_wait+1'b1; if (&disc_wait) disc_wait_done<=1'b1; end
'''
NEW_REG = '''    // prepare_boot_waits_mount.py: past 14.46 s the timer waits for the cue
    // reader's launch mount (below, after psx_cue_mount); 57.8 s is the cap.
    reg [31:0] disc_wait=0; reg disc_wait_done=0;
'''
ANCHOR = '    wire cue_s_allcomplete, cue_table_grant;\n'
NEW_ALWAYS = '''    // prepare_boot_waits_mount.py: the launch mount runs while APF's
    // allcomplete is up and the cue reader has not released its own.
    wire cue_mount_busy=dataslot_allcomplete && !cue_s_allcomplete;
    always @(posedge clk_74a)
        if (!power_reset_n) begin disc_wait<=0; disc_wait_done<=0; end
        else if (reset_n && !disc_wait_done) begin disc_wait<=disc_wait+1'b1; if (((|disc_wait[31:30] || &disc_wait[29:0]) && !cue_mount_busy) || &disc_wait) disc_wait_done<=1'b1; end
'''


def transform(text):
    for name, part in (('disc_wait register', OLD_REG), ('disc_wait timer', OLD_ALWAYS), ('cue reader allcomplete', ANCHOR)):
        if text.count(part) != 1: raise ValueError(f'{name} not found once (needs --boot-waits-disc and --cue-mount)')
    if text.count('    wire dataslot_allcomplete;\n') != 1: raise ValueError('dataslot_allcomplete not found')
    text = text.replace(OLD_REG, NEW_REG).replace(OLD_ALWAYS, '')
    return text.replace(ANCHOR, ANCHOR + NEW_ALWAYS)


def main():
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1])
    path = Path(sys.argv[1])
    if path.is_dir():
        # the core_top the project compiles (prepare_save_states.py moves it to save-states/)
        core = re.findall(r'"([^"]*/core_top\.sv)"', (path/'apf_top.qsf').read_text())
        if len(core) != 1: raise SystemExit(f'{path}: expected one core_top.sv in apf_top.qsf')
        path = Path(core[0])
    try: path.write_text(transform(path.read_text()))
    except ValueError as error: raise SystemExit(f'{path}: {error}')
    print(f'{path}: boot timer waits for the cue mount (cap 57.8 s)')


if __name__ == '__main__':
    main()
